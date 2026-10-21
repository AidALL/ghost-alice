"""installer.transaction: placement through install_transaction.publish in this process, with the gateway
participant the record names, and the records written after placement.

Expected arguments and texts come from installer_lib/install.sh:75-223, 411-610, 640-653 and installer_lib/targets.sh:248-338.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gateway_testing  # noqa: E402
import install_transaction  # noqa: E402
import installed_payload  # noqa: E402
from context_gateway_model import LABEL  # noqa: E402
from installer import gateway_participant, report, transaction  # noqa: E402
from installer.runner import ToolResult  # noqa: E402
from installer.selection import Target  # noqa: E402
from installer.source import HookPathChange, SourceInfo  # noqa: E402
from installer_testing import FakeRunner, make_context, make_source, quiet, use_source  # noqa: E402

COMPANION = "session-intent-analyzer/scripts/session_intent_ledger.py"


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
    """The placement calls each build one publish request and call `install_transaction.publish` in this
    process with the participants the install record names."""

    def setUp(self):
        super().setUp()
        self.info = SourceInfo(root=self.source, branch="main", head="abc123def", dirty="clean")
        self.resolve = self.enterContext(mock.patch("installer.source.resolve_source", return_value=self.info))
        self.enterContext(mock.patch("installer.source.configured_hooks_path", return_value="hooks"))
        self.requests: list[dict] = []
        self.participants: list[list] = []
        self.codes: list[int] = []
        self.chosen = [object()]
        self.enterContext(mock.patch("install_transaction.publish", side_effect=self.publish))
        self.enterContext(mock.patch("installer.gateway_participant.participants", return_value=self.chosen))

    def publish(self, request, participants=(), **kwargs):
        self.requests.append(json.loads(json.dumps(request)))
        self.participants.append(list(participants))
        code = self.codes.pop(0) if self.codes else 0
        if code:
            failure = install_transaction.InstallTransactionError("publish refused")
            if code != 1:
                failure.code, failure.exit_code, failure.outcome, failure.phase = "GATEWAY_PRECONDITION", code, "blocked", "stop"
            raise failure
        return "20261020T000000000000Z-1-aaaaaaaa"

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

    def test_copy_target_publishes_one_copy_with_the_records_participants(self):
        # install.sh:75-93
        ctx = self.context("codex")
        dest = ctx.skills_dir / "alpha"
        with self.runner.patch():
            ok, _ = self.captured(transaction.copy_target, ctx, Target("alpha", self.source / "alpha"), dest)
        self.assertTrue(ok)
        self.assertEqual(self.requests, [self.expected_request(
            "codex", self.expected_target("alpha", self.source / "alpha", dest, "copy"))])
        self.assertEqual(self.participants, [self.chosen])
        # No child process publishes: the participants are objects of this process.
        self.assertEqual(self.runner.tool_calls("install_transaction.py"), [])
        self.codes.append(1)
        with self.runner.patch():
            ok, out = self.captured(transaction.copy_target, ctx, Target("alpha", self.source / "alpha"), dest)
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] Staged copy replace failed: {dest}", out)

    def test_a_refused_publish_prints_its_cause_and_keeps_its_exit_code(self):
        ctx = self.context("codex")
        dest = ctx.skills_dir / "alpha"
        self.codes.append(3)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = transaction.publish(ctx, [self.expected_target("alpha", self.source / "alpha", dest, "copy")])
        self.assertEqual((result.ok, result.returncode), (False, 3))
        self.assertEqual(json.loads(stderr.getvalue()), {"status": "blocked", "error_code": "GATEWAY_PRECONDITION",
                                                         "phase": "stop", "detail": "publish refused"})
        self.codes.append(1)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = transaction.publish(ctx, [self.expected_target("alpha", self.source / "alpha", dest, "copy")])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(stderr.getvalue().strip(), "publish refused")

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
                with self.runner.patch(), contextlib.redirect_stderr(io.StringIO()):
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
        self.requests.clear()
        rc, out = self.captured(transaction.install_shared, link_ctx, link_ctx.skills_dir, False)
        self.assertEqual(rc, 0)
        self.assertEqual(self.requests, [])
        self.assertIn("[WARN] _shared. Already installed (skipped)", out)
        (link_ctx.skills_dir / "_shared").unlink()
        (link_ctx.skills_dir / "_shared").symlink_to(self.home / "elsewhere")
        with self.runner.patch():
            self.assertEqual(transaction.install_shared(link_ctx, link_ctx.skills_dir, False), 0)
        self.assertEqual(self.requests[0]["targets"], [self.expected_target(
            "_shared", self.source / "_shared", link_ctx.skills_dir / "_shared", "link", kind="support")])

    def test_sync_runtime_shared_publishes_the_shared_dir_and_the_companion_in_one_request(self):
        # install.sh:95-103
        ctx = self.context("claude")
        companion = self.source / COMPANION
        companion.parent.mkdir(parents=True)
        companion.write_text("ledger", encoding="utf-8")
        runtime = self.ghost / "runtime" / "current"
        with self.runner.patch():
            ok, out = self.captured(transaction.sync_runtime_shared, ctx)
        self.assertTrue(ok)
        self.assertEqual(self.requests[0]["targets"], [
            self.expected_target("_shared", self.source / "_shared", runtime / "_shared", "copy", kind="runtime"),
            self.expected_target(COMPANION, companion, runtime / COMPANION, "copy", kind="runtime")])
        self.codes.append(2)
        with self.runner.patch(), contextlib.redirect_stderr(io.StringIO()):
            ok, out = self.captured(transaction.sync_runtime_shared, ctx)
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] Staged copy replace failed: {runtime / '_shared'}", out)

    def test_a_checkout_without_the_companion_publishes_the_shared_dir_alone(self):
        ctx = self.context("claude")
        with self.runner.patch():
            self.assertTrue(transaction.sync_runtime_shared(ctx))
        self.assertEqual([t["name"] for t in self.requests[0]["targets"]], ["_shared"])

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

    def test_placement_runs_no_child_tool(self):
        ctx = self.context("codex")
        with self.runner.patch():
            transaction.install_shared(ctx, ctx.skills_dir, True)
            transaction.sync_runtime_shared(ctx)
            transaction.link_or_copy(ctx, Target("alpha", self.source / "alpha"), ctx.skills_dir / "alpha")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(len(self.requests), 3)

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
        with mock.patch("installer.gateway_participant.guard", return_value=0) as guard:
            self.assertEqual(transaction.guard_gateway_removal(ctx, [self.home / "a", self.home / "b"]), 0)
        guard.assert_called_once_with(self.ghost, [self.home / "a", self.home / "b"])
        self.assertEqual(self.runner.calls, [])
        shared = ctx.skills_dir / "_shared"
        shared.symlink_to(self.source / "_shared")
        (ctx.skills_dir / "task-router").mkdir()
        self.assertEqual(transaction.cleanup_shared_if_unused(ctx, ctx.skills_dir), 1)
        self.assertTrue(shared.is_symlink())
        (ctx.skills_dir / "task-router").rmdir()
        with mock.patch("installer.gateway_participant.guard", return_value=2):
            self.assertEqual(transaction.cleanup_shared_if_unused(ctx, ctx.skills_dir), 2)
        self.assertTrue(shared.is_symlink())
        with mock.patch("installer.gateway_participant.guard", return_value=0) as guard:
            rc, out = self.captured(transaction.cleanup_shared_if_unused, ctx, ctx.skills_dir)
        guard.assert_called_once_with(self.ghost, [shared])
        self.assertEqual(rc, 0)
        self.assertFalse(shared.is_symlink())
        self.assertIn("[OK] _shared removed", out)
        self.assertEqual(transaction.cleanup_shared_if_unused(ctx, ctx.skills_dir), 1)


def unrecorded_launchd(effects, platform="darwin"):
    """Answer launchd questions with `effects` (a fake launchd) on `platform`."""
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(sys, "platform", platform))
    stack.enter_context(mock.patch("context_gateway_install._effects", return_value=effects))
    return stack


UNRECORDED_LINE = ("Context gateway: a loaded service runs from the runtime without an install record entry; register "
                   "it with bash install.sh gateway --codex-dir <dir> --ledger-root <dir> --state-dir <dir> "
                   "--launch-agent <plist>, which restarts it")


class GatewayReportTests(TransactionCase):
    """After the runtime publish, the install says whether a context gateway is registered and running. Read-only."""

    def register(self):
        reg = gateway_testing.registered_home(self, self.home)
        return reg

    def test_no_service_section_and_no_loaded_service_prints_nothing(self):
        absent = gateway_testing.FakeEffects(self.home, running=False)
        with self.runner.patch(), unrecorded_launchd(absent):
            observed, out = self.captured(transaction.report_gateway, self.context("claude"))
        self.assertEqual(out, "")
        self.assertIsNone(observed)
        self.assertEqual(self.runner.calls, [])
        # A receipt and a binding alone are not a registration the installer reports.
        (self.ghost / "context-gateway").mkdir(parents=True)
        (self.ghost / "context-gateway" / "installation.json").write_text("{}", encoding="utf-8")
        (self.ghost / "context-gateway-binding.json").write_text("{}", encoding="utf-8")
        with self.runner.patch(), unrecorded_launchd(absent):
            observed, out = self.captured(transaction.report_gateway, self.context("claude"))
        self.assertEqual((observed, out), (None, ""))

    def test_a_registered_running_gateway_is_one_info_line(self):
        reg = self.register()
        self.runner.respond(lambda script, args: script == "command" and args[:2] == ["launchctl", "print"], ToolResult(0))
        with self.runner.patch():
            observed, out = self.captured(transaction.report_gateway, self.context("codex"))
        line = f"Context gateway: registered, runtime {reg.runtime_root}, service running"
        self.assertIn(f"[INFO] {line}", out)
        self.assertEqual(observed, (line, True))
        self.assertEqual(self.runner.commands()[0][:2], ["launchctl", "print"])
        self.assertTrue(self.runner.commands()[0][2].endswith("/org.ghost-alice.context-gateway"))

    def test_a_stopped_service_is_a_warning(self):
        reg = self.register()
        self.runner.respond(lambda script, args: script == "command", ToolResult(113))
        with self.runner.patch():
            observed, out = self.captured(transaction.report_gateway, self.context("codex"))
        line = f"Context gateway: registered, runtime {reg.runtime_root}, service not running"
        self.assertIn(f"[WARN] {line}", out)
        self.assertEqual(observed, (line, False))

    def test_an_unreadable_record_is_a_warning_and_the_install_goes_on(self):
        record = installed_payload.path(self.ghost)
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("{broken", encoding="utf-8")
        with self.runner.patch():
            observed, out = self.captured(transaction.report_gateway, self.context("claude"))
        self.assertIn(f"[WARN] Context gateway: the install record cannot be read: {record}", out)
        self.assertEqual(observed[1], False)


class UnrecordedServiceTests(TransactionCase):
    """A gateway service that launchd has loaded but the install record does not name (a migration that could not
    finish, a hand-registered service). The installer has no record of its files, so it neither stops nor starts
    it and does not hold the publish; after the runtime publish one warning names the registration command, which
    records the service and restarts it on the new code."""

    def setUp(self):
        super().setUp()
        self.reg = gateway_testing.registered_home(self, self.home, service=False)
        self.agent = self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def loaded(self, program, *, agent_known=True, raw=None):
        self.agent.parent.mkdir(parents=True, exist_ok=True)
        self.agent.write_bytes(raw if raw is not None else plistlib.dumps({"Label": LABEL, "ProgramArguments": [
            sys.executable, "-B", str(program), "--config", str(self.reg.state_path)]}))
        return gateway_testing.FakeEffects(self.home, running=True, agent=self.agent if agent_known else None)

    def test_the_runtime_publish_goes_on_and_one_warning_names_the_registration(self):
        effects = self.loaded(self.reg.program)
        ctx = self.context("codex")
        with unrecorded_launchd(effects):
            published, _ = self.captured(transaction.sync_runtime_shared, ctx)
            observed, out = self.captured(transaction.report_gateway, ctx)
        self.assertTrue(published)
        self.assertEqual((self.reg.shared / "install_hooks.py").read_text(encoding="utf-8"), "")
        self.assertIn(f"[WARN] {UNRECORDED_LINE}\n", out)
        self.assertEqual(observed, (UNRECORDED_LINE, True))
        self.assertEqual(effects.service_calls(), [], "the installer never stops or starts a service it has no record of")
        self.assertIsNone(self.reg.service())

    def test_the_line_names_the_install_command_the_shell_reported(self):
        effects = self.loaded(self.reg.program)
        with unrecorded_launchd(effects), mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_COMMAND": ".\\install.cmd"}):
            observed, _ = self.captured(transaction.report_gateway, self.context("codex"))
        self.assertIsNotNone(observed)
        self.assertIn("register it with .\\install.cmd gateway --codex-dir", observed[0])

    def test_a_loaded_service_that_runs_elsewhere_is_not_reported(self):
        effects = self.loaded(self.home / "elsewhere" / "_shared" / "context_gateway.py")
        with unrecorded_launchd(effects):
            observed, out = self.captured(transaction.report_gateway, self.context("codex"))
        self.assertEqual((observed, out), (None, ""))

    def test_an_inconclusive_observation_counts_as_none_and_stays_in_the_details(self):
        for name, effects in (("unknown definition", self.loaded(self.reg.program, agent_known=False)),
                              ("unreadable plist", self.loaded(self.reg.program, raw=b"not a plist"))):
            with self.subTest(case=name), unrecorded_launchd(effects):
                observed, out = self.captured(transaction.report_gateway, self.context("codex"))
                self.assertIsNone(observed)
                self.assertNotIn("[WARN]", out)
                self.assertIn("[INFO] Context gateway: launchd observation was inconclusive", out)
                self.assertEqual(effects.service_calls(), [])

    def test_launchd_is_asked_only_on_macos(self):
        effects = self.loaded(self.reg.program)
        with mock.patch.object(effects, "observe", side_effect=AssertionError("launchd asked")) as observe, \
                unrecorded_launchd(effects, platform="linux"):
            observed, out = self.captured(transaction.report_gateway, self.context("codex"))
        observe.assert_not_called()
        self.assertEqual((observed, out), (None, ""))

    def test_the_report_and_the_removal_share_one_launchd_observation(self):
        # The report reads launchd through the removal's own observation function, not a copy of it.
        seen = gateway_participant.LoadedService("loaded", program=self.reg.program)
        with mock.patch("installer.gateway_participant.loaded_service", return_value=seen) as observation:
            observed, _ = self.captured(transaction.report_gateway, self.context("codex"))
        observation.assert_called_once()
        self.assertEqual(observed, (UNRECORDED_LINE, True))


class RecordMigrationTests(TransactionCase):
    """The first install over an earlier installer's records creates the install record from them once, then
    the gateway's service section from its receipt."""

    def setUp(self):
        super().setUp()
        info = SourceInfo(root=self.source, branch="main", head="abc123def", dirty="clean")
        self.enterContext(mock.patch("installer.source.resolve_source", return_value=info))
        self.enterContext(mock.patch("installer.source.configured_hooks_path", return_value="hooks"))

    def test_migration_reads_every_host_and_reports_what_it_created(self):
        ctx = self.context("claude")
        with mock.patch("installed_payload.migrate", return_value={"targets": 8, "migrated": 5, "unknown": 3}) as migrate, \
             mock.patch("installed_payload.migrate_service", return_value=None):
            _, out = self.captured(transaction.migrate_record, ctx)
        migrate.assert_called_once_with(self.ghost, ("claude", "codex"), installer_commit="abc123def")
        self.assertIn("[INFO] Install record created from earlier install records: 8 targets (migrated 5, unknown 3)",
                      out)

    def test_the_service_section_is_migrated_after_the_targets_and_reported(self):
        ctx = self.context("claude")
        order = []
        with mock.patch("installed_payload.migrate", side_effect=lambda *a, **k: order.append("targets")), \
             mock.patch("installed_payload.migrate_service",
                        side_effect=lambda root: order.append("service") or
                        {"label": "org.ghost-alice.context-gateway", "companions": 1}) as migrate_service:
            _, out = self.captured(transaction.migrate_record, ctx)
        migrate_service.assert_called_once_with(self.ghost)
        self.assertEqual(order, ["targets", "service"])
        self.assertIn("[INFO] Gateway record created from its installation receipt: org.ghost-alice.context-gateway "
                      "(companion targets added 1)", out)

    def test_nothing_to_migrate_prints_nothing(self):
        with mock.patch("installed_payload.migrate", return_value=None), \
             mock.patch("installed_payload.migrate_service", return_value=None):
            _, out = self.captured(transaction.migrate_record, self.context("codex"))
        self.assertEqual(out, "")

    def test_a_migration_that_cannot_finish_is_reported_and_the_install_goes_on(self):
        with mock.patch("installed_payload.migrate", side_effect=installed_payload.RecordError("bad snapshot")), \
             mock.patch("installed_payload.migrate_service", side_effect=installed_payload.RecordError("no receipt")):
            _, out = self.captured(transaction.migrate_record, self.context("codex"))
        self.assertIn("[WARN] Install record migration skipped: bad snapshot", out)
        self.assertIn("[WARN] Gateway record migration skipped: no receipt", out)


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
