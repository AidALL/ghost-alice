"""The user's home, each host's folder and the Ghost-ALICE root come from one owner each. Python standard library."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_context_adapter
import install_destination
import native_transcript


class HomeDirectoryTests(unittest.TestCase):
    def test_home_then_the_windows_variables_then_the_interpreter(self):
        self.assertEqual(install_destination.home_directory(dict(HOME='/h', USERPROFILE='/u')), Path('/h'))
        self.assertEqual(install_destination.home_directory(dict(HOME=' ', USERPROFILE='/u')), Path('/u'))
        self.assertEqual(install_destination.home_directory(dict(HOMEDRIVE='C:', HOMEPATH='\\Users\\x')), Path('C:\\Users\\x'))
        self.assertEqual(install_destination.home_directory(dict(HOMEDRIVE='C:')), Path.home())
        self.assertEqual(install_destination.home_directory({}), Path.home())

    def test_the_ghost_root_follows_the_home_the_environment_names(self):
        self.assertEqual(install_destination.ghost_root(env=dict(HOME='/h')), Path('/h') / '.ghost-alice')
        self.assertEqual(install_destination.ghost_root(env=dict(HOME='/h', GHOST_ALICE_GHOST_ROOT='/g')), Path('/g'))
        self.assertEqual(install_destination.ghost_root(Path('/given'), dict(HOME='/h')), Path('/given') / '.ghost-alice')


class HostFolderTests(unittest.TestCase):
    def test_codex_folder_override_then_codex_home_then_the_home_folder(self):
        self.assertEqual(native_transcript.codex_home(dict(GHOST_ALICE_CODEX_DIR='/o', CODEX_HOME='/c', HOME='/h')), '/o')
        self.assertEqual(native_transcript.codex_home(dict(CODEX_HOME='/c', HOME='/h')), '/c')
        self.assertEqual(native_transcript.codex_home(dict(HOME='/h')), str(Path('/h') / '.codex'))
        self.assertEqual(native_transcript.codex_home({}), str(Path.home() / '.codex'))

    def test_claude_folder_override_then_claude_config_dir_then_the_home_folder(self):
        self.assertEqual(claude_context_adapter.claude_home(dict(GHOST_ALICE_CLAUDE_DIR='/o', CLAUDE_CONFIG_DIR='/c', HOME='/h')), '/o')
        self.assertEqual(claude_context_adapter.claude_home(dict(CLAUDE_CONFIG_DIR='/c', HOME='/h')), '/c')
        self.assertEqual(claude_context_adapter.claude_home(dict(HOME='/h')), str(Path('/h') / '.claude'))
        self.assertEqual(claude_context_adapter.claude_home({}), str(Path.home() / '.claude'))


if __name__ == '__main__':
    unittest.main()
