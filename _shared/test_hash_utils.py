"""TDD for review finding H1 (follow-up): the copy-mode content hash must ignore
the Ghost-ALICE managed marker (.ghost-alice-install.json), whose installed_at
timestamp changes on every install. Otherwise a copy-mode addon's recorded hash
drifts from its live hash across reinstalls and falsely trips the drift gate.

Run: python3 -m pytest _shared/test_hash_utils.py -q
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "_shared"))

import hash_utils  # noqa: E402


class HashTargetMarkerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name) / "skill"
        self.d.mkdir()
        (self.d / "SKILL.md").write_text("body\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_copy_hash_ignores_managed_marker(self):
        marker = self.d / ".ghost-alice-install.json"
        marker.write_text('{"installed_at":"2026-01-01T00:00:00Z"}', encoding="utf-8")
        h1 = hash_utils.hash_target(str(self.d), "copy")
        # marker rewritten with a fresh timestamp (what every reinstall does)
        marker.write_text('{"installed_at":"2026-06-17T23:59:59Z","extra":true}', encoding="utf-8")
        h2 = hash_utils.hash_target(str(self.d), "copy")
        self.assertEqual(h1, h2, "managed marker must not affect the content hash")

    def test_copy_hash_still_tracks_real_content(self):
        (self.d / ".ghost-alice-install.json").write_text("{}", encoding="utf-8")
        h1 = hash_utils.hash_target(str(self.d), "copy")
        (self.d / "SKILL.md").write_text("EDITED BY USER\n", encoding="utf-8")
        h2 = hash_utils.hash_target(str(self.d), "copy")
        self.assertNotEqual(h1, h2, "a real content change must still change the hash")


if __name__ == "__main__":
    unittest.main()
