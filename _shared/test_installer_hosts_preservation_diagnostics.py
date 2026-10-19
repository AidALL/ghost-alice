"""installer.hosts, installer.preservation and installer.diagnostics.

Expected arguments and texts come from installer_lib/install.sh:5-26, 225-362, 783-847,
installer_lib/bootstrap.sh, installer_lib/targets.sh:378-395 and installer_lib/uninstall.sh:142-267.
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_digest  # noqa: E402
import install_lock  # noqa: E402
import installed_payload  # noqa: E402
from installer import diagnostics, hosts, preservation  # noqa: E402
from installer.runner import ToolResult  # noqa: E402
from installer.selection import ALL_SKILLS, Target  # noqa: E402
from installer_testing import FakeRunner, make_context, make_source, quiet, use_source  # noqa: E402


class HostCase(unittest.TestCase):
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


class HookRegistrationTests(HostCase):
    def test_install_hooks_arguments_per_action(self):
        # install.sh:783-847; status and doctor pass no visibility
        ctx = self.context("claude", addon_sources=["/a"])
        with self.runner.patch():
            ok, out = self.captured(hosts.install_hooks, ctx, "install", "claude")
        self.assertTrue(ok)
        self.assertIn("[INFO] Agent hook install... (session-intent + prompt + web-search + tool-checkpoint + "
                      "completion + session-start + io-trace)", out)
        base = ["--core-source-root", str(self.source), "--platform", "claude",
                "--hook-shared-dir", str(self.ghost / "runtime" / "current" / "_shared"), "--skills-dir", str(ctx.skills_dir)]
        self.assertEqual(self.runner.tool_calls("install_hooks.py")[0], base + ["--visibility", "dynamic", "--addon-source", "/a"])
        strict = FakeRunner()
        with strict.patch():
            hosts.install_hooks(self.context("codex", visibility="strict", addon_sources=["/a"]), "install", "codex")
        self.assertEqual(strict.tool_calls("install_hooks.py")[0][8:10], ["--visibility", "strict"])
        status = FakeRunner()
        with status.patch():
            hosts.install_hooks(self.context("claude", visibility="minimal", addon_sources=["/a"]), "status", "claude")
        self.assertEqual(status.tool_calls("install_hooks.py")[0], base + ["--status", "--addon-source", "/a"])
        uninstall = FakeRunner()
        with uninstall.patch():
            hosts.install_hooks(ctx, "uninstall", "claude")
        self.assertEqual(uninstall.tool_calls("install_hooks.py")[0], base + ["--uninstall"])
        failing = FakeRunner()
        failing.respond_to("install_hooks.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(hosts.install_hooks, ctx, "install", "claude")
        self.assertFalse(ok)
        self.assertIn("[ERROR] Hook install failed", out)

    def test_bootstrap_blocks(self):
        # bootstrap.sh:12-38, 86-112
        ctx = self.context("claude")
        with self.runner.patch():
            ok, out = self.captured(hosts.ensure_claude_bootstrap, ctx)
        self.assertTrue(ok)
        self.assertIn("[OK] Claude CLAUDE.md bootstrap block updated", out)
        self.assertTrue((self.home / ".claude").is_dir())
        rules = self.home / ".claude" / "CLAUDE.md"
        self.assertEqual(self.runner.tool_calls("global_rule_blocks.py")[0],
                         ["claude-merge", "--source", str(self.source / "platforms" / "claude" / "CLAUDE.md"),
                          "--dest", str(rules), "--proposed", f"{rules}.ghost-alice-proposed"])
        proposed = FakeRunner()
        proposed.respond_to("global_rule_blocks.py", ToolResult(0, f"proposed:{rules}.ghost-alice-proposed\n", ""))
        with proposed.patch():
            ok, out = self.captured(hosts.ensure_claude_bootstrap, ctx)
        self.assertTrue(ok)
        self.assertIn(f"[WARN] Claude CLAUDE.md is user-owned; wrote proposed file instead: {rules}.ghost-alice-proposed", out)
        codex = FakeRunner()
        with codex.patch():
            ok, out = self.captured(hosts.ensure_codex_bootstrap, ctx)
        self.assertTrue(ok)
        self.assertIn("[OK] Codex AGENTS.md bootstrap block updated", out)
        agents = self.home / ".codex" / "AGENTS.md"
        self.assertEqual(codex.tool_calls("global_rule_blocks.py")[0],
                         ["codex-merge", "--source", str(self.source / "platforms" / "codex" / "AGENTS.md"),
                          "--loader", str(self.source / "platforms" / "codex" / "bootstrap.md"),
                          "--dest", str(agents), "--proposed", f"{agents}.ghost-alice-proposed"])
        failing = FakeRunner()
        failing.respond_to("global_rule_blocks.py", ToolResult(1, "", "boom\n"))
        with failing.patch():
            ok, out = self.captured(hosts.ensure_codex_bootstrap, ctx)
        self.assertFalse(ok)
        self.assertIn("[ERROR] Codex AGENTS.md block merge failed; aborting install", out)

    def test_bootstrap_removal(self):
        # bootstrap.sh:40-84, 114-152
        ctx = self.context("claude")
        self.assertTrue(hosts.remove_claude_bootstrap(ctx))
        self.assertFalse(hosts.remove_claude_bootstrap(ctx, require_change=True))
        rules = self.home / ".claude" / "CLAUDE.md"
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text("# Ghost-ALICE Claude Bootstrap\n", encoding="utf-8")
        removed = FakeRunner()
        removed.respond_to("global_rule_blocks.py", ToolResult(0, f"removed:{rules}\n", ""))
        with removed.patch():
            ok, out = self.captured(hosts.remove_claude_bootstrap_if_unused, ctx, ctx.skills_dir)
        self.assertTrue(ok)
        self.assertIn("[OK] Claude CLAUDE.md bootstrap removed", out)
        self.assertEqual(removed.tool_calls("global_rule_blocks.py")[0], ["claude-remove", "--dest", str(rules)])
        updated = FakeRunner()
        updated.respond_to("global_rule_blocks.py", ToolResult(0, f"updated:{rules}\n", ""))
        with updated.patch():
            ok, out = self.captured(hosts.remove_claude_bootstrap, ctx, True)
        self.assertTrue(ok)
        self.assertIn("[OK] Claude CLAUDE.md Ghost-ALICE block removed; user content preserved", out)
        unchanged = FakeRunner()
        unchanged.respond_to("global_rule_blocks.py", ToolResult(0, "unchanged\n", ""))
        with unchanged.patch():
            self.assertFalse(hosts.remove_claude_bootstrap(ctx, True))
            self.assertTrue(hosts.remove_claude_bootstrap(ctx, False))
        (ctx.skills_dir / "task-router").mkdir()
        with FakeRunner().patch():
            self.assertFalse(hosts.remove_claude_bootstrap_if_unused(ctx, ctx.skills_dir))
        codex_ctx = self.context("codex")
        self.assertFalse(hosts.remove_codex_bootstrap_if_unused(codex_ctx, codex_ctx.skills_dir))
        agents = self.home / ".codex" / "AGENTS.md"
        agents.parent.mkdir(parents=True)
        agents.write_text("x", encoding="utf-8")
        removed = FakeRunner()
        removed.respond_to("global_rule_blocks.py", ToolResult(0, f"removed:{agents}\n", ""))
        with removed.patch():
            ok, out = self.captured(hosts.remove_codex_bootstrap_if_unused, codex_ctx, codex_ctx.skills_dir)
        self.assertTrue(ok)
        self.assertIn("[OK] Codex AGENTS.md bootstrap removed", out)
        self.assertEqual(removed.tool_calls("global_rule_blocks.py")[0], ["codex-remove", "--dest", str(agents)])

    def test_sync_commands(self):
        # installer_lib/install.sh:5-26
        ctx = self.context("claude")
        with self.runner.patch():
            self.assertEqual(hosts.sync_commands(ctx, "check", "claude"), 0)
        self.assertEqual(self.runner.tool_calls("commands_sync.py")[0],
                         [str(self.source / "skill-catalog" / "skills.json"), str(self.source / ".claude" / "commands"),
                          "check", "claude"])
        (self.source / "skill-catalog" / "skills.json").unlink()
        rc, out = self.captured(hosts.sync_commands, ctx)
        self.assertEqual(rc, 1)
        self.assertIn(f"[WARN] skills.json not found: {self.source / 'skill-catalog' / 'skills.json'}", out)


class PreservationTests(HostCase):
    def test_encoding_guard_sequence(self):
        # install.sh:225-271
        ctx = self.context("claude")
        ctx.addon_targets = [Target("noop", self.home / "addon" / "noop", "noop")]
        (ctx.skills_dir / "_shared").symlink_to(self.source / "_shared")
        with self.runner.patch():
            self.assertTrue(preservation.encoding_guard(ctx, ["alpha", "fam"]))
        calls = self.runner.tool_calls("encoding_guard.py")
        self.assertEqual(calls[0], ["--repo-root", str(self.source)])
        self.assertEqual(calls[1], ["--repo-root", str(self.home / "addon" / "noop")])
        self.assertEqual(calls[2], ["--repo-root", str(ctx.skills_dir), "--exclude-root", str(ctx.skills_dir / "_shared"),
                                    "--exclude-root", str(ctx.skills_dir / "alpha"), "--exclude-root", str(ctx.skills_dir / "one"),
                                    "--exclude-root", str(ctx.skills_dir / "two"), "--exclude-root", str(ctx.skills_dir / "noop")])
        failing = FakeRunner()
        failing.respond_to("encoding_guard.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(preservation.encoding_guard, ctx, ["alpha"])
        self.assertFalse(ok)
        self.assertIn(f"[ERROR] encoding guard failed; aborting install: {self.source}", out)
        missing = self.context("codex")
        missing.skills_dir.rmdir()
        with FakeRunner().patch() as _:
            self.assertTrue(preservation.encoding_guard(missing, ["alpha"]))

    def test_preflight_without_snapshot_quarantines_unclean_targets(self):
        # install.sh:273-337
        ctx = self.context("codex")
        (ctx.skills_dir / "_shared").mkdir()
        (ctx.skills_dir / "alpha").mkdir()
        (ctx.skills_dir / "one").symlink_to(self.source / "fam" / "one")
        self.runner.respond(lambda s, a: a[:1] == ["classify-clean"] and "alpha" in a, ToolResult(1))
        with self.runner.patch():
            self.assertTrue(preservation.preflight(ctx, ["alpha", "fam"]))
        classify = self.runner.tool_calls("installer_assets_cli.py")
        self.assertEqual(classify, [["classify-clean", "--asset-id", "_shared", "--path", str(ctx.skills_dir / "_shared"),
                                     "--repo-root", str(self.source)],
                                    ["classify-clean", "--asset-id", "alpha", "--path", str(ctx.skills_dir / "alpha"),
                                     "--repo-root", str(self.source)]])
        pending = self.ghost / "pending-merges" / "codex"
        self.assertEqual(self.runner.tool_calls("quarantine_legacy_cli.py"),
                         [["--target", str(ctx.skills_dir / "alpha"), "--target-name", "alpha", "--pending", str(pending),
                           "--manifest", str(pending / "manifest.json"), "--platform", "codex"]])
        self.assertTrue(pending.is_dir())
        failing = FakeRunner()
        failing.respond_to("installer_assets_cli.py", ToolResult(1))
        failing.respond_to("quarantine_legacy_cli.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(preservation.preflight, ctx, ["alpha"])
        self.assertFalse(ok)
        self.assertIn("[ERROR] unowned target quarantine failed; aborting install: _shared", out)

    def test_preflight_with_snapshot_runs_the_diff_collector(self):
        # install.sh:339-362
        ctx = self.context("codex")
        ctx.addon_targets = [Target("noop", self.home / "addon" / "noop", "noop")]
        pending = self.ghost / "pending-merges" / "codex"
        pending.mkdir(parents=True)
        (pending / "snapshot.json").write_text("{}", encoding="utf-8")
        with self.runner.patch():
            self.assertTrue(preservation.preflight(ctx, ["alpha"]))
        self.assertEqual(self.runner.tool_calls("diff_collector_cli.py")[0],
                         ["--snapshot", str(pending / "snapshot.json"), "--pending", str(pending),
                          "--manifest", str(pending / "manifest.json"), "--platform", "codex",
                          "--skills-dir", str(ctx.skills_dir), "--target", "_shared", "--target", "alpha", "--target", "noop"])
        failing = FakeRunner()
        failing.respond_to("diff_collector_cli.py", ToolResult(1))
        with failing.patch():
            ok, out = self.captured(preservation.preflight, ctx, ["alpha"])
        self.assertFalse(ok)
        self.assertIn("[ERROR] merge-companion preflight change detection failed; aborting install", out)

    def test_cleanup_pending(self):
        # targets.sh:378-395
        ctx = self.context("claude")
        self.runner.respond_to("cleanup_false_positive_legacy.py", ToolResult(3))
        with self.runner.patch():
            self.assertEqual(preservation.cleanup_pending(ctx), 3)
        pending = self.ghost / "pending-merges" / "claude"
        self.assertEqual(self.runner.tool_calls("cleanup_false_positive_legacy.py")[0],
                         ["--platform", "claude", "--pending", str(pending), "--manifest", str(pending / "manifest.json"),
                          "--repo-root", str(self.source), "--apply"])


class InspectTests(HostCase):
    def test_print_target_status(self):
        # uninstall.sh:142-155
        link = self.home / "linked"
        link.symlink_to(self.source / "alpha")
        copy = self.home / "copied"
        copy.mkdir()
        _, out = self.captured(diagnostics.print_target_status, "alpha", link)
        self.assertEqual(out, f"  ● alpha  →  {self.source / 'alpha'}\n")
        _, out = self.captured(diagnostics.print_target_status, "beta", copy, "    ")
        self.assertEqual(out, "    ■ beta  (copied)\n")
        _, out = self.captured(diagnostics.print_target_status, "gamma", self.home / "none")
        self.assertEqual(out, "  ○ gamma  (not installed)\n")

    def test_run_install_doctor_arguments(self):
        # uninstall.sh:157-218
        ctx = self.context("codex")
        with self.runner.patch():
            self.assertEqual(diagnostics.run_install_doctor(ctx, "doctor", ctx.skills_dir), 0)
        args = self.runner.tool_calls("install_doctor.py")[0]
        self.assertEqual(args[:2], ["--platform", "codex"])
        self.assertEqual(args[args.index("--repo-root") + 1], str(self.source))
        self.assertEqual(args[args.index("--encoding-root") + 1], str(self.source))
        self.assertEqual(args[args.index("--encoding-root") + 3], str(ctx.skills_dir))
        self.assertEqual(args[args.index("--ghost-alice-root") + 1], str(self.ghost))
        self.assertEqual(args[args.index("--install-state-manifest") + 1], str(self.ghost / "install-state" / "codex.json"))
        self.assertEqual(args[args.index("--runtime-shared") + 1], str(self.ghost / "runtime" / "current" / "_shared"))
        self.assertEqual(args[args.index("--skills-root") + 1], str(ctx.skills_dir))
        self.assertIn("--strict", args)
        self.assertEqual(args[args.index("--hook-config") + 1], str(self.home / ".codex" / "hooks.json"))
        gr = args.index("--global-rule")
        self.assertEqual(args[gr + 1:gr + 6], ["codex-bootstrap", str(self.home / ".codex" / "AGENTS.md"),
                                              "# Ghost-ALICE Codex Bootstrap",
                                              "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->",
                                              "<!-- Ghost-ALICE managed block end: codex-bootstrap -->"])
        targets = [args[i + 1] for i, t in enumerate(args) if t == "--target"]
        self.assertEqual(targets[0], "_shared")
        self.assertEqual(args[args.index("--target") + 2], str(ctx.skills_dir / "_shared"))
        self.assertEqual(targets[1:], [name for skill in ALL_SKILLS for name in
                                       ([skill] if (self.source / skill / "SKILL.md").is_file() else
                                        [p.name for p in sorted((self.source / skill).iterdir()) if (p / "SKILL.md").is_file()]
                                        if (self.source / skill).is_dir() else [])])
        status = FakeRunner()
        with status.patch():
            diagnostics.run_install_doctor(self.context("claude"), "status", self.home / ".claude" / "skills")
        args = status.tool_calls("install_doctor.py")[0]
        self.assertNotIn("--strict", args)
        self.assertEqual(args[args.index("--hook-config") + 1], str(self.home / ".claude" / "settings.json"))
        self.assertEqual(args[args.index("--global-rule") + 1:args.index("--global-rule") + 3],
                         ["claude-bootstrap", str(self.home / ".claude" / "CLAUDE.md")])

    def test_check_status_order(self):
        # uninstall.sh:220-246
        ctx = self.context("claude")
        with self.runner.patch():
            rc, out = self.captured(diagnostics.check_status, ctx)
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith(f"Install status ({ctx.skills_dir}):\n\n  ○ _shared  (not installed)\n"))
        self.assertIn("  ○ adversarial-verification  (source missing)\n", out)
        self.assertIn("[INFO] Running installer doctor diagnostics...", out)
        self.assertEqual([c[0].rsplit("/", 1)[-1] for c in self.runner.calls if c[0] != "command"],
                         ["install_doctor.py", "install_hooks.py"])
        self.assertIn("--status", self.runner.tool_calls("install_hooks.py")[0])

    def test_run_doctor_reports_leftover_markers_and_never_resumes(self):
        # uninstall.sh:248-267 without the resume
        ctx = self.context("claude")
        with self.runner.patch():
            rc, out = self.captured(diagnostics.run_doctor, ctx)
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith(f"Installer doctor ({ctx.skills_dir}):\n"))
        self.assertEqual([c[0].rsplit("/", 1)[-1] for c in self.runner.calls if c[0] != "command"],
                         ["install_doctor.py", "install_hooks.py"])
        self.assertIn("--strict", self.runner.tool_calls("install_doctor.py")[0])
        addons_dir = self.ghost / "addons" / "claude"
        addons_dir.mkdir(parents=True)
        (addons_dir / "noop.json.removing").write_text("{}", encoding="utf-8")
        again = FakeRunner()
        with again.patch():
            rc, out = self.captured(diagnostics.run_doctor, ctx)
        self.assertEqual(rc, 1)
        self.assertIn("[WARN] A pending addon uninstall could not complete (manual review needed); see .removing markers "
                      "under ~/.ghost-alice/addons", out)
        self.assertNotIn("--resume-pending", [a for _, args, _ in again.calls for a in args])
        # uninstall.sh:264-265: the doctor's own code propagates; a hook status failure is 1
        failing = FakeRunner()
        failing.respond_to("install_doctor.py", ToolResult(2))
        with failing.patch():
            rc, _ = self.captured(diagnostics.run_doctor, self.context("codex"))
        self.assertEqual(rc, 2)
        hooks_failing = FakeRunner()
        hooks_failing.respond_to("install_hooks.py", ToolResult(2))
        with hooks_failing.patch():
            rc, _ = self.captured(diagnostics.run_doctor, self.context("codex"))
        self.assertEqual(rc, 1)


class InstallRecordReportTests(HostCase):
    """`--status` and `--doctor` read the install record and report it; their result stays what the checks decide."""

    def write_record(self, targets, **sections):
        lock = self.ghost / "install.lock"
        install_lock.acquire_lock(lock, owner="test")
        try:
            installed_payload.update(self.ghost, {"targets": targets, **sections})
        finally:
            install_lock.release_lock(lock)

    @staticmethod
    def entry(mode, used_by, **fields):
        entry = {"name": "x", "kind": "skill", "mode": mode, "provider": "core", "source": "/src/x",
                 "source_commit": "abc", "used_by": used_by, "installed_at": "2026-10-20T00:00:00+00:00",
                 "baseline": "recorded"}
        entry.update(fields)
        return entry

    def run_both(self, ctx):
        for run in (diagnostics.check_status, diagnostics.run_doctor):
            runner = FakeRunner()
            with runner.patch():
                rc, out = self.captured(run, ctx)
            yield run.__name__, rc, out

    def test_no_install_record_yet(self):
        record = self.ghost / "installed-payload.json"
        for name, rc, out in self.run_both(self.context("claude")):
            with self.subTest(command=name):
                self.assertEqual(rc, 0)
                self.assertIn(f"[INFO] Install record: none yet; the next install writes {record}", out)

    def test_the_summary_and_every_difference_of_this_hosts_targets(self):
        ctx = self.context("claude")
        linked = ctx.skills_dir / "alpha"
        linked.symlink_to(self.source / "alpha")
        moved = ctx.skills_dir / "one"
        moved.symlink_to(self.home / "elsewhere")
        runtime = self.ghost / "runtime" / "current" / "_shared"
        runtime.mkdir(parents=True)
        (runtime / "a.py").write_text("a", encoding="utf-8")
        (runtime / "b.py").write_text("b", encoding="utf-8")
        files = content_digest.tree(runtime)
        (runtime / "a.py").write_text("edited", encoding="utf-8")
        (runtime / "b.py").unlink()
        (runtime / "c.py").write_text("new", encoding="utf-8")
        codex_copy = self.home / ".agents" / "skills" / "alpha"
        self.write_record({
            str(linked): self.entry("link", ["claude"], link_target=str(self.source / "alpha")),
            str(moved): self.entry("link", ["claude"], link_target=str(self.source / "fam" / "one")),
            str(runtime): self.entry("copy", ["claude", "codex"], kind="runtime", files=files),
            str(ctx.skills_dir / "two"): self.entry("copy", ["claude"], baseline="unknown"),
            str(codex_copy): self.entry("copy", ["codex"], files={"SKILL.md": "sha256:" + "0" * 64}),
        }, last_transaction={"id": "20261020T000000Z-1-aaaaaaaa", "updated_at": "2026-10-20T00:00:00+00:00",
                             "rollback_dir": str(self.ghost / "rollbacks" / "20261020T000000Z-1-aaaaaaaa")})
        record = self.ghost / "installed-payload.json"
        for name, rc, out in self.run_both(ctx):
            with self.subTest(command=name):
                self.assertEqual(rc, 0)
                self.assertIn(f"[INFO] Install record ({record}): 4 claude targets (link 2, copy 2; baseline recorded 3, "
                              "migrated 0, unknown 1); last publish 20261020T000000Z-1-aaaaaaaa", out)
                self.assertIn(f"[WARN] Differs from the install record: {moved} (link target differs)", out)
                self.assertIn(f"[WARN] Differs from the install record: {runtime} (missing b.py; changed a.py; "
                              "extra c.py)", out)
                self.assertNotIn(f"Differs from the install record: {linked}", out)
                self.assertNotIn(str(codex_copy), out)
                self.assertNotIn("match the install record", out)

    def test_matching_targets_are_reported_as_matching(self):
        ctx = self.context("claude")
        linked = ctx.skills_dir / "alpha"
        linked.symlink_to(self.source / "alpha")
        self.write_record({str(linked): self.entry("link", ["claude"], link_target=str(self.source / "alpha"))})
        for name, rc, out in self.run_both(ctx):
            with self.subTest(command=name):
                self.assertIn("[INFO] Install record (", out)
                self.assertIn("last publish none", out)
                self.assertIn("[OK] Installed targets match the install record", out)

    @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0, "needs a file this user cannot read")
    def test_a_target_that_cannot_be_read_is_reported_and_the_command_goes_on(self):
        ctx = self.context("claude")
        runtime = self.ghost / "runtime" / "current" / "_shared"
        runtime.mkdir(parents=True)
        secret = runtime / "a.py"
        secret.write_text("a", encoding="utf-8")
        self.write_record({str(runtime): self.entry("copy", ["claude"], kind="runtime",
                                                    files=content_digest.tree(runtime))})
        secret.chmod(0)
        try:
            results = list(self.run_both(ctx))
        finally:
            secret.chmod(0o644)
        for name, rc, out in results:
            with self.subTest(command=name):
                self.assertEqual(rc, 0)
                self.assertIn(f"[WARN] Install record comparison failed for {runtime}: ", out)

    def test_targets_without_a_baseline_are_counted_and_never_reported_as_matching(self):
        ctx = self.context("claude")
        linked = ctx.skills_dir / "alpha"
        linked.symlink_to(self.source / "alpha")
        self.write_record({
            str(linked): self.entry("link", ["claude"], link_target=str(self.source / "alpha")),
            str(ctx.skills_dir / "two"): self.entry("copy", ["claude"], baseline="unknown"),
            str(ctx.skills_dir / "one"): self.entry("copy", ["claude"], baseline="unknown"),
        })
        for name, rc, out in self.run_both(ctx):
            with self.subTest(command=name):
                self.assertEqual(rc, 0)
                self.assertIn("[INFO] Not compared, no baseline in the install record: 2 targets", out)
                self.assertIn("[OK] Compared targets match the install record: 1 target", out)
                self.assertNotIn("Installed targets match the install record", out)
        self.write_record({str(ctx.skills_dir / name): self.entry("copy", ["claude"], baseline="unknown")
                           for name in ("one", "two")} | {
            str(linked): self.entry("link", ["claude"], baseline="unknown")})
        for name, rc, out in self.run_both(ctx):
            with self.subTest(command=name, compared="none"):
                self.assertIn("[INFO] Not compared, no baseline in the install record: 3 targets", out)
                self.assertNotIn("match the install record", out)

    def test_an_unreadable_install_record_is_reported_and_changes_no_result(self):
        record = self.ghost / "installed-payload.json"
        self.ghost.mkdir(parents=True)
        record.write_text("{broken", encoding="utf-8")
        for name, rc, out in self.run_both(self.context("claude")):
            with self.subTest(command=name):
                self.assertEqual(rc, 0)
                self.assertIn(f"[WARN] Install record unreadable ({record}): ", out)


if __name__ == "__main__":
    unittest.main()
