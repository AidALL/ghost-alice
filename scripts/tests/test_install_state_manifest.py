import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source, installer_ps1_source


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
INSTALL_STATE_SCHEMA = REPO_ROOT / "installer_update" / "install_state_schema.md"
INSTALL_STATE_WRITER = REPO_ROOT / "_shared" / "install_state_writer.py"


def _extract_bash_function(source: str, name: str) -> str:
    lines = source.splitlines(keepends=True)
    start = None
    for idx, line in enumerate(lines):
        if line.startswith(f"{name}() {{"):
            start = idx
            break
    if start is None:
        raise AssertionError(f"Function not found: {name}")

    body = []
    depth = 0
    for line in lines[start:]:
        body.append(line)
        depth += line.count("{")
        depth -= line.count("}")
        if depth == 0:
            return "".join(body)
    raise AssertionError(f"Function did not terminate: {name}")


def _extract_powershell_function(source: str, name: str) -> str:
    lines = source.splitlines(keepends=True)
    start = None
    for idx, line in enumerate(lines):
        if line.startswith(f"function {name} "):
            start = idx
            break
    if start is None:
        raise AssertionError(f"Function not found: {name}")

    body = []
    depth = 0
    for line in lines[start:]:
        body.append(line)
        depth += line.count("{")
        depth -= line.count("}")
        if depth == 0:
            return "".join(body)
    raise AssertionError(f"Function did not terminate: {name}")


def _find_test_bash() -> str | None:
    candidates = [
        shutil.which("bash"),
        shutil.which("bash.exe"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        normalized = path.as_posix().lower()
        if sys.platform.startswith("win") and (
            normalized.endswith("/windows/system32/bash.exe")
            or normalized.endswith("/appdata/local/microsoft/windowsapps/bash.exe")
        ):
            continue
        return str(path)

    return None


class InstallStateManifestTest(unittest.TestCase):
    def test_install_state_schema_document_defines_task_one_fields(self) -> None:
        self.assertTrue(INSTALL_STATE_SCHEMA.exists())
        body = INSTALL_STATE_SCHEMA.read_text(encoding="utf-8")

        for field in [
            "schema_version",
            "source_root",
            "source_branch",
            "source_head",
            "source_dirty_state",
            "remote_freshness_state",
            "target_name",
            "source_path",
            "dest_path",
            "install_mode",
            "target_tree_hash",
            "managed_markers",
            "installed_at",
            "copy-fallback",
        ]:
            self.assertIn(field, body)

    def test_installers_distinguish_copy_fallback_manifest_mode(self) -> None:
        self.assertIn("copy-fallback", installer_bash_source())
        self.assertIn("copy-fallback", installer_ps1_source())

    def test_install_ps1_declares_install_state_manifest_writer(self) -> None:
        body = installer_ps1_source()

        self.assertIn("function Write-InstallStateManifest", body)
        self.assertIn("install-state", body)
        self.assertIn("schema_version", body)
        self.assertIn("source_head", body)
        self.assertIn("managed_markers", body)
        self.assertIn("target_tree_hash", body)
        self.assertIn("Write-InstallStateManifest -TargetPlatform $Platform", body)

    def test_install_state_manifest_failure_is_blocking_in_bash_installer(self) -> None:
        body = installer_bash_source()
        writer = _extract_bash_function(body, "write_install_state_manifest")

        self.assertIn("return 1", writer)
        self.assertIn("Install-state manifest write failed; aborting install", writer)
        self.assertIn('write_install_state_manifest "$SKILLS_DIR" "$copy_only" "${skills[@]}" || exit 1', body)
        self.assertNotIn("Install-state manifest was not written", writer)

    def test_install_state_manifest_failure_is_blocking_in_powershell_installer(self) -> None:
        body = installer_ps1_source()
        writer = _extract_powershell_function(body, "Write-InstallStateManifest")

        self.assertIn("throw", writer)
        self.assertIn("Install-state manifest write failed; aborting install", writer)
        self.assertNotIn("continuing", writer)

    def test_install_state_schema_document_defines_system_env_changes_field(self) -> None:
        body = INSTALL_STATE_SCHEMA.read_text(encoding="utf-8")
        self.assertIn("system_env_changes", body)
        for kind in [
            "ps_policy_change",
            "posix_rc_change",
            "macos_quarantine_fix",
            "posix_chmod_fix",
            "source_repo_hook_path",
            "codex_hooks_feature_flag",
        ]:
            self.assertIn(kind, body)

    def test_install_ps1_writer_records_system_env_changes_field(self) -> None:
        body = installer_ps1_source()
        writer = _extract_powershell_function(body, "Write-InstallStateManifest")
        self.assertIn("system_env_changes = @(Get-SystemEnvChangesForInstallState)", writer)
        self.assertIn("source_repo_hook_path", body)

    def test_install_sh_writer_records_system_env_changes_field(self) -> None:
        body = installer_bash_source()
        writer = _extract_bash_function(body, "write_install_state_manifest")
        self.assertIn("GHOST_ALICE_SOURCE_REPO_HOOK_CHANGED", writer)
        # source_repo_hook_path is computed in the extracted Python module
        writer_py = INSTALL_STATE_WRITER.read_text(encoding="utf-8")
        self.assertIn("source_repo_hook_path", writer_py)

    def test_python_writer_imports_codex_hook_feature_flag_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / ".ghost-alice" / "install-state" / "codex.json"
            sidecar = state_path.with_name("codex-hook-feature-change.json")
            sidecar.parent.mkdir(parents=True)
            config_toml = root / ".codex" / "config.toml"
            config_toml.parent.mkdir()
            config_toml.write_text("[features]\nhooks = true\n", encoding="utf-8")
            sidecar.write_text(
                json.dumps(
                    {
                        "kind": "codex_hooks_feature_flag",
                        "path": config_toml.as_posix(),
                        "before_state": "false",
                        "after_state": "true",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_STATE_WRITER),
                    "codex",
                    str(root / "repo"),
                    "main",
                    "abc123",
                    "clean",
                    str(state_path),
                ],
                env={**os.environ, "HOME": str(root)},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            manifest = json.loads(state_path.read_text(encoding="utf-8"))
            changes = manifest["system_env_changes"]
            self.assertEqual(1, len(changes))
            self.assertEqual("codex_hooks_feature_flag", changes[0]["kind"])
            self.assertEqual("false", changes[0]["before_state"])
            self.assertEqual("true", changes[0]["after_state"])


if __name__ == "__main__":
    unittest.main()
