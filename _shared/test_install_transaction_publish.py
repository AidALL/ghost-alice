"""install_transaction publish and recover: files and the install record change in one step, a journal says how far a publish got, and an unfinished publish is restored."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SHARED = Path(__file__).resolve().parent
sys.path.insert(0, str(SHARED))
import content_digest  # noqa: E402
from context_gateway_model import GatewayServiceError  # noqa: E402
import install_lock  # noqa: E402
import install_transaction  # noqa: E402
import installed_payload  # noqa: E402


class PublishCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home = root / "home"
        self.ghost = self.home / ".ghost-alice"
        self.ghost.mkdir(parents=True)
        self.src = root / "src"
        for name in ("alpha", "beta", "_shared"):
            (self.src / name).mkdir(parents=True)
            (self.src / name / "SKILL.md").write_text(f"{name} v2\n", encoding="utf-8")
        (self.src / "_shared" / "__pycache__").mkdir()
        (self.src / "_shared" / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"cache")
        (self.src / "_shared" / ".DS_Store").write_bytes(b"finder")
        (self.src / "_shared" / "loose.pyc").write_bytes(b"cache")
        self.claude = self.home / ".claude" / "skills"
        self.codex = self.home / ".agents" / "skills"
        self.claude.mkdir(parents=True)
        self.codex.mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()
        self.lock_generation = install_lock.acquire_lock(self.ghost / "install.lock", owner="installer:install:claude:test")

    def tearDown(self):
        install_lock.release_lock(self.ghost / "install.lock", self.lock_generation)
        self.env.stop()
        self.temp.cleanup()

    def target(self, name, dest, mode, *, kind="skill", provider="core", source=None, status=None):
        target = {"name": name, "kind": kind, "provider": provider, "source": str(source or self.src / name),
                  "source_commit": "c0ffee", "dest": str(dest), "mode": mode}
        if status:
            target["status"] = status
        return target

    def request(self, host, *targets, **extra):
        return {"schema_version": 1, "host": host, "ghost_root": str(self.ghost),
                "installer": {"commit": "c0ffee", "schema_version": 1},
                "source": {"root": str(self.src), "commit": "c0ffee", "dirty": "clean", "hooks_path": "hooks"},
                "targets": list(targets), **extra}

    def old_copy(self, dest: Path, text: str = "v1\n") -> None:
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text(text, encoding="utf-8")

    def journal(self, txid: str) -> dict:
        return json.loads((self.ghost / "rollbacks" / txid / "journal.json").read_text(encoding="utf-8"))

    def record(self) -> dict:
        return installed_payload.read(self.ghost)

    def snapshot(self, *paths: Path) -> dict:
        state = {}
        for path in paths:
            if path.is_symlink():
                state[path] = ("link", os.readlink(path))
            elif path.is_dir():
                state[path] = ("dir", content_digest.tree(path))
            else:
                state[path] = ("absent",)
        return state


class PublishTests(PublishCase):
    def test_a_link_and_its_record_entry_are_published_in_one_step(self):
        dest = self.claude / "alpha"
        txid = install_transaction.publish(self.request("claude", self.target("alpha", dest, "link")))
        self.assertEqual(os.readlink(dest), str(self.src / "alpha"))
        record = self.record()
        entry = record["targets"][str(dest)]
        installed_at = entry.pop("installed_at")
        self.assertEqual(entry, {"name": "alpha", "kind": "skill", "mode": "link", "provider": "core",
                                 "source": str(self.src / "alpha"), "source_commit": "c0ffee", "used_by": ["claude"],
                                 "link_target": str(self.src / "alpha"), "baseline": "recorded"})
        self.assertEqual(record["source"], {"root": str(self.src), "commit": "c0ffee", "dirty": "clean",
                                            "hooks_path": "hooks"})
        self.assertEqual(record["installer"], {"commit": "c0ffee", "schema_version": 1})
        self.assertEqual(record["last_transaction"], {"id": txid, "updated_at": installed_at,
                                                      "rollback_dir": str(self.ghost / "rollbacks" / txid)})
        self.assertEqual(self.journal(txid)["phase"], "done")

    def test_a_link_that_already_points_at_its_source_is_recorded_without_a_replacement(self):
        current = self.claude / "alpha"
        current.symlink_to(self.src / "alpha", target_is_directory=True)
        before = os.lstat(current)
        new = self.claude / "beta"
        txid = install_transaction.publish(self.request(
            "claude", self.target("alpha", current, "link", status="current"),
            self.target("beta", new, "link", status="new")))
        after = os.lstat(current)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertEqual(os.readlink(new), str(self.src / "beta"))
        targets = self.record()["targets"]
        self.assertEqual((targets[str(current)]["link_target"], targets[str(current)]["baseline"]),
                         (str(self.src / "alpha"), "recorded"))
        self.assertEqual(targets[str(new)]["link_target"], str(self.src / "beta"))
        journal = self.journal(txid)
        self.assertEqual(([t["dest"] for t in journal["targets"]], journal["phase"]), ([str(new)], "done"))

    def test_a_request_of_current_links_only_replaces_nothing(self):
        current = self.claude / "alpha"
        current.symlink_to(self.src / "alpha", target_is_directory=True)
        participant = mock.Mock(spec=["before_publish", "after_publish", "before_restore", "after_restore"])
        txid = install_transaction.publish(self.request("claude", self.target("alpha", current, "link",
                                                                                status="current")), [participant])
        self.assertEqual(participant.mock_calls, [])
        self.assertEqual(self.record()["targets"][str(current)]["link_target"], str(self.src / "alpha"))
        self.assertEqual(sorted(p.name for p in (self.ghost / "rollbacks" / txid).iterdir()), ["journal.json"])

    def test_a_current_link_that_points_elsewhere_is_replaced(self):
        dest = self.claude / "alpha"
        dest.symlink_to(self.home / "elsewhere", target_is_directory=True)
        install_transaction.publish(self.request("claude", self.target("alpha", dest, "link", status="current")))
        self.assertEqual(os.readlink(dest), str(self.src / "alpha"))

    def test_a_copy_records_the_digest_of_every_copied_file(self):
        dest = self.codex / "_shared"
        install_transaction.publish(self.request("codex", self.target("_shared", dest, "copy", kind="support")))
        self.assertFalse(dest.is_symlink())
        self.assertEqual(sorted(path.name for path in dest.iterdir()), [".ghost-alice-install.json", "SKILL.md"])
        entry = self.record()["targets"][str(dest)]
        self.assertEqual((entry["mode"], entry["kind"], entry["baseline"]), ("copy", "support", "recorded"))
        self.assertEqual(entry["files"], content_digest.tree(dest))
        self.assertEqual(list(entry["files"]), ["SKILL.md"])
        self.assertNotIn("link_target", entry)

    def test_a_copied_file_records_its_digest_under_its_own_path(self):
        command = self.src / "noopcmd.md"
        command.write_text("command v2\n", encoding="utf-8")
        dest = self.home / ".claude" / "commands" / "noopcmd.md"
        install_transaction.publish(self.request("claude", self.target(
            "noopcmd", dest, "copy", kind="command", provider="addon:noop", source=command)))
        entry = self.record()["targets"][str(dest)]
        self.assertEqual(entry["files"], {".": content_digest.file_digest(dest)})
        self.assertNotIn("file", entry)
        self.assertEqual(content_digest.compare(entry, dest)["changed"], [])

    def test_one_request_is_one_transaction_and_keeps_the_replaced_originals_in_its_folder(self):
        self.old_copy(self.codex / "alpha", "alpha v1\n")
        self.old_copy(self.codex / "beta", "beta v1\n")
        txid = install_transaction.publish(self.request(
            "codex", self.target("alpha", self.codex / "alpha", "copy"),
            self.target("beta", self.codex / "beta", "copy"), self.target("_shared", self.codex / "_shared", "copy",
                                                                           kind="support")))
        self.assertEqual([p.name for p in (self.ghost / "rollbacks").iterdir()], [txid])
        self.assertEqual(sorted(self.record()["targets"]),
                         sorted(str(self.codex / name) for name in ("alpha", "beta", "_shared")))
        kept = sorted((self.ghost / "rollbacks" / txid).rglob("SKILL.md"))
        self.assertEqual(sorted(p.read_text(encoding="utf-8") for p in kept), ["alpha v1\n", "beta v1\n"])
        # The rollbacks folder of this publish is the only place that keeps replaced files.
        self.assertFalse((self.codex / ".ghost-alice-install-rollbacks").exists())
        self.assertFalse((self.ghost / "install-rollbacks").exists())
        journal = self.journal(txid)
        self.assertEqual((journal["txid"], journal["host"], journal["phase"]), (txid, "codex", "done"))
        self.assertEqual([t["dest"] for t in journal["targets"]],
                         [str(self.codex / name) for name in ("alpha", "beta", "_shared")])

    def test_a_second_host_joins_the_entry_of_a_path_it_shares(self):
        runtime = self.ghost / "runtime" / "current" / "_shared"
        install_transaction.publish(self.request("claude", self.target("_shared", runtime, "copy", kind="runtime")))
        install_transaction.publish(self.request("codex", self.target("_shared", runtime, "copy", kind="runtime")))
        self.assertEqual(self.record()["targets"][str(runtime)]["used_by"], ["claude", "codex"])

    def test_the_journal_says_prepare_until_files_and_record_are_both_published(self):
        dest = self.codex / "alpha"
        self.old_copy(dest)
        seen = {}
        real_update = installed_payload.update

        def observing_update(root, patches):
            seen["phase"] = self.journal(patches["last_transaction"]["id"])["phase"]
            return real_update(root, patches)

        with mock.patch.object(install_transaction.installed_payload, "update", side_effect=observing_update):
            txid = install_transaction.publish(self.request("codex", self.target("alpha", dest, "copy")))
        self.assertEqual(seen["phase"], "prepare")
        journal = self.journal(txid)
        self.assertEqual(journal["phase"], "done")
        target = journal["targets"][0]
        self.assertEqual(Path(target["rollback"]).parent, self.ghost / "rollbacks" / txid)
        self.assertEqual(target["before"], content_digest.state(Path(target["rollback"])))
        self.assertEqual(target["candidate"], content_digest.state(dest))

    def test_a_record_this_installer_cannot_read_stays_as_it_is_and_the_files_still_publish(self):
        # A newer or damaged record is never overwritten; the install goes on and says why the record did not change.
        record = installed_payload.path(self.ghost)
        record.write_text(json.dumps({"schema_version": 2, "targets": {}}), encoding="utf-8")
        dest = self.claude / "alpha"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            txid = install_transaction.publish(self.request("claude", self.target("alpha", dest, "link")))
        self.assertEqual(os.readlink(dest), str(self.src / "alpha"))
        self.assertEqual(json.loads(record.read_text(encoding="utf-8")), {"schema_version": 2, "targets": {}})
        self.assertIn("[WARN] Install record left unchanged:", stderr.getvalue())
        self.assertEqual(self.journal(txid)["phase"], "done")

    def test_publish_does_not_prune_finished_originals_before_full_install_success(self):
        rollbacks = self.ghost / "rollbacks"
        names = [f"20260101T{index:06d}Z-1-aaaaaaaa" for index in range(22)]
        for name in names:
            (rollbacks / name).mkdir(parents=True)
            (rollbacks / name / "journal.json").write_text(json.dumps({"phase": "done"}), encoding="utf-8")
            (rollbacks / name / "user-data").write_bytes(b"sole user copy")
        txid = install_transaction.publish(self.request("claude", self.target("alpha", self.claude / "alpha", "link")))
        self.assertEqual(sorted(p.name for p in rollbacks.iterdir()), [*names, txid])


class CompletedCleanupTests(PublishCase):
    def replaced_copy(self, *, kind="skill", name="alpha", extra=None):
        dest = self.codex / name
        request = self.request("codex", self.target(name, dest, "copy", kind=kind))
        install_transaction.publish(request)
        if extra is not None:
            extra(dest)
        txid = install_transaction.publish(request)
        original = Path(self.journal(txid)["targets"][0]["rollback"])
        return txid, original

    def test_success_cleanup_removes_managed_originals_and_empty_journals(self):
        txid, original = self.replaced_copy()
        install_transaction.cleanup_completed(self.ghost)
        self.assertFalse(original.exists())
        self.assertFalse((self.ghost / "rollbacks" / txid).exists())

    def test_full_copy_check_preserves_user_content_excluded_from_hashes(self):
        for kind, name in (("skill", "alpha"), ("runtime", "beta")):
            for excluded in (".git", "node_modules", "__pycache__/user-link"):
                with self.subTest(kind=kind, excluded=excluded):
                    dest = self.codex / name
                    if dest.exists():
                        shutil.rmtree(dest)
                    def extra(path):
                        item = path / excluded
                        item.parent.mkdir(parents=True, exist_ok=True)
                        if excluded.endswith("user-link"):
                            item.symlink_to(self.src / "alpha")
                        else:
                            item.mkdir()
                            (item / "user-data").write_bytes(b"sole user bytes")
                    txid, original = self.replaced_copy(kind=kind, name=name, extra=extra)
                    reasons = install_transaction.cleanup_completed(self.ghost)
                    self.assertTrue(os.path.lexists(original / excluded))
                    self.assertTrue(any(str(original) in reason and "unrecorded-copy-content" in reason for reason in reasons))

    def test_generated_cache_does_not_keep_a_managed_runtime_copy(self):
        def extra(path):
            (path / "__pycache__").mkdir()
            (path / "__pycache__" / "cache.pyc").write_bytes(b"cache")
        _, original = self.replaced_copy(kind="runtime", extra=extra)
        install_transaction.cleanup_completed(self.ghost)
        self.assertFalse(original.exists())

    def test_unknown_and_edited_originals_stay_with_their_reasons(self):
        dest = self.codex / "beta"
        self.old_copy(dest, "user original\n")
        txid = install_transaction.publish(self.request("codex", self.target("beta", dest, "copy")))
        def extra(path):
            (path / "SKILL.md").write_text("user edit\n")
        edited_txid, edited = self.replaced_copy(extra=extra)
        reasons = install_transaction.cleanup_completed(self.ghost)
        unknown = Path(self.journal(txid)["targets"][0]["rollback"])
        self.assertEqual((unknown / "SKILL.md").read_text(), "user original\n")
        self.assertTrue(any(str(unknown) in reason and "without-marker" in reason for reason in reasons))
        self.assertEqual((edited / "SKILL.md").read_text(), "user edit\n")
        self.assertTrue(any(str(edited) in reason and "installed-content-changed" in reason for reason in reasons))

    def test_pending_and_current_host_references_keep_originals(self):
        txid, original = self.replaced_copy()
        manifest = self.ghost / "pending-merges" / "codex" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"entries": [{"backup_path": str(original / "SKILL.md"), "decided": False}]}))
        reasons = install_transaction.cleanup_completed(self.ghost)
        self.assertTrue(original.exists())
        self.assertTrue(any("pending-merge-reference" in reason for reason in reasons))
        manifest.unlink()
        entry = dict(self.record()["targets"][str(self.codex / "alpha")])
        entry.update(mode="link", link_target=str(original), source=str(original))
        installed_payload.update(self.ghost, {"targets": {str(self.claude / "alpha"): entry}})
        reasons = install_transaction.cleanup_completed(self.ghost)
        self.assertTrue(original.exists())
        self.assertTrue(any("current-target-reference" in reason for reason in reasons))

    def test_live_inventory_addon_and_payload_sources_keep_only_the_consumed_original(self):
        import addon_registry
        import hash_utils
        import install_destination
        from installer import locations

        self.assertEqual(locations.record_root(), self.ghost)
        for path in (self.ghost, self.claude, self.codex, self.src):
            install_destination.assert_mutation_path(path)

        for record_kind in ('inventory', 'addon', 'payload'):
            with self.subTest(record_kind=record_kind):
                _, original = self.replaced_copy()
                _, unused = self.replaced_copy(name='beta')
                live = self.claude / 'consumer'
                live.symlink_to(original, target_is_directory=True)
                before = (live / 'SKILL.md').read_bytes()
                if record_kind == 'inventory':
                    reference = self.ghost / 'install-state/claude.json'
                    reference.parent.mkdir(parents=True, exist_ok=True)
                    reference.write_text(json.dumps(dict(platform='claude', source_root=str(original.parent),
                        targets=[dict(target_name='consumer', source_path=str(original),
                                      dest_path=str(live), install_mode='symlink')])))
                elif record_kind == 'addon':
                    reference = addon_registry.write_record(dict(schema_version='1.0', addon_id='consumer',
                        addon_version='1', source=str(original), platform='claude', owner='addon', origin='local',
                        depends_on_core=[], min_core_version='1', installed_at='t', provided=[dict(kind='skill',
                            name='consumer', target=str(live), ownership='addon', install_mode='symlink',
                            content_hash=hash_utils.hash_target(live, 'symlink'), marker='', metadata={})]),
                        addons_dir=self.ghost / 'addons/claude')
                else:
                    installed_payload.update(self.ghost, {'targets': {str(live): dict(name='consumer', kind='skill',
                        provider='core', mode='link', source=str(original), link_target=str(original),
                        baseline='recorded', used_by=['claude'])}})
                    reference = None
                try:
                    self.assertIsNone(installed_payload.migrate(self.ghost, ['claude', 'codex'], installer_commit='fixture'))
                    reasons = install_transaction.cleanup_completed(self.ghost)
                    self.assertTrue(original.exists(), reasons)
                    self.assertEqual((live / 'SKILL.md').read_bytes(), before)
                    self.assertFalse(unused.exists())
                    self.assertTrue(any(str(original) in reason for reason in reasons))
                finally:
                    live.unlink()
                    if reference is not None:
                        Path(reference).unlink()
                    else:
                        installed_payload.release_target(self.ghost, destination=live, provider='core', platform='claude')
                install_transaction.cleanup_completed(self.ghost)
                self.assertFalse(original.exists())

    def test_unreadable_host_references_keep_completed_originals_until_explicit_retry(self):
        for relative in ('install-state/claude.json', 'addons/claude/consumer.json'):
            with self.subTest(reference=relative):
                _, original = self.replaced_copy()
                before = (original / 'SKILL.md').read_bytes()
                reference = self.ghost / relative
                reference.parent.mkdir(parents=True, exist_ok=True)
                reference.write_text('{unreadable')
                try:
                    with self.assertRaises(install_transaction.InstallTransactionError):
                        install_transaction.cleanup_completed(self.ghost)
                    self.assertEqual((original / 'SKILL.md').read_bytes(), before)
                finally:
                    reference.unlink()
                install_transaction.cleanup_completed(self.ghost)
                self.assertFalse(original.exists())

    def test_active_journal_and_malformed_pending_manifest_prevent_cleanup(self):
        txid, original = self.replaced_copy()
        journal = install_transaction.Journal.load(self.ghost, txid)
        journal.mark("prepare")
        before = (journal.folder / "journal.json").read_bytes()
        install_transaction.cleanup_completed(self.ghost)
        self.assertTrue(original.exists())
        self.assertEqual((journal.folder / "journal.json").read_bytes(), before)
        journal.mark("done")
        manifest = self.ghost / "pending-merges" / "codex" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{")
        with self.assertRaisesRegex(install_transaction.InstallTransactionError, "pending"):
            install_transaction.cleanup_completed(self.ghost)
        self.assertTrue(original.exists())

    def test_command_original_uses_the_saved_payload_baseline(self):
        source = self.src / "command.md"
        source.write_text("command v1\n")
        dest = self.home / ".claude" / "commands" / "command.md"
        request = self.request("claude", self.target("command", dest, "copy", kind="command", source=source))
        install_transaction.publish(request)
        source.write_text("command v2\n")
        txid = install_transaction.publish(request)
        original = Path(self.journal(txid)["targets"][0]["rollback"])
        self.assertEqual(original.read_text(), "command v1\n")
        install_transaction.cleanup_completed(self.ghost)
        self.assertFalse(original.exists())
        self.assertEqual(dest.read_text(), "command v2\n")

    def test_service_reference_keeps_only_the_original_it_needs(self):
        request = self.request("codex", self.target("alpha", self.codex / "alpha", "copy", kind="runtime"),
                               self.target("beta", self.codex / "beta", "copy", kind="runtime"))
        install_transaction.publish(request)
        txid = install_transaction.publish(request)
        originals = [Path(target["rollback"]) for target in self.journal(txid)["targets"]]
        record = self.record()
        record["service"] = {"program": str(originals[0] / "SKILL.md")}
        installed_payload.path(self.ghost).write_text(json.dumps(record))
        reasons = install_transaction.cleanup_completed(self.ghost)
        self.assertTrue(originals[0].exists())
        self.assertFalse(originals[1].exists())
        self.assertTrue(any("current-service-reference:program" in reason for reason in reasons))

    def test_cleanup_failure_keeps_published_files_and_done_phase(self):
        txid, original = self.replaced_copy()
        record_before = installed_payload.path(self.ghost).read_bytes()
        with mock.patch.object(install_transaction, "_remove_path", side_effect=PermissionError("cannot delete")):
            with self.assertRaisesRegex(install_transaction.InstallTransactionError, "cannot delete"):
                install_transaction.cleanup_completed(self.ghost)
        self.assertTrue(original.exists())
        self.assertEqual((self.codex / "alpha" / "SKILL.md").read_text(), "alpha v2\n")
        self.assertEqual(self.journal(txid)["phase"], "done")
        self.assertEqual(installed_payload.path(self.ghost).read_bytes(), record_before)

    def test_a_marker_can_prove_a_legacy_original_without_a_payload_record(self):
        dest = self.codex / "alpha"
        self.old_copy(dest)
        install_transaction.write_ownership_marker(dest, platform="codex", asset_id="alpha", source_repo=str(self.src),
                                                   source_commit="old", install_mode="copy")
        txid = install_transaction.publish(self.request("codex", self.target("alpha", dest, "copy")))
        original = Path(self.journal(txid)["targets"][0]["rollback"])
        install_transaction.cleanup_completed(self.ghost)
        self.assertFalse(original.exists())

    def test_unrecorded_content_with_a_metadata_filename_is_preserved(self):
        txid = install_transaction.publish(self.request("codex", self.target("alpha", self.codex / "alpha", "copy")))
        folder = self.ghost / "rollbacks" / txid
        user_file = folder / installed_payload.FILENAME
        user_file.write_bytes(b"sole user file")
        reasons = install_transaction.cleanup_completed(self.ghost)
        self.assertTrue(user_file.exists())
        self.assertEqual(user_file.read_bytes(), b"sole user file")
        self.assertTrue(any("unrecognized-publish-content" in reason for reason in reasons))


class StagingAndRetentionTests(PublishCase):
    """The publish removes the staging folder it created when it is empty, the replacement names its
    rollback folder, and the retention rules live in `state_retention`."""

    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": str(self.ghost)})
        environment.start()
        self.addCleanup(environment.stop)

    @staticmethod
    def staging(parent: Path) -> Path:
        return parent / ".ghost-alice-install-staging"

    def test_an_empty_staging_folder_is_removed_after_a_publish(self):
        install_transaction.publish(self.request("codex", self.target("alpha", self.codex / "alpha", "copy"),
                                                 self.target("beta", self.claude / "beta", "link")))
        self.assertFalse(self.staging(self.codex).exists())
        self.assertFalse(self.staging(self.claude).exists())

    def test_an_empty_staging_folder_is_removed_after_a_restore(self):
        self.old_copy(self.codex / "alpha")
        with mock.patch.object(install_transaction.installed_payload, "update", side_effect=OSError("disk full")):
            with self.assertRaises(install_transaction.InstallTransactionError):
                install_transaction.publish(self.request("codex", self.target("alpha", self.codex / "alpha", "copy")))
        self.assertEqual((self.codex / "alpha" / "SKILL.md").read_text(encoding="utf-8"), "v1\n")
        self.assertFalse(self.staging(self.codex).exists())

    def test_a_staging_folder_that_holds_another_entry_stays(self):
        other = self.staging(self.codex) / "stage-of-another-install"
        other.mkdir(parents=True)
        install_transaction.publish(self.request("codex", self.target("alpha", self.codex / "alpha", "copy")))
        self.assertTrue(other.is_dir())
        self.assertFalse((self.ghost / "state-retention-failures.jsonl").exists())

    def test_a_staging_folder_that_cannot_be_removed_is_recorded(self):
        real_rmdir = Path.rmdir

        def rmdir(path):
            if path.name == ".ghost-alice-install-staging":
                raise PermissionError(13, "Permission denied")
            return real_rmdir(path)

        with mock.patch.object(Path, "rmdir", rmdir):
            install_transaction.publish(self.request("codex", self.target("alpha", self.codex / "alpha", "copy")))
        rows = [json.loads(line) for line in
                (self.ghost / "state-retention-failures.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(row["policy"], row["error_type"]) for row in rows], [("install-staging", "PermissionError")])

    def test_the_prune_helpers_live_in_the_retention_table(self):
        for name in ("_prune_transactions", "_prune_rollback_root"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(install_transaction, name))

    def test_a_replacement_names_its_rollback_folder(self):
        with self.assertRaises(TypeError):
            install_transaction.ReplacementBatch([(self.src / "alpha", self.codex / "alpha", "copy")])

    def test_a_publish_failure_event_offers_the_event_log_to_its_retention_entry(self):
        # The entry rotates past 5MB (state_retention tests); the writer offers its log after each event.
        import state_retention

        log = self.ghost / "install-state" / "codex-events.jsonl"
        with mock.patch.object(state_retention, "append", wraps=state_retention.append) as append:
            install_transaction._write_failure_event(log, source=self.src / "alpha", dest=self.codex / "alpha",
                                                     phase="publish", error="disk full")
        self.assertEqual(append.call_args.args[:2], ("install-state-events", log))
        self.assertEqual(json.loads(log.read_text(encoding="utf-8"))["error"], "disk full")


class FailureTests(PublishCase):
    def setUp(self):
        super().setUp()
        install_transaction.publish(self.request("codex", self.target("alpha", self.codex / "alpha", "copy")))
        for name in ("beta", "_shared"):
            self.old_copy(self.codex / name, f"{name} v1\n")
        self.before_record = installed_payload.path(self.ghost).read_bytes()
        self.dests = [self.codex / name for name in ("alpha", "beta", "_shared")]
        self.before_files = self.snapshot(*self.dests)
        (self.src / "alpha" / "SKILL.md").write_text("alpha v3\n", encoding="utf-8")

    def batch(self):
        return self.request("codex", self.target("alpha", self.codex / "alpha", "copy"),
                            self.target("beta", self.codex / "beta", "copy"),
                            self.target("_shared", self.codex / "_shared", "copy", kind="support"))

    def only_txid(self, exclude=()) -> str:
        names = [p.name for p in (self.ghost / "rollbacks").iterdir() if p.name not in exclude]
        self.assertEqual(len(names), 1, names)
        return names[0]

    def assert_restored(self, txid):
        self.assertEqual(self.snapshot(*self.dests), self.before_files)
        self.assertEqual(installed_payload.path(self.ghost).read_bytes(), self.before_record)
        self.assertEqual(self.journal(txid)["phase"], "restored")
        self.assertEqual(install_transaction.unfinished(self.ghost), [])

    def test_a_failed_rename_restores_the_files_and_the_record(self):
        first = self.only_txid()
        real_rename = install_transaction._rename_path
        calls = []

        def failing_rename(source, dest):
            calls.append(dest)
            if len(calls) == 4:
                raise OSError("rename refused")
            return real_rename(source, dest)

        with mock.patch.object(install_transaction, "_rename_path", side_effect=failing_rename):
            with self.assertRaises(install_transaction.InstallTransactionError):
                install_transaction.publish(self.batch())
        self.assert_restored(self.only_txid(exclude=(first,)))

    def test_a_failed_record_update_restores_the_files(self):
        first = self.only_txid()
        with mock.patch.object(install_transaction.installed_payload, "update", side_effect=OSError("disk full")):
            with self.assertRaises(install_transaction.InstallTransactionError):
                install_transaction.publish(self.batch())
        self.assert_restored(self.only_txid(exclude=(first,)))

    def test_a_participant_refusal_keeps_its_exit_code_and_changes_nothing(self):
        first = self.only_txid()
        refusal = GatewayServiceError("Gateway service stop is unconfirmed", phase="stop")
        participant = mock.Mock(spec=["before_publish", "after_publish", "before_restore", "after_restore"])
        participant.before_publish.side_effect = refusal
        with self.assertRaises(install_transaction.InstallTransactionError) as failed:
            install_transaction.publish(self.batch(), [participant])
        self.assertEqual((failed.exception.exit_code, failed.exception.code), (3, "GATEWAY_SERVICE_UNCONFIRMED"))
        participant.after_publish.assert_not_called()
        self.assert_restored(self.only_txid(exclude=(first,)))

    def test_a_crash_leaves_an_unfinished_journal_that_recover_restores(self):
        first = self.only_txid()
        with mock.patch.object(install_transaction.PublishBatch, "finalize", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                install_transaction.publish(self.batch())
        txid = self.only_txid(exclude=(first,))
        self.assertEqual(self.journal(txid)["phase"], "prepare")
        self.assertEqual((self.codex / "alpha" / "SKILL.md").read_text(encoding="utf-8"), "alpha v3\n")
        self.assertEqual(install_transaction.unfinished(self.ghost), [txid])
        restored = install_transaction.recover(self.ghost, txid)
        self.assertEqual(len(restored), 3)
        self.assert_restored(txid)
        self.assertEqual(install_transaction.recover(self.ghost, txid), [])

    def test_recover_refuses_files_that_changed_after_the_publish(self):
        first = self.only_txid()
        with mock.patch.object(install_transaction.PublishBatch, "finalize", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                install_transaction.publish(self.batch())
        txid = self.only_txid(exclude=(first,))
        (self.codex / "beta" / "SKILL.md").write_text("edited after the crash\n", encoding="utf-8")
        published = self.snapshot(*self.dests)
        record = installed_payload.path(self.ghost).read_bytes()
        with self.assertRaisesRegex(install_transaction.InstallTransactionError, str(self.ghost / "rollbacks" / txid)):
            install_transaction.recover(self.ghost, txid)
        self.assertEqual(self.snapshot(*self.dests), published)
        self.assertEqual(installed_payload.path(self.ghost).read_bytes(), record)
        self.assertEqual(self.journal(txid)["phase"], "prepare")
        self.assertEqual(install_transaction.unfinished(self.ghost), [txid])

    def crash_after_publish(self) -> str:
        first = self.only_txid()
        with mock.patch.object(install_transaction.PublishBatch, "finalize", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                install_transaction.publish(self.batch())
        return self.only_txid(exclude=(first,))

    def assert_recover_refused(self, txid):
        folder = self.ghost / "rollbacks" / txid
        paths = [*self.dests, *(Path(target["rollback"]) for target in self.journal(txid)["targets"]
                               if target.get("rollback"))]
        before = {path: content_digest.state(path) for path in paths}

        def file_bytes():
            return {file: file.read_bytes() for root in (*self.dests, folder)
                    for file in (root, *root.rglob("*")) if file.is_file() and not file.is_symlink()}

        saved_bytes = file_bytes()
        record = installed_payload.path(self.ghost).read_bytes()
        with self.assertRaises(install_transaction.InstallTransactionError) as refused:
            install_transaction.recover(self.ghost, txid)
        self.assertIn(f"review {folder}", str(refused.exception))
        self.assertEqual({path: content_digest.state(path) for path in paths}, before)
        self.assertEqual(file_bytes(), saved_bytes)
        self.assertEqual(installed_payload.path(self.ghost).read_bytes(), record)
        self.assertEqual(self.journal(txid)["phase"], "prepare")
        self.assertEqual(install_transaction.unfinished(self.ghost), [txid])

    def test_recover_refuses_a_changed_kept_original_before_modifying_any_target(self):
        txid = self.crash_after_publish()
        # alpha is checked last in reverse recovery order; the other targets must stay untouched too.
        target = next(t for t in self.journal(txid)["targets"] if t["name"] == "alpha")
        kept = Path(target["rollback"])
        self.assertEqual(content_digest.state(kept), target["before"])
        (kept / "SKILL.md").write_text("modified kept original\n", encoding="utf-8")
        self.assert_recover_refused(txid)

    def test_recover_refuses_a_changed_kept_original_when_the_published_target_is_missing(self):
        txid = self.crash_after_publish()
        target = next(t for t in self.journal(txid)["targets"] if t["name"] == "alpha")
        kept = Path(target["rollback"])
        (kept / "SKILL.md").write_text("modified kept original\n", encoding="utf-8")
        shutil.rmtree(self.codex / "alpha")
        self.assert_recover_refused(txid)

    def test_recover_refuses_an_empty_folder_added_after_the_publish(self):
        # Nothing the user adds after the stop may disappear with the published copy.
        txid = self.crash_after_publish()
        (self.codex / "beta" / "notes").mkdir()
        self.assert_recover_refused(txid)

    def test_recover_refuses_a_link_added_after_the_publish(self):
        txid = self.crash_after_publish()
        (self.codex / "beta" / "shortcut").symlink_to(self.home)
        self.assert_recover_refused(txid)

    def test_recover_ignores_caches_written_after_the_publish(self):
        # A hook run after the stop may write __pycache__ into the published copy; that does not block the restore.
        txid = self.crash_after_publish()
        (self.codex / "beta" / "__pycache__").mkdir()
        (self.codex / "beta" / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"cache")
        self.assertEqual(len(install_transaction.recover(self.ghost, txid)), 3)
        self.assert_restored(txid)

    def test_recover_refuses_when_the_kept_original_is_gone(self):
        first = self.only_txid()
        with mock.patch.object(install_transaction.PublishBatch, "finalize", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                install_transaction.publish(self.batch())
        txid = self.only_txid(exclude=(first,))
        kept = Path(next(t for t in self.journal(txid)["targets"] if t["name"] == "beta")["rollback"])
        shutil.rmtree(kept)
        with self.assertRaises(install_transaction.InstallTransactionError):
            install_transaction.recover(self.ghost, txid)
        self.assertEqual(self.journal(txid)["phase"], "prepare")

    def test_a_missing_source_keeps_the_original_and_records_a_failure_event(self):
        dest = self.codex / "beta"
        missing = self.src / "missing-source"
        log = self.ghost / "install-state" / "codex-events.jsonl"
        with self.assertRaises(install_transaction.InstallTransactionError):
            install_transaction.publish(self.request("codex", self.target("beta", dest, "copy", source=missing),
                                                     event_log=str(log)))
        events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["schema_version"], 1)
        self.assertEqual(events[0]["event"], "copy_replace_failure")
        self.assertEqual(events[0]["phase"], "stage-copy")
        self.assertEqual(events[0]["source_path"], missing.as_posix())
        self.assertEqual(events[0]["dest_path"], dest.as_posix())
        self.assertIn("source does not exist", events[0]["error"])
        self.assertEqual((dest / "SKILL.md").read_text(encoding="utf-8"), "beta v1\n")

    def test_a_stage_that_fails_its_verification_keeps_the_original_and_records_the_phase(self):
        dest = self.codex / "beta"
        log = self.ghost / "install-state" / "codex-events.jsonl"
        real_copy = install_transaction._copy_to_stage

        def corrupting_copy(source, stage):
            real_copy(source, stage)
            (stage / "SKILL.md").write_text("corrupt stage\n", encoding="utf-8")

        with mock.patch.object(install_transaction, "_copy_to_stage", side_effect=corrupting_copy):
            with self.assertRaises(install_transaction.InstallTransactionError):
                install_transaction.publish(self.request("codex", self.target("beta", dest, "copy"),
                                                         event_log=str(log)))
        self.assertEqual((dest / "SKILL.md").read_text(encoding="utf-8"), "beta v1\n")
        events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[0]["phase"], "stage-verify")
        self.assertIn("staged copy verification failed", events[0]["error"])

    def test_a_later_target_that_fails_staging_keeps_every_original_and_is_the_one_reported(self):
        beta = self.codex / "beta"
        log = self.ghost / "install-state" / "codex-events.jsonl"
        real_copy = install_transaction._copy_to_stage

        def corrupting_copy(source, stage):
            real_copy(source, stage)
            if source.name == "beta":
                (stage / "SKILL.md").write_text("corrupt stage\n", encoding="utf-8")

        cases = (("stage-copy", self.target("beta", beta, "copy", source=self.src / "missing-source"), real_copy),
                 ("stage-verify", self.target("beta", beta, "copy"), corrupting_copy))
        for phase, second, copy in cases:
            with self.subTest(phase=phase), mock.patch.object(install_transaction, "_copy_to_stage", side_effect=copy):
                with self.assertRaises(install_transaction.InstallTransactionError):
                    install_transaction.publish(self.request(
                        "codex", self.target("alpha", self.codex / "alpha", "copy"), second, event_log=str(log)))
                self.assertEqual(self.snapshot(*self.dests), self.before_files)
                self.assertEqual(installed_payload.path(self.ghost).read_bytes(), self.before_record)
                event = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
                self.assertEqual((event["phase"], event["dest_path"]), (phase, beta.as_posix()))

    def test_a_journal_that_cannot_be_written_stops_the_publish_before_any_change(self):
        shutil.rmtree(self.ghost / "rollbacks")
        (self.ghost / "rollbacks").write_text("not a folder", encoding="utf-8")
        with self.assertRaisesRegex(install_transaction.InstallTransactionError, "journal"):
            install_transaction.publish(self.batch())
        self.assertEqual(self.snapshot(*self.dests), self.before_files)
        self.assertEqual(installed_payload.path(self.ghost).read_bytes(), self.before_record)

    def test_an_unreadable_journal_counts_as_unfinished(self):
        broken = self.ghost / "rollbacks" / "20990101T000000Z-1-ffffffff"
        broken.mkdir(parents=True)
        (broken / "journal.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(install_transaction.unfinished(self.ghost), [broken.name])
        with self.assertRaises(install_transaction.InstallTransactionError):
            install_transaction.recover(self.ghost, broken.name)


class CommandLineTests(PublishCase):
    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(SHARED / "install_transaction.py"), *args], capture_output=True,
                              text=True, encoding="utf-8", env=dict(os.environ, HOME=str(self.home)), check=False)

    def write_request(self, request) -> Path:
        path = self.home / "request.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        return path

    def test_publish_is_not_a_command_line_and_refuses_another_installers_lock(self):
        # Publishing happens in the installer's process with its participants; the command line keeps recover.
        result = self.run_tool("publish", "--request", str(self.write_request(
            self.request("claude", self.target("alpha", self.claude / "alpha", "link")))))
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)
        self.assertFalse((self.claude / "alpha").exists())
        # Another live installer's lock: the publish refuses before anything changes.
        (self.ghost / "install.lock").write_text(json.dumps({"owner": "other", "pid": 424242}), encoding="utf-8")
        with self.assertRaisesRegex(install_transaction.InstallTransactionError, "install lock"):
            install_transaction.publish(self.request("claude", self.target("beta", self.claude / "beta", "link")))
        self.assertFalse((self.claude / "beta").exists())
        self.assertIsNone(self.record())
        self.assertFalse((self.ghost / "rollbacks").exists())

    def test_publish_prints_the_progress_counter_and_writes_target_events(self):
        events = self.home / "run.events.jsonl"
        request = self.request("codex", self.target("alpha", self.codex / "alpha", "copy", status="new"),
                               self.target("_shared", self.codex / "_shared", "copy", kind="support", status="updated"),
                               progress={"label": "  [2/5] Skill sync", "event_file": str(events)})
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            install_transaction.publish(request)
        # The counter moves after each placed target, not only after the last one.
        self.assertIn("  [2/5] Skill sync [0] [Current], [0] [updated], [1] [newly added]", stdout.getvalue())
        self.assertIn("  [2/5] Skill sync [0] [Current], [1] [updated], [1] [newly added]", stdout.getvalue())
        self.assertTrue(stdout.getvalue().endswith("\n"))
        self.assertEqual([json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()], [
            {"type": "target-result", "platform": "codex", "target_id": "alpha", "target_kind": "skill",
             "status": "new"},
            {"type": "target-result", "platform": "codex", "target_id": "_shared", "target_kind": "support",
             "status": "updated"}])

    def test_recover_restores_by_transaction_id_and_reports_a_failure_with_the_folder(self):
        self.old_copy(self.codex / "alpha")
        with mock.patch.object(install_transaction.PublishBatch, "finalize", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                install_transaction.publish(self.request("codex", self.target("alpha", self.codex / "alpha", "copy")))
        txid = install_transaction.unfinished(self.ghost)[0]
        broken = self.ghost / "rollbacks" / "20990101T000000Z-1-ffffffff"
        broken.mkdir(parents=True)
        (broken / "journal.json").write_text("{broken", encoding="utf-8")
        result = self.run_tool("recover", "--ghost-root", str(self.ghost), "--txid", broken.name)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(broken), result.stderr)
        result = self.run_tool("recover", "--txid", txid)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.codex / "alpha"), result.stdout)
        self.assertEqual((self.codex / "alpha" / "SKILL.md").read_text(encoding="utf-8"), "v1\n")
        self.assertFalse(installed_payload.path(self.ghost).exists())
        self.assertEqual(self.journal(txid)["phase"], "restored")


if __name__ == "__main__":
    unittest.main()
