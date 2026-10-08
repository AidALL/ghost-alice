from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_shared"))
import addon_registry
import addon_uninstall
import addon_installer


FIXTURE_INSTALLER = '''import argparse, hashlib, json, shutil, sys
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('command')
p.add_argument('--prefix', type=Path, required=True)
p.add_argument('--source')
p.add_argument('--ghost-alice-root')
p.add_argument('--skills-dir', action='append', default=[])
p.add_argument('--platform-registry', action='append', default=[])
p.add_argument('--openclaw-source')
p.add_argument('--node-source')
a = p.parse_args()
log = a.prefix.parent / 'fixture-calls.jsonl'
log.parent.mkdir(parents=True, exist_ok=True)
with log.open('a') as f: f.write(json.dumps(vars(a), default=str) + '\\n')
registries = dict(item.split('=', 1) for item in a.platform_registry)
sys.path.insert(0,str(Path(a.ghost_alice_root)/'_shared'))
import hash_utils
if a.command == 'install':
    script = a.prefix / 'managed/app/scripts/installer.py'
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(__file__, script)
    resources = [{'role':'runtime', 'path':str(a.prefix/'managed'), 'entries':{
        'app/scripts/installer.py': {'kind':'file', 'sha256':hashlib.sha256(script.read_bytes()).hexdigest()}}}]
    for directory in a.skills_dir:
        skill = Path(directory)/'ai-secretary'
        skill.mkdir(parents=True, exist_ok=True)
        (skill/'SKILL.md').write_text('---\\nname: ai-secretary\\ndescription: owned fixture\\n---\\nfixture skill\\n')
        resources.append({'role':'skill','path':str(skill),'entries':{}})
    provided = [{'kind':'skill' if resource['role']=='skill' else 'resource','name':'ai-secretary' if resource['role']=='skill' else 'ai-secretary-runtime',
        'target':resource['path'],'ownership':'addon','install_mode':'copy','content_hash':hash_utils.hash_target(resource['path'],'copy'),
        'marker':'ai-secretary','metadata':{}} for resource in resources]
    for platform, directory in registries.items():
        sidecar = Path(directory)/'ai-secretary.json'
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({'schema_version':'1.0','addon_id':'ai-secretary','addon_version':'0.1.0',
            'source':str(Path(a.source)/'addons/ai-secretary'),'platform':platform,'owner':'addon','origin':'source','depends_on_core':[],
            'min_core_version':'0.4.0','installed_at':'fixture','provided':provided,
            'installation':{'installer':'ai-secretary','prefix':str(a.prefix),'platform_registries':registries}}))
    (a.prefix/'install-manifest.json').write_text(json.dumps({'schema_version':2,'prefix':str(a.prefix),
        'platform_registries':registries,'resources':resources}))
elif a.command == 'uninstall':
    manifest = json.loads((a.prefix/'install-manifest.json').read_text())
    for resource in manifest['resources']:
        if resource['role']=='skill': shutil.rmtree(resource['path'])
    for directory in registries.values(): (Path(directory)/'ai-secretary.json').unlink()
    shutil.rmtree(a.prefix/'managed')
    (a.prefix/'install-manifest.json').unlink()
print(json.dumps({'status':'installed' if a.command=='install' else 'uninstalled'}))
'''


class ManagedSecretaryAddonTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "home"
        self.home.mkdir()
        self.core = Path(self.temp.name).resolve() / "core"
        shutil.copytree(ROOT, self.core, ignore=shutil.ignore_patterns(".git", ".tmp", "__pycache__"))
        self.source = Path(self.temp.name) / "source"
        skill = self.source / "addons/ai-secretary/ai-secretary"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: ai-secretary\ndescription: fixture\n---\nfixture\n")
        (self.source / "addons-manifest.json").write_text(json.dumps({"manifest_version":1,"addons":[{
            "id":"ai-secretary","path":"addons/ai-secretary","min_core_version":"0.4.0"}]}))
        (self.source / "addons/ai-secretary/addon.json").write_text(json.dumps({"addon_id":"ai-secretary",
            "addon_version":"0.1.0","platforms":["codex","claude"],"depends_on_core":[],
            "skills":[{"name":"ai-secretary","source":"skill","skill_dir":"ai-secretary"}]}))
        (self.source / "scripts").mkdir()
        (self.source / "scripts/installer.py").write_text(FIXTURE_INSTALLER)
        self.env = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.home / ".codex"),
            CLAUDE_CONFIG_DIR=str(self.home / ".claude"), GHOST_ALICE_OFFICIAL_ADDON_AI_SECRETARY_SOURCE=str(self.source),
            GHOST_ALICE_LANGUAGE="en", PYTHONDONTWRITEBYTECODE="1")
        self.prefix = self.home / ".local/share/ai-secretary"

    def run_shell(self, *args):
        return subprocess.run(["bash", str(self.core / "install.sh"), *args], env=self.env, cwd=self.core,
            text=True, capture_output=True, timeout=90)

    def calls(self):
        path = self.prefix.parent / "fixture-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    @unittest.skipUnless(sys.platform == "darwin", "Secretary's complete desktop bundle requires macOS")
    def test_official_alias_installs_full_bundle_once_for_detected_platforms(self):
        (self.home / ".codex").mkdir()
        (self.home / ".claude").mkdir()
        result = self.run_shell("--addon", "ai-secretary", "--skip-source-health", "task-router")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.calls()), 1)
        call = self.calls()[0]
        self.assertEqual(set(call["skills_dir"]), {str(self.home / ".agents/skills"), str(self.home / ".claude/skills")})
        self.assertEqual(set(call["platform_registry"]), {
            f"codex={self.home}/.ghost-alice/addons/codex", f"claude={self.home}/.ghost-alice/addons/claude"})
        self.assertTrue((self.prefix / "managed/app/scripts/installer.py").is_file())
        for platform in ("codex", "claude"):
            self.assertEqual(addon_registry.read_record("ai-secretary", addons_dir=self.home / f".ghost-alice/addons/{platform}")["installation"]["installer"], "ai-secretary")
            state = json.loads((self.home / f".ghost-alice/install-state/{platform}.json").read_text())
            self.assertTrue(any(target.get("addon_id") == "ai-secretary" for target in state["targets"]))
            snapshot = self.home / f".ghost-alice/pending-merges/{platform}/snapshot.json"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(json.dumps({"files":{str(self.home / ".agents/skills/ai-secretary/SKILL.md"):"owned", str(self.home / "unrelated.txt"):"user"}}))
        self.assertIn("description: owned fixture", (self.home / ".agents/skills/ai-secretary/SKILL.md").read_text())
        removed = self.run_shell("--platform", "codex", "--uninstall", "--addon", "ai-secretary")
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertFalse((self.home / ".claude/skills/ai-secretary").exists())
        self.assertFalse((self.home / ".ghost-alice/addons/claude/ai-secretary.json").exists())
        for platform in ("codex", "claude"):
            state = json.loads((self.home / f".ghost-alice/install-state/{platform}.json").read_text())
            self.assertFalse(any(target.get("addon_id") == "ai-secretary" for target in state["targets"]))
            snapshot = json.loads((self.home / f".ghost-alice/pending-merges/{platform}/snapshot.json").read_text())
            self.assertEqual(snapshot["files"], {str(self.home / "unrelated.txt"):"user"})

    @unittest.skipUnless(sys.platform == "darwin", "Secretary's complete desktop bundle requires macOS")
    def test_core_addon_removal_delegates_before_generic_target_deletion(self):
        (self.home / ".codex").mkdir()
        installed = self.run_shell("--platform", "codex", "--addon", "ai-secretary", "--skip-source-health", "task-router")
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        removed = self.run_shell("--platform", "codex", "--uninstall", "--addon", "ai-secretary")
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertEqual([call["command"] for call in self.calls()], ["install", "uninstall"])
        self.assertFalse((self.prefix / "managed").exists())
        self.assertFalse((self.home / ".agents/skills/ai-secretary").exists())
        self.assertTrue((self.home / ".agents/skills/task-router").exists())

    @unittest.skipUnless(sys.platform == "darwin", "Secretary's complete desktop bundle requires macOS")
    def test_modified_owned_uninstaller_is_preserved_without_execution(self):
        (self.home / ".codex").mkdir()
        installed = self.run_shell("--platform", "codex", "--addon", "ai-secretary", "--skip-source-health", "task-router")
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        script = self.prefix / "managed/app/scripts/installer.py"
        script.write_text("raise RuntimeError('must never run')\n")
        removed = self.run_shell("--platform", "codex", "--uninstall", "--addon", "ai-secretary")
        self.assertNotEqual(removed.returncode, 0)
        self.assertTrue(script.exists())
        self.assertTrue((self.home / ".agents/skills/ai-secretary").exists())
        self.assertEqual(len(self.calls()), 1)

    def test_sidecar_cannot_supply_an_executable_command(self):
        module = importlib.import_module("managed_addons")
        record = {"addon_id":"ai-secretary","installation":{"installer":"ai-secretary","prefix":"/tmp/unowned",
            "platform_registries":{"codex":str(self.home / ".ghost-alice/addons/codex")},
            "uninstaller":["/bin/sh", "-c", "must never run"]}}
        with mock.patch.dict(os.environ, self.env):
            report = module.uninstall_secretary(record, addons_dir=self.home / ".ghost-alice/addons/codex", confirm=True)
        self.assertEqual(report["status"], "partial")

    def test_unsupported_host_fails_before_any_owned_writes(self):
        module = importlib.import_module("managed_addons")
        with mock.patch.dict(os.environ, self.env), mock.patch.object(module.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(module.ManagedAddonError, "macOS"):
                module.install_secretary(self.source, core_root=ROOT, platform_targets={"codex":self.home / ".agents/skills"})
        self.assertFalse(self.prefix.exists())

    @unittest.skipUnless(sys.platform == "darwin", "Secretary's complete desktop bundle requires macOS")
    def test_fixed_cache_sources_are_forwarded_as_literal_paths(self):
        module = importlib.import_module("managed_addons")
        cache = self.home / "cache with spaces"
        node = self.home / "node distribution"
        with mock.patch.dict(os.environ, {**self.env,"SECRETARY_OPENCLAW_SOURCE":str(cache),"SECRETARY_NODE_SOURCE":str(node)}):
            module.install_secretary(self.source, core_root=ROOT, platform_targets={"codex":self.home / ".agents/skills"})
        self.assertEqual(self.calls()[0]["openclaw_source"], str(cache))
        self.assertEqual(self.calls()[0]["node_source"], str(node))

    @unittest.skipUnless(sys.platform == "darwin", "Secretary's complete desktop bundle requires macOS")
    def test_legacy_registry_selection_requires_explicit_owned_migration(self):
        module = importlib.import_module("managed_addons")
        self.prefix.mkdir(parents=True)
        manifest = self.prefix / "install-manifest.json"
        content = json.dumps({"schema_version":2,"prefix":str(self.prefix),"registry_dir":str(self.home / ".ghost-alice/addons"),"resources":[]})
        manifest.write_text(content)
        with mock.patch.dict(os.environ, self.env):
            with self.assertRaisesRegex(module.ManagedAddonError, "original --registry-dir"):
                module.install_secretary(self.source, core_root=ROOT, platform_targets={"codex":self.home / ".agents/skills"})
        self.assertEqual(manifest.read_text(), content)
        self.assertFalse((self.prefix / "managed").exists())

    @unittest.skipUnless(sys.platform == "darwin", "Secretary's complete desktop bundle requires macOS")
    def test_malformed_ownership_resources_are_preserved_without_execution(self):
        module = importlib.import_module("managed_addons")
        with mock.patch.dict(os.environ, self.env):
            module.install_secretary(self.source, core_root=ROOT, platform_targets={"codex":self.home / ".agents/skills"})
            manifest = self.prefix / "install-manifest.json"
            data = json.loads(manifest.read_text())
            data["resources"].insert(0, "invalid resource")
            manifest.write_text(json.dumps(data))
            directory = self.home / ".ghost-alice/addons/codex"
            record = addon_registry.read_record("ai-secretary", addons_dir=directory)
            report = module.uninstall_secretary(record, addons_dir=directory, confirm=True)
        self.assertEqual(report["status"], "partial")
        self.assertTrue((self.prefix / "managed/app/scripts/installer.py").is_file())
        self.assertEqual(len(self.calls()), 1)

    @unittest.skipUnless(sys.platform == "darwin", "Secretary's complete desktop bundle requires macOS")
    def test_malformed_runtime_inventory_is_preserved_without_execution(self):
        module = importlib.import_module("managed_addons")
        with mock.patch.dict(os.environ, self.env):
            module.install_secretary(self.source, core_root=ROOT, platform_targets={"codex":self.home / ".agents/skills"})
            manifest = self.prefix / "install-manifest.json"
            data = json.loads(manifest.read_text())
            data["resources"][0]["entries"] = ["invalid inventory"]
            manifest.write_text(json.dumps(data))
            directory = self.home / ".ghost-alice/addons/codex"
            record = addon_registry.read_record("ai-secretary", addons_dir=directory)
            report = module.uninstall_secretary(record, addons_dir=directory, confirm=True)
        self.assertEqual(report["status"], "partial")
        self.assertTrue((self.prefix / "managed/app/scripts/installer.py").is_file())
        self.assertEqual(len(self.calls()), 1)

    @unittest.skipUnless(sys.platform == "darwin", "Secretary's complete desktop bundle requires macOS")
    def test_generic_repair_defers_bundle_without_adopting_missing_skill(self):
        module = importlib.import_module("managed_addons")
        with mock.patch.dict(os.environ, self.env):
            module.install_secretary(self.source, core_root=ROOT, platform_targets={
                "codex":self.home / ".agents/skills", "claude":self.home / ".claude/skills"})
            records = {platform:(self.home / f".ghost-alice/addons/{platform}/ai-secretary.json").read_bytes() for platform in ("codex","claude")}
            skill = self.home / ".agents/skills/ai-secretary"
            shutil.rmtree(skill)
            report = addon_installer.repair_missing_addon_targets(platform="codex",addons_dir=self.home / ".ghost-alice/addons/codex",
                skills_dir=self.home / ".agents/skills",claude_commands_dir=self.home / ".claude/commands",resources_dir=self.home / ".ghost-alice/resources/codex")
        self.assertFalse(skill.exists())
        self.assertEqual(report["errors"], [])
        self.assertTrue(any(item.get("action") == "use-owned-installer" and item["addon_id"] == "ai-secretary" for item in report["kept"]))
        for platform, content in records.items():
            self.assertEqual((self.home / f".ghost-alice/addons/{platform}/ai-secretary.json").read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
