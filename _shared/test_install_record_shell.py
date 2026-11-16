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
import re
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
from installer_testing import checkout_copy, installer_env, run_pty  # noqa: E402

COMPANION = "session-intent-analyzer/scripts/session_intent_ledger.py"


def _target_names() -> list[str]:
    """`_shared` and every core skill target the checkout installs on one host."""
    return ["_shared", *[target.name for skill in selection.ALL_SKILLS for target in selection.expand_skill_targets(skill)]]


class InstallRecordShellTest(unittest.TestCase):
    def setUp(self):
        self.bash = _find_test_bash()
        if not self.bash:
            self.skipTest("bash required")
        if sys.version_info < (3, 11):
            self.skipTest("python 3.11+ required")
        self.source = checkout_copy(self)
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.ghost = self.home / ".ghost-alice"
        self.claude = self.home / ".claude" / "skills"
        self.codex = self.home / ".agents" / "skills"
        self.runtime = self.ghost / "runtime" / "current" / "_shared"
        self.companion = self.ghost / "runtime" / "current" / COMPANION
        self.commands = {str(self.ghost / 'bin' / name) for name in ('ghost-alice', 'ghost-alice.ps1', 'ghost-alice.cmd')}

    def tearDown(self):
        self.temp.cleanup()

    def run_installer(self, *args):
        env = {key: value for key, value in os.environ.items() if key not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        env.update(HOME=str(self.home), GHOST_ALICE_INSTALL_PROGRESS="off")
        result = subprocess.run([self.bash, str(self.source / "install.sh"), *args], cwd=self.source, env=env,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                                check=False)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        return result.stdout + result.stderr

    def head(self) -> str:
        return subprocess.run(["git", "-C", str(self.source), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()

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
                    self.runtime.as_posix(): ["claude", "codex"], self.companion.as_posix(): ["claude", "codex"],
                    **{path: ['claude', 'codex'] for path in self.commands}}
        targets = record["targets"]
        self.assertEqual({dest: entry["used_by"] for dest, entry in targets.items()}, expected)
        for dest, entry in targets.items():
            with self.subTest(dest=dest):
                self.assertEqual((entry["mode"], entry["baseline"], entry["provider"]), ("copy", "recorded", "core"))
        self.assertEqual(targets[self.runtime.as_posix()]["kind"], "runtime")
        # The service's companion script is published with the runtime, by the same publish.
        companion = targets[self.companion.as_posix()]
        self.assertEqual((companion["kind"], companion["name"]), ("runtime", COMPANION))
        active = Path(record['installer']['active_root'])
        self.assertEqual(companion["source"], str(active / COMPANION))
        self.assertEqual(companion["files"], {".": content_digest.file_digest(self.companion)})
        self.assertEqual(targets[(self.codex / "_shared").as_posix()]["kind"], "support")
        self.assert_matches_the_disk(targets)
        head = self.head()
        self.assertEqual(record["installer"], {"commit": head, "schema_version": installed_payload.SCHEMA_VERSION,
                                               "active_root": str(active)})
        self.assertEqual((record["source"]["root"], record["source"]["commit"]), (str(self.source), head))
        journal = Path(record["last_transaction"]["rollback_dir"]) / "journal.json"
        self.assertFalse(journal.exists(), 'completed managed originals must not accumulate after success')
        self.assertEqual(install_transaction.unfinished(self.ghost), [])
        # No gateway is registered in a temporary home: the record has no service section and the
        # installer writes no gateway receipt or binding.
        self.assertNotIn("service", record)
        self.assertFalse((self.ghost / "context-gateway-binding.json").exists())
        status = self.run_installer("--platform", "claude", "--status")
        self.assertIn(f"{len(names) + 5} claude targets (link 0, copy {len(names) + 5}", status)
        self.assertIn("[OK] Installed targets match the install record", status)
        changed = self.source / 'task-router/SKILL.md'
        changed.write_text(changed.read_text() + '\nSelected host update.\n')
        self.run_installer('--platform', 'codex', '--skip-source-health', 'task-router')
        doctor = self.run_installer('--platform', 'claude', '--doctor')
        self.assertIn('golden-hook-test/session-intent: golden-pass', doctor)

    def test_fixed_command_uses_the_successful_package_after_the_input_checkout_is_removed(self):
        import ledger_access
        ledger = ledger_access.load_ledger()
        legacy_state = ledger.default_state('future-host', 'legacy-session')
        legacy_path = ledger.session_paths(self.ghost / 'session-intent', 'future-host', 'legacy-session')['state']
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text(json.dumps(legacy_state))
        legacy_bytes = legacy_path.read_bytes()
        (self.home / '.claude').mkdir()
        self.run_installer('--platform', 'claude', '--skip-source-health', 'task-router')
        active = Path(self.record()['installer']['active_root'])
        self.assertNotEqual(active, self.source)
        self.assertFalse((self.claude / 'task-router').is_symlink())
        self.assertEqual(legacy_path.read_bytes(), legacy_bytes)
        self.assertEqual(ledger.read_session_state(root=self.ghost / 'session-intent', platform='future-host',
                                                  session_id='legacy-session'), legacy_state)
        config = json.loads((self.ghost / 'config.json').read_text())
        self.assertEqual(Path(config['install_locations']['claude']['ledger_root']), self.ghost / 'session-intent')
        shutil.rmtree(self.source)
        from installer_testing import installer_env
        result = subprocess.run([self.bash, str(self.ghost / 'bin/ghost-alice'), '--status'],
                                env=installer_env(self.home), cwd=self.home, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('[OK] Installed targets match the install record', result.stdout)
        self.assertTrue((self.claude / 'task-router/SKILL.md').is_file())

    def custom_runtime_removal(self, *, full=False):
        from installer_testing import installer_env
        runtime = self.home / "user's runtime" / '_shared'
        sibling = runtime.parent / 'user.txt'
        sibling.parent.mkdir()
        sibling.write_text('user data')
        (self.home / '.claude').mkdir()
        with mock.patch.dict(os.environ, {'GHOST_ALICE_RUNTIME_SHARED_DIR': str(runtime)}):
            self.run_installer('--platform', 'claude', '--skip-source-health', 'task-router')
            env = installer_env(self.home)
            env['GHOST_ALICE_RUNTIME_SHARED_DIR'] = str(runtime)
            shutil.rmtree(self.source)
            command = [self.bash, str(self.ghost / 'bin/ghost-alice')]
            removal = ['--platform', 'claude', '--uninstall', *([] if full else ['task-router'])]
            for args in (['--status'], removal):
                result = subprocess.run([*command, *args], env=env, cwd=self.home,
                                        capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(runtime.exists())
        self.assertFalse((runtime.parent / COMPANION).exists())
        self.assertEqual(sibling.read_text(), 'user data')
        if full:
            self.assertIsNone(installed_payload.read(self.ghost))
        else:
            self.assertEqual(self.record()['targets'], {})
            self.assertIsNone(self.record()['installer']['active_root'])

    def test_custom_runtime_selected_removal_uses_recorded_destinations(self):
        self.custom_runtime_removal()

    def test_custom_runtime_full_removal_uses_recorded_destinations(self):
        self.custom_runtime_removal(full=True)

    def test_one_host_failure_keeps_previous_active_cli_and_retry_activates_the_candidate(self):
        from installer_testing import installer_env
        (self.home / '.claude').mkdir()
        (self.home / '.codex').mkdir()
        self.run_installer('--skip-source-health', 'task-router', 'adversarial-verification',
                           'coding-convention/verification-before-completion', 'skill-evolution')
        active = self.record()['installer']['active_root']
        policy_names = ('evaluator-artifact-contract.md', 'live-smoke-regression.md')
        original_policies = {name: (self.source / 'docs/policies' / name).read_bytes() for name in policy_names}
        for name, raw in original_policies.items():
            (self.source / 'docs/policies' / name).write_bytes(raw + b'\nCandidate policy revision.\n')
        skill = self.source / 'task-router/SKILL.md'
        skill.write_text(skill.read_text() + '\nCandidate bytes.\n')
        config = self.home / '.codex/config.toml'
        before = config.read_bytes()
        # Inject the second host's failure after the parent's trust preflight;
        # malformed TOML present at entry correctly stops the whole request earlier.
        entry = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
from installer import cli
config = Path(sys.argv.pop(1))
prepare = cli.source.prepare_source
def fail_second_host(ctx, **kwargs):
    candidate = prepare(ctx, **kwargs)
    config.write_text('[invalid TOML')
    return candidate
cli.source.prepare_source = fail_second_host
raise SystemExit(cli.main(sys.argv[1:]))
'''
        result = subprocess.run([sys.executable, '-c', entry, str(self.source / '_shared'), str(config),
                                 '--skip-source-health', 'task-router'],
                                env=installer_env(self.home), cwd=self.home, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        record = self.record()
        self.assertEqual(record['installer']['active_root'], active)
        installed_source = Path(record['targets'][str(self.claude / 'task-router')]['source'])
        candidate = installed_source.parent.parent
        self.assertNotEqual(str(installed_source.parent), active)
        self.assertIn('Candidate bytes.', (self.claude / 'task-router/SKILL.md').read_text())
        self.assertEqual(json.loads((candidate / 'version.json').read_text())['status'], 'candidate')
        config.write_bytes(before)
        shutil.rmtree(self.source)
        # The old installed consumer skills read each host's actual support placement,
        # independently of which package remains the active management CLI.
        for host, skills in (('claude', self.claude), ('codex', self.codex)):
            support = record['targets'][str(skills / '_shared')]
            self.assertIn(host, support['used_by'])
            package = Path(support['source']).parent
            for consumer, policy in (('adversarial-verification/SKILL.md', policy_names[0]),
                    ('verification-before-completion/references/evaluator-artifacts.md', policy_names[0]),
                    ('skill-evolution/SKILL.md', policy_names[1])):
                instruction = (skills / consumer).read_text()
                self.assertIn('installed-policies.md', instruction)
                self.assertIn(policy, instruction)
                expected_policy = original_policies[policy] + b'\nCandidate policy revision.\n'
                self.assertEqual((package / 'docs/policies' / policy).read_bytes(), expected_policy)
                self.assertIn('## Decision Flow',
                    (package / 'coding-convention/verification-before-completion/references/verify-or-reuse.md').read_text())
            for language in ('', 'ko/'):
                for name in policy_names:
                    policy_file = package / f'docs/{language}policies' / name
                    for reference in re.findall(r'\]\(([^)]+)\)', policy_file.read_text()):
                        path = reference.split('#', 1)[0]
                        if path and '://' not in path:
                            self.assertTrue((policy_file.parent / path).resolve().is_file(), reference)
        previous_manager = subprocess.run([self.bash, str(self.ghost / 'bin/ghost-alice'), '--platform', 'claude',
                                          '--uninstall', 'task-router'], cwd=self.home, env=installer_env(self.home),
                                          capture_output=True, text=True, timeout=180)
        self.assertEqual(previous_manager.returncode, 0, previous_manager.stdout + previous_manager.stderr)
        self.assertFalse((self.claude / 'task-router').exists())
        retry = subprocess.run([self.bash, str(candidate / 'core/install.sh'), '--skip-source-health'],
                               cwd=self.home, env=installer_env(self.home), capture_output=True, text=True, timeout=180)
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertEqual(self.record()['installer']['active_root'], str(candidate / 'core'))
        self.assertEqual(json.loads((candidate / 'version.json').read_text())['status'], 'successful')

    def test_legacy_core_links_become_copies_even_when_their_content_is_current(self):
        (self.home / ".claude").mkdir()
        self.run_installer("--platform", "claude", "--skip-source-health")
        record = self.record()
        active = Path(record['installer']['active_root'])
        names = _target_names()
        for name in names:
            target = self.claude / name
            relative = Path(record['targets'][str(target)]['source']).relative_to(active)
            shutil.rmtree(target)
            target.symlink_to(self.source / relative)
        for name in installed_payload.RECORD_FILENAMES:
            (self.ghost / name).unlink()
        (self.ghost / "install-state" / "claude.json").unlink()
        self.run_installer("--platform", "claude", "--skip-source-health")
        targets = self.record()["targets"]
        self.assertEqual(set(targets), {(self.claude / name).as_posix() for name in names}
                         | {self.runtime.as_posix(), self.companion.as_posix()} | self.commands)
        self.assertFalse(any((self.claude / name).is_symlink() for name in names))
        self.assertEqual({entry["baseline"] for entry in targets.values()}, {"recorded"})
        self.assert_matches_the_disk(targets)

    def test_saved_code_reinstall_keeps_current_selection_and_ledger_binding(self):
        (self.home / '.claude').mkdir()
        self.run_installer('--platform', 'claude', '--skip-source-health', 'task-router')
        first = Path(self.record()['installer']['active_root'])
        skill = self.source / 'task-router/SKILL.md'
        original = skill.read_bytes()
        skill.write_bytes(original + b'\nLater source revision.\n')
        self.run_installer('--platform', 'claude', '--skip-source-health', 'task-router', 'compact-handoff')
        expected = {path for path, entry in self.record()['targets'].items() if entry['kind'] == 'skill'}
        origin_fields = ('root', 'remote', 'ref', 'branch', 'hooks_path', 'ledger_source_roots')
        expected_source = {key: self.record()['source'].get(key) for key in origin_fields}
        config_path = self.ghost / 'config.json'
        config = json.loads(config_path.read_text())
        config['retired_ledger_roots'] = [str(self.source / '.tmp/session-intent')]
        config_path.write_text(json.dumps(config))
        ledger = self.ghost / 'session-intent/retained.txt'
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('current history')
        shutil.rmtree(self.source)
        (self.home / '.codex').mkdir()  # A newly detected host is not an installed selection.
        result = subprocess.run([self.bash, str(first / 'install.sh'), '--adopt-source', '--skip-source-health'],
                                cwd=self.home, env=installer_env(self.home), capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({path for path, entry in self.record()['targets'].items() if entry['kind'] == 'skill'}, expected)
        self.assertEqual((self.claude / 'task-router/SKILL.md').read_bytes(), original)
        self.assertEqual(ledger.read_text(), 'current history')
        after = json.loads(config_path.read_text())
        self.assertEqual(after['retired_ledger_roots'], config['retired_ledger_roots'])
        self.assertEqual(after['install_locations']['claude']['ledger_root'], str(ledger.parent))
        self.assertEqual({key: self.record()['source'].get(key) for key in origin_fields}, expected_source)

    def test_fixed_update_uses_remote_after_clone_removal_and_keeps_each_host_selection(self):
        (self.home / '.claude').mkdir()
        (self.home / '.codex').mkdir()
        remote = self.home / 'remote.git'
        subprocess.run(['git', 'clone', '--bare', str(self.source), str(remote)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.source), 'remote', 'add', 'origin', str(remote)], check=True)
        branch = subprocess.run(['git', '-C', str(self.source), 'branch', '--show-current'],
                                check=True, capture_output=True, text=True).stdout.strip()
        self.run_installer('--platform', 'claude', '--skip-source-health', 'task-router')
        self.run_installer('--platform', 'codex', '--skip-source-health', 'coding-convention/verification-before-completion')
        before = self.record()
        expected = {path: (entry['name'], entry['used_by']) for path, entry in before['targets'].items() if entry['kind'] == 'skill'}
        skill = self.source / 'task-router/SKILL.md'
        skill.write_text(skill.read_text() + '\nUpdated from the recorded remote.\n')
        subprocess.run(['git', '-C', str(self.source), '-c', 'core.hooksPath=/dev/null', 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.source), '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'new candidate'], check=True)
        commit = self.head()
        subprocess.run(['git', '-C', str(self.source), '-c', 'core.hooksPath=/dev/null', 'push', '-q', 'origin', branch], check=True)
        # The same remote also updates an installation whose records still use its source folder.
        legacy_home = self.home / 'source-bound-home'
        (legacy_home / '.claude').mkdir(parents=True)
        legacy_records = self.source / '.tmp/interactive-update-records'
        legacy_records.mkdir(parents=True)
        (legacy_records / 'retained.txt').write_text('existing work records')
        legacy_env = installer_env(legacy_home)
        legacy_env.update(GHOST_ALICE_SESSION_INTENT_ROOT=str(legacy_records), GHOST_ALICE_INSTALL_LOCALE='en_US.UTF-8')
        legacy_install = subprocess.run([self.bash, str(self.source / 'install.sh'), '--platform', 'claude',
            '--skip-source-health', 'task-router'], cwd=legacy_home, env=legacy_env,
            capture_output=True, text=True, timeout=180)
        self.assertEqual(legacy_install.returncode, 0, legacy_install.stdout + legacy_install.stderr)
        legacy_env.pop('GHOST_ALICE_SESSION_INTENT_ROOT')
        question = 'Move your existing work records to the new location? [Y/n]'
        code, output = run_pty([self.bash, str(legacy_home / '.ghost-alice/bin/ghost-alice'), 'update'],
                              cwd=legacy_home, env=legacy_env, responses=[(question, 'n\n')])
        self.assertEqual(code, 0, output)
        self.assertEqual(output.count('Ghost-ALICE update is complete.'), 1)
        self.assertEqual(output.count(question), 1)
        self.assertNotIn('Ghost-ALICE installation is complete.', output)
        self.assertEqual(json.loads((legacy_home / '.ghost-alice/config.json').read_text())
                         ['install_locations']['claude']['ledger_root'], str(legacy_records))
        self.assertEqual((legacy_records / 'retained.txt').read_text(), 'existing work records')
        shutil.rmtree(self.source)
        result = subprocess.run([self.bash, str(self.ghost / 'bin/ghost-alice'), 'update'],
                                cwd=self.home, env=installer_env(self.home), capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = self.record()
        self.assertEqual({path: (entry['name'], entry['used_by']) for path, entry in record['targets'].items() if entry['kind'] == 'skill'}, expected)
        self.assertEqual(record['source']['root'], str(self.source))
        self.assertEqual(record['source']['commit'], commit)
        self.assertIn('Updated from the recorded remote.', (self.claude / 'task-router/SKILL.md').read_text())
        self.assertNotEqual(record['installer']['active_root'], before['installer']['active_root'])

    def test_the_last_host_removal_takes_the_record_and_the_publish_rollbacks(self):
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        self.run_installer("--skip-source-health")
        record = installed_payload.path(self.ghost)
        rollbacks = install_transaction.rollbacks_root(self.ghost)
        self.run_installer("--platform", "claude", "--uninstall")
        # Codex is still installed: the shared record and rollbacks stay for it.
        self.assertTrue(record.exists())
        self.assertTrue(rollbacks.is_dir())
        self.assertEqual(list(rollbacks.iterdir()), [])
        self.assertTrue((self.ghost / "install-state" / "codex.json").is_file())
        self.run_installer("--platform", "codex", "--uninstall")
        self.assertFalse(record.exists())
        self.assertFalse((self.ghost / installed_payload.LEGACY_FILENAME).exists())
        self.assertFalse(rollbacks.exists())
        self.assertFalse(any(Path(path).exists() for path in self.commands))
        self.assertTrue(any((self.ghost / 'versions').iterdir()))
        archived = list((self.ghost / "uninstall-backup" / "codex" / "support-state").glob("publish-rollbacks-*"))
        self.assertEqual(archived, [], 'an empty placement folder needs no recovery copy')

    def test_removal_preserves_common_assets_owned_by_another_recorded_host(self):
        (self.home / '.claude').mkdir()
        self.run_installer('--platform', 'claude', '--skip-source-health', 'task-router')
        shared = {path: dict(entry, used_by=['fixture-host']) for path, entry in self.record()['targets'].items()
                  if entry['kind'] in {'runtime', 'command'}}
        with install_lock.command_lock(self.ghost / 'install.lock', owner='test:other-host'):
            installed_payload.update(self.ghost, {'targets': shared})
        self.run_installer('--platform', 'claude', '--uninstall')
        record = self.record()
        self.assertEqual({tuple(entry['used_by']) for entry in record['targets'].values()}, {('fixture-host',)})
        self.assertTrue(all(Path(path).exists() for path in record['targets']))
        self.assertTrue(Path(record['installer']['active_root']).is_dir())

    def test_selected_last_core_removal_retires_management_after_clone_is_deleted(self):
        from installer_testing import installer_env
        (self.home / '.claude').mkdir()
        self.run_installer('--platform', 'claude', '--skip-source-health', 'task-router')
        saved = Path(self.record()['installer']['active_root'])
        ledger = self.ghost / 'session-intent/retained.txt'
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('history')
        shutil.rmtree(self.source)
        result = subprocess.run([self.bash, str(self.ghost / 'bin/ghost-alice'), '--platform', 'claude',
                                 '--uninstall', 'task-router'], cwd=self.home, env=installer_env(self.home),
                                capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.claude / 'task-router').exists())
        self.assertFalse(self.runtime.exists())
        self.assertFalse(any(Path(path).exists() for path in self.commands))
        self.assertFalse((installed_payload.read(self.ghost) or {}).get('installer', {}).get('active_root'))
        self.assertEqual(ledger.read_text(), 'history')
        self.assertTrue(any((self.ghost / 'versions').iterdir()))
        (self.home / '.codex').mkdir()
        restored = subprocess.run([self.bash, str(saved / 'install.sh'), '--skip-source-health'],
                                 cwd=self.home, env=installer_env(self.home), capture_output=True, text=True, timeout=180)
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertEqual([(row['name'], row['used_by']) for row in self.record()['targets'].values()
                          if row['kind'] == 'skill'], [('task-router', ['claude'])])
        self.assertEqual(ledger.read_text(), 'history')

    def test_the_first_install_over_earlier_records_creates_the_record_from_them(self):
        (self.home / ".codex").mkdir()
        self.run_installer("--platform", "codex", "--skip-source-health")
        for name in installed_payload.RECORD_FILENAMES:
            (self.ghost / name).unlink()
        shutil.rmtree(install_transaction.rollbacks_root(self.ghost))
        names = _target_names()
        out = self.run_installer("--platform", "codex", "--skip-source-health", "--verbose", "task-router")
        self.assertIn(f"Install record created from earlier install records: {len(names) + 2} targets "
                      f"(migrated {len(names)}, unknown 2)", out)
        targets = self.record()["targets"]
        republished = {(self.codex / "_shared").as_posix(), (self.codex / "task-router").as_posix(),
                       self.runtime.as_posix(), self.companion.as_posix()} | self.commands
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
            generation = install_lock.acquire_lock(self.ghost / "install.lock", owner="test")
            try:
                with mock.patch.object(install_transaction.PublishBatch, "finalize", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        install_transaction.publish(request)
            finally:
                install_lock.release_lock(self.ghost / "install.lock", generation)
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
