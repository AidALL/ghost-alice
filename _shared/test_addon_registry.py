"""TDD suite for the per-addon sidecar registry (addon Registry Schema Requirements).

Run with a 3.11+ interpreter, e.g.:
    python3 -m pytest _shared/test_addon_registry.py -q
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import addon_registry as reg  # noqa: E402


def _record(addon_id: str = "alpha", **overrides):
    record = {
        "schema_version": "1.0",
        "addon_id": addon_id,
        "addon_version": "0.1.0",
        "source": "/tmp/src",
        "platform": "claude",
        "owner": "addon",
        "origin": f"addon:{addon_id}",
        "depends_on_core": [],
        "min_core_version": "0.1.0",
        "installed_at": "2026-06-17T00:00:00+00:00",
        "provided": [],
    }
    record.update(overrides)
    return record


def _provided_entry(**overrides):
    entry = {
        "kind": "skill",
        "name": "n",
        "target": "/t",
        "ownership": "addon",
        "install_mode": "symlink",
        "content_hash": "abc123",
        "marker": "",
        "metadata": {},
    }
    entry.update(overrides)
    return entry


class RegistryTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addons = Path(self._tmp.name) / "addons"

    def tearDown(self):
        self._tmp.cleanup()


class WriteReadTest(RegistryTestBase):
    def test_write_two_then_read_all_is_cumulative(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        reg.write_record(_record("beta"), addons_dir=self.addons)
        ids = {r["addon_id"] for r in reg.read_all(addons_dir=self.addons)}
        self.assertEqual(ids, {"alpha", "beta"})

    def test_each_addon_in_its_own_file(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        reg.write_record(_record("beta"), addons_dir=self.addons)
        self.assertTrue((self.addons / "alpha.json").is_file())
        self.assertTrue((self.addons / "beta.json").is_file())

    def test_rewrite_one_updates_only_that_file(self):
        reg.write_record(_record("alpha", addon_version="0.1.0"), addons_dir=self.addons)
        reg.write_record(_record("beta", addon_version="0.1.0"), addons_dir=self.addons)
        beta_before = (self.addons / "beta.json").read_bytes()
        reg.write_record(_record("alpha", addon_version="0.2.0"), addons_dir=self.addons)
        self.assertEqual(reg.read_record("alpha", addons_dir=self.addons)["addon_version"], "0.2.0")
        self.assertEqual((self.addons / "beta.json").read_bytes(), beta_before)

    def test_read_record_roundtrips(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        loaded = reg.read_record("alpha", addons_dir=self.addons)
        self.assertEqual(loaded["origin"], "addon:alpha")

    def test_remove_record_returns_bool(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        self.assertTrue(reg.remove_record("alpha", addons_dir=self.addons))
        self.assertFalse((self.addons / "alpha.json").exists())
        self.assertEqual(reg.read_all(addons_dir=self.addons), [])
        self.assertFalse(reg.remove_record("alpha", addons_dir=self.addons))


class ValidationTest(RegistryTestBase):
    def test_invalid_addon_id_rejected_before_any_write(self):
        for bad in ["../escape", "a/b", "Alpha", "1alpha", "", "a.b", "-lead", "a_b"]:
            with self.subTest(bad=bad):
                with self.assertRaises(reg.InvalidAddonId):
                    reg.write_record(_record(bad), addons_dir=self.addons)
        if self.addons.exists():
            self.assertEqual(list(self.addons.glob("*.json")), [])

    def test_validate_addon_id_direct(self):
        self.assertEqual(reg.validate_addon_id("good-id1"), "good-id1")
        for bad in [None, 123, "Bad", "1bad", ""]:
            with self.subTest(bad=bad):
                with self.assertRaises(reg.InvalidAddonId):
                    reg.validate_addon_id(bad)

    def test_sidecar_path_is_contained(self):
        with self.assertRaises(reg.InvalidAddonId):
            reg.sidecar_path("../../etc/passwd", addons_dir=self.addons)
        good = reg.sidecar_path("alpha", addons_dir=self.addons)
        self.assertEqual(Path(good).name, "alpha.json")
        self.assertEqual(Path(good).resolve().parent, self.addons.resolve())

    def test_missing_required_field_rejected(self):
        rec = _record("alpha")
        del rec["provided"]
        with self.assertRaises(reg.SchemaValidationError):
            reg.write_record(rec, addons_dir=self.addons)
        self.assertEqual(reg.read_all(addons_dir=self.addons), [])

    def test_wrong_field_types_rejected(self):
        for field, bad in [("provided", "oops"), ("depends_on_core", "nope"),
                           ("addon_version", 123), ("installed_at", 0), ("source", "")]:
            with self.subTest(field=field):
                rec = _record("alpha", **{field: bad})
                with self.assertRaises(reg.SchemaValidationError):
                    reg.write_record(rec, addons_dir=self.addons)

    def test_write_unparseable_schema_version_raises_and_writes_nothing(self):
        for bad in ["banana", "1.0.0-evil", " 1 ", "01", "1_0", "-1", "1.10.2"]:
            with self.subTest(bad=bad):
                with self.assertRaises(reg.SchemaValidationError):
                    reg.write_record(_record("alpha", schema_version=bad), addons_dir=self.addons)
        self.assertEqual(reg.read_all(addons_dir=self.addons), [])

    def test_write_future_major_raises_unsupported(self):
        with self.assertRaises(reg.UnsupportedSchemaVersion):
            reg.write_record(_record("alpha", schema_version="2.0"), addons_dir=self.addons)
        self.assertEqual(reg.read_all(addons_dir=self.addons), [])

    def test_float_schema_version_rejected(self):
        with self.assertRaises(reg.SchemaValidationError):
            reg.write_record(_record("alpha", schema_version=1.0), addons_dir=self.addons)

    def test_atomic_write_leaves_no_temp_files(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        leftovers = [p.name for p in self.addons.iterdir() if p.name != "alpha.json"]
        self.assertEqual(leftovers, [])


class AtomicityTest(RegistryTestBase):
    def test_failed_replace_preserves_existing_record_and_cleans_temp(self):
        reg.write_record(_record("alpha", addon_version="0.1.0"), addons_dir=self.addons)
        before = (self.addons / "alpha.json").read_bytes()
        with mock.patch("addon_registry.os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                reg.write_record(_record("alpha", addon_version="9.9.9"), addons_dir=self.addons)
        self.assertEqual((self.addons / "alpha.json").read_bytes(), before)  # unchanged
        leftovers = [p.name for p in self.addons.iterdir() if p.name != "alpha.json"]
        self.assertEqual(leftovers, [])  # temp cleaned

    def test_failed_serialization_cleans_temp(self):
        rec = _record("alpha")
        with mock.patch("addon_registry.json.dump", side_effect=TypeError("not serializable")):
            with self.assertRaises(TypeError):
                reg.write_record(rec, addons_dir=self.addons)
        leftovers = [p.name for p in self.addons.iterdir()] if self.addons.exists() else []
        self.assertEqual(leftovers, [])


class ForwardCompatTest(RegistryTestBase):
    def _rewrite_version(self, addon_id: str, version: str) -> bytes:
        path = self.addons / f"{addon_id}.json"
        data = json.loads(path.read_text())
        data["schema_version"] = version
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        return path.read_bytes()

    def test_unsupported_major_fails_closed_and_leaves_file_untouched(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        before = self._rewrite_version("alpha", "2.0")
        with self.assertRaises(reg.UnsupportedSchemaVersion):
            reg.read_record("alpha", addons_dir=self.addons)
        self.assertEqual((self.addons / "alpha.json").read_bytes(), before)

    def test_zero_major_rejected_on_read(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        self._rewrite_version("alpha", "0.5")
        with self.assertRaises(reg.UnsupportedSchemaVersion):
            reg.read_record("alpha", addons_dir=self.addons)

    def test_newer_minor_preserves_unknown_fields_on_roundtrip(self):
        rec = _record("alpha", schema_version="1.5")
        rec["future_field"] = {"k": "v"}
        reg.write_record(rec, addons_dir=self.addons)
        loaded = reg.read_record("alpha", addons_dir=self.addons)
        self.assertEqual(loaded.get("future_field"), {"k": "v"})
        reg.write_record(loaded, addons_dir=self.addons)
        self.assertEqual(reg.read_record("alpha", addons_dir=self.addons).get("future_field"), {"k": "v"})


class ReadNegativeTest(RegistryTestBase):
    def test_read_record_missing_raises(self):
        with self.assertRaises(reg.RecordNotFound):
            reg.read_record("ghost", addons_dir=self.addons)

    def test_read_record_malformed_json_raises(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        (self.addons / "alpha.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(reg.SchemaValidationError):
            reg.read_record("alpha", addons_dir=self.addons)

    def test_read_record_id_mismatch_rejected(self):
        # file is mike.json but declares addon_id=zulu -> mis-attribution must fail
        path = self.addons
        path.mkdir(parents=True, exist_ok=True)
        (path / "mike.json").write_text(
            json.dumps(_record("zulu")), encoding="utf-8")
        with self.assertRaises(reg.SchemaValidationError):
            reg.read_record("mike", addons_dir=self.addons)

    def test_read_escaping_symlink_rejected_by_containment(self):
        # alpha.json -> a target OUTSIDE the addons dir: caught by sidecar_path containment.
        secret = Path(self._tmp.name) / "secret.json"
        secret.write_text(json.dumps(_record("alpha")), encoding="utf-8")
        self.addons.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(secret, self.addons / "alpha.json")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(reg.RegistryError):  # PathContainmentError
            reg.read_record("alpha", addons_dir=self.addons)

    def test_read_in_dir_symlink_rejected_by_nofollow(self):
        # alpha.json -> beta.json, both INSIDE the dir: containment passes, O_NOFOLLOW rejects.
        reg.write_record(_record("beta"), addons_dir=self.addons)
        try:
            os.symlink(self.addons / "beta.json", self.addons / "alpha.json")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(reg.SchemaValidationError):
            reg.read_record("alpha", addons_dir=self.addons)

    def test_oversized_sidecar_rejected(self):
        self.addons.mkdir(parents=True, exist_ok=True)
        (self.addons / "alpha.json").write_text("x" * (reg.MAX_SIDECAR_BYTES + 1), encoding="utf-8")
        with self.assertRaises(reg.SchemaValidationError):
            reg.read_record("alpha", addons_dir=self.addons)


class ScanRobustnessTest(RegistryTestBase):
    def test_read_all_skips_non_addon_id_files(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        (self.addons / "_migration-report.json").write_text("{}", encoding="utf-8")
        (self.addons / "Not-An-Id.json").write_text("{}", encoding="utf-8")
        ids = {r["addon_id"] for r in reg.read_all(addons_dir=self.addons)}
        self.assertEqual(ids, {"alpha"})

    def test_read_all_skips_unsupported_major_without_raising(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        reg.write_record(_record("beta"), addons_dir=self.addons)
        data = json.loads((self.addons / "beta.json").read_text())
        data["schema_version"] = "9.0"
        (self.addons / "beta.json").write_text(json.dumps(data), encoding="utf-8")
        ids = {r["addon_id"] for r in reg.read_all(addons_dir=self.addons)}
        self.assertEqual(ids, {"alpha"})

    def test_read_all_skips_malformed_and_nondict(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        (self.addons / "bad.json").write_text("{nope", encoding="utf-8")
        (self.addons / "list.json").write_text("[]", encoding="utf-8")
        ids = {r["addon_id"] for r in reg.read_all(addons_dir=self.addons)}
        self.assertEqual(ids, {"alpha"})

    def test_scan_records_surfaces_skips(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        (self.addons / "bad.json").write_text("{nope", encoding="utf-8")
        records, skipped = reg.scan_records(addons_dir=self.addons)
        self.assertEqual({r["addon_id"] for r in records}, {"alpha"})
        self.assertEqual([name for name, _reason in skipped], ["bad.json"])

    def test_scan_records_flags_id_mismatch(self):
        self.addons.mkdir(parents=True, exist_ok=True)
        (self.addons / "mike.json").write_text(json.dumps(_record("zulu")), encoding="utf-8")
        records, skipped = reg.scan_records(addons_dir=self.addons)
        self.assertEqual(records, [])
        self.assertEqual([name for name, _ in skipped], ["mike.json"])

    def test_read_all_on_missing_dir_returns_empty(self):
        self.assertEqual(reg.read_all(addons_dir=self.addons / "nope"), [])


class EnumeratorTest(RegistryTestBase):
    def test_read_all_ids_returns_only_valid_record_ids(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        reg.write_record(_record("beta"), addons_dir=self.addons)
        (self.addons / "gamma.json").write_text("{bad", encoding="utf-8")
        self.assertEqual(reg.read_all_ids(addons_dir=self.addons), ["alpha", "beta"])

    def test_iter_lists_stems_regardless_of_content(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        (self.addons / "gamma.json").write_text("{bad", encoding="utf-8")
        (self.addons / "_migration-report.json").write_text("{}", encoding="utf-8")
        # filename view includes the corrupt gamma; content view excludes it
        self.assertEqual(reg.iter_addon_ids_on_disk(addons_dir=self.addons), ["alpha", "gamma"])
        self.assertEqual(reg.read_all_ids(addons_dir=self.addons), ["alpha"])

    def test_iter_missing_dir_returns_empty(self):
        self.assertEqual(reg.iter_addon_ids_on_disk(addons_dir=self.addons / "nope"), [])


class ProvidedValidationTest(RegistryTestBase):
    def test_provided_entries_structurally_validated(self):
        missing_ownership = _provided_entry()
        del missing_ownership["ownership"]
        missing_install_mode = _provided_entry()
        del missing_install_mode["install_mode"]
        missing_content_hash = _provided_entry()
        del missing_content_hash["content_hash"]
        missing_marker = _provided_entry()
        del missing_marker["marker"]
        missing_metadata = _provided_entry()
        del missing_metadata["metadata"]
        bads = [
            ["notadict"],
            [_provided_entry(kind="evil")],
            [_provided_entry(name="")],
            [_provided_entry(target="")],
            [missing_ownership],
            [missing_install_mode],
            [missing_content_hash],
            [missing_marker],
            [missing_metadata],
            [_provided_entry(metadata=[])],
        ]
        for bad in bads:
            with self.subTest(bad=bad):
                with self.assertRaises(reg.SchemaValidationError):
                    reg.write_record(_record("alpha", provided=bad), addons_dir=self.addons)

    def test_valid_provided_accepted(self):
        reg.write_record(_record("alpha", provided=[
            _provided_entry(kind="skill", name="n", target="/t"),
            _provided_entry(kind="adapter", name="a", target="/x", metadata={"scope": "adapter"}),
        ]), addons_dir=self.addons)
        self.assertEqual(len(reg.read_record("alpha", addons_dir=self.addons)["provided"]), 2)


class ScanStrictTest(RegistryTestBase):
    def test_scan_skips_record_missing_required_field(self):
        reg.write_record(_record("alpha"), addons_dir=self.addons)
        # structurally-incomplete record written directly (bypassing write_record validation)
        (self.addons / "beta.json").write_text(
            json.dumps({"schema_version": "1.0", "addon_id": "beta"}), encoding="utf-8")
        self.assertEqual(reg.read_all_ids(addons_dir=self.addons), ["alpha"])
        _records, skipped = reg.scan_records(addons_dir=self.addons)
        self.assertIn("beta.json", [name for name, _ in skipped])


class SizeGuardTest(RegistryTestBase):
    def test_write_oversized_record_rejected(self):
        big = "x" * reg.MAX_SIDECAR_BYTES
        with self.assertRaises(reg.SchemaValidationError):
            reg.write_record(_record("alpha", source=big), addons_dir=self.addons)
        # rejected before any file is created
        self.assertEqual(reg.read_all(addons_dir=self.addons), [])


class DefaultsTest(unittest.TestCase):
    def test_default_addons_dir(self):
        got = reg.default_addons_dir("/home/x")
        self.assertEqual(Path(got), Path("/home/x/.ghost-alice/addons"))


if __name__ == "__main__":
    unittest.main()
