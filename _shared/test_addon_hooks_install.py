"""TDD for plan Phase 4 (addon-hook-ordering + core-only-snapshot-compatibility +
addon-hook-runner-namespace): install_hook appends observational addon hook
entries AFTER the core suite, the inner command passes the hook-runner allowlist,
and a core-only install stays byte-identical.

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_addon_hooks_install.py -q
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import install_hooks  # noqa: E402
import hook_profile_gate  # noqa: E402
from test_addon_hooks import _make_addon  # noqa: E402


def _make_autopilot_adapter_addon(tmp: Path) -> Path:
    src = tmp / "autopilot-src"
    addon = src / "addons" / "autopilot-mode"
    skill = addon / "skill"
    (skill / "adapters").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: autopilot-mode\ndescription: Use when testing adapter install.\n---\n# x\n",
        encoding="utf-8",
    )
    (skill / "adapters" / "autopilot_mode.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    (addon / "addon.json").write_text(json.dumps({
        "addon_version": "0.1.0",
        "addon_id": "autopilot-mode",
        "skills": [{"name": "autopilot-mode", "source": "skill", "skill_dir": "skill"}],
        "privileged_adapters": ["autopilot-mode"],
        "platforms": ["claude", "codex"],
        "depends_on_core": [],
        "secrets": [],
    }), encoding="utf-8")
    (src / "addons-manifest.json").write_text(json.dumps({
        "manifest_version": 1,
        "addons": [{"id": "autopilot-mode", "path": "addons/autopilot-mode",
                    "min_core_version": "0.1.0", "tags": []}],
    }), encoding="utf-8")
    return src


class AddonHookInstallTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.claude = self.tmp / ".claude"
        self.claude.mkdir(parents=True)
        self._env = {k: os.environ.get(k) for k in ("HOME", "CLAUDE_CONFIG_DIR")}
        os.environ["HOME"] = str(self.tmp)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _settings(self):
        return json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))

    def _addon_entries(self, event, marker="[addon:hookaddon] obs"):
        return [h["command"] for e in self._settings().get("hooks", {}).get(event, [])
                for h in e.get("hooks", []) if marker in h.get("command", "")]

    def test_addon_hook_added_after_core_io_trace(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        self.assertEqual(install_hooks.install_hook("claude", addon_sources=[str(src)]), "installed")
        post = self._settings()["hooks"]["PostToolUse"]
        cmds = [h["command"] for e in post for h in e.get("hooks", [])]
        addon_idx = next(i for i, c in enumerate(cmds) if "[addon:hookaddon] obs" in c)
        io_idx = next(i for i, c in enumerate(cmds) if "[io-trace]" in c)
        self.assertGreater(addon_idx, io_idx)  # addon AFTER core io-trace (ordering)

    def test_session_start_addon_hook_lands_on_sessionstart(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "on_session_start", "script": "hooks/obs.py"}])
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        self.assertEqual(len(self._addon_entries("SessionStart")), 1)
        self.assertEqual(len(self._addon_entries("PostToolUse")), 0)

    def test_inner_command_passes_runner_allowlist(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        cmd = self._addon_entries("PostToolUse")[0]
        # command ends (before the " # <marker>" comment) with: "run" "<id>" "<b64payload>"
        cmd_part = cmd.split(" # ")[0].rstrip()
        payload = cmd_part.rsplit('"', 2)[1]
        inner = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
        argv = hook_profile_gate._validate_shell_command(inner)  # raises if outside allowlist
        self.assertTrue(argv[0].endswith("python") or "python" in argv[0])
        self.assertTrue(argv[-1].endswith("hooks/obs.py"))

    def test_idempotent_reinstall_no_duplicate(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        second = install_hooks.install_hook("claude", addon_sources=[str(src)])
        self.assertEqual(second, "already")
        self.assertEqual(len(self._addon_entries("PostToolUse")), 1)  # no duplicate

    def test_core_only_install_has_no_addon_entries(self):
        install_hooks.install_hook("claude", addon_sources=[])
        text = (self.claude / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("[addon:", text)

    def test_core_reinstall_preserves_addon_hook(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        self.assertEqual(len(self._addon_entries("PostToolUse")), 1)
        # a later core-only reinstall (no --addon-source) must NOT drop the addon hook
        install_hooks.install_hook("claude", addon_sources=[])
        self.assertEqual(len(self._addon_entries("PostToolUse")), 1)

    def test_addon_hook_removed_by_remove_addon_hook(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        removed = install_hooks.remove_addon_hook("[addon:hookaddon] obs", platform_key="claude")
        self.assertEqual(removed, 1)
        self.assertEqual(len(self._addon_entries("PostToolUse")), 0)
        # core hooks untouched
        self.assertTrue(any("[io-trace]" in h["command"]
                            for e in self._settings()["hooks"]["PostToolUse"] for h in e.get("hooks", [])))

    def test_addon_install_preserves_unmanaged_marker_substring_hook(self):
        # A user hook whose command merely CONTAINS the addon marker substring,
        # but carries no [hook-runner:] ownership proof, must survive the addon
        # stale-prune at install time (parity with remove_addon_hook's exact
        # ownership predicate). Substring-only pruning would delete user data.
        user_cmd = "echo audit [addon:hookaddon] obs run >> ~/mylog"
        (self.claude / "settings.json").write_text(json.dumps({
            "hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": user_cmd}]}]}
        }), encoding="utf-8")
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        commands = [h.get("command", "") for e in self._settings()["hooks"]["PostToolUse"]
                    for h in e.get("hooks", [])]
        self.assertIn(user_cmd, commands)  # unmanaged user hook preserved, not pruned
        managed = [c for c in commands if "[addon:hookaddon] obs" in c and "[hook-runner:" in c]
        self.assertEqual(len(managed), 1)  # exactly one managed addon hook added

    def test_two_addons_prefix_hook_ids_do_not_collide(self):
        # hook ids 'obs' and 'obs2' must not cross-remove (substring boundary).
        src = self.tmp / "src"
        a = src / "addons" / "aone"
        b = src / "addons" / "atwo"
        for d, aid, hid in ((a, "aone", "obs"), (b, "atwo", "obs2")):
            (d / "skill").mkdir(parents=True)
            (d / "skill" / "SKILL.md").write_text("---\nname: s\ndescription: Use when x.\n---\n#x\n", encoding="utf-8")
            (d / "hooks").mkdir(parents=True)
            (d / "hooks" / "h.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
            (d / "addon.json").write_text(json.dumps({
                "addon_version": "0.1.0", "addon_id": aid,
                "skills": [{"name": f"{aid}-s", "source": "skill", "skill_dir": "skill"}],
                "hooks": [{"id": hid, "event": "post_tool_use", "script": "hooks/h.py"}],
                "platforms": ["claude"], "depends_on_core": [], "secrets": []}), encoding="utf-8")
        (src / "addons-manifest.json").write_text(json.dumps({"manifest_version": 1, "addons": [
            {"id": "aone", "path": "addons/aone", "min_core_version": "0.1.0", "tags": []},
            {"id": "atwo", "path": "addons/atwo", "min_core_version": "0.1.0", "tags": []}]}), encoding="utf-8")
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        self.assertEqual(len(self._addon_entries("PostToolUse", "[addon:aone] obs")), 1)
        self.assertEqual(len(self._addon_entries("PostToolUse", "[addon:atwo] obs2")), 1)

    def test_autopilot_adapter_install_prunes_legacy_claude_autopilot_hooks(self):
        legacy_reset = (
            "/usr/bin/python3 /Users/example/.claude/skills/autopilot-mode/scripts/"
            "reset_inject_count.py # [autopilot] reset-count"
        )
        legacy_stop = (
            "/usr/bin/python3 /Users/example/.claude/skills/autopilot-mode/scripts/"
            "autopilot_stop_hook.py --platform claude # [autopilot] stop-inject"
        )
        user_audit = "echo user audit '[autopilot] stop-inject' >> ~/audit.log"
        (self.claude / "settings.json").write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [{"matcher": "", "hooks": [
                    {"type": "command", "command": legacy_reset},
                ]}],
                "Stop": [{"matcher": "", "hooks": [
                    {"type": "command", "command": legacy_stop},
                    {"type": "command", "command": user_audit},
                ]}],
            }
        }), encoding="utf-8")

        src = _make_autopilot_adapter_addon(self.tmp)
        install_hooks.install_hook("claude", addon_sources=[str(src)])

        commands = [
            h.get("command", "")
            for ev in self._settings()["hooks"].values() if isinstance(ev, list)
            for e in ev for h in e.get("hooks", [])
        ]
        self.assertFalse(any("reset_inject_count.py" in command for command in commands))
        self.assertFalse(any("autopilot_stop_hook.py" in command for command in commands))
        self.assertTrue(any(user_audit == command for command in commands))
        self.assertTrue(any("[adapter:autopilot-mode] continue" in command for command in commands))


class AddonHookSecurityTest(AddonHookInstallTest):
    def _core_completion_present(self):
        return any("[completion-reminder]" in h.get("command", "")
                   for ev in self._settings().get("hooks", {}).values() if isinstance(ev, list)
                   for e in ev for h in e.get("hooks", []))

    def test_remove_addon_hook_refuses_core_marker(self):
        # a marker that is not a well-formed [addon:<id>] <hook_id> must be refused,
        # so it can NEVER strip a core governance hook (forged-sidecar defense).
        install_hooks.install_hook("claude", addon_sources=[])
        self.assertTrue(self._core_completion_present())
        for forged in ("[completion-reminder] AGENTS.md", "[session-intent-analyzer]",
                       "[io-trace] audit", "[addon:x] obs [hook-runner", "AGENTS.md", ""):
            self.assertEqual(install_hooks.remove_addon_hook(forged, platform_key="claude"), 0,
                             msg=f"forged marker accepted: {forged!r}")
        self.assertTrue(self._core_completion_present(), msg="core hook was removed by a forged marker")

    def test_forged_sidecar_marker_cannot_remove_core_hook(self):
        import addon_registry as reg
        import addon_uninstall as un
        install_hooks.install_hook("claude", addon_sources=[])
        self.assertTrue(self._core_completion_present())
        addons = self.tmp / ".ghost-alice" / "addons" / "claude"
        reg.write_record({
            "schema_version": "1.0", "addon_id": "trojan", "addon_version": "1.0.0",
            "source": "/s/trojan", "platform": "claude", "owner": "addon",
            "origin": "addon:trojan", "depends_on_core": [], "min_core_version": "0.0.0",
            "installed_at": "t", "provided": [],
            # tampered: marker forged to a CORE marker, hook_id also forged
            "hooks": [{"hook_id": "obs", "event": "post_tool_use", "marker": "[completion-reminder] AGENTS.md"}],
        }, addons_dir=addons)
        un.uninstall_addon("trojan", addons_dir=addons,
                           allowed_roots=[self.tmp / ".claude" / "skills"], platform="claude")
        self.assertTrue(self._core_completion_present(),
                        msg="forged sidecar hook marker removed the core completion gate")

    def test_remove_addon_hook_ignores_non_managed_entry(self):
        # a non-managed hook whose command merely CONTAINS the addon marker string
        # (no [hook-runner:] dispatcher) is NOT ours -> must never be removed.
        install_hooks.install_hook("claude", addon_sources=[])
        s = self._settings()
        s["hooks"].setdefault("PostToolUse", []).append(
            {"matcher": "", "hooks": [{"type": "command", "command": "echo '[addon:victim] note now'"}]})
        (self.tmp / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        removed = install_hooks.remove_addon_hook("[addon:victim] note", platform_key="claude")
        self.assertEqual(removed, 0)
        present = any("[addon:victim] note now" in h.get("command", "")
                     for ev in self._settings()["hooks"].values() if isinstance(ev, list)
                     for e in ev for h in e.get("hooks", []))
        self.assertTrue(present, msg="remove_addon_hook deleted a non-managed hook")

    def test_remove_addon_hook_ignores_fake_runner_non_managed_entry(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        s = self._settings()
        s["hooks"].setdefault("PostToolUse", []).append(
            {"matcher": "", "hooks": [{"type": "command",
                                       "command": "echo user # [addon:hookaddon] obs [hook-runner:not-addon]"}]})
        (self.tmp / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")

        removed = install_hooks.remove_addon_hook("[addon:hookaddon] obs", platform_key="claude")

        commands = [h.get("command", "")
                    for ev in self._settings()["hooks"].values() if isinstance(ev, list)
                    for e in ev for h in e.get("hooks", [])]
        self.assertEqual(removed, 1)
        self.assertTrue(any("[hook-runner:not-addon]" in command for command in commands),
                        msg="remove_addon_hook deleted a fake-runner non-managed hook")
        self.assertFalse(any("[addon:hookaddon] obs [hook-runner:obs]" in command for command in commands),
                         msg="remove_addon_hook failed to delete the managed addon hook")

    def test_remove_addon_hook_preserves_user_command_in_same_entry(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        s = self._settings()
        for entry in s["hooks"]["PostToolUse"]:
            if any("[addon:hookaddon] obs" in h.get("command", "") for h in entry.get("hooks", [])):
                entry["hooks"].append({"type": "command", "command": "echo user sibling hook"})
                break
        (self.tmp / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")

        removed = install_hooks.remove_addon_hook("[addon:hookaddon] obs", platform_key="claude")

        commands = [h.get("command", "")
                    for ev in self._settings()["hooks"].values() if isinstance(ev, list)
                    for e in ev for h in e.get("hooks", [])]
        self.assertEqual(removed, 1)
        self.assertTrue(any("user sibling hook" in command for command in commands),
                        msg="remove_addon_hook deleted a user command in the same entry")
        self.assertFalse(any("[addon:hookaddon] obs [hook-runner:obs]" in command for command in commands),
                         msg="remove_addon_hook failed to delete the managed addon hook")

    def test_remove_all_addon_hooks_ignores_non_managed_entry(self):
        install_hooks.install_hook("claude", addon_sources=[])
        s = self._settings()
        s["hooks"].setdefault("PostToolUse", []).append(
            {"matcher": "", "hooks": [{"type": "command", "command": "echo '[addon:x] y user hook'"}]})
        (self.tmp / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        install_hooks.remove_all_addon_hooks(platform_key="claude")
        present = any("user hook" in h.get("command", "")
                     for ev in self._settings()["hooks"].values() if isinstance(ev, list)
                     for e in ev for h in e.get("hooks", []))
        self.assertTrue(present, msg="remove_all_addon_hooks deleted a non-managed hook")

    def test_remove_all_addon_hooks_ignores_fake_runner_non_managed_entry(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        s = self._settings()
        s["hooks"].setdefault("PostToolUse", []).append(
            {"matcher": "", "hooks": [{"type": "command",
                                       "command": "echo user # [addon:hookaddon] obs [hook-runner:not-addon]"}]})
        (self.tmp / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")

        removed = install_hooks.remove_all_addon_hooks(platform_key="claude")

        commands = [h.get("command", "")
                    for ev in self._settings()["hooks"].values() if isinstance(ev, list)
                    for e in ev for h in e.get("hooks", [])]
        self.assertEqual(removed, 1)
        self.assertTrue(any("[hook-runner:not-addon]" in command for command in commands),
                        msg="remove_all_addon_hooks deleted a fake-runner non-managed hook")
        self.assertFalse(any("[addon:hookaddon] obs [hook-runner:obs]" in command for command in commands),
                         msg="remove_all_addon_hooks failed to delete the managed addon hook")

    def test_remove_all_addon_hooks_preserves_user_command_in_same_entry(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        s = self._settings()
        for entry in s["hooks"]["PostToolUse"]:
            if any("[addon:hookaddon] obs" in h.get("command", "") for h in entry.get("hooks", [])):
                entry["hooks"].append({"type": "command", "command": "echo user sibling hook"})
                break
        (self.tmp / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")

        removed = install_hooks.remove_all_addon_hooks(platform_key="claude")

        commands = [h.get("command", "")
                    for ev in self._settings()["hooks"].values() if isinstance(ev, list)
                    for e in ev for h in e.get("hooks", [])]
        self.assertEqual(removed, 1)
        self.assertTrue(any("user sibling hook" in command for command in commands),
                        msg="remove_all_addon_hooks deleted a user command in the same entry")
        self.assertFalse(any("[addon:hookaddon] obs [hook-runner:obs]" in command for command in commands),
                         msg="remove_all_addon_hooks failed to delete the managed addon hook")

    def test_remove_all_addon_hooks_sweep(self):
        src = _make_addon(self.tmp, [{"id": "obs", "event": "post_tool_use", "script": "hooks/obs.py"}])
        install_hooks.install_hook("claude", addon_sources=[str(src)])
        self.assertEqual(len(self._addon_entries("PostToolUse")), 1)
        removed = install_hooks.remove_all_addon_hooks(platform_key="claude")
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(len(self._addon_entries("PostToolUse")), 0)
        self.assertTrue(self._core_completion_present())  # core suite untouched


if __name__ == "__main__":
    unittest.main()
