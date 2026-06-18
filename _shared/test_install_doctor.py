#!/usr/bin/env python3
"""Tests for the read-only installer doctor node-runtime check.

The tool-checkpoint PreToolUse gate is dispatched through `node ghost-alice-hook.mjs`.
The installer blocks hook install when node is absent, but node can be removed from
PATH after install. Doctor is the read-only diagnostic that must surface that drift,
because Claude Code treats a non-2 PreToolUse exit (a missing-node crash) as
non-blocking, so the gate would silently fail open.
"""

import unittest
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import install_doctor
import addon_registry as reg
import hash_utils


def _sidecar(addon_id, target, *, install_mode="copy", content_hash):
    return {
        "schema_version": "1.0", "addon_id": addon_id, "addon_version": "1.0.0",
        "source": f"/s/{addon_id}", "platform": "claude", "owner": "addon",
        "origin": f"addon:{addon_id}", "depends_on_core": [], "min_core_version": "0.0.0",
        "installed_at": "t", "provided": [{
            "kind": "skill", "name": addon_id, "target": str(target), "ownership": "addon",
            "install_mode": install_mode, "content_hash": content_hash, "marker": "", "metadata": {},
        }],
    }


class AddonRegistryAuditTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.gar = Path(self._tmp.name) / ".ghost-alice"
        self.addons = self.gar / "addons" / "claude"
        self.skills = Path(self._tmp.name) / ".claude" / "skills"
        self.skills.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _install(self, addon_id, body="body\n"):
        dest = self.skills / addon_id
        dest.mkdir()
        (dest / "SKILL.md").write_text(body, encoding="utf-8")
        reg.write_record(_sidecar(addon_id, dest, content_hash=hash_utils.hash_target(str(dest), "copy")),
                         addons_dir=self.addons)
        return dest

    def test_intact_addon_target_is_ok(self):
        self._install("noop")
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertEqual([(f["addon_id"], f["status"]) for f in findings], [("noop", "ok")])

    def test_content_hash_tamper_is_error(self):
        dest = self._install("noop")
        (dest / "SKILL.md").write_text("TAMPERED\n", encoding="utf-8")  # bit-rot / edit after install
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual(status, install_doctor.STATUS_ERROR)
        self.assertEqual(findings[0]["status"], install_doctor.STATUS_ERROR)
        self.assertIn("hash", findings[0]["reason"])

    def test_missing_target_is_warning(self):
        dest = self._install("noop")
        import shutil
        shutil.rmtree(dest)  # user deleted the installed skill
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual(findings[0]["status"], install_doctor.STATUS_WARNING)
        self.assertEqual(status, install_doctor.STATUS_WARNING)

    def test_unreadable_sidecar_is_error(self):
        self.addons.mkdir(parents=True, exist_ok=True)
        (self.addons / "corrupt.json").write_text("{ not json", encoding="utf-8")
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual(status, install_doctor.STATUS_ERROR)
        self.assertTrue(any("sidecar" in f["reason"] for f in findings))

    def test_no_addons_dir_is_ok_empty(self):
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertEqual(findings, [])

    def test_platform_scoped_isolation(self):
        # a codex sidecar must not be audited under the claude platform
        self._install("noop")
        codex_dir = self.gar / "addons" / "codex"
        reg.write_record(_sidecar("other", self.skills / "ghost", content_hash="x"),
                         addons_dir=codex_dir)
        status, findings = install_doctor._addon_registry_audit(self.gar, "claude")
        self.assertEqual([f["addon_id"] for f in findings], ["noop"])  # codex 'other' excluded


class LiveDirOwnershipTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addons = root / ".ghost-alice" / "addons" / "claude"
        self.skills = root / ".claude" / "skills"
        self.skills.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _skill(self, name, body="x\n"):
        dest = self.skills / name
        dest.mkdir()
        (dest / "SKILL.md").write_text(body, encoding="utf-8")
        return dest

    def test_classifies_core_addon_domain_user(self):
        self._skill("task-router")                 # core (name in core set)
        addon_dest = self._skill("noop")           # addon (sidecar-owned)
        reg.write_record(_sidecar("noop", addon_dest, content_hash="h"), addons_dir=self.addons)
        self._skill("my-domain-skill")             # domain (SKILL.md, unmanaged)
        (self.skills / "random-dir").mkdir()       # user (no SKILL.md)
        (self.skills / "_shared").mkdir()          # support dir -> excluded

        findings = install_doctor._live_dir_ownership(self.skills, {"task-router"}, self.addons)
        owner = {f["name"]: f["owner"] for f in findings}
        self.assertEqual(owner["task-router"], "core")
        self.assertEqual(owner["noop"], "addon")
        self.assertEqual(owner["my-domain-skill"], "domain")
        self.assertEqual(owner["random-dir"], "user")
        self.assertNotIn("_shared", owner)

    def test_absent_skills_dir_is_empty(self):
        missing = self.skills / "nope"
        self.assertEqual(install_doctor._live_dir_ownership(missing, set(), self.addons), [])


class NodeRuntimeStatusTest(unittest.TestCase):
    def test_missing_node_is_warning(self) -> None:
        with mock.patch.object(install_doctor.shutil, "which", return_value=None):
            status, detail = install_doctor._node_runtime_status(strict=False)
        self.assertEqual(status, install_doctor.STATUS_WARNING)
        self.assertIn("missing", detail)

    def test_missing_node_under_strict_is_error(self) -> None:
        with mock.patch.object(install_doctor.shutil, "which", return_value=None):
            status, _detail = install_doctor._node_runtime_status(strict=True)
        self.assertEqual(status, install_doctor.STATUS_ERROR)

    def test_present_node_is_ok(self) -> None:
        with mock.patch.object(install_doctor.shutil, "which", return_value="/usr/bin/node"):
            status, detail = install_doctor._node_runtime_status(strict=False)
        self.assertEqual(status, install_doctor.STATUS_OK)
        self.assertIn("ok", detail)


if __name__ == "__main__":
    unittest.main()
