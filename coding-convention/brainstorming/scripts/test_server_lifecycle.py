"""Real companion processes, listeners and browser sockets; requires: Node and Bash."""

import base64
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit


SCRIPTS = Path(__file__).resolve().parent


def isolated_env():
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def wait_for(check, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition did not become true before timeout")


@unittest.skipUnless(os.name == "posix" and shutil.which("node") and shutil.which("bash"),
                     "POSIX process groups, Node and Bash required")
class CompanionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="companion-tests-")
        self.root = Path(self.temp.name)
        self.processes = []
        self.logs = []
        self.sockets = []
        self.env = isolated_env()
        for name in ("home", "tmp"):
            (self.root / name).mkdir()
        self.env.update(HOME=str(self.root / "home"), USERPROFILE=str(self.root / "home"),
                        TMPDIR=str(self.root / "tmp"), TMP=str(self.root / "tmp"),
                        TEMP=str(self.root / "tmp"))

    def tearDown(self):
        for connection in self.sockets:
            connection.close()
        # Every group was created by this test. Never discover or signal host processes.
        for process in self.processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=4)
        for log in self.logs:
            log.close()
        self.temp.cleanup()

    def spawn(self, args, env=None):
        log = (self.root / f"child-{len(self.processes)}.log").open("w+")
        self.logs.append(log)
        process = subprocess.Popen(args, cwd=self.root, env=env or self.env,
                                   stdout=log, stderr=log, start_new_session=True)
        self.processes.append(process)
        return process

    def session_info(self):
        def read_info():
            files = list(self.root.rglob("server-info"))
            if not files:
                return None
            try:
                return json.loads(files[0].read_text())
            except json.JSONDecodeError:
                return None
        return wait_for(read_info)

    def connect(self, info, websocket=False):
        connection = socket.create_connection((info["host"], info["port"]), timeout=2)
        self.sockets.append(connection)
        request = b"GET / HTTP/1.1\r\nHost: localhost\r\n"
        if websocket:
            request += (b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                        b"Sec-WebSocket-Version: 13\r\n")
        else:
            request += b"Connection: close\r\n"
        connection.sendall(request + b"\r\n")
        response = connection.recv(4096)
        self.assertIn(b"101 Switching Protocols" if websocket else b"200 OK", response)
        return connection

    def assert_listener_closed(self, info):
        with self.assertRaises(OSError):
            socket.create_connection((info["host"], info["port"]), timeout=0.2)

    def stop(self, session):
        result = subprocess.run(["bash", str(SCRIPTS / "stop-server.sh"), str(session)],
                                env=self.env, cwd=self.root, capture_output=True,
                                text=True, timeout=6)
        print(json.dumps({"stop_exit": result.returncode, "stdout": result.stdout.strip(),
                          "stderr": result.stderr.strip()}), flush=True)
        return result

    def control_request(self, info, headers):
        connection = http.client.HTTPConnection(info["host"], info["port"], timeout=2)
        try:
            connection.request("POST", "/_brainstorm/stop", headers=headers)
            response = connection.getresponse()
            body = response.read()
            return response.status, body
        finally:
            connection.close()

    def control_headers(self, control):
        return {"X-Brainstorm-Token": control["token"],
                "X-Brainstorm-Session": base64.urlsafe_b64encode(
                    control["session_dir"].encode()).decode().rstrip("=")}

    def test_valued_options_reject_missing_and_next_flag_before_creating_session(self):
        for option in ("--project-dir", "--host", "--url-host"):
            for value in ([], ["--foreground"]):
                with self.subTest(option=option, value=value):
                    case_root = self.root / f"case-{len(self.processes)}"
                    case_root.mkdir()
                    process = self.spawn(["bash", str(SCRIPTS / "start-server.sh"),
                                          "--project-dir", str(case_root), option, *value])
                    try:
                        result = process.wait(timeout=0.6)
                    except subprocess.TimeoutExpired:
                        self.fail(f"{option} {value}: parser did not terminate")
                    self.assertNotEqual(result, 0)
                    self.assertEqual(list(case_root.iterdir()), [], "invalid option created session files")
                    self.logs[-1].seek(0)
                    response = json.loads(self.logs[-1].read())
                    self.assertIn(option, response["error"])

    def check_foreground_stop_preserves_mockups(self):
        process = self.spawn(["bash", str(SCRIPTS / "start-server.sh"),
                              "--project-dir", str(self.root), "--foreground"])
        info = self.session_info()
        session = Path(info["state_dir"]).parent
        pid = int((session / "state/server.pid").read_text())
        command = subprocess.check_output(["ps", "-p", str(pid), "-o", "comm="], text=True).strip()
        self.assertEqual(pid, process.pid)
        self.assertIn("node", Path(command).name.lower(), f"PID marker points at {command}")
        self.connect(info).close()
        mockup = session / "content/mockup.html"
        mockup.write_text("<h1>Preserve me</h1>")
        # The real host reaps its foreground child while stop runs separately.
        reaper = threading.Thread(target=process.wait)
        reaper.start()
        result = self.stop(session)
        reaper.join(timeout=2)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "stopped")
        self.assertEqual(process.wait(timeout=2), 0)
        self.assert_listener_closed(info)
        self.assertEqual(mockup.read_text(), "<h1>Preserve me</h1>")

    def test_foreground_marker_identifies_node_and_stop_preserves_mockups(self):
        self.check_foreground_stop_preserves_mockups()

    def test_background_marker_identifies_node_and_stop_closes_listener(self):
        launcher = self.spawn(["bash", str(SCRIPTS / "start-server.sh"),
                               "--project-dir", str(self.root), "--background"])
        info = self.session_info()
        self.assertEqual(launcher.wait(timeout=4), 0)
        session = Path(info["state_dir"]).parent
        pid = int((session / "state/server.pid").read_text())
        command = subprocess.check_output(["ps", "-p", str(pid), "-o", "comm="], text=True).strip()
        self.assertIn("node", Path(command).name.lower())
        self.connect(info).close()
        result = self.stop(session)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "stopped")
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assert_listener_closed(info)

    def test_selected_project_paths_own_session_files_in_both_modes(self):
        for mode in ("--foreground", "--background"):
            for project_arg in ("relative-project", "relative project",
                                str(self.root / f"absolute project {mode[2:]}")):
                with self.subTest(mode=mode, project=project_arg):
                    # A broken relative-path handoff may write beside the launcher.
                    # Keep that real production behavior inside the test fixture.
                    scripts = self.root / f"scripts-{len(self.processes)}"
                    shutil.copytree(SCRIPTS, scripts,
                                    ignore=shutil.ignore_patterns("__pycache__"))
                    original_paths = {path.relative_to(scripts) for path in scripts.rglob("*")}
                    original_files = {path: (scripts / path).read_bytes() for path in original_paths
                                      if (scripts / path).is_file()}
                    cdpath = self.root / "other-projects"
                    (cdpath / "relative-project").mkdir(parents=True, exist_ok=True)
                    (cdpath / "relative project").mkdir(parents=True, exist_ok=True)
                    process = self.spawn(["bash", str(scripts / "start-server.sh"),
                                          "--project-dir", project_arg, mode],
                                         {**self.env, "CDPATH": str(cdpath)})
                    info = None
                    try:
                        if mode == "--background":
                            self.assertEqual(process.wait(timeout=7), 0)
                        info = self.session_info()
                        state = Path(info["state_dir"])
                        project = (self.root / project_arg).resolve()
                        self.assertTrue(state.is_absolute(), info["state_dir"])
                        self.assertEqual(state.parent.parent,
                                         project / ".coding-convention/brainstorm")
                        screen = Path(info["screen_dir"])
                        self.assertEqual(screen, state.parent / "content")
                        self.assertTrue(screen.is_dir())
                        control = json.loads((state / "server-control").read_text())
                        self.assertEqual(Path(control["session_dir"]), state.parent)
                        self.assertEqual(control["pid"], int((state / "server.pid").read_text()))
                        mockup = screen / "selected-project.html"
                        mockup.write_text("<h1>Selected project</h1>")
                        self.connect(info).close()
                        if mode == "--background":
                            events = [json.loads(line) for line in (state / "server.log").read_text().splitlines()]
                            started = next(event for event in events if event.get("type") == "server-started")
                            self.assertEqual(started["state_dir"], str(state))
                            self.assertEqual(started["screen_dir"], str(screen))
                    finally:
                        if info is not None:
                            # Resolve a baseline's wrong relative report only for
                            # cleanup of this test-owned copied script directory.
                            session = (scripts / info["state_dir"]).resolve().parent
                            reaper = threading.Thread(target=process.wait)
                            reaper.start()
                            result = self.stop(session)
                            reaper.join(timeout=2)
                            self.assertEqual(result.returncode, 0, result.stdout)
                            self.assert_listener_closed(info)
                    self.assertEqual(mockup.read_text(), "<h1>Selected project</h1>")
                    self.assertEqual({path.relative_to(scripts) for path in scripts.rglob("*")},
                                     original_paths, "launcher wrote inside its script directory")
                    for path, content in original_files.items():
                        self.assertEqual((scripts / path).read_bytes(), content, str(path))

    def test_automatic_foreground_selection_and_explicit_background_override(self):
        for settings, args, foreground in (
            ({"CODEX_CI": "1"}, [], True),
            ({"MSYSTEM": "MINGW64"}, [], True),
            ({"CODEX_CI": "1", "MSYSTEM": "MINGW64"}, ["--background"], False),
        ):
            with self.subTest(settings=settings, foreground=foreground):
                project = self.root / str(len(self.processes))
                process = self.spawn(["bash", str(SCRIPTS / "start-server.sh"),
                                      "--project-dir", str(project), *args], {**self.env, **settings})
                info_file = wait_for(lambda: next(project.rglob("server-info"), None))
                info = json.loads(info_file.read_text())
                session = Path(info["state_dir"]).parent
                pid = int((session / "state/server.pid").read_text())
                if foreground:
                    self.assertEqual(pid, process.pid)
                    reaper = threading.Thread(target=process.wait)
                    reaper.start()
                else:
                    self.assertNotEqual(pid, process.pid)
                    self.assertEqual(process.wait(timeout=4), 0)
                result = self.stop(session)
                if foreground:
                    reaper.join(timeout=2)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assert_listener_closed(info)

    def test_ipv6_binding_with_valid_options_and_unicode_project_path(self):
        try:
            with socket.socket(socket.AF_INET6) as probe:
                probe.bind(("::1", 0))
        except OSError:
            self.skipTest("IPv6 loopback is unavailable")
        project = self.root / "프로젝트 space"
        process = self.spawn(["bash", str(SCRIPTS / "start-server.sh"), "--project-dir", str(project),
                              "--host", "::1", "--url-host", "::1", "--foreground"])
        info = self.session_info()
        parsed = urlsplit(info["url"])
        self.assertEqual(parsed.hostname, "::1")
        self.assertEqual(parsed.port, info["port"])
        self.connect(info).close()
        reaper = threading.Thread(target=process.wait)
        reaper.start()
        result = self.stop(Path(info["state_dir"]).parent)
        reaper.join(timeout=2)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_listener_closed(info)

    def test_project_mockups_under_tmp_are_persistent(self):
        project = tempfile.TemporaryDirectory(prefix="companion-project-", dir="/tmp")
        self.addCleanup(project.cleanup)
        self.root = Path(project.name)
        self.check_foreground_stop_preserves_mockups()

    def test_ephemeral_session_is_removed_after_process_exit(self):
        process = self.spawn(["bash", str(SCRIPTS / "start-server.sh"), "--foreground"])

        def startup():
            self.logs[-1].seek(0)
            for line in self.logs[-1].readlines():
                event = json.loads(line)
                if event.get("type") == "server-started":
                    return event
            return None

        info = wait_for(startup)
        session = Path(info["state_dir"]).parent
        self.addCleanup(shutil.rmtree, session, True)
        self.assertEqual(session.parent, Path("/tmp"))
        reaper = threading.Thread(target=process.wait)
        reaper.start()
        result = self.stop(session)
        reaper.join(timeout=2)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(process.poll(), 0)
        self.assert_listener_closed(info)
        self.assertFalse(session.exists())

    def test_stale_pid_does_not_signal_unrelated_test_process(self):
        unrelated = self.spawn(["sleep", "60"])
        session = self.root / "stale-session"
        (session / "state").mkdir(parents=True)
        (session / "state/server.pid").write_text(str(unrelated.pid))
        result = self.stop(session)
        self.assertIsNone(unrelated.poll(), "stale PID caused an unrelated process to be signaled")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "failed")
        self.assertTrue((session / "state/server.pid").exists())

    def test_shutdown_authentication_and_private_token(self):
        process = self.spawn(["bash", str(SCRIPTS / "start-server.sh"),
                              "--project-dir", str(self.root), "--foreground"])
        info = self.session_info()
        state = Path(info["state_dir"])
        control = json.loads((state / "server-control").read_text())
        self.assertEqual(stat.S_IMODE((state / "server-control").stat().st_mode), 0o600)
        self.assertNotIn(control["token"], (state / "server-info").read_text())
        self.logs[-1].seek(0)
        self.assertNotIn(control["token"], self.logs[-1].read())
        valid = self.control_headers(control)
        for headers in ({}, {**valid, "X-Brainstorm-Token": "0" * 64},
                        {**valid, "X-Brainstorm-Session": "other-session"},
                        {**valid, "Origin": "http://foreign.example"}):
            with self.subTest(headers=list(headers)):
                status, _ = self.control_request(info, headers)
                self.assertEqual(status, 403)
                self.assertIsNone(process.poll())
                self.connect(info).close()
        # A stale private token must also fail through the actual stop CLI.
        (state / "server-control").write_text(json.dumps({**control, "token": "0" * 64}))
        result = self.stop(state.parent)
        self.assertEqual(json.loads(result.stdout)["status"], "failed")
        self.assertIsNone(process.poll())
        self.connect(info).close()
        self.assertTrue((state / "server.pid").exists())

    def test_browser_payload_does_not_replace_the_servers_source_label(self):
        self.spawn(["bash", str(SCRIPTS / "start-server.sh"),
                    "--project-dir", str(self.root), "--foreground"])
        info = self.session_info()
        browser = self.connect(info, websocket=True)
        payload = json.dumps({"source": "human-user", "choice": "fixture-choice"}).encode()
        browser.sendall(bytes([0x81, 0x80 | len(payload)]) + b"\0" * 4 + payload)

        def observed_event():
            self.logs[-1].seek(0)
            for line in self.logs[-1].readlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("choice") == "fixture-choice":
                    return event
            return None

        self.assertEqual(wait_for(observed_event)["source"], "websocket-message")

    @unittest.skipUnless(shutil.which("sandbox-exec"), "macOS sandbox required for real EPERM")
    def test_permission_denied_is_not_process_absence(self):
        process = self.spawn(["bash", str(SCRIPTS / "start-server.sh"),
                              "--project-dir", str(self.root), "--foreground"])
        info = self.session_info()
        session = Path(info["state_dir"]).parent
        result = subprocess.run(["sandbox-exec", "-p", "(version 1)(allow default)(deny signal)",
                                 "bash", str(SCRIPTS / "stop-server.sh"), str(session)],
                                env=self.env, cwd=self.root, capture_output=True, text=True, timeout=4)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["status"], "failed")
        self.assertIn("EPERM", response["error"])
        self.assertIsNone(process.poll())
        self.assertTrue((session / "state/server.pid").exists())
        self.connect(info).close()

    def test_shutdown_clears_pending_debounce_and_partial_http_socket(self):
        scheduled = self.root / "debounce-scheduled"
        clock = self.root / "debounce-clock.cjs"
        clock.write_text("const fs = require('fs'); const timeout = global.setTimeout;\n"
                         "global.setTimeout = (fn, ms, ...args) => {\n"
                         f"if (ms === 100) fs.writeFileSync({json.dumps(str(scheduled))}, 'scheduled');\n"
                         "return timeout(fn, ms === 100 ? 10000 : ms, ...args); };\n")
        session = self.root / "debounce-session"
        process = self.spawn(["node", "--require", str(clock), str(SCRIPTS / "server.cjs")],
                             {**self.env, "BRAINSTORM_DIR": str(session)})
        info = self.session_info()
        partial = socket.create_connection((info["host"], info["port"]), timeout=2)
        self.sockets.append(partial)
        partial.sendall(b"GET / HTTP/1.1\r\n")
        (session / "content/new.html").write_text("<h1>Keep this pending screen</h1>")
        wait_for(scheduled.exists)
        reaper = threading.Thread(target=process.wait)
        reaper.start()
        result = self.stop(session)
        reaper.join(timeout=2)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(process.poll(), 0)
        self.assert_listener_closed(info)
        self.assertEqual(partial.recv(4096), b"")
        self.assertTrue((session / "content/new.html").exists())
        self.logs[-1].seek(0)
        self.assertNotIn('"type":"screen-added"', self.logs[-1].read())

    def test_exit_marker_does_not_report_shutdown_request_as_process_exit(self):
        hold = self.root / "hold-exit.cjs"
        hold.write_text("const hold = setInterval(() => {}, 1000);\n"
                        "process.once('SIGUSR2', () => clearInterval(hold));\n")
        session = self.root / "held-session"
        process = self.spawn(["node", "--require", str(hold), str(SCRIPTS / "server.cjs")],
                             {**self.env, "BRAINSTORM_DIR": str(session)})
        info = self.session_info()
        control = json.loads((session / "state/server-control").read_text())
        status, response = self.control_request(info, self.control_headers(control))
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(response)["status"], "stopping")
        wait_for(lambda: not (session / "state/server-info").exists())
        self.assert_listener_closed(info)
        self.assertIsNone(process.poll())
        self.assertFalse((session / "state/server-stopped").exists())
        process.send_signal(signal.SIGUSR2)
        self.assertEqual(process.wait(timeout=2), 0)
        self.assertTrue((session / "state/server-stopped").exists())

    def test_shutdown_closes_websocket_when_peer_stops_reading(self):
        process = self.spawn(["bash", str(SCRIPTS / "start-server.sh"),
                              "--project-dir", str(self.root), "--foreground"])
        info = self.session_info()
        browser = self.connect(info, websocket=True)
        browser.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        # Valid masked PING frames fill outbound buffers while this peer does
        # not read the PONG replies. Shutdown must not wait for peer cooperation.
        frame = b"\x89\xfd" + b"\x00" * 4 + b"p" * 125
        browser.sendall(frame * 32768)
        reaper = threading.Thread(target=process.wait)
        reaper.start()
        result = self.stop(Path(info["state_dir"]).parent)
        reaper.join(timeout=2)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(process.poll(), 0)
        self.assert_listener_closed(info)

    def test_display_url_serializes_ipv4_dns_and_ipv6_authorities(self):
        for host in ("127.0.0.1", "localhost", "::1", "[::1]"):
            with self.subTest(host=host):
                directory = self.root / host.replace(":", "_")
                env = {**self.env, "BRAINSTORM_DIR": str(directory), "BRAINSTORM_URL_HOST": host}
                process = self.spawn(["node", str(SCRIPTS / "server.cjs")], env)
                info_file = directory / "state/server-info"
                wait_for(info_file.exists)
                info = json.loads(info_file.read_text())
                parsed = urlsplit(info["url"])
                self.assertEqual(parsed.hostname, host.strip("[]"))
                self.assertEqual(parsed.port, info["port"])
                self.connect(info).close()
                process.terminate()
                process.wait(timeout=2)

    def run_automatic_shutdown(self, reason, browser_connected):
        owner = self.spawn(["sleep", "60"])
        trigger = self.root / "idle-trigger"
        clock = self.root / "clock.cjs"
        # Only accelerate the clock boundary; production listeners, watchers and
        # socket shutdown remain real. No production test flags are introduced.
        clock.write_text("const fs = require('fs');\n"
                         "const interval = global.setInterval;\n"
                         "global.setInterval = (fn, ms, ...args) => interval(fn, ms === 60000 ? 20 : ms, ...args);\n"
                         "const now = Date.now;\n"
                         f"Date.now = () => now() + (fs.existsSync({json.dumps(str(trigger))}) ? 1800001 : 0);\n")
        session = self.root / "automatic-session"
        env = {**self.env, "BRAINSTORM_DIR": str(session), "BRAINSTORM_OWNER_PID": str(owner.pid)}
        process = self.spawn(["node", "--require", str(clock), str(SCRIPTS / "server.cjs")], env)
        info = self.session_info()
        browser = self.connect(info, websocket=True) if browser_connected else None
        if reason == "owner":
            owner.terminate()
            owner.wait(timeout=2)
        else:
            trigger.touch()
        try:
            exit_code = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            marker = session / "state/server-stopped"
            self.fail(f"server process remains alive; stopped marker exists={marker.exists()}, websocket={browser_connected}")
        self.assertEqual(exit_code, 0)
        self.assert_listener_closed(info)
        if browser:
            browser.settimeout(1)
            received = b""
            while chunk := browser.recv(4096):
                received += chunk
            print(json.dumps({"shutdown": reason, "socket_eof": True,
                              "close_frame": received.hex()}), flush=True)
        self.assertFalse((session / "state/server-info").exists())
        stopped = json.loads((session / "state/server-stopped").read_text())
        self.assertEqual(stopped["reason"], "owner process exited" if reason == "owner" else "idle timeout")

    def test_owner_shutdown_without_browser(self):
        self.run_automatic_shutdown("owner", False)

    def test_owner_shutdown_closes_open_websocket_and_process(self):
        self.run_automatic_shutdown("owner", True)

    def test_idle_shutdown_without_browser(self):
        self.run_automatic_shutdown("idle", False)

    def test_idle_shutdown_closes_open_websocket_and_process(self):
        self.run_automatic_shutdown("idle", True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
