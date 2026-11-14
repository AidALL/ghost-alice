"""The gateway participant: when a publish touches the service's code it stops the service before the
files change and starts it after, confirms readiness, and records the restart; when the publish does not,
it calls no service effect. A failed readiness restores the files and starts the prior service.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_digest  # noqa: E402
import gateway_testing  # noqa: E402
import install_transaction  # noqa: E402
import installed_payload  # noqa: E402
from installer import gateway_participant  # noqa: E402


class ParticipantCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reg = gateway_testing.registered_home(self, self.root)
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.source = self.root / "src"
        shutil.copytree(self.reg.runtime_root, self.source)
        (self.source / "_shared" / "context_gateway.py").write_text("new context_gateway.py\n", encoding="utf-8")
        (self.source / gateway_testing.COMPANION).write_text("new companion\n", encoding="utf-8")
        self.original = {p.relative_to(self.reg.runtime_root): p.read_bytes()
                         for p in self.reg.runtime_root.rglob("*") if p.is_file()}

    def effects(self, **kwargs):
        kwargs.setdefault("running", True)
        kwargs.setdefault("agent", self.reg.agent)
        return gateway_testing.FakeEffects(self.root, **kwargs)

    def participant(self, effects, platform="darwin"):
        return gateway_participant.GatewayParticipant(self.reg.ghost, self.reg.service(), effects=effects,
                                                      platform=platform)

    def target(self, name, source, dest, *, kind="runtime"):
        return {"name": name, "kind": kind, "provider": "core", "source": str(source), "source_commit": "c0ffee",
                "dest": str(dest), "mode": "copy"}

    def runtime_request(self):
        return {"schema_version": 1, "host": "claude", "ghost_root": str(self.reg.ghost),
                "installer": {"commit": "c0ffee", "schema_version": 1},
                "source": {"root": str(self.source), "commit": "c0ffee", "dirty": "clean", "hooks_path": "hooks"},
                "targets": [self.target("_shared", self.source / "_shared", self.reg.shared),
                            self.target(gateway_testing.COMPANION, self.source / gateway_testing.COMPANION,
                                        self.reg.companion)]}

    def codex_request(self):
        skills = self.root / ".agents" / "skills"
        skills.mkdir(parents=True, exist_ok=True)
        return {"schema_version": 1, "host": "codex", "ghost_root": str(self.reg.ghost),
                "installer": {"commit": "c0ffee", "schema_version": 1},
                "source": {"root": str(self.source), "commit": "c0ffee", "dirty": "clean", "hooks_path": "hooks"},
                "targets": [self.target("_shared", self.source / "_shared", skills / "_shared", kind="support")]}

    def runtime_files(self):
        return {p.relative_to(self.reg.runtime_root): p.read_bytes() for p in self.reg.runtime_root.rglob("*") if p.is_file()}


class ReplacementTests(ParticipantCase):
    def test_the_runtime_publish_stops_the_service_before_the_files_change_and_starts_it_after(self):
        effects = self.effects()
        rename = install_transaction._rename_path
        seen = []

        def observed(source, destination):
            if destination in (self.reg.shared, self.reg.companion):
                seen.append(effects.running)
            return rename(source, destination)

        before = self.reg.service()
        with mock.patch.object(install_transaction, "_rename_path", side_effect=observed):
            install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual(seen, [False, False], "the service must be stopped while its code is replaced")
        self.assertEqual(effects.service_calls(), ["stop", "start", "ready"])
        self.assertTrue(effects.running)
        self.assertEqual((self.reg.shared / "context_gateway.py").read_text(encoding="utf-8"), "new context_gateway.py\n")
        after = self.reg.service()
        self.assertTrue(after["listener_ready"])
        self.assertNotEqual(after["restarted_at"], before["restarted_at"])
        self.assertEqual({k: v for k, v in after.items() if k not in ("restarted_at", "listener_ready")},
                         {k: v for k, v in before.items() if k not in ("restarted_at", "listener_ready")})
        record = self.reg.record()
        self.assertEqual(record["targets"][str(self.reg.shared)]["files"], content_digest.tree(self.reg.shared))

    def test_the_codex_copy_batch_calls_no_service_effect(self):
        effects = self.effects()
        before = self.reg.service()
        install_transaction.publish(self.codex_request(), [self.participant(effects)])
        self.assertEqual(effects.calls, [])
        self.assertEqual(self.reg.service(), before)
        self.assertTrue((self.root / ".agents" / "skills" / "_shared" / "context_gateway.py").is_file())

    def test_a_service_that_is_not_loaded_is_not_started(self):
        effects = self.effects(running=False)
        before = self.reg.service()
        install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual(effects.service_calls(), [])
        self.assertFalse(effects.running)
        self.assertEqual(self.reg.service(), before)
        self.assertEqual((self.reg.shared / "context_gateway.py").read_text(encoding="utf-8"), "new context_gateway.py\n")

    def test_a_failed_readiness_restores_the_files_and_starts_the_prior_service(self):
        effects = self.effects(ready=[False, True])
        before = self.reg.service()
        starts = []
        start = effects.start

        def observing_start(domain, agent):
            starts.append(self.runtime_files())
            start(domain, agent)

        with mock.patch.object(effects, "start", side_effect=observing_start):
            with self.assertRaises(install_transaction.InstallTransactionError) as failed:
                install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual((failed.exception.exit_code, failed.exception.code), (4, "GATEWAY_OPERATION_RESTORED"))
        self.assertEqual(effects.service_calls(), ["stop", "start", "ready", "stop", "start", "ready"])
        self.assertEqual(self.runtime_files(), self.original)
        self.assertEqual(starts[1], self.original, "the prior service starts only on the restored files")
        self.assertTrue(effects.running)
        self.assertEqual(self.reg.service(), before)
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [])

    def test_a_failed_stop_holds_before_any_file_changes(self):
        effects = self.effects(stop_error=RuntimeError("Service stop failed"))
        with self.assertRaises(install_transaction.InstallTransactionError) as failed:
            install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual((failed.exception.exit_code, failed.exception.code), (3, "GATEWAY_SERVICE_UNCONFIRMED"))
        self.assertEqual(effects.service_calls(), ["stop"])
        self.assertEqual(self.runtime_files(), self.original)
        self.assertTrue(effects.running)

    def test_an_unconfirmed_absence_after_the_stop_holds_and_restarts_the_service(self):
        effects = self.effects()
        stop = effects.stop

        def stop_without_absence(domain):
            stop(domain)
            effects.running = True

        with mock.patch.object(effects, "stop", side_effect=stop_without_absence):
            with self.assertRaises(install_transaction.InstallTransactionError) as failed:
                install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual(failed.exception.exit_code, 3)
        self.assertEqual(self.runtime_files(), self.original)

    def test_a_prior_service_that_will_not_restart_reports_the_unconfirmed_recovery(self):
        effects = self.effects(ready=[False, False])
        with self.assertRaises(install_transaction.InstallTransactionError) as failed:
            install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual((failed.exception.exit_code, failed.exception.code), (5, "GATEWAY_RECOVERY_UNCONFIRMED"))
        self.assertEqual(self.runtime_files(), self.original)
        self.assertEqual(len(install_transaction.unfinished(self.reg.ghost)), 1)

    def test_an_unavailable_service_state_holds_before_any_file_changes(self):
        effects = self.effects()
        with mock.patch.object(effects, "state", side_effect=RuntimeError("Service state is unavailable")):
            with self.assertRaises(install_transaction.InstallTransactionError) as failed:
                install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual(failed.exception.exit_code, 3)
        self.assertEqual(self.runtime_files(), self.original)

    def test_publish_preserves_a_loaded_service_with_a_different_or_unknown_agent(self):
        for observed_agent in (self.root / "unowned.plist", None):
            with self.subTest(observed_agent=observed_agent):
                effects = self.effects(agent=observed_agent)
                record_before = installed_payload.path(self.reg.ghost).read_bytes()
                with self.assertRaises(install_transaction.InstallTransactionError) as failed:
                    install_transaction.publish(self.runtime_request(), [self.participant(effects)])
                self.assertEqual(failed.exception.exit_code, 3)
                self.assertTrue(effects.running)
                self.assertEqual(effects.agent, observed_agent)
                self.assertEqual(effects.service_calls(), [])
                self.assertEqual(self.runtime_files(), self.original)
                self.assertEqual(installed_payload.path(self.reg.ghost).read_bytes(), record_before)

    def test_a_service_without_a_macos_owner_blocks_an_affected_publish(self):
        effects = self.effects()
        with self.assertRaises(install_transaction.InstallTransactionError) as failed:
            install_transaction.publish(self.runtime_request(), [self.participant(effects, platform="linux")])
        self.assertEqual(failed.exception.exit_code, 2)
        self.assertEqual(effects.calls, [])
        self.assertEqual(self.runtime_files(), self.original)

    def test_the_participant_reads_the_listener_coordinates_from_the_state_file(self):
        effects = self.effects()
        install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        ready = [call for call in effects.calls if call[0] == "ready"]
        self.assertEqual(ready, [("ready", gateway_testing.PORT, True)])

    def test_the_participants_of_a_record_use_the_platform_effects_by_default(self):
        [chosen] = gateway_participant.participants(self.reg.ghost)
        import context_gateway_platform as platform
        self.assertIsInstance(chosen.effects, platform.Platform)
        self.assertEqual(chosen.service, self.reg.service())


class JournalTests(ParticipantCase):
    def test_the_journal_says_done_only_after_the_service_is_back(self):
        effects = self.effects()
        phases = []
        start = effects.start

        def observing_start(domain, agent):
            [txid] = [p.name for p in (self.reg.ghost / "rollbacks").iterdir()]
            phases.append(json.loads((self.reg.ghost / "rollbacks" / txid / "journal.json").read_text())["phase"])
            start(domain, agent)

        with mock.patch.object(effects, "start", side_effect=observing_start):
            txid = install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual(phases, ["prepare"])
        self.assertEqual(json.loads((self.reg.ghost / "rollbacks" / txid / "journal.json").read_text())["phase"], "done")


class InterruptedPublishTests(ParticipantCase):
    def interrupt(self, effects, point="after_publish"):
        interruption = mock.Mock(spec=["before_publish", "after_publish", "before_restore", "after_restore"])
        getattr(interruption, point).side_effect = KeyboardInterrupt("interrupted publish")
        with self.assertRaises(KeyboardInterrupt):
            install_transaction.publish(self.runtime_request(), [self.participant(effects), interruption])
        [txid] = install_transaction.unfinished(self.reg.ghost)
        return txid

    def recover(self, txid, effects):
        # Reconstruct from disk through the production recovery entrypoint; the publish participant is gone.
        with mock.patch.object(gateway_participant.gateway, "_effects", return_value=effects), \
                mock.patch.object(gateway_participant.sys, "platform", "darwin"), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("native subprocess forbidden")):
            return install_transaction.recover(self.reg.ghost, txid,
                participant_factory=gateway_participant.recovery_participants)

    def test_low_recovery_cli_refuses_service_facts_without_the_installer_owner(self):
        import contextlib
        import io
        effects = self.effects()
        txid = self.interrupt(effects)
        candidate, calls = self.runtime_files(), effects.service_calls()
        journal = install_transaction.Journal.load(self.reg.ghost, txid)
        saved = journal.data["participants"]
        for state in (saved, [], None, "legacy"):
            with self.subTest(state=state):
                if state == "legacy":
                    journal.data.pop("participants", None)
                else:
                    journal.data["participants"] = state
                journal.save()
                stderr = io.StringIO()
                with mock.patch.object(gateway_participant.gateway, "_effects", return_value=effects), \
                        mock.patch.object(gateway_participant.sys, "platform", "darwin"), \
                        mock.patch("subprocess.Popen", side_effect=AssertionError("native subprocess forbidden")), \
                        contextlib.redirect_stderr(stderr):
                    code = install_transaction.main(["recover", "--ghost-root", str(self.reg.ghost), "--txid", txid])
                self.assertNotEqual(code, 0)
                self.assertIn("installer", stderr.getvalue())
                self.assertEqual(self.runtime_files(), candidate)
                self.assertEqual(effects.service_calls(), calls)
                self.assertTrue(effects.running)
                self.assertEqual(install_transaction.unfinished(self.reg.ghost), [txid])

    def test_low_recovery_accepts_an_old_journal_proven_to_have_no_service(self):
        installed_payload.update(self.reg.ghost, {"service": None})
        interruption = mock.Mock(spec=["before_publish", "after_publish", "before_restore", "after_restore"])
        interruption.after_publish.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            install_transaction.publish(self.runtime_request(), [interruption])
        [txid] = install_transaction.unfinished(self.reg.ghost)
        journal = install_transaction.Journal.load(self.reg.ghost, txid)
        journal.data.pop("participants")
        journal.save()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("native subprocess forbidden")):
            install_transaction.recover(self.reg.ghost, txid)
        self.assertEqual(self.runtime_files(), self.original)
        self.assertIsNone(self.reg.service())
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [])

    def test_recovery_restarts_the_prior_service_after_interruption_following_stop(self):
        effects = self.effects()
        before = installed_payload.path(self.reg.ghost).read_bytes()
        txid = self.interrupt(effects, "before_publish")
        self.assertFalse(effects.running)
        self.recover(txid, effects)
        self.assertTrue(effects.running)
        self.assertEqual(effects.service_calls(), ["stop", "start", "ready"])
        self.assertEqual(self.runtime_files(), self.original)
        self.assertEqual(installed_payload.path(self.reg.ghost).read_bytes(), before)
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [])

    def test_recovery_stops_the_candidate_before_restoring_files_and_starts_the_original(self):
        effects = self.effects()
        before = installed_payload.path(self.reg.ghost).read_bytes()
        txid = self.interrupt(effects)
        removed, starts = [], []
        remove, start = install_transaction._remove_path, effects.start

        def observed_remove(path):
            if Path(path) in (self.reg.shared, self.reg.companion):
                removed.append(effects.running)
            return remove(path)

        def observed_start(domain, agent):
            starts.append(self.runtime_files())
            start(domain, agent)

        with mock.patch.object(install_transaction, "_remove_path", side_effect=observed_remove), \
                mock.patch.object(effects, "start", side_effect=observed_start):
            self.recover(txid, effects)
        self.assertEqual(removed, [False, False])
        self.assertEqual(starts, [self.original])
        self.assertEqual(effects.service_calls(), ["stop", "start", "ready", "stop", "start", "ready"])
        self.assertEqual(installed_payload.path(self.reg.ghost).read_bytes(), before)

    def test_recovery_preserves_an_initially_stopped_service(self):
        effects = self.effects(running=False)
        txid = self.interrupt(effects)
        self.recover(txid, effects)
        self.assertFalse(effects.running)
        self.assertEqual(effects.service_calls(), [])
        self.assertEqual(self.runtime_files(), self.original)

    def test_checkpoint_precedes_stop_even_when_stop_itself_is_interrupted(self):
        effects = self.effects()
        stop = effects.stop

        def interrupted_stop(domain):
            stop(domain)
            raise KeyboardInterrupt("stop completed before interruption")

        with mock.patch.object(effects, "stop", side_effect=interrupted_stop), self.assertRaises(KeyboardInterrupt):
            install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        [txid] = install_transaction.unfinished(self.reg.ghost)
        self.recover(txid, effects)
        self.assertTrue(effects.running)
        self.assertEqual(effects.service_calls(), ["stop", "start", "ready"])

    def test_interrupted_partial_start_is_stopped_before_restore(self):
        effects = self.effects()
        start = effects.start

        def interrupted_start(domain, agent):
            start(domain, agent)
            raise KeyboardInterrupt("start completed before interruption")

        with mock.patch.object(effects, "start", side_effect=interrupted_start), self.assertRaises(KeyboardInterrupt):
            install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        [txid] = install_transaction.unfinished(self.reg.ghost)
        self.recover(txid, effects)
        self.assertTrue(effects.running)
        self.assertEqual(effects.service_calls(), ["stop", "start", "stop", "start", "ready"])
        self.assertEqual(self.runtime_files(), self.original)

    def test_failed_recovery_readiness_leaves_the_journal_unfinished_for_explicit_recovery(self):
        effects = self.effects(ready=[True, False, True])
        txid = self.interrupt(effects)
        with self.assertRaises(install_transaction.InstallTransactionError) as failed:
            self.recover(txid, effects)
        self.assertEqual(failed.exception.exit_code, 5)
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [txid])
        self.assertEqual(self.runtime_files(), self.original)
        self.recover(txid, effects)
        self.assertTrue(effects.running)
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [])

    def test_recovery_can_resume_after_files_and_service_return_before_the_final_mark(self):
        effects = self.effects()
        before = installed_payload.path(self.reg.ghost).read_bytes()
        txid = self.interrupt(effects)
        with mock.patch.object(install_transaction.Journal, "mark", side_effect=KeyboardInterrupt()), \
                self.assertRaises(KeyboardInterrupt):
            self.recover(txid, effects)
        self.assertEqual(self.runtime_files(), self.original)
        self.assertEqual(installed_payload.path(self.reg.ghost).read_bytes(), before)
        self.assertTrue(effects.running)
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [txid])
        self.recover(txid, effects)
        self.assertTrue(effects.running)
        self.assertEqual(self.runtime_files(), self.original)
        self.assertEqual(installed_payload.path(self.reg.ghost).read_bytes(), before)
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [])

    def test_candidate_stop_failure_preserves_candidate_files_and_unfinished_journal(self):
        effects = self.effects()
        txid = self.interrupt(effects)
        candidate = self.runtime_files()
        effects.stop_error = RuntimeError("stop unavailable")
        with self.assertRaises(install_transaction.InstallTransactionError) as failed:
            self.recover(txid, effects)
        self.assertEqual(failed.exception.exit_code, 5)
        self.assertEqual(self.runtime_files(), candidate)
        self.assertTrue(effects.running)
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [txid])

    def test_recovery_preserves_a_different_loaded_service(self):
        effects = self.effects()
        txid = self.interrupt(effects)
        candidate, calls = self.runtime_files(), effects.service_calls()
        effects.agent = self.root / "foreign.plist"
        with self.assertRaises(install_transaction.InstallTransactionError):
            self.recover(txid, effects)
        self.assertEqual(self.runtime_files(), candidate)
        self.assertEqual(effects.service_calls(), calls)
        self.assertTrue(effects.running)

    def test_old_affected_journal_without_running_state_holds_before_mutation(self):
        effects = self.effects()
        txid = self.interrupt(effects)
        journal = install_transaction.Journal.load(self.reg.ghost, txid)
        journal.data.pop("participants", None)
        journal.save()
        candidate, calls = self.runtime_files(), effects.service_calls()
        with self.assertRaisesRegex(install_transaction.InstallTransactionError, "running state"):
            self.recover(txid, effects)
        self.assertEqual(self.runtime_files(), candidate)
        self.assertEqual(effects.service_calls(), calls)
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [txid])

    def test_old_unrelated_journal_restores_files_without_service_effects(self):
        effects = self.effects()
        interruption = mock.Mock(spec=["before_publish", "after_publish", "before_restore", "after_restore"])
        interruption.after_publish.side_effect = KeyboardInterrupt()
        request = self.codex_request()
        with self.assertRaises(KeyboardInterrupt):
            install_transaction.publish(request, [self.participant(effects), interruption])
        [txid] = install_transaction.unfinished(self.reg.ghost)
        journal = install_transaction.Journal.load(self.reg.ghost, txid)
        journal.data.pop("participants", None)
        journal.save()
        self.recover(txid, effects)
        self.assertFalse(Path(request["targets"][0]["dest"]).exists())
        self.assertEqual(effects.service_calls(), [])
        self.assertTrue(effects.running)

    def test_file_only_recovery_preserves_an_original_unreadable_record(self):
        raw = b"unreadable original record\n"
        installed_payload.path(self.reg.ghost).write_bytes(raw)
        interruption = mock.Mock(spec=["before_publish", "after_publish", "before_restore", "after_restore"])
        interruption.before_publish.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            install_transaction.publish(self.codex_request(), [interruption])
        [txid] = install_transaction.unfinished(self.reg.ghost)
        self.recover(txid, self.effects())
        self.assertEqual(installed_payload.path(self.reg.ghost).read_bytes(), raw)
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [])

    def test_checkpoint_write_failure_causes_no_service_effect(self):
        effects = self.effects()
        with mock.patch.object(install_transaction.Journal, "checkpoint", side_effect=OSError("disk unavailable")), \
                self.assertRaises(install_transaction.InstallTransactionError):
            install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual(effects.service_calls(), [])
        self.assertTrue(effects.running)
        self.assertEqual(self.runtime_files(), self.original)

    def test_stop_error_after_effect_restores_the_prior_running_service(self):
        effects = self.effects()
        stop = effects.stop

        def failed_after_stop(domain):
            stop(domain)
            raise RuntimeError("stop response lost after effect")

        with mock.patch.object(effects, "stop", side_effect=failed_after_stop), \
                self.assertRaises(install_transaction.InstallTransactionError) as failed:
            install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual(failed.exception.exit_code, 3)
        self.assertTrue(effects.running)
        self.assertEqual(effects.service_calls(), ["stop", "start", "ready"])
        self.assertEqual(self.runtime_files(), self.original)
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [])

    def test_unconfirmed_state_after_stop_error_leaves_recovery_pending(self):
        effects = self.effects()
        stop, state = effects.stop, effects.state

        def failed_after_stop(domain):
            stop(domain)
            raise RuntimeError("stop response lost after effect")

        def failed_observation_after_stop(domain):
            if not effects.running:
                raise RuntimeError("service observation unavailable")
            return state(domain)

        with mock.patch.object(effects, "stop", side_effect=failed_after_stop), \
                mock.patch.object(effects, "state", side_effect=failed_observation_after_stop), \
                self.assertRaises(install_transaction.InstallTransactionError) as failed:
            install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual(failed.exception.exit_code, 5)
        self.assertEqual(len(install_transaction.unfinished(self.reg.ghost)), 1)
        self.assertEqual(self.runtime_files(), self.original)

    def test_recovery_does_not_stop_a_service_started_outside_an_initially_stopped_publish(self):
        effects = self.effects(running=False)
        txid = self.interrupt(effects)
        candidate = self.runtime_files()
        effects.running = True
        with self.assertRaises(install_transaction.InstallTransactionError):
            self.recover(txid, effects)
        self.assertEqual(effects.service_calls(), [])
        self.assertEqual(self.runtime_files(), candidate)
        self.assertTrue(effects.running)

    def test_recovery_checks_every_target_before_service_effects(self):
        effects = self.effects()
        txid = self.interrupt(effects)
        self.reg.program.write_text("external runtime edit\n")
        calls = effects.service_calls()
        with self.assertRaises(install_transaction.InstallTransactionError):
            self.recover(txid, effects)
        self.assertEqual(effects.service_calls(), calls)
        self.assertEqual(self.reg.program.read_text(), "external runtime edit\n")

    def test_fresh_process_recovers_without_the_original_participant_object(self):
        driver = r'''
import json, sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])
import gateway_testing, install_transaction
from installer import gateway_participant, lock
mode, data_path = sys.argv[2], Path(sys.argv[3])
data = json.loads(data_path.read_text())
effects = gateway_testing.FakeEffects(Path(data["home"]), running=data["running"], agent=Path(data["agent"]))
with mock.patch.object(gateway_participant.gateway, "_effects", return_value=effects), \
        mock.patch.object(gateway_participant.sys, "platform", "darwin"), \
        mock.patch("subprocess.Popen", side_effect=AssertionError("native subprocess forbidden")):
    if mode == "publish":
        interrupt = mock.Mock(spec=["before_publish", "after_publish", "before_restore", "after_restore"])
        interrupt.after_publish.side_effect = KeyboardInterrupt()
        try:
            install_transaction.publish(data["request"], [*gateway_participant.participants(data["ghost"]), interrupt])
        except KeyboardInterrupt:
            pass
        [data["txid"]] = install_transaction.unfinished(data["ghost"])
    else:
        lock.restore_unfinished_publishes()
data.update(running=effects.running, calls=effects.service_calls())
data_path.write_text(json.dumps(data))
'''
        data_path = self.root / "fake-service.json"
        data_path.write_text(json.dumps(dict(home=str(self.root), ghost=str(self.reg.ghost),
            agent=str(self.reg.agent), running=True, request=self.runtime_request())))
        for mode in ("publish", "recover"):
            result = subprocess.run([sys.executable, "-B", "-c", driver, str(Path(__file__).parent), mode,
                                     str(data_path)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(data_path.read_text())
        self.assertEqual(data["calls"], ["stop", "start", "ready"])
        self.assertTrue(data["running"])
        self.assertEqual(self.runtime_files(), self.original)
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [])


if __name__ == "__main__":
    unittest.main()
