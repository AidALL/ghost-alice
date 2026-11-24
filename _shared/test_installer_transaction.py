"""installer.transaction: placement through install_transaction.publish in this process, with the gateway
participant the record names, and the records written after placement.

Expected arguments and texts come from installer_lib/install.sh:75-223, 411-610, 640-653 and installer_lib/targets.sh:248-338.
"""
from __future__ import annotations

import contextlib
import base64
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gateway_testing  # noqa: E402
import install_lock  # noqa: E402
import install_transaction  # noqa: E402
import installed_payload  # noqa: E402
import runtime_config  # noqa: E402
from context_gateway_model import LABEL  # noqa: E402
from installer import gateway_participant, locations, lock, report, selection, source, transaction  # noqa: E402
from installer.runner import ToolResult  # noqa: E402
from installer.selection import Target  # noqa: E402
from installer.source import SourceInfo  # noqa: E402
from installer_testing import FakeRunner, make_context, make_source, quiet, use_source  # noqa: E402

COMPANION = "session-intent-analyzer/scripts/session_intent_ledger.py"
CHECKOUT = Path(__file__).resolve().parents[1]


class TransactionCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.source = make_source(root / "src")
        self.enterContext(use_source(self.source))
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GHOST_ALICE_RUNTIME_SHARED_DIR", "GHOST_ALICE_GHOST_ROOT"):
            os.environ.pop(key, None)
        quiet(self.home)
        self.runner = FakeRunner()
        self.ghost = self.home / ".ghost-alice"

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def context(self, platform="claude", **options):
        ctx = make_context(self.home, platform, **options)
        ctx.skills_dir.mkdir(parents=True, exist_ok=True)
        return ctx

    @staticmethod
    def captured(fn, *args, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn(*args, **kwargs)
        return result, out.getvalue()

    def add_entry_files(self):
        """The files in the checkout that the management commands start."""
        for name in locations.MANAGEMENT_COMMANDS.values():
            (self.source / name).write_text(f"{name}\n", encoding="utf-8")

    def request_source(self) -> dict:
        return {"root": str(self.source), "commit": "abc123def", "dirty": "clean"}

    @contextlib.contextmanager
    def candidate(self, package: Path):
        """This process as the candidate that runs from the saved `package`."""
        request = {"package_root": str(package.resolve()), "source": self.request_source()}
        with use_source(package.resolve()), mock.patch.dict(os.environ, {source.REQUEST_ENV: json.dumps(request)}):
            yield


class OriginalCopyCleanupTests(TransactionCase):
    def setUp(self):
        super().setUp()
        import uninstall_cleanup
        self.cleanup = uninstall_cleanup
        self.ctx = self.context()
        self.target = self.ctx.skills_dir / 'alpha'

    def original(self, *, scoped=False, review=False):
        from installer_assets import write_ownership_marker
        self.target.mkdir()
        (self.target / 'SKILL.md').write_bytes(b'only old original\n')
        write_ownership_marker(self.target, platform='claude', asset_id='alpha',
            source_repo=str(self.source), source_commit='old', install_mode='copy')
        folder = (self.ctx.skills_dir / '.ghost-alice-install-rollbacks' if scoped
                  else self.ghost / 'install-rollbacks')
        folder.mkdir(parents=True)
        original = folder / 'rollback-alpha-20261001T000000Z-1-abcdef12'
        self.target.rename(original)
        if review:
            manifest = self.ghost / 'pending-merges/claude/manifest.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps(dict(version=1, entries=[dict(platform='claude',
                source_path=str(self.target), backup_path=str(original), decided=False)])))
        return original

    def completed(self, action='replacement-verified', *, destination=None):
        import content_digest
        dest = destination or self.target
        if action == 'replacement-verified':
            dest.mkdir(parents=True)
            (dest / 'SKILL.md').write_bytes(b'new version\n')
            self.ghost.mkdir(parents=True, exist_ok=True)
            (self.ghost / 'installed-payload.json').write_text(json.dumps(dict(schema_version=1, targets={
                str(dest): dict(name='alpha', kind='skill', provider='core', mode='copy',
                               baseline='recorded', used_by=['claude'], files=content_digest.tree(dest))})))
        return [dict(target_name='alpha', source_path=str(self.source / 'alpha'), dest_path=str(dest),
                     source_root=str(self.source), provider='core', action=action)]

    def clean(self, completed):
        return self.cleanup.cleanup_obsolete_copies(root=self.ghost, platform='claude', completed=completed)

    def test_new_host_location_removes_unused_managed_copy_without_inventing_its_old_path(self):
        original = self.original()
        new_target = self.home / 'new-claude/skills/alpha'
        rows = self.clean(self.completed(destination=new_target))
        self.assertFalse(original.exists())
        self.assertEqual(rows[0]['action'], 'removed')
        self.assertEqual(rows[0]['target_path'], str(new_target))
        self.assertNotIn('original_path', rows[0])

    def test_requested_removal_deletes_only_old_managed_copy_when_target_is_missing(self):
        original = self.original()
        rows = self.clean(self.completed('missing'))
        self.assertFalse(original.exists())
        self.assertEqual(rows[0]['action'], 'removed')

    def test_scoped_clean_original_is_removed_after_verified_replacement(self):
        original = self.original(scoped=True)
        rows = self.clean(self.completed())
        self.assertFalse(original.exists())
        self.assertEqual(rows[0]['target_path'], str(self.target))
        self.assertEqual(rows[0]['action'], 'removed')

    def test_pending_review_keeps_the_copy_it_actually_references(self):
        original = self.original(review=True)
        rows = self.clean(self.completed('missing'))
        self.assertEqual((original / 'SKILL.md').read_bytes(), b'only old original\n')
        self.assertEqual(rows[0]['reason'], 'pending-original-review')

    def test_same_target_pending_review_does_not_keep_an_unreferenced_old_copy(self):
        original = self.original()
        pending = self.ghost / 'pending-merges/claude'
        pending.mkdir(parents=True)
        user_copy = pending / 'user-original'
        user_copy.write_bytes(b'user changes\n')
        (pending / 'manifest.json').write_text(json.dumps(dict(version=1, entries=[dict(platform='claude',
            source_path=str(self.target), backup_path=str(user_copy), decided=False)])))
        rows = self.clean(self.completed())
        self.assertFalse(original.exists())
        self.assertEqual(rows[0]['action'], 'removed')
        self.assertEqual(user_copy.read_bytes(), b'user changes\n')

    def test_relative_link_ownership_cannot_be_inferred_from_the_current_target_parent(self):
        folder = self.ghost / 'install-rollbacks'
        folder.mkdir(parents=True)
        original = folder / 'rollback-alpha-20261001T000000Z-1-abcdef12'
        link = os.path.relpath(self.source / 'alpha', self.target.parent)
        original.symlink_to(link)
        completed = self.completed()
        rows = self.clean(completed)
        self.assertEqual(os.readlink(original), link)
        self.assertEqual(rows[0]['reason'], 'cleanup-scope-unconfirmed')
        # The older pending record can identify this link's original base.
        pending = self.ghost / 'pending-merges/claude/manifest.json'
        pending.parent.mkdir(parents=True)
        pending.write_text(json.dumps(dict(version=1, entries=[dict(platform='claude',
            source_path=str(self.target), backup_path=str(original), decided=True)])))
        rows = self.clean(completed)
        self.assertFalse(original.is_symlink())
        self.assertEqual(rows[0]['action'], 'removed')


