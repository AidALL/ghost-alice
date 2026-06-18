"""TDD for plan Phase 4 (addon-hook-manifest-parsing + observational allow-list +
block-addon-control-flow-hooks + hook-script-containment): addon hooks[] parse
into AddonTarget.hooks, only observational events are permitted, control-flow
events are rejected fail-closed, and the script is containment-checked.

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_addon_hooks.py -q
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import addon_installer as ai  # noqa: E402


def _make_addon(tmp: Path, hooks, *, addon_id="hookaddon") -> Path:
    """Write a minimal addon source tree with the given hooks[] list."""
    root = tmp / "src"
    addon = root / "addons" / addon_id
    (addon / "skill").mkdir(parents=True)
    (addon / "skill" / "SKILL.md").write_text(
        "---\nname: hookskill\ndescription: Use when testing hook parse.\n---\n# x\n", encoding="utf-8")
    (addon / "hooks").mkdir(parents=True, exist_ok=True)
    (addon / "hooks" / "obs.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    (root / "addons-manifest.json").write_text(json.dumps({
        "manifest_version": 1,
        "addons": [{"id": addon_id, "path": f"addons/{addon_id}", "min_core_version": "0.1.0", "tags": []}],
    }), encoding="utf-8")
    (addon / "addon.json").write_text(json.dumps({
        "addon_version": "0.1.0", "addon_id": addon_id,
        "skills": [{"name": f"{addon_id}-skill", "source": "skill", "skill_dir": "skill"}],
        "hooks": hooks,
        "platforms": ["claude", "codex"], "depends_on_core": [], "secrets": [],
    }), encoding="utf-8")
    return root


class AddonHookParseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_observational_hook_parses_onto_target(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        targets = ai.load_addon_targets([src], platform="claude")
        target = next(t for t in targets if t.addon_id == "hookaddon")
        self.assertEqual([(h[0], h[1]) for h in target.hooks], [("obs", "post_tool_use")])
        self.assertTrue(target.hooks[0][2].replace("\\", "/").endswith("hooks/obs.py"))

    def test_session_start_is_allowed(self):
        src = _make_addon(self.tmp, [{"id": "ss", "event": "on_session_start", "script": "hooks/obs.py"}])
        targets = ai.load_addon_targets([src], platform="claude")
        self.assertEqual(targets[0].hooks[0][1], "on_session_start")

    def test_missing_hooks_defaults_empty(self):
        noop = REPO_ROOT / "_shared" / "tests" / "fixtures" / "dummy-addon"
        self.assertEqual(ai.load_addon_targets([noop], platform="claude")[0].hooks, ())

    def _assert_rejected(self, hooks, needle):
        src = _make_addon(self.tmp, hooks)
        with self.assertRaises(ai.AddonManifestError) as ctx:
            ai.load_addon_targets([src], platform="claude")
        self.assertIn(needle, str(ctx.exception).lower())

    def test_stop_event_rejected(self):
        self._assert_rejected([{"id": "s", "event": "on_agent_stop", "script": "hooks/obs.py"}], "hook")

    def test_user_prompt_event_rejected(self):
        self._assert_rejected([{"id": "u", "event": "on_user_prompt", "script": "hooks/obs.py"}], "hook")

    def test_pre_tool_use_event_rejected(self):
        # PreToolUse can BLOCK a tool -> not observational -> rejected by the allowlist
        self._assert_rejected([{"id": "p", "event": "pre_tool_use", "script": "hooks/obs.py"}], "hook")

    def test_raw_concrete_event_name_rejected(self):
        # only semantic intents are accepted; a raw "Stop"/"UserPromptSubmit" is rejected
        self._assert_rejected([{"id": "s", "event": "Stop", "script": "hooks/obs.py"}], "hook")

    def test_escaping_script_rejected(self):
        self._assert_rejected([{"id": "e", "event": "post_tool_use", "script": "../../../etc/passwd"}], "")

    def test_absolute_script_rejected(self):
        self._assert_rejected([{"id": "a", "event": "post_tool_use", "script": "/etc/passwd"}], "")

    def test_core_id_collision_rejected(self):
        # an addon hook id colliding with a reserved core hook id is rejected
        self._assert_rejected([{"id": "io-trace", "event": "post_tool_use", "script": "hooks/obs.py"}], "")

    def test_duplicate_hook_id_rejected(self):
        self._assert_rejected([
            {"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"},
            {"id": "obs", "event": "on_session_start", "script": "hooks/obs.py"},
        ], "")


if __name__ == "__main__":
    unittest.main()
