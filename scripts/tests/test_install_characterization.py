"""End-to-end install/uninstall characterization (golden-master) tests.

These tests assert the recorded install.sh/install.ps1 behavior. Changes to
the fixture require evidence that the intended installer contract changed.

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

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "_shared"))
from installer_testing import checkout_copy  # noqa: E402

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


def _run_install(source: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the install.sh of ``source`` (a checkout copy) with a deterministic, offline, non-interactive environment.

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

    return subprocess.run(
        [bash_exe, str(source / "install.sh"), *args],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


def _run_uninstall(source: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run install.sh in uninstall mode with the same deterministic environment."""

    return _run_install(source, home, "--uninstall", *args)


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

        source = checkout_copy(self)
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            result = _run_install(
                source, home, "--platform", "claude", "--skip-source-health", "task-router"
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

            settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
            prompts = json.dumps(settings["hooks"]["UserPromptSubmit"])
            self.assertNotIn("pending-merge", prompts)
            self.assertIn("session-intent", prompts)
            self.assertNotIn("web-search-first", prompts)
            snapshot = _snapshot_settings(home)
            expected = json.loads(SETTINGS_SHAPE_FIXTURE.read_text(encoding="utf-8"))
            if os.name == "nt" and snapshot["installed_markers"] and not expected["installed_markers"]:
                self.assertEqual(snapshot["hook_events"], expected["hook_events"])
                self.assertTrue(
                    all(marker == ".ghost-alice-install.json" for marker in snapshot["installed_markers"])
                )
                return
            self.assertEqual(snapshot, expected)

    def test_uninstall_preserves_user_file_and_removes_markers(self) -> None:
        """Assert install/uninstall cleanup behavior.

        Non-managed user files survive full uninstall and managed ownership
        markers are removed from the selected skill root.
        """

        source = checkout_copy(self)
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            skills_dir = home / ".claude" / "skills"
            skills_dir.mkdir(parents=True)
            user_file = skills_dir / "user-note.txt"
            user_file.write_text("keep me", encoding="utf-8")

            install_result = _run_install(
                source, home, "--platform", "claude", "--skip-source-health", "task-router"
            )
            self.assertEqual(
                install_result.returncode,
                0,
                msg=install_result.stderr + install_result.stdout,
            )

            uninstall_result = _run_uninstall(source, home, "--platform", "claude")
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
