import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from merge_companion_messages import render_pending_merge_message


class TestMergeCompanionMessages(unittest.TestCase):
    def assert_user_copy_first(self, body: str) -> None:
        user_idx = body.index("User:")
        tech_idx = body.index("Tech:")
        self.assertLess(user_idx, tech_idx)
        self.assertNotIn("Non-developer note:", body)
        self.assertNotIn("Developer note:", body)

    def test_install_tail_supports_english(self):
        body = render_pending_merge_message("install_tail")
        body_lower = body.lower()

        self.assert_user_copy_first(body)
        self.assertIn("During the agent tool update", body)
        self.assertIn("your local changes", body)
        self.assertIn("the next time you open claude/codex", body_lower)
        self.assertIn("Please review backed-up changes.", body)

    def test_session_start_supports_english(self):
        body = render_pending_merge_message("session_start")
        body_lower = body.lower()

        self.assert_user_copy_first(body)
        self.assertIn("This conversation is a new session", body)
        self.assertIn("in this conversation", body_lower)
        self.assertNotIn("the next time you open claude/codex", body_lower)


if __name__ == "__main__":
    unittest.main()
