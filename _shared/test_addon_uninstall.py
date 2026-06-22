"""TDD for the per-addon uninstall core (plan tasks T2.3-T2.7, T2.10).

Removes ONLY the requested addon's recorded targets, symlink-safe, preserves a
target that drifted since install (user-modified), uses a two-phase
<addon_id>.json.removing intent marker, deletes the sidecar last, and resumes.

Run: python3 -m pytest _shared/test_addon_uninstall.py -q
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
sys.path.insert(0, str(REPO_ROOT / "merge-companion" / "scripts"))

import addon_registry as reg  # noqa: E402
import install_hooks  # noqa: E402
import addon_uninstall as un  # noqa: E402
import hash_utils  # noqa: E402
from snapshot import capture_snapshot, load_snapshot  # noqa: E402


class UninstallTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # platform-scoped layout (review M5): sidecars live under addons/<platform>/
        self.addons = self.root / ".ghost-alice" / "addons" / "claude"
        self.skills = self.root / ".claude" / "skills"
        self.src = self.root / "src"

    def tearDown(self):
        self._tmp.cleanup()

    def _install(self, addon_id, names):
        self.skills.mkdir(parents=True, exist_ok=True)
        provided = []
        for name in names:
            src = self.src / addon_id / name
            src.mkdir(parents=True, exist_ok=True)
            (src / "SKILL.md").write_text("x", encoding="utf-8")
            dest = self.skills / name
            dest.symlink_to(src)
            provided.append({
                "kind": "skill", "name": name, "target": str(dest), "ownership": "addon",
                "install_mode": "symlink", "content_hash": hash_utils.hash_target(str(dest), "symlink"),
                "marker": "", "metadata": {},
            })
        reg.write_record({
            "schema_version": "1.0", "addon_id": addon_id, "addon_version": "1.0.0",
            "source": str(self.src / addon_id), "platform": "claude", "owner": "addon",
            "origin": f"addon:{addon_id}", "depends_on_core": [], "min_core_version": "0.0.0",
            "installed_at": "t", "provided": provided,
        }, addons_dir=self.addons)

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
    def test_removes_recorded_targets_and_sidecar_last(self):
        self._install("alpha", ["one", "two"])
        result = self._uninstall("alpha")
        self.assertEqual(result["status"], "removed")
        self.assertFalse(os.path.lexists(self.skills / "one"))
        self.assertFalse(os.path.lexists(self.skills / "two"))
        self.assertFalse((self.addons / "alpha.json").exists())  # sidecar deleted last
        self.assertFalse(self._marker("alpha").exists())  # intent marker cleaned

    def test_other_addon_untouched(self):
        self._install("alpha", ["a1"])
        self._install("beta", ["b1"])
        self._uninstall("alpha")
        self.assertFalse(os.path.lexists(self.skills / "a1"))
        self.assertTrue(os.path.lexists(self.skills / "b1"))  # beta intact
        self.assertTrue((self.addons / "beta.json").exists())
        self.assertEqual(reg.read_all_ids(addons_dir=self.addons), ["beta"])

    def test_removes_only_via_symlink_not_target(self):
        self._install("alpha", ["one"])
        src = self.src / "alpha" / "one"
        self._uninstall("alpha")
        self.assertFalse(os.path.lexists(self.skills / "one"))
        self.assertTrue(src.exists())  # symlink target (addon source) NOT deleted

    def test_drifted_copy_target_is_preserved(self):
        # copy-mode target whose content drifted since install -> hash mismatch
        # -> must NOT be removed (real content could be lost). Drift protection
        # stays in force for any modified managed target (copy or re-pointed
        # symlink): a drifted target is preserved as manual-review, not removed.
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
        (self.skills / "gone").unlink()  # remove one target out of band
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
        result = self._uninstall("alpha", confirm=False)
        self.assertEqual(result["status"], "dry-run")
        self.assertTrue(os.path.lexists(self.skills / "one"))
        self.assertTrue((self.addons / "alpha.json").exists())
        self.assertFalse(self._marker("alpha").exists())  # dry-run persists no marker


class ResumeTest(UninstallTestBase):
    def test_resume_finishes_after_crash(self):
        self._install("alpha", ["one", "two"])
        # simulate a crash AFTER the marker was written and one target removed,
        # but BEFORE the sidecar was deleted.
        self._marker("alpha").write_text('{"addon_id":"alpha","stage":"removing"}', encoding="utf-8")
        (self.skills / "one").unlink()
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
        dest.symlink_to(self.root / "src")  # a real link on disk
        # tampered: install_mode 'missing' + content_hash 'missing' would match the hash gate
        self._write_sidecar("alpha", [{
            "kind": "skill", "name": "x", "target": str(dest), "ownership": "addon",
            "install_mode": "missing", "content_hash": "missing", "marker": "", "metadata": {}}])
        result = self._uninstall("alpha")
        self.assertEqual([i["action"] for i in result["items"]], ["missing"])
        self.assertTrue(os.path.lexists(dest))  # NOT deleted

    def test_empty_content_hash_fails_closed(self):
        # copy-mode target with no recorded content_hash: ownership is unprovable
        # and there is real content to lose, so it must fail closed (preserved).
        # (Symlink mode is exempt because unlinking the link is non-destructive.)
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
    """Approved uninstall gap scenarios (A4, A9, B2, C4, C5, partial-managed-hook-strip, H1).

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
        """Re-point an installed skill symlink at a user dir -> recorded hash drifts."""
        dest = self.skills / name
        other = self.src / "user-dir" / name
        other.mkdir(parents=True, exist_ok=True)
        (other / "SKILL.md").write_text("user", encoding="utf-8")
        dest.unlink()
        dest.symlink_to(other)
        return dest

    # A4: a user re-pointed (drifted) symlink is preserved as manual-review, not removed.
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
        self._install("alpha", ["clean", "drift"])
        self._repoint("drift")
        result = self._uninstall("alpha")
        self.assertEqual(result["status"], "partial")
        actions = {i["name"]: i["action"] for i in result["items"]}
        self.assertEqual(actions["clean"], "removed")
        self.assertEqual(actions["drift"], "manual-review")
        self.assertFalse(os.path.lexists(self.skills / "clean"))
        self.assertTrue(os.path.lexists(self.skills / "drift"))
        self.assertTrue((self.addons / "alpha.json").exists())

    # C4: resume after a partial (target still blocked) stays partial, idempotent.
    def test_c4_resume_after_partial_still_blocked_is_idempotent(self):
        self._install("alpha", ["one"])
        dest = self._repoint("one")
        self.assertEqual(self._uninstall("alpha")["status"], "partial")
        self.assertTrue(self._marker("alpha").exists())
        results = un.resume_pending(addons_dir=self.addons, allowed_roots=[self.skills],
                                    platform="claude", confirm=True)
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
                                    platform="claude", confirm=True)
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
