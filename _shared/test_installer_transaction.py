"""installer.transaction: placement through install_transaction.py and the records written after placement.

Expected arguments and texts come from installer_lib/install.sh:75-223, 411-610, 640-653 and installer_lib/targets.sh:248-338.
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
from installer import report, transaction  # noqa: E402
from installer.runner import ToolResult  # noqa: E402
from installer.selection import Target  # noqa: E402
from installer.source import HookPathChange, SourceInfo  # noqa: E402
from installer_testing import FakeRunner, make_context, make_source, quiet, use_source  # noqa: E402


class TransactionCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.source = make_source(root / "src")
        self.enterContext(use_source(self.source))
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GHOST_ALICE_RUNTIME_SHARED_DIR", "GHOST_ALICE_GHOST_ROOT"):
            os.environ.pop(key, None)
        quiet(self.home)
        self.runner = FakeRunner()
        self.ghost = self.home / ".ghost-alice"

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def context(self, platform="claude", **options):
        ctx = make_context(self.home, platform, **options)
        ctx.skills_dir.mkdir(parents=True, exist_ok=True)
        return ctx

    @staticmethod
    def captured(fn, *args, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn(*args, **kwargs)
        return result, out.getvalue()


class PlacementTests(TransactionCase):
    def test_copy_target(self):
        # install.sh:75-93
        ctx = self.context("codex")
        with self.runner.patch():
            ok, _ = self.captured(transaction.copy_target, ctx, self.source / "_shared", ctx.skills_dir / "_shared")
        self.assertTrue(ok)
        self.assertEqual(self.runner.tool_calls("install_transaction.py")[0],
                         ["copy-replace", "--source", str(self.source / "_shared"), "--dest", str(ctx.skills_dir / "_shared"),
                          "--rollback-root", str(self.ghost / "install-rollbacks"),
                          "--event-log", str(self.ghost / "install-state" / "codex-events.jsonl")])
        failing = FakeRunner()
        failing.respond_to("install_transaction.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(transaction.copy_target, ctx, self.source / "alpha", ctx.skills_dir / "alpha")
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] Staged copy replace failed: {ctx.skills_dir / 'alpha'}", out)

    def test_copy_targets_batch(self):
        # install.sh:105-223, 1018-1039
        ctx = self.context("codex")
        batch = transaction.CopyBatch()
        self.assertTrue(transaction.copy_targets_batch(ctx, batch))
        batch.add(self.source / "_shared", ctx.skills_dir / "_shared", "_shared", "support", "updated")
        batch.add(self.source / "alpha", ctx.skills_dir / "alpha", "alpha", "skill", "new")
        with self.runner.patch():
            self.assertTrue(transaction.copy_targets_batch(ctx, batch, progress_label="  [2/5] Skill sync         "))
        args = self.runner.tool_calls("install_transaction.py")[0]
        self.assertEqual(args[:5], ["copy-replace-many", "--rollback-root", str(self.ghost / "install-rollbacks"),
                                    "--event-log", str(self.ghost / "install-state" / "codex-events.jsonl")])
        self.assertEqual(args[5:11], ["--progress-label", "  [2/5] Skill sync         ", "--progress-status", "updated",
                                      "--progress-status", "new"])
        self.assertNotIn("--progress-event-file", args)
        self.assertEqual(args[-6:], ["--target", str(self.source / "_shared"), str(ctx.skills_dir / "_shared"),
                                     "--target", str(self.source / "alpha"), str(ctx.skills_dir / "alpha")])
        event_file = self.home / "run.events.jsonl"
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_EVENT_FILE": str(event_file)}):
            quiet(self.home, report_child=True)
        child = FakeRunner()
        with child.patch():
            self.assertTrue(transaction.copy_targets_batch(ctx, batch))
        args = child.tool_calls("install_transaction.py")[0]
        self.assertNotIn("--progress-label", args)
        self.assertEqual(args[5:9], ["--progress-event-file", str(event_file), "--progress-platform", "codex"])
        self.assertEqual(args[9:21], ["--progress-target-id", "_shared", "--progress-target-kind", "support",
                                      "--progress-target-status", "updated", "--progress-target-id", "alpha",
                                      "--progress-target-kind", "skill", "--progress-target-status", "new"])
        failing = FakeRunner()
        failing.respond_to("install_transaction.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(transaction.copy_targets_batch, ctx, batch)
        self.assertFalse(ok)
        self.assertIn("[ERROR] Staged copy batch replace failed", out)

    def test_link_or_copy_fallback_matrix(self):
        # install.sh:640-653
        ctx = self.context("claude")
        src, dest = self.source / "alpha", ctx.skills_dir / "alpha"
        with self.runner.patch():
            self.assertEqual(transaction.link_or_copy(ctx, src, dest), 0)
        self.assertEqual(self.runner.tool_calls("install_transaction.py"),
                         [["symlink-replace", "--source", str(src), "--dest", str(dest),
                           "--event-log", str(self.ghost / "install-state" / "claude-events.jsonl")]])
        fallback = FakeRunner()
        fallback.respond(lambda s, a: a[0] == "symlink-replace", ToolResult(1))
        with fallback.patch():
            self.assertEqual(transaction.link_or_copy(ctx, src, dest), 0)
        self.assertEqual([a[0] for a in fallback.tool_calls("install_transaction.py")], ["symlink-replace", "copy-replace"])
        for code in (2, 3, 4, 5):
            with self.subTest(code=code):
                kept = FakeRunner()
                kept.respond(lambda s, a: a[0] == "symlink-replace", ToolResult(code))
                with kept.patch():
                    self.assertEqual(transaction.link_or_copy(ctx, src, dest), code)
                self.assertEqual([a[0] for a in kept.tool_calls("install_transaction.py")], ["symlink-replace"])

    def test_install_shared(self):
        # targets.sh:248-261
        ctx = self.context("codex")
        with self.runner.patch():
            self.assertEqual(transaction.install_shared(ctx, ctx.skills_dir, True), 0)
        self.assertEqual(self.runner.tool_calls("install_transaction.py")[0][0], "copy-replace")
        link_ctx = self.context("claude")
        (link_ctx.skills_dir / "_shared").symlink_to(self.source / "_shared")
        linked = FakeRunner()
        with linked.patch():
            rc, out = self.captured(transaction.install_shared, link_ctx, link_ctx.skills_dir, False)
        self.assertEqual(rc, 0)
        self.assertEqual(linked.calls, [])
        self.assertIn("[WARN] _shared. Already installed (skipped)", out)
        (link_ctx.skills_dir / "_shared").unlink()
        (link_ctx.skills_dir / "_shared").symlink_to(self.home / "elsewhere")
        relink = FakeRunner()
        with relink.patch():
            self.assertEqual(transaction.install_shared(link_ctx, link_ctx.skills_dir, False), 0)
        self.assertEqual(relink.tool_calls("install_transaction.py")[0][0], "symlink-replace")

    def test_sync_runtime_shared(self):
        # install.sh:95-103
        ctx = self.context("claude")
        with self.runner.patch():
            self.assertTrue(transaction.sync_runtime_shared(ctx))
        args = self.runner.tool_calls("install_transaction.py")[0]
        self.assertEqual(args[:5], ["copy-replace", "--source", str(self.source / "_shared"), "--dest",
                                    str(self.ghost / "runtime" / "current" / "_shared")])
        failing = FakeRunner()
        failing.respond_to("install_transaction.py", ToolResult(2))
        with failing.patch():
            ok, out = self.captured(transaction.sync_runtime_shared, ctx)
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] Staged copy replace failed: {self.ghost / 'runtime' / 'current' / '_shared'}", out)

    def test_detect_install_mode_and_removal(self):
        # install.sh:411-426; targets.sh:284-294
        link = self.home / "link"
        link.symlink_to(self.source / "alpha")
        copy = self.home / "copy"
        copy.mkdir()
        self.assertEqual(transaction.detect_install_mode(link, False), "symlink")
        self.assertEqual(transaction.detect_install_mode(copy, True), "copy")
        self.assertEqual(transaction.detect_install_mode(copy, False), "copy-fallback")
        self.assertEqual(transaction.detect_install_mode(self.home / "none", False), "missing")
        ok, out = self.captured(transaction.remove_installed_target, "alpha", link)
        self.assertTrue(ok)
        self.assertFalse(link.is_symlink())
        self.assertTrue((self.source / "alpha" / "SKILL.md").is_file())
        self.assertIn("[OK] alpha removed", out)
        self.assertTrue(transaction.remove_installed_target("copy", copy))
        self.assertFalse(copy.exists())
        self.assertFalse(transaction.remove_installed_target("none", self.home / "none"))

    def test_gateway_guard_and_shared_cleanup(self):
        # targets.sh:275-282, 322-338
        ctx = self.context("claude")
        self.assertEqual(transaction.guard_gateway_removal(ctx, []), 0)
        with self.runner.patch():
            self.assertEqual(transaction.guard_gateway_removal(ctx, [self.home / "a", self.home / "b"]), 0)
        self.assertEqual(self.runner.tool_calls("context_gateway_install.py")[0],
                         ["detach", "--root", str(self.home / "a"), "--root", str(self.home / "b"), "--confirm"])
        shared = ctx.skills_dir / "_shared"
        shared.symlink_to(self.source / "_shared")
        (ctx.skills_dir / "task-router").mkdir()
        self.assertEqual(transaction.cleanup_shared_if_unused(ctx, ctx.skills_dir), 1)
        self.assertTrue(shared.is_symlink())
        (ctx.skills_dir / "task-router").rmdir()
        refused = FakeRunner()
        refused.respond_to("context_gateway_install.py", ToolResult(2))
        with refused.patch():
            self.assertEqual(transaction.cleanup_shared_if_unused(ctx, ctx.skills_dir), 2)
        self.assertTrue(shared.is_symlink())
        with self.runner.patch():
            rc, out = self.captured(transaction.cleanup_shared_if_unused, ctx, ctx.skills_dir)
        self.assertEqual(rc, 0)
        self.assertFalse(shared.is_symlink())
        self.assertIn("[OK] _shared removed", out)
        self.assertEqual(transaction.cleanup_shared_if_unused(ctx, ctx.skills_dir), 1)


class RecordTests(TransactionCase):
    def setUp(self):
        super().setUp()
        self.info = SourceInfo(root=self.source, branch="main", head="abc123def", dirty="clean")
        self.resolve = mock.patch("installer.source.resolve_source", return_value=self.info)
        self.resolve.start()

    def tearDown(self):
        self.resolve.stop()
        super().tearDown()

    def test_verify_after_copy(self):
        # install.sh:428-470
        ctx = self.context("claude")
        ctx.addon_targets = [Target("noop", self.home / "addon" / "noop", "noop")]
        (ctx.skills_dir / "_shared").symlink_to(self.source / "_shared")
        (ctx.skills_dir / "alpha").mkdir()
        with self.runner.patch():
            self.assertTrue(transaction.verify_after_copy(ctx, ctx.skills_dir, False, ["alpha", "fam"]))
        args = self.runner.tool_calls("install_verifier.py")[0]
        self.assertEqual(args[:4], ["--platform", "claude", "--state-root", str(self.ghost / "install-state")])
        self.assertEqual(args[4:9], ["--target", "_shared", str(self.source / "_shared"), str(ctx.skills_dir / "_shared"), "symlink"])
        self.assertEqual(args[9:14], ["--target", "alpha", str(self.source / "alpha"), str(ctx.skills_dir / "alpha"), "copy-fallback"])
        self.assertEqual(args[14:19], ["--target", "one", str(self.source / "fam" / "one"), str(ctx.skills_dir / "one"), "missing"])
        self.assertEqual(args[-5:], ["--target", "noop", str(self.home / "addon" / "noop"), str(ctx.skills_dir / "noop"), "missing"])
        failing = FakeRunner()
        failing.respond_to("install_verifier.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(transaction.verify_after_copy, ctx, ctx.skills_dir, False, ["alpha"])
        self.assertFalse(ok)
        self.assertIn("[ERROR] Post-install source/destination verification failed; aborting before snapshot", out)

    def test_write_ownership_markers(self):
        # install.sh:487-536
        ctx = self.context("codex")
        ctx.addon_targets = [Target("noop", self.home / "addon" / "noop", "noop")]
        (ctx.skills_dir / "_shared").mkdir()
        (ctx.skills_dir / "noop").mkdir()
        with self.runner.patch():
            self.assertTrue(transaction.write_ownership_markers(ctx, ctx.skills_dir, True, ["alpha"]))
        args = self.runner.tool_calls("installer_assets_cli.py")[0]
        self.assertEqual(args[:6], ["--platform", "codex", "--source-repo", str(self.source), "--source-commit", "abc123def"])
        self.assertEqual(args[6:10], ["--target", "_shared", str(ctx.skills_dir / "_shared"), "copy"])
        self.assertEqual(args[10:14], ["--target", "alpha", str(ctx.skills_dir / "alpha"), "missing"])
        self.assertEqual(args[14:19], ["--addon-target", "noop", str(ctx.skills_dir / "noop"), "copy", "noop"])
        failing = FakeRunner()
        failing.respond_to("installer_assets_cli.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(transaction.write_ownership_markers, ctx, ctx.skills_dir, True, ["alpha"])
        self.assertFalse(ok)
        self.assertIn("[ERROR] Ownership marker write failed; aborting before snapshot", out)

    def test_snapshot_and_empty_manifest(self):
        # install.sh:364-409
        ctx = self.context("claude")
        pending = self.ghost / "pending-merges" / "claude"
        with self.runner.patch():
            self.assertTrue(transaction.snapshot_after_install(ctx))
        self.assertTrue(pending.is_dir())
        self.assertEqual(self.runner.tool_calls("snapshot_cli.py")[0],
                         ["--output", str(pending / "snapshot.json"), "--platform", "claude", "--skills-dir", str(ctx.skills_dir)])
        self.assertEqual(self.runner.tool_calls("pending_manifest_writer.py"), [[str(pending / "manifest.json"), "claude"]])
        (pending / "manifest.json").write_text("{}", encoding="utf-8")
        again = FakeRunner()
        with again.patch():
            self.assertTrue(transaction.snapshot_after_install(ctx))
        self.assertEqual(again.tool_calls("pending_manifest_writer.py"), [])
        failing = FakeRunner()
        failing.respond_to("snapshot_cli.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(transaction.snapshot_after_install, ctx)
        self.assertFalse(ok)
        self.assertIn("[ERROR] merge-companion snapshot capture failed; aborting install", out)

    def test_write_install_state_manifest(self):
        # install.sh:571-610
        ctx = self.context("claude")
        (ctx.skills_dir / "_shared").symlink_to(self.source / "_shared")
        change = HookPathChange(changed=True, before_present=True, before="old-hooks")
        with self.runner.patch():
            ok, out = self.captured(transaction.write_install_state_manifest, ctx, ctx.skills_dir, False, ["alpha"], change)
        self.assertTrue(ok)
        state = self.ghost / "install-state" / "claude.json"
        self.assertIn(f"[INFO] Install-state manifest: {state}", out)
        script, args, kwargs = next(c for c in self.runner.calls if c[0].endswith("install_state_writer.py"))
        self.assertEqual(args[:6], ["claude", str(self.source), "main", "abc123def", "clean", str(state)])
        self.assertEqual(args[6:10], ["_shared", str(self.source / "_shared"), str(ctx.skills_dir / "_shared"), "symlink"])
        self.assertEqual(args[10:14], ["alpha", str(self.source / "alpha"), str(ctx.skills_dir / "alpha"), "missing"])
        self.assertEqual(kwargs["env"], {"GHOST_ALICE_SOURCE_REPO_HOOK_CHANGED": "1",
                                         "GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE_PRESENT": "1",
                                         "GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE": "old-hooks",
                                         "GHOST_ALICE_SOURCE_REPO_HOOK_AFTER": "hooks"})
        plain = FakeRunner()
        with plain.patch():
            transaction.write_install_state_manifest(ctx, ctx.skills_dir, False, [], None)
        _, _, kwargs = next(c for c in plain.calls if c[0].endswith("install_state_writer.py"))
        self.assertEqual(kwargs["env"], {"GHOST_ALICE_SOURCE_REPO_HOOK_CHANGED": "0",
                                         "GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE_PRESENT": "0",
                                         "GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE": "",
                                         "GHOST_ALICE_SOURCE_REPO_HOOK_AFTER": "hooks"})
        failing = FakeRunner()
        failing.respond_to("install_state_writer.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(transaction.write_install_state_manifest, ctx, ctx.skills_dir, False, [], None)
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] Install-state manifest write failed; aborting install: {state}", out)


if __name__ == "__main__":
    unittest.main()
