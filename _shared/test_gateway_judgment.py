"""The gateway judgment: does a publish or a removal touch the service's code?

The judgment compares the install locations a publisher names with the record's `service.runtime_root`
and the three service files. It never resolves links, and the data roots the service reads (`~/.codex`,
`~/.claude`, the ledger) are not inputs. A removal without a `service` section asks launchd whether a
service with the gateway's label is loaded and holds only when that service's program is inside a
removal root.
"""
from __future__ import annotations

import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gateway_testing  # noqa: E402
import installed_payload  # noqa: E402
from context_gateway_model import GatewayBlocked, LABEL  # noqa: E402
from hook_definitions import PROVIDER  # noqa: E402
from installer import gateway_participant  # noqa: E402

HOME = Path("/Users/someone")
RUNTIME_ROOT = HOME / ".ghost-alice" / "runtime" / "current"
SERVICE = {
    "label": LABEL, "runtime_root": str(RUNTIME_ROOT), "program": str(RUNTIME_ROOT / "_shared" / "context_gateway.py"),
    "launch_agent": str(HOME / "Library" / "LaunchAgents" / f"{LABEL}.plist"), "launch_agent_digest": "sha256:" + "0" * 64,
    "state_path": str(HOME / ".ghost-alice" / "context-gateway" / "state.json"), "state_digest": "sha256:" + "0" * 64,
    "config_path": str(HOME / ".codex" / "config.toml"), "config_block_digest": None, "config_backup": None,
    "registered_at": "2026-10-20T00:00:00+00:00", "restarted_at": None, "listener_ready": True,
}


class JudgmentTests(unittest.TestCase):
    """`affects(service, locations)` answers from paths alone."""

    def test_the_codex_copy_batch_does_not_touch_the_service(self):
        # Contradiction 16(b): the Codex copy of _shared is an install target of Codex skills, not service code.
        locations = [HOME / ".agents" / "skills" / name for name in ("_shared", "task-router", "alpha")]
        self.assertFalse(gateway_participant.affects(SERVICE, locations))

    def test_recreating_the_claude_links_does_not_touch_the_service(self):
        # Defect A: ~/.claude is a data root the service reads, not its code.
        locations = [HOME / ".claude" / "skills" / name for name in ("_shared", "task-router")]
        self.assertFalse(gateway_participant.affects(SERVICE, locations))

    def test_the_runtime_shared_publish_touches_the_service(self):
        self.assertTrue(gateway_participant.affects(SERVICE, [RUNTIME_ROOT / "_shared"]))

    def test_the_companion_script_publish_touches_the_service(self):
        companion = RUNTIME_ROOT / "session-intent-analyzer" / "scripts" / "session_intent_ledger.py"
        self.assertTrue(gateway_participant.affects(SERVICE, [companion]))

    def test_a_link_that_resolves_under_the_codex_dir_is_judged_by_its_own_path(self):
        # Contradiction 15: removing ~/.agents/skills/task-router deletes a link; where it points is not removed.
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            checkout = home / ".codex" / "worktrees" / "d0bf" / "ghost-alice" / "task-router"
            checkout.mkdir(parents=True)
            link = home / ".agents" / "skills" / "task-router"
            link.parent.mkdir(parents=True)
            link.symlink_to(checkout, target_is_directory=True)
            service = dict(SERVICE, runtime_root=str(home / ".ghost-alice" / "runtime" / "current"),
                           config_path=str(home / ".codex" / "config.toml"))
            self.assertFalse(gateway_participant.affects(service, [link]))

    def test_a_folder_above_the_runtime_root_touches_the_service(self):
        self.assertTrue(gateway_participant.affects(SERVICE, [HOME / ".ghost-alice" / "runtime"]))
        self.assertTrue(gateway_participant.affects(SERVICE, [RUNTIME_ROOT]))

    def test_a_service_file_among_the_locations_touches_the_service(self):
        for key in ("launch_agent", "state_path", "config_path"):
            with self.subTest(key=key):
                self.assertTrue(gateway_participant.affects(SERVICE, [Path(SERVICE[key])]))
                # A removal root that holds a service file removes it too.
                self.assertTrue(gateway_participant.affects(SERVICE, [Path(SERVICE[key]).parent]))
        self.assertFalse(gateway_participant.affects(SERVICE, [HOME / ".ghost-alice" / "context-gateway-other"]))

    def test_the_codex_full_removal_touches_the_service(self):
        self.assertTrue(gateway_participant.affects(SERVICE, [HOME / ".agents" / "skills" / "alpha"], remove_codex=True))
        self.assertFalse(gateway_participant.affects(SERVICE, [HOME / ".agents" / "skills" / "alpha"], remove_codex=False))

    def test_no_locations_touch_nothing(self):
        self.assertFalse(gateway_participant.affects(SERVICE, []))


