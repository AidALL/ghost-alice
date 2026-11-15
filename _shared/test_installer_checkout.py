"""Installer assets stay in the executing package; source provenance names the input."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer import auto, hosts, runner, selection, source  # noqa: E402
from installer_testing import make_context, make_source, quiet, use_source  # noqa: E402

class CheckoutNameTests(unittest.TestCase):
    def test_saved_package_uses_input_provenance_without_using_origin_as_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'home'
            package = make_source(home / '.ghost-alice/versions/fixed/core')
            origin = Path(temp) / 'removed-clone'
            provenance = dict(root=str(origin), branch='main', commit='merged-commit', dirty='clean',
                              remote='local-test-remote', ref='refs/heads/main')
            (package.parent / 'version.json').write_text(json.dumps({'source': provenance}))
            with use_source(package), mock.patch.dict(os.environ, {'HOME': str(home)}):
                self.assertEqual(source.input_record(), provenance)
                self.assertEqual(source.resolve_source(), source.SourceInfo(origin, 'main', 'merged-commit', 'clean'))
                self.assertEqual(selection.expand_skill_targets('alpha')[0].path, package / 'alpha')
                self.assertEqual(hosts.claude_bootstrap_source(), package / 'platforms/claude/CLAUDE.md')
                self.assertFalse(origin.exists())
                current = dict(provenance, root=str(Path(temp) / 'new-developer-clone'),
                               commit='newer-commit', dirty='dirty', remote='new-remote')
                (home / '.ghost-alice/installed-payload.json').write_text(json.dumps(
                    {'schema_version': 1, 'targets': {}, 'source': current}))
                actual = source.input_record()
                self.assertEqual(actual['root'], current['root'])
                self.assertEqual(actual['remote'], current['remote'])
                self.assertEqual((actual['commit'], actual['dirty']), ('merged-commit', 'clean'))

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
                ctx = make_context(home, "codex")
                self.assertEqual(auto.child_command(ctx, "codex")[1], str(fixture / "_shared" / "installer" / "cli.py"))
                runner.run_tool("_shared/encoding_guard.py", "--repo-root", "x")
                self.assertEqual(run_command.call_args.args[0][1], str(fixture / "_shared" / "encoding_guard.py"))


if __name__ == "__main__":
    unittest.main()
