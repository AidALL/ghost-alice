"""installer.git_hooks: the fixed Git hooks folder, the repository's shared `core.hooksPath` and the recorded values.

Every test uses a temporary HOME and a repository of its own. The fixture package holds only `hooks/post-merge`.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_write  # noqa: E402
import install_transaction  # noqa: E402
from installer import cli, git_hooks, locations, report, runner, source  # noqa: E402
from installer_testing import make_context, use_source  # noqa: E402
from test_install_transaction_publish import PublishCase  # noqa: E402
from test_installer_source import SourceCase, git, init_repo  # noqa: E402

SCRIPT = b"#!/usr/bin/env bash\nexit 0\n"


class GitHooksCase(SourceCase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_LOCALE": "en", "LC_ALL": "C"}))
        self.addCleanup(report.configure, tty=False, progress_env="off", home=self.root / "home")
        report.configure(tty=False, progress_env="auto", home=self.root / "home")
        self.package = self.root / "package"
        (self.package / "hooks").mkdir(parents=True)
        (self.package / "hooks" / "post-merge").write_bytes(SCRIPT)
        self.enterContext(use_source(self.package))
        self.repo = init_repo(self.root / "repo").resolve()
        self.ghost = self.root / "home" / ".ghost-alice"
        self.ghost.mkdir()
        self.state = self.ghost / "install-state"
        self.folder = self.ghost / "git-hooks"
        self.hook = self.folder / "post-merge"
        self.record = self.folder / "repositories.json"
        self.fixed = (self.ghost.resolve() / "git-hooks").as_posix()

    def register(self, root: Path | None = None) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            git_hooks.register(self.repo if root is None else root)
        return out.getvalue()

    def value(self, repo: Path | None = None) -> str | None:
        found = subprocess.run(["git", "-C", str(repo or self.repo), "config", "--local", "--get", "core.hooksPath"],
                               capture_output=True, text=True)
        return found.stdout.strip() if found.returncode == 0 else None

    def set_value(self, value: str | None, repo: Path | None = None) -> None:
        if value is None:
            git(repo or self.repo, "config", "--local", "--unset-all", "core.hooksPath")
        else:
            git(repo or self.repo, "config", "--local", "core.hooksPath", value)

    def row(self, repo: Path | None = None) -> dict:
        """The one recorded row of a repository."""
        common = (repo or self.repo) / ".git"
        found, = [row for row in json.loads(self.record.read_text(encoding="utf-8"))
                  if row.get("common_dir") == common.as_posix()]
        return found

    def restores(self, repo: Path | None = None) -> str | None:
        row = self.row(repo)
        self.assertEqual(row["before_present"], row["before"] is not None)
        return row["before"]

    def older_row(self, after: str, before: str | None, repo: Path | None = None) -> dict:
        return {"kind": "source_repo_hook_path", "repo_root": (repo or self.repo).as_posix(),
                "before_present": before is not None, "before": before, "after": after,
                "applied_at": "2026-10-01T00:00:00Z"}

    def write_older_record(self, after: str, before: str | None, repo: Path | None = None) -> Path:
        path = self.state / "source-repo-hook-path.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.older_row(after, before, repo)), encoding="utf-8")
        return path

    def write_inventory(self, platform: str, *rows: dict) -> Path:
        path = self.state / f"{platform}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "platform": platform, "targets": [],
                                    "system_env_changes": list(rows)}), encoding="utf-8")
        return path

    def package_hooks(self, name: str = "a" * 64) -> str:
        """The value that an earlier release wrote for a saved version."""
        return (self.ghost.resolve() / "versions" / name / "core" / "hooks").as_posix()

    def effects(self) -> dict[str, tuple]:
        """Every path of the record folder and the repository's Git configuration, with its bytes and mode."""
        found = {}
        for path in [*sorted(self.ghost.rglob("*")), self.repo / ".git" / "config"]:
            content = path.read_bytes() if path.is_file() else None
            found[str(path)] = content, stat.S_IMODE(path.lstat().st_mode)
        return found

    def refused(self, root: Path | None = None) -> str:
        """A registration that stops: the record folder and the Git configuration stay as they are."""
        before = self.effects()
        with self.assertRaises(ValueError) as stopped:
            self.register(root)
        self.assertEqual(self.effects(), before)
        return str(stopped.exception)


