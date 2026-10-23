"""hook_launcher: the short hook registration, the launcher file, the record's hooks section and the launch.

A host registers `"<ghost_root>/bin/hook-launcher" <hook-id> <platform> # <marker> [hook-runner:<hook-id>]`.
The launcher finds Python, runs this module from the runtime `_shared`, and the module reads the install
record's `hooks` section, binds the destination and hands the runner the arguments the long registration
carried. The runner's behaviour is unchanged.
"""
from __future__ import annotations

import base64
import dataclasses
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_digest  # noqa: E402
import hook_definitions  # noqa: E402
import hook_launcher  # noqa: E402
import install_destination  # noqa: E402
import install_hooks  # noqa: E402
import install_lock  # noqa: E402
import installed_payload  # noqa: E402
import registration_testing  # noqa: E402
import runtime_config  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
OLD_POSIX_HOOK_PYTHON_LAUNCHER = (
    'for py in "${GHOST_ALICE_PYTHON:-}" python3 python '
    "/opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 /bin/python3 "
    "/opt/homebrew/bin/python3.[0-9]* /usr/local/bin/python3.[0-9]* "
    "/usr/bin/python3.[0-9]* /bin/python3.[0-9]* __GHOST_ALICE_INSTALL_PYTHON__; do "
    '[ -n "$py" ] || continue; '
    'if command -v "$py" >/dev/null 2>&1 || [ -x "$py" ]; then '
    '"$py" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1 && exec "$py" "$@"; '
    "fi; "
    "done; "
    'echo "Ghost-ALICE hook requires Python 3.11+" >&2; exit 127'
)


def isolated_destination(root: Path, platform: str = "claude") -> install_destination.InstallDestination:
    return install_destination.InstallDestination(
        mode="isolated", owned_root=str(root), platform_dir=str(root / platform), skills_dir=str(root / "skills"),
        ghost_root=str(root / "ghost"), ledger_root=str(root / "ledger"), project_root=str(root / "project"),
        core_source_root=str(REPO_ROOT))


def _entries(**commands: str) -> dict:
    return {hook_id: {"event": "UserPromptSubmit", "marker": f"[{hook_id}]", "command": command, "provider": "core"}
            for hook_id, command in commands.items()}


def _commands(settings: dict) -> list[str]:
    return [hook.get("command", "") for groups in settings.get("hooks", {}).values()
            for group in groups for hook in group.get("hooks", [])]


