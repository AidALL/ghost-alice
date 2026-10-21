"""Tests for `state_retention`: the retention table and the one function that applies an entry.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_retention  # noqa: E402

MB = 1024 * 1024
DAY = 86400


class RetentionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ghost = self.root / "ghost"
        environment = mock.patch.dict(os.environ, {"GHOST_ALICE_GHOST_ROOT": str(self.ghost)})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("GHOST_ALICE_OWNED_ROOT", None)

    def failures(self) -> list[dict]:
        path = self.ghost / "state-retention-failures.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    @staticmethod
    def age(path: Path, seconds: float) -> None:
        moment = time.time() - seconds
        os.utime(path, (moment, moment))


class PolicyTableTests(unittest.TestCase):
    """The table holds the approved methods and limits."""

    def test_approved_limits(self):
        policies = state_retention.POLICIES
        expected = {
            "session-logs": ("age", 14, None),
            "gateway-usage": ("size-rotation", 20 * MB, None),
            "gateway-service-log": ("size-rotation", 5 * MB, None),
            "gateway-config-backups": ("keep-newest", 5, None),
            "governance-events": ("size-rotation", 5 * MB, None),
            "install-logs": ("keep-newest", 30, None),
            "uninstall-reports": ("keep-newest", 30, None),
            "install-state-events": ("size-rotation", 5 * MB, None),
            "retention-failures": ("line-count", 500, 500),
            "io-trace": ("line-count", 10_000, 5_000),
            "publish-rollbacks": ("keep-newest", 20, None),
            "rollback-root": ("keep-newest", 20, None),
            "hook-settings-backups": ("keep-newest", 3, None),
        }
        for name, (method, limit, keep) in expected.items():
            with self.subTest(name=name):
                self.assertEqual((policies[name].method, policies[name].limit), (method, limit))
                if keep is not None:
                    self.assertEqual(policies[name].keep, keep)
        self.assertEqual(policies["session-logs"].per_run, 50)
        self.assertTrue(policies["gateway-service-log"].copy_truncate)

    def test_every_entry_has_a_known_method_and_a_pattern_or_a_caller_path(self):
        for name, policy in state_retention.POLICIES.items():
            with self.subTest(name=name):
                self.assertEqual(policy.name, name)
                self.assertIn(policy.method, state_retention.METHODS)
                if policy.method in ("overwrite", "user-kept"):
                    self.assertTrue(policy.patterns)

    def test_kept_material_is_never_deleted_by_the_program(self):
        for name in ("uninstall-backup", "retired-assets", "pending-merge-copies", "live-smoke"):
            with self.subTest(name=name):
                self.assertEqual(state_retention.POLICIES[name].method, "user-kept")


class SizeRotationTests(RetentionTestCase):
    def test_file_within_the_limit_is_left_alone(self):
        path = self.root / "usage.jsonl"
        path.write_bytes(b"a\n" * 10)
        with mock.patch.dict(state_retention.POLICIES, {"gateway-usage": state_retention.POLICIES["gateway-usage"]._replace(limit=64)}):
            state_retention.apply("gateway-usage", path)
        self.assertEqual(path.read_bytes(), b"a\n" * 10)
        self.assertFalse(path.with_name("usage.jsonl.1").exists())

    def test_file_past_the_limit_becomes_the_one_previous_file(self):
        path = self.root / "usage.jsonl"
        previous = path.with_name("usage.jsonl.1")
        previous.write_bytes(b"older\n")
        path.write_bytes(b"x" * 60 + b"\n" + b"y" * 9 + b"\n")
        with mock.patch.dict(state_retention.POLICIES, {"gateway-usage": state_retention.POLICIES["gateway-usage"]._replace(limit=64)}):
            state_retention.apply("gateway-usage", path)
        self.assertFalse(path.exists())
        self.assertEqual(previous.read_bytes(), b"x" * 60 + b"\n" + b"y" * 9 + b"\n")
        self.assertEqual(self.failures(), [])

    def test_file_over_twice_the_limit_keeps_only_its_last_limit_bytes_from_a_line_boundary(self):
        # A backlog far past the limit does not survive whole as the previous file.
        path = self.root / "usage.jsonl"
        lines = [f"{index:04d}".encode() + b"\n" for index in range(100)]
        path.write_bytes(b"".join(lines))
        with mock.patch.dict(state_retention.POLICIES, {"gateway-usage": state_retention.POLICIES["gateway-usage"]._replace(limit=48)}):
            state_retention.apply("gateway-usage", path)
        kept = path.with_name("usage.jsonl.1").read_bytes()
        self.assertLessEqual(len(kept), 48)
        self.assertTrue(kept.endswith(b"0099\n"))
        self.assertEqual(kept, b"".join(lines[-(len(kept) // 5):]))
        self.assertFalse(path.exists())

    def test_copy_truncate_keeps_the_open_append_descriptor_writing_from_the_start(self):
        # launchd opened service.log in append mode before the gateway started.
        path = self.root / "service.log"
        path.write_bytes(b"traceback\n" * 10)
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
        self.addCleanup(os.close, descriptor)
        identity = path.stat().st_ino
        # 100 bytes stay within twice the limit, so the whole file becomes the previous file.
        with mock.patch.dict(state_retention.POLICIES,
                             {"gateway-service-log": state_retention.POLICIES["gateway-service-log"]._replace(limit=64)}):
            state_retention.apply("gateway-service-log", path)
        self.assertEqual(path.stat().st_ino, identity)
        self.assertEqual(path.stat().st_size, 0)
        self.assertEqual(path.with_name("service.log.1").read_bytes(), b"traceback\n" * 10)
        os.write(descriptor, b"after\n")
        self.assertEqual(path.read_bytes(), b"after\n")

    def test_copy_failure_leaves_the_file_untouched_and_records_one_line(self):
        path = self.root / "service.log"
        path.write_bytes(b"z\n" * 40)
        policy = state_retention.POLICIES["gateway-service-log"]._replace(limit=64)
        with mock.patch.dict(state_retention.POLICIES, {"gateway-service-log": policy}), \
                mock.patch("state_retention.shutil.copyfile", side_effect=OSError("disk full")):
            state_retention.apply("gateway-service-log", path)
        self.assertEqual(path.read_bytes(), b"z\n" * 40)
        [failure] = self.failures()
        self.assertEqual((failure["policy"], failure["path"], failure["error_type"]),
                         ("gateway-service-log", str(path), "OSError"))


class LineCountTests(RetentionTestCase):
    def test_lines_past_the_limit_keep_the_last_lines_and_an_unfinished_record(self):
        path = self.root / "io-trace.jsonl"
        path.write_bytes(b"".join(f"{index}\n".encode() for index in range(12)) + b"partial")
        policy = state_retention.POLICIES["io-trace"]._replace(limit=10, keep=5)
        with mock.patch.dict(state_retention.POLICIES, {"io-trace": policy}):
            state_retention.apply("io-trace", path)
        self.assertEqual(path.read_bytes(), b"7\n8\n9\n10\n11\npartial")

    def test_lines_within_the_limit_are_left_alone(self):
        path = self.root / "io-trace.jsonl"
        path.write_bytes(b"1\n2\n")
        state_retention.apply("io-trace", path)
        self.assertEqual(path.read_bytes(), b"1\n2\n")


class KeepNewestTests(RetentionTestCase):
    def test_install_logs_keep_the_newest_runs_with_both_files_of_a_run(self):
        root = self.root / "install"
        root.mkdir()
        stamps = [f"2026-10-{day:02d}-120000" for day in range(1, 33)]
        for stamp in stamps:
            (root / f"{stamp}.log").write_text("log\n", encoding="utf-8")
            (root / f"{stamp}.events.jsonl").write_text("", encoding="utf-8")
        state_retention.apply("install-logs", root)
        remaining = sorted(path.name for path in root.iterdir())
        self.assertEqual(len(remaining), 60)
        self.assertNotIn(f"{stamps[0]}.log", remaining)
        self.assertNotIn(f"{stamps[1]}.events.jsonl", remaining)
        self.assertIn(f"{stamps[2]}.log", remaining)
        self.assertIn(f"{stamps[2]}.events.jsonl", remaining)

    def test_gateway_backups_keep_five_registrations_and_the_recorded_one(self):
        backups = self.root / "backups"
        backups.mkdir()
        stamps = [f"202610{day:02d}T000000000000Z" for day in range(1, 10)]
        for stamp in stamps:
            (backups / f"{stamp}-config.toml").write_text("model = 1\n", encoding="utf-8")
            (backups / f"{stamp}-artifacts").mkdir()
            (backups / f"{stamp}-artifacts" / "manifest.json").write_text("[]", encoding="utf-8")
        recorded = backups / f"{stamps[0]}-config.toml"
        state_retention.apply("gateway-config-backups", backups, protect=[recorded])
        kept = {path.name.split("-", 1)[0] for path in backups.iterdir()}
        self.assertEqual(kept, {stamps[0], *stamps[-5:]})
        self.assertTrue((backups / f"{stamps[0]}-artifacts" / "manifest.json").exists())

    def test_entries_the_owner_marks_ineligible_are_neither_counted_nor_deleted(self):
        root = self.root / "rollbacks"
        root.mkdir()
        names = [f"2026102{index}T000000000000Z-1-abcd" for index in range(5)]
        for name in names:
            (root / name).mkdir()
        unfinished = {names[0]}
        policy = state_retention.POLICIES["publish-rollbacks"]._replace(limit=2)
        with mock.patch.dict(state_retention.POLICIES, {"publish-rollbacks": policy}):
            state_retention.apply("publish-rollbacks", root, eligible=lambda path: path.name not in unfinished)
        self.assertEqual(sorted(path.name for path in root.iterdir()), [names[0], names[3], names[4]])

    def test_hook_backups_keep_the_newest_by_time_and_never_touch_links_or_pending_merges(self):
        settings = self.root / "settings.json"
        settings.write_text("{}", encoding="utf-8")
        backups = []
        for index in range(5):
            backup = settings.with_name(f"settings.json.bak.{index}")
            backup.write_text(str(index), encoding="utf-8")
            os.utime(backup, (1000 + index, 1000 + index))
            backups.append(backup)
        link = settings.with_name("settings.json.bak.link")
        link.symlink_to(backups[0])
        state_retention.apply("hook-settings-backups", settings)
        self.assertEqual(sorted(path.name for path in self.root.glob("settings.json.bak*")),
                         ["settings.json.bak.2", "settings.json.bak.3", "settings.json.bak.4", "settings.json.bak.link"])

        pending = self.root / "pending" / "settings.json"
        pending.parent.mkdir()
        pending.write_text("{}", encoding="utf-8")
        for index in range(5):
            pending.with_name(f"settings.json.bak.{index}").write_text(str(index), encoding="utf-8")
        state_retention.apply("hook-settings-backups", pending, protect=[self.root / "pending"])
        self.assertEqual(len(list(pending.parent.glob("settings.json.bak*"))), 5)


class AgeTests(RetentionTestCase):
    def session(self, host: str, name: str, age_days: float, *, log: bool = True) -> Path:
        folder = self.root / "session-logs" / host / name
        folder.mkdir(parents=True)
        (folder / "work.txt").write_text("work", encoding="utf-8")
        if log:
            marker = folder / "strict-hook-output.jsonl"
            marker.write_text("{}\n", encoding="utf-8")
            self.age(marker, age_days * DAY)
        self.age(folder, age_days * DAY)
        return folder

    def test_folders_past_fourteen_days_go_in_every_host_folder(self):
        # A host folder without new sessions is pruned too.
        old_unknown = self.session("unknown", "u1", 30)
        old_codex = self.session("codex", "c1", 20)
        recent = self.session("codex", "c2", 3)
        current = self.session("claude", "now", 0)
        state_retention.apply("session-logs", self.root / "session-logs", protect=[current])
        self.assertFalse(old_unknown.exists())
        self.assertFalse(old_codex.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(current.exists())

    def test_the_log_age_counts_not_the_folder_age(self):
        folder = self.session("codex", "c1", 20)
        marker = folder / "strict-hook-output.jsonl"
        self.age(marker, 1 * DAY)
        state_retention.apply("session-logs", self.root / "session-logs")
        self.assertTrue(folder.exists())

    def test_one_call_deletes_at_most_fifty_oldest_folders(self):
        folders = [self.session("codex", f"c{index:03d}", 100 - index * 0.1) for index in range(60)]
        state_retention.apply("session-logs", self.root / "session-logs")
        self.assertEqual([folder.exists() for folder in folders], [False] * 50 + [True] * 10)

    def test_linked_folders_are_not_followed(self):
        target = self.session("codex", "real", 30)
        link = self.root / "session-logs" / "codex" / "link"
        link.symlink_to(target, target_is_directory=True)
        os.utime(link, (1, 1), follow_symlinks=False)
        with mock.patch.dict(state_retention.POLICIES,
                             {"session-logs": state_retention.POLICIES["session-logs"]._replace(per_run=1)}):
            state_retention.apply("session-logs", self.root / "session-logs")
        self.assertTrue(link.is_symlink())
        self.assertFalse(target.exists())


class AppendTests(RetentionTestCase):
    """Writers that share a log append and apply its entry under one lock on the log, so no writer rotates with a size
    it read before another writer rotated."""

    def test_appends_and_rotations_of_one_log_never_overlap(self):
        path = self.root / "governance-events.jsonl"
        policy = state_retention.POLICIES["governance-events"]._replace(limit=64)
        inside, peak, counting = [0], [0], threading.Lock()
        rotate = state_retention._size_rotation

        def observed(target, entry):
            with counting:
                inside[0] += 1
                peak[0] = max(peak[0], inside[0])
            time.sleep(0.02)  # time for a writer without the lock to overlap
            try:
                return rotate(target, entry)
            finally:
                with counting:
                    inside[0] -= 1

        def writer(index):
            for count in range(5):
                state_retention.append("governance-events", path, f'{{"writer":{index},"n":{count}}}\n')

        with mock.patch.dict(state_retention.POLICIES, {"governance-events": policy}), \
                mock.patch("state_retention._size_rotation", side_effect=observed):
            threads = [threading.Thread(target=writer, args=(index,)) for index in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertEqual(peak[0], 1)
        self.assertEqual(self.failures(), [])

    @unittest.skipIf(state_retention.fcntl is None, "this platform has no flock")
    def test_a_writer_that_waited_while_the_log_was_rotated_locks_the_new_log(self):
        path = self.root / "io-trace.jsonl"
        path.write_text("old\n", encoding="utf-8")
        locked = []

        def writer():
            with state_retention._exclusive(path) as held:
                locked.append(held.st_ino)

        with state_retention._exclusive(path):
            thread = threading.Thread(target=writer)
            thread.start()
            time.sleep(0.1)  # the writer now waits for the lock on the old file
            path.rename(path.with_name("io-trace.jsonl.1"))
            path.write_text("new\n", encoding="utf-8")
        thread.join(10)
        self.assertEqual(locked, [path.stat().st_ino])

    @unittest.skipIf(state_retention.fcntl is None, "this platform has no flock")
    def test_a_lock_held_too_long_appends_the_line_without_rotation_and_records_it(self):
        # A writer that stops while holding the lock must not stop the hooks behind it.
        path = self.root / "governance-events.jsonl"
        path.write_bytes(b"x" * 100 + b"\n")
        policy = state_retention.POLICIES["governance-events"]._replace(limit=64)
        done = threading.Event()

        def writer():
            state_retention.append("governance-events", path, '{"late": true}\n')
            done.set()

        with mock.patch.dict(state_retention.POLICIES, {"governance-events": policy}), \
                mock.patch.object(state_retention, "LOCK_WAIT_S", 0.2), state_retention._exclusive(path):
            thread = threading.Thread(target=writer)
            thread.start()
            finished = done.wait(5)
        thread.join(10)
        self.assertTrue(finished, "append waited past its lock limit")
        self.assertTrue(path.read_bytes().endswith(b'{"late": true}\n'))
        self.assertFalse(path.with_name("governance-events.jsonl.1").exists())
        [failure] = self.failures()
        self.assertEqual((failure["policy"], failure["error_type"]), ("governance-events", "LockTimeout"))

    @unittest.skipIf(state_retention.fcntl is None, "this platform has no flock")
    def test_a_failure_record_held_too_long_still_gets_the_line(self):
        record = self.ghost / "state-retention-failures.jsonl"
        record.parent.mkdir(parents=True)
        record.write_text("", encoding="utf-8")
        with mock.patch.object(state_retention, "LOCK_WAIT_S", 0.2), state_retention._exclusive(record):
            state_retention.apply("no-such-entry", self.root)
        [failure] = self.failures()
        self.assertEqual(failure["error_type"], "UnknownPolicy")

    def test_append_writes_the_text_and_applies_the_entry(self):
        path = self.root / "io-trace.jsonl"
        with mock.patch.object(state_retention, "apply", wraps=state_retention.apply) as apply:
            state_retention.append("io-trace", path, "one\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "one\n")
        apply.assert_called_once_with("io-trace", path)


class FailureTests(RetentionTestCase):
    def test_a_delete_failure_is_recorded_and_never_raised(self):
        root = self.root / "install"
        root.mkdir()
        for day in range(1, 33):
            (root / f"2026-10-{day:02d}-120000.log").write_text("", encoding="utf-8")
        with mock.patch("state_retention._remove", side_effect=PermissionError("denied")):
            state_retention.apply("install-logs", root)
        failures = self.failures()
        self.assertEqual(len(failures), 2)
        self.assertEqual({failure["policy"] for failure in failures}, {"install-logs"})
        self.assertEqual({failure["error_type"] for failure in failures}, {"PermissionError"})

    def test_an_entry_another_writer_already_removed_is_not_a_failure(self):
        root = self.root / "install"
        root.mkdir()
        for day in range(1, 33):
            (root / f"2026-10-{day:02d}-120000.log").write_text("", encoding="utf-8")
        with mock.patch("state_retention._remove", side_effect=FileNotFoundError("gone")):
            state_retention.apply("install-logs", root)
        self.assertEqual(self.failures(), [])

    def test_an_unexpected_error_is_recorded_and_never_raised(self):
        path = self.root / "usage.jsonl"
        path.write_bytes(b"x\n")
        with mock.patch("state_retention._size_rotation", side_effect=RuntimeError("bug")):
            state_retention.apply("gateway-usage", path)
        [failure] = self.failures()
        self.assertEqual((failure["policy"], failure["error_type"]), ("gateway-usage", "RuntimeError"))

    def test_an_unknown_entry_is_recorded(self):
        state_retention.apply("no-such-entry", self.root)
        [failure] = self.failures()
        self.assertEqual((failure["policy"], failure["error_type"]), ("no-such-entry", "UnknownPolicy"))

    def test_a_path_that_is_not_the_entry_target_changes_nothing(self):
        wrong = self.root / "notes.txt"
        wrong.write_bytes(b"x\n" * 40)
        policy = state_retention.POLICIES["gateway-usage"]._replace(limit=16)
        with mock.patch.dict(state_retention.POLICIES, {"gateway-usage": policy}):
            state_retention.apply("gateway-usage", wrong)
        self.assertEqual(wrong.read_bytes(), b"x\n" * 40)
        [failure] = self.failures()
        self.assertEqual(failure["error_type"], "PathMismatch")

    def test_an_isolated_installation_never_deletes_outside_its_root(self):
        path = self.root / "outside" / "usage.jsonl"
        path.parent.mkdir()
        path.write_bytes(b"x\n" * 40)
        policy = state_retention.POLICIES["gateway-usage"]._replace(limit=16)
        with mock.patch.dict(os.environ, {"GHOST_ALICE_OWNED_ROOT": str(self.ghost)}), \
                mock.patch.dict(state_retention.POLICIES, {"gateway-usage": policy}):
            state_retention.apply("gateway-usage", path)
        self.assertEqual(path.read_bytes(), b"x\n" * 40)
        [failure] = self.failures()
        self.assertEqual(failure["error_type"], "ValueError")

    def test_the_failure_record_lives_under_the_install_destination_root(self):
        self.assertEqual(state_retention.failure_record_path(), self.ghost / "state-retention-failures.jsonl")

    def test_a_failure_outside_the_table_is_recorded_under_its_name(self):
        state_retention.record_failure("install-staging", self.root / "stage", PermissionError(13, "denied"))
        [failure] = self.failures()
        self.assertEqual((failure["policy"], failure["error_type"]), ("install-staging", "PermissionError"))

    def test_the_failure_record_keeps_its_last_five_hundred_lines(self):
        for index in range(505):
            state_retention.apply(f"missing-{index}", self.root)
        failures = self.failures()
        self.assertEqual(len(failures), 500)
        self.assertEqual(failures[-1]["policy"], "missing-504")


def _entry_callers() -> set[str]:
    """Entry names that production code passes to `state_retention.apply` or `append`, plus the entries
    `state_retention` applies itself."""
    import ast
    shared = Path(__file__).resolve().parent
    names: set[str] = set()
    for source in shared.rglob("*.py"):
        if source.name.startswith("test_") or source.name.endswith("_testing.py"):
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("apply", "append") and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "state_retention" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                names.add(node.args[0].value)
            if (source.name == "state_retention.py" and isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name) and node.value.id == "POLICIES"
                    and isinstance(node.slice, ast.Constant)):
                names.add(node.slice.value)
    return names


class EntryCallerTests(unittest.TestCase):
    """An entry that removes something has a writer that applies it, or names the change that adds that writer;
    a gap never passes silently."""

    def test_every_removing_entry_has_a_caller_or_names_the_change_that_adds_one(self):
        callers = _entry_callers()
        for name, policy in state_retention.POLICIES.items():
            if policy.method in ("overwrite", "user-kept"):
                continue
            with self.subTest(name=name):
                if policy.pending:
                    self.assertNotIn(name, callers, f"{name} has a caller now; remove its pending note")
                else:
                    self.assertIn(name, callers, f"no writer applies {name}")

    def test_the_gateway_entries_wait_for_the_gateway_change(self):
        for name in ("gateway-usage", "gateway-service-log", "gateway-config-backups"):
            with self.subTest(name=name):
                self.assertTrue(state_retention.POLICIES[name].pending)


GATEWAY_AGENT = "org.ghost-alice.context-gateway.plist"


def _gateway_beside(home: Path) -> dict[str, str]:
    """The `service` paths of a gateway registered with its state folder in `home`'s Ghost-ALICE folder."""
    return {"state_path": str(home / ".ghost-alice" / "context-gateway" / "state.json"),
            "launch_agent": str(home / "Library" / "LaunchAgents" / GATEWAY_AGENT)}


