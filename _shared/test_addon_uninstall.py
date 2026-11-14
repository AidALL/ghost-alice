"""Per-addon uninstall contract checks.

Remove only owned recorded targets; preserve drifted targets. Validate the
two-phase removal marker, sidecar-last deletion and resumable cleanup.

Run: python3 -m pytest _shared/test_addon_uninstall.py -q
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import shlex
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "_shared"))
sys.path.insert(0, str(REPO_ROOT / "merge-companion" / "scripts"))

import addon_registry as reg  # noqa: E402
import install_hooks  # noqa: E402
import install_transaction  # noqa: E402
import addon_uninstall as un  # noqa: E402
import hash_utils  # noqa: E402
import install_destination  # noqa: E402
import file_lock  # noqa: E402
import pending_manifest  # noqa: E402
from snapshot import capture_snapshot, load_snapshot  # noqa: E402
from installer_testing import installer_env


class UninstallTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.enterContext(mock.patch.dict(os.environ, installer_env(self.root), clear=True))
        # platform-scoped layout: sidecars live under addons/<platform>/
        self.addons = self.root / ".ghost-alice" / "addons" / "claude"
        self.skills = self.root / ".claude" / "skills"
        self.src = self.root / "src"
        self._install_modes = {}
        self._destination = install_destination.use(dict(
            mode="isolated", owned_root=str(self.root), platform_dir=str(self.root / ".claude"),
            skills_dir=str(self.skills), ghost_root=str(self.root / ".ghost-alice"),
            ledger_root=str(self.root / "ledger"), project_root=str(self.root / "project"),
            core_source_root=str(REPO_ROOT), runtime_shared_dir=str(self.root / ".ghost-alice/runtime/current/_shared"),
        ))
        self._destination.__enter__()
        self.addCleanup(self._destination.__exit__, None, None, None)

    def tearDown(self):
        pass  # LIFO cleanups restore subclass environment patches before destination.

    def _provision_target(self, src, dest):
        try:
            return install_transaction._create_directory_link(src, dest)
        except OSError:
            shutil.copytree(src, dest)
            return "copy"

    def _install(self, addon_id, names):
        self.skills.mkdir(parents=True, exist_ok=True)
        provided = []
        modes = {}
        for name in names:
            src = self.src / addon_id / name
            src.mkdir(parents=True, exist_ok=True)
            (src / "SKILL.md").write_text("x", encoding="utf-8")
            dest = self.skills / name
            mode = self._provision_target(src, dest)
            modes[name] = mode
            self._install_modes[dest] = mode
            provided.append({
                "kind": "skill", "name": name, "target": str(dest), "ownership": "addon",
                "install_mode": mode, "content_hash": hash_utils.hash_target(str(dest), mode),
                "marker": "", "metadata": {},
            })
        reg.write_record({
            "schema_version": "1.0", "addon_id": addon_id, "addon_version": "1.0.0",
            "source": str(self.src / addon_id), "platform": "claude", "owner": "addon",
            "origin": f"addon:{addon_id}", "depends_on_core": [], "min_core_version": "0.0.0",
            "installed_at": "t", "provided": provided,
        }, addons_dir=self.addons)
        return modes

    def _write_sidecar(self, addon_id, provided):
        reg.write_record({
            "schema_version": "1.0", "addon_id": addon_id, "addon_version": "1.0.0",
            "source": str(self.src / addon_id), "platform": "claude", "owner": "addon",
            "origin": f"addon:{addon_id}", "depends_on_core": [], "min_core_version": "0.0.0",
            "installed_at": "t", "provided": provided,
        }, addons_dir=self.addons)

    def _uninstall(self, addon_id, **kw):
        kw.setdefault("allowed_roots", [self.skills])
        return un.uninstall_addon(addon_id, addons_dir=self.addons, platform="claude", **kw)

    def _marker(self, addon_id):
        return self.addons / f"{addon_id}.json.removing"


class CoreUninstallTest(UninstallTestBase):
    def test_standalone_addon_cli_removes_its_payload_reference_under_the_install_lock(self):
        import installed_payload
        import install_lock
        self._install('alpha', ['one'])
        target = self.skills/'one'
        ghost = self.root/'.ghost-alice'
        generation = install_lock.acquire_lock(ghost/'install.lock', owner='test')
        try:
            installed_payload.update(ghost, {'targets': {str(target): dict(
                name='one', kind='skill', mode='link', link_target=os.readlink(target), provider='addon:alpha',
                baseline='recorded', used_by=['claude'])}})
        finally:
            install_lock.release_lock(ghost/'install.lock', generation)
        with contextlib.redirect_stdout(io.StringIO()):
            code = un.main(['--addons-dir', str(self.addons), '--platform', 'claude',
                            '--skills-dir', str(self.skills), '--addon-id', 'alpha'])
        self.assertEqual(code, 0)
        self.assertEqual(installed_payload.read(ghost)['targets'], {})
        self.assertFalse((ghost/'install.lock').exists())
        self.assertFalse(os.path.lexists(target))

    def test_shared_addon_file_remains_and_only_requesting_host_record_is_removed(self):
        self._install('alpha', ['one'])
        record = reg.read_record('alpha', addons_dir=self.addons)
        record['platform'] = 'codex'
        other_dir = self.addons.parent/'codex'
        reg.write_record(record, addons_dir=other_dir)
        other = reg.sidecar_path('alpha', addons_dir=other_dir)
        before = other.read_bytes()
        link = os.readlink(self.skills/'one')
        result = self._uninstall('alpha')
        self.assertEqual(result['status'], 'removed')
        self.assertEqual(os.readlink(self.skills/'one'), link)
        self.assertEqual(other.read_bytes(), before)
        self.assertFalse(reg.sidecar_path('alpha', addons_dir=self.addons).exists())
        self.assertEqual(next(i for i in result['items'] if i.get('name') == 'one')['action'], 'reference-released')

    def test_partial_removal_prunes_successful_target_inventory_only(self):
        self._install('alpha', ['one', 'two'])
        state = self.root/'.ghost-alice/install-state/claude.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(dict(schema_version=1, platform='claude', targets=[dict(
            target_name=name, dest_path=str(self.skills/name), owner='addon', addon_id='alpha',
            install_mode='symlink') for name in ('one', 'two')])))
        (self.skills/'two').unlink()
        (self.skills/'two').symlink_to(self.root/'personal-source')
        result = self._uninstall('alpha')
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(os.path.lexists(self.skills/'one'))
        self.assertEqual([row['target_name'] for row in json.loads(state.read_text())['targets']], ['two'])
        self.assertTrue(self._marker('alpha').exists())
        self.assertTrue(reg.sidecar_path('alpha', addons_dir=self.addons).exists())

    def test_unreadable_settings_are_not_permission_attribution_retention(self):
        self._install('first', ['one'])
        settings = self.root / '.claude/settings.json'
        settings.write_bytes(b'\xff')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = un.main(['--addons-dir', str(self.addons), '--skills-dir', str(self.skills),
                            '--addon-id', 'first'])
        self.assertEqual(code, 2)
        result = json.loads(output.getvalue())
        self.assertNotEqual(result['status'], 'permissions-retained')
        self.assertTrue(result.get('errors') or any(item.get('errors') for item in result['items']))
        self.assertEqual(settings.read_bytes(), b'\xff')
        self.assertTrue((self.skills / 'one').exists())

    def test_resume_without_markers_does_not_require_settings(self):
        self.addons.mkdir(parents=True)
        (self.root / '.claude/settings.json').mkdir(parents=True)
        self.assertEqual(un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills]), [])

    def test_resume_snapshot_failure_is_a_reported_error(self):
        self._install('first', ['one'])
        self._marker('first').write_text('{}')
        (self.root / '.claude/settings.json').mkdir()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = un.main(['--addons-dir', str(self.addons), '--skills-dir', str(self.skills), '--resume-pending'])
        self.assertEqual(code, 2)
        result = json.loads(output.getvalue())['resumed'][0]
        self.assertEqual(result['status'], 'error')
        self.assertTrue(self._marker('first').exists())

    def test_resume_two_addons_keeps_the_command_start_backup(self):
        from types import SimpleNamespace
        import addon_skill_permissions
        targets = []
        for addon_id, name in (('first', 'one'), ('second', 'two')):
            self._install(addon_id, [name])
            self._marker(addon_id).write_text('{}')
            targets.append(SimpleNamespace(addon_id=addon_id, name=name))
        settings = self.root / '.claude/settings.json'
        allowed = ['Bash(custom)', 'Skill(one)', 'Skill(two)']
        settings.write_text(json.dumps(dict(permissions=dict(allow=allowed))))
        addon_skill_permissions.record_install(settings, targets, ['Bash(custom)'], allowed)
        original = settings.read_bytes()
        results = un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills])
        self.assertEqual([row['status'] for row in results], ['removed', 'removed'])
        self.assertEqual(settings.with_suffix('.json.bak').read_bytes(), original)
        self.assertEqual(json.loads(settings.read_text())['permissions']['allow'], ['Bash(custom)'])
        for name in ('one', 'two'):
            self.assertFalse((self.skills / name).exists())

    def test_public_apply_aliases_remove_once(self):
        for index, selection in enumerate(({"apply_changes": True}, {"confirm": True},
                                           {"apply_changes": True, "confirm": True})):
            addon_id, name = f"alias-{index}", f"alias-skill-{index}"
            self._install(addon_id, [name])
            with self.subTest(selection=selection):
                self.assertEqual(self._uninstall(addon_id, **selection)["status"], "removed")
                self.assertFalse(os.path.lexists(self.skills / name))
                self.assertFalse((self.addons / f"{addon_id}.json").exists())

    def test_cli_default_and_positive_aliases_apply_removal(self):
        for index, flags in enumerate(([], ["--apply"], ["--confirm"], ["--apply", "--confirm"])):
            addon_id, name = f"cli-{index}", f"cli-skill-{index}"
            self._install(addon_id, [name])
            with self.subTest(flags=flags), contextlib.redirect_stdout(io.StringIO()) as output:
                status = un.main(["--addons-dir", str(self.addons), "--skills-dir", str(self.skills),
                                  "--addon-id", addon_id, *flags])
                self.assertEqual(status, 0)
                self.assertEqual(json.loads(output.getvalue())["status"], "removed")
                self.assertFalse(os.path.lexists(self.skills / name))

    def test_apply_aliases_preview_without_changing_recorded_target(self):
        self._install("alpha", ["one"])
        original = (self.addons / "alpha.json").read_bytes()
        for selection in ({"apply_changes": False}, {"confirm": False},
                          {"apply_changes": False, "confirm": False}):
            with self.subTest(selection=selection):
                self.assertEqual(self._uninstall("alpha", **selection)["status"], "dry-run")
                self.assertTrue(os.path.lexists(self.skills / "one"))
                self.assertEqual((self.addons / "alpha.json").read_bytes(), original)
                self.assertFalse(self._marker("alpha").exists())

    def test_conflicting_apply_aliases_preserve_even_a_leftover_marker(self):
        self.addons.mkdir(parents=True)
        marker = self._marker("alpha")
        marker.write_text("leftover", encoding="utf-8")
        for operation in (lambda **kw: self._uninstall("alpha", **kw),
                          lambda **kw: un.resume_pending(addons_dir=self.addons,
                              allowed_roots=[self.skills], **kw)):
            with self.assertRaisesRegex(ValueError, "apply_changes.*confirm.*conflict"):
                operation(apply_changes=False, confirm=True)
            self.assertEqual(marker.read_text(encoding="utf-8"), "leftover")

    def test_cli_dry_run_wins_over_both_positive_aliases(self):
        self._install("alpha", ["one"])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = un.main(["--addons-dir", str(self.addons), "--skills-dir", str(self.skills),
                              "--addon-id", "alpha", "--apply", "--confirm", "--dry-run"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "dry-run")
        self.assertTrue(os.path.lexists(self.skills / "one"))

    def test_removes_recorded_targets_and_sidecar_last(self):
        self._install("alpha", ["one", "two"])
        result = self._uninstall("alpha")
        self.assertEqual(result["status"], "removed")
        self.assertFalse(os.path.lexists(self.skills / "one"))
        self.assertFalse(os.path.lexists(self.skills / "two"))
        self.assertFalse((self.addons / "alpha.json").exists())  # sidecar deleted last
        self.assertFalse(self._marker("alpha").exists())  # removal marker cleaned

    def test_other_addon_untouched(self):
        self._install("alpha", ["a1"])
        self._install("beta", ["b1"])
        self._uninstall("alpha")
        self.assertFalse(os.path.lexists(self.skills / "a1"))
        self.assertTrue(os.path.lexists(self.skills / "b1"))  # beta intact
        self.assertTrue((self.addons / "beta.json").exists())
        self.assertEqual(reg.read_all_ids(addons_dir=self.addons), ["beta"])

    def test_removes_only_via_symlink_not_target(self):
        modes = self._install("alpha", ["one"])
        if modes["one"] == "copy":
            self.skipTest("directory link capability is required")
        src = self.src / "alpha" / "one"
        self._uninstall("alpha")
        self.assertFalse(os.path.lexists(self.skills / "one"))
        self.assertTrue(src.exists())  # link target (addon source) NOT deleted

    def test_drifted_copy_target_is_preserved(self):
        # copy-mode target whose content drifted since install -> hash mismatch -> must NOT be removed (real content could be lost). Drift protection stays in force for any modified managed target (copy or re-pointed symlink): a drifted target is preserved as manual-review, not removed.
        self.skills.mkdir(parents=True, exist_ok=True)
        dest = self.skills / "one"
        dest.mkdir()
        (dest / "f.txt").write_text("original", encoding="utf-8")
        recorded = hash_utils.hash_target(str(dest), "copy")
        (dest / "f.txt").write_text("user-edited", encoding="utf-8")  # drift
        self._write_sidecar("alpha", [{
            "kind": "skill", "name": "one", "target": str(dest), "ownership": "addon",
            "install_mode": "copy", "content_hash": recorded, "marker": "", "metadata": {}}])
        result = self._uninstall("alpha")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(os.path.lexists(dest))  # preserved
        self.assertTrue((self.addons / "alpha.json").exists())  # sidecar kept (not fully removed)
        review = [i for i in result["items"] if i["action"] == "manual-review"]
        self.assertEqual([i["name"] for i in review], ["one"])

    def test_missing_target_marked_missing(self):
        self._install("alpha", ["one", "gone"])
        un._symlink_safe_remove(self.skills / "gone")  # remove one target out of band
        result = self._uninstall("alpha")
        self.assertEqual(result["status"], "removed")  # missing is not a block
        actions = {i["name"]: i["action"] for i in result["items"]}
        self.assertEqual(actions["gone"], "missing")
        self.assertEqual(actions["one"], "removed")

    def test_prunes_install_state_after_removal(self):
        self._install("alpha", ["one"])
        state = self.root / ".ghost-alice" / "install-state" / "claude.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"targets": [
            {"target_name": "one", "addon_id": "alpha", "dest_path": str(self.skills / "one")},
            {"target_name": "task-router", "dest_path": "/d/tr"},
        ]}), encoding="utf-8")
        self._uninstall("alpha")
        names = {t["target_name"] for t in json.loads(state.read_text())["targets"]}
        self.assertEqual(names, {"task-router"})  # alpha pruned from install-state, core kept

    def test_prunes_pending_snapshot_and_deleted_manifest_entries_after_copy_mode_addon_removal(self):
        self.skills.mkdir(parents=True, exist_ok=True)
        dest = self.skills / "copy-addon-skill"
        adapters = dest / "adapters"
        adapters.mkdir(parents=True)
        skill_file = dest / "SKILL.md"
        adapter_file = adapters / "adapter.py"
        skill_file.write_text("skill\n", encoding="utf-8")
        adapter_file.write_text("adapter\n", encoding="utf-8")
        recorded = hash_utils.hash_target(str(dest), "copy")
        self._write_sidecar("copy-addon", [{
            "kind": "skill", "name": "copy-addon-skill", "target": str(dest), "ownership": "addon",
            "install_mode": "copy", "content_hash": recorded, "marker": "", "metadata": {}}])
        pending_dir = self.root / ".ghost-alice" / "pending-merges" / "claude"
        snapshot_path = pending_dir / "snapshot.json"
        manifest_path = pending_dir / "manifest.json"
        capture_snapshot(snapshot_path, [skill_file, adapter_file], "claude", skills_dir=self.skills)
        manifest_path.write_text(json.dumps({
            "version": 1,
            "platform": "claude",
            "entries": [
                {
                    "id": "pending-copy-addon-skill",
                    "platform": "claude",
                    "skill": "copy-addon-skill",
                    "source_path": str(skill_file),
                    "backup_path": None,
                    "snapshot_hash": "old",
                    "current_hash": None,
                    "change_kind": "deleted",
                    "relative_path": "copy-addon-skill/SKILL.md",
                    "decided": False,
                    "decision": None,
                    "created_at": "t",
                },
                {
                    "id": "pending-user-skill",
                    "platform": "claude",
                    "skill": "user-skill",
                    "source_path": str(self.skills / "user-skill" / "SKILL.md"),
                    "backup_path": None,
                    "snapshot_hash": "old",
                    "current_hash": None,
                    "change_kind": "deleted",
                    "relative_path": "user-skill/SKILL.md",
                    "decided": False,
                    "decision": None,
                    "created_at": "t",
                },
            ],
        }), encoding="utf-8")

        result = self._uninstall("copy-addon")
        snapshot = load_snapshot(snapshot_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "removed")
        self.assertFalse(any("copy-addon-skill" in path for path in snapshot["files"]))
        self.assertFalse(any("copy-addon-skill" in path for path in snapshot["file_records"]))
        entries = {entry["id"]: entry for entry in manifest["entries"]}
        self.assertTrue(entries["pending-copy-addon-skill"]["decided"])
        self.assertEqual(entries["pending-copy-addon-skill"]["decision"], "addon-uninstalled")
        self.assertFalse(entries["pending-user-skill"]["decided"])

    @unittest.skipIf(file_lock.fcntl is None, "this platform has no flock")
    def test_pending_deletion_decision_preserves_a_concurrent_append(self):
        path = self.root / ".ghost-alice/pending-merges/claude/manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"entries": [{
            "id": "removed", "change_kind": "deleted", "decided": False,
            "source_path": str(self.skills / "pilot/SKILL.md")}]}))
        provided = [{"kind": "skill", "name": "pilot", "install_mode": "copy",
                     "target": str(self.skills / "pilot")}]
        observed, release = threading.Event(), threading.Event()
        errors = []
        read_text = Path.read_text

        def pause_after_read(candidate, *args, **kwargs):
            body = read_text(candidate, *args, **kwargs)
            if candidate == path and threading.current_thread().name == "addon-decision":
                observed.set()
                if not release.wait(5):
                    raise TimeoutError("addon decision reader was not released")
            return body

        def decide():
            try:
                un._mark_pending_manifest_deleted_entries(
                    "pilot", addons_dir=self.addons, platform="claude", provided=provided)
            except Exception as error:
                errors.append(error)

        def append(data):
            data["entries"].append({"id": "concurrent", "decided": False})
            return True

        blocked = False
        worker = threading.Thread(target=decide, name="addon-decision")
        with mock.patch.object(Path, "read_text", pause_after_read):
            worker.start()
            try:
                self.assertTrue(observed.wait(5))
                try:
                    pending_manifest.update_manifest(path, pending_manifest.read_json, append, wait=0.05)
                except file_lock.LockTimeout:
                    blocked = True
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        if blocked:
            pending_manifest.update_manifest(path, pending_manifest.read_json, append, wait=5)
        entries = pending_manifest.read_json(path)["entries"]
        self.assertEqual([entry["id"] for entry in entries], ["removed", "concurrent"])
        self.assertEqual(entries[0]["decision"], "addon-uninstalled")
        self.assertTrue(entries[0]["decided"])
        self.assertFalse(entries[1]["decided"])

    def test_failed_inventory_cleanup_keeps_addon_record_until_retry_finishes(self):
        self._install("alpha", ["one"])
        state = self.root / ".ghost-alice/install-state/claude.json"
        state.parent.mkdir(parents=True)
        valid = {"platform": "claude", "targets": [{
            "target_name": "one", "addon_id": "alpha", "dest_path": str(self.skills / "one")} ]}
        state.write_text("{malformed")
        with self.assertRaises(ValueError):
            self._uninstall("alpha")
        self.assertTrue((self.addons / "alpha.json").is_file())
        self.assertTrue((self.addons / "alpha.json.removing").is_file())
        self.assertFalse(os.path.lexists(self.skills / "one"))
        state.write_text(json.dumps(valid))
        resumed = un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills], platform="claude")
        self.assertEqual([row["status"] for row in resumed], ["removed"])
        self.assertEqual(json.loads(state.read_text())["targets"], [])
        self.assertFalse((self.addons / "alpha.json").exists())
        self.assertFalse((self.addons / "alpha.json.removing").exists())

    def test_pending_read_and_shape_failures_keep_mapping_for_removal_retry(self):
        for filename in ("snapshot.json", "manifest.json"):
            for failure in ("read", "json", "shape"):
                with self.subTest(filename=filename, failure=failure):
                    addon_id = filename.split(".")[0] + "-" + failure
                    name = "skill-" + addon_id
                    self._install(addon_id, [name])
                    path = self.root / ".ghost-alice/pending-merges/claude" / filename
                    path.parent.mkdir(parents=True, exist_ok=True)
                    for other in ("snapshot.json", "manifest.json"):
                        (path.parent / other).unlink(missing_ok=True)
                    skill_file = self.skills / name / "SKILL.md"
                    if filename == "snapshot.json":
                        capture_snapshot(path, [skill_file], "claude", skills_dir=self.skills)
                        valid = path.read_bytes()
                    else:
                        valid = json.dumps({"entries": [{"id": name, "source_path": str(skill_file),
                            "change_kind": "deleted", "decided": False}]}).encode()
                        path.write_bytes(valid)
                    if failure == "json":
                        path.write_bytes(b"{malformed")
                    elif failure == "shape":
                        path.write_bytes(b'[]')
                    retained = path.read_bytes()
                    sidecar = self.addons / (addon_id + ".json")
                    sidecar_before = sidecar.read_bytes()
                    read_text = Path.read_text

                    def read(candidate, *args, **kwargs):
                        if failure == "read" and candidate == path:
                            raise PermissionError("temporary pending read failure")
                        return read_text(candidate, *args, **kwargs)

                    from installer import runner
                    subprocess_failure = (mock.patch.object(runner, 'run_tool', return_value=runner.ToolResult(
                        1, '', 'temporary pending read failure'))
                        if filename == 'snapshot.json' and failure == 'read' else contextlib.nullcontext())
                    with subprocess_failure, mock.patch.object(Path, "read_text", read):
                        with self.assertRaises((OSError, ValueError)):
                            self._uninstall(addon_id)
                    self.assertEqual(sidecar.read_bytes(), sidecar_before)
                    self.assertTrue(self._marker(addon_id).exists())
                    self.assertEqual(path.read_bytes(), retained)
                    path.write_bytes(valid)
                    resumed = self._uninstall(addon_id)
                    self.assertEqual(resumed["status"], "removed")
                    current = json.loads(path.read_bytes())
                    if filename == "snapshot.json":
                        self.assertEqual(current["files"], {})
                        self.assertEqual(current["file_records"], {})
                    else:
                        self.assertEqual(current["entries"][0]["decision"], "addon-uninstalled")
                        self.assertTrue(current["entries"][0]["decided"])
                    self.assertFalse(sidecar.exists())
                    self.assertFalse(self._marker(addon_id).exists())

    def test_non_skill_target_relative_name_does_not_decide_skill_pending_entry(self):
        self.skills.mkdir(parents=True, exist_ok=True)
        commands = self.root / ".claude" / "commands"
        resources = self.root / ".ghost-alice" / "resources" / "claude"
        commands.mkdir(parents=True, exist_ok=True)
        resources.mkdir(parents=True, exist_ok=True)
        command_target = commands / "task-router.md"
        command_target.write_text("command\n", encoding="utf-8")
        skill_file = self.skills / "task-router" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("skill\n", encoding="utf-8")
        self._write_sidecar("command-addon", [{
            "kind": "command",
            "name": "task-router",
            "target": str(command_target),
            "ownership": "addon",
            "install_mode": "copy",
            "content_hash": hash_utils.hash_target(str(command_target), "copy"),
            "marker": "",
            "metadata": {},
        }])
        pending_dir = self.root / ".ghost-alice" / "pending-merges" / "claude"
        manifest_path = pending_dir / "manifest.json"
        pending_dir.mkdir(parents=True)
        manifest_path.write_text(json.dumps({
            "version": 1,
            "platform": "claude",
            "entries": [
                {
                    "id": "pending-skill-task-router",
                    "platform": "claude",
                    "skill": "task-router",
                    "source_path": str(skill_file),
                    "backup_path": None,
                    "snapshot_hash": "old",
                    "current_hash": None,
                    "change_kind": "deleted",
                    "relative_path": "task-router/SKILL.md",
                    "decided": False,
                    "decision": None,
                    "created_at": "t",
                },
            ],
        }), encoding="utf-8")

        result = self._uninstall(
            "command-addon",
            allowed_roots=[self.skills, commands, resources],
        )
        entry = json.loads(manifest_path.read_text(encoding="utf-8"))["entries"][0]

        self.assertEqual(result["status"], "removed")
        self.assertFalse(command_target.exists())
        self.assertFalse(entry["decided"])
        self.assertIsNone(entry["decision"])

    def test_dry_run_removes_nothing(self):
        self._install("alpha", ["one"])
        result = self._uninstall("alpha", apply_changes=False)
        self.assertEqual(result["status"], "dry-run")
        self.assertTrue(os.path.lexists(self.skills / "one"))
        self.assertTrue((self.addons / "alpha.json").exists())
        self.assertFalse(self._marker("alpha").exists())  # dry-run persists no marker


def _with_old_adapter_binding(command: str) -> str:
    """The registration an older installer wrote: its binding carries the adapter key this Core rejects."""
    outer = shlex.split(command, comments=True)
    encoded = outer[outer.index("--binding-b64") + 1]
    binding = json.loads(base64.urlsafe_b64decode(encoded))
    binding["adapter"] = {"adapter_id": "demo-addon", "runner_id": "adapter-demo-addon-stop", "script": "/x/adapters/stop.py",
                          "content_hash": "0" * 64, "event": "Stop"}
    return command.replace(encoded, install_destination.encode_binding(binding))


class ExecutableDetachmentOrderTest(UninstallTestBase):
    """Detach real temporary hook registrations before changing addon files."""

    def setUp(self):
        super().setUp()
        self.settings = self.root / ".claude" / "settings.json"
        self.settings.parent.mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.settings.parent)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self._install("demo-addon", ["pilot-skill"])
        record = reg.read_record("demo-addon", addons_dir=self.addons)
        record["hooks"] = [{"hook_id": "obs", "event": "post_tool_use"}]
        # An older installer recorded an adapter hook under the retired kind; its registration must go too.
        record["provided"].append({
            "kind": "adapter", "name": "demo-addon",
            "target": str(self.skills / "pilot-skill" / "adapters" / "stop.py"),
            "ownership": "addon", "install_mode": "copy", "content_hash": "recorded",
            "marker": "[adapter:demo-addon] stop", "metadata": {"hook_id": "stop"},
        })
        reg.write_record(record, addons_dir=self.addons)
        observer = install_hooks._hook_runner_command_entry(
            "obs", "echo observer", "[addon:demo-addon] obs", platform_key="claude",
        )
        retired_script = self.src / "demo-addon" / "pilot-skill" / "adapters" / "stop.py"
        retired_script.parent.mkdir(parents=True)
        retired_script.write_text("# fixture adapter\n", encoding="utf-8")
        retired = install_hooks._hook_runner_command_entry(
            "adapter-demo-addon-stop", f'{sys.executable} "{retired_script}"', "[adapter:demo-addon] stop", platform_key="claude",
        )
        retired["hooks"][0]["command"] = _with_old_adapter_binding(retired["hooks"][0]["command"])
        self.retained_commands = ["echo core-completion # [completion]", "echo user-hook"]
        self.settings.write_text(json.dumps({"hooks": {
            "PostToolUse": [observer],
            "Stop": [retired, {"hooks": [{"type": "command", "command": c}
                                         for c in self.retained_commands]}],
        }}), encoding="utf-8")

    def _assert_detached(self):
        settings = json.loads(self.settings.read_text(encoding="utf-8"))
        commands = [hook["command"] for groups in settings["hooks"].values()
                    for group in groups for hook in group.get("hooks", [])]
        self.assertFalse(any("[addon:demo-addon]" in c or "[adapter:demo-addon]" in c for c in commands))
        for command in self.retained_commands:
            self.assertIn(command, commands)

    def test_other_owner_pending_preserves_settings_and_selected_addon_files(self):
        import addon_skill_permissions as permissions
        settings = json.loads(self.settings.read_text())
        settings['permissions'] = {'allow': ['Skill(task-router)']}
        self.settings.write_text(json.dumps(settings))
        permissions.record_install(self.settings, [], [], ['Skill(task-router)'], core_names=['task-router'])
        def fail(*args, **kwargs):
            raise OSError('before settings commit')
        with self.assertRaises(OSError):
            permissions.detach_core(['task-router'], self.settings, settings, fail)
        original = self.settings.read_bytes()
        receipt = permissions._state_path().read_bytes()
        result = self._uninstall('demo-addon')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertEqual(permissions._state_path().read_bytes(), receipt)
        self.assertTrue(os.path.lexists(self.skills / 'pilot-skill'))

    def test_own_pending_finishes_before_hook_changes(self):
        import addon_skill_permissions as permissions
        from types import SimpleNamespace
        settings = json.loads(self.settings.read_text())
        settings['permissions'] = {'allow': ['Skill(pilot-skill)']}
        self.settings.write_text(json.dumps(settings))
        permissions.record_install(self.settings, [SimpleNamespace(addon_id='demo-addon', name='pilot-skill')],
                                   [], ['Skill(pilot-skill)'])
        record = reg.read_record('demo-addon', addons_dir=self.addons)
        def fail(*args, **kwargs):
            raise OSError('before settings commit')
        with self.assertRaises(OSError):
            permissions.detach(record, self.settings, settings, set(), fail)
        result = self._uninstall('demo-addon')
        self.assertEqual(result['status'], 'removed')
        self.assertFalse(permissions._state_path().exists())
        self.assertEqual(json.loads(self.settings.read_text())['permissions']['allow'], [])
        self._assert_detached()

    def test_hooks_are_detached_before_first_target_deletion(self):
        remove = un._symlink_safe_remove
        observed = []

        def observe_deletion(path):
            self._assert_detached()
            self.assertTrue(self._marker("demo-addon").exists())
            self.assertTrue((self.addons / "demo-addon.json").exists())
            observed.append(path)
            return remove(path)

        with mock.patch.object(un, "_symlink_safe_remove", side_effect=observe_deletion):
            result = self._uninstall("demo-addon")
        self.assertEqual(result["status"], "removed")
        self.assertEqual(len(observed), 1)

    def test_failed_hook_detachment_preserves_files_for_resume(self):
        with mock.patch.object(install_hooks, "remove_addon_hook", side_effect=OSError("config write failed")):
            with self.assertRaisesRegex(OSError, "config write failed"):
                self._uninstall("demo-addon")
        self.assertTrue(os.path.lexists(self.skills / "pilot-skill"))
        self.assertTrue(self._marker("demo-addon").exists())
        self.assertTrue((self.addons / "demo-addon.json").exists())
        resumed = un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills], platform="claude")
        self.assertEqual([r["status"] for r in resumed], ["removed"])
        self._assert_detached()

    def test_missing_hook_object_finishes_removal_and_clears_marker(self):
        self.settings.write_text('{"theme":"dark","permissions":{"allow":["Read"]}}', encoding='utf-8')
        original = self.settings.read_bytes()
        remove = un._symlink_safe_remove
        observed = []

        def observe_deletion(path):
            self.assertTrue(self._marker('demo-addon').is_file())
            self.assertEqual(self.settings.read_bytes(), original)
            observed.append(path)
            return remove(path)

        with mock.patch.object(un, '_symlink_safe_remove', side_effect=observe_deletion):
            result = self._uninstall('demo-addon')
        self.assertEqual(result['status'], 'removed')
        self.assertEqual(len(observed), 1)
        self.assertFalse(os.path.lexists(self.skills / 'pilot-skill'))
        self.assertFalse(self._marker('demo-addon').exists())
        self.assertFalse((self.addons / 'demo-addon.json').exists())
        self.assertEqual(un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills], platform='claude'), [])
        self.assertEqual(self.settings.read_bytes(), original)

    def test_file_removal_failure_leaves_hooks_detached_and_can_resume(self):
        with mock.patch.object(un, "_symlink_safe_remove", side_effect=OSError("file removal interrupted")):
            with self.assertRaisesRegex(OSError, "file removal interrupted"):
                self._uninstall("demo-addon")
        self._assert_detached()
        self.assertTrue(os.path.lexists(self.skills / "pilot-skill"))
        self.assertTrue(self._marker("demo-addon").exists())
        resumed = un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills], platform="claude")
        self.assertEqual([r["status"] for r in resumed], ["removed"])

    def _public_failure_report(self, *, record_failure, resume, unreadable_record=None, unreadable_target=False):
        import content_digest
        import installed_payload
        import install_state_writer
        from installer import locations

        ghost = self.root / '.ghost-alice'
        target = self.skills / 'pilot-skill'
        state = ghost / 'install-state/claude.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(dict(platform='claude', targets=[dict(
            target_name='pilot-skill', dest_path=str(target), addon_id='demo-addon')])) )
        entry = dict(name='pilot-skill', kind='skill', provider='addon:demo-addon',
                     baseline='recorded', used_by=['claude'])
        if self._install_modes[target] in {'symlink', 'junction'}:
            entry.update(mode='link', link_target=content_digest.link_target(target))
        else:
            entry.update(mode='copy', files=content_digest.tree(target))
        installed_payload.path(ghost).write_text(json.dumps(dict(schema_version=1, targets={str(target): entry})))
        self.assertEqual(locations.record_root(), ghost)
        self.assertEqual(locations.skills_dir('claude'), self.skills)
        for path in (target.parent, state, self.settings, installed_payload.path(ghost)):
            install_destination.assert_mutation_path(path)
        if resume:
            record = reg.read_record('demo-addon', addons_dir=self.addons)
            un._write_marker(self._marker('demo-addon'), 'demo-addon', record['provided'])
        args = ['--addons-dir', str(self.addons), '--platform', 'claude', '--skills-dir', str(self.skills),
                *(['--resume-pending'] if resume else ['--addon-id', 'demo-addon'])]

        def invoke():
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = un.main(args)
            raw = output.getvalue()
            payload = json.loads(raw[raw.index('{'):])
            return code, payload['resumed'][0] if resume else payload

        primary = OSError('requested removal interrupted')
        failed = False
        unreadable = (target if unreadable_target else self._marker('demo-addon') if unreadable_record == 'marker' else
                      reg.sidecar_path('demo-addon', addons_dir=self.addons) if unreadable_record == 'sidecar' else None)

        def interrupted(*args, **kwargs):
            nonlocal failed
            failed = True
            raise primary

        def observe_metadata(operation):
            def observed(path, *args, **kwargs):
                if failed and str(path) == str(unreadable):
                    raise PermissionError('removal state lookup denied')
                return operation(path, *args, **kwargs)
            return observed

        owner, operation = (install_state_writer, 'remove_targets') if record_failure else (un, '_symlink_safe_remove')
        with mock.patch.object(owner, operation, side_effect=interrupted), \
                mock.patch.object(os, 'stat', observe_metadata(os.stat)), \
                mock.patch.object(os, 'lstat', observe_metadata(os.lstat)):
            code, result = invoke()
        self.assertEqual(code, 2)
        self._assert_detached()
        self.assertEqual(os.path.lexists(target), not record_failure)
        self.assertTrue(self._marker('demo-addon').exists())
        self.assertTrue(reg.sidecar_path('demo-addon', addons_dir=self.addons).exists())
        self.assertEqual([row['dest_path'] for row in json.loads(state.read_text())['targets']], [str(target)])
        self.assertEqual(str(target) in installed_payload.read(ghost)['targets'], not record_failure)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['reason'], str(primary))
        self.assertTrue(result['items'], 'the public failure must retain the observed target and hook results')
        item = next(row for row in result['items'] if row.get('kind') == 'addon-target')
        self.assertEqual(item['target'], str(target))
        self.assertEqual(item['file_state'], 'unknown' if unreadable_target else 'absent' if record_failure else 'present')
        self.assertEqual(item['detail'], 'requested removal interrupted')
        if unreadable_target:
            self.assertEqual(item['file_state_error'], 'removal state lookup denied')
        self.assertEqual({row['action'] for row in result['items'] if row.get('kind') in {'hook', 'adapter'}}, {'removed'})
        expected = {str(reg.sidecar_path('demo-addon', addons_dir=self.addons)), str(self._marker('demo-addon'))}
        self.assertEqual(set(result['retry_records']), expected - ({str(unreadable)} if unreadable else set()))
        if unreadable_record is not None:
            self.assertEqual(result['retry_record_errors'], {str(unreadable): 'removal state lookup denied'})
            self.assertIs(result['has_pending_marker'], None if unreadable_record == 'marker' else True)

        code, result = invoke()
        self.assertEqual((code, result['status']), (0, 'removed'))
        self._assert_detached()
        self.assertFalse(os.path.lexists(target))
        self.assertFalse(self._marker('demo-addon').exists())
        self.assertFalse(reg.sidecar_path('demo-addon', addons_dir=self.addons).exists())
        self.assertEqual(json.loads(state.read_text())['targets'], [])
        self.assertNotIn(str(target), installed_payload.read(ghost)['targets'])

    def test_cli_file_failure_reports_detached_hooks_remaining_file_and_retry_records(self):
        self._public_failure_report(record_failure=False, resume=False)

    def test_cli_record_failure_reports_detached_hooks_absent_file_and_retry_records(self):
        self._public_failure_report(record_failure=True, resume=False)

    def test_resume_file_failure_reports_detached_hooks_remaining_file_and_retry_records(self):
        self._public_failure_report(record_failure=False, resume=True)

    def test_resume_record_failure_reports_detached_hooks_absent_file_and_retry_records(self):
        self._public_failure_report(record_failure=True, resume=True)

    def test_cli_record_failure_keeps_its_result_when_marker_lookup_is_denied(self):
        self._public_failure_report(record_failure=True, resume=False, unreadable_record='marker')

    def test_resume_file_failure_keeps_its_result_when_sidecar_lookup_is_denied(self):
        self._public_failure_report(record_failure=False, resume=True, unreadable_record='sidecar')

    def test_cli_file_failure_reports_unknown_when_target_lookup_is_denied(self):
        self._public_failure_report(record_failure=False, resume=False, unreadable_target=True)

    def test_resume_record_failure_reports_unknown_when_target_lookup_is_denied(self):
        self._public_failure_report(record_failure=True, resume=True, unreadable_target=True)

    def test_direct_api_keeps_original_exception_when_all_removal_state_lookups_fail(self):
        primary = OSError('requested removal interrupted')
        records = {self._marker('demo-addon'), reg.sidecar_path('demo-addon', addons_dir=self.addons)}
        target_path = self.skills / 'pilot-skill'
        denied_paths = {str(path) for path in records | {target_path}}
        failed = False

        def interrupted(*args):
            nonlocal failed
            failed = True
            raise primary

        def observe_metadata(operation):
            def observed(path, *args, **kwargs):
                if failed and str(path) in denied_paths:
                    raise PermissionError('removal state lookup denied')
                return operation(path, *args, **kwargs)
            return observed

        with mock.patch.object(un, '_symlink_safe_remove', side_effect=interrupted), \
                mock.patch.object(os, 'stat', observe_metadata(os.stat)), \
                mock.patch.object(os, 'lstat', observe_metadata(os.lstat)), self.assertRaises(OSError) as captured:
            self._uninstall('demo-addon')
        self.assertIs(captured.exception, primary)
        result = captured.exception.uninstall_result
        self.assertEqual((result['status'], result['reason']), ('error', str(primary)))
        self.assertEqual(result['retry_records'], [])
        self.assertEqual(result['retry_record_errors'], {str(path): 'removal state lookup denied' for path in records})
        self.assertIsNone(result['has_pending_marker'])
        target = next(item for item in result['items'] if item.get('kind') == 'addon-target')
        self.assertEqual(target['file_state'], 'unknown')
        self.assertEqual(target['file_state_error'], 'removal state lookup denied')
        self.assertEqual({item['action'] for item in result['items'] if item.get('kind') in {'hook', 'adapter'}}, {'removed'})
        json.dumps(result)
        self._assert_detached()
        self.assertTrue(os.path.lexists(self.skills / 'pilot-skill'))
        for path in records:
            self.assertTrue(path.exists())
        self.assertEqual(self._uninstall('demo-addon')['status'], 'removed')
        self.assertFalse(os.path.lexists(self.skills / 'pilot-skill'))
        for path in records:
            self.assertFalse(path.exists())

    def test_record_failure_reports_absent_when_target_parent_became_a_regular_file(self):
        import install_state_writer
        primary = OSError('inventory cleanup interrupted')

        def interrupted(*args, **kwargs):
            self.skills.rmdir()
            self.skills.write_text('replacement parent')
            raise primary

        with mock.patch.object(install_state_writer, 'remove_targets', side_effect=interrupted), \
                self.assertRaises(OSError) as captured:
            self._uninstall('demo-addon')
        self.assertIs(captured.exception, primary)
        item = next(row for row in primary.uninstall_result['items'] if row.get('kind') == 'addon-target')
        self.assertEqual((item['file_state'], item['detail']), ('absent', str(primary)))
        self.assertNotIn('file_state_error', item)
        self.assertEqual(self.skills.read_text(), 'replacement parent')
        self._assert_detached()
        self.assertTrue(self._marker('demo-addon').exists())
        self.skills.unlink()
        self.skills.mkdir()
        self.assertEqual(self._uninstall('demo-addon')['status'], 'removed')
        self.assertFalse(self._marker('demo-addon').exists())

    def _public_finalization_failure(self, *, marker_failure):
        marker = self._marker('demo-addon')
        sidecar = reg.sidecar_path('demo-addon', addons_dir=self.addons)
        args = ['--addons-dir', str(self.addons), '--platform', 'claude', '--skills-dir', str(self.skills),
                '--addon-id', 'demo-addon']
        unlink = Path.unlink

        def deny_marker(path, *args, **kwargs):
            if path == marker:
                raise OSError('removal marker locked')
            return unlink(path, *args, **kwargs)

        denied = (mock.patch.object(Path, 'unlink', deny_marker) if marker_failure else
                  mock.patch.object(reg, 'remove_record', side_effect=OSError('sidecar locked')))
        output = io.StringIO()
        with denied, contextlib.redirect_stdout(output):
            code = un.main(args)
        raw = output.getvalue()
        result = json.loads(raw[raw.index('{'):])
        self.assertEqual((code, result['status']), (2, 'error'))
        self._assert_detached()
        self.assertFalse(os.path.lexists(self.skills / 'pilot-skill'))
        self.assertEqual(sidecar.exists(), not marker_failure)
        self.assertTrue(marker.exists())
        self.assertEqual(set(result['retry_records']), {str(marker)} if marker_failure else {str(sidecar), str(marker)})
        self.assertEqual(next(item['action'] for item in result['items'] if item.get('kind') == 'addon-target'), 'removed')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(un.main(args), 0)
        self.assertFalse(sidecar.exists())
        self.assertFalse(marker.exists())
        self._assert_detached()

    def test_cli_sidecar_failure_reports_removed_files_and_remaining_retry_records(self):
        self._public_finalization_failure(marker_failure=False)

    def test_cli_marker_failure_reports_removed_files_and_only_the_remaining_marker(self):
        self._public_finalization_failure(marker_failure=True)

    def test_dry_run_keeps_registrations_files_and_sidecar(self):
        original = self.settings.read_bytes()
        result = self._uninstall("demo-addon", apply_changes=False)
        self.assertEqual(result["status"], "dry-run")
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertTrue(os.path.lexists(self.skills / "pilot-skill"))
        self.assertTrue((self.addons / "demo-addon.json").exists())
        self.assertFalse(self._marker("demo-addon").exists())


class ResumeTest(UninstallTestBase):
    def test_resume_finishes_after_crash(self):
        self._install("alpha", ["one", "two"])
        # simulate a crash AFTER the marker was written and one target removed, but BEFORE the sidecar was deleted.
        self._marker("alpha").write_text('{"addon_id":"alpha","stage":"removing"}', encoding="utf-8")
        un._symlink_safe_remove(self.skills / "one")
        results = un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills], platform="claude")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "removed")
        self.assertFalse(os.path.lexists(self.skills / "two"))
        self.assertFalse((self.addons / "alpha.json").exists())
        self.assertFalse(self._marker("alpha").exists())

    def test_resume_when_sidecar_already_gone_clears_marker(self):
        self.addons.mkdir(parents=True, exist_ok=True)
        self._marker("ghost").write_text("{}", encoding="utf-8")  # marker but no sidecar
        results = un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills], platform="claude")
        self.assertEqual(results[0]["status"], "resumed-noop")
        self.assertFalse(self._marker("ghost").exists())

    def test_preview_preserves_marker_when_sidecar_is_gone(self):
        self.addons.mkdir(parents=True, exist_ok=True)
        marker = self._marker("ghost")
        original = b'{"addon_id":"ghost","stage":"removing"}\n'
        for selection in ({"apply_changes": False}, {"confirm": False},
                          {"apply_changes": False, "confirm": False}):
            with self.subTest(selection=selection):
                marker.write_bytes(original)
                result = self._uninstall("ghost", **selection)
                self.assertEqual(marker.read_bytes() if marker.exists() else None, original)
                self.assertEqual(result["status"], "dry-run")
                self.assertTrue(result["has_pending_marker"])
                resumed = un.resume_pending(addons_dir=self.addons,
                    allowed_roots=[self.skills], platform="claude", **selection)
                self.assertEqual(resumed, [result])
                self.assertEqual(marker.read_bytes(), original)
                self.assertEqual(self._uninstall("ghost")["status"], "resumed-noop")
                self.assertFalse(marker.exists())

    def test_cli_resume_preview_keeps_marker_available_for_apply(self):
        self.addons.mkdir(parents=True, exist_ok=True)
        marker = self._marker("ghost")
        original = b'{"addon_id":"ghost","stage":"removing"}\n'
        for flags in (["--dry-run"], ["--apply", "--confirm", "--dry-run"]):
            with self.subTest(flags=flags), contextlib.redirect_stdout(io.StringIO()) as output:
                marker.write_bytes(original)
                status = un.main(["--resume-pending", "--addons-dir", str(self.addons),
                                  "--skills-dir", str(self.skills), *flags])
                self.assertEqual(status, 0)
                self.assertEqual(marker.read_bytes() if marker.exists() else None, original)
                self.assertEqual(json.loads(output.getvalue())["resumed"], [{
                    "addon_id": "ghost", "status": "dry-run", "items": [],
                    "has_pending_marker": True,
                }])
                applied = un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills])
                self.assertEqual(applied[0]["status"], "resumed-noop")
                self.assertFalse(marker.exists())
                self.assertEqual(un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills]), [])

    def test_resume_ignores_non_addon_id_marker(self):
        (self.addons).mkdir(parents=True, exist_ok=True)
        (self.addons / "Not-An-Id.json.removing").write_text("{}", encoding="utf-8")
        results = un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills], platform="claude")
        self.assertEqual(results, [])


class SecurityTest(UninstallTestBase):
    def test_target_escaping_allowed_roots_is_refused(self):
        outside = self.root / "outside" / "victim"
        outside.mkdir(parents=True)
        (outside / "f").write_text("x", encoding="utf-8")
        self._write_sidecar("evil", [{
            "kind": "skill", "name": "v", "target": str(outside), "ownership": "addon",
            "install_mode": "copy", "content_hash": hash_utils.hash_target(str(outside), "copy"),
            "marker": "", "metadata": {}}])
        result = self._uninstall("evil")  # allowed_roots = [self.skills]
        self.assertEqual(result["status"], "partial")
        self.assertEqual([i["action"] for i in result["items"]], ["refused"])
        self.assertTrue(outside.exists())  # NOT deleted (escaped containment)
        self.assertTrue((self.addons / "evil.json").exists())  # sidecar kept

    def test_install_mode_missing_never_deletes(self):
        self.skills.mkdir(parents=True, exist_ok=True)
        dest = self.skills / "x"
        self.src.mkdir()
        self._provision_target(self.src, dest)
        # tampered: install_mode 'missing' + content_hash 'missing' would match the hash gate
        self._write_sidecar("alpha", [{
            "kind": "skill", "name": "x", "target": str(dest), "ownership": "addon",
            "install_mode": "missing", "content_hash": "missing", "marker": "", "metadata": {}}])
        result = self._uninstall("alpha")
        self.assertEqual([i["action"] for i in result["items"]], ["missing"])
        self.assertTrue(os.path.lexists(dest))  # NOT deleted

    def test_empty_content_hash_fails_closed(self):
        # copy-mode target with no recorded content_hash: ownership is unprovable and there is real content to lose, so it must fail closed (preserved). (Symlink mode is exempt because unlinking the link is non-destructive.)
        self.skills.mkdir(parents=True, exist_ok=True)
        dest = self.skills / "x"
        dest.mkdir()
        (dest / "f.txt").write_text("payload", encoding="utf-8")
        self._write_sidecar("alpha", [{
            "kind": "skill", "name": "x", "target": str(dest), "ownership": "addon",
            "install_mode": "copy", "content_hash": "", "marker": "", "metadata": {}}])
        result = self._uninstall("alpha")
        self.assertEqual([i["action"] for i in result["items"]], ["manual-review"])
        self.assertTrue(os.path.lexists(dest))  # preserved (ownership unprovable)

    def test_registry_rejects_invalid_install_mode(self):
        with self.assertRaises(reg.SchemaValidationError):
            self._write_sidecar("alpha", [{
                "kind": "skill", "name": "x", "target": "/t", "ownership": "addon",
                "install_mode": "evil", "content_hash": "h", "marker": "", "metadata": {}}])


class CliTest(UninstallTestBase):
    def test_cli_addon_id_removes(self):
        self._install("alpha", ["one"])
        rc = un.main(["--addon-id", "alpha", "--addons-dir", str(self.addons),
                      "--platform", "claude", "--skills-dir", str(self.skills)])
        self.assertEqual(rc, 0)
        self.assertFalse((self.addons / "alpha.json").exists())
        self.assertFalse(os.path.lexists(self.skills / "one"))

    def test_cli_resume_pending(self):
        self._install("alpha", ["one"])
        self._marker("alpha").write_text('{"addon_id":"alpha"}', encoding="utf-8")
        rc = un.main(["--resume-pending", "--addons-dir", str(self.addons),
                      "--platform", "claude", "--skills-dir", str(self.skills)])
        self.assertEqual(rc, 0)
        self.assertFalse((self.addons / "alpha.json").exists())

    def test_cli_unknown_addon_returns_1(self):
        self.addons.mkdir(parents=True, exist_ok=True)
        rc = un.main(["--addon-id", "ghost", "--addons-dir", str(self.addons),
                      "--platform", "claude", "--skills-dir", str(self.skills)])
        self.assertEqual(rc, 1)


class UninstallScenarioGapTest(UninstallTestBase):
    """Uninstall gap scenarios (A4, A9, B2, C4, C5, partial-managed-hook-strip, H1).

    Hook operations resolve via CLAUDE_CONFIG_DIR; setUp pins it at a temp dir so
    these tests can NEVER touch the real ~/.claude/settings.json.
    """

    def setUp(self):
        super().setUp()
        claude_cfg = self.root / ".claude"
        claude_cfg.mkdir(parents=True, exist_ok=True)
        (claude_cfg / "settings.json").write_text('{"hooks": {}}', encoding="utf-8")
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(claude_cfg)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        super().tearDown()

    def _repoint(self, name):
        """Re-point an installed skill symlink at another directory -> an external change makes its recorded hash differ."""
        dest = self.skills / name
        if self._install_modes.get(dest) == "copy":
            self.skipTest("directory link capability is required")
        other = self.src / "user-dir" / name
        other.mkdir(parents=True, exist_ok=True)
        (other / "SKILL.md").write_text("user", encoding="utf-8")
        un._symlink_safe_remove(dest)
        try:
            self._install_modes[dest] = install_transaction._create_directory_link(other, dest)
        except OSError as exc:
            self.skipTest(f"directory link capability is required: {exc}")
        return dest

    # A4: a symlink with an external change is preserved as manual-review, not removed; the installer does not record the change's writer.
    def test_a4_repointed_symlink_is_preserved(self):
        self._install("alpha", ["one"])
        dest = self._repoint("one")
        result = self._uninstall("alpha")
        self.assertEqual(result["status"], "partial")
        self.assertEqual({i["name"]: i["action"] for i in result["items"]}["one"], "manual-review")
        self.assertTrue(os.path.lexists(dest))  # link preserved (not clobbered)
        self.assertTrue((self.addons / "alpha.json").exists())  # sidecar kept (partial)
        self.assertTrue(self._marker("alpha").exists())  # marker kept for retry

    # A9 (SAFETY): recorded mode symlink but live dest is a REAL directory -> never rmtree.
    def test_a9_recorded_symlink_live_real_dir_not_rmtree(self):
        self.skills.mkdir(parents=True, exist_ok=True)
        dest = self.skills / "one"
        dest.mkdir()
        (dest / "precious.txt").write_text("keep", encoding="utf-8")
        self._write_sidecar("alpha", [{
            "kind": "skill", "name": "one", "target": str(dest), "ownership": "addon",
            "install_mode": "symlink", "content_hash": "recorded-hash-does-not-match",
            "marker": "", "metadata": {}}])
        result = self._uninstall("alpha")
        self.assertEqual({i["name"]: i["action"] for i in result["items"]}["one"], "manual-review")
        self.assertTrue(dest.is_dir())  # real directory survives
        self.assertTrue((dest / "precious.txt").exists())  # content NOT rmtree'd

    # B2: mixed clean + drifted -> partial; clean removed, drifted preserved, sidecar kept.
    def test_b2_mixed_drift_is_partial(self):
        self._install("alpha", ["a-drift", "z-clean"])
        drift = self._repoint("a-drift")
        result = self._uninstall("alpha")
        self.assertEqual(result["status"], "partial")
        actions = {i["name"]: i["action"] for i in result["items"]}
        self.assertEqual(actions["z-clean"], "removed")
        self.assertEqual(actions["a-drift"], "manual-review")
        self.assertFalse(os.path.lexists(self.skills / "z-clean"))
        self.assertTrue(os.path.lexists(drift))
        self.assertEqual((drift / "SKILL.md").read_text(encoding="utf-8"), "user")
        self.assertTrue((self.addons / "alpha.json").exists())

    # C4: resume after a partial (target still blocked) stays partial, idempotent.
    def test_c4_resume_after_partial_still_blocked_is_idempotent(self):
        self._install("alpha", ["one"])
        dest = self._repoint("one")
        self.assertEqual(self._uninstall("alpha")["status"], "partial")
        self.assertTrue(self._marker("alpha").exists())
        results = un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills],
                                    platform="claude", apply_changes=True)
        self.assertEqual([r["status"] for r in results], ["partial"])
        self.assertTrue(os.path.lexists(dest))  # still preserved
        self.assertTrue((self.addons / "alpha.json").exists())

    # C5: resume completes idempotently when the addon's hook is already gone from settings.
    def test_c5_resume_with_hook_already_gone_is_idempotent(self):
        self._install("alpha", ["one"])  # clean skill
        rec = reg.read_record("alpha", addons_dir=self.addons)
        rec["hooks"] = [{"hook_id": "obs", "event": "post_tool_use", "marker": "[addon:alpha] obs"}]
        reg.write_record(rec, addons_dir=self.addons)
        # crash mid-uninstall: leftover marker, hook NOT present in settings.json
        self._marker("alpha").write_text(
            '{"addon_id":"alpha","stage":"removing","targets":["one"]}', encoding="utf-8")
        results = un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills],
                                    platform="claude", apply_changes=True)
        self.assertEqual([r["status"] for r in results], ["removed"])  # completes
        self.assertFalse((self.addons / "alpha.json").exists())  # sidecar deleted
        self.assertFalse(self._marker("alpha").exists())  # marker cleared

    # Partial-managed-hook-strip scenario: a partial uninstall must still disable the addon's executable hooks.
    def test_d2_partial_strips_managed_hooks_but_keeps_sidecar_for_retry(self):
        self._install("alpha", ["one"])
        settings_path = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
        hook_entry = install_hooks._hook_runner_command_entry(
            "addon:alpha:obs",
            f"{Path(__file__).parent / 'hook_profile_gate.py'} run obs e30=",
            "[addon:alpha] obs",
        )
        settings_path.write_text(json.dumps({"hooks": {"PostToolUse": [hook_entry]}}), encoding="utf-8")
        rec = reg.read_record("alpha", addons_dir=self.addons)
        rec["hooks"] = [{"hook_id": "obs", "event": "post_tool_use", "marker": "[addon:alpha] obs"}]
        reg.write_record(rec, addons_dir=self.addons)
        self._repoint("one")  # drift -> partial
        result = self._uninstall("alpha")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(any(i.get("kind") == "hook" and i.get("action") == "removed" for i in result["items"]),
                        msg="partial uninstall left a managed addon hook firing")
        settings_after = json.loads(settings_path.read_text(encoding="utf-8"))
        commands = [h.get("command", "") for e in settings_after["hooks"].get("PostToolUse", [])
                    for h in e.get("hooks", [])]
        self.assertFalse(any("[addon:alpha] obs" in command for command in commands),
                         msg="partial uninstall orphaned a managed addon hook")
        self.assertEqual(reg.read_record("alpha", addons_dir=self.addons).get("hooks"),
                         rec["hooks"])  # hook record retained for retry

    # H1 (ROBUSTNESS): an unreadable / future-major sidecar must not crash uninstall.
    def test_h1_unreadable_sidecar_uninstall_is_graceful(self):
        self.addons.mkdir(parents=True, exist_ok=True)
        (self.addons / "alpha.json").write_text(
            '{"schema_version": "99.0", "addon_id": "alpha"}', encoding="utf-8")
        try:
            result = self._uninstall("alpha")
        except Exception as exc:  # noqa: BLE001 - the point is it must NOT raise
            self.fail(f"uninstall crashed on an unreadable sidecar: {exc!r}")
        self.assertNotEqual(result.get("status"), "removed")  # not silently removed
        self.assertTrue((self.addons / "alpha.json").exists())  # sidecar preserved


if __name__ == "__main__":
    unittest.main()
