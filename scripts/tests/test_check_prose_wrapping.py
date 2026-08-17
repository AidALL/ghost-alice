import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_prose_wrapping.py"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


def initialize_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet"], cwd=path, check=True)


class ProseWrappingCheckerTest(unittest.TestCase):
    def run_checker(self, repo: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(repo), "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if not result.stdout.strip():
            self.fail("checker must emit JSON; stderr was:\n" + result.stderr)
        return result, json.loads(result.stdout)

    def scan_cases(self, cases: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], dict]:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            initialize_repo(repo)
            for name, markdown in cases.items():
                write_text(repo / f"{name}.md", markdown)
            return self.run_checker(repo)

    def test_rejects_adjacent_prose_without_explicit_markdown_boundaries(self) -> None:
        cases = {
            "ordinary_sentences": """
                One complete sentence ends here.
                Another complete sentence is still the same Markdown paragraph.
            """,
            "colon_records": """
                Input: one value
                Output: another value
            """,
            "inline_html": """
                <span>Inline HTML remains ordinary prose.</span>
                Its continuation is not a block boundary.
            """,
            "lazy_blockquote": """
                > Quoted prose begins here
                and this lazy continuation remains in the quoted paragraph.
            """,
            "indented_pseudo_frontmatter": "  ---\n  title: This is ordinary Markdown prose\n  summary: Indentation cannot create frontmatter\n  owner: This remains part of the paragraph\n  ---\nA separate paragraph follows.\n",
            "invalid_backtick_fence_info": """
                ```python`invalid
                This is ordinary prose.
                It remains adjacent prose.
                ```
            """,
            "inline_code_in_prose": """
                Use ``path ` fragment`` in this ordinary prose line
                and keep its continuation on the same physical line.
            """,
            "list_item_continuation": """
                - This list paragraph begins here
                  and this is the same paragraph without a new marker.
            """,
        }

        result, payload = self.scan_cases(cases)

        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertEqual(
            {item["path"] for item in payload["violations"]},
            {f"{name}.md" for name in cases},
        )

    def test_accepts_explicit_markdown_boundaries(self) -> None:
        cases = {
            "blank_lines": "First paragraph.\n\nSecond paragraph.\n",
            "headings": """
                # ATX heading
                A one-line paragraph.

                Setext heading
                ==============
                Another one-line paragraph.

                ---
                Final one-line paragraph.
            """,
            "frontmatter": """
                ---
                title: Example
                summary: Frontmatter is a file protocol.
                ---
                A one-line paragraph follows.
            """,
            "lists": """
                - First item.
                - Second item.
                  - Nested item.

                  A blank-separated one-line paragraph in the item.

                      indented_code()
                      second_code_line()
            """,
            "table": """
                | Name | Value |
                | --- | ---: |
                | one | two |
                | three | four |
            """,
            "fences": """
                ```python
                first_code_line()
                second_code_line()
                ```

                ~~~~text with ` allowed
                first raw line
                second raw line
                ~~~~
            """,
            "html": """
                <!--
                A multiline comment is raw HTML.
                -->

                <pre>
                raw text line one
                raw text line two
                </pre>

                <div>
                block HTML line one
                block HTML line two
                </div>
            """,
            "links": """
                [example]: https://example.com/
                    "Optional title"
                [second]: <https://example.com/two>
            """,
            "protocol": """
                [gate-state]
                - intent: each field has a list marker
                - why: the marker is an explicit boundary
            """,
            "hard_breaks": (
                "A deliberate space break ends here.  \nNext line.\n\n"
                "A backslash break ends here.\\\nNext line.\n\n"
                "An HTML break ends here.<br />\nNext line.\n"
            ),
            "standalone_inline_code": """
                `path`
                ``path ` fragment``
                ````path ``` fragment````
            """,
            "quotes": "> One quoted paragraph.\n>\n> Another quoted paragraph.\n",
        }

        result, payload = self.scan_cases(cases)

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(payload["violation_count"], 0, payload["violations"])

    def test_scans_tracked_and_untracked_markdown_but_excludes_ignored_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            initialize_repo(repo)
            write_text(repo / ".gitignore", "ignored/\n")
            write_text(repo / "tracked.md", "Tracked prose wraps\nonto another line.\n")
            subprocess.run(["git", "add", ".gitignore", "tracked.md"], cwd=repo, check=True)
            write_text(repo / "nested" / "untracked.markdown", "Untracked prose wraps\nonto another line.\n")
            write_text(repo / "ignored" / "generated.md", "Ignored prose wraps\nonto another line.\n")

            result, payload = self.run_checker(repo)

        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertEqual(payload["checked_file_count"], 2)
        self.assertEqual(
            [item["path"] for item in payload["violations"]],
            ["nested/untracked.markdown", "tracked.md"],
        )

    def test_current_repository_markdown_has_no_width_only_wraps(self) -> None:
        result, payload = self.run_checker(REPO_ROOT)

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(payload["violation_count"], 0, payload["violations"])


class ProseWrappingGateWiringTest(unittest.TestCase):
    def test_ci_and_release_checklists_invoke_the_canonical_checker_once(self) -> None:
        command = "python3 scripts/check_prose_wrapping.py"
        surfaces = (
            REPO_ROOT / ".github" / "workflows" / "skill-validation.yml",
            REPO_ROOT / "docs" / "release" / "public-release-checklist.md",
            REPO_ROOT / "docs" / "ko" / "release" / "public-release-checklist.md",
        )

        for surface in surfaces:
            with self.subTest(surface=surface.relative_to(REPO_ROOT).as_posix()):
                body = surface.read_text(encoding="utf-8")
                self.assertEqual(body.count(command), 1)


if __name__ == "__main__":
    unittest.main()
