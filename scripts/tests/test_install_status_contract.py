import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_ps1_source


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_DOCTOR = REPO_ROOT / "_shared" / "install_doctor.py"
PENDING_MANIFEST_WRITER = REPO_ROOT / "_shared" / "pending_manifest_writer.py"
CODEX_MARKER = "# Ghost-ALICE Codex Bootstrap"
CODEX_BLOCK_BEGIN = "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->"
CODEX_BLOCK_END = "<!-- Ghost-ALICE managed block end: codex-bootstrap -->"
CLAUDE_MARKER = "# Ghost-ALICE Claude Bootstrap"
CLAUDE_BLOCK_BEGIN = "<!-- Ghost-ALICE managed block begin: claude-bootstrap -->"
CLAUDE_BLOCK_END = "<!-- Ghost-ALICE managed block end: claude-bootstrap -->"


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


class InstallStatusContractTest(unittest.TestCase):
    def _valid_encoding_root(self, root: Path) -> Path:
        repo = root / "repo"
        skill = repo / "task-router"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: task-router\ndescription: Test skill.\n---\n\n# Task Router\n",
            encoding="utf-8",
        )
        return repo

    def _managed_skill_root(
        self,
        root: Path,
        *,
        installed_at: str,
        marker_platform: str = "codex",
    ) -> Path:
        target = root / ".agents" / "skills" / "task-router"
        target.mkdir(parents=True)
        skill_file = target / "SKILL.md"
        skill_file.write_text("# managed skill\n", encoding="utf-8")
        digest = hashlib.sha256(skill_file.read_bytes()).hexdigest()
        marker = {
            "schema_version": 1,
            "managed_by": "Ghost-ALICE",
            "platform": marker_platform,
            "asset_id": "task-router",
            "source_repo": "test",
            "source_commit": "abc123",
            "installed_at": installed_at,
            "install_mode": "copy",
            "content_hashes": {"SKILL.md": digest},
            "encoding_contract": {"text": "utf-8-strict"},
        }
        (target / ".ghost-alice-install.json").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target

    def _write_snapshot(self, ghost_alice_root: Path, *, captured_at: str) -> None:
        pending = ghost_alice_root / "pending-merges" / "codex"
        pending.mkdir(parents=True)
        (pending / "snapshot.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "platform": "codex",
                    "captured_at": captured_at,
                    "files": {},
                    "file_records": {},
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_pending_manifest(self, ghost_alice_root: Path, platform: str, undecided_count: int) -> None:
        pending = ghost_alice_root / "pending-merges" / platform
        pending.mkdir(parents=True, exist_ok=True)
        entries = [
            {
                "source_path": f"skill-{index}/SKILL.md",
                "decided": False,
            }
            for index in range(undecided_count)
        ]
        (pending / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "platform": platform,
                    "entries": entries,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_install_state_manifest(self, ghost_alice_root: Path, *, target_names: list[str]) -> Path:
        manifest = ghost_alice_root / "install-state" / "codex.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "platform": "codex",
                    "targets": [{"target_name": name} for name in target_names],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return manifest

    def test_doctor_reports_ownership_pending_snapshot_and_encoding_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            ghost_alice = root / ".ghost-alice"
            pending = ghost_alice / "pending-merges" / "codex"
            pending.mkdir(parents=True)
            (pending / "manifest.json").write_text(
                '{"entries":[{"source_path":"task-router/SKILL.md","decided":false}]}\n',
                encoding="utf-8",
            )
            skills = root / ".agents" / "skills"
            target = skills / "task-router"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("# local edit without marker\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(ghost_alice),
                    "--target",
                    "task-router",
                    str(target),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("ownership: warning", result.stdout)
            self.assertIn("task-router", result.stdout)
            self.assertIn("legacy-no-baseline", result.stdout)
            self.assertIn("pending-merge: pending (1 undecided)", result.stdout)
            self.assertIn("snapshot: missing", result.stdout)
            self.assertIn("encoding: ok", result.stdout)

    def test_doctor_reports_cross_platform_pending_advisory_without_changing_current_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            ghost_alice = root / ".ghost-alice"
            self._write_snapshot(ghost_alice, captured_at="2026-01-02T00:00:00Z")
            self._write_pending_manifest(ghost_alice, "codex", 0)
            self._write_pending_manifest(ghost_alice, "claude", 30)

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(ghost_alice),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("pending-merge: clean", result.stdout)
            self.assertIn(
                "merge-companion cross-platform advisory: claude has 30 undecided entries; current platform codex is clean.",
                result.stdout,
            )
            self.assertIn("overall: warning", result.stdout)

    def test_doctor_accepts_fresh_snapshot_for_managed_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            ghost_alice = root / ".ghost-alice"
            self._write_snapshot(ghost_alice, captured_at="2026-01-02T00:00:00Z")
            target = self._managed_skill_root(root, installed_at="2026-01-01T00:00:00Z")

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(ghost_alice),
                    "--target",
                    "task-router",
                    str(target),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("ownership: ok", result.stdout)
            self.assertIn("snapshot: present", result.stdout)

    def test_doctor_does_not_mark_snapshot_stale_from_other_shared_platform_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            ghost_alice = root / ".ghost-alice"
            self._write_snapshot(ghost_alice, captured_at="2026-01-01T00:00:00Z")
            target = self._managed_skill_root(
                root,
                installed_at="2026-01-02T00:00:00Z",
                marker_platform="claude",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(ghost_alice),
                    "--target",
                    "task-router",
                    str(target),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("ownership: ok", result.stdout)
            self.assertIn("snapshot: present", result.stdout)
            self.assertIn("overall: ok", result.stdout)

    def test_doctor_uses_install_state_manifest_targets_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            ghost_alice = root / ".ghost-alice"
            self._write_snapshot(ghost_alice, captured_at="2026-01-02T00:00:00Z")
            manifest = self._write_install_state_manifest(ghost_alice, target_names=["task-router"])
            target = self._managed_skill_root(root, installed_at="2026-01-01T00:00:00Z")

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(ghost_alice),
                    "--install-state-manifest",
                    str(manifest),
                    "--target",
                    "task-router",
                    str(target),
                    "--target",
                    "custom-local-skill",
                    str(root / ".agents" / "skills" / "custom-local-skill"),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("ownership: ok", result.stdout)
            self.assertIn("task-router", result.stdout)
            self.assertNotIn("custom-local-skill", result.stdout)
            self.assertIn("overall: ok", result.stdout)

    def test_doctor_still_reports_manifest_target_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            ghost_alice = root / ".ghost-alice"
            self._write_snapshot(ghost_alice, captured_at="2026-01-02T00:00:00Z")
            manifest = self._write_install_state_manifest(ghost_alice, target_names=["task-router"])

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(ghost_alice),
                    "--install-state-manifest",
                    str(manifest),
                    "--target",
                    "task-router",
                    str(root / ".agents" / "skills" / "task-router"),
                    "--target",
                    "custom-local-skill",
                    str(root / ".agents" / "skills" / "custom-local-skill"),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("ownership: error", result.stdout)
            self.assertIn("task-router", result.stdout)
            self.assertIn("expected-target-absent", result.stdout)
            self.assertNotIn("custom-local-skill", result.stdout)
            self.assertIn("overall: error", result.stdout)

    def test_doctor_strict_does_not_fail_for_pending_merge_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            ghost_alice = root / ".ghost-alice"
            self._write_snapshot(ghost_alice, captured_at="2026-01-02T00:00:00Z")
            self._write_pending_manifest(ghost_alice, "codex", 1)

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(ghost_alice),
                    "--strict",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("pending-merge: pending (1 undecided)", result.stdout)
            self.assertIn("overall: warning", result.stdout)

    def test_doctor_reports_stale_snapshot_when_marker_is_newer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            ghost_alice = root / ".ghost-alice"
            self._write_snapshot(ghost_alice, captured_at="2026-01-01T00:00:00Z")
            target = self._managed_skill_root(root, installed_at="2026-01-02T00:00:00Z")

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(ghost_alice),
                    "--target",
                    "task-router",
                    str(target),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("ownership: ok", result.stdout)
            self.assertIn("snapshot: stale", result.stdout)
            self.assertIn("captured-before-latest-install", result.stdout)
            self.assertIn("overall: warning", result.stdout)

    def test_doctor_reports_invalid_snapshot_json_even_without_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            ghost_alice = root / ".ghost-alice"
            pending = ghost_alice / "pending-merges" / "codex"
            pending.mkdir(parents=True)
            (pending / "snapshot.json").write_text("{invalid json", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(ghost_alice),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("snapshot: invalid", result.stdout)
            self.assertIn("JSONDecodeError", result.stdout)
            self.assertIn("overall: error", result.stdout)

    def test_doctor_strict_returns_nonzero_for_invalid_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            (repo / "bad.md").write_bytes(b"\xff")

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(root / ".ghost-alice"),
                    "--strict",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("encoding: error", result.stdout)
            self.assertIn("invalid-utf8", result.stdout)

    def test_doctor_checks_every_encoding_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            installed = root / ".agents" / "skills"
            repo.mkdir()
            installed_skill = installed / "custom-local-skill"
            installed_skill.mkdir(parents=True)
            (repo / "bad.md").write_bytes(b"\xff")
            (installed_skill / "SKILL.md").write_text(
                "---\nname: custom-local-skill\ndescription: Local skill.\n---\n\n# Local\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--encoding-root",
                    str(installed),
                    "--ghost-alice-root",
                    str(root / ".ghost-alice"),
                    "--strict",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("encoding: error", result.stdout)
            self.assertIn("bad.md", result.stdout)
            self.assertIn("invalid-utf8", result.stdout)

    def test_doctor_reports_missing_global_rule_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            agents = root / ".codex" / "AGENTS.md"

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(root / ".ghost-alice"),
                    "--global-rule",
                    "codex-bootstrap",
                    str(agents),
                    CODEX_MARKER,
                    CODEX_BLOCK_BEGIN,
                    CODEX_BLOCK_END,
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("global-rule: error", result.stdout)
            self.assertIn("codex-bootstrap", result.stdout)
            self.assertIn("rule-file-absent", result.stdout)

    def test_doctor_accepts_managed_global_rule_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            agents = root / ".codex" / "AGENTS.md"
            agents.parent.mkdir(parents=True)
            agents.write_text(
                f"user rule\n{CODEX_BLOCK_BEGIN}\n# managed\n{CODEX_BLOCK_END}\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "codex",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(root / ".ghost-alice"),
                    "--global-rule",
                    "codex-bootstrap",
                    str(agents),
                    CODEX_MARKER,
                    CODEX_BLOCK_BEGIN,
                    CODEX_BLOCK_END,
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("global-rule: ok", result.stdout)
            self.assertIn("codex-bootstrap", result.stdout)
            self.assertIn("managed-block-present", result.stdout)

    def test_doctor_does_not_count_markerless_claude_file_or_proposal_as_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._valid_encoding_root(root)
            claude_rules = root / ".claude" / "CLAUDE.md"
            claude_rules.parent.mkdir(parents=True)
            claude_rules.write_text("# user rules\n", encoding="utf-8")
            claude_rules.with_name("CLAUDE.md.ghost-alice-proposed").write_text(
                f"{CLAUDE_MARKER}\n{CLAUDE_BLOCK_BEGIN}\nmanaged\n{CLAUDE_BLOCK_END}\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(INSTALL_DOCTOR),
                    "--platform",
                    "claude",
                    "--repo-root",
                    str(repo),
                    "--encoding-root",
                    str(repo),
                    "--ghost-alice-root",
                    str(root / ".ghost-alice"),
                    "--global-rule",
                    "claude-bootstrap",
                    str(claude_rules),
                    CLAUDE_MARKER,
                    CLAUDE_BLOCK_BEGIN,
                    CLAUDE_BLOCK_END,
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertNotIn("global-rule: ok", result.stdout)
            self.assertIn("global-rule: warning", result.stdout)
            self.assertIn("claude-bootstrap", result.stdout)
            self.assertIn("user-owned markerless-existing-rule-file", result.stdout)

    def test_installers_wire_status_and_doctor_to_common_cli(self) -> None:
        # The bash side of this contract now lives in _shared/test_installer_hosts_preservation_diagnostics.py.
        ps1 = installer_ps1_source()
        self.assertIn("[switch]$Doctor", ps1)
        self.assertIn("function Invoke-InstallDoctor", ps1)
        self.assertIn("install_doctor.py", ps1)
        self.assertIn("--global-rule", ps1)
        self.assertIn("$ClaudeManagedBlockBegin", ps1)
        self.assertIn("$CodexManagedBlockBegin", ps1)
        self.assertIn("if ($Doctor)", ps1)
        self.assertIn('Invoke-InstallDoctor -Mode "status"', _extract_powershell_function(ps1, "Show-Status"))
        ps_doctor = _extract_powershell_function(ps1, "Invoke-InstallDoctor")
        self.assertIn('"--global-rule", "claude-bootstrap"', ps_doctor)
        self.assertIn('"--encoding-root", $ScriptDir', ps_doctor)
        self.assertIn('"--encoding-root", $SkillsRoot', ps_doctor)
        self.assertIn("--install-state-manifest", ps_doctor)
        powershell_supported = _extract_powershell_function(ps1, "Test-CodexHooksSupported")
        self.assertIn("return $true", powershell_supported)
        self.assertNotIn("Windows_NT", powershell_supported)
        self.assertIn(
            'Invoke-InstallHooks -Action "status" -TargetPlatform $Platform',
            _extract_powershell_function(ps1, "Show-Status"),
        )
        powershell_hooks = _extract_powershell_function(ps1, "Invoke-InstallHooks")
        self.assertIn("Test-CodexHooksSupported", powershell_hooks)
        self.assertIn("Codex hooks are unavailable in this runtime", powershell_hooks)
        self.assertNotIn("disabled on Windows", powershell_hooks)
        self.assertIn('"--hook-shared-dir", (Resolve-GhostAliceRuntimeSharedDir)', powershell_hooks)
        self.assertIn('"--runtime-shared", (Resolve-GhostAliceRuntimeSharedDir)', ps_doctor)
        self.assertIn('"--hook-config", (Join-Path (Resolve-CodexHome) "hooks.json")', ps_doctor)
        self.assertIn('[Alias("Visibility", "agent-visibility")]', ps1)
        self.assertIn("[string]$AgentVisibility", ps1)
        self.assertIn('$visibility = $AgentVisibility', powershell_hooks)
        self.assertIn('if ($Action -eq "install" -and -not $visibility) {', powershell_hooks)
        self.assertIn('$visibility = "dynamic"', powershell_hooks)
        self.assertIn('"--visibility", $visibility', powershell_hooks)

    def test_status_and_doctor_forward_selected_addon_sources_to_hook_status(self) -> None:
        # The bash side of this contract now lives in _shared/test_installer_hosts_preservation_diagnostics.py.
        powershell_hooks = _extract_powershell_function(
            installer_ps1_source(),
            "Invoke-InstallHooks",
        )
        self.assertIn('$Action -ne "uninstall"', powershell_hooks)
        self.assertIn('$pyArgs += @("--addon-source", $source)', powershell_hooks)

        powershell_status = _extract_powershell_function(
            installer_ps1_source(),
            "Show-Status",
        )
        powershell_doctor = _extract_powershell_function(
            installer_ps1_source(),
            "Show-Doctor",
        )
        expected_call = 'Invoke-InstallHooks -Action "status" -TargetPlatform $Platform'
        self.assertIn(expected_call, powershell_status)
        self.assertIn(expected_call, powershell_doctor)

    def test_installers_write_empty_pending_merge_manifest_after_snapshot(self) -> None:
        transaction_py = (REPO_ROOT / "_shared" / "installer" / "transaction.py").read_text(encoding="utf-8")
        # The installer delegates empty-manifest serialization to the _shared writer.
        self.assertIn("pending_manifest_writer.py", transaction_py)
        self.assertIn("def write_empty_pending_manifest_if_missing", transaction_py)
        writer_py = PENDING_MANIFEST_WRITER.read_text(encoding="utf-8")
        self.assertIn('"version": 1', writer_py)
        self.assertIn('"platform": platform', writer_py)
        self.assertIn('"entries": []', writer_py)

        ps1 = installer_ps1_source()
        ps_writer = _extract_powershell_function(ps1, "Write-EmptyPendingManifestIfMissing")
        self.assertIn("version = 1", ps_writer)
        self.assertIn("platform = $TargetPlatform", ps_writer)
        self.assertIn("entries = @()", ps_writer)
        self.assertIn(
            "Write-EmptyPendingManifestIfMissing -TargetPlatform $TargetPlatform -Manifest $manifest",
            _extract_powershell_function(ps1, "Invoke-SnapshotAfterInstall"),
        )

    def test_readme_documents_platform_specific_visibility_commands(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Claude Code uses `/visibility strict|dynamic|minimal`", readme)
        self.assertIn("Every platform, including Codex, can inspect and change the same profile value through "
                      "`_shared/agent_visibility_cli.py`", readme)
        self.assertIn("Codex has no `/visibility` command.", readme)
        self.assertNotIn("Claude workspace command `/visibility", readme)


if __name__ == "__main__":
    unittest.main()