class RegistrationFormatTests(unittest.TestCase):
    def test_the_python_loop_is_the_one_the_long_registration_embedded(self):
        # Older registrations are recognized by this exact text, so moving its owner changes no byte.
        self.assertEqual(hook_launcher.POSIX_HOOK_PYTHON_LAUNCHER, OLD_POSIX_HOOK_PYTHON_LAUNCHER)
        self.assertIs(install_hooks.POSIX_HOOK_PYTHON_LAUNCHER, hook_launcher.POSIX_HOOK_PYTHON_LAUNCHER)

    def test_registration_command_is_short_and_parses_back(self):
        ghost = Path("/Users/someone/.ghost-alice")
        launcher = hook_launcher.launcher_path(ghost, windows=False)
        self.assertEqual(launcher, ghost / "bin" / "hook-launcher")
        self.assertEqual(hook_launcher.launcher_path(ghost, windows=True), ghost / "bin" / "hook-launcher.cmd")
        command = hook_launcher.registration_command(launcher, "session-intent", "claude", "[session-intent-analyzer]")
        self.assertEqual(command, '"/Users/someone/.ghost-alice/bin/hook-launcher" session-intent claude '
                                  "# [session-intent-analyzer] [hook-runner:session-intent]")
        self.assertLess(len(command), 200)
        parsed = hook_launcher.parse_registration(command)
        self.assertEqual((parsed.launcher, parsed.hook_id, parsed.platform), (launcher, "session-intent", "claude"))
        windows = hook_launcher.registration_command(
            hook_launcher.launcher_path(Path("C:/Users/someone/.ghost-alice"), windows=True), "io-trace", "codex", "[io-trace] audit")
        self.assertTrue(windows.startswith('"C:/Users/someone/.ghost-alice/bin/hook-launcher.cmd" io-trace codex # '))
        self.assertEqual(hook_launcher.parse_registration(windows).hook_id, "io-trace")

    def test_parse_registration_accepts_only_the_launcher_form(self):
        launcher = "/Users/someone/.ghost-alice/bin/hook-launcher"
        for command in (
            "bin/hook-launcher session-intent claude # [x]",
            "/Users/someone/.ghost-alice/bin/not-hook-launcher session-intent claude",
            "/Users/someone/.ghost-alice/hooks/ghost-alice-hook.mjs session-intent claude",
            f'"{launcher}" session-intent cursor',
            f'"{launcher}" session-intent',
            f'"{launcher}" session-intent claude extra',
            f'"{launcher}" session-intent claude; rm -rf /',
            "",
            registration_testing.long_runner_command("session-intent", "echo x", "[x]", platform="claude",
                                                     destination=isolated_destination(Path("/tmp/owned"))),
        ):
            with self.subTest(command=command):
                self.assertIsNone(hook_launcher.parse_registration(command))

    def test_launcher_text_finds_python_then_runs_the_module_with_the_record(self):
        text = hook_launcher.launcher_text(install_python="/opt/py/bin/python3.14",
                                           module=Path("/Users/someone/.ghost-alice/runtime/current/_shared/hook_launcher.py"),
                                           record=Path("/Users/someone/.ghost-alice/installed-payload.json"), windows=False)
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn('"${GHOST_ALICE_PYTHON:-}" python3 python /opt/homebrew/bin/python3', text)
        self.assertIn("'/opt/py/bin/python3.14'", text)
        self.assertIn('exec "$py" \'/Users/someone/.ghost-alice/runtime/current/_shared/hook_launcher.py\' '
                      '\'/Users/someone/.ghost-alice/installed-payload.json\' "$@"', text)
        self.assertTrue(text.rstrip("\n").endswith('echo "Ghost-ALICE hook requires Python 3.11+" >&2\nexit 127'))
        windows = hook_launcher.launcher_text(install_python="C:/Python314/python.exe",
                                              module=Path("C:/Users/someone/.ghost-alice/runtime/current/_shared/hook_launcher.py"),
                                              record=Path("C:/Users/someone/.ghost-alice/installed-payload.json"), windows=True)
        self.assertEqual(windows, '@"C:/Python314/python.exe" "C:/Users/someone/.ghost-alice/runtime/current/_shared/'
                                  'hook_launcher.py" "C:/Users/someone/.ghost-alice/installed-payload.json" %*\r\n')


class RecordSectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = isolated_destination(self.root)
        self.ghost = Path(self.destination.ghost_root)
        self.runtime = self.ghost / "runtime" / "current" / "_shared"
        self.runtime.mkdir(parents=True)
        (self.runtime / "hook_launcher.py").write_text("# runtime copy\n", encoding="utf-8")

    def write(self, platform="claude", entries=None, **overrides):
        return hook_launcher.write_registration(
            self.ghost, platform, hook_file=Path(self.destination.platform_dir) / "settings.json",
            destination=self.destination, entries=entries if entries is not None else _entries(**{"session-intent": "echo intake"}),
            runtime_shared_dir=self.runtime, install_python=sys.executable, **overrides)

    def test_write_registration_creates_the_launcher_and_the_record_section_under_its_own_lock(self):
        changed = self.write()
        self.assertTrue(changed)
        launcher = self.ghost / "bin" / "hook-launcher"
        self.assertTrue(launcher.is_file())
        if os.name != "nt":
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
        text = launcher.read_text(encoding="utf-8")
        self.assertIn(f"'{(self.runtime / 'hook_launcher.py').as_posix()}'", text)
        self.assertIn(f"'{installed_payload.path(self.ghost).as_posix()}'", text)
        record = installed_payload.read(self.ghost)
        section = record["hooks"]["claude"]
        self.assertEqual(section["hook_file"], str(Path(self.destination.platform_dir) / "settings.json"))
        self.assertEqual(section["destination"], dataclasses.asdict(self.destination))
        self.assertEqual(section["entries"], _entries(**{"session-intent": "echo intake"}))
        self.assertIn("registered_at", section)
        launcher_section = record["hooks"]["launcher"]
        self.assertEqual(launcher_section["path"], str(launcher))
        self.assertEqual(launcher_section["python"], sys.executable)
        self.assertEqual(launcher_section["digest"], content_digest.file_digest(launcher))
        self.assertFalse((self.ghost / installed_payload.LOCK_FILENAME).exists(), "the lock stays released")

    def test_write_registration_is_idempotent_and_keeps_the_other_host_and_addon_entries(self):
        self.write()
        self.write("codex", entries=_entries(**{"io-trace": "echo codex"}))
        addon = {"obs": {"event": "PostToolUse", "marker": "[addon:x] obs", "command": "echo obs", "provider": "addon:x"}}
        self.write(entries={**_entries(**{"session-intent": "echo intake"}), **addon})
        before = installed_payload.path(self.ghost).read_bytes()
        launcher_before = (self.ghost / "bin" / "hook-launcher").stat().st_mtime_ns
        self.assertFalse(self.write(entries={**_entries(**{"session-intent": "echo intake"}), **addon}))
        self.assertEqual(installed_payload.path(self.ghost).read_bytes(), before)
        self.assertEqual((self.ghost / "bin" / "hook-launcher").stat().st_mtime_ns, launcher_before)
        # A core-only write keeps the addon entry the host file still carries, and replaces core entries.
        self.assertTrue(self.write(entries=_entries(**{"session-intent": "echo intake-2"})))
        hooks = installed_payload.read(self.ghost)["hooks"]
        self.assertEqual(hooks["claude"]["entries"]["session-intent"]["command"], "echo intake-2")
        self.assertEqual(hooks["claude"]["entries"]["obs"], addon["obs"])
        self.assertEqual(hooks["codex"]["entries"], _entries(**{"io-trace": "echo codex"}))

    def test_a_child_of_the_lock_holder_writes_without_taking_the_lock(self):
        lock = self.ghost / installed_payload.LOCK_FILENAME
        install_lock.acquire_lock(lock, owner="installer:install:claude:test")
        self.addCleanup(install_lock.release_lock, lock)
        self.assertTrue(self.write())
        self.assertTrue(lock.exists())

    def test_write_registration_refuses_while_another_process_holds_the_lock(self):
        lock = self.ghost / installed_payload.LOCK_FILENAME
        lock.write_text(json.dumps({"owner": "installer:install:codex:1", "pid": 1, "acquired_at": "now"}), encoding="utf-8")
        with self.assertRaises(hook_launcher.RegistrationError):
            self.write()
        self.assertFalse((self.ghost / "bin" / "hook-launcher").exists())
        self.assertIsNone(installed_payload.read(self.ghost))

    def test_remove_registration_drops_the_host_and_the_launcher_last(self):
        self.write()
        self.write("codex", entries=_entries(**{"io-trace": "echo codex"}))
        self.assertEqual(hook_launcher.remove_registration(self.ghost, "claude"), {"section": True, "launcher": False})
        hooks = installed_payload.read(self.ghost)["hooks"]
        self.assertNotIn("claude", hooks)
        self.assertIn("launcher", hooks)
        self.assertTrue((self.ghost / "bin" / "hook-launcher").is_file())
        self.assertEqual(hook_launcher.remove_registration(self.ghost, "codex"), {"section": True, "launcher": True})
        self.assertNotIn("hooks", installed_payload.read(self.ghost))
        self.assertFalse((self.ghost / "bin" / "hook-launcher").exists())
        self.assertEqual(hook_launcher.remove_registration(self.ghost, "codex"), {"section": False, "launcher": False})

    def test_remove_entries_drops_only_the_named_hooks(self):
        addon = {"obs": {"event": "PostToolUse", "marker": "[addon:x] obs", "command": "echo obs", "provider": "addon:x"}}
        self.write(entries={**_entries(**{"session-intent": "echo intake"}), **addon})
        self.assertEqual(hook_launcher.remove_entries(self.ghost, "claude", ["obs", "unknown"]), 1)
        self.assertEqual(set(installed_payload.read(self.ghost)["hooks"]["claude"]["entries"]), {"session-intent"})

    def test_registration_status_compares_the_record_and_the_launcher_with_what_the_install_expects(self):
        expected = dict(hook_file=Path(self.destination.platform_dir) / "settings.json", destination=self.destination,
                        entries=_entries(**{"session-intent": "echo intake"}))
        self.assertEqual(hook_launcher.registration_status(self.ghost, "claude", **expected), (False, False))
        self.write()
        self.assertEqual(hook_launcher.registration_status(self.ghost, "claude", **expected), (True, True))
        # Extra addon entries in the record do not count as drift.
        hook_launcher.write_registration(
            self.ghost, "claude", hook_file=expected["hook_file"], destination=self.destination,
            entries={**expected["entries"], "obs": {"event": "PostToolUse", "marker": "[addon:x] obs",
                                                    "command": "echo obs", "provider": "addon:x"}},
            runtime_shared_dir=self.runtime, install_python=sys.executable)
        self.assertEqual(hook_launcher.registration_status(self.ghost, "claude", **expected), (True, True))
        stale = dict(expected, entries=_entries(**{"session-intent": "echo newer-intake"}))
        self.assertEqual(hook_launcher.registration_status(self.ghost, "claude", **stale), (True, False))
        (self.ghost / "bin" / "hook-launcher").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.assertEqual(hook_launcher.registration_status(self.ghost, "claude", **expected), (True, False))
        (self.ghost / "bin" / "hook-launcher").unlink()
        self.assertEqual(hook_launcher.registration_status(self.ghost, "claude", **expected), (True, False))


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = isolated_destination(self.root)
        self.ghost = Path(self.destination.ghost_root)
        self.record = installed_payload.path(self.ghost)
        self.runtime = self.ghost / "runtime" / "current" / "_shared"
        self.runtime.mkdir(parents=True)
        self.stderr = io.StringIO()
        patcher = mock.patch.object(sys, "stderr", self.stderr)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(mock.patch.stopall)

    def register(self, entries=None):
        hook_launcher.write_registration(
            self.ghost, "claude", hook_file=Path(self.destination.platform_dir) / "settings.json",
            destination=self.destination, entries=entries if entries is not None else _entries(**{"session-intent": "echo intake"}),
            runtime_shared_dir=self.runtime, install_python=sys.executable)

    def launch(self, *args):
        with self.assertRaises(SystemExit) as stop:
            hook_launcher.main(list(args))
        return stop.exception.code

    def test_usage_errors_exit_one_without_blocking_the_host(self):
        for args in ((), (str(self.record),), (str(self.record), "session-intent"),
                     (str(self.record), "session-intent", "cursor"), (str(self.record), "", "claude")):
            with self.subTest(args=args):
                self.assertEqual(self.launch(*args), 1)
                self.assertIn("usage:", self.stderr.getvalue())

    def test_a_missing_record_is_rejected_with_126(self):
        self.assertEqual(self.launch(str(self.record), "session-intent", "claude"), 126)
        self.assertIn("hook registration rejected: no install record", self.stderr.getvalue())

    def test_a_missing_entry_is_rejected_and_audited_in_the_strict_log(self):
        self.register()
        env = {key: value for key, value in os.environ.items() if not key.startswith("GHOST_ALICE_")}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("hook_profile_gate._read_stdin", return_value=json.dumps({"session_id": "s-reject"})):
            self.assertEqual(self.launch(str(self.record), "completion", "claude"), 126)
        self.assertIn("hook registration rejected: no claude registration for hook completion", self.stderr.getvalue())
        log = self.ghost / "session-logs" / "claude" / "s-reject" / "strict-hook-output.jsonl"
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(row["exit_code"], 126)
        self.assertEqual(row["surface_item"]["value_key"], "hook-command-rejection")
        self.assertIn("no claude registration for hook completion", row["stderr"])

    def test_a_record_without_the_platform_or_with_a_bad_destination_is_rejected(self):
        self.register()
        self.assertEqual(self.launch(str(self.record), "session-intent", "codex"), 126)
        self.assertIn("no codex hook registration", self.stderr.getvalue())
        record = json.loads(self.record.read_text(encoding="utf-8"))
        record["hooks"]["claude"]["destination"]["ledger_root"] = "relative/ledger"
        self.record.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(self.launch(str(self.record), "session-intent", "claude"), 126)
        self.assertIn("hook registration rejected: destination ledger_root must be an absolute path",
                      self.stderr.getvalue())

    def test_the_launch_binds_the_destination_then_hands_the_runner_the_long_arguments(self):
        self.register()
        seen = {}

        def runner_main(argv):
            seen["argv"] = list(argv)
            seen["env"] = {key: os.environ.get(key) for key in ("GHOST_ALICE_GHOST_ROOT", "GHOST_ALICE_SESSION_INTENT_ROOT",
                                                                  "GHOST_ALICE_PLATFORM_DIR", "GHOST_ALICE_OWNED_ROOT")}
            raise SystemExit(0)

        env = {key: value for key, value in os.environ.items() if not key.startswith("GHOST_ALICE_")}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("hook_profile_gate.main", runner_main):
            self.assertEqual(self.launch(str(self.record), "session-intent", "claude", "#", "[marker]", "[hook-runner:x]"), 0)
        payload = base64.urlsafe_b64encode(b"echo intake").decode("ascii")
        binding = install_destination.encode_binding(self.destination.binding())
        self.assertEqual(seen["argv"], ["run", "session-intent", "claude", payload, "--binding-b64", binding])
        self.assertEqual(seen["env"], {"GHOST_ALICE_GHOST_ROOT": str(self.ghost),
                                       "GHOST_ALICE_SESSION_INTENT_ROOT": self.destination.ledger_root,
                                       "GHOST_ALICE_PLATFORM_DIR": self.destination.platform_dir,
                                       "GHOST_ALICE_OWNED_ROOT": self.destination.owned_root})

    @unittest.skipIf(os.name == "nt", "POSIX shell launcher")
    def test_the_registered_command_runs_end_to_end_through_the_launcher(self):
        install_hooks.install_hook("claude", destination=self.destination)
        settings = json.loads((Path(self.destination.platform_dir) / "settings.json").read_text(encoding="utf-8"))
        command = next(command for command in _commands(settings) if "[hook-runner:session-start]" in command)
        self.assertIsNotNone(hook_launcher.parse_registration(command))
        env = {key: value for key, value in os.environ.items()
               if key not in {"GHOST_ALICE_PLATFORM", "GHOST_ALICE_SESSION_ID", "CODEX_THREAD_ID"}}
        env["HOME"] = str(self.root)
        result = subprocess.run(["/bin/sh", "-c", command], cwd=self.root, env=env, timeout=60,
                                input=json.dumps({"hook_event_name": "SessionStart", "session_id": "s-launch"}),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["continue"])
        log = self.ghost / "session-logs" / "claude" / "s-launch" / "strict-hook-output.jsonl"
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(row["hook_id"], "session-start")
        self.assertEqual(row["observed_duration_source"], "hook-runner")


class InstallerRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = isolated_destination(self.root)
        self.ghost = Path(self.destination.ghost_root)
        self.settings = Path(self.destination.platform_dir) / "settings.json"
        self.addCleanup(mock.patch.stopall)

    def install(self, platform="claude", **kwargs):
        destination = self.destination if platform == "claude" else isolated_destination(self.root, "codex")
        if platform == "codex":
            mock.patch.object(install_hooks, "_codex_hooks_supported", return_value=True).start()
        return install_hooks.install_hook(platform, destination=destination, **kwargs)

    def settings_of(self, platform="claude"):
        file = Path(self.destination.platform_dir) / "settings.json" if platform == "claude" \
            else self.root / "codex" / "hooks.json"
        return json.loads(file.read_text(encoding="utf-8"))

    def test_install_registers_short_commands_and_records_every_core_hook(self):
        self.assertEqual(self.install(), "installed")
        commands = _commands(self.settings_of())
        parsed = [hook_launcher.parse_registration(command) for command in commands]
        self.assertTrue(all(parsed), commands)
        self.assertEqual({item.hook_id for item in parsed}, set(hook_definitions.CORE_HOOK_IDS))
        self.assertEqual({item.platform for item in parsed}, {"claude"})
        self.assertEqual({item.launcher for item in parsed}, {self.ghost / "bin" / "hook-launcher"})
        for command in commands:
            self.assertNotIn("hook_profile_gate.py", command)
            self.assertNotIn("--binding-b64", command)
            self.assertLess(len(command), 200 + len(str(self.ghost)))
            self.assertTrue(install_hooks._hook_runner_id_match(command, hook_launcher.parse_registration(command).hook_id))
        section = installed_payload.read(self.ghost)["hooks"]["claude"]
        self.assertEqual(set(section["entries"]), set(hook_definitions.CORE_HOOK_IDS))
        self.assertEqual(section["destination"], dataclasses.asdict(self.destination))
        self.assertEqual(section["hook_file"], str(self.settings))
        for hook_id, entry in section["entries"].items():
            self.assertEqual(entry["provider"], "core")
            self.assertEqual(entry["event"], next(group_event for group_event, groups in self.settings_of()["hooks"].items()
                                                  for group in groups for hook in group["hooks"]
                                                  if f"[hook-runner:{hook_id}]" in hook["command"]))
        self.assertIn(str(self.destination.ledger_root), section["entries"]["session-intent"]["command"])
        self.assertEqual(self.install(), "already")

    def test_install_replaces_a_long_core_registration_and_uninstall_removes_it(self):
        self.install()
        inner = install_hooks._session_intent_analyzer_command(platform="claude", output_format="json", payload_mode=True)
        long_form = registration_testing.long_runner_command(
            "session-intent", inner + " # " + install_hooks.SESSION_INTENT_MARKER, install_hooks.SESSION_INTENT_MARKER,
            platform="claude", destination=self.destination)
        self.assertTrue(install_hooks._is_retired_hook_command(long_form))
        self.assertTrue(install_hooks._hook_runner_id_match(long_form, "session-intent"))
        settings = self.settings_of()
        settings["hooks"]["UserPromptSubmit"] = [{"matcher": "", "hooks": [{"type": "command", "command": long_form}]}]
        self.settings.write_text(json.dumps(settings), encoding="utf-8")
        self.assertEqual(self.install(), "installed")
        intake = [command for command in _commands(self.settings_of()) if "[hook-runner:session-intent]" in command]
        self.assertEqual(len(intake), 1)
        self.assertIsNotNone(hook_launcher.parse_registration(intake[0]))
        settings = self.settings_of()
        settings["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": long_form}]})
        self.settings.write_text(json.dumps(settings), encoding="utf-8")
        self.assertEqual(install_hooks.uninstall_hook("claude", destination=self.destination), "removed")
        self.assertEqual(_commands(self.settings_of()), [])

    def test_a_long_addon_registration_is_not_retired_by_a_core_install(self):
        self.install()
        long_form = registration_testing.long_runner_command(
            "obs", "echo observer", "[addon:demo] obs", platform="claude", destination=self.destination)
        self.assertFalse(install_hooks._is_retired_hook_command(long_form))
        settings = self.settings_of()
        settings["hooks"]["PostToolUse"].append({"hooks": [{"type": "command", "command": long_form}]})
        self.settings.write_text(json.dumps(settings), encoding="utf-8")
        self.assertEqual(self.install(), "already")
        self.assertIn(long_form, _commands(self.settings_of()))

    def test_a_short_registration_with_a_retired_id_is_removed(self):
        self.install()
        retired = hook_launcher.registration_command(self.ghost / "bin" / "hook-launcher", "prompt", "claude", "[old]")
        self.assertTrue(install_hooks._is_retired_hook_command(retired))
        settings = self.settings_of()
        settings["hooks"]["UserPromptSubmit"].append({"hooks": [{"type": "command", "command": retired}]})
        self.settings.write_text(json.dumps(settings), encoding="utf-8")
        self.assertEqual(self.install(), "installed")
        self.assertNotIn(retired, _commands(self.settings_of()))

    def test_status_reports_drift_when_the_record_or_the_launcher_differs(self):
        self.install()
        status = install_hooks.check_status_detail("claude", destination=self.destination)
        self.assertEqual(status.status_token, "HOOK_INSTALLED_OK")
        self.assertIn("registration", status.details["installed"])
        record = json.loads(installed_payload.path(self.ghost).read_text(encoding="utf-8"))
        record["hooks"]["claude"]["entries"]["completion"]["command"] = "echo stale"
        installed_payload.path(self.ghost).write_text(json.dumps(record), encoding="utf-8")
        status = install_hooks.check_status_detail("claude", destination=self.destination)
        self.assertEqual(status.status_token, "HOOK_INSTALLED_DRIFT")
        self.assertEqual(status.details["drifted"], ["registration"])
        self.assertEqual(self.install(), "already")
        self.assertEqual(install_hooks.check_status_detail("claude", destination=self.destination).status_token,
                         "HOOK_INSTALLED_OK")
        (self.ghost / "bin" / "hook-launcher").unlink()
        self.assertEqual(install_hooks.check_status_detail("claude", destination=self.destination).status_token,
                         "HOOK_INSTALLED_DRIFT")
        self.assertEqual(self.install(), "already")
        self.assertTrue((self.ghost / "bin" / "hook-launcher").is_file())
        installed_payload.path(self.ghost).unlink()
        status = install_hooks.check_status_detail("claude", destination=self.destination)
        self.assertEqual(status.status_token, "HOOK_MISSING")
        self.assertEqual(status.details["missing"], ["registration"])

    def test_install_fails_closed_when_the_record_cannot_be_written(self):
        self.ghost.mkdir(parents=True, exist_ok=True)
        (self.ghost / installed_payload.LOCK_FILENAME).write_text(
            json.dumps({"owner": "installer:install:codex:1", "pid": 1, "acquired_at": "now"}), encoding="utf-8")
        with self.assertRaises(hook_launcher.RegistrationError):
            self.install()
        self.assertFalse(self.settings.exists())
        self.assertFalse((self.ghost / "bin" / "hook-launcher").exists())

    def test_uninstall_of_one_host_keeps_the_launcher_for_the_other(self):
        self.install()
        self.install("codex")
        launcher = self.ghost / "bin" / "hook-launcher"
        self.assertEqual(set(installed_payload.read(self.ghost)["hooks"]), {"launcher", "claude", "codex"})
        self.assertEqual(install_hooks.uninstall_hook("claude", destination=self.destination), "removed")
        self.assertEqual(set(installed_payload.read(self.ghost)["hooks"]), {"launcher", "codex"})
        self.assertTrue(launcher.is_file())
        self.assertEqual(install_hooks.uninstall_hook("codex", destination=isolated_destination(self.root, "codex")), "removed")
        self.assertNotIn("hooks", installed_payload.read(self.ghost))
        self.assertFalse(launcher.exists())

    def test_dry_run_writes_neither_the_launcher_nor_the_record(self):
        Path(self.destination.platform_dir).mkdir(parents=True)
        self.assertEqual(self.install(dry_run=True), "installed")
        self.assertFalse((self.ghost / "bin").exists())
        self.assertIsNone(installed_payload.read(self.ghost))
        self.assertFalse(self.settings.exists())


