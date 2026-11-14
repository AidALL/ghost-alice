"""Hook stdin decoding and consumer-specific failure contracts."""

import base64
import hashlib
import io
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_stop_verification_hook as stop
import hook_input
import hook_profile_gate as runner
import io_trace_hook as trace
import pending_merge_precheck_hook as pending
import session_intent_analyzer_hook as session


def _runner_payload():
    return runner._payload_from_stdin(runner._read_stdin())


PAYLOAD_READERS = (
    _runner_payload,
    session.read_payload,
    stop._read_hook_input,
    pending._read_hook_input,
)


class _BrokenInput:
    def __init__(self, error, *, buffered):
        self.error = error
        self.buffer = self if buffered else None

    def isatty(self):
        return False

    def read(self):
        raise self.error


class _TerminalInput:
    def __init__(self):
        self.buffer = self
        self.read_attempted = False

    def isatty(self):
        return True

    def read(self):
        self.read_attempted = True
        raise AssertionError("hook must not wait for terminal input")


class HookInputTest(unittest.TestCase):
    def test_decoder_limit_failures_preserve_the_cause_as_a_json_error(self):
        for raw, error in (('[' * 5000 + '0' + ']' * 5000, RecursionError),
                           ('{"number":' + '1' * 5000 + '}', ValueError)):
            with self.subTest(error=error.__name__):
                try:
                    decoded = json.loads(raw)
                except error:
                    pass
                else:
                    # A decoder that accepts this input needs no application limit.
                    self.assertEqual(hook_input.parse_json_object(raw), decoded if isinstance(decoded, dict) else {})
                    continue
                with self.assertRaises(json.JSONDecodeError) as caught:
                    hook_input.parse_json_object(raw)
                self.assertIsInstance(caught.exception.__cause__, error)
                self.assertEqual(caught.exception.doc, raw)
                self.assertEqual(caught.exception.pos, 0)

    def test_pending_merge_reads_utf8_bytes_behind_cp949_wrapper(self):
        payload = {"session_id": "세션", "transcript_path": "C:/작업/기록.jsonl"}
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with io.TextIOWrapper(io.BytesIO(raw), encoding="cp949", errors="surrogateescape") as stream:
            with mock.patch.object(sys, "stdin", stream):
                self.assertEqual(pending._read_hook_input(), payload)

    def test_payload_consumers_decode_utf8_and_replace_invalid_bytes(self):
        for read in PAYLOAD_READERS:
            for raw, expected in (
                ('{"prompt":"상태 확인"}'.encode("utf-8"), {"prompt": "상태 확인"}),
                (b'{"prompt":"bad\xffbyte"}', {"prompt": "bad\ufffdbyte"}),
            ):
                with self.subTest(reader=read.__module__, raw=raw):
                    with io.TextIOWrapper(io.BytesIO(raw), encoding="cp949", errors="surrogateescape") as stream:
                        with mock.patch.object(sys, "stdin", stream):
                            self.assertEqual(read(), expected)

    def test_payload_consumers_use_empty_object_for_empty_malformed_and_non_object_json(self):
        for read in PAYLOAD_READERS:
            for raw in ("", " \n\t", "{broken", "[]", '"text"', "null", "0", '[' * 5000 + '0' + ']' * 5000,
                        '{"number":' + '1' * 5000 + '}'):
                with self.subTest(reader=read.__module__, raw=raw):
                    with mock.patch.object(sys, "stdin", io.StringIO(raw)):
                        self.assertEqual(read(), {})

    def test_consumers_do_not_read_from_a_terminal(self):
        for read in (*PAYLOAD_READERS, trace._read_stdin_utf8):
            stream = _TerminalInput()
            with self.subTest(reader=read.__module__):
                with mock.patch.object(sys, "stdin", stream):
                    self.assertIn(read(), ({}, ""))
                self.assertFalse(stream.read_attempted)

    def test_payload_consumers_keep_their_stream_error_contracts(self):
        for read in PAYLOAD_READERS:
            for buffered in (False, True):
                with self.subTest(reader=read.__module__, buffered=buffered):
                    with mock.patch.object(sys, "stdin", _BrokenInput(OSError("closed"), buffered=buffered)):
                        self.assertEqual(read(), {})
                    with mock.patch.object(sys, "stdin", _BrokenInput(ValueError("closed"), buffered=buffered)):
                        if read in (stop._read_hook_input, pending._read_hook_input):
                            self.assertEqual(read(), {})
                        else:
                            with self.assertRaises(ValueError):
                                read()

    def test_trace_skips_malformed_json_but_logs_empty_and_non_object_input(self):
        for raw, appended in (("{broken", False), ('{"number":' + '1' * 5000 + '}', False),
                              ("", True), (" \n", True), ("[]", True), ("null", True)):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                with mock.patch.dict(os.environ, {"HOME": directory}, clear=True), \
                     mock.patch.object(sys, "stdin", io.StringIO(raw)):
                    self.assertEqual(trace.main(), 0)
                path = Path(directory) / ".ghost-alice/io-trace.jsonl"
                self.assertEqual(path.exists(), appended)
                if appended:
                    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                    self.assertEqual(len(rows), 1)
                    self.assertEqual((rows[0]["platform"], rows[0]["session"], rows[0]["tool"]),
                                     ("unknown", "unknown", "unknown"))


    def test_trace_logs_utf8_path_from_cp949_wrapper(self):
        payload = {"platform": "codex", "session_id": "utf8", "tool_name": "Read",
                   "tool_input": {"file_path": "C:/작업/계획.md"}}
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            with io.TextIOWrapper(io.BytesIO(raw), encoding="cp949", errors="surrogateescape") as stream:
                with mock.patch.dict(os.environ, {"HOME": directory}, clear=True), \
                     mock.patch.object(sys, "stdin", stream):
                    self.assertEqual(trace.main(), 0)
            row = json.loads((Path(directory) / ".ghost-alice/io-trace.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(row["path"], payload["tool_input"]["file_path"])
        self.assertEqual((row["platform"], row["session"], row["tool"]), ("codex", "utf8", "Read"))

    def test_runner_passes_exact_raw_utf8_to_child_and_audit_digest(self):
        code = "import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
        encoded = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
        for raw in ('{\n  "prompt": "상태 확인", "extra": 1\n}\n', '{"prompt": "상태 확인"', '["상태 확인"]\n',
                    '[' * 5000 + '0' + ']' * 5000, '{"number":' + '1' * 5000 + '}'):
            with self.subTest(raw_length=len(raw)), tempfile.TemporaryDirectory() as directory:
                environment = dict(HOME=directory, PATH=os.environ.get('PATH', ''), GHOST_ALICE_PLATFORM="codex",
                                   GHOST_ALICE_SESSION_ID="raw-input", GHOST_ALICE_AGENT_VISIBILITY="strict")
                with io.TextIOWrapper(io.BytesIO(raw.encode("utf-8")), encoding="cp949", errors="surrogateescape") as stream:
                    with mock.patch.dict(os.environ, environment, clear=True), \
                         mock.patch.object(sys, "stdin", stream), \
                         mock.patch.object(sys, "stdout", io.StringIO()):
                        self.assertEqual(runner.run("prompt", encoded), 0)
                log = Path(directory) / ".ghost-alice/session-logs/codex/raw-input/strict-hook-output.jsonl"
                row = json.loads(log.read_text(encoding="utf-8"))
                digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                self.assertEqual(row["stdout"], digest + "\n")
                self.assertEqual(row["payload_digest"], "sha256:" + digest)


if __name__ == "__main__":
    unittest.main()