class PlacementTests(TransactionCase):
    """The placement calls each build one publish request and call `install_transaction.publish` in this
    process with the participants the install record names."""

    def setUp(self):
        super().setUp()
        self.info = SourceInfo(root=self.source, branch="main", head="abc123def", dirty="clean")
        self.origin = {"root": str(self.source), "commit": "abc123def", "dirty": "clean",
                       "branch": "main", "hooks_path": "hooks", "remote": None, "ref": "main"}
        self.enterContext(mock.patch("installer.source.input_record", return_value=self.origin))
        self.requests: list[dict] = []
        self.participants: list[list] = []
        self.codes: list[int] = []
        self.chosen = [object()]
        self.enterContext(mock.patch("install_transaction.publish", side_effect=self.publish))
        self.enterContext(mock.patch("installer.gateway_participant.participants", return_value=self.chosen))

    def publish(self, request, participants=(), **kwargs):
        self.requests.append(json.loads(json.dumps(request)))
        self.participants.append(list(participants))
        code = self.codes.pop(0) if self.codes else 0
        if code:
            failure = install_transaction.InstallTransactionError("publish refused")
            if code != 1:
                failure.code, failure.exit_code, failure.outcome, failure.phase = "GATEWAY_PRECONDITION", code, "blocked", "stop"
            raise failure
        return "20261020T000000000000Z-1-aaaaaaaa"

    def expected_request(self, host, *targets, **extra):
        return {"schema_version": 1, "host": host, "ghost_root": str(self.ghost),
                "event_log": str(self.ghost / "install-state" / f"{host}-events.jsonl"),
                "installer": {"commit": "abc123def", "schema_version": installed_payload.SCHEMA_VERSION},
                "source": dict(self.origin, ledger_source_roots={}), "asset_root": str(self.source),
                **({"install_state": str(self.ghost / "install-state" / f"{host}.json")}
                   if any(t["kind"] == "support" for t in targets) else {}),
                "targets": list(targets), **extra}

    def expected_target(self, name, source, dest, mode, *, kind="skill", provider="core", commit="abc123def",
                        status=None):
        target = {"name": name, "kind": kind, "provider": provider, "source": str(source), "source_commit": commit,
                  "dest": str(dest), "mode": mode}
        if status:
            target["status"] = status
        return target

    def test_copy_target_publishes_one_copy_with_the_records_participants(self):
        # install.sh:75-93
        ctx = self.context("codex")
        dest = ctx.skills_dir / "alpha"
        with self.runner.patch():
            ok, _ = self.captured(transaction.copy_target, ctx, Target("alpha", self.source / "alpha"), dest)
        self.assertTrue(ok)
        self.assertEqual(self.requests, [self.expected_request(
            "codex", self.expected_target("alpha", self.source / "alpha", dest, "copy"))])
        self.assertEqual(self.participants, [self.chosen])
        # No child process publishes: the participants are objects of this process.
        self.assertEqual(self.runner.tool_calls("install_transaction.py"), [])
        self.codes.append(1)
        with self.runner.patch():
            ok, out = self.captured(transaction.copy_target, ctx, Target("alpha", self.source / "alpha"), dest)
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] Staged copy replace failed: {dest}", out)

    def test_a_repair_publish_assigns_no_installer_release_or_inventory(self):
        ctx = self.context()
        package = self.source.parent / "package"
        for kind, name in (("skill", "alpha"), ("support", "_shared")):
            with self.subTest(kind=kind):
                dest = ctx.skills_dir / name
                with self.runner.patch():
                    self.captured(transaction.copy_target, ctx, Target(name, package / name), dest, kind, repair_from=package)
                expected = self.expected_request("claude", self.expected_target(name, package / name, dest, "copy", kind=kind))
                expected.update(installer={}, source={"ledger_source_roots": {}}, asset_root=str(package))
                expected.pop("install_state", None)
                self.assertEqual(self.requests.pop(), expected)

    def test_a_refused_publish_prints_its_cause_and_keeps_its_exit_code(self):
        ctx = self.context("codex")
        dest = ctx.skills_dir / "alpha"
        self.codes.append(3)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = transaction.publish(ctx, [self.expected_target("alpha", self.source / "alpha", dest, "copy")])
        self.assertEqual((result.ok, result.returncode), (False, 3))
        self.assertEqual(json.loads(stderr.getvalue()), {"status": "blocked", "error_code": "GATEWAY_PRECONDITION",
                                                         "phase": "stop", "detail": "publish refused"})
        self.codes.append(1)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = transaction.publish(ctx, [self.expected_target("alpha", self.source / "alpha", dest, "copy")])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(stderr.getvalue().strip(), "publish refused")

    def test_copy_targets_batch_publishes_every_target_in_one_request(self):
        # install.sh:105-223, 1018-1039
        ctx = self.context("codex")
        batch = transaction.CopyBatch()
        with self.runner.patch():
            self.assertTrue(transaction.copy_targets_batch(ctx, batch))
        self.assertEqual(self.requests, [])
        addon = self.home / "addon" / "noop"
        batch.add(self.source / "_shared", ctx.skills_dir / "_shared", "_shared", "support", "updated")
        batch.add(self.source / "alpha", ctx.skills_dir / "alpha", "alpha", "skill", "new")
        batch.add(addon, ctx.skills_dir / "noop", "noop", "skill", "new", "addon:noop")
        with mock.patch("installer.source.commit_of", return_value="feed42") as commit_of, self.runner.patch():
            self.assertTrue(transaction.copy_targets_batch(ctx, batch, progress_label="  [2/5] Skill sync         "))
        commit_of.assert_called_once_with(addon)
        self.assertEqual(self.requests, [self.expected_request(
            "codex",
            self.expected_target("_shared", self.source / "_shared", ctx.skills_dir / "_shared", "copy",
                                 kind="support", status="updated"),
            self.expected_target("alpha", self.source / "alpha", ctx.skills_dir / "alpha", "copy", status="new"),
            self.expected_target("noop", addon, ctx.skills_dir / "noop", "copy", provider="addon:noop",
                                 commit="feed42", status="new"),
            progress={"label": "  [2/5] Skill sync         "})])
        event_file = self.home / "run.events.jsonl"
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_EVENT_FILE": str(event_file)}):
            quiet(self.home, report_child=True)
        self.requests.clear()
        with mock.patch("installer.source.commit_of", return_value="feed42"), self.runner.patch():
            self.assertTrue(transaction.copy_targets_batch(ctx, batch))
        self.assertEqual(self.requests[0]["progress"], {"event_file": str(event_file)})
        self.codes.append(1)
        with mock.patch("installer.source.commit_of", return_value="feed42"), self.runner.patch():
            ok, out = self.captured(transaction.copy_targets_batch, ctx, batch)
        self.assertFalse(ok)
        self.assertIn("[ERROR] Staged copy batch replace failed", out)

    def test_link_or_copy_falls_back_to_a_copy_only_on_exit_code_1(self):
        # install.sh:640-653
        ctx = self.context("claude")
        target, dest = Target("alpha", self.source / "alpha"), ctx.skills_dir / "alpha"
        with self.runner.patch():
            self.assertEqual(transaction.link_or_copy(ctx, target, dest), 0)
        self.assertEqual(self.requests, [self.expected_request(
            "claude", self.expected_target("alpha", self.source / "alpha", dest, "link"))])
        self.requests.clear()
        self.codes.append(1)
        with self.runner.patch():
            self.assertEqual(transaction.link_or_copy(ctx, target, dest), 0)
        self.assertEqual([r["targets"][0]["mode"] for r in self.requests], ["link", "copy"])
        for code in (2, 3, 4, 5):
            with self.subTest(code=code):
                self.requests.clear()
                self.codes.append(code)
                with self.runner.patch(), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(transaction.link_or_copy(ctx, target, dest), code)
                self.assertEqual([r["targets"][0]["mode"] for r in self.requests], ["link"])

    def test_install_shared(self):
        # targets.sh:248-261
        ctx = self.context("codex")
        with self.runner.patch():
            self.assertEqual(transaction.install_shared(ctx, ctx.skills_dir, True), 0)
        self.assertEqual(self.requests[0]["targets"], [self.expected_target(
            "_shared", self.source / "_shared", ctx.skills_dir / "_shared", "copy", kind="support")])
        link_ctx = self.context("claude")
        (link_ctx.skills_dir / "_shared").symlink_to(self.source / "_shared")
        self.requests.clear()
        rc, out = self.captured(transaction.install_shared, link_ctx, link_ctx.skills_dir, False)
        self.assertEqual(rc, 0)
        self.assertEqual(self.requests, [])
        self.assertIn("[WARN] _shared. Already installed (skipped)", out)
        (link_ctx.skills_dir / "_shared").unlink()
        (link_ctx.skills_dir / "_shared").symlink_to(self.home / "elsewhere")
        with self.runner.patch():
            self.assertEqual(transaction.install_shared(link_ctx, link_ctx.skills_dir, False), 0)
        self.assertEqual(self.requests[0]["targets"], [self.expected_target(
            "_shared", self.source / "_shared", link_ctx.skills_dir / "_shared", "link", kind="support")])

    def test_sync_runtime_shared_publishes_the_shared_dir_the_companion_and_the_commands_in_one_request(self):
        # install.sh:95-103
        ctx = self.context("claude")
        self.add_entry_files()
        companion = self.source / COMPANION
        companion.parent.mkdir(parents=True)
        companion.write_text("ledger", encoding="utf-8")
        runtime = self.ghost / "runtime" / "current"
        with self.runner.patch():
            ok, out = self.captured(transaction.sync_runtime_shared, ctx)
        self.assertTrue(ok)
        texts = {target["name"]: target.pop("command_text", None) for target in self.requests[0]["targets"]}
        self.assertEqual(self.requests, [self.expected_request("claude", *(
            self.expected_target("_shared", self.source / "_shared", runtime / "_shared", "copy", kind="runtime"),
            self.expected_target(COMPANION, companion, runtime / COMPANION, "copy", kind="runtime"),
            *(self.expected_target(name, self.source / entry, self.ghost / "bin" / name, "copy", kind="command")
              for name, entry in locations.MANAGEMENT_COMMANDS.items())))])
        # The command file of a shell names the package entry; cmd starts the PowerShell command beside it.
        for name in ("ghost-alice", "ghost-alice.ps1"):
            self.assertIn(str(self.source / locations.MANAGEMENT_COMMANDS[name]), texts[name])
            self.assertIn("--management-entry", texts[name])
        self.assertIsNone(texts["ghost-alice.cmd"])
        self.codes.append(2)
        with self.runner.patch(), contextlib.redirect_stderr(io.StringIO()):
            ok, out = self.captured(transaction.sync_runtime_shared, ctx)
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] Staged copy replace failed: {runtime / '_shared'}", out)

    def test_a_checkout_without_the_companion_and_the_entry_files_publishes_the_shared_dir_alone(self):
        ctx = self.context("claude")
        with self.runner.patch():
            self.assertTrue(transaction.sync_runtime_shared(ctx))
        self.assertEqual([t["name"] for t in self.requests[0]["targets"]], ["_shared"])

    def test_only_a_candidate_names_its_own_package_as_the_active_installer(self):
        ctx = self.context("codex")
        package = self.source.resolve()

        def placed():
            self.requests.clear()
            with self.runner.patch():
                transaction.sync_runtime_shared(ctx)
                transaction.copy_target(ctx, Target("alpha", package / "alpha"), ctx.skills_dir / "alpha")
                transaction.record_current_links(ctx, [(Target("alpha", package / "alpha"),
                                                        ctx.skills_dir / "alpha", "skill")])
            return [request["installer"] for request in self.requests]
        writer = {"commit": "abc123def", "schema_version": installed_payload.SCHEMA_VERSION}
        with self.candidate(package):
            self.assertEqual(placed(), [dict(writer, active_root=str(package))] * 3)
        self.assertEqual(placed(), [writer] * 3)

    def test_links_already_pointing_at_their_source_are_recorded_in_one_request(self):
        ctx = self.context("claude")
        with self.runner.patch():
            transaction.record_current_links(ctx, [])
        self.assertEqual(self.requests, [])
        links = [(Target("_shared", self.source / "_shared"), ctx.skills_dir / "_shared", "support"),
                 (Target("alpha", self.source / "alpha"), ctx.skills_dir / "alpha", "skill")]
        with self.runner.patch():
            transaction.record_current_links(ctx, links)
        self.assertEqual(self.requests, [self.expected_request(
            "claude",
            self.expected_target("_shared", self.source / "_shared", ctx.skills_dir / "_shared", "link",
                                 kind="support", status="current"),
            self.expected_target("alpha", self.source / "alpha", ctx.skills_dir / "alpha", "link",
                                 status="current"))])
        self.codes.append(1)
        with self.runner.patch():
            _, out = self.captured(transaction.record_current_links, ctx, links)
        self.assertIn("[WARN] The install record could not record the targets that were already installed", out)

    def test_one_command_keeps_its_original_source_between_placements(self):
        ctx = self.context("claude")
        with self.runner.patch():
            transaction.sync_runtime_shared(ctx)
            with mock.patch("installer.source.input_record", side_effect=AssertionError("source changed")):
                transaction.link_or_copy(ctx, Target("alpha", self.source / "alpha"), ctx.skills_dir / "alpha")
        self.assertEqual([request["source"] for request in self.requests],
                         [dict(self.origin, ledger_source_roots={})] * 2)
        self.assertEqual(ctx.record_source, self.origin)

    def test_placement_runs_no_child_tool(self):
        ctx = self.context("codex")
        with self.runner.patch():
            transaction.install_shared(ctx, ctx.skills_dir, True)
            transaction.sync_runtime_shared(ctx)
            transaction.link_or_copy(ctx, Target("alpha", self.source / "alpha"), ctx.skills_dir / "alpha")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(len(self.requests), 3)

    def test_detect_install_mode_and_removal(self):
        ctx = self.context("claude")
        link = ctx.skills_dir / "alpha"
        link.symlink_to(self.source / "alpha")
        copy = ctx.skills_dir / "copy"
        copy.mkdir()
        self.assertEqual(transaction.detect_install_mode(link, False), "symlink")
        self.assertEqual(transaction.detect_install_mode(copy, True), "copy")
        self.assertEqual(transaction.detect_install_mode(copy, False), "copy-fallback")
        self.assertEqual(transaction.detect_install_mode(self.home / "none", False), "missing")
        result, out = self.captured(transaction.remove_installed_target, ctx, "alpha", link,
                                    source_path=self.source / "alpha")
        self.assertEqual(result["action"], "removed")
        self.assertFalse(link.is_symlink())
        self.assertTrue((self.source / "alpha" / "SKILL.md").is_file())
        self.assertIn("[OK] alpha removed", out)
        self.assertEqual(transaction.remove_installed_target(ctx, "copy", copy)["action"], "manual-review")
        self.assertTrue(copy.exists())
        self.assertEqual(transaction.remove_installed_target(ctx, "none", ctx.skills_dir / "none")["action"], "missing")

    def test_gateway_guard_uses_exact_roots_and_empty_set_is_a_noop(self):
        ctx = self.context("claude")
        self.assertEqual(transaction.guard_gateway_removal(ctx, []), 0)
        with mock.patch("installer.gateway_participant.guard", return_value=0) as guard:
            self.assertEqual(transaction.guard_gateway_removal(ctx, [self.home / "a", self.home / "b"]), 0)
        guard.assert_called_once_with(self.ghost, [self.home / "a", self.home / "b"])
        self.assertEqual(self.runner.calls, [])


