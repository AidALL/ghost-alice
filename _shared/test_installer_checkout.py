"""installer: the checkout the installer runs from has one name, `runner.REPO_ROOT`.

Every module reads `runner.REPO_ROOT` when it runs, so patching that one name
(`installer_testing.use_source`) moves the whole installer to another checkout.
"""
from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer  # noqa: E402
from installer import auto, cli, hosts, report, runner, selection, source  # noqa: E402
from installer_testing import make_context, make_source, quiet, use_source  # noqa: E402

PACKAGE = Path(installer.__file__).resolve().parent


class CheckoutNameTests(unittest.TestCase):
    def test_context_has_no_second_name_for_the_checkout(self):
        self.assertNotIn("source_root", [f.name for f in dataclasses.fields(cli.Context)])
        with self.assertRaises(TypeError):
            cli.Context(options=cli.Options(), source_root=Path("/elsewhere"))

    def test_no_installer_function_takes_a_source_root(self):
        # Addon sources are the only second root, and they travel in `ctx.addon_sources`.
        for path in sorted(PACKAGE.glob("*.py")):
            module = importlib.import_module(f"installer.{path.stem}")
            for name, function in inspect.getmembers(module, inspect.isfunction):
                if function.__module__ == module.__name__:
                    with self.subTest(function=f"{path.stem}.{name}"):
                        self.assertNotIn("source_root", inspect.signature(function).parameters)

    def test_only_runner_names_the_checkout_and_everyone_reads_it_at_run_time(self):
        for path in sorted(PACKAGE.glob("*.py")):
            if path.name == "runner.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                with self.subTest(module=path.name, line=getattr(node, "lineno", 0)):
                    if isinstance(node, ast.ImportFrom) and node.module == "installer.runner":
                        self.assertNotIn("REPO_ROOT", [alias.name for alias in node.names])
                    self.assertFalse(isinstance(node, ast.Name) and node.id == "REPO_ROOT")
                    self.assertFalse(isinstance(node, ast.Attribute) and node.attr == "source_root")

    def test_one_patch_moves_every_reader_to_the_fixture_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = make_source(root / "src")
            home = root / "home"
            home.mkdir()
            quiet(home)
            with use_source(fixture), mock.patch.dict(os.environ, {"HOME": str(home)}), \
                    mock.patch("installer.runner.run_command", return_value=runner.ToolResult(0)) as run_command:
                self.assertEqual(selection.expand_skill_targets("alpha"), [selection.Target("alpha", fixture / "alpha")])
                self.assertEqual(hosts.claude_bootstrap_source(), fixture / "platforms" / "claude" / "CLAUDE.md")
                source.assert_session_gate_contract()
                ctx = make_context(home, "codex")
                self.assertEqual(auto.child_command(ctx, "codex")[1], str(fixture / "_shared" / "installer" / "cli.py"))
                runner.run_tool("_shared/encoding_guard.py", "--repo-root", "x")
                self.assertEqual(run_command.call_args.args[0][1], str(fixture / "_shared" / "encoding_guard.py"))
                (fixture / "skill-catalog" / "session-gates.json").unlink()
                with self.assertRaises(runner.InstallerExit), mock.patch.object(report, "error") as error:
                    source.assert_session_gate_contract()
                self.assertIn(str(fixture), error.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
