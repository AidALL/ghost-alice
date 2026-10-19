"""content_digest: the one fingerprint of installed content and the one comparison of a record entry with the disk."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_digest  # noqa: E402


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class DigestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rel: str, data: bytes = b"x") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class FileAndLinkTests(DigestCase):
    def test_file_digest_is_the_prefixed_sha256_of_the_bytes(self):
        path = self.write("a.txt", b"hello\n")
        self.assertEqual(content_digest.file_digest(path), _sha(b"hello\n"))
        self.assertRegex(content_digest.file_digest(path), r"^sha256:[0-9a-f]{64}$")

    def test_link_target_is_the_link_text_and_never_a_digest(self):
        target = self.root / "elsewhere"
        link = self.root / "link"
        link.symlink_to(target)
        self.assertEqual(content_digest.link_target(link), str(target))
        relative = self.root / "relative-link"
        relative.symlink_to("dangling/child")
        self.assertEqual(content_digest.link_target(relative), "dangling/child")


class TreeTests(DigestCase):
    def test_tree_maps_each_relative_posix_path_to_its_digest(self):
        self.write("SKILL.md", b"skill")
        self.write("scripts/tool.py", b"print(1)\n")
        self.write("references/deep/note.md", b"note")
        self.assertEqual(content_digest.tree(self.root), {
            "SKILL.md": _sha(b"skill"),
            "scripts/tool.py": _sha(b"print(1)\n"),
            "references/deep/note.md": _sha(b"note"),
        })

    def test_tree_leaves_out_caches_generated_files_and_the_ownership_marker(self):
        self.write("SKILL.md", b"skill")
        for rel in ("__pycache__/tool.cpython-314.pyc", ".git/HEAD", ".pytest_cache/v/cache", ".mypy_cache/x.json",
                    ".ruff_cache/x", "node_modules/pkg/index.js", "scripts/__pycache__/x.pyc", "loose.pyc",
                    ".DS_Store", "scripts/.DS_Store", ".ghost-alice-install.json"):
            self.write(rel)
        self.assertEqual(content_digest.tree(self.root), {"SKILL.md": _sha(b"skill")})

    def test_tree_never_follows_links(self):
        outside = self.write("outside/secret.md", b"outside").parent
        skill = self.write("skill/SKILL.md", b"skill").parent
        (skill / "linked-dir").symlink_to(outside, target_is_directory=True)
        (skill / "linked-file.md").symlink_to(outside / "secret.md")
        self.assertEqual(content_digest.tree(skill), {"SKILL.md": _sha(b"skill")})

    def test_an_empty_folder_has_no_files(self):
        empty = self.root / "empty"
        (empty / "nested").mkdir(parents=True)
        self.assertEqual(content_digest.tree(empty), {})


class CompareTests(DigestCase):
    def copy_entry(self, files):
        return {"mode": "copy", "files": files, "baseline": "recorded"}

    def test_a_copy_that_matches_its_record_has_no_differences(self):
        self.write("skill/SKILL.md", b"skill")
        entry = self.copy_entry(content_digest.tree(self.root / "skill"))
        self.assertEqual(content_digest.compare(entry, self.root / "skill"),
                         {"missing": [], "changed": [], "extra": [], "link_target_differs": False})

    def test_missing_changed_and_extra_files_are_listed_in_path_order(self):
        self.write("skill/SKILL.md", b"skill")
        self.write("skill/a.md", b"a")
        self.write("skill/b.md", b"b")
        entry = self.copy_entry(content_digest.tree(self.root / "skill"))
        (self.root / "skill" / "a.md").unlink()
        self.write("skill/b.md", b"edited")
        self.write("skill/z-new.md", b"new")
        self.write("skill/c-new.md", b"new")
        self.write("skill/__pycache__/ignored.pyc", b"cache")
        self.assertEqual(content_digest.compare(entry, self.root / "skill"),
                         {"missing": ["a.md"], "changed": ["b.md"], "extra": ["c-new.md", "z-new.md"],
                          "link_target_differs": False})

    def test_an_absent_copy_misses_every_recorded_file(self):
        entry = self.copy_entry({"SKILL.md": _sha(b"skill"), "a.md": _sha(b"a")})
        self.assertEqual(content_digest.compare(entry, self.root / "gone"),
                         {"missing": ["SKILL.md", "a.md"], "changed": [], "extra": [], "link_target_differs": False})

    def test_a_copy_replaced_by_a_link_has_none_of_the_recorded_files(self):
        self.write("source/SKILL.md", b"skill")
        entry = self.copy_entry(content_digest.tree(self.root / "source"))
        (self.root / "skill").symlink_to(self.root / "source", target_is_directory=True)
        self.assertEqual(content_digest.compare(entry, self.root / "skill")["missing"], ["SKILL.md"])

    def test_a_copy_without_a_baseline_reports_nothing(self):
        self.write("skill/SKILL.md", b"skill")
        entry = {"mode": "copy", "baseline": "unknown"}
        self.assertEqual(content_digest.compare(entry, self.root / "skill"),
                         {"missing": [], "changed": [], "extra": [], "link_target_differs": False})

    def test_a_link_differs_only_when_it_no_longer_points_at_the_recorded_target(self):
        source = self.root / "source"
        source.mkdir()
        link = self.root / "skill"
        link.symlink_to(source, target_is_directory=True)
        entry = {"mode": "link", "link_target": str(source), "baseline": "recorded"}
        self.assertEqual(content_digest.compare(entry, link),
                         {"missing": [], "changed": [], "extra": [], "link_target_differs": False})
        link.unlink()
        link.symlink_to(self.root / "other", target_is_directory=True)
        self.assertTrue(content_digest.compare(entry, link)["link_target_differs"])
        link.unlink()
        link.mkdir()
        self.assertTrue(content_digest.compare(entry, link)["link_target_differs"])
        link.rmdir()
        self.assertTrue(content_digest.compare(entry, link)["link_target_differs"])


if __name__ == "__main__":
    unittest.main()