class InstalledLedgerLocationTests(TransactionCase):
    def setUp(self):
        super().setUp()
        self.enterContext(install_lock.command_lock(self.ghost / "install.lock", owner="installer:test"))
        self.ctx = self.context("codex")
        self.ctx.record_source = {"root": str(self.source), "commit": "candidate", "dirty": "clean"}
        self.old_source = (self.home.parent / "old-source").resolve()
        self.old_ledger = self.old_source / "session-intent"

    def seed_source(self, root=None, relationships=None):
        section = {"root": str(root or self.old_source), "commit": "previous"}
        if relationships is not None:
            section["ledger_source_roots"] = relationships
        installed_payload.update(self.ghost, {"source": section})

    def publish_alpha(self):
        return transaction.copy_target(self.ctx, Target("alpha", self.source / "alpha"),
                                       self.ctx.skills_dir / "alpha")

    def legacy_hook(self, platform='codex'):
        # One root-only legacy fixture exercises observation before config is normalized.
        script = Path(__file__).with_name("hook_profile_gate.py")
        payload = base64.urlsafe_b64encode(
            f'python3 ledger.py --root {shlex.quote(str(self.old_ledger))}'.encode()).decode()
        settings = self.home / f'.{platform}' / ('hooks.json' if platform == 'codex' else 'settings.json')
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{
            "type": "command", "command":
            f'python3 {shlex.quote(str(script))} run session-intent {platform} {payload}'}]}]}}))
        return settings

    def test_inactive_other_host_config_error_allows_independent_publish(self):
        self.seed_source()
        runtime_config.save_config({'install_locations': {'claude': 'not-an-object'}})
        before = (self.ghost / 'config.json').read_bytes()
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)['source']['root'], str(self.source))
        self.assertEqual((self.ghost / 'config.json').read_bytes(), before)

    def test_requested_host_config_error_refuses_publish_before_changes(self):
        self.seed_source()
        runtime_config.save_config({'install_locations': {'codex': 'not-an-object'}})
        before = installed_payload.path(self.ghost).read_bytes()
        with self.assertRaisesRegex(ValueError, 'codex'):
            self.publish_alpha()
        self.assertEqual(installed_payload.path(self.ghost).read_bytes(), before)
        self.assertFalse((self.ctx.skills_dir / 'alpha').exists())

    def test_inactive_host_error_preserves_unattributed_legacy_fact_without_blocking_source_change(self):
        fixed = (self.ghost / 'session-intent').resolve()
        for restored in (self.old_ledger, fixed):
            with self.subTest(restored=restored):
                self.seed_source(relationships={str(self.old_ledger): str(self.old_source)})
                runtime_config.save_config({'install_locations': {'claude': 'not-an-object'}})
                before = (self.ghost / 'config.json').read_bytes()
                self.assertTrue(self.publish_alpha())
                self.assertEqual((self.ghost / 'config.json').read_bytes(), before)
                uncertain = {str(self.old_ledger): str(self.old_source)}
                self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'], uncertain)
                runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(restored)}}})
                if restored == self.old_ledger:
                    observed = transaction.installed_ledger_locations()
                    self.assertEqual(observed.roots[self.old_ledger]['state'], 'unknown')
                    self.assertIsNone(observed.roots[self.old_ledger]['source_root'])
                self.assertTrue(self.publish_alpha())
                record = installed_payload.read(self.ghost)
                self.assertEqual(record['source']['root'], str(self.source))
                expected = uncertain if restored == self.old_ledger else {}
                self.assertEqual(record['source']['ledger_source_roots'], expected)

    def test_same_source_publish_keeps_unattributed_legacy_relationship_until_config_repair(self):
        self.seed_source(relationships={str(self.old_ledger): str(self.old_source)})
        self.ctx.record_source['root'] = str(self.old_source)
        runtime_config.save_config({'install_locations': {'claude': 'not-an-object'}})
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'],
                         {str(self.old_ledger): str(self.old_source)})

    def test_invalid_target_host_references_cannot_make_bad_config_look_inactive(self):
        self.seed_source()
        self.assertTrue(self.publish_alpha())
        runtime_config.save_config({'install_locations': {'claude': 'not-an-object'}})
        self.legacy_hook('claude').write_text('{"hooks": null}')
        record = installed_payload.read(self.ghost)
        existing = str(self.ctx.skills_dir / 'alpha')
        target = record['targets'][existing]
        destination = self.ctx.skills_dir / 'other'
        for hosts in (None, ['claude', 1]):
            with self.subTest(used_by=hosts):
                record['targets'][existing] = dict(target, used_by=hosts)
                installed_payload.path(self.ghost).write_text(json.dumps(record))
                before = installed_payload.path(self.ghost).read_bytes()
                observed = transaction.installed_ledger_locations()
                record_path = str(installed_payload.path(self.ghost))
                self.assertEqual(set(observed.observation_errors), {record_path})
                self.assertIn(existing, observed.observation_errors[record_path])
                self.assertEqual(set(observed.host_config_errors), {'claude'})
                retained = transaction.retain_record_source_writers(self.ghost, observed)
                self.assertEqual(set(retained.errors), {record_path})
                self.assertEqual(set(retained.blocked_hosts['claude']),
                                 {record_path, str(self.ghost / 'config.json')})
                self.assertIn(existing, retained.blocked_hosts['claude'][record_path])
                with self.assertRaises(ValueError) as caught:
                    transaction.copy_target(self.ctx, Target('alpha', self.source / 'alpha'), destination)
                self.assertIn(existing, str(caught.exception))
                self.assertIn(record_path, str(caught.exception))
                self.assertEqual(installed_payload.path(self.ghost).read_bytes(), before)
                self.assertFalse(destination.exists())

    def test_observed_inactive_host_relationship_survives_config_repair_after_source_change(self):
        self.seed_source()
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(self.old_ledger)}}})
        self.ctx.record_source['root'] = str(self.old_source)
        self.assertTrue(self.publish_alpha())
        runtime_config.save_config({'install_locations': {'claude': 'not-an-object'}})
        self.ctx.record_source['root'] = str(self.source)
        self.assertTrue(self.publish_alpha())
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(self.old_ledger)}}})
        observed = transaction.installed_ledger_locations()
        self.assertEqual(observed.roots[self.old_ledger]['state'], 'source-bound')
        self.assertEqual(observed.roots[self.old_ledger]['source_root'], self.old_source)

    def test_legacy_unattributed_source_does_not_bind_a_later_host(self):
        self.seed_source(root=self.source,
                         relationships={str(self.old_ledger): str(self.old_source)})
        runtime_config.save_config({'install_locations': {'codex': {'ledger_root': str(self.old_ledger)}}})
        observed = transaction.installed_ledger_locations()
        self.assertEqual(observed.roots[self.old_ledger]['state'], 'unknown')
        self.assertIsNone(observed.roots[self.old_ledger]['source_root'])
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'],
                         {str(self.old_ledger): str(self.old_source)})

    def test_hidden_relationship_cannot_supply_the_source_of_a_different_host_binding(self):
        self.seed_source()
        fixed = (self.ghost / 'session-intent').resolve()
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(self.old_ledger)}}})
        self.ctx.record_source['root'] = str(self.old_source)
        self.assertTrue(self.publish_alpha())
        hooks = self.legacy_hook('claude')
        hooks.write_text('{"hooks": null}')
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(fixed)}}})
        self.ctx.record_source['root'] = str(self.source)
        self.assertTrue(self.publish_alpha())
        hooks.write_text('{"hooks": {}}')
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(fixed)},
                                                        'codex': {'ledger_root': str(self.old_ledger)}}})
        observed = transaction.installed_ledger_locations()
        self.assertEqual(observed.roots[self.old_ledger]['state'], 'unknown')
        self.assertIsNone(observed.roots[self.old_ledger]['source_root'])
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'], {})

    def test_changed_binding_discards_its_saved_source_before_the_host_becomes_unreadable(self):
        self.seed_source()
        fixed = (self.ghost / 'session-intent').resolve()
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(self.old_ledger)}}})
        self.assertTrue(self.publish_alpha())
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(fixed)}}})
        self.assertTrue(self.publish_alpha())
        runtime_config.save_config({'install_locations': {'claude': 'not-an-object'}})
        self.assertTrue(self.publish_alpha())
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(self.old_ledger)}}})
        self.assertEqual(transaction.installed_ledger_locations().roots[self.old_ledger]['state'], 'unknown')

    def test_observation_accepts_supported_legacy_and_current_target_records(self):
        self.seed_source()
        self.assertTrue(self.publish_alpha())
        record = installed_payload.read(self.ghost)
        for version in (1, installed_payload.SCHEMA_VERSION):
            with self.subTest(schema_version=version):
                record['schema_version'] = version
                installed_payload.path(self.ghost).write_text(json.dumps(record))
                self.assertEqual(transaction.installed_ledger_locations().installed_hosts, ('codex',))

    def assert_other_host_source_survives_config_repair(self, hook_state):
        self.seed_source()
        if hook_state == 'absent-with-target':
            self.ctx.platform = 'claude'
            self.ctx.record_source['root'] = str(self.old_source)
            self.assertTrue(self.publish_alpha())
            self.ctx.platform = 'codex'
            self.ctx.record_source['root'] = str(self.source)
        else:
            hooks = self.legacy_hook('claude')
            if hook_state == 'unreadable':
                hooks.write_text('{"hooks": null}')
        runtime_config.save_config({'install_locations': {'claude': 'not-an-object'}})
        before = installed_payload.path(self.ghost).read_bytes()
        with self.assertRaisesRegex(ValueError, 'claude'):
            self.publish_alpha()
        self.assertEqual(installed_payload.path(self.ghost).read_bytes(), before)
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(self.old_ledger)}}})
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'],
                         {str(self.old_ledger): {'source_root': str(self.old_source), 'hosts': ['claude']}})

    def test_other_host_config_error_cannot_erase_source_for_installed_targets(self):
        self.assert_other_host_source_survives_config_repair('absent-with-target')

    def test_other_host_config_error_cannot_erase_source_for_nonfixed_hooks(self):
        self.assert_other_host_source_survives_config_repair('nonfixed')

    def test_other_host_config_error_cannot_erase_source_for_unreadable_hooks(self):
        self.assert_other_host_source_survives_config_repair('unreadable')

    def test_same_source_publish_preserves_relationships_hidden_by_host_config_error(self):
        expected = {str(self.old_ledger): {'source_root': str(self.old_source), 'hosts': ['claude']}}
        self.seed_source(root=self.source, relationships=expected)
        runtime_config.save_config({'install_locations': {'claude': 'not-an-object'}})
        self.legacy_hook('claude').write_text('{"hooks": null}')
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'], expected)

    def test_legacy_relationship_is_saved_on_first_publish_and_retained_until_config_normalization(self):
        self.seed_source()
        hooks = self.legacy_hook()
        original_hooks = hooks.read_bytes()
        expected = {str(self.old_ledger): {'source_root': str(self.old_source), 'hosts': ['codex']}}
        for _ in range(2):
            self.assertTrue(self.publish_alpha())
            saved = installed_payload.read(self.ghost)["source"]
            self.assertEqual(saved["root"], str(self.source))
            self.assertEqual(saved["ledger_source_roots"], expected)
            self.assertEqual(runtime_config.load_config().get("install_locations", {}), {})
            self.assertEqual(hooks.read_bytes(), original_hooks)
        runtime_config.save_config({"install_locations": {"codex": {"ledger_root": str(self.old_ledger)}}})
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)["source"]["ledger_source_roots"], expected)
        self.assertNotIn("ledger_source_roots", self.ctx.record_source)

    def test_later_publish_failure_and_retry_keep_the_first_publish_relationship(self):
        self.seed_source()
        self.legacy_hook()
        self.assertTrue(self.publish_alpha())
        original = installed_payload.read(self.ghost)["source"]
        with mock.patch.object(installed_payload, "update", side_effect=OSError("record disk full")), \
                contextlib.redirect_stderr(io.StringIO()):
            result, _ = self.captured(self.publish_alpha)
        self.assertFalse(result)
        self.assertEqual(installed_payload.read(self.ghost)["source"], original)
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)["source"], original)

    def test_observation_groups_hosts_by_normalized_root_and_retains_missing_original_source(self):
        second = self.old_source / "second-ledger"
        self.seed_source(root=self.source, relationships={
            str(self.old_ledger): {'source_root': str(self.old_source), 'hosts': ['claude', 'codex']},
            str(second): {'source_root': str(self.old_source), 'hosts': ['future-host']}})
        bindings = {"claude": {"ledger_root": str(self.old_ledger)},
                    "codex": {"ledger_root": str(self.old_ledger / ".." / self.old_ledger.name)},
                    "future-host": {"ledger_root": str(second)}}
        runtime_config.save_config({"install_locations": bindings})
        before = (self.ghost / "config.json").read_bytes()
        observed = transaction.installed_ledger_locations().roots
        self.assertEqual(set(observed), {self.old_ledger, second})
        self.assertEqual(observed[self.old_ledger], {"source_root": self.old_source, "state": "source-bound",
                         "hosts": {host: bindings[host] for host in ("claude", "codex")}})
        self.assertEqual(observed[second], {"source_root": self.old_source, "state": "source-bound",
                         "hosts": {"future-host": bindings["future-host"]}})
        self.assertFalse(self.old_source.exists())
        self.assertEqual((self.ghost / "config.json").read_bytes(), before)

    def test_publish_prunes_fixed_retired_and_unbound_relationships_and_then_clears_empty_map(self):
        fixed = (self.ghost / "session-intent").resolve()
        retired, unbound = self.old_source / "retired", self.old_source / "unbound"
        self.seed_source(relationships={str(root): str(self.old_source)
                                       for root in (self.old_ledger, fixed, retired, unbound)})
        runtime_config.save_config({"install_locations": {
            "claude": {"ledger_root": str(self.old_ledger)}, "codex": {"ledger_root": str(fixed)},
            "future-host": {"ledger_root": str(retired)}}, "retired_ledger_roots": [str(retired)]})
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)["source"]["ledger_source_roots"],
                         {str(self.old_ledger): {'source_root': str(self.old_source), 'hosts': ['claude']}})
        runtime_config.save_config({"install_locations": {"claude": {"ledger_root": str(fixed)}}})
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)["source"]["ledger_source_roots"], {})

    def test_transported_relationships_cannot_supply_installed_facts(self):
        self.seed_source()
        transported = {str(self.old_ledger): str(self.old_source)}
        self.ctx.record_source["ledger_source_roots"] = transported
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)["source"]["ledger_source_roots"], {})
        self.assertEqual(self.ctx.record_source["ledger_source_roots"], transported)

    def test_unrelated_registered_location_has_unknown_source_and_development_override_is_not_installed(self):
        self.seed_source(root=self.source)
        runtime_config.save_config({"install_locations": {"codex": {"ledger_root": str(self.old_ledger)}}})
        development = self.source / "independent-development-ledger"
        development.mkdir()
        with mock.patch.dict(os.environ, {"GHOST_ALICE_SESSION_INTENT_ROOT": str(development)}):
            observed = transaction.installed_ledger_locations().roots
        self.assertEqual(observed, {self.old_ledger: {"source_root": None, "state": "unknown",
                         "hosts": {"codex": {"ledger_root": str(self.old_ledger)}}}})

    def test_unreadable_other_host_preserves_observed_and_previously_known_relationships(self):
        remembered = self.old_source / 'unreadable-host-ledger'
        remembered_relation = {'source_root': str(self.old_source), 'hosts': ['codex']}
        expected = {str(self.old_ledger): {'source_root': str(self.old_source), 'hosts': ['claude']},
                    str(remembered): remembered_relation}
        self.seed_source(relationships={str(remembered): remembered_relation})
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(self.old_ledger)}}})
        hooks = self.home / '.codex/hooks.json'
        hooks.parent.mkdir(parents=True, exist_ok=True)
        hooks.write_text('{"hooks": null}')
        observed = transaction.installed_ledger_locations()
        self.assertEqual(set(observed.roots), {self.old_ledger})
        self.assertEqual(set(observed.unavailable_hosts), {'codex'})
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'], expected)
        hooks.write_text('{"hooks": {}}')
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'],
                         {str(self.old_ledger): {'source_root': str(self.old_source), 'hosts': ['claude']}})

    def test_unreadable_host_does_not_invent_a_source_relationship(self):
        self.seed_source()
        hooks = self.legacy_hook()
        hooks.write_text('{"hooks": null}')
        self.assertTrue(self.publish_alpha())
        self.assertEqual(installed_payload.read(self.ghost)['source']['ledger_source_roots'], {})

    def test_observation_uses_the_locked_installation_config_under_an_environment_override(self):
        self.seed_source()
        runtime_config.save_config({'install_locations': {'claude': {'ledger_root': str(self.old_ledger)}}})
        alternate = self.home / 'development-state'
        alternate.mkdir()
        (alternate / 'config.json').write_text(json.dumps({'install_locations': {
            'codex': {'ledger_root': str(alternate / 'records')}}}))
        with mock.patch.dict(os.environ, {'GHOST_ALICE_GHOST_ROOT': str(alternate)}):
            observed = transaction.installed_ledger_locations()
            self.assertEqual(set(observed.roots), {self.old_ledger})
            self.assertEqual(set(observed.roots[self.old_ledger]['hosts']), {'claude'})


