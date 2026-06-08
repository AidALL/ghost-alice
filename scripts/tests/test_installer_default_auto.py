from __future__ import annotations

import json
import os
import re
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
README = REPO_ROOT / "README.md"


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


def _skill_target_count(*skill_names: str) -> int:
    total = 0
    for skill_name in skill_names:
        skill_root = REPO_ROOT / skill_name
        if (skill_root / "SKILL.md").exists():
            total += 1
            continue
        total += sum(
            1
            for child in skill_root.iterdir()
            if child.is_dir() and (child / "SKILL.md").exists()
        )
    return total


def _read_install_report_events(temp_home: Path) -> list[dict[str, object]]:
    event_files = sorted((temp_home / ".ghost-alice" / "install").glob("*.events.jsonl"))
    if not event_files:
        return []
    events: list[dict[str, object]] = []
    for line in event_files[-1].read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def _run_pty(command: list[str], *, env: dict[str, str]) -> tuple[int, str]:
    if not hasattr(os, "openpty"):
        raise unittest.SkipTest("pseudo-terminal support is required for TTY progress test")

    import select

    master_fd, slave_fd = os.openpty()
    try:
        proc = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        slave_fd = -1

        chunks: list[bytes] = []
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            if proc.poll() is not None:
                while True:
                    ready, _, _ = select.select([master_fd], [], [], 0)
                    if not ready:
                        break
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    chunks.append(data)
                break
        return_code = proc.wait(timeout=5)
    finally:
        if slave_fd != -1:
            os.close(slave_fd)
        os.close(master_fd)

    return return_code, b"".join(chunks).decode("utf-8", errors="replace")


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _printable_live_progress_lines(output: str) -> list[str]:
    lines: list[str] = []
    for segment in re.split(r"[\r\n]", output):
        clean = ANSI_RE.sub("", segment)
        if "Target operations" in clean or "Sync [" in clean:
            lines.append(clean)
    return lines


