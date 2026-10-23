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


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
README = REPO_ROOT / "README.md"
ADDON_FIXTURE = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"
if str(REPO_ROOT / "_shared") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "_shared"))
from installer_testing import checkout_copy  # noqa: E402


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


def _run_pty(command: list[str], *, env: dict[str, str], cwd: Path) -> tuple[int, str]:
    if not hasattr(os, "openpty"):
        raise unittest.SkipTest("pseudo-terminal support is required for TTY progress test")

    import select

    master_fd, slave_fd = os.openpty()
    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
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
        if return_code:
            # Compact installers retain child failures in the disposable HOME.
            # Keep those diagnostics before TemporaryDirectory removes them.
            for log in sorted((Path(env["HOME"]) / ".ghost-alice" / "install").glob("*.log")):
                details = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
                chunks.append(f"\nInstaller details ({log.name}):\n{details}\n".encode())
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
        if "Target operations" in clean or "Sync [" in clean or "Common targets" in clean:
            lines.append(clean)
    return lines


class InstallerDefaultAutoTest(unittest.TestCase):
    def test_auto_install_messages_explain_distinct_platform_targets(self) -> None:
        auto_py = (REPO_ROOT / "_shared" / "installer" / "auto.py").read_text(encoding="utf-8")

        self.assertIn("separate install path", auto_py)
        self.assertIn("not a duplicate install", auto_py)
        self.assertIn("[auto] ({index}/{total})", auto_py)

    def test_readme_primary_usage_has_no_auto_flag(self) -> None:
        readme = README.read_text(encoding="utf-8")
        install_sh = INSTALL_SH.read_text(encoding="utf-8")
        install_ps1 = INSTALL_PS1.read_text(encoding="utf-8-sig")

        first_command = readme.split("```bash\n", 1)[1].split("```", 1)[0].strip()
        self.assertEqual(first_command, "bash install.sh")
        self.assertIn("./docs/getting-started/installation.md", readme)
        self.assertNotIn("```powershell\n.\\install.ps1\n```", readme)
        self.assertNotIn("bash install.sh --auto", readme)
        self.assertNotIn(".\\install.ps1 -Auto", readme)
        self.assertNotIn("install.cmd -Auto", readme)
        self.assertNotIn("--allow-dirty-source", readme)
        self.assertNotIn("--allow-dirty-source", install_sh)
        self.assertNotIn("AllowDirtySource", install_ps1)

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

            source = checkout_copy(self)
            result = subprocess.run(
                [bash, str(source / "install.sh"), "--verbose", "--skip-source-health", "task-router"],
                cwd=source,
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

            source = checkout_copy(self)
            return_code, output = _run_pty(
                [bash, str(source / "install.sh"), "--skip-source-health", "task-router"],
                env=env,
                cwd=source,
            )

            expected_common_targets = _skill_target_count("task-router") + 1
            skill_sync_line = f"  [2/5] Skill sync          [{expected_common_targets}] common targets"
            initial_line = (
                f"        Common targets      [------------------------------] "
                f"[0/{expected_common_targets}] common targets synced"
            )
            final_line = (
                f"\r\x1b[2K        Common targets      [##############################] "
                f"[{expected_common_targets}/{expected_common_targets}] "
                "common targets synced on all platforms"
            )
            self.assertEqual(return_code, 0, msg=output)
            self.assertEqual(output.count("Ghost-ALICE OS installation Process Report"), 1)
            self.assertIn(f"  Skills: [{expected_common_targets}] common targets", output)
            self.assertIn(skill_sync_line, output)
            self.assertIn(initial_line, output)
            self.assertNotIn("  [3/5] Hooks               pending", output)
            self.assertNotIn("\x1b[11A\r", output)
            self.assertNotIn("\x1b[1B\r", output)
            self.assertNotIn("        Sync [", output)
            self.assertIn(final_line, output)
            live_lines = _printable_live_progress_lines(output)
            self.assertTrue(live_lines, msg=output)
            self.assertLess(output.index(initial_line), output.rindex(final_line))
            self.assertIn("  [5/5] Verification        ok", output)

    def test_install_sh_auto_addon_report_counts_addon_common_target(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh addon auto-detect test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            (temp_home / ".claude").mkdir()
            (temp_home / ".codex").mkdir()

            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "0"

            source = checkout_copy(self)
            result = subprocess.run(
                [bash, str(source / "install.sh"), "--addon-source", ADDON_FIXTURE.as_posix(), "--skip-source-health"],
                cwd=source,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            output = result.stderr + result.stdout
            self.assertEqual(result.returncode, 0, msg=output)
            self.assertIn("  Platform: claude, codex", result.stdout)
            self.assertIn("  Skills: [25] common targets", result.stdout)
            self.assertIn("[25/25] common targets synced on all platforms", result.stdout)
            self.assertNotIn("[25/24]", result.stdout)

    def test_install_sh_auto_detect_accepts_git_addon_tag_after_parent_prepares_source(self) -> None:
        bash = _find_test_bash()
        if not bash:
            self.skipTest("bash executable is required for install.sh addon auto-detect test")
        if not shutil.which("git"):
            self.skipTest("git executable is required for tagged addon source test")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            temp_home = temp_root / "home"
            temp_home.mkdir()
            (temp_home / ".claude").mkdir()
            (temp_home / ".codex").mkdir()
            addon_repo = temp_root / "addon-repo"
            shutil.copytree(ADDON_FIXTURE, addon_repo)
            for command in (
                ["git", "init"],
                ["git", "add", "."],
                ["git", "-c", "user.name=Ghost ALICE Test", "-c", "user.email=test@example.com", "commit", "-m", "fixture"],
                ["git", "tag", "v1"],
            ):
                subprocess.run(
                    command,
                    cwd=addon_repo,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

            env = os.environ.copy()
            env["HOME"] = temp_home.as_posix()
            env["GHOST_ALICE_LANG"] = "en"
            env["GHOST_ALICE_INSTALL_PROGRESS"] = "0"

            source = checkout_copy(self)
            result = subprocess.run(
                [
                    bash,
                    str(source / "install.sh"),
                    "--addon-source",
                    addon_repo.resolve().as_uri(),
                    "--addon-tag",
                    "v1",
                    "--skip-source-health",
                    "task-router",
                ],
                cwd=source,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            output = result.stderr + result.stdout
            self.assertEqual(result.returncode, 0, msg=output)
            self.assertIn("  Platform: claude, codex", result.stdout)
            self.assertIn("common targets synced on all platforms", result.stdout)
            self.assertNotIn("--addon-tag can only be used with git URL addon sources", output)

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

            source = checkout_copy(self)
            result = subprocess.run(
                [bash, str(source / "install.sh"), "--platform", "codex", "--skip-source-health", *requested_skills],
                cwd=source,
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
                "  [3/5] Hooks               session-intent, tool-checkpoint, completion, session-start, session-receipt, io-trace enabled",
                result.stdout,
            )
            self.assertIn("  [4/5] Runtime config      codex hooks=true, Visibility Level=[dynamic]", result.stdout)
            self.assertIn("  [5/5] Verification        ok", result.stdout)
            # Codex has no /visibility command; only the Claude tail names it.
            self.assertNotIn("/visibility", result.stdout)
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

            source = checkout_copy(self)
            result = subprocess.run(
                [bash, str(source / "install.sh"), "--platform", "codex", "--skip-source-health", "coding-convention"],
                cwd=source,
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

            source = checkout_copy(self)
            return_code, output = _run_pty(
                [bash, str(source / "install.sh"), "--platform", "codex", "--skip-source-health", *requested_skills],
                env=env,
                cwd=source,
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

            source = checkout_copy(self)
            return_code, output = _run_pty(
                [
                    zsh,
                    "-lc",
                    "./install.sh --platform codex --skip-source-health coding-convention",
                ],
                env=env,
                cwd=source,
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

            source = checkout_copy(self)
            return_code, output = _run_pty(
                [
                    zsh,
                    "-lc",
                    "./install.sh --skip-source-health",
                ],
                env=env,
                cwd=source,
            )

            self.assertEqual(return_code, 0, msg=output)
            self.assertNotIn("ADDON_SOURCES[@]: unbound variable", output)
            self.assertNotIn("ARGS[@]: unbound variable", output)
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

                    source = checkout_copy(self)
                    result = subprocess.run(
                        [bash, str(source / "install.sh"), "--platform", "codex", "--skip-source-health", *requested_skills],
                        cwd=source,
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

            source = checkout_copy(self)
            result = subprocess.run(
                [bash, str(source / "install.sh"), "--platform", "codex", "--visibility", "minimal", "--skip-source-health", *requested_skills],
                cwd=source,
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


if __name__ == "__main__":
    unittest.main()
