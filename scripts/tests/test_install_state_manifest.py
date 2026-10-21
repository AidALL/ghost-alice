import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path



REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_STATE_SCHEMA = REPO_ROOT / "installer_update" / "install_state_schema.md"
INSTALL_STATE_WRITER = REPO_ROOT / "_shared" / "install_state_writer.py"


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
        transaction_py = (REPO_ROOT / "_shared" / "installer" / "transaction.py").read_text(encoding="utf-8")
        self.assertIn("copy-fallback", transaction_py)

    def test_install_state_schema_document_defines_system_env_changes_field(self) -> None:
        body = INSTALL_STATE_SCHEMA.read_text(encoding="utf-8")
        self.assertIn("system_env_changes", body)
        for kind in [
            "source_repo_hook_path",
            "codex_hooks_feature_flag",
            "codex_project_trust",
        ]:
            self.assertIn(kind, body)

    def test_installer_writer_records_system_env_changes_field(self) -> None:
        transaction_py = (REPO_ROOT / "_shared" / "installer" / "transaction.py").read_text(encoding="utf-8")
        self.assertIn("GHOST_ALICE_SOURCE_REPO_HOOK_RECORDED", transaction_py)
        # source_repo_hook_path is computed by the Python installer module
        writer_py = INSTALL_STATE_WRITER.read_text(encoding="utf-8")
        self.assertIn("source_repo_hook_path", writer_py)

    def test_python_writer_records_the_hook_path_the_install_owns(self) -> None:
        # The entry tells the full uninstall what the repository's shared core.hooksPath was before Ghost-ALICE
        # wrote the install source's hooks folder; an install that does not know it records no entry.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / ".ghost-alice" / "install-state" / "claude.json"
            hooks = (root / "repo" / "hooks").as_posix()
            argv = [sys.executable, str(INSTALL_STATE_WRITER), "claude", str(root / "repo"), "main", "abc123", "clean",
                    str(state_path)]
            env = {**os.environ, "HOME": str(root),
                   "GHOST_ALICE_SOURCE_REPO_HOOK_RECORDED": "1",
                   "GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE_PRESENT": "0",
                   "GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE": "",
                   "GHOST_ALICE_SOURCE_REPO_HOOK_AFTER": hooks,
                   "GHOST_ALICE_SOURCE_REPO_HOOK_APPLIED_AT": "2026-10-01T00:00:00Z"}
            result = subprocess.run(argv, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            manifest = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["system_env_changes"], [{
                "kind": "source_repo_hook_path",
                "repo_root": (root / "repo").as_posix(),
                "before_present": False,
                "before": None,
                "after": hooks,
                "applied_at": "2026-10-01T00:00:00Z",
            }])
            env["GHOST_ALICE_SOURCE_REPO_HOOK_RECORDED"] = "0"
            result = subprocess.run(argv, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            manifest = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["system_env_changes"], [])

    def test_python_writer_imports_codex_hook_feature_flag_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / ".ghost-alice" / "install-state" / "codex.json"
            sidecar = state_path.with_name("codex-hook-feature-change.json")
            trust_sidecar = state_path.with_name("codex-project-trust-change.json")
            sidecar.parent.mkdir(parents=True)
            config_toml = root / ".codex" / "config.toml"
            config_toml.parent.mkdir()
            config_toml.write_text("[features]\nhooks = true\n", encoding="utf-8")
            project = root / "repo"
            project.mkdir()
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
            trust_sidecar.write_text(
                json.dumps(
                    {
                        "kind": "codex_project_trust",
                        "path": config_toml.as_posix(),
                        "project_path": str(project.resolve()),
                        "before_state": "missing",
                        "after_state": "trusted",
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
            self.assertEqual(2, len(changes))
            self.assertEqual("codex_hooks_feature_flag", changes[0]["kind"])
            self.assertEqual("false", changes[0]["before_state"])
            self.assertEqual("true", changes[0]["after_state"])
            self.assertEqual("codex_project_trust", changes[1]["kind"])
            self.assertEqual(str(project.resolve()), changes[1]["project_path"])
            self.assertEqual("missing", changes[1]["before_state"])
            self.assertEqual("trusted", changes[1]["after_state"])


if __name__ == "__main__":
    unittest.main()