class LedgerRetentionTests(unittest.TestCase):
    """The ledger root an installation already uses comes from the record first, then an older installer's config."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / ".codex").mkdir()
        self.ghost = self.home / ".ghost-alice"
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(install_hooks, "_home", return_value=self.home).start()
        mock.patch.dict(os.environ, {"GHOST_ALICE_SESSION_INTENT_ROOT": "", "GHOST_ALICE_GHOST_ROOT": "",
                                     install_hooks.HOOK_SHARED_DIR_ENV: "", "GHOST_ALICE_RUNTIME_SHARED_DIR": ""}).start()

    def recorded(self, ledger: Path):
        destination = install_destination.InstallDestination(
            mode="normal", owned_root=str(self.home), platform_dir=str(self.home / ".codex"),
            skills_dir=str(self.home / ".agents/skills"), ghost_root=str(self.ghost), ledger_root=str(ledger),
            project_root=str(REPO_ROOT), core_source_root=str(REPO_ROOT))
        runtime = self.ghost / "runtime/current/_shared"
        runtime.mkdir(parents=True, exist_ok=True)
        hook_launcher.write_registration(self.ghost, "codex", hook_file=self.home / ".codex/hooks.json",
                                         destination=destination, entries=_entries(**{"session-intent": "echo x"}),
                                         runtime_shared_dir=runtime, install_python=sys.executable)

    def test_the_record_is_read_first_then_the_earlier_config_then_the_default(self):
        self.recorded(self.home / "recorded-ledger")
        config = runtime_config.config_path(self.home)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"schema_version": "ghost-alice-config.v1",
                                      "install_locations": {"codex": {"ledger_root": str(self.home / "older-ledger")}}}))
        self.assertEqual(install_hooks._default_destination("codex").ledger_root, str(self.home / "recorded-ledger"))
        installed_payload.path(self.ghost).unlink()
        self.assertEqual(install_hooks._default_destination("codex").ledger_root, str(self.home / "older-ledger"))
        config.unlink()
        self.assertEqual(install_hooks._default_destination("codex").ledger_root, str(REPO_ROOT / ".tmp/session-intent"))

    def test_runtime_config_no_longer_carries_install_locations(self):
        config = runtime_config.config_path(self.home)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"schema_version": "ghost-alice-config.v1",
                                      "install_locations": {"codex": {"ledger_root": "/elsewhere"}}}))
        self.assertNotIn("install_locations", runtime_config.load_config(home=self.home))
        runtime_config.save_config({"agent_visibility": {"profile": "strict"}}, home=self.home)
        self.assertNotIn("install_locations", json.loads(config.read_text(encoding="utf-8")))

    def test_short_registrations_are_not_parsed_for_a_ledger_root(self):
        hooks = self.home / ".codex" / "hooks.json"
        command = hook_launcher.registration_command(self.ghost / "bin/hook-launcher", "session-intent", "codex", "[x]")
        hooks.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": command}]}]}}))
        self.assertIsNone(install_hooks._retained_installed_ledger("codex"))


class RecordHooksSectionSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ghost = Path(self.temp.name) / ".ghost-alice"
        self.ghost.mkdir()
        install_lock.acquire_lock(self.ghost / installed_payload.LOCK_FILENAME, owner="installer:install:claude:test")
        self.addCleanup(install_lock.release_lock, self.ghost / installed_payload.LOCK_FILENAME)
        self.host = {"hook_file": "/h/settings.json", "destination": {"mode": "normal"}, "entries": {
            "session-intent": {"event": "UserPromptSubmit", "marker": "[x]", "command": "echo", "provider": "core"}},
            "registered_at": "2026-10-23T00:00:00+00:00"}
        self.launcher = {"path": "/g/bin/hook-launcher", "python": "/usr/bin/python3",
                         "digest": "sha256:" + "0" * 64, "written_at": "2026-10-23T00:00:00+00:00"}

    def test_hooks_merge_per_key_and_none_deletes_a_key_and_an_empty_section(self):
        self.assertIn("hooks", installed_payload.SECTIONS)
        installed_payload.update(self.ghost, {"hooks": {"launcher": self.launcher, "claude": self.host}})
        installed_payload.update(self.ghost, {"hooks": {"codex": dict(self.host, hook_file="/h/hooks.json")}})
        hooks = installed_payload.read(self.ghost)["hooks"]
        self.assertEqual(set(hooks), {"launcher", "claude", "codex"})
        installed_payload.update(self.ghost, {"hooks": {"claude": None}})
        self.assertEqual(set(installed_payload.read(self.ghost)["hooks"]), {"launcher", "codex"})
        installed_payload.update(self.ghost, {"hooks": {"codex": None, "launcher": None}})
        self.assertNotIn("hooks", installed_payload.read(self.ghost))

    def test_hook_section_shapes_are_checked(self):
        for patch in (
            {"cursor": self.host},
            {"claude": dict(self.host, hook_file="relative/settings.json")},
            {"claude": dict(self.host, destination="not-an-object")},
            {"claude": dict(self.host, entries={"x": {"event": "Stop"}})},
            {"claude": dict(self.host, entries={"x": dict(self.host["entries"]["session-intent"], command=7)})},
            {"launcher": dict(self.launcher, path="bin/hook-launcher")},
            {"launcher": {"path": "/g/bin/hook-launcher"}},
            "not-an-object",
        ):
            with self.subTest(patch=patch):
                with self.assertRaises(installed_payload.RecordError):
                    installed_payload.update(self.ghost, {"hooks": patch})
        self.assertIsNone(installed_payload.read(self.ghost))


if __name__ == "__main__":
    unittest.main()
