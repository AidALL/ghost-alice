"""installer.report: messages, verbosity, the install log, progress lines, the report and the event file.

Expected texts come from installer_lib/report.sh; the line numbers are cited next to each case.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer import report  # noqa: E402
from installer import runner  # noqa: E402


class ReportCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        for key in ("GHOST_ALICE_INSTALL_LOG_FILE", "GHOST_ALICE_INSTALL_EVENT_FILE",
                    "GHOST_ALICE_INSTALL_REPORT_CHILD", "GHOST_ALICE_INSTALL_PROGRESS"):
            os.environ.pop(key, None)
        report.configure(verbose=False, report_child=False, tty=False, progress_env="auto", home=self.home)

    def tearDown(self):
        report.configure(verbose=False, report_child=False, tty=False, progress_env="auto", home=None)
        self.env.stop()
        self.temp.cleanup()

    @staticmethod
    def captured(fn, *args, **kwargs) -> str:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            fn(*args, **kwargs)
        return buffer.getvalue()


class InstallCommandTests(ReportCase):
    def test_hints_name_the_command_the_shell_reports(self):
        # install.ps1 reports `.\\install.cmd`; without a report the hints name `bash install.sh`.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GHOST_ALICE_INSTALL_COMMAND", None)
            self.assertEqual(report.install_command(), "bash install.sh")
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_COMMAND": ".\\install.cmd"}):
            self.assertEqual(report.install_command(), ".\\install.cmd")
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_COMMAND": ""}):
            self.assertEqual(report.install_command(), "bash install.sh")


class MessageTests(ReportCase):
    def test_prefixes_without_color_when_not_a_tty(self):
        # report.sh:5-11; colors only on a terminal (install.sh:95-99)
        self.assertEqual(self.captured(report.info, "hello"), "[INFO] hello\n")
        self.assertEqual(self.captured(report.ok, "done"), "[OK] done\n")
        self.assertEqual(self.captured(report.warn, "careful"), "[WARN] careful\n")
        self.assertEqual(self.captured(report.error, "bad"), "[ERROR] bad\n")
        self.assertFalse(report.colors_enabled())

    def test_prefixes_colored_on_a_tty(self):
        report.configure(verbose=False, report_child=False, tty=True, progress_env="auto", home=self.home)
        out = self.captured(report.info, "hello")
        self.assertTrue(report.colors_enabled())
        self.assertIn("\033[0;36m[INFO]\033[0m hello", out)
        self.assertIn("\033[0;32m[OK]\033[0m", self.captured(report.ok, "x"))
        self.assertIn("\033[0;33m[WARN]\033[0m", self.captured(report.warn, "x"))
        self.assertIn("\033[0;31m[ERROR]\033[0m", self.captured(report.error, "x"))

    def test_detail_messages_only_in_verbose_mode(self):
        # report.sh:23-31
        self.assertEqual(self.captured(report.detail_ok, "x"), "")
        self.assertEqual(self.captured(report.detail_warn, "x"), "")
        report.configure(verbose=True, report_child=False, tty=False, progress_env="auto", home=self.home)
        self.assertEqual(self.captured(report.detail_ok, "x"), "[OK] x\n")
        self.assertEqual(self.captured(report.detail_warn, "x"), "[WARN] x\n")

    def test_compact_and_report_flags(self):
        # report.sh:70-76
        self.assertTrue(report.compact_output_enabled())
        self.assertTrue(report.report_enabled())
        report.configure(verbose=True, report_child=False, tty=False, progress_env="auto", home=self.home)
        self.assertFalse(report.compact_output_enabled())
        self.assertFalse(report.report_enabled())
        report.configure(verbose=False, report_child=True, tty=False, progress_env="auto", home=self.home)
        self.assertTrue(report.compact_output_enabled())
        self.assertFalse(report.report_enabled())

    def test_live_counter_matrix(self):
        # report.sh:33-39
        for progress, verbose, tty, expected in (
            ("auto", False, True, True),
            ("auto", False, False, False),
            ("auto", True, True, False),
            ("0", False, True, False), ("false", False, True, False), ("False", False, True, False),
            ("FALSE", False, True, False), ("off", False, True, False), ("OFF", False, True, False),
            ("no", False, True, False), ("NO", False, True, False),
            ("1", False, True, True),
        ):
            with self.subTest(progress=progress, verbose=verbose, tty=tty):
                report.configure(verbose=verbose, report_child=False, tty=tty, progress_env=progress, home=self.home)
                self.assertEqual(report.live_counter_enabled(), expected)

    def test_configure_reads_progress_env_and_tty_defaults(self):
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_PROGRESS": "off"}):
            state = report.configure(verbose=False, report_child=False, home=self.home)
        self.assertEqual(state.progress_env, "off")
        self.assertFalse(report.live_counter_enabled())

    def test_log_and_event_env_only_honored_for_a_report_child(self):
        # The install.ps1 rule (test_installer_default_auto.py:790-824): a stale parent env is ignored
        stale_log = self.home / "stale.log"
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_LOG_FILE": str(stale_log),
                                          "GHOST_ALICE_INSTALL_EVENT_FILE": str(self.home / "stale.events.jsonl")}):
            parent = report.configure(verbose=False, report_child=False, tty=False, progress_env="auto", home=self.home)
            self.assertIsNone(parent.log_file)
            self.assertIsNone(parent.event_file)
            child = report.configure(verbose=False, report_child=True, tty=False, progress_env="auto", home=self.home)
            self.assertEqual(child.log_file, stale_log)
            self.assertEqual(child.event_file, self.home / "stale.events.jsonl")


class LogTests(ReportCase):
    def test_log_init_creates_the_install_log_path_once(self):
        # report.sh:78-89
        path = report.log_init()
        self.assertEqual(path.parent, self.home / ".ghost-alice" / "install")
        self.assertTrue(path.parent.is_dir())
        self.assertRegex(path.name, r"^\d{4}-\d{2}-\d{2}-\d{6}\.log$")
        self.assertEqual(os.environ.get("GHOST_ALICE_INSTALL_LOG_FILE"), str(path))
        self.assertEqual(report.log_init(), path)

    def test_log_init_keeps_the_last_thirty_runs_before_it_opens_a_new_log(self):
        # A run is its .log and its .events.jsonl.
        root = self.home / ".ghost-alice" / "install"
        root.mkdir(parents=True)
        stamps = [f"2026-10-{day:02d}-{hour:02d}0000" for day in range(1, 17) for hour in (9, 21)]
        for stamp in stamps:
            (root / f"{stamp}.log").write_text("log\n", encoding="utf-8")
            (root / f"{stamp}.events.jsonl").write_text("", encoding="utf-8")
        path = report.log_init()
        kept = sorted({name.name.split(".", 1)[0] for name in root.iterdir()})
        self.assertEqual(kept, stamps[2:])
        self.assertFalse(path.exists())

    def test_the_install_log_folder_comes_from_the_installer_locations(self):
        folder = self.home / "configured-install-logs"
        with mock.patch.object(report.locations, "install_log_root", return_value=folder) as root:
            path = report.log_init()
        root.assert_called_once_with(self.home)
        self.assertEqual(path.parent, folder)

    def test_a_child_with_an_inherited_log_removes_nothing(self):
        root = self.home / ".ghost-alice" / "install"
        root.mkdir(parents=True)
        for day in range(1, 33):
            (root / f"2026-10-{day:02d}-120000.log").write_text("log\n", encoding="utf-8")
        inherited = root / "2026-10-32-120000.log"
        report.configure(verbose=False, report_child=True, tty=False, progress_env="auto", home=self.home,
                         log_file=inherited)
        self.assertEqual(report.log_init(), inherited)
        self.assertEqual(len(list(root.iterdir())), 32)

    def test_run_logged_appends_step_output_to_the_log_in_compact_mode(self):
        # report.sh:103-110
        def step(word):
            report.info(word)
            print("plain " + word)
            return 7

        result = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = report.run_logged(step, "one")
            report.run_logged(step, "two")
        self.assertEqual(result, 7)
        self.assertEqual(out.getvalue(), "")
        log = report.state().log_file
        self.assertIsNotNone(log)
        text = log.read_text(encoding="utf-8")
        self.assertIn("[INFO] one\nplain one\n", text)
        self.assertIn("[INFO] two\nplain two\n", text)
        self.assertIsNone(report.sink())

    def test_run_logged_prints_directly_in_verbose_mode(self):
        report.configure(verbose=True, report_child=False, tty=False, progress_env="auto", home=self.home)
        out = self.captured(report.run_logged, report.info, "shown")
        self.assertEqual(out, "[INFO] shown\n")
        self.assertIsNone(report.state().log_file)

    def test_child_tools_write_into_the_log_during_a_logged_step(self):
        # the child inherits the sink, like `>>"$LOG" 2>&1` (report.sh:106)
        def step():
            return runner.run_tool("_shared/install_lock.py", "--help")

        with contextlib.redirect_stdout(io.StringIO()):
            result = report.run_logged(step)
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", report.state().log_file.read_text(encoding="utf-8"))

    def test_failure_excerpt_filters_and_tails(self):
        # report.sh:91-101
        log = self.home / "install.log"
        lines = ["[INFO] fine", "[ERROR] first   ", "[WARN] [auto] Install failed for codex (exit code 1)",
                 "addon install collision detected", "addon manifest error", "Exception: boom",
                 "step failed - aborting", "clone failed here", "python not found", "Python 3.11+ is required",
                 "[ERROR] last"]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        excerpt = report.failure_excerpt(log)
        self.assertEqual(len(excerpt), 8)
        self.assertEqual(excerpt[0], "addon install collision detected")
        self.assertEqual(excerpt[-1], "[ERROR] last")
        self.assertNotIn("[INFO] fine", excerpt)
        self.assertEqual(report.failure_excerpt(log, max_lines=2), ["Python 3.11+ is required", "[ERROR] last"])
        self.assertEqual(report.failure_excerpt(None), [])
        self.assertEqual(report.failure_excerpt(self.home / "missing.log"), [])
        full = report.failure_excerpt(log, max_lines=20)
        self.assertEqual(full[0], "[ERROR] first")


class LineTests(ReportCase):
    def test_count_label_and_sync_summary(self):
        # report.sh:41-68
        self.assertEqual(report.count_label(1, "skill target", "skill targets"), "1 skill target")
        self.assertEqual(report.count_label(3, "skill target", "skill targets"), "3 skill targets")
        out = self.captured(report.print_skill_sync_summary, 3, 1, 2, 1, 0, "copy-only compatibility mode")
        self.assertEqual(out, "[INFO] [2/5] Skill sync: 3 skill targets, 1 support target; "
                              "2 installed, 1 updated, 0 skipped (copy-only compatibility mode)\n")
        out = self.captured(report.print_skill_sync_summary, 1, 0, 0, 0, 1)
        self.assertEqual(out, "[INFO] [2/5] Skill sync: 1 skill target, 0 support targets; 0 installed, 0 updated, 1 skipped\n")

    def test_join_by(self):
        self.assertEqual(report.join_by(", ", ["claude", "codex"]), "claude, codex")
        self.assertEqual(report.join_by(", ", []), "")

    def test_hook_suite_label(self):
        # report.sh:126-129; test_report_hook_suite_label.py
        for platform in ("claude", "codex", ""):
            label = report.hook_suite_label(platform)
            self.assertEqual(label, "session-intent, tool-checkpoint, completion, session-start, session-receipt, io-trace")
            self.assertNotIn("web-search-first", label)
            self.assertNotIn("prompt", label)

    def test_sync_lines(self):
        # report.sh:131-140
        self.assertEqual(report.skill_sync_line(0, 0, 0),
                         "  [2/5] Skill sync          [0] [Current], [0] [updated], [0] [newly added]")
        self.assertEqual(report.skill_sync_line(1, 2, 3),
                         "  [2/5] Skill sync          [1] [Current], [2] [updated], [3] [newly added]")
        self.assertEqual(report.common_skill_sync_line(24), "  [2/5] Skill sync          [24] common targets")

    def test_progress_bar(self):
        # report.sh:142-156
        self.assertEqual(report.progress_bar(0, 24, 30), "-" * 30)
        self.assertEqual(report.progress_bar(24, 24, 30), "#" * 30)
        self.assertEqual(report.progress_bar(12, 24, 30), "#" * 15 + "-" * 15)
        self.assertEqual(report.progress_bar(1, 24, 30), "#" + "-" * 29)
        self.assertEqual(report.progress_bar(5, 0, 20), "-" * 20)
        self.assertEqual(report.progress_bar(40, 24, 30), "#" * 30)

    def test_common_target_progress_line(self):
        # report.sh:158-163; test_install_progress_line_width.py:101-102, 136-138
        self.assertEqual(report.common_target_progress_line(2, 2, "done"),
                         "        Common targets      [##############################] [2/2] done")
        line = report.common_target_progress_line(26, 26, "common targets synced on all platforms")
        self.assertIn("Common targets", line)
        self.assertIn("[26/26]", line)
        self.assertTrue(line.endswith("common targets synced on all platforms"))
        self.assertEqual(report.common_target_progress_line(0, 24),
                         "        Common targets      [------------------------------] [0/24] common targets synced")
        self.assertEqual(report.clear_current_line(), "\r\x1b[2K")

    def test_animation_frames(self):
        # report.sh:174-185; test_install_progress_animation.py
        out = self.captured(report.auto_animate_common_target_progress_line, 0, 3, 3, "tween-test", delay=0)
        self.assertEqual(out.count("Common targets"), 3)
        for frame in ("[1/3]", "[2/3]", "[3/3]"):
            self.assertIn(frame, out)
        self.assertEqual(self.captured(report.auto_animate_common_target_progress_line, 3, 3, 3, "x", delay=0), "")
        clamped = self.captured(report.auto_animate_common_target_progress_line, 1, 9, 3, "x", delay=0)
        self.assertEqual(clamped.count("Common targets"), 2)
        self.assertIn("[3/3]", clamped)
        self.assertNotIn("[4/3]", clamped)
        self.assertTrue(clamped.startswith("\r\x1b[2K"))


class ReportTextTests(ReportCase):
    def test_print_start_tail_and_full(self):
        # report.sh:187-227; test_installer_default_auto.py:568-594
        start = self.captured(report.print_start, "codex", 5, "dynamic")
        self.assertTrue(start.startswith("Ghost-ALICE OS installation Process Report\n\nTarget\n  Platform: codex\n"
                                         "  Skills: [5] targets\n  Hooks: enabled\n  Visibility Level: [dynamic]\n\n"
                                         "Progress\n  [1/5] Preflight           ok\n"))
        self.assertTrue(start.endswith("  [2/5] Skill sync          [0] [Current], [0] [updated], [0] [newly added]"))
        log = report.state().log_file
        self.assertIsNotNone(log)
        tail = self.captured(report.print_tail, "codex", "dynamic")
        # Codex has no /visibility command, so a Codex-only tail without notes has no Attention section.
        self.assertEqual(tail,
            "  [3/5] Hooks               session-intent, tool-checkpoint, completion, session-start, session-receipt, io-trace enabled\n"
            "  [4/5] Runtime config      codex hooks=true, Visibility Level=[dynamic]\n"
            "  [5/5] Verification        ok\n\n"
            "Details\n"
            f"  log: {log}\n"
            "  rerun with --verbose to show per-skill actions\n")
        full = self.captured(report.print_full, "claude", 7, 1, 2, 4, "minimal")
        self.assertIn("  Skills: [7] targets\n", full)
        self.assertIn("  Visibility Level: [minimal]\n", full)
        self.assertIn("  [2/5] Skill sync          [1] [Current], [2] [updated], [4] [newly added]\n"
                      "  [3/5] Hooks               ", full)
        self.assertIn("  [4/5] Runtime config      claude hooks=true, Visibility Level=[minimal]\n", full)
        self.assertEqual(full.count("Ghost-ALICE OS installation Process Report"), 1)

    def test_tail_attention_takes_extra_notes(self):
        tail = self.captured(report.print_tail, "claude", "dynamic",
                             notes=["Context gateway: registered from /src, 32 files, service running"])
        self.assertIn("Attention\n"
                      "  - visibility can be changed later in Claude Code with /visibility between: dynamic | minimal | strict\n"
                      "  - Context gateway: registered from /src, 32 files, service running\n\n"
                      "Details\n", tail)
        full = self.captured(report.print_full, "claude", 7, 1, 2, 4, "minimal", notes=["one note"])
        self.assertIn("  - one note\n", full)
        auto = self.captured(report.print_auto_full, "claude, codex", 24, "dynamic", 24, notes=["one note"])
        self.assertIn("  - visibility can be changed later in Claude Code with /visibility", auto)
        self.assertIn("  - one note\n", auto)
        codex = self.captured(report.print_tail, "codex", "dynamic", notes=["one note"])
        self.assertIn("Attention\n  - one note\n\nDetails\n", codex)
        self.assertNotIn("/visibility", codex)

    def test_gateway_event_round_trip(self):
        event_file = self.home / "run.events.jsonl"
        report.configure(verbose=False, report_child=True, tty=False, progress_env="off", home=self.home,
                         event_file=event_file)
        report.write_gateway_event("codex", "Context gateway: registered from /src, 3 files, service running",
                                   running=True)
        report.write_gateway_event("claude", "Context gateway: registered from /src, 3 files, service running",
                                   running=True)
        lines = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines[0], {"type": "gateway-result", "platform": "codex",
                                    "summary": "Context gateway: registered from /src, 3 files, service running",
                                    "running": True})
        # The same observation from every host is one note.
        self.assertEqual(report.read_gateway_notes(event_file),
                         ["Context gateway: registered from /src, 3 files, service running"])
        self.assertEqual(report.read_gateway_notes(self.home / "absent.jsonl"), [])

    def test_print_auto_start_and_full(self):
        # report.sh:229-261; test_installer_default_auto.py:409-433, 476-479
        start = self.captured(report.print_auto_start, "claude, codex", 24, "dynamic")
        self.assertIn("  Platform: claude, codex\n  Skills: [24] common targets\n", start)
        self.assertIn("  [2/5] Skill sync          [24] common targets\n", start)
        self.assertTrue(start.endswith("        Common targets      [------------------------------] [0/24] common targets synced"))
        full = self.captured(report.print_auto_full, "claude, codex", 24, "dynamic", 24)
        self.assertIn("        Common targets      [##############################] [24/24] common targets synced on all platforms\n"
                      "  [3/5] Hooks               ", full)
        self.assertIn("  [4/5] Runtime config      claude, codex hooks=true, Visibility Level=[dynamic]\n", full)


class EventTests(ReportCase):
    def test_events_written_only_when_an_event_file_is_set(self):
        # report.sh:263-275
        report.write_event("claude", 5, 1, 2, 2)
        report.write_target_event("claude", "x", "skill", "new")
        self.assertEqual(list((self.home).glob("**/*.jsonl")), [])
        event_file = self.home / "run" / "install.events.jsonl"
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_EVENT_FILE": str(event_file)}):
            report.configure(verbose=False, report_child=True, tty=False, progress_env="auto", home=self.home)
        report.write_event("claude", 5, 1, 2, 2)
        report.write_target_event("codex", "task-router", "skill", "updated")
        lines = event_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], '{"type":"platform-result","platform":"claude","total_targets":5,"current":1,'
                                   '"updated":2,"new":2,"verification":"ok","hooks":"enabled"}')
        self.assertEqual(lines[1], '{"type":"target-result","platform":"codex","target_id":"task-router",'
                                   '"target_kind":"skill","status":"updated"}')
        self.assertEqual(json.loads(lines[0])["type"], "platform-result")

    def test_progress_readers(self):
        # report.sh:277-305 wrap install_report_events; 0 for a missing or empty file
        event_file = self.home / "events.jsonl"
        self.assertEqual(report.read_all_common_target_progress(event_file, 2), 0)
        self.assertEqual(report.read_weighted_common_target_progress(event_file, 2, 24), 0)
        event_file.write_text("", encoding="utf-8")
        self.assertEqual(report.read_all_common_target_progress(event_file, 2), 0)
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_EVENT_FILE": str(event_file)}):
            report.configure(verbose=False, report_child=True, tty=False, progress_env="auto", home=self.home)
        for platform in ("claude", "codex"):
            report.write_target_event(platform, "_shared", "support", "new")
            report.write_target_event(platform, "task-router", "skill", "new")
        report.write_target_event("claude", "only-claude", "skill", "new")
        self.assertEqual(report.read_all_common_target_progress(event_file, 2), 2)
        self.assertEqual(report.read_weighted_common_target_progress(event_file, 2, 24), 2)
        self.assertEqual(report.read_weighted_common_target_progress(event_file, 1, 24), 5)
        self.assertEqual(report.read_weighted_common_target_progress(event_file, 2, 1), 1)


if __name__ == "__main__":
    unittest.main()
