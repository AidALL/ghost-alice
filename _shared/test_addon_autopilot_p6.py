"""TDD for Phase P6: official autopilot privileged-adapter addon.

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_addon_autopilot_p6.py -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import addon_installer as ai  # noqa: E402
import autopilot_state as aps  # noqa: E402
import install_hooks  # noqa: E402


AUTOPILOT_SOURCE = REPO_ROOT / "skill-catalog" / "addons" / "autopilot-mode"


def _work_item(item_id: str) -> dict:
    return {
        "id": item_id,
        "status": "ready",
        "focus_layer": "meso",
        "depends_on": [],
        "prompt": f"Do {item_id}",
        "acceptance_criteria": [f"{item_id}-ac"],
        "allowed_surface": ["_shared/..."],
        "completion": {
            "state": "not_started",
            "verdict": None,
            "evidence": [],
            "completion_check_digest": None,
            "reopen_target": None,
        },
        "attempt": 0,
    }


def _write_approved_run(run_dir: Path, items: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "approved-run.json").write_text(
        json.dumps({
            "schema_version": "autopilot-run.v1",
            "run_id": "run-1",
            "approved": True,
            "status": "running",
            "scope": {"summary": "P6 autopilot adapter test run"},
            "budget": {"remaining_steps": 3},
            "allowed_surfaces": ["_shared/..."],
            "stop_conditions": ["budget_exhausted", "user_stop"],
            "approval_evidence": {"decision": "GO", "source": "unit-test"},
        }),
        encoding="utf-8",
    )
    aps.write_work_items(run_dir / "tasks.jsonl", items)


class OfficialAutopilotAddonTest(unittest.TestCase):
    def test_official_autopilot_addon_resolves_privileged_adapter_from_core_data(self):
        targets = ai.load_addon_targets([AUTOPILOT_SOURCE], platform="claude")

        self.assertEqual(len(targets), 1)
        target = targets[0]
        self.assertEqual(target.addon_id, "autopilot-mode")
        self.assertEqual(target.name, "autopilot-mode")
        self.assertEqual(target.privileged_adapters, ("autopilot-mode",))

        specs = ai.iter_privileged_adapter_hook_specs(targets)
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["event"], "on_agent_stop")
        self.assertEqual(specs[0]["marker"], "[adapter:autopilot-mode] continue")
        self.assertEqual(specs[0]["runner_id"], "adapter-autopilot-mode-continue")
        self.assertTrue(Path(specs[0]["script"]).is_file())

    def test_official_autopilot_adapter_hook_installs_and_full_uninstall_removes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude = root / ".claude"
            claude.mkdir(parents=True)
            old_env = {key: os.environ.get(key) for key in ("HOME", "CLAUDE_CONFIG_DIR")}
            try:
                os.environ["HOME"] = str(root)
                os.environ["CLAUDE_CONFIG_DIR"] = str(claude)

                self.assertEqual(
                    install_hooks.install_hook("claude", addon_sources=[str(AUTOPILOT_SOURCE)]),
                    "installed",
                )
                installed = self._commands(claude)
                self.assertTrue(any("[adapter:autopilot-mode] continue" in c for c in installed))
                self.assertTrue(any("[hook-runner:adapter-autopilot-mode-continue]" in c for c in installed))

                install_hooks.uninstall_hook("claude")

                removed = self._commands(claude)
                self.assertFalse(any("[adapter:autopilot-mode] continue" in c for c in removed))
            finally:
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def _commands(self, claude: Path) -> list[str]:
        settings = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
        return [
            hook.get("command", "")
            for event in settings.get("hooks", {}).values()
            if isinstance(event, list)
            for entry in event
            for hook in entry.get("hooks", [])
        ]

    def test_adapter_script_consumes_approved_run_dir_without_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            _write_approved_run(run_dir, [_work_item("next")])
            script = (
                AUTOPILOT_SOURCE
                / "addons"
                / "autopilot-mode"
                / "skill"
                / "adapters"
                / "autopilot_mode.py"
            )
            env = os.environ.copy()
            env["GHOST_ALICE_AUTOPILOT_RUN_DIR"] = str(run_dir)

            result = subprocess.run(
                [sys.executable, str(script)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["continue"])
        self.assertIn("work-item: next", payload["systemMessage"])
        self.assertIn("Do next", payload["systemMessage"])


if __name__ == "__main__":
    unittest.main()
