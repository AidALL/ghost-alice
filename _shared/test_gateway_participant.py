"""The gateway participant: when a publish touches the service's code it stops the service before the
files change and starts it after, confirms readiness, and records the restart; when the publish does not,
it calls no service effect. A failed readiness restores the files and starts the prior service.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
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
        self.assertEqual(install_transaction.unfinished(self.reg.ghost), [])

    def test_an_unavailable_service_state_holds_before_any_file_changes(self):
        effects = self.effects()
        with mock.patch.object(effects, "state", side_effect=RuntimeError("Service state is unavailable")):
            with self.assertRaises(install_transaction.InstallTransactionError) as failed:
                install_transaction.publish(self.runtime_request(), [self.participant(effects)])
        self.assertEqual(failed.exception.exit_code, 3)
        self.assertEqual(self.runtime_files(), self.original)

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


if __name__ == "__main__":
    unittest.main()
