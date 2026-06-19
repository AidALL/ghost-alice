"""TDD for Phase P5: core-owned privileged adapter provisioning.

Run: /opt/homebrew/bin/python3 -m pytest _shared/test_addon_privileged_adapters.py -q
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import addon_installer as ai  # noqa: E402
import addon_registry as reg  # noqa: E402
import addon_uninstall as un  # noqa: E402
import hash_utils  # noqa: E402
import install_hooks  # noqa: E402


def _adapter_spec(
    *,
    adapter_id: str = "p5-demo",
    addon_id: str = "pilot",
    skill_name: str = "pilot-skill",
    event: str = "post_tool_use",
    script_rel: str = "adapters/p5_demo.py",
    hook_id: str = "p5-hook",
) -> dict:
    return {
        "adapter_id": adapter_id,
        "allowed_addon_id": addon_id,
        "expected_skill_name": skill_name,
        "events": [event],
        "script_rel_by_event": {event: script_rel},
        "hook_id_by_event": {event: hook_id},
        "args_policy": "no_args",
        "marker_template": "[adapter:{adapter_id}] {hook_id}",
        "runner_namespace": "adapter-{adapter_id}-{hook_id}",
        "source_policy": "expected_skill_source",
        "hash_policy": "content_hash",
        "uninstall_matcher": "exact_marker_runner",
    }


def _adapter_allowlist(addon_id: str = "pilot", skill_name: str = "pilot-skill") -> dict:
    return {"p5-demo": _adapter_spec(addon_id=addon_id, skill_name=skill_name)}


def _write_allowlist(path: Path, *, trust_owner: str, adapters: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "trust_owner": trust_owner,
            "adapters": adapters,
        }),
        encoding="utf-8",
    )


def _make_adapter_addon(
    base: Path,
    *,
    addon_id: str = "pilot",
    skill_name: str = "pilot-skill",
    request: object = ("p5-demo",),
) -> Path:
    root = base / "src"
    addon = root / "addons" / addon_id
    skill = addon / "skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: Use when testing privileged adapters.\n---\n# x\n",
        encoding="utf-8",
    )
    (skill / "adapters").mkdir()
    (skill / "adapters" / "p5_demo.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    (root / "addons-manifest.json").write_text(
        json.dumps({
            "manifest_version": 1,
            "addons": [{"id": addon_id, "path": f"addons/{addon_id}", "min_core_version": "0.1.0"}],
        }),
        encoding="utf-8",
    )
    manifest = {
        "addon_version": "0.1.0",
        "addon_id": addon_id,
        "skills": [{"name": skill_name, "source": "skill", "skill_dir": "skill"}],
        "platforms": ["claude", "codex"],
        "depends_on_core": [],
        "secrets": [],
    }
    if request is not None:
        manifest["privileged_adapters"] = list(request) if isinstance(request, tuple) else request
    (addon / "addon.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


class PrivilegedAdapterManifestTest(unittest.TestCase):
    def test_manifest_requests_adapter_ids_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_adapter_addon(Path(tmp))
            targets = ai.load_addon_targets(
                [src],
                platform="claude",
                privileged_adapter_allowlist=_adapter_allowlist(),
            )
        self.assertEqual(targets[0].privileged_adapters, ("p5-demo",))

    def test_manifest_supplied_adapter_implementation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_adapter_addon(
                Path(tmp),
                request=[{"id": "p5-demo", "script": "adapters/p5_demo.py"}],
            )
            with self.assertRaises(ai.AddonManifestError) as ctx:
                ai.load_addon_targets(
                    [src],
                    platform="claude",
                    privileged_adapter_allowlist=_adapter_allowlist(),
                )
        message = str(ctx.exception).lower()
        self.assertIn("implementation", message)
        self.assertIn("request an adapter id only", message)
        self.assertIn("core or machine-owner", message)
        self.assertIn("allowlist", message)
        self.assertNotIn("forbidden", message)

    def test_top_level_adapter_implementation_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_adapter_addon(Path(tmp))
            manifest_path = src / "addons" / "pilot" / "addon.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["script_rel_by_event"] = {"post_tool_use": "adapters/p5_demo.py"}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ai.AddonManifestError) as ctx:
                ai.load_addon_targets(
                    [src],
                    platform="claude",
                    privileged_adapter_allowlist=_adapter_allowlist(),
                )
        message = str(ctx.exception).lower()
        self.assertIn("implementation", message)
        self.assertIn("core or machine-owner", message)
        self.assertIn("allowlist", message)
        self.assertNotIn("forbidden", message)

    def test_unknown_wrong_addon_and_wrong_skill_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_adapter_addon(Path(tmp))
            with self.assertRaises(ai.AddonManifestError):
                ai.load_addon_targets([src], platform="claude", privileged_adapter_allowlist={})

        with tempfile.TemporaryDirectory() as tmp:
            src = _make_adapter_addon(Path(tmp), addon_id="other")
            with self.assertRaises(ai.AddonManifestError):
                ai.load_addon_targets(
                    [src],
                    platform="claude",
                    privileged_adapter_allowlist=_adapter_allowlist(addon_id="pilot"),
                )

        with tempfile.TemporaryDirectory() as tmp:
            src = _make_adapter_addon(Path(tmp), skill_name="wrong-skill")
            with self.assertRaises(ai.AddonManifestError):
                ai.load_addon_targets(
                    [src],
                    platform="claude",
                    privileged_adapter_allowlist=_adapter_allowlist(skill_name="pilot-skill"),
                )


class PrivilegedAdapterAllowlistRootTest(unittest.TestCase):
    def test_default_core_allowlist_contains_autopilot_adapter(self):
        allowlist = ai.load_privileged_adapter_allowlist(user_path=None)

        spec = allowlist["autopilot-mode"]
        self.assertEqual(spec["allowed_addon_id"], "autopilot-mode")
        self.assertEqual(spec["expected_skill_name"], "autopilot-mode")
        self.assertEqual(spec["events"], ["on_agent_stop"])
        self.assertEqual(spec["args_policy"], "no_args")

    def test_core_allowlist_data_file_enables_adapter_without_engine_constant_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            official = root / "skill-catalog" / "privileged-adapters.json"
            _write_allowlist(official, trust_owner="core", adapters=_adapter_allowlist())
            allowlist = ai.load_privileged_adapter_allowlist(official_path=official, user_path=None)

            src = _make_adapter_addon(root / "addon-src")
            targets = ai.load_addon_targets(
                [src],
                platform="claude",
                privileged_adapter_allowlist=allowlist,
            )

        self.assertEqual(targets[0].privileged_adapters, ("p5-demo",))

    def test_user_allowlist_requires_machine_owner_trust_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_allowlist = root / ".ghost-alice" / "privileged-adapters.json"
            _write_allowlist(user_allowlist, trust_owner="core", adapters={
                "local-pilot": _adapter_spec(
                    adapter_id="local-pilot",
                    addon_id="local",
                    skill_name="local-skill",
                    hook_id="local-hook",
                ),
            })

            with self.assertRaises(ai.AddonManifestError) as ctx:
                ai.load_privileged_adapter_allowlist(official_path=None, user_path=user_allowlist)

        self.assertIn("machine-owner", str(ctx.exception))

    def test_machine_owner_allowlist_enables_custom_addon_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_allowlist = root / ".ghost-alice" / "privileged-adapters.json"
            _write_allowlist(user_allowlist, trust_owner="machine-owner", adapters={
                "local-pilot": _adapter_spec(
                    adapter_id="local-pilot",
                    addon_id="local",
                    skill_name="local-skill",
                    event="on_agent_stop",
                    hook_id="local-stop",
                ),
            })
            allowlist = ai.load_privileged_adapter_allowlist(official_path=None, user_path=user_allowlist)

            src = _make_adapter_addon(
                root / "addon-src",
                addon_id="local",
                skill_name="local-skill",
                request=("local-pilot",),
            )
            targets = ai.load_addon_targets(
                [src],
                platform="claude",
                privileged_adapter_allowlist=allowlist,
            )
            hooks = ai.iter_privileged_adapter_hook_specs(targets, privileged_adapter_allowlist=allowlist)

        self.assertEqual(targets[0].privileged_adapters, ("local-pilot",))
        self.assertEqual(hooks[0]["event"], "on_agent_stop")
        self.assertEqual(hooks[0]["marker"], "[adapter:local-pilot] local-stop")

    def test_default_user_allowlist_root_follows_current_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            user_allowlist = home / ".ghost-alice" / "privileged-adapters.json"
            _write_allowlist(user_allowlist, trust_owner="machine-owner", adapters={
                "local-pilot": _adapter_spec(
                    adapter_id="local-pilot",
                    addon_id="local",
                    skill_name="local-skill",
                    hook_id="local-hook",
                ),
            })

            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                allowlist = ai.load_privileged_adapter_allowlist(official_path=None)

        self.assertIn("local-pilot", allowlist)


class PrivilegedAdapterSidecarTest(unittest.TestCase):
    def test_write_sidecars_records_marker_keyed_adapter_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _make_adapter_addon(root)
            skills = root / "skills"
            skills.mkdir()
            (skills / "pilot-skill").symlink_to(src / "addons" / "pilot" / "skill", target_is_directory=True)
            addons = root / "addons-state"
            with mock.patch.dict(ai.CORE_PRIVILEGED_ADAPTER_ALLOWLIST, _adapter_allowlist(), clear=True):
                rc = ai.main([
                    "write-sidecars",
                    "--source", str(src),
                    "--platform", "claude",
                    "--addons-dir", str(addons),
                    "--skills-dir", str(skills),
                    "--installed-at", "t",
                ])
            self.assertEqual(rc, 0)
            record = reg.read_record("pilot", addons_dir=addons)
            adapters = [p for p in record["provided"] if p["kind"] == "adapter"]
            self.assertEqual(len(adapters), 1)
            entry = adapters[0]
            self.assertEqual(entry["name"], "p5-demo")
            self.assertEqual(entry["marker"], "[adapter:p5-demo] p5-hook")
            self.assertEqual(entry["metadata"]["event"], "post_tool_use")
            self.assertEqual(entry["metadata"]["hook_id"], "p5-hook")
            self.assertEqual(entry["content_hash"], hash_utils.hash_target(entry["target"], "copy"))


class PrivilegedAdapterHookLifecycleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.claude = self.tmp / ".claude"
        self.claude.mkdir(parents=True)
        self._env = {k: os.environ.get(k) for k in ("HOME", "CLAUDE_CONFIG_DIR")}
        os.environ["HOME"] = str(self.tmp)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude)

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def _commands(self) -> list[str]:
        settings = json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))
        return [
            h.get("command", "")
            for event in settings.get("hooks", {}).values() if isinstance(event, list)
            for entry in event
            for h in entry.get("hooks", [])
        ]

    def test_adapter_hook_is_installed_and_removed_by_exact_marker(self):
        src = _make_adapter_addon(self.tmp)
        with mock.patch.dict(ai.CORE_PRIVILEGED_ADAPTER_ALLOWLIST, _adapter_allowlist(), clear=True):
            self.assertEqual(install_hooks.install_hook("claude", addon_sources=[str(src)]), "installed")
        commands = self._commands()
        self.assertTrue(any("[adapter:p5-demo] p5-hook" in command for command in commands))
        self.assertTrue(any("[hook-runner:adapter-p5-demo-p5-hook]" in command for command in commands))

        removed = install_hooks.remove_adapter_hook("[adapter:p5-demo] p5-hook", platform_key="claude")

        self.assertEqual(removed, 1)
        self.assertFalse(any("[adapter:p5-demo] p5-hook" in command for command in self._commands()))
        self.assertEqual(install_hooks.remove_adapter_hook("[addon:pilot] obs", platform_key="claude"), 0)

    def test_full_uninstall_removes_adapter_hook(self):
        # Full uninstall (install.sh --uninstall -> install_hooks.py --uninstall ->
        # uninstall_hook) must strip managed [adapter:...] hooks, not only addon hooks,
        # so a privileged adapter hook never survives a completed full uninstall.
        src = _make_adapter_addon(self.tmp)
        with mock.patch.dict(ai.CORE_PRIVILEGED_ADAPTER_ALLOWLIST, _adapter_allowlist(), clear=True):
            self.assertEqual(install_hooks.install_hook("claude", addon_sources=[str(src)]), "installed")
        self.assertTrue(any("[adapter:p5-demo] p5-hook" in c for c in self._commands()))

        install_hooks.uninstall_hook("claude")

        self.assertFalse(
            any("[adapter:p5-demo] p5-hook" in c for c in self._commands()),
            msg="full uninstall must remove the managed adapter hook",
        )

    def test_adapter_hook_matching_uses_installer_runner_helper(self):
        marker = "[adapter:p5-demo] p5-hook"
        runner_id = "adapter-p5-demo-p5-hook-v2"
        command = install_hooks._hook_runner_command_entry(
            runner_id,
            "python adapter.py",
            marker,
        )["hooks"][0]["command"]

        with mock.patch.object(ai, "privileged_adapter_runner_id", return_value=runner_id):
            self.assertTrue(install_hooks._is_exact_adapter_hook_command(command, marker))
            self.assertTrue(install_hooks._is_managed_adapter_command(command))

    def test_uninstall_removes_adapter_hook_without_removing_source_script(self):
        src = _make_adapter_addon(self.tmp)
        skill_src = src / "addons" / "pilot" / "skill"
        script = skill_src / "adapters" / "p5_demo.py"
        skills = self.tmp / ".claude" / "skills"
        skills.mkdir(parents=True)
        dest = skills / "pilot-skill"
        dest.symlink_to(skill_src, target_is_directory=True)
        with mock.patch.dict(ai.CORE_PRIVILEGED_ADAPTER_ALLOWLIST, _adapter_allowlist(), clear=True):
            install_hooks.install_hook("claude", addon_sources=[str(src)])
            provided = [{
                "kind": "skill",
                "name": "pilot-skill",
                "target": str(dest),
                "ownership": "addon",
                "install_mode": "symlink",
                "content_hash": hash_utils.hash_target(str(dest), "symlink"),
                "marker": "",
                "metadata": {},
            }]
            provided.extend(ai.build_privileged_adapter_provided_entries(
                ai.load_addon_targets([src], platform="claude"),
                skills_dir=skills,
            )["pilot"])
        addons = self.tmp / ".ghost-alice" / "addons" / "claude"
        reg.write_record(
            ai.build_sidecar_record(
                ai.load_addon_targets(
                    [src],
                    platform="claude",
                    privileged_adapter_allowlist=_adapter_allowlist(),
                )[0],
                platform="claude",
                installed_at="t",
                provided=provided,
            ),
            addons_dir=addons,
        )

        result = un.uninstall_addon("pilot", addons_dir=addons, allowed_roots=[skills], platform="claude")

        self.assertEqual(result["status"], "removed")
        self.assertTrue(script.is_file(), msg="adapter source script must not be deleted by uninstall")
        self.assertFalse(any("[adapter:p5-demo] p5-hook" in command for command in self._commands()))

    def test_partial_uninstall_keeps_skill_target_but_processes_adapter_hook_entry(self):
        src = _make_adapter_addon(self.tmp)
        skill_src = src / "addons" / "pilot" / "skill"
        skills = self.tmp / ".claude" / "skills"
        skills.mkdir(parents=True)
        dest = skills / "pilot-skill"
        dest.symlink_to(skill_src, target_is_directory=True)
        with mock.patch.dict(ai.CORE_PRIVILEGED_ADAPTER_ALLOWLIST, _adapter_allowlist(), clear=True):
            install_hooks.install_hook("claude", addon_sources=[str(src)])
            provided = [{
                "kind": "skill",
                "name": "pilot-skill",
                "target": str(dest),
                "ownership": "addon",
                "install_mode": "symlink",
                "content_hash": "drifted",
                "marker": "",
                "metadata": {},
            }]
            provided.extend(ai.build_privileged_adapter_provided_entries(
                ai.load_addon_targets([src], platform="claude"),
                skills_dir=skills,
            )["pilot"])
        addons = self.tmp / ".ghost-alice" / "addons" / "claude"
        reg.write_record(
            ai.build_sidecar_record(
                ai.load_addon_targets(
                    [src],
                    platform="claude",
                    privileged_adapter_allowlist=_adapter_allowlist(),
                )[0],
                platform="claude",
                installed_at="t",
                provided=provided,
            ),
            addons_dir=addons,
        )

        result = un.uninstall_addon("pilot", addons_dir=addons, allowed_roots=[skills], platform="claude")

        self.assertEqual(result["status"], "partial")
        self.assertTrue(dest.exists(), msg="drifted skill target must stay for manual review")
        self.assertTrue(any(item["action"] == "manual-review" for item in result["items"]))
        self.assertTrue(any(item.get("kind") == "adapter" for item in result["items"]))
        self.assertFalse(any("[adapter:p5-demo] p5-hook" in command for command in self._commands()))


if __name__ == "__main__":
    unittest.main()
