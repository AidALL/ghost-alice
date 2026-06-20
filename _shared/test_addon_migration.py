"""TDD for plan tasks T1.6/T1.7/T1.8: read-only migration audit of an existing
install-state, seed sidecars ONLY when attribution is unambiguous, and write
unattributable targets to _migration-report.json (never fabricate ownership).

Run: python3 -m pytest _shared/test_addon_migration.py -q
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import addon_migration as mig  # noqa: E402
import addon_registry as reg  # noqa: E402

CORE = {"task-router", "verification-before-completion", "_shared"}


def _write_state(root: Path, targets) -> Path:
    state_path = root / ".ghost-alice" / "install-state" / "claude.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"schema_version": 1, "platform": "claude",
                                      "targets": targets}), encoding="utf-8")
    return state_path


def _attributed(name="alpha"):
    return {"target_name": name, "addon_id": name, "origin": f"addon:{name}",
            "addon_version": "1.0.0", "source_path": f"/s/{name}", "dest_path": f"/d/{name}",
            "install_mode": "symlink", "target_tree_hash": "h", "installed_at": "t"}


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addons = self.root / ".ghost-alice" / "addons"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, state_path):
        return mig.run_migration(platform="claude", install_state_path=state_path,
                                 addons_dir=self.addons, core_skill_names=CORE,
                                 installed_at="2026-06-17T00:00:00+00:00")

    def test_core_targets_are_not_migrated(self):
        sp = _write_state(self.root, [{"target_name": "task-router", "dest_path": "/d/tr",
                                       "install_mode": "symlink"}])
        result = self._run(sp)
        self.assertEqual(result["seeded"], [])
        self.assertEqual(result["unresolved"], [])

    def test_attributed_target_without_sidecar_is_seeded(self):
        sp = _write_state(self.root, [_attributed("alpha")])
        result = self._run(sp)
        self.assertEqual(result["seeded"], ["alpha"])
        rec = reg.read_record("alpha", addons_dir=self.addons)
        self.assertEqual(rec["origin"], "addon:alpha")
        self.assertEqual(rec["provided"][0]["name"], "alpha")
        self.assertEqual(rec.get("migration", {}).get("mode"), "best-effort")

    def test_attributed_target_with_existing_sidecar_is_not_reseeded(self):
        # a sidecar already exists for alpha -> migration must not overwrite/re-seed it
        reg.write_record({
            "schema_version": "1.0", "addon_id": "alpha", "addon_version": "9.9.9",
            "source": "/real", "platform": "claude", "owner": "addon",
            "origin": "addon:alpha", "depends_on_core": [], "min_core_version": "0.0.0",
            "installed_at": "real", "provided": [{
                "kind": "skill", "name": "alpha", "target": "/d/alpha",
                "ownership": "addon", "install_mode": "symlink",
                "content_hash": "h", "marker": "", "metadata": {},
            }],
        }, addons_dir=self.addons)
        sp = _write_state(self.root, [_attributed("alpha")])
        result = self._run(sp)
        self.assertEqual(result["seeded"], [])
        self.assertEqual(reg.read_record("alpha", addons_dir=self.addons)["addon_version"], "9.9.9")

    def test_noncore_unattributed_target_goes_to_report_not_seeded(self):
        sp = _write_state(self.root, [{"target_name": "mystery", "dest_path": "/d/mystery",
                                       "install_mode": "symlink"}])
        result = self._run(sp)
        self.assertEqual(result["seeded"], [])
        self.assertEqual([u["target_name"] for u in result["unresolved"]], ["mystery"])
        report = self.addons / "_migration-report.json"
        self.assertTrue(report.exists())
        body = json.loads(report.read_text())
        self.assertEqual(body["mode"], "best-effort")
        self.assertEqual([u["target_name"] for u in body["unresolved_targets"]], ["mystery"])

    def test_groups_same_addon_id_targets_into_one_sidecar(self):
        sp = _write_state(self.root, [
            {"target_name": "one", "addon_id": "multi", "origin": "addon:multi",
             "dest_path": "/d/one", "install_mode": "symlink", "addon_version": "1.0.0", "source_path": "/s"},
            {"target_name": "two", "addon_id": "multi", "origin": "addon:multi",
             "dest_path": "/d/two", "install_mode": "symlink", "addon_version": "1.0.0", "source_path": "/s"},
        ])
        result = self._run(sp)
        self.assertEqual(result["seeded"], ["multi"])  # ONE sidecar for the addon
        rec = reg.read_record("multi", addons_dir=self.addons)
        self.assertEqual({p["name"] for p in rec["provided"]}, {"one", "two"})  # both targets preserved

    def test_missing_state_file_is_noop(self):
        result = self._run(self.root / "nope.json")
        self.assertEqual(result["seeded"], [])
        self.assertEqual(result["unresolved"], [])
        self.assertFalse((self.addons / "_migration-report.json").exists())

    def test_cli_runs(self):
        sp = _write_state(self.root, [_attributed("beta")])
        rc = mig.main(["--platform", "claude", "--install-state", str(sp),
                       "--addons-dir", str(self.addons), "--core-skill", "task-router",
                       "--installed-at", "t"])
        self.assertEqual(rc, 0)
        self.assertEqual(reg.read_all_ids(addons_dir=self.addons), ["beta"])


if __name__ == "__main__":
    unittest.main()
