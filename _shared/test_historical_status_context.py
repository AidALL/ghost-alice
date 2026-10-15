"""Native Stop retains the explicit control's route-binding requirement."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_completion_skill_evidence import assert_stop_observation


HOOK = Path(__file__).with_name("claude_stop_verification_hook.py")


class ExplicitControlRouteBindingTests(unittest.TestCase):
    def run_hook(self, text, platform, question="Status."):
        entries = [
            {"type": "user", "message": {"role": "user", "content": question}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "transcript.jsonl"
            transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(HOOK), "--platform", platform],
                input=json.dumps({"session_id": "fixture-stop", "last_assistant_message": text,
                                  "transcript_path": str(transcript)}), text=True,
                capture_output=True, check=True,
                env={key: value for key, value in os.environ.items()
                     if not key.startswith(('GHOST_ALICE_', 'CODEX_', 'CLAUDE_'))},
            )
        return json.loads(result.stdout)


    def test_native_stop_requires_bound_route_for_explicit_partial_control(self):
        text = "[partial-check]\n- scope: partial\n- unverified:\n  - pending: evidence missing"
        for platform in ("codex", "claude"):
            with self.subTest(platform=platform):
                assert_stop_observation(self, self.run_hook(text, platform),
                                        issue='response-route', repair_target='response_route')


if __name__ == "__main__":
    unittest.main()
