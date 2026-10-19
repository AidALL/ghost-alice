"""installer.transaction: placement through install_transaction.py and the records written after placement.

Expected arguments and texts come from installer_lib/install.sh:75-223, 411-610, 640-653 and installer_lib/targets.sh:248-338.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installed_payload  # noqa: E402
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
    """The five placement calls each write one publish request and run `install_transaction.py publish --request`."""

    def setUp(self):
        super().setUp()
        self.info = SourceInfo(root=self.source, branch="main", head="abc123def", dirty="clean")
        self.resolve = self.enterContext(mock.patch("installer.source.resolve_source", return_value=self.info))
        self.enterContext(mock.patch("installer.source.configured_hooks_path", return_value="hooks"))
        self.requests: list[dict] = []
        self.request_files: list[Path] = []
        self.codes: list[int] = []
        self.runner.respond(lambda script, args: script.endswith("install_transaction.py"), self.publish)

    def publish(self, script, args, kwargs):
        self.assertEqual(args[:2], ["publish", "--request"])
        self.assertEqual(len(args), 3)
        path = Path(args[2])
        self.request_files.append(path)
        self.requests.append(json.loads(path.read_text(encoding="utf-8")))
        return ToolResult(self.codes.pop(0) if self.codes else 0)

    def expected_request(self, host, *targets, **extra):
        return {"schema_version": 1, "host": host, "ghost_root": str(self.ghost),
                "event_log": str(self.ghost / "install-state" / f"{host}-events.jsonl"),
                "installer": {"commit": "abc123def", "schema_version": 1},
                "source": {"root": str(self.source), "commit": "abc123def", "dirty": "clean", "hooks_path": "hooks"},
                "targets": list(targets), **extra}

    def expected_target(self, name, source, dest, mode, *, kind="skill", provider="core", commit="abc123def",
                        status=None):
        target = {"name": name, "kind": kind, "provider": provider, "source": str(source), "source_commit": commit,
                  "dest": str(dest), "mode": mode}
        if status:
            target["status"] = status
        return target

    def test_copy_target_publishes_one_copy(self):
        # install.sh:75-93
        ctx = self.context("codex")
        dest = ctx.skills_dir / "alpha"
        with self.runner.patch():
            ok, _ = self.captured(transaction.copy_target, ctx, Target("alpha", self.source / "alpha"), dest)
        self.assertTrue(ok)
        self.assertEqual(self.requests, [self.expected_request(
            "codex", self.expected_target("alpha", self.source / "alpha", dest, "copy"))])
        # The request file is transport only: the installer removes it once the child returns.
        self.assertFalse(self.request_files[0].exists())
        self.codes.append(1)
        with self.runner.patch():
            ok, out = self.captured(transaction.copy_target, ctx, Target("alpha", self.source / "alpha"), dest)
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] Staged copy replace failed: {dest}", out)

    def test_copy_targets_batch_publishes_every_target_in_one_request(self):
        # install.sh:105-223, 1018-1039
        ctx = self.context("codex")
        batch = transaction.CopyBatch()
        with self.runner.patch():
            self.assertTrue(transaction.copy_targets_batch(ctx, batch))
        self.assertEqual(self.requests, [])
        addon = self.home / "addon" / "noop"
        batch.add(self.source / "_shared", ctx.skills_dir / "_shared", "_shared", "support", "updated")
        batch.add(self.source / "alpha", ctx.skills_dir / "alpha", "alpha", "skill", "new")
        batch.add(addon, ctx.skills_dir / "noop", "noop", "skill", "new", "addon:noop")
        with mock.patch("installer.source.commit_of", return_value="feed42") as commit_of, self.runner.patch():
            self.assertTrue(transaction.copy_targets_batch(ctx, batch, progress_label="  [2/5] Skill sync         "))
        commit_of.assert_called_once_with(addon)
        self.assertEqual(self.requests, [self.expected_request(
            "codex",
            self.expected_target("_shared", self.source / "_shared", ctx.skills_dir / "_shared", "copy",
                                 kind="support", status="updated"),
            self.expected_target("alpha", self.source / "alpha", ctx.skills_dir / "alpha", "copy", status="new"),
            self.expected_target("noop", addon, ctx.skills_dir / "noop", "copy", provider="addon:noop",
                                 commit="feed42", status="new"),
            progress={"label": "  [2/5] Skill sync         "})])
        event_file = self.home / "run.events.jsonl"
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_EVENT_FILE": str(event_file)}):
            quiet(self.home, report_child=True)
        self.requests.clear()
        with mock.patch("installer.source.commit_of", return_value="feed42"), self.runner.patch():
            self.assertTrue(transaction.copy_targets_batch(ctx, batch))
        self.assertEqual(self.requests[0]["progress"], {"event_file": str(event_file)})
        self.codes.append(1)
        with mock.patch("installer.source.commit_of", return_value="feed42"), self.runner.patch():
            ok, out = self.captured(transaction.copy_targets_batch, ctx, batch)
        self.assertFalse(ok)
        self.assertIn("[ERROR] Staged copy batch replace failed", out)

    def test_link_or_copy_falls_back_to_a_copy_only_on_exit_code_1(self):
        # install.sh:640-653
        ctx = self.context("claude")
        target, dest = Target("alpha", self.source / "alpha"), ctx.skills_dir / "alpha"
        with self.runner.patch():
            self.assertEqual(transaction.link_or_copy(ctx, target, dest), 0)
        self.assertEqual(self.requests, [self.expected_request(
            "claude", self.expected_target("alpha", self.source / "alpha", dest, "link"))])
        self.requests.clear()
        self.codes.append(1)
        with self.runner.patch():
            self.assertEqual(transaction.link_or_copy(ctx, target, dest), 0)
        self.assertEqual([r["targets"][0]["mode"] for r in self.requests], ["link", "copy"])
        for code in (2, 3, 4, 5):
            with self.subTest(code=code):
                self.requests.clear()
                self.codes.append(code)
                with self.runner.patch():
                    self.assertEqual(transaction.link_or_copy(ctx, target, dest), code)
                self.assertEqual([r["targets"][0]["mode"] for r in self.requests], ["link"])

    def test_install_shared(self):
        # targets.sh:248-261
        ctx = self.context("codex")
        with self.runner.patch():
            self.assertEqual(transaction.install_shared(ctx, ctx.skills_dir, True), 0)
        self.assertEqual(self.requests[0]["targets"], [self.expected_target(
            "_shared", self.source / "_shared", ctx.skills_dir / "_shared", "copy", kind="support")])
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
        self.requests.clear()
        with self.runner.patch():
            self.assertEqual(transaction.install_shared(link_ctx, link_ctx.skills_dir, False), 0)
        self.assertEqual(self.requests[0]["targets"], [self.expected_target(
            "_shared", self.source / "_shared", link_ctx.skills_dir / "_shared", "link", kind="support")])

    def test_sync_runtime_shared(self):
        # install.sh:95-103
        ctx = self.context("claude")
        runtime = self.ghost / "runtime" / "current" / "_shared"
        with self.runner.patch():
            self.assertTrue(transaction.sync_runtime_shared(ctx))
        self.assertEqual(self.requests[0]["targets"], [self.expected_target(
            "_shared", self.source / "_shared", runtime, "copy", kind="runtime")])
        self.codes.append(2)
        with self.runner.patch():
            ok, out = self.captured(transaction.sync_runtime_shared, ctx)
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] Staged copy replace failed: {runtime}", out)

    def test_links_already_pointing_at_their_source_are_recorded_in_one_request(self):
        ctx = self.context("claude")
        with self.runner.patch():
            transaction.record_current_links(ctx, [])
        self.assertEqual(self.requests, [])
        links = [(Target("_shared", self.source / "_shared"), ctx.skills_dir / "_shared", "support"),
                 (Target("alpha", self.source / "alpha"), ctx.skills_dir / "alpha", "skill")]
        with self.runner.patch():
            transaction.record_current_links(ctx, links)
        self.assertEqual(self.requests, [self.expected_request(
            "claude",
            self.expected_target("_shared", self.source / "_shared", ctx.skills_dir / "_shared", "link",
                                 kind="support", status="current"),
            self.expected_target("alpha", self.source / "alpha", ctx.skills_dir / "alpha", "link",
                                 status="current"))])
        self.codes.append(1)
        with self.runner.patch():
            _, out = self.captured(transaction.record_current_links, ctx, links)
        self.assertIn("[WARN] The install record could not record the targets that were already installed", out)

    def test_the_source_is_read_once_per_command(self):
        ctx = self.context("claude")
        with self.runner.patch():
            transaction.sync_runtime_shared(ctx)
            transaction.link_or_copy(ctx, Target("alpha", self.source / "alpha"), ctx.skills_dir / "alpha")
        self.assertEqual(self.resolve.call_count, 1)
        self.assertEqual(len(self.requests), 2)

    def test_placement_never_calls_the_earlier_replacement_commands(self):
        ctx = self.context("codex")
        with self.runner.patch():
            transaction.install_shared(ctx, ctx.skills_dir, True)
            transaction.sync_runtime_shared(ctx)
            transaction.link_or_copy(ctx, Target("alpha", self.source / "alpha"), ctx.skills_dir / "alpha")
        self.assertEqual({args[0] for args in self.runner.tool_calls("install_transaction.py")}, {"publish"})

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


class RecordMigrationTests(TransactionCase):
    """The first install over an earlier installer's records creates the install record from them once."""

    def setUp(self):
        super().setUp()
        info = SourceInfo(root=self.source, branch="main", head="abc123def", dirty="clean")
        self.enterContext(mock.patch("installer.source.resolve_source", return_value=info))
        self.enterContext(mock.patch("installer.source.configured_hooks_path", return_value="hooks"))

    def test_migration_reads_every_host_and_reports_what_it_created(self):
        ctx = self.context("claude")
        with mock.patch("installed_payload.migrate", return_value={"targets": 8, "migrated": 5, "unknown": 3}) as migrate:
            _, out = self.captured(transaction.migrate_record, ctx)
        migrate.assert_called_once_with(self.ghost, ("claude", "codex"), installer_commit="abc123def")
        self.assertIn("[INFO] Install record created from earlier install records: 8 targets (migrated 5, unknown 3)",
                      out)

    def test_nothing_to_migrate_prints_nothing(self):
        with mock.patch("installed_payload.migrate", return_value=None):
            _, out = self.captured(transaction.migrate_record, self.context("codex"))
        self.assertEqual(out, "")

    def test_a_migration_that_cannot_finish_is_reported_and_the_install_goes_on(self):
        with mock.patch("installed_payload.migrate", side_effect=installed_payload.RecordError("bad snapshot")):
            _, out = self.captured(transaction.migrate_record, self.context("codex"))
        self.assertIn("[WARN] Install record migration skipped: bad snapshot", out)


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