def unrecorded_launchd(effects, platform="darwin"):
    """Answer launchd questions with `effects` (a fake launchd) on `platform`."""
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(sys, "platform", platform))
    stack.enter_context(mock.patch("context_gateway_install._effects", return_value=effects))
    return stack


UNRECORDED_LINE = ("Context gateway: a loaded service runs from the runtime without an install record entry; register "
                   "it with bash install.sh gateway --codex-dir <dir> --ledger-root <dir> --state-dir <dir> "
                   "--launch-agent <plist>, which restarts it")


class GatewayReportTests(TransactionCase):
    """After the runtime publish, the install says whether a context gateway is registered and running. Read-only."""

    def register(self):
        reg = gateway_testing.registered_home(self, self.home)
        return reg

    def test_no_service_section_and_no_loaded_service_prints_nothing(self):
        absent = gateway_testing.FakeEffects(self.home, running=False)
        with self.runner.patch(), unrecorded_launchd(absent):
            observed, out = self.captured(transaction.report_gateway, self.context("claude"))
        self.assertEqual(out, "")
        self.assertIsNone(observed)
        self.assertEqual(self.runner.calls, [])
        # A receipt and a binding alone are not a registration the installer reports.
        (self.ghost / "context-gateway").mkdir(parents=True)
        (self.ghost / "context-gateway" / "installation.json").write_text("{}", encoding="utf-8")
        (self.ghost / "context-gateway-binding.json").write_text("{}", encoding="utf-8")
        with self.runner.patch(), unrecorded_launchd(absent):
            observed, out = self.captured(transaction.report_gateway, self.context("claude"))
        self.assertEqual((observed, out), (None, ""))

    def test_a_registered_running_gateway_is_one_info_line(self):
        reg = self.register()
        seen = gateway_participant.LoadedService("loaded", program=reg.program, pid=4242)
        with self.runner.patch(), mock.patch("installer.gateway_participant.loaded_service", return_value=seen):
            observed, out = self.captured(transaction.report_gateway, self.context("codex"))
        line = f"Context gateway: registered, runtime {reg.runtime_root}, service running"
        self.assertIn(f"[INFO] {line}", out)
        self.assertEqual(observed, (line, True))

    def test_absent_and_unavailable_service_observations_have_distinct_warnings(self):
        reg = self.register()
        for state in ('absent', 'inconclusive', 'unreadable', 'loaded'):
            seen = gateway_participant.LoadedService(state, detail='query unavailable')
            with self.subTest(state=state), self.runner.patch(), \
                    mock.patch("installer.gateway_participant.loaded_service", return_value=seen):
                observed, out = self.captured(transaction.report_gateway, self.context("codex"))
            suffix = ('service not running' if state == 'absent' else
                      'service loaded; running process unconfirmed' if state == 'loaded' else
                      'service state unknown (query unavailable)')
            line = f"Context gateway: registered, runtime {reg.runtime_root}, {suffix}"
            self.assertIn(f"[WARN] {line}", out)
            self.assertEqual(observed, (line, False))

    def test_an_unreadable_record_is_a_warning_and_the_install_goes_on(self):
        record = installed_payload.path(self.ghost)
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("{broken", encoding="utf-8")
        with self.runner.patch():
            observed, out = self.captured(transaction.report_gateway, self.context("claude"))
        self.assertIn(f"[WARN] Context gateway: the install record cannot be read: {record}", out)
        self.assertEqual(observed[1], False)


