"""installer.auto: one command, every detected platform, each in a child installer process.

Expected texts and the child protocol come from install.sh:271-299, 306-446 and installer_lib/targets.sh:150-228.
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer import auto, cli, report  # noqa: E402
from installer.runner import ToolResult  # noqa: E402
from installer.selection import ALL_SKILLS, Target  # noqa: E402
from installer_testing import FakeRunner, make_context, make_source, quiet, use_source  # noqa: E402



class FakeProcess:
    """Stands in for the child installer: writes scripted events to the event file, then exits."""

    def __init__(self, returncode: int, events: list[str], event_file: Path | None):
        self.returncode = returncode
        self._events = events
        self._event_file = event_file
        self._polls = 0

    def poll(self):
        self._polls += 1
        if self._polls == 1 and self._event_file is not None:
            with open(self._event_file, "a", encoding="utf-8") as handle:
                handle.write("".join(line + "\n" for line in self._events))
            return None
        return self.returncode

    def wait(self):
        self.poll()
        return self.returncode


class AutoCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.source = make_source(root / "src")
        self.enterContext(use_source(self.source))
        self.cli = str(self.source / "_shared" / "installer" / "cli.py")
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GHOST_ALICE_INSTALL_LOG_FILE", "GHOST_ALICE_INSTALL_EVENT_FILE",
                    "GHOST_ALICE_INSTALL_REPORT_CHILD"):
            os.environ.pop(key, None)
        quiet(self.home)
        self.runner = FakeRunner()
        self.children: list[tuple[list[str], dict]] = []
        self.child_results: dict[str, tuple[int, list[str]]] = {}

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def context(self, **options):
        return make_context(self.home, "claude", **options)

    def fake_popen(self, argv, *, env=None, stdout=None):
        argv = [str(a) for a in argv]
        self.children.append((argv, dict(env or {})))
        platform = argv[argv.index("--platform") + 1]
        code, events = self.child_results.get(platform, (0, []))
        event_file = Path(env["GHOST_ALICE_INSTALL_EVENT_FILE"]) if env and "GHOST_ALICE_INSTALL_EVENT_FILE" in env else None
        if stdout is not None:
            stdout.write(f"child {platform} output\n")
        return FakeProcess(code, events, event_file)

    def run_children(self, ctx, detected):
        out = io.StringIO()
        with self.runner.patch(), mock.patch("installer.runner.popen", side_effect=self.fake_popen), \
                mock.patch("installer.auto.count_common_targets", return_value=5), contextlib.redirect_stdout(out):
            rc = auto.run_install_children(ctx, detected)
        return rc, out.getvalue()


class TargetKeyTests(AutoCase):
    def test_platform_keys_and_common_count(self):
        # targets.sh:150-228
        ctx = self.context()
        keys = auto.platform_target_keys(ctx, "claude", ["alpha", "fam"])
        self.assertEqual(keys, ["support:_shared", "skill:alpha", "skill:one", "skill:two"])
        per_platform = {"claude": [Target("both", Path("/a/both"), "a"), Target("only-claude", Path("/a/oc"), "a")],
                        "codex": [Target("both", Path("/a/both"), "a")]}
        ctx = self.context(addon_sources=["/a"])
        with mock.patch("installer.addons.collect_addon_targets", side_effect=lambda c: per_platform[c.platform]):
            self.assertEqual(auto.platform_target_keys(ctx, "codex", ["alpha"]),
                             ["support:_shared", "skill:alpha", "skill:both"])
            self.assertEqual(auto.count_common_targets(ctx, ["claude", "codex"], ["alpha", "fam"]), 5)
            self.assertEqual(auto.count_common_targets(ctx, ["claude"], ["alpha", "fam"]), 6)
        self.assertEqual(auto.count_common_targets(ctx, [], ["alpha"]), 0)


class ChildCommandTests(AutoCase):
    def test_install_child_forwards_options(self):
        # install.sh:315-333, 385
        ctx = self.context(skip_source_health=True, visibility="strict", verbose=True, addon_skip=True,
                           addon_sources=["/a", "/b"], addon_tags=["v1"], args=["task-router"])
        ctx.addon_sources = ["/cache/a", "/cache/b"]
        ctx.addon_sources_prepared = True
        self.assertEqual(auto.child_command(ctx, "codex"),
                         [sys.executable, self.cli, "--platform", "codex", "--skip-source-health", "--visibility", "strict",
                          "--verbose", "--addon-source", "/cache/a", "--addon-source", "/cache/b", "--addon-skip", "task-router"])
        plain = self.context(addon_sources=["https://x/y.git"], addon_tags=["v1"])
        self.assertEqual(auto.child_command(plain, "claude"),
                         [sys.executable, self.cli, "--platform", "claude", "--addon-source", "https://x/y.git", "--addon-tag", "v1"])

    def test_uninstall_child(self):
        # install.sh:294
        self.assertEqual(auto.child_command(self.context(skip_source_health=True), "codex", uninstall=True),
                         [sys.executable, self.cli, "--platform", "codex", "--uninstall"])


class VerboseAutoTests(AutoCase):
    def test_sequential_children_with_messages(self):
        # install.sh:335-359; test_installer_default_auto.py:296-328, 382-387
        quiet(self.home, verbose=True)
        ctx = self.context(verbose=True, args=["task-router"])
        self.runner.respond(lambda s, a: s == "command" and "claude" in a, ToolResult(37))
        out = io.StringIO()
        with self.runner.patch(), contextlib.redirect_stdout(out):
            rc = auto.run_install_children(ctx, ["claude", "codex"])
        self.assertEqual(rc, 1)
        text = out.getvalue()
        self.assertIn("[INFO] Detected platforms: claude codex", text)
        self.assertIn("[INFO] Auto install will process 2 detected platform target(s) in sequence; each has a separate "
                      "install path (not a duplicate install).", text)
        self.assertIn("[INFO] [auto] (1/2) Starting install for claude", text)
        self.assertIn("[WARN] [auto] Install failed for claude (exit code 37)", text)
        self.assertIn("[INFO] [auto] (2/2) Starting install for codex", text)
        self.assertIn("[ERROR] [auto] Some platform installs failed. Check the log above.", text)
        self.assertEqual([c[c.index("--platform") + 1] for c in self.runner.commands()], ["claude", "codex"])
        self.assertTrue(all(c[:2] == [sys.executable, self.cli] for c in self.runner.commands()))
        self.assertEqual(self.runner.commands()[1][-1], "task-router")
        self.runner = FakeRunner()
        out = io.StringIO()
        with self.runner.patch(), contextlib.redirect_stdout(out):
            rc = auto.run_install_children(ctx, ["claude", "codex"])
        self.assertEqual(rc, 0)
        self.assertIn("[OK] [auto] Install complete for all 2 detected install target(s)", out.getvalue())


class CompactAutoTests(AutoCase):
    def test_children_log_and_report(self):
        # install.sh:361-445; test_installer_default_auto.py:404-433
        ctx = self.context(args=["task-router"])
        self.child_results = {
            "claude": (0, ['{"type":"target-result","platform":"claude","target_id":"_shared","target_kind":"support","status":"new"}',
                           '{"type":"platform-result","platform":"claude","total_targets":5,"current":0,"updated":0,"new":5,"verification":"ok","hooks":"enabled"}']),
            "codex": (0, ['{"type":"target-result","platform":"codex","target_id":"_shared","target_kind":"support","status":"new"}',
                          '{"type":"platform-result","platform":"codex","total_targets":5,"current":0,"updated":0,"new":5,"verification":"ok","hooks":"enabled"}']),
        }
        rc, out = self.run_children(ctx, ["claude", "codex"])
        self.assertEqual(rc, 0)
        log = report.state().log_file
        self.assertIsNotNone(log)
        event_file = Path(str(log)[:-len(".log")] + ".events.jsonl")
        self.assertTrue(event_file.is_file())
        self.assertEqual(len(self.children), 2)
        for argv, env in self.children:
            self.assertEqual(argv[:2], [sys.executable, self.cli])
            self.assertEqual(env["GHOST_ALICE_INSTALL_REPORT_CHILD"], "1")
            self.assertEqual(env["GHOST_ALICE_INSTALL_LOG_FILE"], str(log))
            self.assertEqual(env["GHOST_ALICE_INSTALL_EVENT_FILE"], str(event_file))
        log_text = log.read_text(encoding="utf-8")
        self.assertIn("[auto] (1/2) Starting install for claude (separate install path; not a duplicate install)", log_text)
        self.assertIn("child claude output", log_text)
        self.assertIn("Ghost-ALICE OS installation Process Report", out)
        self.assertIn("  Platform: claude, codex\n  Skills: [5] common targets\n", out)
        self.assertIn("        Common targets      [######------------------------] [1/5] common targets synced on all platforms\n", out)
        self.assertIn("  [4/5] Runtime config      claude, codex hooks=true, Visibility Level=[dynamic]\n", out)
        self.assertIn(f"  log: {log}\n", out)
        self.assertNotIn("[auto] Install failed", out)

    def test_failure_excerpt(self):
        ctx = self.context()
        self.child_results = {"claude": (0, []), "codex": (1, [])}

        def failing_popen(argv, *, env=None, stdout=None):
            if stdout is not None and "codex" in argv:
                stdout.write("[ERROR] addon manifest error: manifest not found\n")
            return self.fake_popen(argv, env=env, stdout=stdout)

        out = io.StringIO()
        with self.runner.patch(), mock.patch("installer.runner.popen", side_effect=failing_popen), \
                mock.patch("installer.auto.count_common_targets", return_value=5), contextlib.redirect_stdout(out):
            rc = auto.run_install_children(ctx, ["claude", "codex"])
        self.assertEqual(rc, 1)
        text = out.getvalue()
        log = report.state().log_file
        self.assertIn("[WARN] [auto] Install failed for codex (exit code 1)", text)
        self.assertIn("[ERROR] [auto] Some platform installs failed. Check the log above.", text)
        self.assertIn(f"[ERROR] Details log: {log}", text)
        self.assertIn("[ERROR] [auto] [ERROR] addon manifest error: manifest not found", text)
        self.assertNotIn("Ghost-ALICE OS installation Process Report", text)


class UninstallChildrenTests(AutoCase):
    def test_full_uninstall_children(self):
        # install.sh:278-299
        ctx = self.context()
        self.runner.respond(lambda s, a: s == "command" and "codex" in a, ToolResult(1))
        out = io.StringIO()
        with self.runner.patch(), contextlib.redirect_stdout(out):
            rc = auto.run_uninstall_children(ctx, ["claude", "codex"])
        self.assertEqual(rc, 1)
        self.assertIn("[INFO] Detected uninstall targets: claude codex", out.getvalue())
        self.assertIn("[INFO] [uninstall] (1/2) Starting claude full cleanup", out.getvalue())
        self.assertIn("[INFO] [uninstall] (2/2) Starting codex full cleanup", out.getvalue())
        self.assertEqual(self.runner.commands(), [[sys.executable, self.cli, "--platform", "claude", "--uninstall"],
                                                  [sys.executable, self.cli, "--platform", "codex", "--uninstall"]])


if __name__ == "__main__":
    unittest.main()
