"""Every file a Core installation creates belongs to an entry of the retention table (`state_retention`).

A copy of this checkout installs into an empty HOME, every registered hook command runs once, the context gateway
forwards a request, and the full uninstall runs. After the activity and after the uninstall, every file, link and
empty folder under the Ghost-ALICE state root and the host folders must match an entry of
`state_retention.POLICIES`; a new writer passes only after its rule is in the table.

Dependencies: Python 3.11+, bash, Node.js (the installed hook runner), git.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_gateway  # noqa: E402
import state_retention  # noqa: E402
from installer_testing import checkout_copy  # noqa: E402

HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")


def _payload(event: str, session: str, cwd: Path, transcript: Path) -> dict:
    payload = {"session_id": session, "hook_event_name": event, "cwd": str(cwd), "transcript_path": str(transcript)}
    if event == "SessionStart":
        payload["source"] = "startup"
    elif event == "UserPromptSubmit":
        payload["prompt"] = "Summarize the README."
    elif event in ("PreToolUse", "PostToolUse"):
        # A destructive command also makes the governance capture write its event log.
        payload.update(tool_name="Bash", tool_input={"command": "rm -rf build && cat README.md"},
                       tool_response={"stdout": ""})
    elif event == "Stop":
        payload["stop_hook_active"] = False
    return payload


@unittest.skipUnless(shutil.which("bash") and shutil.which("node") and shutil.which("git"),
                     "the installer needs bash, node and git")
class RetentionInvariantTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        for host in (".claude", ".codex"):
            (self.home / host).mkdir(parents=True)
        (self.root / "tmp").mkdir()
        self.checkout = checkout_copy(self)
        self.env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8",
                    "TMPDIR": str(self.root / "tmp")}
        for key in ("SYSTEMROOT", "PYTHONUSERBASE"):
            if key in os.environ:
                self.env[key] = os.environ[key]
        # The in-process gateway of forward_one_request writes where a registration beside this home would; a
        # real registration would make the uninstall stop the account's gateway service.
        service = {"state_path": str(self.home / ".ghost-alice" / "context-gateway" / "state.json"),
                   "launch_agent": str(self.home / "Library" / "LaunchAgents" / "org.ghost-alice.context-gateway.plist")}
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=True):
            self.roots = state_retention.default_roots(service=service)

    def run_installer(self, *args: str) -> None:
        result = subprocess.run(["bash", "install.sh", *args], cwd=self.checkout, env=self.env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=600)
        self.assertEqual(result.returncode, 0, msg=result.stdout[-4000:] + result.stderr[-4000:])

    def run_hooks(self) -> None:
        transcript = self.root / "transcript.jsonl"
        transcript.write_text("", encoding="utf-8")
        session = str(uuid.uuid4())
        configs = {"claude": self.home / ".claude" / "settings.json", "codex": self.home / ".codex" / "hooks.json"}
        ran = 0
        for config in configs.values():
            for event, groups in json.loads(config.read_text(encoding="utf-8")).get("hooks", {}).items():
                if event not in HOOK_EVENTS:
                    continue
                for group in groups:
                    for hook in group.get("hooks", []):
                        subprocess.run(["/bin/sh", "-c", hook["command"]],
                                       input=json.dumps(_payload(event, session, self.checkout, transcript)),
                                       cwd=self.checkout, env=self.env, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=300)
                        ran += 1
        self.assertGreater(ran, 0, "no hook command was registered")

    def forward_one_request(self) -> None:
        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = b'data: {"type":"response.completed","response":{"id":"r","usage":{"input_tokens":1}}}\n\n'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        usage = self.roots["gateway"] / "usage.jsonl"
        usage.parent.mkdir(parents=True, exist_ok=True)
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        gateway = context_gateway.make_server("scope", usage,
                                              origins={"codex": f"http://127.0.0.1:{upstream.server_port}"})
        for server in (upstream, gateway):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{gateway.server_port}/codex/responses",
                                             data=b'{"model":"m","input":[]}',
                                             headers={"Content-Type": "application/json",
                                                      "X-Ghost-Context-Scope": "scope"})
            with urllib.request.urlopen(request, timeout=30) as reply:
                reply.read()
        finally:
            for server in (gateway, upstream):
                server.shutdown()
                server.server_close()

    def created(self) -> list[tuple[Path, bool]]:
        """Every file, link and empty folder under the state root and the host folders; links are not followed."""
        found = []
        for top in self.roots.values():
            if not top.is_dir():
                continue
            for folder, directories, files in os.walk(top):
                folder_path = Path(folder)
                for directory in list(directories):
                    if (folder_path / directory).is_symlink():
                        directories.remove(directory)
                        files.append(directory)
                for file in files:
                    found.append((folder_path / file, False))
                if folder_path != top and not directories and not files:
                    found.append((folder_path, True))
        return sorted(set(found))

    def assert_every_path_has_an_entry(self, moment: str) -> None:
        unmatched = [str(path.relative_to(self.home)) for path, is_dir in self.created()
                     if state_retention.entry_for(path, self.roots, directory=is_dir) is None]
        self.assertEqual(unmatched, [], f"{moment}: created paths without a retention entry")

    def test_every_created_path_matches_a_retention_entry(self):
        self.run_installer("--skip-source-health")
        self.run_hooks()
        self.forward_one_request()
        ghost = self.roots["ghost"]
        for expected in (ghost / "io-trace.jsonl", ghost / "governance-events.jsonl",
                         self.roots["gateway"] / "usage.jsonl"):
            self.assertTrue(expected.is_file(), f"the scenario wrote no {expected.name}")
        self.assertTrue(any((ghost / "session-logs").rglob("strict-hook-output.jsonl")))
        self.assertTrue(any((self.roots["record"] / "install").glob("*.log")))
        self.assert_every_path_has_an_entry("after install, hooks and a gateway request")

        self.run_installer("--uninstall")
        self.assertTrue(any((self.roots["record"] / "uninstall-reports").glob("*.json")))
        self.assert_every_path_has_an_entry("after the full uninstall")


if __name__ == "__main__":
    unittest.main()
