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
    def test_install_migrates_failure_only_state_and_preserves_unreadable_state(self):
        for old in (b'{"schema_version":1,"platform":"claude","partial_failure":true,"failures":[]}', b'{broken'):
            with self.subTest(old=old), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state = root / 'install-state/claude.json'
                state.parent.mkdir()
                state.write_bytes(old)
                source = root / 'skill'
                source.mkdir()
                (source/'SKILL.md').write_text('current source')
                result = subprocess.run([sys.executable, str(INSTALL_STATE_WRITER), 'claude', str(root),
                    'main', 'head', 'clean', str(state), 'skill', str(source), str(source), 'copy'],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(state.read_text())['targets'][0]['target_name'], 'skill')
                if old == b'{broken':
                    self.assertEqual([p.read_bytes() for p in state.parent.glob('claude.json.*.bak')], [old])

    def test_partial_reinstall_keeps_unselected_core_baseline_and_host_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / '.ghost-alice/install-state/claude.json'
            targets = []
            for name in ('alpha', 'beta'):
                path = root/name
                path.mkdir()
                (path/'SKILL.md').write_text(name, encoding='utf-8')
                targets.extend([name, str(path), str(path), 'copy'])
            command = [sys.executable, str(INSTALL_STATE_WRITER), 'claude', str(root), 'main', 'first',
                       'clean', str(state)]
            first = subprocess.run(command + targets, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            beta = json.loads(state.read_text())['targets'][1]
            (root/'beta/SKILL.md').write_text('unselected user change', encoding='utf-8')
            other = state.with_name('codex.json')
            other.write_text('{"platform":"codex","targets":[]}', encoding='utf-8')
            original_other = other.read_bytes()
            second = subprocess.run(command + targets[:4], capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            rows = {row['target_name']: row for row in json.loads(state.read_text())['targets']}
            self.assertEqual(set(rows), {'alpha', 'beta'})
            self.assertEqual(rows['beta'], beta)
            self.assertEqual(other.read_bytes(), original_other)

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
        # wrote the install source's hooks folder. A later install without new hook input retains that record
        # so full uninstall can still restore it (install_state_schema.md, system_env_changes).
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
            expected_changes = [{
                "kind": "source_repo_hook_path",
                "repo_root": (root / "repo").as_posix(),
                "before_present": False,
                "before": None,
                "after": hooks,
                "applied_at": "2026-10-01T00:00:00Z",
            }]
            self.assertEqual(manifest["system_env_changes"], expected_changes)
            env["GHOST_ALICE_SOURCE_REPO_HOOK_RECORDED"] = "0"
            result = subprocess.run(argv, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            manifest = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["system_env_changes"], expected_changes)

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
