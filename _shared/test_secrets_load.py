"""Credential file behavior using synthetic values in temporary directories."""

import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_write

SOURCE = Path(__file__).resolve().parent / "secrets"
spec = importlib.util.spec_from_file_location("ghost_alice_test_secret_loader", SOURCE / "load.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


class SecretFileTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "Bash is required for the shell credential writer")
    def test_shell_writer_propagates_failed_steps_without_replacing_existing_values(self):
        for operation in ('set', 'unset'):
            for command in ('mktemp', 'grep', 'mv', 'chmod'):
                with self.subTest(operation=operation, command=command), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'secrets.env'
                    original = b'DEMO_TOKEN=original-value\nDEMO_OTHER=keep-value\n'
                    path.write_bytes(original)
                    path.chmod(0o600)
                    marker = Path(directory) / 'fault-reached'
                    script = ('source "$1"; ' + command + '() { echo "injected failure" > "$TEST_FAULT_PATH"; return 71; }; '
                              + 'if secrets_' + operation + ' DEMO_TOKEN changed-value; then exit 0; else exit "$?"; fi')
                    result = subprocess.run([shutil.which('bash'), '-c', script, 'test', str(SOURCE / 'load.sh')],
                        env=dict(os.environ, GHOST_ALICE_SECRETS_FILE=str(path), TMPDIR=directory,
                                 TEST_FAULT_PATH=str(marker)),
                        text=True, capture_output=True, check=False)
                    self.assertEqual(marker.read_text(), 'injected failure\n')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(set(Path(directory).iterdir()), {path, marker})

    @unittest.skipUnless(os.name != 'nt' and shutil.which('bash'), 'A POSIX terminal and Bash are required')
    def test_prompt_reports_failed_storage_without_claiming_saved(self):
        import pty
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'secrets.env'
            original = b'DEMO_OTHER=keep-value\n'
            path.write_bytes(original)
            env = dict(os.environ, GHOST_ALICE_SECRETS_FILE=str(path), TMPDIR=directory)
            env.pop('DEMO_TOKEN', None)
            master, slave = pty.openpty()
            try:
                process = subprocess.Popen([shutil.which('bash'), '-c',
                    'source "$1"; mv() { echo "injected replacement failure" >&2; return 71; }; '
                    'secrets_get_or_prompt DEMO_TOKEN', 'test', str(SOURCE / 'load.sh')],
                    stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
                os.write(master, b'fake-entered-value\ny\n')
                stdout, stderr = process.communicate(timeout=5)
            finally:
                os.close(master)
                os.close(slave)
            self.assertIn('injected replacement failure', stderr)
            self.assertEqual(process.returncode, 0)
            self.assertIn('DEMO_TOKEN was not saved', stderr)
            self.assertNotIn('[OK]', stderr)
            self.assertEqual(stdout, 'fake-entered-value')
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    @unittest.skipUnless(shutil.which('bash'), 'Bash is required for the shell credential writer')
    def test_shell_set_and_unset_preserve_other_keys_and_allow_empty_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'secrets.env'
            path.write_text('DEMO_TOKEN=old\nDEMO_OTHER=keep\n', encoding='utf-8')
            for operation, expected in [
                ('secrets_set DEMO_TOKEN new', 'DEMO_OTHER=keep\nDEMO_TOKEN=new\n'),
                ('secrets_unset DEMO_TOKEN', 'DEMO_OTHER=keep\n'),
                ('secrets_unset DEMO_OTHER', ''),
            ]:
                result = subprocess.run([shutil.which('bash'), '-c', 'source "$1"; ' + operation,
                    'test', str(SOURCE / 'load.sh')],
                    env=dict(os.environ, GHOST_ALICE_SECRETS_FILE=str(path), TMPDIR=directory),
                    text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(path.read_text(), expected)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(shutil.which("bash"), "Bash is required for the shell credential reader")
    def test_shell_and_python_select_last_assignment_in_lf_and_crlf_files(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "secrets.env"
                path.write_bytes((f'DEMO_TOKEN="first-value"{newline}'
                                  f'DEMO_TOKEN="last-value"{newline}').encode())
                env = dict(os.environ, GHOST_ALICE_SECRETS_FILE=str(path))
                env.pop("DEMO_TOKEN", None)
                with mock.patch.dict(os.environ, env, clear=True):
                    self.assertEqual(loader.get("DEMO_TOKEN"), "last-value")
                result = subprocess.run([shutil.which("bash"), "-c", 'source "$1"; secrets_get DEMO_TOKEN',
                                         "test", str(SOURCE / "load.sh")],
                                        env=env, text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "last-value")

    def test_failed_replacement_preserves_existing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.env"
            original = b"DEMO_TOKEN=original-value\n"
            path.write_bytes(original)
            path.chmod(0o600)
            with mock.patch.dict(os.environ, {"GHOST_ALICE_SECRETS_FILE": str(path)}), \
                 mock.patch.object(atomic_write.os, "replace", side_effect=OSError("replacement refused")):
                with self.assertRaises(OSError):
                    loader.set("DEMO_TOKEN", "changed-value")
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(".secrets.env.*.tmp")), [])
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_successful_replacement_keeps_other_keys_and_owner_only_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.env"
            path.write_text("DEMO_TOKEN=original-value\nDEMO_OTHER=keep-value\n", encoding="utf-8")
            path.chmod(0o600)
            result = subprocess.run([sys.executable, "-I", str(SOURCE / "load.py"), "set", "DEMO_TOKEN", "changed-value"],
                                    env=dict(os.environ, GHOST_ALICE_SECRETS_FILE=str(path)),
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            with mock.patch.dict(os.environ, {"GHOST_ALICE_SECRETS_FILE": str(path)}):
                self.assertEqual(loader._read_file(), {"DEMO_TOKEN": "changed-value", "DEMO_OTHER": "keep-value"})
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
