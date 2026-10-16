"""Tests for removing pending-merge entries that never belonged to Ghost-ALICE skills.

Dependencies: Python 3.11+ standard library + local merge-companion/_shared modules.
"""

import json
import tempfile
import unittest
from pathlib import Path

from cleanup_false_positive_legacy import cleanup_false_positive_legacy
from manifest_io import read_manifest, write_manifest


class TestUnmanagedRootCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        (self.repo / "skill-catalog").mkdir(parents=True)
        (self.repo / "skill-catalog" / "skills.json").write_text(json.dumps({"skills": [
            {"name": "task-router", "path": "task-router/SKILL.md"},
            {"name": "writing-plans", "path": "coding-convention/writing-plans/SKILL.md"},
        ]}), encoding="utf-8")
        self.skills = root / "home" / ".claude" / "skills"
        self.pending = root / "home" / ".ghost-alice" / "pending-merges" / "claude"
        self.pending.mkdir(parents=True)
        self.manifest = self.pending / "manifest.json"
        self.install_state = root / "home" / ".ghost-alice" / "install-state" / "claude.json"
        self.install_state.parent.mkdir(parents=True)
        review = self.skills / "review-helper"
        review.mkdir(parents=True)
        (review / ".ghost-alice-install.json").write_text('{"managed_by": "Ghost-ALICE"}', encoding="utf-8")
        # The installer recorded one copy-mode target.
        self.install_state.write_text(json.dumps({"targets": [{"dest_path": str(review)}]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, entry_id, relative, *, decided=False, backup=True):
        source = self.skills / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("current", encoding="utf-8")
        backup_path = self.pending / f"{entry_id}.bak"
        if backup:
            backup_path.write_text("backup", encoding="utf-8")
        return {"id": entry_id, "platform": "claude", "skill": Path(relative).parts[0],
                "source_path": str(source), "relative_path": relative, "backup_path": str(backup_path),
                "decided": decided, "decision": "discarded" if decided else None}

    def cleanup(self, *, apply, install_state=None):
        return cleanup_false_positive_legacy(manifest_path=self.manifest, pending_dir=self.pending, repo_root=self.repo,
                                             apply=apply, install_state_path=install_state or self.install_state)

    def test_entries_outside_ghost_alice_skill_roots_are_removed_with_their_backups(self):
        write_manifest(self.manifest, {"version": 1, "platform": "claude", "entries": [
            self.entry("synced-open", "synced/bucket/pptx/SKILL.md"),
            self.entry("synced-old", "synced/bucket/manifest.json", decided=True, backup=False),
            self.entry("own-skill", "my-notes/SKILL.md"),
            self.entry("legacy-core", "task-router/SKILL.md"),
            self.entry("family-core", "writing-plans/SKILL.md"),
            self.entry("copy-target", "review-helper/SKILL.md"),
        ]})

        dry = self.cleanup(apply=False)
        self.assertEqual((dry["removable"], dry["removed_entries"]), (3, 0))
        self.assertEqual(len(read_manifest(self.manifest)["entries"]), 6)

        summary = self.cleanup(apply=True)

        # Catalog skills without a marker and recorded copy targets stay; nothing else belongs to Ghost-ALICE.
        self.assertEqual([e["id"] for e in read_manifest(self.manifest)["entries"]],
                         ["legacy-core", "family-core", "copy-target"])
        self.assertEqual(summary["removed_entries"], 3)
        self.assertFalse((self.pending / "synced-open.bak").exists())
        self.assertFalse((self.pending / "own-skill.bak").exists())
        self.assertTrue((self.pending / "legacy-core.bak").exists())

    def test_unknown_install_state_removes_nothing(self):
        write_manifest(self.manifest, {"version": 1, "platform": "claude",
                                       "entries": [self.entry("synced-open", "synced/bucket/pptx/SKILL.md")]})

        summary = self.cleanup(apply=True, install_state=self.install_state.with_name("missing.json"))

        self.assertEqual(summary["removed_entries"], 0)
        self.assertEqual(len(read_manifest(self.manifest)["entries"]), 1)
        self.assertTrue((self.pending / "synced-open.bak").exists())


if __name__ == "__main__":
    unittest.main()
