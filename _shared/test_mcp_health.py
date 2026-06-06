"""Tests for MCP health and backoff state.

Dependencies: Python 3.11+ standard library only.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class McpHealthTests(unittest.TestCase):
    def test_non_mcp_payload_passes(self) -> None:
        from mcp_health import is_mcp_payload

        self.assertFalse(is_mcp_payload({"tool_name": "Read"}))

    def test_error_statuses_are_unhealthy(self) -> None:
        from mcp_health import classify_probe_result

        for status in (401, 403, 429, 503):
            with self.subTest(status=status):
                result = classify_probe_result(status_code=status, error=None)
                self.assertEqual(result["status"], "unhealthy")
        self.assertEqual(classify_probe_result(status_code=None, error="transport error")["status"], "unhealthy")

    def test_ttl_suppresses_reprobe_for_same_server(self) -> None:
        from mcp_health import McpHealthState

        now = [1000.0]
        state = McpHealthState(ttl_seconds=60, clock=lambda: now[0])
        self.assertTrue(state.should_probe("github"))
        state.record("github", {"status": "unhealthy", "reason": "429"})
        self.assertFalse(state.should_probe("github"))
        now[0] = 1061.0
        self.assertTrue(state.should_probe("github"))

    def test_backoff_increases_and_resets_on_healthy(self) -> None:
        from mcp_health import McpHealthState

        now = [2000.0]
        state = McpHealthState(ttl_seconds=0, clock=lambda: now[0])
        first = state.record("github", {"status": "unhealthy", "reason": "503"})
        second = state.record("github", {"status": "unhealthy", "reason": "503"})
        healthy = state.record("github", {"status": "healthy", "reason": "ok"})

        self.assertGreater(second["backoff_seconds"], first["backoff_seconds"])
        self.assertEqual(healthy["backoff_seconds"], 0)
        self.assertEqual(healthy["failure_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
