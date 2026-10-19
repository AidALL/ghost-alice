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
        self.claude = self.home / ".claude" / "skills"
        self.codex = self.home / ".agents" / "skills"
        self.claude.mkdir(parents=True)
        self.codex.mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()
        install_lock.acquire_lock(self.ghost / "install.lock", owner="installer:install:claude:test")

    def tearDown(self):
        install_lock.release_lock(self.ghost / "install.lock")
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

    def test_a_copy_records_the_digest_of_every_copied_file(self):
        dest = self.codex / "_shared"
        install_transaction.publish(self.request("codex", self.target("_shared", dest, "copy", kind="support")))
        self.assertFalse(dest.is_symlink())
        self.assertFalse((dest / "__pycache__").exists())
        entry = self.record()["targets"][str(dest)]
        self.assertEqual((entry["mode"], entry["kind"], entry["baseline"]), ("copy", "support", "recorded"))
        self.assertEqual(entry["files"], content_digest.tree(dest))
        self.assertEqual(list(entry["files"]), ["SKILL.md"])
        self.assertNotIn("link_target", entry)

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
        self.assertEqual(target["before"], {"files": content_digest.tree(Path(target["rollback"]))})
        self.assertEqual(target["candidate"], {"files": content_digest.tree(dest)})

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

    def test_only_the_newest_finished_rollback_folders_are_kept(self):
        rollbacks = self.ghost / "rollbacks"
        for name, phase in (("20260101T000000Z-1-aaaaaaaa", "prepare"), ("20260102T000000Z-1-bbbbbbbb", "done"),
                            ("20260103T000000Z-1-cccccccc", "restored"), ("20260104T000000Z-1-dddddddd", "done")):
            (rollbacks / name).mkdir(parents=True)
            (rollbacks / name / "journal.json").write_text(json.dumps({"phase": phase}), encoding="utf-8")
        txid = install_transaction.publish(self.request("claude", self.target("alpha", self.claude / "alpha", "link")),
                                           rollback_keep=2)
        # An unfinished publish keeps its folder until it is restored.
        self.assertEqual(sorted(p.name for p in rollbacks.iterdir()),
                         ["20260101T000000Z-1-aaaaaaaa", "20260104T000000Z-1-dddddddd", txid])


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

    def test_a_gateway_refusal_keeps_its_exit_code_and_changes_nothing(self):
        first = self.only_txid()
        refusal = GatewayServiceError("Gateway service stop is unconfirmed", phase="stop")
        with mock.patch("context_gateway_install.coordinate_payload_replacement", side_effect=refusal):
            with self.assertRaises(install_transaction.InstallTransactionError) as failed:
                install_transaction.publish(self.batch())
        self.assertEqual((failed.exception.exit_code, failed.exception.code), (3, "GATEWAY_SERVICE_UNCONFIRMED"))
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

    def test_publish_reads_the_request_file_and_keeps_the_gateway_exit_code(self):
        dest = self.claude / "alpha"
        result = self.run_tool("publish", "--request", str(self.write_request(
            self.request("claude", self.target("alpha", dest, "link")))))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record()["targets"][str(dest)]["link_target"], str(self.src / "alpha"))
        # Another live installer's lock: the gateway coordination refuses with its own code and nothing changes.
        (self.ghost / "install.lock").write_text(json.dumps({"owner": "other", "pid": 424242}), encoding="utf-8")
        before = installed_payload.path(self.ghost).read_bytes()
        result = self.run_tool("publish", "--request", str(self.write_request(
            self.request("claude", self.target("beta", self.claude / "beta", "link")))))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stderr)["error_code"], "GATEWAY_PRECONDITION")
        self.assertFalse((self.claude / "beta").exists())
        self.assertEqual(installed_payload.path(self.ghost).read_bytes(), before)

    def test_publish_prints_the_progress_counter_and_writes_target_events(self):
        events = self.home / "run.events.jsonl"
        request = self.request("codex", self.target("alpha", self.codex / "alpha", "copy", status="new"),
                               self.target("_shared", self.codex / "_shared", "copy", kind="support", status="updated"),
                               progress={"label": "  [2/5] Skill sync", "event_file": str(events)})
        result = self.run_tool("publish", "--request", str(self.write_request(request)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("  [2/5] Skill sync [0] [Current], [1] [updated], [1] [newly added]", result.stdout)
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
