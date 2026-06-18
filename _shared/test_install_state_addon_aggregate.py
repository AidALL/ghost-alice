"""TDD for plan tasks T1.4/T1.5: install-state is a derived aggregate that includes
addons from the cumulative sidecar scan (so separate-run addons persist -- the
lost-update fix). Run: /opt/homebrew/bin/python3 -m pytest _shared/test_install_state_addon_aggregate.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WRITER = REPO_ROOT / "_shared" / "install_state_writer.py"
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import addon_registry as reg  # noqa: E402


def _sidecar(addon_id, dest, *, install_mode="symlink", content_hash="abc123"):
    return {
        "schema_version": "1.0",
        "addon_id": addon_id,
        "addon_version": "0.1.0",
        "source": f"/src/{addon_id}",
        "platform": "claude",
        "owner": "addon",
        "origin": f"addon:{addon_id}",
        "depends_on_core": [],
        "min_core_version": "0.0.0",
        "installed_at": "2026-06-17T00:00:00+00:00",
        "provided": [{
            "kind": "skill", "name": addon_id, "target": str(dest),
            "ownership": "addon", "install_mode": install_mode,
            "content_hash": content_hash, "marker": "", "metadata": {},
        }],
    }


class InstallStateAddonAggregateTest(unittest.TestCase):
    def _run_writer(self, root: Path, *target_args: str) -> Path:
        state_path = root / ".ghost-alice" / "install-state" / "claude.json"
        result = subprocess.run(
            [sys.executable, str(WRITER), "claude", str(root), "branch", "head", "clean",
             str(state_path), *target_args],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return state_path

    def test_addon_from_sidecar_appears_in_install_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            addons = root / ".ghost-alice" / "addons" / "claude"  # platform-scoped (M5)
            dest = root / ".claude" / "skills" / "alpha"
            reg.write_record(_sidecar("alpha", dest), addons_dir=addons)
            # writer runs with ONLY a core target (alpha was installed in a prior run)
            state_path = self._run_writer(
                root, "_shared", str(root / "src"), str(root / "dest_shared"), "missing")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            by_name = {t["target_name"]: t for t in state["targets"]}
            self.assertIn("alpha", by_name)  # derived from the sidecar scan
            self.assertEqual(by_name["alpha"]["addon_id"], "alpha")
            self.assertEqual(by_name["alpha"]["origin"], "addon:alpha")
            self.assertEqual(by_name["alpha"]["owner"], "addon")
            self.assertEqual(by_name["alpha"]["target_tree_hash"], "abc123")

    def test_separate_run_addons_both_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            addons = root / ".ghost-alice" / "addons" / "claude"  # platform-scoped (M5)
            reg.write_record(_sidecar("aaa", root / ".claude/skills/aaa"), addons_dir=addons)
            reg.write_record(_sidecar("bbb", root / ".claude/skills/bbb"), addons_dir=addons)
            state_path = self._run_writer(
                root, "_shared", str(root / "src"), str(root / "dest_shared"), "missing")
            names = {t["target_name"] for t in json.loads(state_path.read_text())["targets"]}
            self.assertTrue({"aaa", "bbb"} <= names)  # both runs' addons present

    def test_argv_addon_target_is_enriched_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            addons = root / ".ghost-alice" / "addons" / "claude"  # platform-scoped (M5)
            dest = root / ".claude" / "skills" / "noop"
            reg.write_record(_sidecar("noop", dest), addons_dir=addons)
            # this run's argv ALSO lists noop (same dest) -> must enrich, not duplicate
            state_path = self._run_writer(
                root, "noop", str(root / "src/noop"), str(dest), "missing")
            targets = json.loads(state_path.read_text())["targets"]
            noop_targets = [t for t in targets if t["target_name"] == "noop"]
            self.assertEqual(len(noop_targets), 1)
            self.assertEqual(noop_targets[0]["origin"], "addon:noop")

    def test_future_major_sidecar_fails_closed_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            addons = root / ".ghost-alice" / "addons" / "claude"  # platform-scoped (M5)
            reg.write_record(_sidecar("alpha", root / ".claude/skills/alpha"), addons_dir=addons)
            # a tampered / future-major sidecar that read_all would silently skip
            (addons / "beta.json").write_text(
                json.dumps({**_sidecar("beta", root / ".claude/skills/beta"), "schema_version": "9.0"}),
                encoding="utf-8")
            state_path = root / ".ghost-alice" / "install-state" / "claude.json"
            result = subprocess.run(
                [sys.executable, str(WRITER), "claude", str(root), "b", "h", "clean",
                 str(state_path), "_shared", str(root / "src"), str(root / "dest"), "missing"],
                capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0, msg="must fail closed on a skipped sidecar")
            self.assertFalse(state_path.exists(), "install-state must NOT be overwritten when a sidecar is dropped")
            health = state_path.with_name("claude-registry-health.json")
            self.assertTrue(health.exists(), "a registry-health diagnostic must be written")
            self.assertIn("beta", health.read_text())

    def test_no_sidecars_leaves_install_state_unchanged_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = self._run_writer(
                root, "_shared", str(root / "src"), str(root / "dest_shared"), "missing")
            state = json.loads(state_path.read_text())
            self.assertEqual([t["target_name"] for t in state["targets"]], ["_shared"])
            self.assertNotIn("addon_id", state["targets"][0])  # core target not addon-attributed


if __name__ == "__main__":
    unittest.main()