class InstallerDefaultAutoTest(unittest.TestCase):
    def test_install_sh_defaults_plain_install_to_auto_detect(self) -> None:
        install_sh = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn("AUTO_DETECT=0", install_sh)
        self.assertIn("AUTO_DETECT=1", install_sh)
        self.assertIn("# auto/default:", install_sh)
        self.assertIn('[ "$CLEANUP_PENDING" -eq 0 ]', install_sh)
        self.assertIn('"")\n      AUTO_DETECT=1', install_sh)
        self.assertIn('if [ "$AUTO_DETECT" -eq 1 ]; then', install_sh)
        self.assertIn("bash install.sh                              #", install_sh)
        self.assertIn("Install to detected AI tools", install_sh)
        self.assertNotIn("bash install.sh --auto                       #", install_sh)
        self.assertNotIn("AUTO_DETECT_REQUESTED", install_sh)
        self.assertNotIn("--auto and --platform", install_sh)
        self.assertNotIn("--auto and --prompt-platform", install_sh)

    def test_install_ps1_defaults_plain_install_to_auto_detect(self) -> None:
        install_ps1 = installer_ps1_source()

        self.assertIn("[switch]$Auto,", install_ps1)
        self.assertIn("if (-not $PlatformWasExplicit -and -not $PromptPlatform -and -not $hasInspectionCommand -and -not $PlainFullUninstall -and -not $CleanupPending -and -not $UpdateSource) {", install_ps1)
        self.assertIn("$Auto = $true", install_ps1)
        self.assertIn(".\\install.cmd                          # Install to detected AI tools", install_ps1)
        self.assertNotIn(".\\install.ps1 -Auto                    # Recommended", install_ps1)
        self.assertNotIn("$AutoDetectRequested", install_ps1)
        self.assertNotIn("-Auto and -Platform", install_ps1)
        self.assertNotIn("-Auto and -PromptPlatform", install_ps1)

    def test_auto_install_messages_explain_distinct_platform_targets(self) -> None:
        install_sh = INSTALL_SH.read_text(encoding="utf-8")
        install_ps1 = INSTALL_PS1.read_text(encoding="utf-8-sig")

        for body in (install_sh, install_ps1):
            with self.subTest(installer=body[:20]):
                self.assertIn("separate install path", body)
                self.assertIn("not a duplicate install", body)

        self.assertIn("[auto] (${platform_index}/${#detected[@]})", install_sh)
        self.assertIn("[auto] ({0}/{1})", install_ps1)

    def test_install_ps1_auto_report_uses_common_target_progress(self) -> None:
        install_ps1 = installer_ps1_source()
        auto_branch = install_ps1[
            install_ps1.index("# auto/default:") : install_ps1.index("if ($PromptPlatform")
        ]

        self.assertIn("Write-InstallReportAutoFull", install_ps1)
        self.assertIn("Write-InstallReportTargetEvent", install_ps1)
        self.assertIn("Read-AllCommonTargetProgress", install_ps1)
        self.assertIn("Read-WeightedCommonTargetProgress", install_ps1)
        self.assertIn("common targets synced on all platforms", install_ps1)
        self.assertIn("$autoCommonTargets = Get-InstallTargetCount -Targets $fallbackTargets", auto_branch)
        self.assertNotIn("* $detected.Count", auto_branch)

    def test_install_ps1_auto_child_output_avoids_shared_log_redirection(self) -> None:
        install_ps1 = INSTALL_PS1.read_text(encoding="utf-8-sig")
        auto_branch = install_ps1[
            install_ps1.index("# auto/default:") : install_ps1.index("if ($PromptPlatform")
        ]

        self.assertIn("$childOutputFile = Join-Path", auto_branch)
        self.assertIn("& $pwshExe @engineArgs *> $childOutputFile", auto_branch)
        self.assertIn("Get-Content -LiteralPath $childOutputFile", auto_branch)
        self.assertNotIn("*>> $script:InstallReportLogFile", auto_branch)

    def test_auto_progress_uses_short_platform_suffix_and_wide_bar(self) -> None:
        install_sh = installer_bash_source()
        install_ps1 = installer_ps1_source()

        self.assertIn('report_progress_bar "$done_count" "$total_count" 30', install_sh)
        self.assertIn('"${plat} ${platform_index}/${auto_platform_count}"', install_sh)
        self.assertIn("report_read_target_operation_progress", install_sh)
        self.assertIn("        Sync [", install_sh)
        self.assertNotIn("report_auto_progress_cursor_up_rows", install_sh)
        self.assertNotIn("report_print_tail_pending", install_sh)
        self.assertNotIn("report_print_tail_overwrite", install_sh)
        self.assertNotIn("common targets synced for ${plat}", install_sh)

        self.assertIn("[int]$Width = 30", install_ps1)
        self.assertIn('"For {0} [{1}/{2}]" -f $plat, $index, $detected.Count', install_ps1)
        self.assertIn("Read-WeightedCommonTargetProgress", install_ps1)
        self.assertNotIn("common targets synced for", install_ps1)
        self.assertNotIn('"-Uninstall:$Uninstall"', install_ps1)
        self.assertNotIn('"-SkipSourceHealth:$SkipSourceHealth"', install_ps1)

    def test_install_sh_auto_failure_message_preserves_child_exit_code(self) -> None:
        install_sh = INSTALL_SH.read_text(encoding="utf-8")
        auto_loop = install_sh[install_sh.index("# auto/default:") : install_sh.index('if [ "$PROMPT_PLATFORM" -eq 1 ]')]

        self.assertIn("child_rc=$?", auto_loop)
        self.assertIn("exit code $child_rc", auto_loop)
        self.assertNotIn('if ! bash "${BASH_SOURCE[0]}"', auto_loop)

    def test_readme_primary_usage_has_no_auto_flag(self) -> None:
        readme = README.read_text(encoding="utf-8")
        install_sh = INSTALL_SH.read_text(encoding="utf-8")
        install_ps1 = INSTALL_PS1.read_text(encoding="utf-8-sig")

        self.assertIn("bash install.sh\n", readme)
        self.assertIn(".\\install.cmd\n", readme)
        self.assertIn("install.cmd\n", readme)
        self.assertNotIn("```powershell\n.\\install.ps1\n```", readme)
        self.assertNotIn("bash install.sh --auto", readme)
        self.assertNotIn(".\\install.ps1 -Auto", readme)
        self.assertNotIn("install.cmd -Auto", readme)
        self.assertNotIn("--allow-dirty-source", readme)
        self.assertNotIn("--allow-dirty-source", install_sh)
        self.assertNotIn("AllowDirtySource", install_ps1)

    def test_plain_uninstall_defaults_to_detected_full_cleanup(self) -> None:
        install_sh = INSTALL_SH.read_text(encoding="utf-8")
        install_ps1 = INSTALL_PS1.read_text(encoding="utf-8-sig")

        self.assertIn("detect_uninstall_platforms", install_sh)
        self.assertIn('case "${ARGS[0]:-}" in', install_sh)
        self.assertIn("plain_full_uninstall_args", install_sh)
        self.assertIn("Get-DetectedUninstallPlatforms", install_ps1)
        self.assertIn("$PlainFullUninstall", install_ps1)
        self.assertIn("-Uninstall", install_ps1)

    def test_install_sh_auto_verbose_uses_expanded_output_not_process_report(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh auto-detect test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            (temp_home / ".claude").mkdir()
            (temp_home / ".codex").mkdir()

            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"

            result = subprocess.run(
                [bash, str(INSTALL_SH), "--verbose", "--skip-source-health", "task-router"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            output = result.stderr + result.stdout
            self.assertEqual(result.returncode, 0, msg=output)
            self.assertIn("Detected platforms: claude codex", output)
            self.assertIn("[auto] (1/2) Starting install for claude", output)
            self.assertIn("[auto] (2/2) Starting install for codex", output)
            self.assertNotIn("Ghost-ALICE OS installation Process Report", output)
            self.assertNotIn("rerun with --verbose", output)

    def test_install_sh_auto_tty_output_starts_report_before_live_updates(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh auto-detect test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            (temp_home / ".claude").mkdir()
            (temp_home / ".codex").mkdir()

            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            env.setdefault("TERM", "xterm-256color")

            return_code, output = _run_pty(
                [bash, str(INSTALL_SH), "--skip-source-health", "task-router"],
                env=env,
            )

            expected_common_targets = _skill_target_count("task-router") + 1
            expected_operations = expected_common_targets * 2
            skill_sync_line = f"  [2/5] Skill sync          [{expected_common_targets}] common targets"
            initial_line = (
                f"        Sync [--------------------] "
                f"[0/{expected_operations}] pending"
            )
            final_line = (
                f"\r\x1b[2K        Sync [####################] "
                f"[{expected_operations}/{expected_operations}] done"
            )
            self.assertEqual(return_code, 0, msg=output)
            self.assertEqual(output.count("Ghost-ALICE OS installation Process Report"), 1)
            self.assertIn(f"  Skills: [{expected_common_targets}] common targets", output)
            self.assertNotIn("platform-target operations", output)
            self.assertIn(skill_sync_line, output)
            self.assertIn(initial_line, output)
            self.assertNotIn("  [3/5] Hooks               pending", output)
            self.assertNotIn("\x1b[11A\r", output)
            self.assertNotIn("\x1b[1B\r", output)
            self.assertIn(final_line, output)
            for completed in range(1, expected_operations + 1):
                self.assertIn(f"[{completed}/{expected_operations}]", output)
            live_lines = _printable_live_progress_lines(output)
            self.assertTrue(live_lines, msg=output)
            self.assertLessEqual(max(len(line) for line in live_lines), 79, msg=live_lines)
            self.assertLess(output.index(initial_line), output.rindex(final_line))
            self.assertIn("  [5/5] Verification        ok", output)

    def test_install_sh_default_output_uses_process_report_with_dynamic_counts(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh integration test")

        requested_skills = ["coding-convention"]
        expected_total_targets = _skill_target_count(*requested_skills) + 1

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            (temp_home / ".codex").mkdir()
            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"

            result = subprocess.run(
                [bash, str(INSTALL_SH), "--platform", "codex", "--skip-source-health", *requested_skills],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("Ghost-ALICE OS installation Process Report", result.stdout)
            self.assertIn("Target", result.stdout)
            self.assertIn("  Platform: codex", result.stdout)
            self.assertIn(f"  Skills: [{expected_total_targets}] targets", result.stdout)
            self.assertIn("  Hooks: enabled", result.stdout)
            self.assertIn("  Visibility Level: [dynamic]", result.stdout)
            self.assertIn("Progress", result.stdout)
            self.assertIn("  [1/5] Preflight           ok", result.stdout)
            self.assertIn(
                f"  [2/5] Skill sync          [0] [Current], [0] [updated], [{expected_total_targets}] [newly added]",
                result.stdout,
            )
            self.assertIn(
                "  [3/5] Hooks               prompt, session-intent, web-search-first, tool-checkpoint, completion, session-start, io-trace enabled",
                result.stdout,
            )
            self.assertIn("  [4/5] Runtime config      codex hooks=true, Visibility Level=[dynamic]", result.stdout)
            self.assertIn("  [5/5] Verification        ok", result.stdout)
            self.assertIn("Attention", result.stdout)
            self.assertIn("visibility can be changed later with /visibility between: dynamic | minimal | strict", result.stdout)
            self.assertIn("Details", result.stdout)
            self.assertRegex(result.stdout, r"log: .+\.ghost-alice/install/\d{4}-\d{2}-\d{2}-\d{6}\.log")
            self.assertIn("rerun with --verbose to show per-skill actions", result.stdout)
            self.assertNotIn("\r[2/5] Skill sync [", result.stdout)
            self.assertNotIn("[install_hooks]", result.stdout)
            self.assertNotIn("brainstorming → copied", result.stdout)
            self.assertNotIn("→ copied (copy-only compatibility mode)", result.stdout)

    def test_install_sh_moves_deprecated_installed_skills_out_of_discovery_path(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            (temp_home / ".codex").mkdir()
            skills_root = temp_home / ".agents" / "skills"
            for name in ("harness-security-scan", "session-intent-guard"):
                skill_dir = skills_root / name
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text(f"# stale {name}\n", encoding="utf-8")

            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"

            result = subprocess.run(
                [bash, str(INSTALL_SH), "--platform", "codex", "--skip-source-health", "coding-convention"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertFalse((skills_root / "harness-security-scan").exists())
            self.assertFalse((skills_root / "session-intent-guard").exists())
            backup_root = temp_home / ".ghost-alice" / "deprecated-skill-backups"
            self.assertTrue(any(backup_root.glob("harness-security-scan-*")))
            self.assertTrue(any(backup_root.glob("session-intent-guard-*")))

    def test_install_sh_tty_output_uses_one_line_skill_sync_counter(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh integration test")

        requested_skills = ["coding-convention"]
        expected_total = _skill_target_count(*requested_skills) + 1

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            (temp_home / ".codex").mkdir()
            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            env.setdefault("TERM", "xterm-256color")

            return_code, output = _run_pty(
                [bash, str(INSTALL_SH), "--platform", "codex", "--skip-source-health", *requested_skills],
                env=env,
            )
            self.assertEqual(return_code, 0, msg=output)
            self.assertIn("\r  [2/5] Skill sync          [0] [Current], [0] [updated], [0] [newly added]", output)
            self.assertIn(
                f"\r  [2/5] Skill sync          [0] [Current], [0] [updated], [{expected_total}] [newly added]",
                output,
            )
            self.assertEqual(output.count("Ghost-ALICE OS installation Process Report"), 1)

    def test_install_sh_zsh_entrypoint_uses_bash_shebang_live_counter(self) -> None:
        zsh = shutil.which("zsh")
        if not zsh:
            self.skipTest("zsh executable is required for install.sh entrypoint test")

        requested_skills = ["coding-convention"]
        expected_total = _skill_target_count(*requested_skills) + 1

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            (temp_home / ".codex").mkdir()
            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            env.setdefault("TERM", "xterm-256color")

            return_code, output = _run_pty(
                [
                    zsh,
                    "-lc",
                    "./install.sh --platform codex --skip-source-health coding-convention",
                ],
                env=env,
            )

            self.assertEqual(return_code, 0, msg=output)
            self.assertIn("\r  [2/5] Skill sync          [0] [Current], [0] [updated], [0] [newly added]", output)
            self.assertIn(
                f"\r  [2/5] Skill sync          [0] [Current], [0] [updated], [{expected_total}] [newly added]",
                output,
            )
            self.assertEqual(output.count("Ghost-ALICE OS installation Process Report"), 1)

    def test_install_sh_zsh_default_auto_guards_empty_optional_arrays(self) -> None:
        zsh = shutil.which("zsh")
        if not zsh:
            self.skipTest("zsh executable is required for install.sh entrypoint test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            (temp_home / ".codex").mkdir()
            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            env.setdefault("TERM", "xterm-256color")

            return_code, output = _run_pty(
                [
                    zsh,
                    "-lc",
                    "./install.sh --skip-source-health",
                ],
                env=env,
            )

            self.assertEqual(return_code, 0, msg=output)
            self.assertNotIn("ADDON_SOURCES[@]: unbound variable", output)
            self.assertNotIn("ARGS[@]: unbound variable", output)
            self.assertEqual(output.count("Ghost-ALICE OS installation Process Report"), 1)

    def test_install_ps1_default_output_uses_process_report_with_dynamic_counts(self) -> None:
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh executable is required for install.ps1 integration test")

        requested_skills = ["coding-convention"]
        expected_total_targets = _skill_target_count(*requested_skills) + 1

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            codex_home = temp_home / ".codex"
            codex_home.mkdir()
            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["CODEX_HOME"] = codex_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            env["GHOST_ALICE_TEST_SKIP_PWSH_LTS_BASELINE"] = "1"

            result = subprocess.run(
                [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(INSTALL_PS1),
                    "-Platform",
                    "codex",
                    "-SkipSourceHealth",
                    "-Skills",
                    *requested_skills,
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("Ghost-ALICE OS installation Process Report", result.stdout)
            self.assertIn("  Platform: codex", result.stdout)
            self.assertIn(f"  Skills: [{expected_total_targets}] targets", result.stdout)
            self.assertIn("  Hooks: enabled", result.stdout)
            self.assertIn("  Visibility Level: [dynamic]", result.stdout)
            self.assertIn(
                f"  [2/5] Skill sync          [0] [Current], [0] [updated], [{expected_total_targets}] [newly added]",
                result.stdout,
            )
            self.assertIn("  [4/5] Runtime config      codex hooks=true, Visibility Level=[dynamic]", result.stdout)
            self.assertIn("  [5/5] Verification        ok", result.stdout)
            self.assertRegex(result.stdout, r"log: .+\.ghost-alice[/\\]install[/\\]\d{4}-\d{2}-\d{2}-\d{6}\.log")
            self.assertNotIn("\r[2/5] Skill sync [", result.stdout)
            self.assertNotIn("[install_hooks]", result.stdout)

    def test_install_ps1_top_level_ignores_stale_report_env(self) -> None:
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh executable is required for install.ps1 integration test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            codex_home = temp_home / ".codex"
            codex_home.mkdir()
            stale_dir = temp_home / "stale-report"
            stale_dir.mkdir()
            stale_log = stale_dir / "old.log"
            stale_log.write_text("old log\n", encoding="utf-8")

            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["CODEX_HOME"] = codex_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            env["GHOST_ALICE_INSTALL_LOG_FILE"] = stale_log.as_posix()
            env["GHOST_ALICE_INSTALL_EVENT_FILE"] = (stale_dir / "old.events.jsonl").as_posix()
            env.pop("GHOST_ALICE_INSTALL_REPORT_CHILD", None)
            env["GHOST_ALICE_TEST_SKIP_PWSH_LTS_BASELINE"] = "1"

            result = subprocess.run(
                [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(INSTALL_PS1),
                    "-Platform",
                    "codex",
                    "-SkipSourceHealth",
                    "-Skills",
                    "coding-convention",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertNotIn(stale_log.as_posix(), result.stdout)
            self.assertRegex(result.stdout, r"log: .+\.ghost-alice[/\\]install[/\\]\d{4}-\d{2}-\d{2}-\d{6}\.log")

    def test_install_ps1_tty_output_uses_one_line_skill_sync_counter(self) -> None:
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh executable is required for install.ps1 integration test")

        requested_skills = ["coding-convention"]
        expected_total = _skill_target_count(*requested_skills) + 1

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            codex_home = temp_home / ".codex"
            codex_home.mkdir()
            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["CODEX_HOME"] = codex_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            env.setdefault("TERM", "xterm-256color")
            env["GHOST_ALICE_TEST_SKIP_PWSH_LTS_BASELINE"] = "1"

            return_code, output = _run_pty(
                [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(INSTALL_PS1),
                    "-Platform",
                    "codex",
                    "-SkipSourceHealth",
                    "-Skills",
                    *requested_skills,
                ],
                env=env,
            )

            self.assertEqual(return_code, 0, msg=output)
            self.assertIn("\r  [2/5] Skill sync          [0] [Current], [0] [updated], [0] [newly added]", output)
            self.assertIn(
                f"\r  [2/5] Skill sync          [0] [Current], [0] [updated], [{expected_total}] [newly added]",
                output,
            )
            self.assertEqual(output.count("Ghost-ALICE OS installation Process Report"), 1)

    def _write_existing_config(self, home: Path, profile: str) -> Path:
        config_dir = home / ".ghost-alice"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": "ghost-alice-config.v1",
                    "agent_visibility": {"profile": profile},
                    "strict_session_log": {"mode": "always"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return config_path

    def test_install_sh_defaults_visibility_dynamic_over_existing_config_without_flag(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh integration test")

        requested_skills = ["coding-convention"]
        for existing in ("strict", "minimal"):
            with self.subTest(existing=existing):
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_home = Path(temp_dir)
                    (temp_home / ".codex").mkdir()
                    config_path = self._write_existing_config(temp_home, existing)
                    env = os.environ.copy()
                    env["HOME"] = temp_home.as_posix()
                    env["GHOST_ALICE_LANG"] = "en"

                    result = subprocess.run(
                        [bash, str(INSTALL_SH), "--platform", "codex", "--skip-source-health", *requested_skills],
                        cwd=REPO_ROOT,
                        env=env,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )

                    self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
                    self.assertIn("  Visibility Level: [dynamic]", result.stdout)
                    self.assertIn(
                        "  [4/5] Runtime config      codex hooks=true, Visibility Level=[dynamic]",
                        result.stdout,
                    )
                    saved = json.loads(config_path.read_text(encoding="utf-8"))
                    self.assertEqual(saved["agent_visibility"]["profile"], "dynamic")

    def test_install_sh_report_reflects_flag_over_existing_config(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh integration test")

        requested_skills = ["coding-convention"]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            (temp_home / ".codex").mkdir()
            config_path = self._write_existing_config(temp_home, "strict")
            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"

            result = subprocess.run(
                [bash, str(INSTALL_SH), "--platform", "codex", "--visibility", "minimal", "--skip-source-health", *requested_skills],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("  Visibility Level: [minimal]", result.stdout)
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["agent_visibility"]["profile"], "minimal")

    def test_report_sh_resolve_effective_visibility_priority(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for report.sh helper test")

        script = r"""
set -euo pipefail
SCRIPT_DIR="$PWD"
source installer_lib/python_runtime.sh
source installer_lib/report.sh
mkdir -p "$HOME/.ghost-alice"
printf '{"schema_version":"ghost-alice-config.v1","agent_visibility":{"profile":"minimal"},"strict_session_log":{"mode":"always"}}\n' > "$HOME/.ghost-alice/config.json"
AGENT_VISIBILITY=""
printf 'existing=%s\n' "$(resolve_effective_visibility)"
AGENT_VISIBILITY="strict"
printf 'flag=%s\n' "$(resolve_effective_visibility)"
rm "$HOME/.ghost-alice/config.json"
AGENT_VISIBILITY=""
printf 'default=%s\n' "$(resolve_effective_visibility)"
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["HOME"] = temp_dir
            result = subprocess.run(
                [bash, "-c", script],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("existing=dynamic", result.stdout)
        self.assertIn("flag=strict", result.stdout)
        self.assertIn("default=dynamic", result.stdout)

    def test_report_ps1_resolve_effective_visibility_priority(self) -> None:
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh executable is required for report.ps1 helper test")

        script = r"""
$ErrorActionPreference = "Stop"
$script:GhostAliceRoot = (Get-Location).Path
. (Join-Path $script:GhostAliceRoot "installer_lib/python_runtime.ps1")
. (Join-Path $script:GhostAliceRoot "installer_lib/report.ps1")
$configDir = Join-Path $env:HOME ".ghost-alice"
New-Item -ItemType Directory -Path $configDir -Force | Out-Null
$configPath = Join-Path $configDir "config.json"
'{"schema_version":"ghost-alice-config.v1","agent_visibility":{"profile":"minimal"},"strict_session_log":{"mode":"always"}}' | Set-Content -LiteralPath $configPath -Encoding UTF8
"existing=$(Resolve-EffectiveVisibility -Flag '')"
"flag=$(Resolve-EffectiveVisibility -Flag 'strict')"
Remove-Item -LiteralPath $configPath -Force
"default=$(Resolve-EffectiveVisibility -Flag '')"
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["HOME"] = temp_dir
            result = subprocess.run(
                [pwsh, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("existing=dynamic", result.stdout)
        self.assertIn("flag=strict", result.stdout)
        self.assertIn("default=dynamic", result.stdout)

    def test_install_ps1_defaults_visibility_dynamic_over_existing_config_without_flag(self) -> None:
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh executable is required for install.ps1 integration test")

        requested_skills = ["coding-convention"]
        for existing in ("strict", "minimal"):
            with self.subTest(existing=existing):
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_home = Path(temp_dir)
                    codex_home = temp_home / ".codex"
                    codex_home.mkdir()
                    config_path = self._write_existing_config(temp_home, existing)
                    env = os.environ.copy()
                    env["HOME"] = temp_home.as_posix()
                    env["CODEX_HOME"] = codex_home.as_posix()
                    env["GHOST_ALICE_LANG"] = "en"
                    env["GHOST_ALICE_TEST_SKIP_PWSH_LTS_BASELINE"] = "1"

                    result = subprocess.run(
                        [
                            pwsh,
                            "-NoLogo",
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(INSTALL_PS1),
                            "-Platform",
                            "codex",
                            "-SkipSourceHealth",
                            "-Skills",
                            *requested_skills,
                        ],
                        cwd=REPO_ROOT,
                        env=env,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )

                    self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
                    self.assertIn("  Visibility Level: [dynamic]", result.stdout)
                    self.assertIn(
                        "  [4/5] Runtime config      codex hooks=true, Visibility Level=[dynamic]",
                        result.stdout,
                    )
                    saved = json.loads(config_path.read_text(encoding="utf-8"))
                    self.assertEqual(saved["agent_visibility"]["profile"], "dynamic")


if __name__ == "__main__":
    unittest.main()