class RemovalDecisionTests(unittest.TestCase):
    """`removal(ghost_root, removing, ...)` judges from the record, then detaches or leaves the service."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def removal(self, reg, removing, *, remove_codex=False, confirm=True, effects=None, platform="darwin"):
        return gateway_participant.removal(reg.ghost, [Path(p) for p in removing], remove_codex=remove_codex,
                                           confirm=confirm, effects=effects, platform=platform)

    def test_removing_codex_skills_leaves_a_registered_service_alone(self):
        reg = gateway_testing.registered_home(self, self.root)
        effects = gateway_testing.FakeEffects(self.root, running=True, agent=reg.agent)
        result = self.removal(reg, [self.root / ".agents" / "skills" / "task-router"], effects=effects)
        self.assertEqual(result["action"], "unchanged")
        self.assertEqual(result["reason"], "independent-gateway-runtime")
        self.assertEqual(effects.calls, [])
        self.assertIsNotNone(reg.service())

    def test_the_codex_full_removal_detaches_the_service(self):
        reg = gateway_testing.registered_home(self, self.root)
        effects = gateway_testing.FakeEffects(self.root, running=True, agent=reg.agent)
        result = self.removal(reg, [self.root / ".agents" / "skills" / "alpha"], remove_codex=True, effects=effects)
        self.assertEqual(result["action"], "detached")
        self.assertEqual(effects.service_calls(), ["stop"])
        self.assertFalse(reg.agent.exists())
        self.assertNotIn(PROVIDER, reg.config.read_text(encoding="utf-8"))
        self.assertIn('model="native"', reg.config.read_text(encoding="utf-8"))
        self.assertIsNone(reg.service())
        self.assertTrue((reg.state_dir / "usage.jsonl").is_file())

    def test_removing_the_runtime_detaches_the_service_first(self):
        reg = gateway_testing.registered_home(self, self.root)
        effects = gateway_testing.FakeEffects(self.root, running=True, agent=reg.agent)
        result = self.removal(reg, [reg.ghost / "runtime"], effects=effects)
        self.assertEqual(result["action"], "detached")
        self.assertFalse(effects.running)

    def test_a_preview_judges_but_changes_nothing(self):
        reg = gateway_testing.registered_home(self, self.root)
        effects = gateway_testing.FakeEffects(self.root, running=True, agent=reg.agent)
        result = self.removal(reg, [reg.ghost / "runtime"], confirm=False, effects=effects)
        self.assertEqual((result["status"], result["action"]), ("dry-run", "would-detach"))
        self.assertEqual(effects.service_calls(), [])
        self.assertTrue(reg.agent.exists())
        self.assertIsNotNone(reg.service())

    def test_without_a_service_section_an_absent_launchd_service_means_remove_only(self):
        reg = gateway_testing.registered_home(self, self.root, service=False)
        effects = gateway_testing.FakeEffects(self.root, running=False)
        result = self.removal(reg, [reg.ghost / "runtime"], effects=effects)
        self.assertEqual((result["action"], result["reason"]), ("missing", "gateway-not-registered"))

    def test_without_a_service_section_a_loaded_service_inside_the_removal_roots_holds(self):
        reg = gateway_testing.registered_home(self, self.root, service=False)
        agent = self.root / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": [
            sys.executable, "-B", str(reg.program), "--config", str(reg.state_path)]}))
        effects = gateway_testing.FakeEffects(self.root, running=True, agent=agent)
        with self.assertRaises(GatewayBlocked) as held:
            self.removal(reg, [reg.ghost / "runtime"], effects=effects)
        self.assertEqual((held.exception.exit_code, held.exception.code), (3, "GATEWAY_SERVICE_UNCONFIRMED"))
        self.assertEqual(effects.service_calls(), [])
        # The same loaded service is independent of a removal that leaves its program alone.
        result = self.removal(reg, [self.root / ".agents" / "skills" / "alpha"], effects=effects)
        self.assertEqual((result["action"], result["reason"]), ("unchanged", "independent-unregistered-gateway-runtime"))

    def test_without_a_service_section_an_inconclusive_launchd_answer_holds(self):
        reg = gateway_testing.registered_home(self, self.root, service=False)
        effects = gateway_testing.FakeEffects(self.root, running=True, agent=None)
        with self.assertRaises(GatewayBlocked) as held:
            self.removal(reg, [reg.ghost / "runtime"], effects=effects)
        self.assertEqual(held.exception.exit_code, 3)

    def test_without_a_service_section_launchd_is_asked_only_on_macos(self):
        reg = gateway_testing.registered_home(self, self.root, service=False)
        effects = gateway_testing.FakeEffects(self.root, running=True, agent=reg.agent)
        with mock.patch.object(effects, "observe", side_effect=AssertionError("launchd asked")) as observe:
            result = self.removal(reg, [reg.ghost / "runtime"], effects=effects, platform="win32")
        observe.assert_not_called()
        self.assertEqual(result["action"], "missing")

    def test_an_unreadable_record_blocks_the_judgment(self):
        reg = gateway_testing.registered_home(self, self.root)
        installed_payload.path(reg.ghost).write_text("{broken", encoding="utf-8")
        with self.assertRaises(GatewayBlocked) as held:
            self.removal(reg, [reg.ghost / "runtime"], effects=gateway_testing.FakeEffects(self.root))
        self.assertEqual(held.exception.exit_code, 2)

    def test_the_guard_turns_the_decision_into_an_exit_code(self):
        reg = gateway_testing.registered_home(self, self.root)
        effects = gateway_testing.FakeEffects(self.root, running=True, agent=reg.agent,
                                              stop_error=RuntimeError("stop refused"))
        self.assertEqual(gateway_participant.guard(reg.ghost, [self.root / ".agents" / "skills" / "alpha"],
                                                   effects=effects, platform="darwin"), 0)
        self.assertEqual(gateway_participant.guard(reg.ghost, [reg.ghost / "runtime"], effects=effects,
                                                   platform="darwin"), 3)
        self.assertTrue(reg.agent.exists())
        self.assertIsNotNone(reg.service())


class ParticipantSelectionTests(unittest.TestCase):
    def test_a_record_with_a_service_section_gets_the_gateway_participant(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reg = gateway_testing.registered_home(self, root)
            chosen = gateway_participant.participants(reg.ghost)
            self.assertEqual([type(p).__name__ for p in chosen], ["GatewayParticipant"])
            installed_payload.update(reg.ghost, {"service": None})
            self.assertEqual(gateway_participant.participants(reg.ghost), [])
            installed_payload.path(reg.ghost).unlink()
            self.assertEqual(gateway_participant.participants(reg.ghost), [])

    def test_an_unreadable_record_holds_a_runtime_publish_and_lets_the_others_go_on(self):
        # Whether a gateway runs from the runtime is unknown without the record: the runtime publish waits
        # for the record to be reviewed, a skill or Codex copy publish does not depend on it.
        from installer.participant import PublishContext
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reg = gateway_testing.registered_home(self, root)
            record = installed_payload.path(reg.ghost)
            for broken in (json.dumps({"schema_version": 2, "targets": {}}), "{broken"):
                with self.subTest(record=broken[:12]):
                    record.write_text(broken, encoding="utf-8")
                    [chosen] = gateway_participant.participants(reg.ghost, runtime_root=reg.runtime_root)
                    runtime = PublishContext(ghost_root=reg.ghost, host="claude", txid="t",
                                             destinations=(reg.shared, reg.companion))
                    with self.assertRaises(GatewayBlocked) as held:
                        chosen.before_publish(runtime)
                    self.assertEqual(held.exception.exit_code, 2)
                    self.assertIn(str(record), str(held.exception))
                    skills = PublishContext(ghost_root=reg.ghost, host="codex", txid="t",
                                            destinations=(root / ".agents" / "skills" / "_shared",))
                    for point in ("before_publish", "after_publish", "before_restore", "after_restore"):
                        self.assertIsNone(getattr(chosen, point)(skills))
                    self.assertIsNone(chosen.after_restore(runtime))


if __name__ == "__main__":
    unittest.main()
