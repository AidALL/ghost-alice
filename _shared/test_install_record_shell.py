"""Shell e2e: one install record for both hosts, the first install over an earlier installer's records, and the
restore of a publish that stopped halfway.

The earlier-records home is made by this checkout's installer with the install record removed afterwards: the
installer still writes the ownership markers, the merge-companion snapshot, the addon sidecars and the
install-state exactly as the earlier installer did, and a shallow CI checkout has no earlier installer to run.

Run: python3 -m pytest _shared/test_install_record_shell.py -q
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from _shared.test_addon_installer import _find_test_bash

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED = REPO_ROOT / "_shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

import content_digest  # noqa: E402
import install_lock  # noqa: E402
import install_transaction  # noqa: E402
import installed_payload  # noqa: E402
from installer import selection  # noqa: E402


def _target_names() -> list[str]:
    """`_shared` and every core skill target the checkout installs on one host."""
    return ["_shared", *[target.name for skill in selection.ALL_SKILLS for target in selection.expand_skill_targets(skill)]]


def _head() -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


class InstallRecordShellTest(unittest.TestCase):
    def setUp(self):
        self.bash = _find_test_bash()
        if not self.bash:
            self.skipTest("bash required")
        if sys.version_info < (3, 11):
            self.skipTest("python 3.11+ required")
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.ghost = self.home / ".ghost-alice"
        self.claude = self.home / ".claude" / "skills"
        self.codex = self.home / ".agents" / "skills"
        self.runtime = self.ghost / "runtime" / "current" / "_shared"

    def tearDown(self):
        self.temp.cleanup()

    def run_installer(self, *args):
        env = {key: value for key, value in os.environ.items() if key not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        env.update(HOME=str(self.home), GHOST_ALICE_INSTALL_PROGRESS="off")
        result = subprocess.run([self.bash, str(REPO_ROOT / "install.sh"), *args], cwd=REPO_ROOT, env=env,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                                check=False)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        return result.stdout + result.stderr

    def record(self) -> dict:
        record = installed_payload.read(self.ghost)
        self.assertIsNotNone(record)
        return record

    def assert_matches_the_disk(self, targets: dict) -> None:
        for dest, entry in targets.items():
            with self.subTest(dest=dest):
                self.assertEqual(content_digest.compare(entry, Path(dest)),
                                 {"missing": [], "changed": [], "extra": [], "link_target_differs": False})

    def test_a_fresh_install_records_every_target_of_both_hosts_in_one_record(self):
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        self.run_installer("--skip-source-health")
        record = self.record()
        names = _target_names()
        expected = {**{(self.claude / name).as_posix(): ["claude"] for name in names},
                    **{(self.codex / name).as_posix(): ["codex"] for name in names},
                    self.runtime.as_posix(): ["claude", "codex"]}
        targets = record["targets"]
        self.assertEqual({dest: entry["used_by"] for dest, entry in targets.items()}, expected)
        for dest, entry in targets.items():
            with self.subTest(dest=dest):
                host_mode = "link" if dest.startswith(self.claude.as_posix()) else "copy"
                self.assertEqual((entry["mode"], entry["baseline"], entry["provider"]), (host_mode, "recorded", "core"))
        self.assertEqual(targets[self.runtime.as_posix()]["kind"], "runtime")
        self.assertEqual(targets[(self.codex / "_shared").as_posix()]["kind"], "support")
        self.assert_matches_the_disk(targets)
        head = _head()
        self.assertEqual(record["installer"], {"commit": head, "schema_version": 1})
        self.assertEqual((record["source"]["root"], record["source"]["commit"]), (str(REPO_ROOT), head))
        journal = Path(record["last_transaction"]["rollback_dir"]) / "journal.json"
        self.assertIn('"phase": "done"', journal.read_text(encoding="utf-8"))
        self.assertEqual(install_transaction.unfinished(self.ghost), [])
        status = self.run_installer("--platform", "claude", "--status")
        self.assertIn(f"{len(names) + 1} claude targets (link {len(names)}, copy 1", status)
        self.assertIn("[OK] Installed targets match the install record", status)

    def test_the_first_install_over_earlier_records_creates_the_record_from_them(self):
        (self.home / ".codex").mkdir()
        self.run_installer("--platform", "codex", "--skip-source-health")
        installed_payload.path(self.ghost).unlink()
        shutil.rmtree(install_transaction.rollbacks_root(self.ghost))
        names = _target_names()
        out = self.run_installer("--platform", "codex", "--skip-source-health", "--verbose", "task-router")
        self.assertIn(f"Install record created from earlier install records: {len(names)} targets "
                      f"(migrated {len(names)}, unknown 0)", out)
        targets = self.record()["targets"]
        republished = {(self.codex / "_shared").as_posix(), (self.codex / "task-router").as_posix(),
                       self.runtime.as_posix()}
        self.assertEqual(set(targets), {(self.codex / name).as_posix() for name in names} | republished)
        for dest, entry in targets.items():
            with self.subTest(dest=dest):
                self.assertEqual(entry["baseline"], "recorded" if dest in republished else "migrated")
                self.assertEqual(entry["used_by"], ["codex"])
        # A migrated copy keeps the fingerprints its ownership marker recorded at install.
        adversarial = targets[(self.codex / "adversarial-verification").as_posix()]
        marker = self.codex / "adversarial-verification" / content_digest.MARKER_FILENAME
        hashes = json.loads(marker.read_text(encoding="utf-8"))["content_hashes"]
        self.assertEqual(adversarial["files"], {rel: content_digest.PREFIX + value for rel, value in hashes.items()})
        self.assert_matches_the_disk(targets)

    def test_the_next_command_restores_a_publish_that_stopped_halfway(self):
        (self.home / ".codex").mkdir()
        self.run_installer("--platform", "codex", "--skip-source-health")
        dest = self.codex / "task-router"
        original = (dest / "SKILL.md").read_bytes()
        record_before = installed_payload.path(self.ghost).read_bytes()
        edited = self.home / "edited-task-router"
        edited.mkdir()
        (edited / "SKILL.md").write_text("an edit that must not stay\n", encoding="utf-8")
        request = {"schema_version": 1, "host": "codex", "ghost_root": str(self.ghost),
                   "installer": {"commit": "test", "schema_version": 1},
                   "source": {"root": str(edited), "commit": "test", "dirty": "clean", "hooks_path": "hooks"},
                   "targets": [{"name": "task-router", "kind": "skill", "provider": "core", "source": str(edited),
                                "source_commit": "test", "dest": str(dest), "mode": "copy"}]}
        # The publish stops after its files and record changed and before its journal says done.
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            install_lock.acquire_lock(self.ghost / "install.lock", owner="test")
            try:
                with mock.patch.object(install_transaction.PublishBatch, "finalize", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        install_transaction.publish(request)
            finally:
                install_lock.release_lock(self.ghost / "install.lock")
        self.assertEqual((dest / "SKILL.md").read_text(encoding="utf-8"), "an edit that must not stay\n")
        [txid] = install_transaction.unfinished(self.ghost)
        out = self.run_installer("--platform", "codex", "--repair")
        self.assertIn(f"Restored {dest} from {install_transaction.rollbacks_root(self.ghost) / txid}", out)
        self.assertIn("[OK] Repair complete: 0 re-provisioned, 0 left untouched", out)
        self.assertEqual((dest / "SKILL.md").read_bytes(), original)
        self.assertEqual(installed_payload.path(self.ghost).read_bytes(), record_before)
        self.assertEqual(install_transaction.unfinished(self.ghost), [])


if __name__ == "__main__":
    unittest.main()