class UnrecordedServiceTests(TransactionCase):
    """A gateway service that launchd has loaded but the install record does not name (a migration that could not
    finish, a hand-registered service). The installer has no record of its files, so it neither stops nor starts
    it and does not hold the publish; after the runtime publish one warning names the registration command, which
    records the service and restarts it on the new code."""

    def setUp(self):
        super().setUp()
        self.reg = gateway_testing.registered_home(self, self.home, service=False)
        self.agent = self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def loaded(self, program, *, agent_known=True, raw=None):
        self.agent.parent.mkdir(parents=True, exist_ok=True)
        self.agent.write_bytes(raw if raw is not None else plistlib.dumps({"Label": LABEL, "ProgramArguments": [
            sys.executable, "-B", str(program), "--config", str(self.reg.state_path)]}))
        return gateway_testing.FakeEffects(self.home, running=True, agent=self.agent if agent_known else None)

    def test_the_runtime_publish_goes_on_and_one_warning_names_the_registration(self):
        effects = self.loaded(self.reg.program)
        ctx = self.context("codex")
        with unrecorded_launchd(effects):
            published, _ = self.captured(transaction.sync_runtime_shared, ctx)
            observed, out = self.captured(transaction.report_gateway, ctx)
        self.assertTrue(published)
        self.assertEqual((self.reg.shared / "install_hooks.py").read_text(encoding="utf-8"), "")
        self.assertIn(f"[WARN] {UNRECORDED_LINE}\n", out)
        self.assertEqual(observed, (UNRECORDED_LINE, True))
        self.assertEqual(effects.service_calls(), [], "the installer never stops or starts a service it has no record of")
        self.assertIsNone(self.reg.service())

    def test_the_line_names_the_install_command_the_shell_reported(self):
        effects = self.loaded(self.reg.program)
        with unrecorded_launchd(effects), mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_COMMAND": ".\\install.cmd"}):
            observed, _ = self.captured(transaction.report_gateway, self.context("codex"))
        self.assertIsNotNone(observed)
        self.assertIn("register it with .\\install.cmd gateway --codex-dir", observed[0])

    def test_a_loaded_service_that_runs_elsewhere_is_not_reported(self):
        effects = self.loaded(self.home / "elsewhere" / "_shared" / "context_gateway.py")
        with unrecorded_launchd(effects):
            observed, out = self.captured(transaction.report_gateway, self.context("codex"))
        self.assertEqual((observed, out), (None, ""))

    def test_an_inconclusive_observation_counts_as_none_and_stays_in_the_details(self):
        for name, effects in (("unknown definition", self.loaded(self.reg.program, agent_known=False)),
                              ("unreadable plist", self.loaded(self.reg.program, raw=b"not a plist"))):
            with self.subTest(case=name), unrecorded_launchd(effects):
                observed, out = self.captured(transaction.report_gateway, self.context("codex"))
                self.assertIsNone(observed)
                self.assertNotIn("[WARN]", out)
                self.assertIn("[INFO] Context gateway: launchd observation was inconclusive", out)
                self.assertEqual(effects.service_calls(), [])

    def test_launchd_is_asked_only_on_macos(self):
        effects = self.loaded(self.reg.program)
        with mock.patch.object(effects, "observe", side_effect=AssertionError("launchd asked")) as observe, \
                unrecorded_launchd(effects, platform="linux"):
            observed, out = self.captured(transaction.report_gateway, self.context("codex"))
        observe.assert_not_called()
        self.assertEqual((observed, out), (None, ""))


