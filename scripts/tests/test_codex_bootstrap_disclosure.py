"""Exercise common governance delivery through the production merge CLI."""

from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
MERGER = ROOT / "_shared/global_rule_blocks.py"
CONTRACT = ROOT / "platforms/codex/AGENTS.md"
LOADER = ROOT / "platforms/codex/bootstrap.md"


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


class CodexBootstrapDisclosureTests(unittest.TestCase):
    def merge(self, dest):
        import sys
        return subprocess.run(
            [sys.executable, str(MERGER), "codex-merge", "--source", str(CONTRACT),
             "--loader", str(LOADER), "--dest", str(dest)],
            capture_output=True, text=True, check=False,
        )

    def doctor(self, dest):
        import sys
        return subprocess.run(
            [sys.executable, str(ROOT / "_shared/install_doctor.py"),
             "--platform", "codex", "--repo-root", str(ROOT),
             "--ghost-alice-root", str(dest.parent),
             "--global-rule", "codex-bootstrap", str(dest),
             "# Ghost-ALICE Codex Bootstrap",
             "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->",
             "<!-- Ghost-ALICE managed block end: codex-bootstrap -->"],
            capture_output=True, text=True,
        )

    def test_loader_delivers_common_authority_and_preserves_project_rules(self):
        with tempfile.TemporaryDirectory(prefix="common governance ") as tmp:
            dest = Path(tmp) / "AGENTS.md"
            project_rules = "\n# Project constraints\nUse the project test command.\n"
            dest.write_text(
                "# Ghost-ALICE Codex Bootstrap\n"
                "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->\n"
                "If trusted project instructions already supply the complete contract, use that body.\n"
                "<!-- Ghost-ALICE managed block end: codex-bootstrap -->\n"
                + project_rules
            )
            result = self.merge(dest)
            self.assertEqual(result.returncode, 0, result.stderr)
            body = dest.read_text()
            self.assertTrue(body.endswith(project_rules))
            self.assertIn("Common Ghost-ALICE governance comes from", body)
            self.assertIn("Project instruction files supply project-specific rules", body)
            self.assertIn("Explicit user instructions take precedence", body)
            self.assertIn("without reading the common governance file", body)
            self.assertIn("even when a hook message points to its sections", body)
            self.assertNotIn("If trusted project instructions already supply the complete", body)

    def test_doctor_uses_managed_dependency_marker_independently_of_headings(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "AGENTS.md"
            self.assertEqual(self.merge(dest).returncode, 0)
            lines = dest.read_text().splitlines(keepends=True)
            dest.write_text("".join("## Instructions\n" if line.startswith("## ") else line
                                    for line in lines))
            dest.with_name("ghost-alice-governance.md").unlink()
            self.assertIn("codex-governance: absent", self.doctor(dest).stdout)

    def test_doctor_does_not_treat_user_appendix_as_managed_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "AGENTS.md"
            dest.write_text(
                "# Ghost-ALICE Codex Bootstrap\n"
                "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->\n"
                "Full common governance body.\n"
                "<!-- Ghost-ALICE managed block end: codex-bootstrap -->\n"
                "\n# User example\n"
                "<!-- Ghost-ALICE governance source: installed-common -->\n"
                "## Select the applicable contract\n"
                "An example references ghost-alice-governance.md.\n"
            )
            self.assertNotIn("codex-governance:", self.doctor(dest).stdout)

    def test_doctor_recognizes_installed_legacy_selector_dependencies(self):
        for heading in ("## Select the applicable contract", "## Select the instruction body"):
            with self.subTest(heading=heading), tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / "AGENTS.md"
                dest.write_text(
                    "# Ghost-ALICE Codex Bootstrap\n"
                    "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->\n"
                    f"{heading}\nRead `{dest.with_name('ghost-alice-governance.md')}`.\n"
                    "<!-- Ghost-ALICE managed block end: codex-bootstrap -->\n"
                )
                self.assertIn("codex-governance: absent", self.doctor(dest).stdout)

    def test_full_contract_is_available_outside_default_instruction_file(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap disclosure ") as tmp:
            dest = Path(tmp) / "AGENTS.md"
            result = self.merge(dest)
            self.assertEqual(result.returncode, 0, result.stderr)
            fallback = dest.with_name("ghost-alice-governance.md")
            self.assertIn(str(fallback), dest.read_text())
            # Required governance procedures remain available to ordinary projects.
            self.assertIn((ROOT / "platforms/common-governance.md").read_text().strip(), fallback.read_text())
            self.assertNotIn("## Codex Hookless Fallback", dest.read_text())

    def test_user_owned_fallback_blocks_loader_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "AGENTS.md"
            fallback = dest.with_name("ghost-alice-governance.md")
            fallback.write_text("user-owned contract\n")
            result = self.merge(dest)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fallback.read_text(), "user-owned contract\n")
            self.assertFalse(dest.exists())

    def test_uninstall_does_not_follow_fallback_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "AGENTS.md"
            self.assertEqual(self.merge(dest).returncode, 0)
            fallback = dest.with_name("ghost-alice-governance.md")
            external = Path(tmp) / "external.md"
            external.write_text(fallback.read_text() + "\nuser appendix\n")
            before = external.read_bytes()
            fallback.unlink()
            fallback.symlink_to(external)
            import sys
            result = subprocess.run([sys.executable, str(MERGER), "codex-remove", "--dest", str(dest)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(external.read_bytes(), before, "Uninstall changed a symlink target")
            self.assertTrue(fallback.is_symlink())
            from _shared.installer_assets import classify_global_rule_file
            classification = classify_global_rule_file(
                fallback, full_file_marker="# Ghost-ALICE Codex Bootstrap", reject_symlinks=True)
            self.assertEqual(classification.ownership, "ownership-conflict",
                             "Doctor must not report an uninstall-protected symlink as managed")

    def test_orphaned_owned_fallback_is_removed_by_full_cleanup(self):
        import sys
        sys.path.insert(0, str(ROOT / "_shared"))
        from unittest.mock import patch
        import uninstall_cleanup
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "AGENTS.md"
            self.assertEqual(self.merge(dest).returncode, 0)
            fallback = dest.with_name("ghost-alice-governance.md")
            dest.unlink()
            with patch.object(uninstall_cleanup, "_codex_home", return_value=Path(tmp)):
                dry = uninstall_cleanup._global_rule_items("codex", confirm=False)
                self.assertEqual(dry[-1]["path"], fallback.as_posix())
                self.assertEqual(dry[-1]["action"], "would-remove-global-rule")
                self.assertTrue(fallback.exists())
                result = uninstall_cleanup._global_rule_items("codex", confirm=True)
            self.assertEqual(result[-1]["action"], "removed-global-rule")
            self.assertFalse(fallback.exists())

    def test_cleanup_reports_user_primary_and_owned_companion_independently(self):
        import sys
        sys.path.insert(0, str(ROOT / "_shared"))
        from unittest.mock import patch
        import uninstall_cleanup
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "AGENTS.md"
            self.assertEqual(self.merge(dest).returncode, 0)
            dest.write_text("user-owned primary\n")
            with patch.object(uninstall_cleanup, "_codex_home", return_value=Path(tmp)):
                dry = uninstall_cleanup._global_rule_items("codex", confirm=False)
                result = uninstall_cleanup._global_rule_items("codex", confirm=True)
            self.assertEqual([x["action"] for x in dry], ["unchanged", "would-remove-global-rule"])
            self.assertEqual([x["action"] for x in result], ["unchanged", "removed-global-rule"])
            self.assertEqual(dest.read_text(), "user-owned primary\n")

    def test_cleanup_preserves_directory_at_companion_path(self):
        import sys
        sys.path.insert(0, str(ROOT / "_shared"))
        from unittest.mock import patch
        import uninstall_cleanup
        with tempfile.TemporaryDirectory() as tmp:
            fallback = Path(tmp) / "ghost-alice-governance.md"
            fallback.mkdir()
            (fallback / "user.txt").write_text("preserve\n")
            with patch.object(uninstall_cleanup, "_codex_home", return_value=Path(tmp)):
                for confirm in (False, True):
                    result = uninstall_cleanup._global_rule_items("codex", confirm=confirm)
                    self.assertEqual(result[-1]["action"], "manual-review")
            self.assertEqual((fallback / "user.txt").read_text(), "preserve\n")

    def test_doctor_reports_missing_fallback_for_installed_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "AGENTS.md"
            self.assertEqual(self.merge(dest).returncode, 0)
            dest.with_name("ghost-alice-governance.md").unlink()
            result = self.doctor(dest)
            self.assertIn("codex-governance: absent", result.stdout,
                          "Doctor accepted a selector without its required fallback")

    @unittest.skipUnless(shutil.which("codex"), "Native Codex CLI not installed")
    def test_native_project_input_does_not_repeat_full_governance(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-native-") as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)
            source = (ROOT / "AGENTS.md").read_text()
            (project / "AGENTS.md").write_text(source)
            (project / ".codex").mkdir()
            shutil.copyfile(ROOT / ".codex/config.toml", project / ".codex/config.toml")
            task_home = Path(tmp) / "runtime"
            task_home.mkdir()
            (task_home / "config.toml").write_text(
                f"[projects.{json.dumps(str(project))}]\ntrust_level = \"trusted\"\n"
            )
            result = self.merge(task_home / "AGENTS.md")
            self.assertEqual(result.returncode, 0, result.stderr)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith("GHOST_ALICE_") and k != "CODEX_THREAD_ID"}
            env["CODEX_HOME"] = str(task_home)
            rendered = subprocess.run(
                ["codex", "-C", str(project), "debug", "prompt-input", "Explain the objective."],
                env=env, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            instructions = "\n".join(strings(json.loads(rendered.stdout)))
            self.assertIn(source, instructions, "Project contract was truncated")
            rule = "## Session Intent"
            self.assertEqual(instructions.count(rule), 0, "Project input preloaded the common procedure")
            self.assertNotIn("## Codex Hookless Fallback", instructions)


if __name__ == "__main__":
    unittest.main()
