"""End-to-end install/uninstall characterization (golden-master) tests.

These tests PIN CURRENT BEHAVIOR of the Ghost-ALICE installer as a safety net
for an upcoming behavior-preserving refactor of install.sh / install.ps1. They
intentionally capture whatever the installer does today (including any existing
quirks) and must NOT be used to "fix" behavior.

Determinism notes (read before editing the golden fixture):
- The installer writes <HOME>/.claude/settings.json hooks for every install,
  regardless of how individual skills are materialized, so the per-event hook
  entry counts are environment-independent.
- ``.ghost-alice-install.json`` ownership markers are only written for skills
  installed in copy / copy-fallback mode (see
  ``_shared/installer_assets_cli.py``: ``MARKER_INSTALL_MODES``). On a
  filesystem that supports symlinks (the default for macOS dev hosts and Linux
  CI) skills are symlinked and therefore carry no markers, so
  ``installed_markers`` is the empty list. The snapshot helper still discovers
  real markers structurally, so the golden master stays correct if a future
  environment falls back to copy mode -- but the committed fixture reflects the
  symlink-capable baseline.
- The snapshot deliberately records STRUCTURE ONLY: never absolute paths,
  command strings, timestamps, PIDs, or session ids (all of which vary per run).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
FIXTURE_DIR = Path(__file__).resolve().parent / "_fixtures" / "characterization"
SETTINGS_SHAPE_FIXTURE = FIXTURE_DIR / "install_settings_shape.json"


def _find_test_bash() -> str | None:
    """Resolve a non-WSL bash executable, mirroring the existing installer tests.

    Returns ``None`` when only a Windows/WSL bash shim is available so callers
    can ``skipTest`` instead of exercising an incompatible interpreter.
    """

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


def _run_install(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run install.sh with a deterministic, offline, non-interactive environment.

    ``HOME`` is pinned to ``home`` and progress animation is disabled so output
    is stable. ``--skip-source-health`` (passed by callers) disables all
    git/network access, which is required for determinism.
    """

    bash_exe = _find_test_bash()
    if not bash_exe:
        raise unittest.SkipTest(
            "No non-WSL bash executable available for install.sh characterization test"
        )

    env = os.environ.copy()
    env["HOME"] = home.as_posix()
    env["GHOST_ALICE_INSTALL_PROGRESS"] = "off"

    with _preserve_source_repo_hookspath():
        return subprocess.run(
            [bash_exe, str(INSTALL_SH), *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )


def _run_uninstall(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run install.sh in uninstall mode with the same deterministic environment."""

    return _run_install(home, "--uninstall", *args)


@contextlib.contextmanager
def _preserve_source_repo_hookspath() -> Iterator[None]:
    """Snapshot and restore the source repo's ``core.hooksPath`` git config.

    The uninstall path resolves the *source* repository from the installer's
    location (this checkout) and, as part of cleanup, removes/restores its
    ``core.hooksPath``. That is the behavior under characterization, but it
    mutates the live worktree git config -- shared state that other installer
    tests depend on. To avoid cross-test contamination we capture the current
    value and restore it verbatim after the uninstall runs, without changing
    what the installer actually does during the test.
    """

    def _read() -> str | None:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "config", "--local", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    previous = _read()
    try:
        yield
    finally:
        if previous is None:
            subprocess.run(
                ["git", "-C", str(REPO_ROOT), "config", "--local", "--unset", "core.hooksPath"],
                capture_output=True,
                text=True,
            )
        else:
            subprocess.run(
                ["git", "-C", str(REPO_ROOT), "config", "--local", "core.hooksPath", previous],
                capture_output=True,
                text=True,
            )


def _markers_under_skills(home: Path) -> list[str]:
    """Return basenames of every ownership marker under ``<home>/.claude/skills``."""

    skills_dir = home / ".claude" / "skills"
    return sorted(
        marker.name for marker in skills_dir.rglob(".ghost-alice-install.json")
    )


def _snapshot_settings(home: Path) -> dict[str, dict[str, int] | list[str]]:
    """Return an environment-independent STRUCTURAL snapshot of an install.

    Captures only shape:
    - ``hook_events``: ``{event_name: number_of_entries}`` for each hooks event
      in ``<home>/.claude/settings.json``.
    - ``installed_markers``: sorted basenames of every ``.ghost-alice-install.json``
      ownership marker found under ``<home>/.claude/skills``.

    No absolute paths, command strings, timestamps, PIDs, or session ids are
    included; those vary per run and would make the golden master flaky.
    """

    settings_path = home / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    hooks = settings.get("hooks", {})
    hook_events = {event: len(entries) for event, entries in hooks.items()}

    return {
        "hook_events": hook_events,
        "installed_markers": _markers_under_skills(home),
    }


class InstallCharacterizationTest(unittest.TestCase):
    def test_fresh_claude_install_matches_golden_settings_shape(self) -> None:
        if not SETTINGS_SHAPE_FIXTURE.exists():
            self.fail(
                "Golden fixture missing: "
                f"{SETTINGS_SHAPE_FIXTURE}. Generate it from a real install run "
                "and commit it (see module docstring)."
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            result = _run_install(
                home, "--platform", "claude", "--skip-source-health", "task-router"
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

            snapshot = _snapshot_settings(home)
            expected = json.loads(SETTINGS_SHAPE_FIXTURE.read_text(encoding="utf-8"))
            self.assertEqual(snapshot, expected)

    def test_uninstall_preserves_user_file_and_removes_markers(self) -> None:
        """Pin CURRENT install->uninstall cleanup behavior.

        A non-managed user file placed directly under ``<home>/.claude/skills``
        must survive a full uninstall, and no ``.ghost-alice-install.json``
        ownership markers may remain underneath afterwards.

        This characterizes today's behavior only. Per the refactor plan it must
        NOT be changed to assert the user-modified-skill backup finding is fixed;
        it merely records what the installer does now.
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            skills_dir = home / ".claude" / "skills"
            skills_dir.mkdir(parents=True)
            user_file = skills_dir / "user-note.txt"
            user_file.write_text("keep me", encoding="utf-8")

            install_result = _run_install(
                home, "--platform", "claude", "--skip-source-health", "task-router"
            )
            self.assertEqual(
                install_result.returncode,
                0,
                msg=install_result.stderr + install_result.stdout,
            )

            uninstall_result = _run_uninstall(home, "--platform", "claude")
            self.assertEqual(
                uninstall_result.returncode,
                0,
                msg=uninstall_result.stderr + uninstall_result.stdout,
            )

            self.assertTrue(
                user_file.exists(),
                msg="non-managed user file was removed by uninstall",
            )
            self.assertEqual(user_file.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(_markers_under_skills(home), [])


if __name__ == "__main__":
    unittest.main()
