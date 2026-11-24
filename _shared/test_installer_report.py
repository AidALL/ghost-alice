"""installer.report: messages, verbosity, the install log, progress lines, the report and the event file.

Expected texts come from installer_lib/report.sh; the line numbers are cited next to each case.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
import tempfile
import time
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
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "GHOST_ALICE_INSTALL_LOCALE": "en_US.UTF-8"}, clear=False)
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
    def test_removal_lines_keep_setting_cause_and_the_actual_retained_location(self):
        item = dict(kind='source-repo-hook-config', path='/repository', action='manual-review',
                    reason='restore-failed', detail='config.lock exists', removal_incomplete=True)
        output = '\n'.join(text for _, text in report.removal_item_lines(item))
        self.assertIn('core.hooksPath', output)
        self.assertIn('/repository', output)
        self.assertIn('config.lock exists', output)
        for root, separator in ((PurePosixPath('/state'), '/'), (PureWindowsPath('C:/state'), '\\')):
            with self.subTest(root=root), mock.patch.object(report, 'Path', type(root)), \
                    mock.patch.object(report.os, 'sep', separator):
                source, backup = root / 'rollbacks', root / 'backup/original'
                original = f"{source / 'tx'}: pending"
                item = dict(kind='support-artifact', target_name='publish-rollbacks', path=source.as_posix(),
                            action='manual-review', reason='recovery-link-time-not-preserved',
                            backup_path=backup.as_posix(), retained_originals=[original])
                self.assertIn(('INFO', original), report.removal_item_lines(item))
                item['action'] = 'quarantined-removed'
                self.assertEqual(report.removal_item_lines(item), [('INFO', f"{backup / 'tx'}: pending")])

    def test_nested_exception_leaves_and_cleanup_note_keep_recovery_evidence(self):
        first = PermissionError('new receipt publish denied')
        second = OSError('legacy receipt restore denied')
        error = ExceptionGroup('receipt retained at /temporary/claude-v1-original.bak',
                               [ExceptionGroup('commit', [first]), second])
        error.add_note('Temporary receipt cleanup failed at /temporary/receipt')
        output = '\n'.join(report.exception_lines(error))
        for message in ('PermissionError: new receipt publish denied', 'OSError: legacy receipt restore denied',
                        '/temporary/claude-v1-original.bak', 'Temporary receipt cleanup failed'):
            self.assertIn(message, output)

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


class LocalizedMessageTests(ReportCase):
    def configure_locale(self, values):
        with mock.patch.dict(os.environ, values, clear=True):
            report.configure(tty=False, home=self.home)

    def test_locale_precedence_and_english_fallback_are_visible_in_output(self):
        cases = [
            ({"GHOST_ALICE_INSTALL_LOCALE": "ko_KR", "LC_ALL": "en_US.UTF-8"}, "ko"),
            ({"GHOST_ALICE_INSTALL_LOCALE": "", "LC_ALL": "ko_KR.UTF-8"}, "en"),
            ({"LC_ALL": "ko-KR", "LC_MESSAGES": "en_US", "LANG": "en_US"}, "ko"),
            ({"LC_MESSAGES": "ko_KR.UTF-8", "LANG": "en_US"}, "ko"),
            ({"LC_ALL": "", "LC_MESSAGES": "ko_KR.UTF-8", "LANG": "en_US"}, "ko"),
            ({"LANG": "ko"}, "ko"),
            ({"LANG": "koala"}, "en"),
            ({"LANG": "ja_JP.UTF-8"}, "en"),
            ({"LANG": "C.UTF-8"}, "en"),
        ]
        for values, language in cases:
            with self.subTest(values=values):
                self.configure_locale(values)
                expected = "Ghost-ALICE 설치를 완료했습니다." if language == "ko" else "Ghost-ALICE installation is complete."
                self.assertEqual(self.captured(report.emit, report.message("install_complete")), expected + "\n")
                self.assertEqual(report.state().language, language)

    def test_configured_language_does_not_change_with_environment(self):
        self.configure_locale({"GHOST_ALICE_INSTALL_LOCALE": "ko_KR"})
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_LOCALE": "en_US"}):
            self.assertEqual(report.message("update_complete"), "Ghost-ALICE 업데이트를 완료했습니다.")
        self.configure_locale({"LANG": "en_US"})
        self.assertEqual(report.message("update_complete"), "Ghost-ALICE update is complete.")

    def test_windows_display_language_is_only_used_without_locale(self):
        native = mock.Mock()
        native.kernel32.GetUserDefaultUILanguage.return_value = 0x0412
        with mock.patch.object(sys, "platform", "win32"), mock.patch("ctypes.windll", native, create=True):
            self.configure_locale({})
            self.assertEqual(report.message("install_complete"), "Ghost-ALICE 설치를 완료했습니다.")
            native.kernel32.GetUserDefaultUILanguage.assert_called_once_with()
            native.kernel32.GetUserDefaultUILanguage.reset_mock()
            self.configure_locale({"GHOST_ALICE_INSTALL_LOCALE": ""})
            self.assertEqual(report.message("install_complete"), "Ghost-ALICE installation is complete.")
            native.kernel32.GetUserDefaultUILanguage.assert_not_called()
            native.kernel32.GetUserDefaultUILanguage.side_effect = OSError("unavailable")
            self.configure_locale({})
            self.assertEqual(report.message("install_complete"), "Ghost-ALICE installation is complete.")

    def test_absent_locale_on_non_windows_uses_english(self):
        with mock.patch.object(sys, "platform", "darwin"):
            self.configure_locale({})
        self.assertEqual(report.message("install_complete"), "Ghost-ALICE installation is complete.")

    def test_move_question_shows_locations_and_choices_in_selected_language(self):
        fields = dict(source_path="/설치 원본", current_path="/설치 원본/작업 기록", new_path="/사용자/.ghost-alice/state")
        for language, intro, explanation, question, choices in (
            ("ko", "현재 Ghost-ALICE는 설치에 사용한 폴더 안에 작업 기록을 저장하고 있습니다.",
             "기존 작업 기록을 설치에 사용한 폴더와 분리된 위치에 보관하려고 합니다.\n기존 작업 기록을 옮기면 앞으로의 작업 기록도 새 위치에 저장합니다.",
             "기존 작업 기록을 새 위치로 옮길까요? [Y/n]", "Enter: 옮기기 · n: 지금은 옮기지 않기"),
            ("en", "Ghost-ALICE currently stores your work records inside the folder used for installation.",
             "This keeps your work records separate from the folder used for installation.\nAfter the move, Ghost-ALICE will also store new work records in the new location.",
             "Move your existing work records to the new location? [Y/n]", "Enter: move records · n: leave them where they are for now"),
        ):
            with self.subTest(language=language):
                self.configure_locale({"LANG": language})
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    for key, values in (("move_intro", {}), ("move_locations", fields), ("move_explanation", {}),
                                        ("move_question", {}), ("move_choices", {})):
                        report.emit(report.message(key, **values))
                text = output.getvalue()
                for part in (intro, explanation, question, choices, *fields.values()):
                    self.assertIn(part, text)
                self.assertNotIn("{source_path}", text)
                self.assertNotIn("ledger", text)

    def test_move_outcomes_preserve_evidence_and_command_tokens(self):
        values = dict(source_path="C:\\설치 원본", current_path="C:\\설치 원본\\기록", new_path="C:\\사용자\\새 기록",
                      entry_command='"C:\\사용자 폴더\\ghost-alice.cmd"', host_name="Claude Code", cause="[ERROR] source.root --verbose",
                      recovery_paths="/복구 기록", recovery="ghost-alice update", step="service-resume", paths="/보관 자료",
                      affected_paths="/설정 파일", phase="prepare", journal_path="/정리 저널")
        cases = (
            ("move_complete", ("current_path", "new_path"), "are now at", "옮겼습니다"),
            ("move_cancelled", ("current_path", "entry_command"), "will ask again", "다시 묻습니다"),
            ("move_deferred", ("current_path", "entry_command"), "in a terminal", "터미널에서"),
            ("move_host_running", ("host_name",), "is still running", "아직 실행 중"),
            ("move_host_unknown", ("host_name", "cause"), "could not check", "확인하지 못했습니다"),
            ("move_not_moved", ("current_path", "cause"), "were not moved", "옮기지 않았습니다"),
            ("move_restored", ("current_path", "cause"), "restored the previous state", "옮기기 전 상태로 되돌렸습니다"),
            ("move_recovery_incomplete", ("current_path", "new_path", "recovery_paths", "cause", "recovery", "affected_paths", "phase"), "could not finish restoring", "복원을 마치지 못했습니다"),
            ("move_committed_pending", ("current_path", "new_path", "step"), "is configured to use", "기록 위치도 이 경로로 설정했습니다"),
            ("move_cleanup_failed", ("current_path", "new_path", "cause"), "could not finish deleting", "모두 삭제하지 못했습니다"),
            ("move_source_removed", ("current_path", "new_path"), "deleted the old record folder", "삭제했습니다"),
            ("move_source_cleanup_failed", ("new_path", "cause"), "could not finish deleting", "삭제를 마치지 못했습니다"),
            ("record_source_journal_retained", ("current_path", "journal_path"), "cleanup journal", "정리 저널을"),
            ("retained_installation_artifacts", ("paths",), "retained these installation artifacts", "배치 보관 자료를 유지했습니다"),
            ("move_doctor", ("current_path", "entry_command"), "inside the folder", "폴더 안에 있습니다"),
            ("move_uninstall", ("current_path", "source_path"), "were not deleted", "지우지 않았습니다"),
            ("move_relation_unknown", ("current_path",), "could not establish its relation", "관계를 확인하지 못했습니다"),
            ("move_not_run", ("current_path", "cause"), "was not started", "시작하지 않았습니다"),
        )
        for language in ("en", "ko"):
            self.configure_locale({"LANG": language})
            for key, fields, english, korean in cases:
                with self.subTest(language=language, key=key):
                    output = self.captured(report.warn, report.message(key, **{name: values[name] for name in fields}))
                    self.assertTrue(output.startswith("[WARN] "))
                    self.assertIn(korean if language == "ko" else english, output)
                    for name in fields:
                        self.assertIn(values[name], output)
                    self.assertNotIn("{", output)
            cancelled = report.message("move_cancelled", current_path=values["current_path"], entry_command=values["entry_command"])
            self.assertNotIn(report.message("install_complete"), cancelled)
            self.assertIn(values["entry_command"] + " update", cancelled)

    def test_the_refusal_of_an_earlier_request_names_its_command_in_the_selected_language(self):
        command = '/saved package/core/install.sh'
        for language in ('en', 'ko'):
            with self.subTest(language=language), mock.patch.dict(os.environ, {'GHOST_ALICE_INSTALL_LOCALE': language}):
                report.configure(home=self.home, tty=False)
                output = self.captured(report.error, report.message('earlier_request', command=command))
                self.assertEqual(output.count(command), 1, output)
                # A request for another host or other skills needs its arguments again.
                self.assertIn('--platform', output)
                self.assertEqual(bool(re.search('[\uac00-\ud7a3]', output)), language == 'ko', output)

    def test_a_stopped_update_names_the_command_that_keeps_the_recorded_remote(self):
        expected = {
            'en': ('without --remote', 'Run the update again', 'If an error above names another command, run that command.'),
            'ko': ('--remote 없이', '갱신 명령을 다시 실행', '위 오류에 다른 명령이 안내되어 있으면 그 명령을 실행해 주세요.'),
        }
        for language, (command, repeated, condition) in expected.items():
            with self.subTest(language=language):
                self.configure_locale({'LANG': language})
                changed = report.message('update_origin_changed')
                self.assertIn(command, changed)
                self.assertNotIn(repeated, changed)
                # This message follows the error above it.
                self.assertIn(condition, report.message('install_unfinished'))

    def test_a_repair_that_cannot_restore_the_active_package_names_the_update(self):
        for language in ('en', 'ko'):
            with self.subTest(language=language):
                self.configure_locale({'LANG': language})
                text = report.message('active_version_mismatch', package='/package', update='/checkout/install.sh update')
                self.assertIn('/checkout/install.sh update', text)
                # The active installer's own message carries one whole sentence for the restoration.
                restore = report.message('restore_from_registered', repair='/checkout/install.sh --repair')
                self.assertIn('/checkout/install.sh --repair', restore)
                replacement = report.message('replacement_update', command='/package/install.sh update')
                text = report.message('active_core_lost', package='/package', file='/package/a', restore=restore,
                                      replacement=replacement)
                self.assertIn(restore + ' ', text)
                self.assertIn('/package/install.sh update', text)
                self.assertNotIn('{', text + report.message('restore_from_checkout'))
                # An installation of other files would add every skill and replace the release.
                self.assertNotIn('--local-source', text)

    def test_input_and_recovery_guidance_uses_the_selected_language(self):
        expected = {
            "en": {"move_invalid": "Press Enter, or type y, yes, n, or no", "move_wait_invalid": "Press Enter, or type n or no",
                   "recovery_retry": "installation preparation", "step_restart": "Restarting the context gateway",
                   "step_mark": "Saving the completion record"},
            "ko": {"move_invalid": "Enter를 누르거나 y, yes, n, no", "move_wait_invalid": "Enter를 누르거나 n 또는 no",
                   "recovery_retry": "설치 준비", "step_restart": "문맥 게이트웨이 다시 시작", "step_mark": "완료 기록 저장"},
        }
        for language, messages in expected.items():
            self.configure_locale({"LANG": language})
            for key, fragment in messages.items():
                with self.subTest(language=language, key=key):
                    self.assertIn(fragment, self.captured(report.emit, report.message(key)))

    def test_unknown_observations_do_not_prescribe_closing_an_unidentified_writer(self):
        for language in ('ko', 'en'):
            self.configure_locale({'LANG': language})
            for key in ('move_host_unknown', 'move_service_blocked'):
                output = report.message(key, host_name='claude', cause='ps unavailable')
                self.assertIn('\n' + ('진단 상세: ' if language == 'ko' else 'Diagnostic details: ') + 'ps unavailable', output)
                for wrong in ('닫은 뒤', '중지한 뒤', 'Close it', 'Stop the service'):
                    self.assertNotIn(wrong, output)

    def test_unknown_relation_states_only_the_shared_fact(self):
        for language in ('ko', 'en'):
            self.configure_locale({'LANG': language})
            output = report.message('move_relation_unknown', current_path='/records')
            self.assertNotIn('did not move', output)
            self.assertNotIn('옮기지 않았', output)

    def test_localized_messages_use_the_existing_log_sink(self):
        self.configure_locale({"LANG": "ko_KR"})
        path = "C:\\사용자 폴더\\{기록}"
        output = self.captured(report.run_logged, report.warn, report.message("move_not_moved", current_path=path,
                                                                           cause="[ERROR] --verbose source.root"))
        expected = (f"[WARN] 기존 작업 기록을 옮기지 않았습니다. 기록 위치는 그대로 {path}입니다.\n"
                    "[WARN] 진단 상세: [ERROR] --verbose source.root\n")
        self.assertEqual(output, expected)
        self.assertEqual(report.state().log_file.read_text(encoding="utf-8"), expected)
        self.assertIsNone(report.sink())


class LogTests(ReportCase):
    def test_gateway_notes_skip_integer_parser_failure_and_keep_later_note(self):
        event_file = self.home / "events.jsonl"
        event_file.write_text('{"value":' + "9" * 5000 + '}\n'
                              '{"type":"gateway-result","summary":"later-valid"}\n')
        previous = sys.get_int_max_str_digits()
        try:
            sys.set_int_max_str_digits(4300)
            self.assertEqual(report.read_install_results(event_file), ({}, ["later-valid"], {}))
        finally:
            sys.set_int_max_str_digits(previous)

    def test_log_init_retains_thirty_legacy_and_unique_parent_runs_with_their_events(self):
        root = self.home / ".ghost-alice" / "install"
        root.mkdir(parents=True)
        runs = ["2026-10-01-010203"] + [f"2026-11-01-010203-{number:032x}" for number in range(32)]
        for run in runs:
            (root / f"{run}.log").write_text("log\n")
            (root / f"{run}.events.jsonl").write_text("{}\n")
        path = report.log_init()
        self.assertEqual(sorted(path.name for path in root.iterdir()),
                         sorted(f"{run}.{suffix}" for run in runs[-30:] for suffix in ("log", "events.jsonl")))
        self.assertFalse(path.exists())

    def test_event_readers_wait_for_lf_and_keep_committed_unicode_strings(self):
        event_file = self.home / "events.jsonl"
        complete = json.dumps({"type": "target-result", "platform": "claude",
                               "target_id": "a\u2028b", "target_kind": "skill"}, ensure_ascii=False)
        unfinished = json.dumps({"type": "target-result", "platform": "codex",
                                 "target_id": "a\u2028b", "target_kind": "skill"}, ensure_ascii=False)
        event_file.write_text(complete + "\n" + unfinished)
        self.assertEqual(report.read_all_common_target_progress(event_file, 2), 0)
        self.assertEqual(report.read_weighted_common_target_progress(event_file, 1, 5), 1)
        with event_file.open("a") as stream:
            stream.write("\n")
        self.assertEqual(report.read_all_common_target_progress(event_file, 2), 1)
        event_file.write_bytes((complete + "\n").encode() + b'{"summary":"\xe2')
        self.assertEqual(report.read_weighted_common_target_progress(event_file, 1, 5), 1)
        event_file.write_text(complete + "\ninvalid\n")
        with self.assertRaises(ValueError):
            report.read_all_common_target_progress(event_file, 2)
        with self.assertRaises(ValueError):
            report.read_weighted_common_target_progress(event_file, 1, 5)

    def test_gateway_notes_ignore_incomplete_tail_but_keep_valid_records_after_bad_lines(self):
        event_file = self.home / "events.jsonl"
        note = json.dumps({"type": "gateway-result", "summary": "one\u2028two"}, ensure_ascii=False)
        unfinished = json.dumps({"type": "gateway-result", "summary": "pending"})
        event_file.write_text("invalid\n" + note + "\n" + unfinished)
        self.assertEqual(report.read_install_results(event_file), ({}, ["one\u2028two"], {}))
        with event_file.open("a") as stream:
            stream.write("\n")
        self.assertEqual(report.read_install_results(event_file), ({}, ["one\u2028two", "pending"], {}))
        event_file.write_bytes((note + "\n").encode() + b'{"summary":"\xe2')
        self.assertEqual(report.read_install_results(event_file), ({}, ["one\u2028two"], {}))
        event_file.write_bytes((note + "\n").encode() + b'{"summary":"\xe2\n')
        with self.assertRaises(UnicodeDecodeError):
            report.read_install_results(event_file)

    def test_log_init_creates_the_install_log_path_once(self):
        # report.sh:78-89
        path = report.log_init()
        self.assertEqual(path.parent, self.home / ".ghost-alice" / "install")
        self.assertTrue(path.parent.is_dir())
        self.assertRegex(path.name, r"^\d{4}-\d{2}-\d{2}-\d{6}-[0-9a-f]{32}\.log$")
        self.assertEqual(os.environ.get("GHOST_ALICE_INSTALL_LOG_FILE"), str(path))
        self.assertEqual(report.log_init(), path)

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

    def test_explicit_install_request_log_is_shared_without_changing_report_presentation(self):
        original = report.log_init()
        report.configure(report_child=False, log_file=original, home=self.home)
        self.assertEqual(report.log_init(), original)
        self.assertTrue(report.report_enabled())
        self.assertEqual(report.state().event_file, original.with_suffix('.events.jsonl'))

    def test_the_result_is_the_last_event_and_the_details_stay(self):
        digest = 'a' * 64
        version = self.home / '.ghost-alice/versions' / digest
        version.mkdir(parents=True)
        (version / 'version.json').write_text('{}')
        report.run_logged(report.info, 'actual detail')
        log = report.state().log_file
        report.write_event('claude', 1, 0, 0, 1, verification='ok',
                           hook_status=mock.Mock(legacy_status='installed', details={}))
        report.finish_install(digest, 0)
        self.assertIn('actual detail', log.read_text())
        self.assertEqual(json.loads(log.with_suffix('.events.jsonl').read_text().splitlines()[-1]),
                         dict(type='install-result', source_digest=digest, returncode=0))

    def test_finishing_an_older_failure_keeps_both_current_files_and_the_previous_success(self):
        digest = 'a' * 64
        version = self.home / '.ghost-alice/versions' / digest
        version.mkdir(parents=True)
        (version / 'version.json').write_text('{}')
        report.run_logged(report.info, 'current failure details')
        current = report.state().log_file
        report.write_event('claude', 0, 0, 1, 0, verification='failed',
                           hook_status=mock.Mock(legacy_status='installed', details={}))
        previous = current.parent / '2026-01-01-000000.log'
        previous.write_text('previous successful details\n')
        previous_event = previous.with_suffix('.events.jsonl')
        previous_event.write_text(json.dumps(dict(type='install-result', source_digest=digest, returncode=0)) + '\n')
        saved_success = {path: path.read_bytes() for path in (previous, previous_event)}
        failures = []
        # Earlier runs can carry later timestamps after a wall-clock correction.
        future = time.time() + 3600
        for number in range(31):
            log = current.parent / f'2026-02-01-000000-{number:032x}.log'
            log.write_text(f'prior failure {number}\n')
            event = log.with_suffix('.events.jsonl')
            event.write_text(json.dumps(dict(type='install-result', source_digest=digest, returncode=1)) + '\n')
            for path in (log, event):
                os.utime(path, (future + number, future + number))
            failures.append((log, event))

        report.finish_install(digest, 1)

        self.assertIn('current failure details', current.read_text())
        self.assertEqual(json.loads(current.with_suffix('.events.jsonl').read_text().splitlines()[-1]),
                         dict(type='install-result', source_digest=digest, returncode=1))
        for path, content in saved_success.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(set(current.parent.iterdir()),
                         {current, current.with_suffix('.events.jsonl'), previous, previous_event,
                          *(path for pair in failures[1:] for path in pair)})

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
            return runner.run_tool("_shared/install_transaction.py", "--help")

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

    def test_hook_suite_label(self):
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
        self.assertTrue(self.captured(report.auto_update_common_target_progress_line, 26, 26,
                                      "common targets synced on all platforms").endswith(
                                          "[26/26] common targets synced on all platforms"))


class ReportTextTests(ReportCase):
    def test_print_start_tail_and_full(self):
        # report.sh:187-227; test_installer_default_auto.py:568-594
        start = self.captured(report.print_start, "codex", 5, "dynamic")
        self.assertTrue(start.startswith("Ghost-ALICE OS installation Process Report\n\nTarget\n  Platform: codex\n"
                                         "  Skills: [5] targets\n  Hooks: registration requested\n  Visibility Level: [dynamic]\n\n"
                                         "Progress\n  [1/5] Preflight           ok\n"))
        self.assertTrue(start.endswith("  [2/5] Skill sync          [0] [Current], [0] [updated], [0] [newly added]"))
        log = report.state().log_file
        self.assertIsNotNone(log)
        tail = self.captured(report.print_tail, "codex", "dynamic", hook_statuses={'codex': 'installed'})
        # Codex has no /visibility command, so a Codex-only tail without notes has no Attention section.
        self.assertEqual(tail,
            "  [3/5] codex Hooks: installed\n"
            "        session-intent, tool-checkpoint, completion, session-start, session-receipt, io-trace registered\n"
            "  [4/5] Runtime config      Visibility Level=[dynamic]\n"
            "  [5/5] Verification        ok\n\n"
            "Details\n"
            f"  log: {log}\n"
            "  rerun with --verbose to show per-skill actions\n")
        full = self.captured(report.print_full, "claude", 7, 1, 2, 4, "minimal")
        self.assertIn("  Skills: [7] targets\n", full)
        self.assertIn("  Visibility Level: [minimal]\n", full)
        self.assertIn("  [2/5] Skill sync          [1] [Current], [2] [updated], [4] [newly added]\n"
                      "  [3/5] claude Hooks: unknown", full)
        self.assertIn("  [4/5] Runtime config      Visibility Level=[minimal]\n", full)
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
        self.assertEqual(report.read_install_results(event_file),
                         ({}, ["Context gateway: registered from /src, 3 files, service running"], {}))
        self.assertEqual(report.read_install_results(self.home / "absent.jsonl"), ({}, [], {}))

    def test_print_auto_start_and_full(self):
        # report.sh:229-261; test_installer_default_auto.py:409-433, 476-479
        start = self.captured(report.print_auto_start, "claude, codex", 24, "dynamic")
        self.assertIn("  Platform: claude, codex\n  Skills: [24] common targets\n", start)
        self.assertIn("  [2/5] Skill sync          [24] common targets\n", start)
        self.assertTrue(start.endswith("        Common targets      [------------------------------] [0/24] common targets synced"))
        full = self.captured(report.print_auto_full, "claude, codex", 24, "dynamic", 24)
        self.assertIn("        Common targets      [##############################] [24/24] common targets synced on all platforms\n"
                      "  [3/5] claude Hooks: unknown", full)
        self.assertIn("  [4/5] Runtime config      Visibility Level=[dynamic]\n", full)


class EventTests(ReportCase):
    def test_events_written_only_when_an_event_file_is_set(self):
        # report.sh:263-275
        report.write_event("claude", 5, 1, 2, 2, verification="ok")
        report.write_target_event("claude", "x", "skill", "new")
        self.assertEqual(list((self.home).glob("**/*.jsonl")), [])
        event_file = self.home / "run" / "install.events.jsonl"
        with mock.patch.dict(os.environ, {"GHOST_ALICE_INSTALL_EVENT_FILE": str(event_file)}):
            report.configure(verbose=False, report_child=True, tty=False, progress_env="auto", home=self.home)
        report.write_event("claude", 5, 1, 2, 2, verification="ok")
        report.write_target_event("codex", "task-router", "skill", "updated")
        lines = event_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0]), dict(type='platform-result', platform='claude', total_targets=5,
                         current=1, updated=2, new=2, verification='ok', hooks='unknown', hook_notes=[], hook_errors=[]))
        self.assertEqual(lines[1], '{"type":"target-result","platform":"codex","target_id":"task-router",'
                                   '"target_kind":"skill","status":"updated"}')
        from install_hooks import HookStatus
        hook_status = HookStatus('claude', 'HOOK_INSTALLED', 'installed', 'installed',
                                 {'items': [{'action': 'manual-review'}]})
        notes = report.write_event('claude', 5, 1, 2, 2, verification='ok', hook_status=hook_status)
        self.assertEqual(notes, ['partial: permissions retained for review'])
        self.assertEqual(report.read_install_results(event_file), ({'claude': 'installed'}, notes, {'claude': []}))

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
