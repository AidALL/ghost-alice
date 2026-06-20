"""TDD for plan tasks T1.1/T1.2/T1.3 (python layer): addon identity threading + sidecar writing.

Run: python3 -m pytest _shared/test_addon_sidecar_write.py -q
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import addon_installer as ai  # noqa: E402
import addon_registry as reg  # noqa: E402


def _addon_source_without_min_core(temp_dir: str) -> Path:
    source = Path(temp_dir)
    skill_dir = source / "addons" / "demo" / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n", encoding="utf-8")
    source.joinpath("addons-manifest.json").write_text(
        json.dumps({"manifest_version": 1, "addons": [{"id": "demo", "path": "addons/demo"}]}),
        encoding="utf-8",
    )
    source.joinpath("addons/demo/addon.json").write_text(
        json.dumps({
            "addon_version": "0.2.0",
            "addon_id": "demo",
            "skills": [{"name": "demo", "source": "skill", "skill_dir": "skill"}],
            "platforms": ["claude"],
        }),
        encoding="utf-8",
    )
    return source


class IdentityThreadingTest(unittest.TestCase):
    def test_min_core_version_threaded_from_top_level_entry(self):
        targets = ai.load_addon_targets([FIXTURE_ROOT])
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].min_core_version, "0.1.0")

    def test_min_core_version_defaults_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _addon_source_without_min_core(tmp)
            targets = ai.load_addon_targets([source])
            self.assertEqual(targets[0].min_core_version, "0.0.0")

    def test_as_dict_includes_min_core_version(self):
        targets = ai.load_addon_targets([FIXTURE_ROOT])
        self.assertEqual(targets[0].as_dict()["min_core_version"], "0.1.0")


class BuildRecordTest(unittest.TestCase):
    def test_build_sidecar_record_passes_registry_validation(self):
        target = ai.load_addon_targets([FIXTURE_ROOT])[0]
        record = ai.build_sidecar_record(
            target,
            platform="claude",
            installed_at="2026-06-17T00:00:00+00:00",
            provided=[{"kind": "skill", "name": "noop", "target": "/dest/noop",
                       "ownership": "addon", "install_mode": "symlink",
                       "content_hash": "", "marker": "", "metadata": {}}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            addons = Path(tmp) / "addons"
            reg.write_record(record, addons_dir=addons)  # would raise if invalid
            loaded = reg.read_record("noop", addons_dir=addons)
            self.assertEqual(loaded["addon_id"], "noop")
            self.assertEqual(loaded["min_core_version"], "0.1.0")
            self.assertEqual(loaded["origin"], "addon:noop")
            self.assertEqual(loaded["provided"][0]["name"], "noop")

    def test_secrets_included_only_when_present(self):
        target = ai.load_addon_targets([FIXTURE_ROOT])[0]  # noop has secrets: []
        record = ai.build_sidecar_record(
            target, platform="claude", installed_at="t", provided=[])
        self.assertNotIn("secrets", record)


class WriteSidecarsTest(unittest.TestCase):
    def test_write_one_sidecar_per_addon_from_fixture(self):
        targets = ai.load_addon_targets([FIXTURE_ROOT])
        with tempfile.TemporaryDirectory() as tmp:
            addons = Path(tmp) / "addons"
            written = ai.write_addon_sidecars(
                targets, platform="claude", addons_dir=addons, installed_at="t")
            self.assertEqual(len(written), 1)
            ids = reg.read_all_ids(addons_dir=addons)
            self.assertEqual(ids, ["noop"])
            rec = reg.read_record("noop", addons_dir=addons)
            self.assertEqual([p["kind"] for p in rec["provided"]], ["skill"])
            self.assertEqual(rec["provided"][0]["name"], "noop")

    def test_groups_multiple_skills_of_one_addon_into_one_sidecar(self):
        def t(name):
            return ai.AddonTarget(
                name=name, source=Path(f"/src/{name}"), addon_id="multi",
                addon_version="1.0.0", origin="addon:multi", platforms=("claude",),
                depends_on_core=(), secrets=(), tags=(), min_core_version="0.0.0")
        with tempfile.TemporaryDirectory() as tmp:
            addons = Path(tmp) / "addons"
            written = ai.write_addon_sidecars(
                [t("one"), t("two")], platform="claude", addons_dir=addons, installed_at="t")
            self.assertEqual(len(written), 1)  # one sidecar for the addon
            rec = reg.read_record("multi", addons_dir=addons)
            self.assertEqual({p["name"] for p in rec["provided"]}, {"one", "two"})

    def test_explicit_provided_by_addon_is_used(self):
        targets = ai.load_addon_targets([FIXTURE_ROOT])
        provided = {"noop": [{"kind": "skill", "name": "noop", "target": "/real/dest",
                              "ownership": "addon", "install_mode": "copy",
                              "content_hash": "abc", "marker": "", "metadata": {}}]}
        with tempfile.TemporaryDirectory() as tmp:
            addons = Path(tmp) / "addons"
            ai.write_addon_sidecars(targets, platform="claude", addons_dir=addons,
                                    installed_at="t", provided_by_addon=provided)
            rec = reg.read_record("noop", addons_dir=addons)
            self.assertEqual(rec["provided"][0]["target"], "/real/dest")
            self.assertEqual(rec["provided"][0]["content_hash"], "abc")


def _t(name, addon_id="m", **over):
    fields = dict(
        name=name, source=Path(f"/s/{name}"), addon_id=addon_id, addon_version="1.0.0",
        origin=f"addon:{addon_id}", platforms=("claude",), depends_on_core=(), secrets=(),
        tags=(), min_core_version="0.0.0", addon_root=Path("/s"),
    )
    fields.update(over)
    return ai.AddonTarget(**fields)


class HardeningTest(unittest.TestCase):
    def _make_source(self, base, addon_id, ver, skill, min_core=None):
        src = Path(base)
        sd = src / "addons" / addon_id / "skill"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text(f"---\nname: {skill}\ndescription: d\n---\n", encoding="utf-8")
        entry = {"id": addon_id, "path": f"addons/{addon_id}"}
        if min_core is not None:
            entry["min_core_version"] = min_core
        src.joinpath("addons-manifest.json").write_text(
            json.dumps({"manifest_version": 1, "addons": [entry]}), encoding="utf-8")
        src.joinpath(f"addons/{addon_id}/addon.json").write_text(json.dumps({
            "addon_version": ver, "addon_id": addon_id,
            "skills": [{"name": skill, "source": "skill", "skill_dir": "skill"}],
            "platforms": ["claude"]}), encoding="utf-8")
        return src

    def test_duplicate_addon_id_across_sources_rejected(self):
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            s1 = self._make_source(t1, "dup", "1.0.0", "alpha")
            s2 = self._make_source(t2, "dup", "9.9.9", "beta")
            with self.assertRaises(ai.AddonManifestError):
                ai.load_addon_targets([s1, s2])

    def test_malformed_min_core_version_rejected(self):
        for bad in ["abc", "1.2.3.4", "1.2"]:
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                src = self._make_source(tmp, "demo", "1.0.0", "demo", min_core=bad)
                with self.assertRaises(ai.AddonManifestError):
                    ai.load_addon_targets([src])

    def test_partial_provided_by_addon_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            addons = Path(tmp) / "addons"
            partial = {"m": [{"kind": "skill", "name": "a1", "target": "/d",
                              "ownership": "addon", "install_mode": "copy",
                              "content_hash": "", "marker": "", "metadata": {}}]}
            with self.assertRaises(ValueError):
                ai.write_addon_sidecars([_t("a1"), _t("a2")], platform="claude",
                                        addons_dir=addons, installed_at="t",
                                        provided_by_addon=partial)

    def test_origin_is_derived_not_trusted(self):
        spoof = _t("x", addon_id="x", origin="core:spoofed")
        rec = ai.build_sidecar_record(spoof, platform="claude", installed_at="t",
                                      provided=[{"kind": "skill", "name": "x", "target": "/d"}])
        self.assertEqual(rec["origin"], "addon:x")

    def test_min_core_version_exceeds_core_is_rejected(self):
        # noop declares min_core 0.1.0 -> fine against the live core version (VERSION SSOT)
        self.assertEqual(len(ai.load_addon_targets([FIXTURE_ROOT], core_version=ai._read_core_version())), 1)
        with tempfile.TemporaryDirectory() as tmp:
            src = self._make_source(tmp, "highver", "0.1.0", "highver", min_core="9999.0.0")
            with self.assertRaises(ai.AddonManifestError):
                ai.load_addon_targets([src], core_version=ai._read_core_version())

    def test_default_core_version_from_file_rejects_future_addon(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._make_source(tmp, "highver", "0.1.0", "highver", min_core="9999.0.0")
            with self.assertRaises(ai.AddonManifestError):
                ai.load_addon_targets([src])  # core_version defaults to the VERSION file

    def test_sidecar_source_is_addon_root_not_skill_dir(self):
        target = ai.load_addon_targets([FIXTURE_ROOT])[0]
        rec = ai.build_sidecar_record(target, platform="claude", installed_at="t",
                                      provided=[{"kind": "skill", "name": "noop", "target": "/d"}])
        self.assertTrue(rec["source"].endswith("addons/noop"))
        self.assertFalse(rec["source"].endswith("skill"))


class CliWriteSidecarsTest(unittest.TestCase):
    def test_cli_writes_sidecar_for_symlink_installed_addon(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp) / "skills"
            skills.mkdir()
            addons = Path(tmp) / "addons"
            src = FIXTURE_ROOT / "addons" / "noop" / "skill"
            (skills / "noop").symlink_to(src)  # simulate the install.sh symlink
            rc = ai.main([
                "write-sidecars", "--source", str(FIXTURE_ROOT), "--platform", "claude",
                "--addons-dir", str(addons), "--skills-dir", str(skills), "--installed-at", "t",
            ])
            self.assertEqual(rc, 0)
            rec = reg.read_record("noop", addons_dir=addons)
            self.assertEqual(rec["addon_id"], "noop")
            self.assertEqual(rec["min_core_version"], "0.1.0")
            self.assertEqual(rec["provided"][0]["name"], "noop")
            self.assertEqual(rec["provided"][0]["install_mode"], "symlink")
            self.assertEqual(rec["provided"][0]["target"], str(skills / "noop"))
            self.assertTrue(rec["provided"][0]["content_hash"])  # real hash, not placeholder

    def test_cli_missing_install_is_marked_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp) / "skills"
            skills.mkdir()
            addons = Path(tmp) / "addons"
            # do NOT create skills/noop -> install dest missing
            rc = ai.main([
                "write-sidecars", "--source", str(FIXTURE_ROOT), "--platform", "claude",
                "--addons-dir", str(addons), "--skills-dir", str(skills), "--installed-at", "t",
            ])
            self.assertEqual(rc, 0)
            rec = reg.read_record("noop", addons_dir=addons)
            self.assertEqual(rec["provided"][0]["install_mode"], "missing")

    def test_cli_refuses_to_downgrade_existing_future_major_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp) / "skills"
            addons = Path(tmp) / "addons"
            skills.mkdir()
            addons.mkdir()
            src = FIXTURE_ROOT / "addons" / "noop" / "skill"
            (skills / "noop").symlink_to(src)
            existing = {
                "schema_version": "9.0",
                "addon_id": "noop",
                "addon_version": "9.9.9",
                "source": "/future",
                "platform": "claude",
                "owner": "addon",
                "origin": "addon:noop",
                "depends_on_core": [],
                "min_core_version": "9.9.9",
                "installed_at": "future",
                "provided": [],
                "future_field": {"keep": "me"},
            }
            sidecar = addons / "noop.json"
            sidecar.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            before = sidecar.read_text(encoding="utf-8")

            rc = ai.main([
                "write-sidecars", "--source", str(FIXTURE_ROOT), "--platform", "claude",
                "--addons-dir", str(addons), "--skills-dir", str(skills), "--installed-at", "t",
            ])

            self.assertNotEqual(rc, 0)
            self.assertEqual(sidecar.read_text(encoding="utf-8"), before)

    def test_cli_preserves_existing_higher_minor_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp) / "skills"
            addons = Path(tmp) / "addons"
            skills.mkdir()
            addons.mkdir()
            src = FIXTURE_ROOT / "addons" / "noop" / "skill"
            (skills / "noop").symlink_to(src)
            existing = {
                "schema_version": "1.5",
                "addon_id": "noop",
                "addon_version": "0.1.0",
                "source": str(FIXTURE_ROOT / "addons" / "noop"),
                "platform": "claude",
                "owner": "addon",
                "origin": "addon:noop",
                "depends_on_core": [],
                "min_core_version": "0.0.0",
                "installed_at": "old",
                "provided": [],
                "future_field": {"keep": "me"},
            }
            (addons / "noop.json").write_text(
                json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            rc = ai.main([
                "write-sidecars", "--source", str(FIXTURE_ROOT), "--platform", "claude",
                "--addons-dir", str(addons), "--skills-dir", str(skills), "--installed-at", "t",
            ])

            self.assertEqual(rc, 0)
            rec = reg.read_record("noop", addons_dir=addons)
            self.assertEqual(rec.get("future_field"), {"keep": "me"})


if __name__ == "__main__":
    unittest.main()