class ActivationTests(TransactionCase):
    """`activate` makes a saved package the active installer before anything is placed."""

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.dict(os.environ, {"USERPROFILE": str(self.home)}))
        self.add_entry_files()
        (self.source / "hooks").mkdir()
        shutil.copy2(CHECKOUT / "hooks" / "post-merge", self.source / "hooks" / "post-merge")
        self.enterContext(install_lock.command_lock(self.ghost / "install.lock", owner="installer:test"))
        self.ctx = self.context("codex")
        self.earlier = str(self.home / "earlier" / "core")
        self.ledger = str(self.source.resolve() / "session-intent")

    CHOICES = {"codex": ["alpha"]}

    def prepared(self) -> Path:
        return source.prepare_source(self.ctx, provenance=self.request_source(), selections=self.CHOICES)[0] / "core"

    def entry(self, dest: Path) -> dict:
        return {"name": dest.name, "kind": "skill", "mode": "copy", "provider": "core", "source": "/earlier/alpha",
                "source_commit": "earlier", "used_by": ["codex"], "files": {}, "baseline": "recorded",
                "installed_at": "2026-10-20T00:00:00+00:00"}

    def seed(self, relations=None) -> dict:
        """The record that an earlier package's installation left."""
        dest = self.ctx.skills_dir / "alpha"
        installed_payload.update(self.ghost, {
            "installer": {"active_root": self.earlier, "commit": "earlier", "schema_version": 2},
            "source": {"root": str(self.source), "commit": "earlier", "ledger_source_roots": relations or {}},
            "targets": {str(dest): self.entry(dest)}})
        return installed_payload.read(self.ghost)

    def files(self) -> dict:
        """The files and links under the home, without the installer's log, and the source's Git configuration."""
        logs = self.ghost / "install"
        paths = [path for path in self.home.rglob("*")
                 if (path.is_symlink() or path.is_file()) and logs not in path.parents]
        config = self.source / ".git" / "config"
        return {str(path): os.readlink(path) if path.is_symlink() else path.read_bytes()
                for path in [*paths, *([config] if config.exists() else [])]}

    def activate(self, package: Path, choices=None):
        with self.candidate(package):
            self.captured(transaction.activate, self.ctx, choices or self.CHOICES)

    def placed(self, package: Path, *hosts: str) -> dict:
        """The record after `package` placed the runtime and the commands for `hosts`."""
        with self.candidate(package):
            for host in hosts:
                self.assertTrue(self.captured(transaction.sync_runtime_shared, self.context(host))[0])
            install_transaction.cleanup_completed(self.ghost)
        return installed_payload.read(self.ghost)

    def later(self) -> Path:
        """The saved package of a later release."""
        with open(self.source / "_shared" / "encoding_guard.py", "a", encoding="utf-8") as file:
            file.write("later\n")
        return self.prepared()

    def active(self):
        return (install_transaction.record_after_recovery(self.ghost) or {}).get("installer", {}).get("active_root")

    def test_activation_does_not_close_migration_over_an_unreadable_peer_inventory(self):
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        package = self.prepared()
        installed_payload.update(self.ghost, {'targets': {}})
        state = self.ghost / 'install-state' / 'claude.json'
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_bytes(b'{broken')
        before = installed_payload.read(self.ghost)
        before_hook = (self.source / '.git' / 'config').read_bytes()
        with self.assertRaisesRegex(ValueError, 'claude.json'):
            self.activate(package)
        self.assertEqual(state.read_bytes(), b'{broken')
        self.assertEqual(installed_payload.read(self.ghost), before)
        self.assertEqual((self.source / '.git' / 'config').read_bytes(), before_hook)
        state.write_text(json.dumps(dict(platform='claude', targets=[{
            'dest_path': str(self.home / 'claude' / 'old'), 'target_name': 'old', 'install_mode': 'copy'}])))
        self.activate(package)
        entry = installed_payload.read(self.ghost)['targets'][(self.home / 'claude' / 'old').as_posix()]
        self.assertEqual(entry['used_by'], ['claude'])

    @contextlib.contextmanager
    def stopped_after(self, call: str, name: str, number: int = 1):
        """End the step at the `call` (`os.replace` or `Path.rename`) that puts a file called `name` in place for the
        time `number`."""
        owner, attribute = (os, "replace") if call == "os.replace" else (Path, "rename")
        real, seen = getattr(owner, attribute), []

        def stopping(path, dest, *args, **kwargs):
            result = real(path, dest, *args, **kwargs)
            if Path(dest).name == name:
                seen.append(dest)
                if len(seen) == number:
                    raise KeyboardInterrupt(f"stopped after {call} of {name}")
            return result
        with mock.patch.object(owner, attribute, stopping), self.assertRaisesRegex(KeyboardInterrupt, "stopped after"):
            yield

    def test_activation_replaces_one_record_file_and_changes_only_the_active_package(self):
        package = self.prepared()
        relation = {self.ledger: {"source_root": str(self.source), "hosts": ["codex"]}}
        for schema, relations in ((2, {self.ledger: str(self.source)}), (3, relation)):
            with self.subTest(schema=schema):
                record = self.seed(relations)
                self.assertEqual(record["schema_version"], schema)
                before = self.files()
                replaced = []
                real = os.replace

                def replace(path, dest, *args, **kwargs):
                    replaced.append(Path(dest))
                    return real(path, dest, *args, **kwargs)
                with mock.patch("os.replace", replace):
                    self.activate(package)
                detail = installed_payload.read_path(self.ghost)
                self.assertEqual(installed_payload.read(self.ghost),
                                 dict(record, installer=dict(record["installer"], active_root=str(package))))
                self.assertEqual([path for path in replaced if path.name in installed_payload.RECORD_FILENAMES], [detail])
                after = self.files()
                self.assertEqual({path for path in {*before, *after} if before.get(path) != after.get(path)}, {str(detail)})

    def test_a_first_activation_leaves_the_record_and_the_commands_to_the_placement(self):
        package = self.prepared()
        before = self.files()
        self.activate(package)
        self.assertEqual(self.files(), before)
        self.assertIsNone(installed_payload.read(self.ghost))
        record = self.placed(package, "codex")
        self.assertEqual(record["installer"]["active_root"], str(package))
        commands = {name: self.ghost / "bin" / name for name in locations.MANAGEMENT_COMMANDS}
        self.assertEqual({path for path, entry in record["targets"].items() if entry["kind"] == "command"},
                         {str(path) for path in commands.values()})
        for name, path in commands.items():
            if name != "ghost-alice.cmd":
                self.assertIn(str(package / locations.MANAGEMENT_COMMANDS[name]), path.read_text(encoding="utf-8"))

    def test_the_activation_replaces_the_commands_and_keeps_their_users(self):
        command = self.ghost / "bin" / "ghost-alice"
        broken = {host: {"install_locations": {host: "not-an-object"}} for host in ("claude", "codex")}
        for users, config, host, replaced in (
                (("codex",), {}, "codex", True), (("claude", "codex"), {}, "codex", True),
                # The requested host does not use the commands yet.
                (("claude",), {}, "codex", True),
                (("claude", "codex"), broken["claude"], "codex", True),
                # A host whose configuration is invalid does not stop the installation of another host.
                (("claude",), broken["claude"], "codex", False)):
            with self.subTest(users=users, config=config):
                earlier = self.later()
                self.placed(earlier, *users)
                # A host that uses a skill and the command of an add-on only.
                skill, addon = self.home / ".agents" / "skills" / "alpha", self.home / ".agents" / "commands" / "noop.md"
                installed_payload.update(self.ghost, {"targets": {
                    str(skill): dict(self.entry(skill), used_by=["agents"]),
                    str(addon): dict(self.entry(addon), kind="command", provider="addon:noop", used_by=["agents"])}})
                record = installed_payload.read(self.ghost)
                package = self.later()
                (self.ghost / "config.json").write_text(json.dumps(config), encoding="utf-8")
                self.ctx = self.context(host)
                self.activate(package, dict.fromkeys(("claude", "codex"), ["alpha"]))
                (self.ghost / "config.json").write_text("{}", encoding="utf-8")
                self.assertEqual(self.active(), str(package))
                self.assertEqual(install_transaction.unfinished(self.ghost), [])
                self.assertIn(str(package if replaced else earlier), command.read_text(encoding="utf-8"))
                targets = installed_payload.read(self.ghost)["targets"]
                self.assertEqual({path: entry["used_by"] for path, entry in targets.items()},
                                 {path: entry["used_by"] for path, entry in record["targets"].items()})
                self.assertEqual({path for path, entry in targets.items() if entry != record["targets"][path]},
                                 {path for path, entry in targets.items()
                                  if replaced and (entry["kind"], entry["provider"]) == ("command", "core")})
                for path in (*installed_payload.RECORD_FILENAMES, "bin", "runtime"):
                    shutil.rmtree(self.ghost / path) if (self.ghost / path).is_dir() else (self.ghost / path).unlink(missing_ok=True)

    def test_the_activation_replaces_each_recorded_command_for_its_own_users_and_adds_none(self):
        self.placed(self.prepared(), "claude", "codex")
        commands = {name: self.ghost / "bin" / name for name in locations.MANAGEMENT_COMMANDS}
        record = installed_payload.read(self.ghost)
        record["targets"][str(commands["ghost-alice.ps1"])]["used_by"] = ["codex"]
        del record["targets"][str(commands["ghost-alice.cmd"])]
        installed_payload.read_path(self.ghost).write_text(json.dumps(record), encoding="utf-8")
        package = self.later()
        self.activate(package)
        self.assertEqual({path: entry["used_by"] for path, entry in installed_payload.read(self.ghost)["targets"].items()},
                         {path: entry["used_by"] for path, entry in record["targets"].items()})
        for name in ("ghost-alice", "ghost-alice.ps1"):
            self.assertIn(str(package / locations.MANAGEMENT_COMMANDS[name]), commands[name].read_text(encoding="utf-8"))

    def test_a_refused_publish_of_the_commands_stops_after_the_activation(self):
        earlier = self.prepared()
        self.placed(earlier, "codex")
        command = self.ghost / "bin" / "ghost-alice"
        before = command.read_bytes()
        package = self.later()
        refused = install_transaction.InstallTransactionError("the fixture refuses the publish")
        with mock.patch.object(install_transaction, "publish", side_effect=refused), \
                contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(ValueError) as stop:
            self.activate(package)
        self.assertIn(str(self.ghost / "bin"), str(stop.exception))
        self.assertIn("the fixture refuses the publish", errors.getvalue())
        self.assertEqual((self.active(), command.read_bytes()), (str(package), before))
        self.activate(package)
        self.assertIn(str(package), command.read_text(encoding="utf-8"))

    def test_every_stop_condition_comes_before_the_first_change(self):
        package = self.prepared()
        record = self.seed()
        for path in (self.ghost / "bin" / "ghost-alice", self.ghost / "install-state" / "codex.json",
                     self.ctx.skills_dir / "alpha" / "SKILL.md"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("installed\n", encoding="utf-8")
        saved, config = package / "_shared" / "encoding_guard.py", self.ghost / "config.json"
        detail, pending = installed_payload.read_path(self.ghost), self.ghost / installed_payload.PENDING_SOURCE_FILENAME
        hostless = dict(record, targets={path: dict(entry, used_by="codex") for path, entry in record["targets"].items()})
        moved = dict(record, source=dict(record["source"], root=str(self.home / "another source")))

        def put(path, data):
            if data is None:
                path.unlink()
            else:
                path.write_bytes(data)
        for label, path, content in (
                ("a changed package file", saved, b"changed\n"), ("a missing package file", saved, None),
                ("an unreadable configuration", config, b"{"), ("an unreadable pending source", pending, b"{"),
                ("a target without a host list", detail, json.dumps(hostless).encode()),
                ("an unreadable record", detail, b"{")):
            with self.subTest(stop=label):
                original = path.read_bytes() if path.exists() else None
                put(path, content)
                before = self.files()
                try:
                    with self.assertRaises(ValueError) as stop:
                        self.activate(package, {"claude": ["beta"]})
                    self.assertEqual(self.files(), before)
                finally:
                    put(path, original)
                self.assertIn(path.name, str(stop.exception))
                self.assertEqual(self.active(), self.earlier)
        config.write_text('{"install_locations": {"codex": "not-an-object"}}', encoding="utf-8")
        for label, installed, host in (
                ("the invalid configuration of every requested host", record, "codex"),
                # The invalid configuration of a host in use may hide a relationship with the installed source.
                ("the invalid configuration of another host", moved, "claude")):
            with self.subTest(stop=label):
                detail.write_text(json.dumps(installed), encoding="utf-8")
                self.ctx = self.context(host)
                before = self.files()
                with self.assertRaisesRegex(ValueError, "install_locations.codex"):
                    self.activate(package, {host: ["beta"]})
                self.assertEqual(self.files(), before)
        config.write_text("{}", encoding="utf-8")
        self.activate(package)
        self.assertEqual(self.active(), str(package))

    def test_the_package_keeps_the_choices_and_the_source_of_the_request_until_it_is_finished(self):
        package = self.prepared()
        version = package.parent / "version.json"
        saved = json.loads(version.read_text(encoding="utf-8"))
        choices = {"claude": ["alpha", "beta"], "codex": ["alpha"]}
        for label, before in (("an unfinished version", dict(saved, selections={"codex": ["beta"]})),
                              ("a finished version", dict(saved, status="successful", platforms=["codex"])),
                              ("a version of another source", dict(saved, source=dict(saved["source"], root="/earlier")))):
            with self.subTest(version=label):
                self.seed()
                version.write_text(json.dumps(before), encoding="utf-8")
                self.activate(package, choices)
                self.assertEqual(json.loads(version.read_text(encoding="utf-8")), dict(
                    before, status="candidate", selections=choices, source=self.request_source()))
                with self.candidate(package):
                    self.assertEqual(source.reinstall_selection(package), choices)

    def test_the_package_keeps_the_choices_before_the_record_names_it(self):
        package = self.prepared()
        self.seed()
        choices = {"claude": ["alpha", "beta"]}
        with self.stopped_after("os.replace", installed_payload.FILENAME):
            self.activate(package, choices)
        self.assertEqual(self.active(), str(package))
        self.assertEqual(json.loads((package.parent / "version.json").read_text(encoding="utf-8"))["selections"], choices)

    @unittest.skipIf(shutil.which("git") is None, "git is required")
    def test_a_git_setting_that_cannot_be_written_stops_before_the_first_change(self):
        subprocess.run(["git", "init", "-q", str(self.source)], check=True, capture_output=True)
        package = self.prepared()
        (self.ghost / "install-state").mkdir()
        (self.ghost / "install-state" / "codex.json").write_text(json.dumps({
            "platform": "codex", "source_root": str(self.source), "targets": []}), encoding="utf-8")
        record = {"schema_version": 1, "source": {"root": str(self.source)},
                  "targets": {"/earlier": self.entry(Path("/earlier"))}}
        (self.ghost / installed_payload.LEGACY_FILENAME).write_text(json.dumps(record), encoding="utf-8")
        before = self.files()
        config_lock = self.source / ".git" / "config.lock"
        config_lock.write_bytes(b"")
        with self.assertRaises(ValueError):
            self.activate(package, {"claude": ["beta"]})
        self.assertEqual(self.files(), before)
        config_lock.unlink()
        self.activate(package)
        self.assertEqual(self.active(), str(package))

    def test_earlier_records_are_migrated_before_the_record_names_the_package(self):
        package = self.prepared()
        dest = self.ctx.skills_dir / "alpha"
        dest.mkdir()
        (dest / "SKILL.md").write_text("installed\n", encoding="utf-8")
        inventory = self.ghost / "install-state" / "codex.json"
        inventory.parent.mkdir()
        inventory.write_text(json.dumps({"platform": "codex", "source_root": str(self.source), "targets": [
            {"target_name": "alpha", "source_path": str(self.source / "alpha"), "dest_path": str(dest),
             "install_mode": "copy"}]}), encoding="utf-8")
        other = self.home / "earlier-target"
        for schema in (None, 1):
            with self.subTest(schema=schema):
                expected = {str(dest)}
                if schema is not None:
                    (self.ghost / installed_payload.LEGACY_FILENAME).write_text(json.dumps({
                        "schema_version": schema, "source": {"root": str(self.source)},
                        "targets": {str(other): self.entry(other)}}), encoding="utf-8")
                    expected.add(str(other))
                content = inventory.read_bytes()
                self.activate(package)
                record = installed_payload.read(self.ghost)
                self.assertEqual((record["schema_version"], record["installer"]["active_root"]), (2, str(package)))
                self.assertLessEqual(expected, set(record["targets"]))
                self.assertEqual(inventory.read_bytes(), content)
                for name in installed_payload.RECORD_FILENAMES:
                    (self.ghost / name).unlink(missing_ok=True)

    def test_the_hosts_of_a_matching_pending_source_reach_the_record_once(self):
        package = self.prepared()
        relations = {self.ledger: {"source_root": str(self.source), "hosts": ["codex"]}}
        content = json.dumps({"active_root": self.earlier, "source": {
            "root": str(self.source), "ledger_source_roots": relations}}).encode()
        pending = self.ghost / installed_payload.PENDING_SOURCE_FILENAME
        for stopped in (None, "record", "removal"):
            with self.subTest(stopped=stopped):
                record = self.seed({self.ledger: str(self.source)})
                pending.write_bytes(content)
                if stopped == "record":
                    with self.stopped_after("os.replace", installed_payload.ATTRIBUTED_FILENAME):
                        self.activate(package)
                    with self.candidate(package):
                        self.captured(lock.restore_unfinished_publishes)
                    self.assertEqual(pending.read_bytes(), content)
                elif stopped == "removal":
                    real = Path.unlink

                    def unlink(path, *args, **kwargs):
                        if path.name == pending.name:
                            raise KeyboardInterrupt("stopped before the removal")
                        return real(path, *args, **kwargs)
                    with mock.patch.object(Path, "unlink", unlink), self.assertRaisesRegex(KeyboardInterrupt, "removal"):
                        self.activate(package)
                    self.assertEqual(pending.read_bytes(), content)
                self.activate(package)
                expected = dict(record, schema_version=3, installer=dict(record["installer"], active_root=str(package)),
                                source=dict(record["source"], ledger_source_roots=relations))
                self.assertEqual(installed_payload.read(self.ghost), expected)
                self.assertFalse(pending.exists())
                self.activate(package)
                self.assertEqual(installed_payload.read(self.ghost), expected)
                self.assertEqual(install_transaction.unfinished(self.ghost), [])

    def test_a_pending_source_of_another_activation_changes_no_source_fact(self):
        package = self.prepared()
        record = self.seed({self.ledger: str(self.source)})
        pending = self.ghost / installed_payload.PENDING_SOURCE_FILENAME
        pending.write_text(json.dumps({"active_root": self.earlier + "-next", "source": {
            "root": str(self.source),
            "ledger_source_roots": {self.ledger: {"source_root": str(self.source), "hosts": ["codex"]}}}}))
        self.activate(package)
        self.assertEqual(installed_payload.read(self.ghost),
                         dict(record, installer=dict(record["installer"], active_root=str(package))))
        self.assertFalse(pending.exists())

    def test_the_state_of_the_earlier_package_does_not_stop_an_activation(self):
        package = self.prepared()
        earlier = Path(self.earlier)
        for label in ("missing", "unable to read the record"):
            with self.subTest(earlier=label):
                if label != "missing":
                    (earlier / "_shared").mkdir(parents=True)
                    (earlier / "_shared" / "installed_payload.py").write_text("raise SystemExit(1)\n")
                self.seed({self.ledger: {"source_root": str(self.source), "hosts": ["codex"]}})
                before = {path: content for path, content in self.files().items() if path.startswith(self.earlier)}
                self.activate(package)
                self.assertEqual(self.active(), str(package))
                self.assertEqual({path: content for path, content in self.files().items()
                                  if path.startswith(self.earlier)}, before)

    def test_an_activation_that_stops_while_it_replaces_the_commands_is_finished_by_its_package(self):
        command = self.ghost / "bin" / "ghost-alice"
        # The first replacement of the record names the package; the second one belongs to the publish of the commands.
        for call, name, number, unfinished in (("os.replace", "installed-payload-v2.json", 1, 0),
                                               ("Path.rename", "ghost-alice", 1, 1),
                                               ("os.replace", "installed-payload-v2.json", 2, 1)):
            with self.subTest(stopped=name, number=number):
                earlier = self.later()
                record = self.placed(earlier, "codex")
                before = command.read_bytes()
                package = self.later()
                with self.stopped_after(call, name, number):
                    self.activate(package)
                self.assertEqual(len(install_transaction.unfinished(self.ghost)), unfinished)
                self.assertEqual(self.active(), str(package))
                with self.candidate(package):
                    self.captured(lock.restore_unfinished_publishes)
                self.assertEqual(install_transaction.unfinished(self.ghost), [])
                self.assertEqual(installed_payload.read(self.ghost),
                                 dict(record, installer=dict(record["installer"], active_root=str(package))))
                self.assertEqual(command.read_bytes(), before)
                self.activate(package)
                self.assertEqual(self.active(), str(package))
                self.assertIn(str(package), command.read_text(encoding="utf-8"))

    def test_a_first_publish_that_stops_is_undone_and_runs_again(self):
        package = self.prepared()
        self.activate(package)
        for call, name in (("Path.rename", "ghost-alice"), ("os.replace", "installed-payload-v2.json")):
            with self.subTest(stopped=name):
                with self.candidate(package), self.stopped_after(call, name):
                    self.captured(transaction.sync_runtime_shared, self.ctx)
                self.assertEqual(len(install_transaction.unfinished(self.ghost)), 1)
                with self.candidate(package):
                    self.captured(lock.restore_unfinished_publishes)
                self.assertEqual(install_transaction.unfinished(self.ghost), [])
                self.assertIsNone(installed_payload.read(self.ghost))
                self.assertFalse(os.path.lexists(self.ghost / "bin" / "ghost-alice"))
        self.placed(package, "codex")
        self.assertEqual(self.active(), str(package))

    def test_the_activation_keeps_the_original_of_a_command_that_the_user_changed(self):
        first = self.prepared()
        self.activate(first)
        self.placed(first, "codex")
        changed, unchanged = self.ghost / "bin" / "ghost-alice", self.ghost / "bin" / "ghost-alice.ps1"
        user_text, published = changed.read_text(encoding="utf-8") + "# mine\n", unchanged.read_bytes()
        changed.write_text(user_text, encoding="utf-8")
        second = self.later()
        self.activate(second)
        install_transaction.cleanup_completed(self.ghost)
        self.assertIn(str(second), changed.read_text(encoding="utf-8"))
        kept = [path.read_bytes() for path in (self.ghost / "rollbacks").rglob("*") if path.is_file()]
        self.assertIn(user_text.encode(), kept)
        self.assertNotIn(published, kept)


class RecordMigrationTests(TransactionCase):
    """The first install over an earlier installer's records creates the install record from them once, then
    the gateway's service section from its receipt."""

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch("installer.source.input_record", return_value={
            "root": str(self.source), "branch": "main", "commit": "abc123def", "dirty": "clean", "hooks_path": "hooks"}))

    def test_migration_reads_every_host_and_reports_what_it_created(self):
        ctx = self.context("claude")
        state_dir = self.ghost / 'install-state'
        state_dir.mkdir(parents=True, exist_ok=True)
        for host in ('claude', 'future-host'):
            dest = self.home / host / 'task-router'
            dest.mkdir(parents=True)
            (dest / 'SKILL.md').write_text('installed bytes')
            (state_dir / f'{host}.json').write_text(json.dumps({
                'platform': host, 'source_root': str(self.source), 'targets': [{
                    'target_name': 'task-router', 'source_path': str(self.source / 'task-router'),
                    'dest_path': str(dest), 'install_mode': 'copy'}]}))
        runtime = self.ghost / 'runtime/current/_shared'
        runtime.mkdir(parents=True)
        (runtime / 'module.py').write_text('legacy runtime without recorded fingerprint')
        import install_lock
        with install_lock.command_lock(self.ghost / 'install.lock', owner='test'), \
                mock.patch("installed_payload.migrate_service", return_value=None):
            _, out = self.captured(transaction.migrate_record, ctx)
        record = installed_payload.read(self.ghost)
        self.assertEqual({tuple(row['used_by']) for row in record['targets'].values()},
                         {('claude',), ('future-host',), ('claude', 'future-host')})
        self.assertEqual(record['targets'][str(runtime)]['kind'], 'runtime')
        self.assertEqual(record['targets'][str(runtime)]['baseline'], 'unknown')
        self.assertNotIn('files', record['targets'][str(runtime)])
        import uninstall_cleanup
        from unittest.mock import patch
        # A legacy runtime is visible to removal, but present bytes are not historical proof.
        with patch.object(installed_payload, 'read', return_value={
                'targets': {str(runtime): dict(record['targets'][str(runtime)], used_by=['claude'])}}):
            item, = uninstall_cleanup._runtime_artifact_items('claude', [])
        self.assertEqual((item['action'], item['reason']), ('manual-review', 'no-installed-baseline'))
        self.assertIn('3 targets', out)

    def test_v1_record_with_removed_skills_still_inventories_remaining_runtime(self):
        state_dir = self.ghost / 'install-state'
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / 'claude.json').write_text(json.dumps(dict(platform='claude', targets=[], source_root=str(self.source))))
        (self.ghost / installed_payload.LEGACY_FILENAME).write_text(json.dumps(dict(schema_version=1, targets={})))
        runtime = self.ghost / 'runtime/current/_shared'
        runtime.mkdir(parents=True)
        (runtime / 'module.py').write_text('old runtime')
        import install_lock
        with install_lock.command_lock(self.ghost / 'install.lock', owner='test'):
            self.captured(transaction.migrate_record, self.context('claude'))
        record = installed_payload.read(self.ghost)
        self.assertEqual(record['schema_version'], installed_payload.SCHEMA_VERSION)
        self.assertEqual(record['targets'][str(runtime)]['baseline'], 'unknown')
        self.assertEqual(record['targets'][str(runtime)]['used_by'], ['claude'])
        self.assertNotIn('files', record['targets'][str(runtime)])

    def test_the_service_section_is_migrated_after_the_targets_and_reported(self):
        ctx = self.context("claude")
        order = []
        with mock.patch("installed_payload.migrate", side_effect=lambda *a, **k: order.append("targets")), \
             mock.patch("installed_payload.migrate_service",
                        side_effect=lambda root: order.append("service") or
                        {"label": "org.ghost-alice.context-gateway", "companions": 1}) as migrate_service:
            _, out = self.captured(transaction.migrate_record, ctx)
        migrate_service.assert_called_once_with(self.ghost)
        self.assertEqual(order, ["targets", "service"])
        self.assertIn("[INFO] Gateway record created from its installation receipt: org.ghost-alice.context-gateway "
                      "(companion targets added 1)", out)

    def test_nothing_to_migrate_prints_nothing(self):
        with mock.patch("installed_payload.migrate", return_value=None), \
             mock.patch("installed_payload.migrate_service", return_value=None):
            _, out = self.captured(transaction.migrate_record, self.context("codex"))
        self.assertEqual(out, "")

    def test_record_migration_preserves_old_original_bytes_and_links(self):
        # Migrating records proves neither replacement nor release of a recovery original.
        roots = [self.ghost / "install-rollbacks",
                 self.home / ".claude" / "skills" / ".ghost-alice-install-rollbacks"]
        for outcome in ("success", "targets-error", "service-error"):
            with self.subTest(outcome=outcome):
                originals = []
                for root in roots:
                    folder = root / outcome
                    folder.mkdir(parents=True)
                    original = folder / "SKILL.md"
                    original.write_bytes(b"only original\n")
                    link = folder / "source"
                    link.symlink_to("../original-checkout")
                    originals.append((original, link))
                with mock.patch("installed_payload.migrate", return_value=None,
                                side_effect=installed_payload.RecordError("targets failed")
                                if outcome == "targets-error" else None), \
                     mock.patch("installed_payload.migrate_service", return_value=None,
                                side_effect=installed_payload.RecordError("service failed")
                                if outcome == "service-error" else None):
                    self.captured(transaction.migrate_record, self.context("claude"))
                for original, link in originals:
                    self.assertTrue(original.is_file(), str(original))
                    self.assertEqual(original.read_bytes(), b"only original\n")
                    self.assertEqual(os.readlink(link), "../original-checkout")

    def test_a_migration_that_cannot_finish_is_reported_and_the_install_goes_on(self):
        with mock.patch("installed_payload.migrate", side_effect=installed_payload.RecordError("bad snapshot")), \
             mock.patch("installed_payload.migrate_service", side_effect=installed_payload.RecordError("no receipt")):
            _, out = self.captured(transaction.migrate_record, self.context("codex"))
        self.assertIn("[WARN] Install record migration skipped: bad snapshot", out)
        self.assertIn("[WARN] Gateway record migration skipped: no receipt", out)


