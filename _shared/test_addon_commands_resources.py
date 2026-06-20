"""Tests for addon command and resource provisioning.

- commands[] / resources[] parse into AddonTarget (schema + resolution).
- write-sidecars provisions Claude-only command files and platform-scoped
  resource files, enforces realpath containment for resources, and records both
  in the sidecar provided[] (kind=command / kind=resource) so the generic
  uninstall + doctor machinery covers them.

Run: python3 -m pytest _shared/test_addon_commands_resources.py -q
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RICH = REPO_ROOT / "_shared" / "tests" / "fixtures" / "rich-addon"
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import addon_installer as ai  # noqa: E402
import addon_registry as reg  # noqa: E402


class ResolutionTest(unittest.TestCase):
    def test_commands_and_resources_resolve_onto_target(self):
        targets = ai.load_addon_targets([RICH], platform="claude")
        target = next(t for t in targets if t.name == "richskill")
        self.assertEqual([c[0] for c in target.commands], ["richcmd"])
        self.assertTrue(target.commands[0][1].replace("\\", "/").endswith("commands/richcmd.md"))
        self.assertEqual([r[0] for r in target.resources], ["ref.txt"])
        self.assertTrue(target.resources[0][1].replace("\\", "/").endswith("resources/ref.txt"))

    def test_missing_commands_resources_default_empty(self):
        noop = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"
        targets = ai.load_addon_targets([noop], platform="claude")
        self.assertEqual(targets[0].commands, ())
        self.assertEqual(targets[0].resources, ())

    def test_resource_path_dot_rejected(self):
        import json  # noqa: PLC0415
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            addon = root / "addons" / "r"
            (addon / "skill").mkdir(parents=True)
            (addon / "d").mkdir()
            (addon / "skill" / "SKILL.md").write_text(
                "---\nname: s\ndescription: Use when x.\n---\n#x\n", encoding="utf-8")
            (addon / "d" / "f.txt").write_text("x", encoding="utf-8")
            (root / "addons-manifest.json").write_text(json.dumps({
                "manifest_version": 1,
                "addons": [{"id": "r", "path": "addons/r", "min_core_version": "0.1.0", "tags": []}]}),
                encoding="utf-8")
            (addon / "addon.json").write_text(json.dumps({
                "addon_version": "0.1.0", "addon_id": "r",
                "skills": [{"name": "rs", "source": "skill", "skill_dir": "skill"}],
                "resources": [{"path": ".", "source": "d/f.txt"}],
                "platforms": ["claude"], "depends_on_core": [], "secrets": []}), encoding="utf-8")
            with self.assertRaises(ai.AddonManifestError):
                ai.load_addon_targets([root], platform="claude")


class ProvisionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addons = self.root / ".ghost-alice" / "addons" / "claude"
        self.skills = self.root / ".claude" / "skills"
        self.commands = self.root / ".claude" / "commands"
        self.resources = self.root / ".ghost-alice" / "resources" / "claude"
        # the skill is already "installed" by the sync loop before write-sidecars runs
        shutil.copytree(RICH / "addons" / "rich" / "skill", self.skills / "richskill")

    def tearDown(self):
        self._tmp.cleanup()

    def _write_sidecars(self, platform="claude"):
        res = self.root / ".ghost-alice" / "resources" / platform
        return ai.main([
            "write-sidecars", "--source", str(RICH), "--platform", platform,
            "--addons-dir", str(self.addons.parent / platform), "--skills-dir", str(self.skills),
            "--installed-at", "t", "--claude-commands-dir", str(self.commands),
            "--resources-dir", str(res)])

    def test_claude_provisions_command_resource_and_records(self):
        rc = self._write_sidecars("claude")
        self.assertEqual(rc, 0)
        # command file copied into the Claude commands dir
        self.assertTrue((self.commands / "richcmd.md").is_file())
        self.assertEqual((self.commands / "richcmd.md").read_text(encoding="utf-8"),
                         (RICH / "addons" / "rich" / "commands" / "richcmd.md").read_text(encoding="utf-8"))
        # resource copied under resources/<platform>/<addon_id>/
        self.assertTrue((self.resources / "rich" / "ref.txt").is_file())
        # sidecar records skill + command + resource, each with a content_hash
        rec = reg.read_record("rich", addons_dir=self.addons)
        by_kind = {(p["kind"], p["name"]): p for p in rec["provided"]}
        self.assertIn(("skill", "richskill"), by_kind)
        self.assertIn(("command", "richcmd"), by_kind)
        self.assertIn(("resource", "ref.txt"), by_kind)
        for key in [("command", "richcmd"), ("resource", "ref.txt")]:
            self.assertEqual(by_kind[key]["install_mode"], "copy")
            self.assertTrue(by_kind[key]["content_hash"])
            self.assertIn(str(self.root), by_kind[key]["target"])

    def test_codex_skips_claude_commands(self):
        res = self.root / ".ghost-alice" / "resources" / "codex"
        rc = ai.main([
            "write-sidecars", "--source", str(RICH), "--platform", "codex",
            "--addons-dir", str(self.root / ".ghost-alice" / "addons" / "codex"),
            "--skills-dir", str(self.skills), "--installed-at", "t",
            "--claude-commands-dir", str(self.commands), "--resources-dir", str(res)])
        self.assertEqual(rc, 0)
        self.assertFalse((self.commands / "richcmd.md").exists())  # Claude-only
        self.assertTrue((res / "rich" / "ref.txt").is_file())      # resources still provisioned
        rec = reg.read_record("rich", addons_dir=self.root / ".ghost-alice" / "addons" / "codex")
        kinds = {p["kind"] for p in rec["provided"]}
        self.assertNotIn("command", kinds)
        self.assertIn("resource", kinds)


class ExtrasSecurityTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addons = self.root / ".ghost-alice" / "addons" / "claude"
        self.skills = self.root / ".claude" / "skills"
        self.commands = self.root / ".claude" / "commands"
        self.resources = self.root / ".ghost-alice" / "resources" / "claude"
        shutil.copytree(RICH / "addons" / "rich" / "skill", self.skills / "richskill")

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self):
        return ai.main([
            "write-sidecars", "--source", str(RICH), "--platform", "claude",
            "--addons-dir", str(self.addons), "--skills-dir", str(self.skills),
            "--installed-at", "t", "--claude-commands-dir", str(self.commands),
            "--resources-dir", str(self.resources)])

    def test_refuses_symlink_leaf_command_no_write_through(self):
        self.commands.mkdir(parents=True)
        victim = self.root / "victim.txt"
        victim.write_text("ORIG\n", encoding="utf-8")
        (self.commands / "richcmd.md").symlink_to(victim)
        self.assertNotEqual(self._write(), 0)
        self.assertEqual(victim.read_text(encoding="utf-8"), "ORIG\n")  # never written through

    def test_refuses_symlink_temporary_file_no_write_through(self):
        self.commands.mkdir(parents=True)
        victim = self.root / "tmp-victim.txt"
        victim.write_text("ORIG\n", encoding="utf-8")
        (self.commands / ".richcmd.md.provision.tmp").symlink_to(victim)
        self.assertEqual(self._write(), 0)
        self.assertEqual(victim.read_text(encoding="utf-8"), "ORIG\n")

    def test_refuses_foreign_existing_command(self):
        self.commands.mkdir(parents=True)
        (self.commands / "richcmd.md").write_text("USER COMMAND\n", encoding="utf-8")
        self.assertNotEqual(self._write(), 0)
        self.assertEqual((self.commands / "richcmd.md").read_text(encoding="utf-8"), "USER COMMAND\n")

    def test_refuses_symlink_leaf_resource_no_write_through(self):
        base = self.resources / "rich"
        base.mkdir(parents=True)
        victim = self.root / "rvictim.txt"
        victim.write_text("ORIG\n", encoding="utf-8")
        (base / "ref.txt").symlink_to(victim)
        self.assertNotEqual(self._write(), 0)
        self.assertEqual(victim.read_text(encoding="utf-8"), "ORIG\n")

    def test_refuses_symlinked_resource_base_no_escape(self):
        # the resources/<addon_id> base itself pre-planted as a symlink to an
        # external dir must be refused, not provisioned through (base symlink guard).
        escape = self.root / "escape_dir"
        escape.mkdir()
        self.resources.mkdir(parents=True)
        (self.resources / "rich").symlink_to(escape, target_is_directory=True)
        self.assertNotEqual(self._write(), 0)
        self.assertFalse((escape / "ref.txt").exists())  # nothing written into the escape dir

    def test_reinstall_overwrites_own_extras(self):
        self.assertEqual(self._write(), 0)
        self.assertEqual(self._write(), 0)  # reinstall must NOT false-refuse its own files
        self.assertTrue((self.commands / "richcmd.md").is_file())
        self.assertTrue((self.resources / "rich" / "ref.txt").is_file())

    def test_reinstall_refuses_modified_own_extras(self):
        self.assertEqual(self._write(), 0)
        command = self.commands / "richcmd.md"
        resource = self.resources / "rich" / "ref.txt"
        command.write_text("USER COMMAND EDIT\n", encoding="utf-8")
        resource.write_text("USER RESOURCE EDIT\n", encoding="utf-8")

        self.assertNotEqual(self._write(), 0)

        self.assertEqual(command.read_text(encoding="utf-8"), "USER COMMAND EDIT\n")
        self.assertEqual(resource.read_text(encoding="utf-8"), "USER RESOURCE EDIT\n")


if __name__ == "__main__":
    unittest.main()