def _roots_in(home: Path, **service) -> dict[str, Path]:
    """`default_roots` as a process whose environment names only `home` resolves it."""
    with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
        return state_retention.default_roots(**service)


class DefaultRootsTests(unittest.TestCase):
    """`default_roots` takes every root from the code that owns it and assembles none."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.record = self.root / "record"
        self.record.mkdir()

    def roots(self) -> dict[str, Path]:
        from installer import locations
        skills = {"claude": self.root / "claude-skills", "codex": self.root / "codex-skills"}
        with mock.patch.object(locations, "record_root", return_value=self.record), \
                mock.patch.object(locations, "claude_home", return_value=self.root / "claude"), \
                mock.patch.object(locations, "codex_home", return_value=self.root / "codex"), \
                mock.patch.object(locations, "skills_dir", side_effect=skills.__getitem__), \
                mock.patch.object(state_retention.install_destination, "ghost_root", return_value=self.root / "ghost"):
            return state_retention.default_roots()

    def test_the_roots_come_from_the_installer_locations_and_the_install_destination(self):
        self.assertEqual(self.roots(), {
            "ghost": self.root / "ghost", "record": self.record, "claude": self.root / "claude",
            "codex": self.root / "codex", "claude_skills": self.root / "claude-skills",
            "codex_skills": self.root / "codex-skills"})

    def test_the_gateway_roots_come_from_the_registration_in_the_install_record(self):
        service = {"state_path": str(self.root / "gateway-state" / "state.json"),
                   "launch_agent": str(self.root / "agents" / GATEWAY_AGENT)}
        (self.record / "installed-payload.json").write_text(
            json.dumps({"schema_version": 1, "targets": {}, "service": service}), encoding="utf-8")
        roots = self.roots()
        self.assertEqual((roots["gateway"], roots["launch_agents"]), (self.root / "gateway-state", self.root / "agents"))

    def test_an_installation_without_a_registered_gateway_has_no_gateway_roots(self):
        roots = self.roots()
        self.assertNotIn("gateway", roots)
        self.assertIsNone(state_retention.entry_for(self.record / "context-gateway" / "usage.jsonl", roots))
        self.assertEqual(state_retention.entry_for(self.root / "ghost" / "io-trace.jsonl", roots), "io-trace")


class RareWriterPathTests(unittest.TestCase):
    """The writers the empty-home scenario never runs write where their entries say, also when the Ghost-ALICE
    state root is configured apart from the home."""

    def test_the_rare_writers_write_under_their_entries(self):
        from installer import locations
        import live_agent_smoke
        for overrides in ({}, {"GHOST_ALICE_GHOST_ROOT": "/state/ghost-alice"}):
            with self.subTest(overrides=overrides), \
                    mock.patch.dict(os.environ, {"HOME": "/home/user", **overrides}, clear=True):
                roots = state_retention.default_roots()
                command = locations.claude_commands_dir() / "retired.md"
                cases = {
                    locations.events_log("codex"): "install-state-events",
                    locations.deprecated_skill_backup_root() / "20261022T000000Z-old-skill" / "SKILL.md":
                        "retired-assets",
                    # retired_assets backs a retired command up beside the commands folder.
                    command.parent.parent / "retired-command-backups" / "20261022T000000Z" / "retired.md":
                        "retired-assets",
                    locations.pending_root("codex") / "20261022T000000Z-task-router-SKILL.md.bak":
                        "pending-merge-copies",
                    locations.pending_root("codex") / "legacy-targets" / "task-router" / "SKILL.md":
                        "pending-merge-copies",
                    locations.addon_source_cache_root() / "0123abcd" / "README.md": "addon-source-cache",
                    live_agent_smoke._default_log_root() / "20261022T000000Z" / "result.json": "live-smoke",
                }
                for path, name in cases.items():
                    self.assertEqual(state_retention.entry_for(path, roots), name, str(path))


class InvariantMatchTests(unittest.TestCase):
    """`entry_for` names the table entry a created path belongs to; the invariant test fails on None."""

    def setUp(self) -> None:
        self.home = Path("/home/user")
        self.roots = _roots_in(self.home, service=_gateway_beside(self.home))

    def test_created_files_map_to_their_entries(self):
        cases = {
            ".ghost-alice/session-logs/codex/s-1/strict-hook-output.jsonl": "session-logs",
            ".ghost-alice/context-gateway/usage.jsonl.1": "gateway-usage",
            ".ghost-alice/context-gateway/backups/20261020T000000000000Z-artifacts/manifest.json": "gateway-config-backups",
            ".ghost-alice/install/2026-10-22-000019.events.jsonl": "install-logs",
            ".ghost-alice/uninstall-reports/codex-confirm-20261021T150111Z.json": "uninstall-reports",
            ".ghost-alice/uninstall-backup/codex/support-state/pending-merges-517xy6wp/original/manifest.json":
                "uninstall-backup",
            ".ghost-alice/state-retention-failures.jsonl": "retention-failures",
            ".ghost-alice/rollbacks/20261021T150020837001Z-9768-5351ef10/journal.json": "publish-rollbacks",
            ".claude/settings.json.bak": "hook-settings-backups",
            ".claude/skills/task-router": "installed-skills",
            ".agents/skills/_shared/state_retention.py": "installed-skills",
        }
        for relative, name in cases.items():
            with self.subTest(path=relative):
                self.assertEqual(state_retention.entry_for(self.home / relative, self.roots), name)

    def test_a_staging_folder_left_after_a_publish_matches_no_entry(self):
        for relative in (".claude/skills/.ghost-alice-install-staging",
                         ".ghost-alice/runtime/current/.ghost-alice-install-staging",
                         ".ghost-alice/unknown-new-file.jsonl"):
            with self.subTest(path=relative):
                self.assertIsNone(state_retention.entry_for(self.home / relative, self.roots))

    def test_an_empty_folder_matches_when_an_entry_lives_under_it(self):
        self.assertEqual(state_retention.entry_for(self.home / ".ghost-alice/install-state", self.roots, directory=True),
                         "install-record")
        self.assertIsNone(state_retention.entry_for(self.home / ".ghost-alice/empty-new-folder", self.roots,
                                                    directory=True))


if __name__ == "__main__":
    unittest.main()
