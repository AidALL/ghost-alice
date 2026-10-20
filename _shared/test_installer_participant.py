"""The participant interface of a publish: four points the transaction calls in order, and the restore
points every failure reaches. `installer.participant` defines the interface and the null participant;
`install_transaction.publish` calls them and never imports a gateway module.
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_lock  # noqa: E402
import install_transaction  # noqa: E402
import installed_payload  # noqa: E402
from context_gateway_model import GatewayFailure, GatewayServiceError  # noqa: E402
from installer import participant  # noqa: E402
from test_install_transaction_publish import PublishCase  # noqa: E402


class Recorder:
    """A participant that records every call with the context it received."""

    def __init__(self, *, fail_at: str | None = None, error: Exception | None = None):
        self.calls: list[tuple[str, participant.PublishContext]] = []
        self.fail_at, self.error = fail_at, error

    def _point(self, name, ctx):
        self.calls.append((name, ctx))
        if name == self.fail_at:
            raise self.error

    def before_publish(self, ctx):
        self._point("before_publish", ctx)

    def after_publish(self, ctx):
        self._point("after_publish", ctx)

    def before_restore(self, ctx):
        self._point("before_restore", ctx)

    def after_restore(self, ctx):
        self._point("after_restore", ctx)

    @property
    def names(self):
        return [name for name, _ in self.calls]


class InterfaceTests(unittest.TestCase):
    def test_the_null_participant_implements_the_four_points_and_does_nothing(self):
        null = participant.NullParticipant()
        ctx = participant.PublishContext(ghost_root=Path("/g"), host="claude", txid="t", destinations=(Path("/d"),))
        for name in ("before_publish", "after_publish", "before_restore", "after_restore"):
            self.assertIsNone(getattr(null, name)(ctx))
        self.assertIsInstance(null, participant.Participant)
        self.assertIsInstance(Recorder(), participant.Participant)
        self.assertNotIsInstance(object(), participant.Participant)

    def test_the_context_is_frozen_data(self):
        ctx = participant.PublishContext(ghost_root=Path("/g"), host="codex", txid="t", destinations=(Path("/a"), Path("/b")))
        with self.assertRaises(Exception):
            ctx.host = "claude"
        self.assertEqual(ctx.destinations, (Path("/a"), Path("/b")))


class PublishOrderTests(PublishCase):
    def test_publish_calls_before_publish_then_after_publish_with_the_replaced_locations(self):
        recorder = Recorder()
        current = self.claude / "alpha"
        current.symlink_to(self.src / "alpha", target_is_directory=True)
        new = self.claude / "beta"
        seen = {}

        def observing_update(root, patches):
            seen["names"] = list(recorder.names)
            return real_update(root, patches)

        real_update = installed_payload.update
        with mock.patch.object(install_transaction.installed_payload, "update", side_effect=observing_update):
            txid = install_transaction.publish(self.request(
                "claude", self.target("alpha", current, "link", status="current"), self.target("beta", new, "link")),
                [recorder])
        self.assertEqual(recorder.names, ["before_publish", "after_publish"])
        # The files and the record are published between the two points.
        self.assertEqual(seen["names"], ["before_publish"])
        for _, ctx in recorder.calls:
            self.assertEqual((ctx.ghost_root, ctx.host, ctx.txid), (self.ghost, "claude", txid))
            self.assertEqual(ctx.destinations, (new,))
        self.assertEqual(self.journal(txid)["phase"], "done")

    def test_a_request_of_current_links_only_calls_no_participant(self):
        recorder = Recorder()
        current = self.claude / "alpha"
        current.symlink_to(self.src / "alpha", target_is_directory=True)
        install_transaction.publish(self.request("claude", self.target("alpha", current, "link", status="current")),
                                    [recorder])
        self.assertEqual(recorder.calls, [])

    def test_every_participant_is_called_in_order(self):
        first, second = Recorder(), Recorder()
        order = []
        for recorder, label in ((first, "first"), (second, "second")):
            original = recorder._point
            recorder._point = lambda name, ctx, _o=original, _l=label: (order.append((_l, name)), _o(name, ctx))
        install_transaction.publish(self.request("claude", self.target("alpha", self.claude / "alpha", "link")),
                                    [first, second])
        self.assertEqual(order, [("first", "before_publish"), ("second", "before_publish"),
                                 ("first", "after_publish"), ("second", "after_publish")])

    def test_publish_without_the_install_lock_changes_nothing(self):
        install_lock.release_lock(self.ghost / "install.lock")
        try:
            with self.assertRaisesRegex(install_transaction.InstallTransactionError, "install lock"):
                install_transaction.publish(self.request("claude", self.target("alpha", self.claude / "alpha", "link")))
        finally:
            install_lock.acquire_lock(self.ghost / "install.lock", owner="installer:install:claude:test")
        self.assertFalse((self.claude / "alpha").exists())
        self.assertFalse((self.ghost / "rollbacks").exists())
        self.assertIsNone(self.record())


class RestoreTests(PublishCase):
    """Every failure after the journal reaches before_restore and after_restore, and the cause's exit code stays."""

    def setUp(self):
        super().setUp()
        self.dest = self.codex / "alpha"
        self.old_copy(self.dest, "alpha v1\n")
        self.before_files = self.snapshot(self.dest)

    def publish_with(self, recorder):
        with self.assertRaises(install_transaction.InstallTransactionError) as failed:
            install_transaction.publish(self.request("codex", self.target("alpha", self.dest, "copy")), [recorder])
        return failed.exception

    def only_txid(self):
        names = [p.name for p in (self.ghost / "rollbacks").iterdir()]
        self.assertEqual(len(names), 1, names)
        return names[0]

    def assert_restored(self, recorder, exc, exit_code):
        self.assertEqual(self.snapshot(self.dest), self.before_files)
        self.assertIsNone(self.record())
        self.assertEqual(self.journal(self.only_txid())["phase"], "restored")
        self.assertEqual(install_transaction.unfinished(self.ghost), [])
        self.assertEqual(recorder.names[-2:], ["before_restore", "after_restore"])
        self.assertEqual(getattr(exc, "exit_code", 1), exit_code)

    def test_a_staging_failure_reaches_the_restore_points(self):
        recorder = Recorder()
        with mock.patch.object(install_transaction, "_verify_staged_copy", side_effect=install_transaction.InstallTransactionError("stage")):
            exc = self.publish_with(recorder)
        self.assertEqual(recorder.names, ["before_restore", "after_restore"])
        self.assert_restored(recorder, exc, 1)

    def test_a_before_publish_failure_keeps_its_exit_code_and_changes_no_file(self):
        recorder = Recorder(fail_at="before_publish", error=GatewayServiceError("stop unconfirmed", phase="stop"))
        exc = self.publish_with(recorder)
        self.assertEqual(recorder.names, ["before_publish", "before_restore", "after_restore"])
        self.assert_restored(recorder, exc, 3)
        self.assertEqual((exc.code, exc.phase), ("GATEWAY_SERVICE_UNCONFIRMED", "stop"))

    def test_a_publish_failure_reaches_the_restore_points(self):
        recorder = Recorder()
        real_rename = install_transaction._rename_path
        refused = []

        def failing_rename(source, dest):
            # The publish's rename into the install location fails once; the restore's renames succeed.
            if dest == self.dest and not refused:
                refused.append(dest)
                raise OSError("rename refused")
            return real_rename(source, dest)

        with mock.patch.object(install_transaction, "_rename_path", side_effect=failing_rename):
            exc = self.publish_with(recorder)
        self.assertEqual(recorder.names, ["before_publish", "before_restore", "after_restore"])
        self.assert_restored(recorder, exc, 1)

    def test_an_after_publish_failure_restores_the_published_files(self):
        recorder = Recorder(fail_at="after_publish", error=GatewayFailure("Candidate service is not ready",
                                                                           phase="readiness", restored=True))
        exc = self.publish_with(recorder)
        self.assertEqual(recorder.names, ["before_publish", "after_publish", "before_restore", "after_restore"])
        self.assert_restored(recorder, exc, 4)

    def test_a_failing_restore_point_reports_the_unconfirmed_recovery(self):
        recorder = Recorder(fail_at="after_restore", error=GatewayFailure("Restored service is not ready",
                                                                           phase="readiness", restored=False))
        with mock.patch.object(install_transaction, "_verify_staged_copy", side_effect=install_transaction.InstallTransactionError("stage")):
            exc = self.publish_with(recorder)
        # The files are back and the journal says so; the service recovery is what stays unconfirmed.
        self.assertEqual(self.snapshot(self.dest), self.before_files)
        self.assertEqual(self.journal(self.only_txid())["phase"], "restored")
        self.assertEqual((exc.exit_code, exc.code), (5, "GATEWAY_RECOVERY_UNCONFIRMED"))
        self.assertIn("restore failed", str(exc))

    def test_the_failure_event_names_the_participant_phase(self):
        events = self.home / "events.jsonl"
        recorder = Recorder(fail_at="before_publish", error=GatewayServiceError("stop unconfirmed", phase="stop"))
        with self.assertRaises(install_transaction.InstallTransactionError):
            install_transaction.publish(self.request("codex", self.target("alpha", self.dest, "copy"),
                                                     event_log=str(events)), [recorder])
        self.assertIn('"phase": "stop"', events.read_text(encoding="utf-8"))


class LayerTests(unittest.TestCase):
    def test_install_transaction_knows_only_the_participant_interface(self):
        source = (Path(__file__).resolve().parent / "install_transaction.py").read_text(encoding="utf-8")
        self.assertNotIn("context_gateway", source)
        self.assertIn("installer.participant", source.replace("from installer import participant", "installer.participant"))

    def test_the_publish_subcommand_is_not_a_command_line(self):
        # Publishing happens in the installer process, with its participants; a command line could not carry them.
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
            install_transaction.main(["publish", "--request", os.devnull])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
