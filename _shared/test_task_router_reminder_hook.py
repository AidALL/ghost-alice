#!/usr/bin/env python3
"""Tests for task_router_reminder_hook freshness fail-closed behavior."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import task_router_reminder_hook as trh


class DegradedLedgerFailClosedTests(unittest.TestCase):
    """H5: a degraded-ledger marker must withhold routing release instead of
    letting the silent-allow invariant ride a stale lineage anchor."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.platform = "codex"
        self.session = "s-run"
        self.payload = {"session_id": self.session}
        self.session_dir = trh.session_dir(self.root, self.platform, self.session)
        self.session_dir.mkdir(parents=True)
        # A prior turn's observed event: the stale anchor.
        (self.session_dir / "intent-events.jsonl").write_text(
            json.dumps({
                "event": "user-input-observed",
                "event_id": "evt-old",
                "input_digest": "sha256:old",
            }) + "\n",
            encoding="utf-8",
        )

    def test_degrade_marker_withholds_routing(self) -> None:
        (self.session_dir / "ledger-degraded.json").write_text(
            '{"schema_version": "session-intent-degrade.v1", "reason": "ledger-broken"}\n',
            encoding="utf-8",
        )
        message = trh.reminder_message("base", self.root, self.platform, self.payload)
        self.assertIn("withheld", message)
        self.assertIn("ledger is degraded", message)
        self.assertIn("ledger-broken", message)
        self.assertNotIn("silent allow", message)

    def test_without_marker_stale_anchor_still_releases_as_before(self) -> None:
        # Control: pre-fix behavior preserved when no degrade marker exists.
        message = trh.reminder_message("base", self.root, self.platform, self.payload)
        self.assertIn("silent allow", message)

    def test_unreadable_marker_still_withholds(self) -> None:
        (self.session_dir / "ledger-degraded.json").write_text("{broken", encoding="utf-8")
        message = trh.reminder_message("base", self.root, self.platform, self.payload)
        self.assertIn("withheld", message)
        self.assertIn("unreadable-marker", message)

    def test_producer_marker_path_matches_consumer_lookup(self) -> None:
        # Cross-module seam: the analyzer hook WRITES the marker with its own
        # session-key derivation; this consumer LOOKS IT UP with resolve_session_id.
        # If the two ever diverge, fail-closed silently stops working — the exact
        # drift class that produced the N2 intent-root divergence.
        import shutil
        import subprocess
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        home = base / "home"
        home.mkdir()
        broken = home / ".claude" / "skills" / "session-intent-analyzer" / "scripts"
        broken.mkdir(parents=True)
        (broken / "session_intent_ledger.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        shared = base / "_shared"
        shared.mkdir()
        analyzer_src = Path(trh.__file__).resolve().with_name("session_intent_analyzer_hook.py")
        shutil.copy2(analyzer_src, shared / "session_intent_analyzer_hook.py")
        root = base / "root"
        payload = {"session_id": "s-xchain", "prompt": "hello"}
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("GHOST_ALICE_SESSION_ID", None)
        proc = subprocess.run(
            [sys.executable, str(shared / "session_intent_analyzer_hook.py"),
             "--platform", "codex", "--format", "json", "--root", str(root)],
            input=json.dumps(payload), capture_output=True, text=True, env=env, check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        message = trh.reminder_message("base", root, "codex", payload)
        self.assertIn("withheld", message)
        self.assertIn("ledger is degraded", message)

    def test_producer_marker_path_matches_consumer_lookup_with_equals_session_id(self) -> None:
        # Session ids may contain platform-produced separators. Producer and
        # consumer sanitizers must agree, or the degraded-ledger marker is
        # written under one directory and looked up under another.
        import shutil
        import subprocess
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        home = base / "home"
        home.mkdir()
        broken = home / ".claude" / "skills" / "session-intent-analyzer" / "scripts"
        broken.mkdir(parents=True)
        (broken / "session_intent_ledger.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        shared = base / "_shared"
        shared.mkdir()
        analyzer_src = Path(trh.__file__).resolve().with_name("session_intent_analyzer_hook.py")
        shutil.copy2(analyzer_src, shared / "session_intent_analyzer_hook.py")
        root = base / "root"
        payload = {"session_id": "s=eq", "prompt": "hello"}
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("GHOST_ALICE_SESSION_ID", None)
        proc = subprocess.run(
            [sys.executable, str(shared / "session_intent_analyzer_hook.py"),
             "--platform", "codex", "--format", "json", "--root", str(root)],
            input=json.dumps(payload), capture_output=True, text=True, env=env, check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        message = trh.reminder_message("base", root, "codex", payload)
        self.assertIn("withheld", message)
        self.assertIn("ledger is degraded", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
