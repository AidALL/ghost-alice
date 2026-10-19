"""installer.addons: addon sources, manifests and the addon entry points the installer drives.

Expected texts and arguments come from install.sh:203-213, installer_lib/targets.sh:28-148,
installer_lib/uninstall.sh:50-79, 110-140, 269-281, 457-478 and installer_lib/install.sh:612-638, 665-675, 766-781.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer import addons  # noqa: E402
from installer.runner import InstallerExit, ToolResult  # noqa: E402
from installer.selection import ALL_SKILLS, Target  # noqa: E402
from installer_testing import FakeRunner, make_context, make_source, quiet  # noqa: E402


class AddonCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.source = make_source(root / "src")
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GHOST_ALICE_GHOST_ROOT", "GHOST_ALICE_ADDON_SOURCE_CACHE_DIR",
                    "GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE"):
            os.environ.pop(key, None)
        quiet(self.home)
        self.runner = FakeRunner()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def context(self, platform="claude", **options):
        return make_context(self.source, self.home, platform, **options)

    @staticmethod
    def captured(fn, *args, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn(*args, **kwargs)
        return result, out.getvalue()


class SourceTests(AddonCase):
    def test_official_source(self):
        # install.sh:203-213
        self.assertEqual(addons.official_addon_source("autopilot"), "https://github.com/AidALL/ghost-alice-autopilot.git")
        self.assertEqual(addons.official_addon_source("autopilot-mode"), "https://github.com/AidALL/ghost-alice-autopilot.git")
        with mock.patch.dict(os.environ, {"GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE": "/tmp/local-autopilot"}):
            self.assertEqual(addons.official_addon_source("autopilot"), "/tmp/local-autopilot")
        self.assertIsNone(addons.official_addon_source("other"))

    def test_git_url_detection(self):
        # targets.sh:28-34
        for url in ("http://x/y.git", "https://x/y.git", "ssh://git@x/y", "file:///tmp/repo", "git@github.com:a/b.git"):
            self.assertTrue(addons.source_is_git_url(url), url)
        for path in ("/tmp/addons", "./ghost-alice-addons", "gitlab.com/x", "git@"):
            self.assertFalse(addons.source_is_git_url(path), path)

    def test_selected_ref(self):
        # targets.sh:36-46
        self.assertEqual(addons.selected_ref([]), "")
        self.assertEqual(addons.selected_ref(["v1"]), "v1")
        _, out = self.captured(lambda: self.assertRaises(InstallerExit, addons.selected_ref, ["v1", "v2"]))
        self.assertIn("[ERROR] --addon-tag accepts one branch/tag for git URL addon sources", out)

    def test_cache_key(self):
        # targets.sh:55-61
        expected = hashlib.sha256(b"https://x/y.git\0v1").hexdigest()[:24]
        self.assertEqual(addons.source_cache_key("https://x/y.git", "v1"), expected)
        self.assertEqual(len(addons.source_cache_key("a", "")), 24)

    def test_clone_git_source(self):
        # targets.sh:64-94
        cache = self.home / ".ghost-alice" / "addon-source-cache"
        key = addons.source_cache_key("https://x/y.git", "v1")

        def clone(script, argv, kwargs):
            Path(argv[-1]).mkdir(parents=True)
            (Path(argv[-1]) / "addons-manifest.json").write_text("{}", encoding="utf-8")
            return ToolResult(0)

        self.runner.respond(lambda s, a: s == "command" and a[:2] == ["git", "clone"], clone)
        with self.runner.patch():
            dest, out = self.captured(addons.clone_git_source, "https://x/y.git", "v1")
        self.assertEqual(dest, cache / key)
        self.assertTrue((dest / "addons-manifest.json").is_file())
        self.assertFalse(Path(f"{dest}.tmp.{os.getpid()}").exists())
        self.assertIn("[INFO] Cloning addon source: https://x/y.git (v1)", out)
        self.assertEqual(self.runner.commands()[0],
                         ["git", "clone", "--quiet", "--depth", "1", "--branch", "v1", "https://x/y.git", f"{dest}.tmp.{os.getpid()}"])
        runner = FakeRunner()
        runner.respond(lambda s, a: s == "command", ToolResult(128))
        with runner.patch():
            _, out = self.captured(lambda: self.assertRaises(InstallerExit, addons.clone_git_source, "https://x/z.git", ""))
        self.assertIn("[INFO] Cloning addon source: https://x/z.git\n", out)
        self.assertIn("[ERROR] Addon source clone failed: https://x/z.git", out)
        self.assertEqual(runner.commands()[0][:6], ["git", "clone", "--quiet", "--depth", "1", "https://x/z.git"])

    def test_prepare_sources(self):
        # targets.sh:96-121
        ctx = self.context(addon_sources=["/tmp/local-addon"])
        with self.runner.patch():
            addons.prepare_sources(ctx)
        self.assertEqual(ctx.addon_sources, ["/tmp/local-addon"])
        self.assertTrue(ctx.addon_sources_prepared)
        self.assertEqual(self.runner.calls, [])
        tagged = self.context(addon_sources=["/tmp/local-addon"], addon_tags=["v1"])
        with self.runner.patch():
            _, out = self.captured(lambda: self.assertRaises(InstallerExit, addons.prepare_sources, tagged))
        self.assertIn("[ERROR] --addon-tag can only be used with git URL addon sources; check out local sources yourself", out)
        skipped = self.context(addon_sources=["https://x/y.git"], addon_skip=True)
        with self.runner.patch():
            addons.prepare_sources(skipped)
        self.assertTrue(skipped.addon_sources_prepared)
        self.assertEqual(skipped.addon_sources, ["https://x/y.git"])
        with mock.patch("installer.addons.clone_git_source", return_value=Path("/cache/key")) as clone:
            git = self.context(addon_sources=["https://x/y.git", "/tmp/local"], addon_tags=["v1"])
            self.captured(lambda: self.assertRaises(InstallerExit, addons.prepare_sources, git))
        clone.assert_called_once_with("https://x/y.git", "v1")
        with mock.patch("installer.addons.clone_git_source", return_value=Path("/cache/key")):
            git = self.context(addon_sources=["https://x/y.git"], addon_tags=["v1"])
            addons.prepare_sources(git)
            addons.prepare_sources(git)
        self.assertEqual(git.addon_sources, ["/cache/key"])


class ManifestTests(AddonCase):
    def test_collect_addon_targets(self):
        # targets.sh:123-148 with --format json
        self.assertEqual(addons.collect_addon_targets(self.context()), [])
        self.assertEqual(addons.collect_addon_targets(self.context(addon_sources=["/a"], addon_skip=True)), [])
        payload = {"target_count": 1, "targets": [{"name": "noop", "source": "/a/skills/noop", "addon_id": "noop",
                                                   "origin": "addon:noop"}]}
        self.runner.respond_to("addon_installer.py", ToolResult(0, json.dumps(payload), ""))
        ctx = self.context("codex", addon_sources=["/a"])
        with self.runner.patch():
            targets = addons.collect_addon_targets(ctx)
        self.assertEqual(targets, [Target("noop", Path("/a/skills/noop"), "noop")])
        args = self.runner.tool_calls("addon_installer.py")[0]
        self.assertEqual(args[:2], ["--source", "/a"])
        self.assertEqual(args[-4:], ["--platform", "codex", "--format", "json"])
        core = [args[i + 1] for i, token in enumerate(args) if token == "--core-skill"]
        self.assertEqual(core[:3], list(ALL_SKILLS[:3]))
        self.assertIn("alpha", core) if "alpha" in ALL_SKILLS else self.assertNotIn("alpha", core)
        failing = FakeRunner()
        failing.respond_to("addon_installer.py", ToolResult(1, "", "addon manifest error: manifest not found\n"))
        with failing.patch():
            _, out = self.captured(lambda: self.assertRaises(InstallerExit, addons.collect_addon_targets, ctx))
        self.assertIn("addon manifest error: manifest not found", out)

    def test_list_addons(self):
        # uninstall.sh:110-140
        _, out = self.captured(addons.list_addons, self.context(addon_skip=True))
        self.assertIn("[INFO] Addon installation is disabled (--addon-skip)", out)
        rc, out = self.captured(addons.list_addons, self.context())
        self.assertEqual(rc, 1)
        self.assertIn("[ERROR] --list-addons requires --addon-source", out)
        with self.runner.patch():
            rc = addons.list_addons(self.context(addon_sources=["/a"]))
        self.assertEqual(rc, 0)
        args = self.runner.tool_calls("addon_installer.py")[0]
        self.assertEqual(args[-4:], ["--platform", "claude", "--format", "text"])

    def test_write_sidecars(self):
        # install.sh:612-638
        self.assertTrue(addons.write_sidecars(self.context(), self.home / "skills"))
        ctx = self.context("codex", addon_sources=["/a", "/b"])
        with self.runner.patch():
            ok, out = self.captured(addons.write_sidecars, ctx, ctx.skills_dir)
        self.assertTrue(ok)
        self.assertIn("[INFO] Addon sidecars written", out)
        args = self.runner.tool_calls("addon_installer.py")[0]
        self.assertEqual(args[:5], ["write-sidecars", "--source", "/a", "--source", "/b"])
        self.assertEqual(args[args.index("--platform") + 1], "codex")
        self.assertEqual(args[args.index("--addons-dir") + 1], str(self.home / ".ghost-alice" / "addons" / "codex"))
        self.assertEqual(args[args.index("--skills-dir") + 1], str(ctx.skills_dir))
        self.assertRegex(args[args.index("--installed-at") + 1], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(args[args.index("--claude-commands-dir") + 1], str(self.home / ".claude" / "commands"))
        self.assertEqual(args[args.index("--resources-dir") + 1], str(self.home / ".ghost-alice" / "resources" / "codex"))
        failing = FakeRunner()
        failing.respond_to("addon_installer.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(addons.write_sidecars, ctx, ctx.skills_dir)
        self.assertFalse(ok)
        self.assertIn("[ERROR] Addon sidecar write failed; aborting install", out)

    def test_migrate_state_once(self):
        # install.sh:766-781
        ctx = self.context("claude")
        with self.runner.patch():
            addons.migrate_state_once(ctx)
        self.assertEqual(self.runner.calls, [])
        state = self.home / ".ghost-alice" / "install-state" / "claude.json"
        state.parent.mkdir(parents=True)
        state.write_text("{}", encoding="utf-8")
        with self.runner.patch():
            addons.migrate_state_once(ctx)
        args = self.runner.tool_calls("addon_migration.py")[0]
        self.assertEqual(args[:6], ["--platform", "claude", "--install-state", str(state),
                                    "--addons-dir", str(self.home / ".ghost-alice" / "addons" / "claude")])
        self.assertEqual([args[i + 1] for i, t in enumerate(args) if t == "--core-skill"], list(ALL_SKILLS))

    def test_repair_missing(self):
        # install.sh:665-675
        ctx = self.context("claude")
        with self.runner.patch():
            self.assertTrue(addons.repair_missing(ctx))
        self.assertEqual(self.runner.calls, [])
        (self.home / ".ghost-alice" / "addons" / "claude").mkdir(parents=True)
        with self.runner.patch():
            self.assertTrue(addons.repair_missing(ctx))
        args = self.runner.tool_calls("addon_installer.py")[0]
        self.assertEqual(args[:3], ["repair-missing", "--platform", "claude"])
        self.assertEqual(args[args.index("--skills-dir") + 1], str(ctx.skills_dir))


class UninstallHelperTests(AddonCase):
    def test_resume_pending_uninstalls(self):
        # uninstall.sh:269-281
        ctx = self.context("codex")
        with self.runner.patch():
            addons.resume_pending_uninstalls(ctx)
        self.assertEqual(self.runner.calls, [])
        (self.home / ".ghost-alice" / "addons" / "codex").mkdir(parents=True)
        self.runner.respond_to("addon_uninstall.py", ToolResult(1, "resumed noop\n", "warning\n"))
        with self.runner.patch():
            addons.resume_pending_uninstalls(ctx)
        args = self.runner.tool_calls("addon_uninstall.py")[0]
        self.assertEqual(args, ["--resume-pending", "--addons-dir", str(self.home / ".ghost-alice" / "addons" / "codex"),
                                "--skills-dir", str(ctx.skills_dir), "--skills-dir", str(self.home / ".claude" / "commands"),
                                "--skills-dir", str(self.home / ".ghost-alice" / "resources" / "codex"),
                                "--platform", "codex", "--confirm"])
        self.assertEqual((self.home / ".ghost-alice" / "addon-resume.log").read_text(encoding="utf-8"), "resumed noop\nwarning\n")

    def test_uninstall_addon_and_dependents(self):
        # uninstall.sh:329-333, 367-368
        ctx = self.context("claude")
        self.runner.respond(lambda s, a: "--addon-id" in a, ToolResult(2))
        self.runner.respond(lambda s, a: "--dependents" in a, ToolResult(2))
        with self.runner.patch():
            self.assertEqual(addons.uninstall_addon(ctx, "noop"), 2)
            self.assertEqual(addons.dependents_rc(ctx, "task-router"), 2)
        removal, dependents = self.runner.tool_calls("addon_uninstall.py")
        self.assertEqual(removal[:2], ["--addon-id", "noop"])
        self.assertEqual(removal[-3:], ["--platform", "claude", "--confirm"])
        self.assertEqual(dependents, ["--dependents", "task-router", "--addons-dir",
                                      str(self.home / ".ghost-alice" / "addons" / "claude")])

    def test_uninstall_all_before_full(self):
        # uninstall.sh:457-478
        ctx = self.context("claude")
        self.assertTrue(addons.uninstall_all_before_full(ctx))
        addons_dir = self.home / ".ghost-alice" / "addons" / "claude"
        addons_dir.mkdir(parents=True)
        for name in ("noop.json", "rich.json", "Bad_Name.json", "x.json.removing"):
            (addons_dir / name).write_text("{}", encoding="utf-8")
        self.runner.respond(lambda s, a: "--addon-id" in a and a[a.index("--addon-id") + 1] == "rich", ToolResult(2))
        with self.runner.patch():
            ok, out = self.captured(addons.uninstall_all_before_full, ctx)
        self.assertFalse(ok)
        ids = [a[1] for a in self.runner.tool_calls("addon_uninstall.py")]
        self.assertEqual(ids, ["noop", "rich"])
        self.assertIn("[WARN] Full uninstall preserved addon rich; manual review is required before sidecar cleanup", out)

    def test_installed_ids_and_promotion(self):
        # uninstall.sh:50-79
        addons_dir = self.home / ".ghost-alice" / "addons" / "claude"
        addons_dir.mkdir(parents=True)
        (addons_dir / "noop.json").write_text("{}", encoding="utf-8")
        (addons_dir / "half.json.removing").write_text("{}", encoding="utf-8")
        self.assertTrue(addons.installed_addon_id("noop", addons_dir))
        self.assertTrue(addons.installed_addon_id("half", addons_dir))
        self.assertFalse(addons.installed_addon_id("other", addons_dir))
        self.assertFalse(addons.installed_addon_id("Bad", addons_dir))
        ctx = self.context("claude")
        addon_ids, skills = addons.promote_bare_installed_addon_ids(ctx, ["alpha", "noop", "fam", "noop", "other"])
        self.assertEqual(addon_ids, ["noop"])
        self.assertEqual(skills, ["alpha", "fam", "other"])


if __name__ == "__main__":
    unittest.main()