class RecordTests(TransactionCase):
    def test_postflight_keeps_initial_support_expectation_when_source_disappears(self):
        ctx = self.context("claude")
        checkout = Path(__file__).resolve().parents[1]
        verifier = self.source / "merge-companion" / "scripts"
        verifier.mkdir(parents=True)
        for name in ("install_verifier.py", "file_walker.py"):
            shutil.copy2(checkout / "merge-companion" / "scripts" / name, verifier / name)
        collected = selection.collect_targets(ctx, ["alpha"])
        self.assertEqual(collected.support_target_total, 1)
        for name in ("_shared", "alpha"):
            shutil.copytree(self.source / name, ctx.skills_dir / name)
        shutil.rmtree(self.source / "_shared")
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(checkout / "_shared")}):
            self.assertFalse(transaction.verify_after_copy(ctx, ctx.skills_dir, False, collected))
        state = json.loads((self.ghost / "install-state" / "claude.json").read_text())
        self.assertEqual([(row["target_name"], row["reason"]) for row in state["failures"]],
                         [("_shared", "missing-source")])

    def test_record_writers_keep_selected_family_members_after_source_changes(self):
        ctx = self.context("claude")
        collected = selection.collect_targets(ctx, ["fam"])
        self.assertEqual([target.name for target in collected.all_targets], ["one", "two"])
        shutil.rmtree(self.source / "fam" / "one")
        (self.source / "fam" / "three").mkdir()
        (self.source / "fam" / "three" / "SKILL.md").write_text("new source member")
        with self.runner.patch():
            self.assertTrue(transaction.write_install_state_manifest(ctx, ctx.skills_dir, False, collected))
        state_args = self.runner.tool_calls("install_state_writer.py")[0]
        self.assertEqual(state_args[6::4], ["_shared", "one", "two"])

    def test_postflight_keeps_a_selected_source_that_disappears(self):
        ctx = self.context("claude")
        checkout = Path(__file__).resolve().parents[1]
        verifier = self.source / "merge-companion" / "scripts"
        verifier.mkdir(parents=True)
        for name in ("install_verifier.py", "file_walker.py"):
            shutil.copy2(checkout / "merge-companion" / "scripts" / name, verifier / name)
        (ctx.skills_dir / "_shared").symlink_to(self.source / "_shared")
        collected = selection.collect_targets(ctx, ["alpha"])
        self.assertEqual([target.name for target in collected.all_targets], ["alpha"])
        shutil.copytree(self.source / "alpha", ctx.skills_dir / "alpha")
        shutil.rmtree(self.source / "alpha")
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(checkout / "_shared")}):
            self.assertFalse(transaction.verify_after_copy(ctx, ctx.skills_dir, False, collected))
        state = json.loads((self.ghost / "install-state" / "claude.json").read_text())
        self.assertEqual(state["status"], "partial_failure")
        self.assertEqual([(row["target_name"], row["reason"]) for row in state["failures"]],
                         [("alpha", "missing-source")])

    def test_postflight_calls_the_real_verifier_for_a_disappeared_target(self):
        ctx = self.context("claude")
        checkout = Path(__file__).resolve().parents[1]
        verifier = self.source / "merge-companion" / "scripts"
        verifier.mkdir(parents=True)
        for name in ("install_verifier.py", "file_walker.py"):
            shutil.copy2(checkout / "merge-companion" / "scripts" / name, verifier / name)
        (ctx.skills_dir / "_shared").symlink_to(self.source / "_shared")
        collected = selection.collect_targets(ctx, ["alpha"])
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(checkout / "_shared")}):
            self.assertFalse(transaction.verify_after_copy(ctx, ctx.skills_dir, False, collected))
        state = json.loads((self.ghost / "install-state" / "claude.json").read_text())
        self.assertEqual(state["status"], "partial_failure")
        self.assertEqual([(row["target_name"], row["reason"]) for row in state["failures"]],
                         [("alpha", "missing-target")])

    def setUp(self):
        super().setUp()
        self.info = SourceInfo(root=self.source, branch="main", head="abc123def", dirty="clean")
        self.resolve = mock.patch("installer.source.resolve_source", return_value=self.info)
        self.resolve.start()

    def tearDown(self):
        self.resolve.stop()
        super().tearDown()

    def test_verify_after_copy(self):
        # install.sh:428-470
        ctx = self.context("claude")
        with mock.patch("installer.addons.collect_addon_targets", return_value=[Target("noop", self.home / "addon" / "noop", "noop")]):
            collected = selection.collect_targets(ctx, ["alpha", "fam"])
        (ctx.skills_dir / "_shared").symlink_to(self.source / "_shared")
        (ctx.skills_dir / "alpha").mkdir()
        with self.runner.patch():
            self.assertTrue(transaction.verify_after_copy(ctx, ctx.skills_dir, False, collected))
        args = self.runner.tool_calls("install_verifier.py")[0]
        self.assertEqual(args[:4], ["--platform", "claude", "--state-root", str(self.ghost / "install-state")])
        self.assertEqual(args[4:9], ["--target", "_shared", str(self.source / "_shared"), str(ctx.skills_dir / "_shared"), "symlink"])
        self.assertEqual(args[9:14], ["--target", "alpha", str(self.source / "alpha"), str(ctx.skills_dir / "alpha"), "copy-fallback"])
        self.assertEqual(args[14:19], ["--target", "one", str(self.source / "fam" / "one"), str(ctx.skills_dir / "one"), "missing"])
        self.assertEqual(args[-5:], ["--target", "noop", str(self.home / "addon" / "noop"), str(ctx.skills_dir / "noop"), "missing"])
        failing = FakeRunner()
        failing.respond_to("install_verifier.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(transaction.verify_after_copy, ctx, ctx.skills_dir, False, collected)
        self.assertFalse(ok)
        self.assertIn("[ERROR] Post-install source/destination verification failed; aborting before snapshot", out)

    def test_snapshot_and_empty_manifest(self):
        # install.sh:364-409
        ctx = self.context("claude")
        pending = self.ghost / "pending-merges" / "claude"
        with self.runner.patch():
            self.assertTrue(transaction.snapshot_after_install(ctx))
        self.assertTrue(pending.is_dir())
        self.assertEqual(self.runner.tool_calls("snapshot_cli.py")[0],
                         ["--output", str(pending / "snapshot.json"), "--platform", "claude", "--skills-dir", str(ctx.skills_dir),
                          "--install-record-root", str(self.ghost)])
        self.assertEqual(self.runner.tool_calls("pending_manifest_writer.py"), [[str(pending / "manifest.json"), "claude"]])
        (pending / "manifest.json").write_text("{}", encoding="utf-8")
        again = FakeRunner()
        with again.patch():
            self.assertTrue(transaction.snapshot_after_install(ctx))
        self.assertEqual(again.tool_calls("pending_manifest_writer.py"), [])
        failing = FakeRunner()
        failing.respond_to("snapshot_cli.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(transaction.snapshot_after_install, ctx)
        self.assertFalse(ok)
        self.assertIn("[ERROR] merge-companion snapshot capture failed; aborting install", out)

    def test_write_install_state_manifest(self):
        # install.sh:571-610
        ctx = self.context("claude")
        collected = selection.collect_targets(ctx, ["alpha"])
        (ctx.skills_dir / "_shared").symlink_to(self.source / "_shared")
        with self.runner.patch():
            ok, out = self.captured(transaction.write_install_state_manifest, ctx, ctx.skills_dir, False, collected)
        self.assertTrue(ok)
        state = self.ghost / "install-state" / "claude.json"
        self.assertIn(f"[INFO] Install-state manifest: {state}", out)
        script, args, kwargs = next(c for c in self.runner.calls if c[0].endswith("install_state_writer.py"))
        self.assertEqual(args[:6], ["claude", str(self.source), "main", "abc123def", "clean", str(state)])
        self.assertEqual(args[6:10], ["_shared", str(self.source / "_shared"), str(ctx.skills_dir / "_shared"), "symlink"])
        self.assertEqual(args[10:14], ["alpha", str(self.source / "alpha"), str(ctx.skills_dir / "alpha"), "missing"])
        self.assertFalse(kwargs.get("env"))
        failing = FakeRunner()
        failing.respond_to("install_state_writer.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(transaction.write_install_state_manifest, ctx, ctx.skills_dir, False, collected)
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] Install-state manifest write failed; aborting install: {state}", out)


if __name__ == "__main__":
    unittest.main()
