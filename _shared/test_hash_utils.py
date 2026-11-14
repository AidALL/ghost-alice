"""Copy-mode content hashing excludes the managed install marker.

The marker timestamp varies per installation and must not change the
recorded content hash of an otherwise unchanged skill.

Run: python3 -m pytest _shared/test_hash_utils.py -q
"""

from __future__ import annotations

import sys
import hashlib
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

    def test_directory_hash_frames_binary_file_boundaries(self):
        first = self.d / "a"
        first.write_bytes(b"\0b\0c")
        one_file = hash_utils.hash_target(self.d, "copy")
        first.write_bytes(b"")
        (self.d / "b").write_bytes(b"c")
        self.assertNotEqual(one_file, hash_utils.hash_target(self.d, "copy"))

    def test_legacy_record_uses_its_original_algorithm_without_rewriting_it(self):
        from addon_installer import _same_addon_target_is_clean
        from addon_uninstall import _process_target
        legacy = hashlib.sha256(b"SKILL.md\0body\n\0").hexdigest()
        entry = {"name": "skill", "target": str(self.d), "install_mode": "copy", "content_hash": legacy}
        self.assertTrue(_same_addon_target_is_clean(self.d, legacy, "copy"))
        self.assertEqual(_process_target(entry, allowed_roots=[self.d.parent],
                         addons_dir=self.d.parent / 'addons/codex', platform='codex')["action"], "would-remove")
        self.assertEqual(entry["content_hash"], legacy)
        self.assertTrue(hash_utils.hash_target(self.d, "copy").startswith("tree-v2:"))
        (self.d / "SKILL.md").write_text("external change")
        self.assertFalse(_same_addon_target_is_clean(self.d, legacy, "copy"))
        self.assertEqual(_process_target(entry, allowed_roots=[self.d.parent],
                         addons_dir=self.d.parent / 'addons/codex', platform='codex')["action"], "manual-review")

    def test_new_directory_baseline_still_authorizes_clean_removal(self):
        from addon_uninstall import _process_target, _apply_target
        entry = {"name": "skill", "target": str(self.d), "install_mode": "copy", "content_hash": hash_utils.hash_target(self.d, "copy")}
        item = _process_target(entry, allowed_roots=[self.d.parent],
                               addons_dir=self.d.parent / 'addons/codex', platform='codex')
        _apply_target(item, entry, allowed_roots=[self.d.parent],
                      addons_dir=self.d.parent / 'addons/codex', platform='codex')
        self.assertEqual(item["action"], "removed")
        self.assertFalse(self.d.exists())

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

    def test_copy_hash_ignores_python_bytecode_cache(self):
        h1 = hash_utils.hash_target(str(self.d), "copy")
        cache = self.d / "__pycache__"
        cache.mkdir()
        (cache / "module.cpython-314.pyc").write_bytes(b"bytecode-cache")
        h2 = hash_utils.hash_target(str(self.d), "copy")
        self.assertEqual(h1, h2, "Python import cache files must not affect the content hash")

    def test_copy_hash_tracks_bytecode_named_payload_outside_python_cache(self):
        h1 = hash_utils.hash_target(str(self.d), "copy")
        (self.d / "payload.pyc").write_bytes(b"managed-content")
        h2 = hash_utils.hash_target(str(self.d), "copy")
        self.assertNotEqual(h1, h2, "bytecode-named payloads outside __pycache__ must affect the content hash")


if __name__ == "__main__":
    unittest.main()