class RegistrationTests(GitHooksCase):
    def test_the_first_registration_writes_the_script_the_row_and_the_git_value(self):
        out = self.register()
        self.assertEqual(self.value(), self.fixed)
        self.assertEqual(self.hook.read_bytes(), SCRIPT)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o755)
        row = self.row()
        self.assertTrue(row.pop("applied_at"))
        self.assertEqual(row, {"kind": "source_repo_hook_path", "repo_root": self.repo.as_posix(),
                               "common_dir": (self.repo / ".git").as_posix(), "before_present": False,
                               "before": None, "after": self.fixed})
        self.assertEqual(out, f"[INFO] Git hook path → {self.fixed} (post-merge auto-refresh enabled)\n")
        self.assertEqual(locations.git_hooks_dir(), self.folder)

    @unittest.skipIf(os.name == "nt", "Git for Windows runs the hook through its own shell")
    def test_a_merge_runs_the_registered_script(self):
        (self.package / "hooks" / "post-merge").write_bytes(
            b'#!/bin/sh\ntouch "$(dirname -- "$0")/ran"\n')
        self.register()
        git(self.repo, "checkout", "-q", "-b", "topic")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "two")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "merge", "-q", "--ff-only", "topic")
        self.assertTrue((self.folder / "ran").is_file())

    def test_registering_again_writes_nothing(self):
        self.set_value("elsewhere")
        self.register()
        before = self.effects()
        times = {path: path.stat().st_mtime_ns for path in (self.hook, self.record, self.repo / ".git/config")}
        self.assertEqual(self.register(), "")
        self.assertEqual(self.effects(), before)
        self.assertEqual({path: path.stat().st_mtime_ns for path in times}, times)

    def test_a_value_from_elsewhere_is_the_value_to_restore(self):
        for value in ("elsewhere", "custom/hooks", "/opt/team/hooks", "~/hooks", ""):
            with self.subTest(value=value):
                self.set_value(value)
                self.register()
                self.assertEqual(self.value(), self.fixed)
                self.assertEqual(self.restores(), value)

    def test_the_row_is_written_before_the_git_value(self):
        self.set_value("elsewhere")
        run, seen = runner.run_command, []

        def observe(argv, **kwargs):
            if argv[-2:] == ["core.hooksPath", self.fixed]:
                seen.append((self.restores(), self.hook.read_bytes(), self.value()))
            return run(argv, **kwargs)

        with mock.patch.object(runner, "run_command", observe):
            self.register()
        self.assertEqual(seen, [("elsewhere", SCRIPT, "elsewhere")])

    def test_an_interruption_before_the_git_write_keeps_the_value_to_restore(self):
        run = runner.run_command

        def interrupt(argv, **kwargs):
            if argv[-2:] == ["core.hooksPath", self.fixed]:
                raise KeyboardInterrupt
            return run(argv, **kwargs)

        checkout_hooks = (self.repo / "hooks").as_posix()
        for current, recorded, expected in ((None, False, None), ("elsewhere", False, "elsewhere"),
                                            (self.package_hooks(), True, "custom"), (checkout_hooks, True, "custom"),
                                            ("hooks", True, "custom")):
            with self.subTest(current=current):
                shutil.rmtree(self.folder, ignore_errors=True)
                self.set_value("placeholder")
                self.set_value(current)
                self.write_older_record(current if recorded else "/other/hooks", "custom")
                with mock.patch.object(runner, "run_command", interrupt), self.assertRaises(KeyboardInterrupt):
                    self.register()
                self.assertEqual(self.value(), current)
                self.assertEqual(self.restores(), expected)
                self.register()
                self.assertEqual(self.value(), self.fixed)
                self.assertEqual(self.restores(), expected)

    def test_a_value_written_after_the_registration_is_the_next_value_to_restore(self):
        self.set_value("elsewhere")
        self.register()
        self.set_value("chosen-later")
        self.register()
        self.assertEqual((self.value(), self.restores()), (self.fixed, "chosen-later"))

    def test_a_new_release_replaces_the_script_and_keeps_the_rows(self):
        self.set_value("elsewhere")
        self.register()
        rows = self.record.read_bytes()
        (self.package / "hooks" / "post-merge").write_bytes(b"#!/usr/bin/env bash\nexit 7\n")
        self.hook.chmod(0o644)
        self.assertEqual(self.register(), "")
        self.assertEqual(self.hook.read_bytes(), b"#!/usr/bin/env bash\nexit 7\n")
        if os.name != "nt":
            self.assertTrue(self.hook.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(self.record.read_bytes(), rows)

    def test_another_hook_in_the_folder_stays(self):
        self.folder.mkdir()
        (self.folder / "pre-commit").write_text("mine")
        self.register()
        self.assertEqual((self.folder / "pre-commit").read_text(), "mine")

    def test_worktrees_share_the_registration_and_keep_their_own_setting(self):
        worktree = (self.root / "worktree").resolve()
        git(self.repo, "worktree", "add", "-q", "-b", "work", str(worktree))
        git(self.repo, "config", "--local", "extensions.worktreeConfig", "true")
        git(worktree, "config", "--worktree", "core.hooksPath", "/dev/null")
        self.set_value("elsewhere")
        self.register(worktree)
        self.assertEqual(self.row()["repo_root"], worktree.as_posix())
        rows = self.record.read_bytes()
        self.assertEqual(self.register(self.repo), "")
        self.assertEqual(self.record.read_bytes(), rows)
        self.assertEqual((self.value(), self.restores()), (self.fixed, "elsewhere"))
        self.assertEqual(git(worktree, "config", "--worktree", "--get", "core.hooksPath"), "/dev/null")
        self.assertEqual(git(worktree, "config", "--get", "core.hooksPath"), "/dev/null")

    def test_the_rows_of_other_repositories_stay(self):
        other = init_repo(self.root / "other").resolve()
        self.set_value("theirs", other)
        self.register(other)
        kept = self.row(other)
        self.set_value("elsewhere")
        self.register()
        self.assertEqual(self.row(other), kept)
        self.assertEqual((self.restores(), self.restores(other)), ("elsewhere", "theirs"))
        self.assertEqual((self.value(), self.value(other)), (self.fixed, self.fixed))

    def test_a_repository_never_takes_the_value_of_another_repository(self):
        other = init_repo(self.root / "other").resolve()
        self.set_value("theirs", other)
        self.register(other)
        self.set_value(self.fixed)
        self.register()
        self.assertIsNone(self.restores())
        self.assertEqual(self.restores(other), "theirs")

    def test_a_moved_repository_keeps_the_value_to_restore(self):
        self.set_value("theirs")
        self.register()
        moved = self.repo.rename(self.repo.with_name("moved"))
        self.register(moved)
        self.assertEqual(self.restores(moved), "theirs")
        rows = git_hooks.rows()
        git(moved, "init", "-q", "--separate-git-dir", str(self.root / "store.git"))
        self.register(moved)
        self.assertEqual(git_hooks.rows(), rows)

    def test_a_moved_repository_takes_none_of_several_values(self):
        other = init_repo(self.root / "other").resolve()
        for repo, value in ((self.repo, "ours"), (other, "theirs")):
            self.set_value(value, repo)
            self.register(repo)
        self.repo.rename(self.repo.with_name("first"))
        moved = other.rename(other.with_name("second"))
        self.register(moved)
        self.assertIsNone(self.restores(moved))


class EarlierReleaseTests(GitHooksCase):
    """The first registration over an earlier release's value keeps the value from before that release."""

    def test_the_earlier_record_names_the_value_to_restore(self):
        for after in (self.package_hooks(), (self.repo / "hooks").as_posix(), "hooks"):
            for before in (None, "custom-hooks"):
                for place in ("record", "claude", "codex"):
                    with self.subTest(after=after, before=before, place=place):
                        shutil.rmtree(self.folder, ignore_errors=True)
                        shutil.rmtree(self.state, ignore_errors=True)
                        if place == "record":
                            earlier = self.write_older_record(after, before)
                        else:
                            earlier = self.write_inventory(place, {"kind": "other"}, self.older_row(after, before))
                        content = earlier.read_bytes()
                        self.set_value(after)
                        self.register()
                        self.assertEqual((self.value(), self.restores()), (self.fixed, before))
                        self.assertEqual(earlier.read_bytes(), content)
                        self.assertEqual(self.register(), "")

    def test_the_last_written_record_comes_before_the_host_inventories(self):
        self.write_inventory("claude", self.older_row(self.package_hooks(), "stale"))
        self.write_older_record(self.package_hooks(), "custom-hooks")
        self.set_value(self.package_hooks())
        self.register()
        self.assertEqual(self.restores(), "custom-hooks")

    def test_an_earlier_value_of_another_checkout_counts_by_its_absolute_path(self):
        self.write_older_record("/earlier/source/hooks", "custom-hooks", Path("/earlier/source"))
        self.set_value("/earlier/source/hooks")
        self.register()
        self.assertEqual(self.restores(), "custom-hooks")

    def test_two_repositories_with_one_earlier_value_keep_their_own_previous_values(self):
        other = init_repo(self.root / "other").resolve()
        self.write_older_record(self.package_hooks(), "theirs", other)
        self.write_inventory("claude", self.older_row(self.package_hooks(), "ours"))
        self.set_value(self.package_hooks())
        self.set_value(self.package_hooks(), other)
        self.register()
        self.register(other)
        self.assertEqual((self.restores(), self.restores(other)), ("ours", "theirs"))

    def test_the_relative_value_counts_only_within_its_own_repository(self):
        worktree = (self.root / "earlier-worktree").resolve()
        git(self.repo, "worktree", "add", "-q", "-b", "earlier", str(worktree))
        other = init_repo(self.root / "other").resolve()
        for recorded, expected in ((self.repo, "custom-hooks"), (worktree, "custom-hooks"), (other, None)):
            with self.subTest(recorded=recorded.name):
                shutil.rmtree(self.folder, ignore_errors=True)
                self.write_older_record("hooks", "custom-hooks", recorded)
                self.set_value("hooks")
                self.register()
                self.assertEqual((self.value(), self.restores()), (self.fixed, expected))

    def test_a_path_of_the_record_folder_without_a_row_is_never_restored(self):
        for value in (self.fixed, self.package_hooks(), (self.ghost / "versions/gone/core/hooks").as_posix(), "hooks"):
            with self.subTest(value=value):
                shutil.rmtree(self.folder, ignore_errors=True)
                self.set_value(value)
                self.register()
                self.assertEqual((self.value(), self.restores()), (self.fixed, None))

    def test_a_lost_row_file_falls_back_to_the_earlier_record(self):
        self.write_older_record(self.package_hooks(), "custom-hooks")
        self.set_value(self.package_hooks())
        self.register()
        shutil.rmtree(self.folder)
        self.register()
        self.assertEqual((self.value(), self.restores()), (self.fixed, "custom-hooks"))

    def test_an_earlier_release_installed_again_keeps_the_first_value(self):
        for lost in (False, True):
            with self.subTest(lost=lost):
                shutil.rmtree(self.folder, ignore_errors=True)
                self.set_value("placeholder")
                self.set_value("elsewhere")
                self.register()
                # The earlier release takes the fixed folder for another tool's value.
                self.write_older_record(self.package_hooks(), self.fixed)
                self.set_value(self.package_hooks())
                if lost:
                    shutil.rmtree(self.folder)
                self.register()
                self.assertEqual((self.value(), self.restores()), (self.fixed, None if lost else "elsewhere"))

    def test_the_earlier_rows_of_other_repositories_are_kept_for_the_uninstall(self):
        other = init_repo(self.root / "other").resolve()
        theirs = self.older_row(self.package_hooks("b" * 64), "theirs", other)
        self.write_inventory("claude", self.older_row(self.package_hooks(), "ours"), theirs)
        self.write_inventory("codex", theirs)
        self.set_value(self.package_hooks())
        self.register()
        rows = json.loads(self.record.read_text(encoding="utf-8"))
        self.assertEqual(rows.count(theirs), 1)
        self.assertEqual(rows[0], self.row())
        self.assertEqual(git_hooks.rows(), rows)

    def test_the_row_file_replaces_the_earlier_records(self):
        self.register()
        rows = git_hooks.rows()
        self.write_older_record(self.package_hooks(), "ignored")
        self.write_inventory("claude", self.older_row(self.package_hooks(), "ignored"))
        self.assertEqual(git_hooks.rows(), rows)
        self.assertEqual([row["after"] for row in rows], [self.fixed])

    def test_the_earlier_rows_are_read_once_each_in_order(self):
        self.assertEqual(git_hooks.rows(), [])
        record = self.older_row(self.package_hooks(), "custom-hooks")
        other = self.older_row("hooks", None, Path("/another/checkout"))
        self.write_older_record(self.package_hooks(), "custom-hooks")
        self.write_inventory("claude", dict(record, applied_at="2026-10-02T00:00:00Z"), other, {"kind": "other"}, "text")
        self.write_inventory("codex", other)
        (self.state / "broken.json").write_text("{broken")
        (self.state / "list.json").write_text("[]")
        with mock.patch.object(runner, "run_command", side_effect=AssertionError("the reader runs no command")):
            self.assertEqual(git_hooks.rows(), [record, other])


class SkippedRegistrationTests(GitHooksCase):
    def test_an_install_source_without_a_repository_changes_nothing(self):
        plain = self.root / "plain"
        plain.mkdir()
        for root in (plain, self.root / "missing"):
            with self.subTest(root=root.name):
                before = self.effects()
                out = self.register(root)
                self.assertIn("[INFO]", out)
                self.assertIn(str(root), out)
                self.assertEqual(self.effects(), before)

    def test_a_host_without_git_changes_nothing(self):
        before = self.effects()
        with mock.patch.object(runner, "run_command", return_value=runner.ToolResult(127, "", "no git")):
            out = self.register()
        self.assertIn("[INFO]", out)
        self.assertEqual(self.effects(), before)


class RefusedRegistrationTests(GitHooksCase):
    """A registration that cannot finish leaves the record folder and the Git configuration byte-identical."""

    def test_a_repository_that_git_cannot_read_stops(self):
        shutil.move(self.repo / ".git", self.root / "moved")
        broken = self.root / "broken"
        broken.mkdir()
        (broken / ".git").write_text(f"gitdir: {self.root / 'gone'}\n")
        (self.repo / ".git").mkdir()
        (self.repo / ".git" / "config").write_text("")
        for root in (broken, self.repo):
            with self.subTest(root=root.name):
                probe = subprocess.run(
                    ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
                    capture_output=True, text=True,
                )
                self.assertNotEqual(probe.returncode, 0)
                self.assertTrue(probe.stderr.strip())
                message = self.refused(root)
                self.assertIn(f"Git could not read the repository at {root}", message)
                self.assertIn(f"Diagnostic details: {probe.stderr.strip()}", message)
        self.assertFalse(self.folder.exists())

    def test_a_locked_git_configuration_stops_the_first_registration(self):
        self.set_value("elsewhere")
        lock = self.repo / ".git" / "config.lock"
        lock.write_text("")
        message = self.refused()
        self.assertIn(str(lock), message)
        self.assertIn(str(self.repo / ".git" / "config"), message)
        self.assertFalse(self.folder.exists())
        lock.unlink()
        self.register()
        self.assertEqual((self.value(), self.restores()), (self.fixed, "elsewhere"))

    def test_a_locked_git_configuration_restores_the_earlier_files(self):
        self.set_value("elsewhere")
        self.register()
        (self.package / "hooks" / "post-merge").write_bytes(b"#!/usr/bin/env bash\nexit 7\n")
        self.hook.chmod(0o600)
        self.set_value("chosen-later")
        (self.repo / ".git" / "config.lock").write_text("")
        self.refused()
        self.assertEqual(self.restores(), "elsewhere")

    def test_a_user_hook_in_the_folder_survives_a_stopped_first_registration(self):
        self.folder.mkdir()
        (self.folder / "pre-commit").write_text("mine")
        (self.repo / ".git" / "config.lock").write_text("")
        self.refused()
        self.assertEqual((self.folder / "pre-commit").read_text(), "mine")

    def test_several_values_stop(self):
        git(self.repo, "config", "--local", "--add", "core.hooksPath", "one")
        git(self.repo, "config", "--local", "--add", "core.hooksPath", "two")
        message = self.refused()
        self.assertIn(str(self.repo / ".git" / "config"), message)
        self.assertNotIn("config.lock", message)

    @unittest.skipIf(os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0,
                     "Requires POSIX permissions and a user without root rights")
    def test_a_folder_without_write_permission_stops(self):
        self.folder.mkdir()
        self.folder.chmod(0o555)
        self.set_value("elsewhere")
        self.assertIn(str(self.folder), self.refused())
        self.assertEqual(self.value(), "elsewhere")

    def test_an_occupied_path_stops(self):
        for name in ("git-hooks", "git-hooks/post-merge", "git-hooks/repositories.json"):
            with self.subTest(name=name):
                if self.folder.is_dir():
                    shutil.rmtree(self.folder)
                path = self.ghost / name
                if name == "git-hooks":
                    path.write_text("a file of the user")
                else:
                    path.mkdir(parents=True)
                self.refused()
                if name == "git-hooks":
                    path.unlink()

    def test_a_linked_path_stops_and_its_target_stays(self):
        for name in ("git-hooks", "git-hooks/post-merge", "git-hooks/repositories.json"):
            with self.subTest(name=name):
                path, target = self.ghost / name, self.root / "elsewhere" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                path.parent.mkdir(exist_ok=True)
                target.mkdir() if name == "git-hooks" else target.write_text("[]")
                path.symlink_to(target, target_is_directory=target.is_dir())
                self.refused()
                self.assertTrue(path.is_symlink())
                self.assertEqual(list(target.iterdir()) if target.is_dir() else target.read_text(),
                                 [] if target.is_dir() else "[]")
                path.unlink()

    def test_a_file_that_cannot_be_put_back_is_named_with_the_first_cause(self):
        (self.repo / ".git" / "config.lock").write_text("")
        with mock.patch.object(git_hooks, "_put_back", side_effect=OSError("restore denied")), \
                self.assertRaises(ValueError) as stopped:
            self.register()
        for cause in (str(self.repo / ".git" / "config.lock"), "restore denied"):
            self.assertIn(cause, str(stopped.exception))

    def test_a_failed_row_write_stops_before_the_git_value(self):
        self.set_value("elsewhere")
        write = atomic_write.write_bytes

        def fail(path, data, **kwargs):
            if Path(path).name == "repositories.json":
                raise OSError("row write denied")
            return write(path, data, **kwargs)

        with mock.patch.object(atomic_write, "write_bytes", fail):
            self.assertIn("row write denied", self.refused())
        self.assertFalse(self.folder.exists())

    def test_a_package_without_the_script_stops(self):
        (self.package / "hooks" / "post-merge").unlink()
        self.assertIn("post-merge", self.refused())
        self.assertFalse(self.folder.exists())

    def test_an_unreadable_row_file_stops(self):
        self.register()
        valid = json.loads(self.record.read_text(encoding="utf-8"))[0]
        for content in ("{broken", "{}", '["text"]', json.dumps([dict(valid, kind="other")]),
                        json.dumps([dict(valid, after="")]), json.dumps([dict(valid, repo_root=None)]),
                        json.dumps([dict(valid, common_dir=7)]), json.dumps([dict(valid, common_dir=".git")]),
                        json.dumps([dict(valid, before_present=True)]),
                        json.dumps([dict(valid, before="elsewhere")]), json.dumps([dict(valid, before_present="no")])):
            with self.subTest(content=content):
                self.record.write_text(content, encoding="utf-8")
                self.assertIn(str(self.record), self.refused())
                with self.assertRaisesRegex(ValueError, "repositories.json"):
                    git_hooks.rows()

    def test_an_unreadable_earlier_record_stops(self):
        self.set_value(self.package_hooks())
        path = self.write_older_record(self.package_hooks(), "custom-hooks")
        valid = json.loads(path.read_text(encoding="utf-8"))
        for content in ("{broken", "[]", json.dumps(dict(valid, before=None)), json.dumps(dict(valid, after=None))):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                self.assertIn(str(path), self.refused())
        self.assertFalse(self.folder.exists())

    def test_the_stop_is_explained_in_korean(self):
        (self.repo / ".git" / "config.lock").write_text("")
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_LOCALE": "ko"}):
            report.configure(tty=False, progress_env="auto", home=self.root / "home")
        message = self.refused()
        self.assertIn("진단 상세", message)
        self.assertIn(str(self.repo / ".git" / "config.lock"), message)
        self.assertIn("삭제", message)


class CandidateTests(GitHooksCase):
    """The candidate registers the folder under the install lock, before it places files."""

    def setUp(self):
        super().setUp()
        (self.package / "_shared").mkdir()
        (self.package / "_shared" / "product.py").write_text("product")
        with mock.patch.dict(os.environ, {source.REQUEST_ENV: ""}), contextlib.redirect_stdout(io.StringIO()):
            folder, _ = source.prepare_source(make_context(self.root / "home"))
        self.package = folder / "core"
        self.enterContext(use_source(self.package))

    def dispatch(self) -> tuple[int, str, list]:
        calls = []
        request = dict(package_root=str(self.package.resolve()), source=dict(root=str(self.repo), commit="c0ffee",
                       dirty="clean", remote="origin", ref=source.RELEASE_REF), addon_commits={})
        out = io.StringIO()

        def place(ctx):
            self.assertEqual(ctx.platform, "claude")
            calls.append(self.value())
            return 1

        with mock.patch.dict(os.environ, {source.REQUEST_ENV: json.dumps(request)}), \
                mock.patch.object(cli, "confirm_host_shutdown", return_value=True), \
                mock.patch.object(cli, "run_install", place), contextlib.redirect_stdout(out):
            code = cli.main(["--platform", "claude"])
        return code, out.getvalue(), calls

    def test_the_candidate_registers_before_it_places_files(self):
        self.set_value("elsewhere")
        _, out, calls = self.dispatch()
        self.assertEqual(calls, [self.fixed], out)
        self.assertEqual(self.restores(), "elsewhere")

    def test_a_failed_registration_stops_the_candidate_before_any_other_change(self):
        self.set_value("elsewhere")
        (self.repo / ".git" / "config.lock").write_text("")
        before = self.effects()
        code, out, calls = self.dispatch()
        self.assertEqual((code, calls), (1, []), out)
        self.assertIn("[ERROR]", out)
        self.assertIn("config.lock", out)
        left = {path: content for path, content in self.effects().items()
                if Path(path).name != "install.lock.guard"}
        self.assertEqual(left, before)


class InstalledCommandTests(PublishCase):
    """The script runs the installed command with bash."""

    def test_the_command_has_no_carriage_return_where_text_files_get_windows_line_endings(self):
        text = "#!/usr/bin/env bash\nexec bash install.sh\n"
        source_file = self.src / "install.sh"
        source_file.write_text(text, encoding="utf-8")
        dest = self.ghost / "bin" / "ghost-alice"
        target = dict(self.target("ghost-alice", dest, "copy", kind="command", source=source_file), command_text=text)
        write_text = Path.write_text

        def windows_write_text(path, data, encoding=None, errors=None, newline=None):
            data = data.replace("\n", "\r\n") if newline is None else data
            return write_text(path, data, encoding=encoding, errors=errors, newline=newline)

        with mock.patch.object(Path, "write_text", windows_write_text):
            install_transaction.publish(self.request("claude", target))
        self.assertEqual(dest.read_bytes(), text.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
