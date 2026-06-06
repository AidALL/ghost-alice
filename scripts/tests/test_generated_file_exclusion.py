import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MERGE_SCRIPTS = REPO_ROOT / "merge-companion" / "scripts"
if str(MERGE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(MERGE_SCRIPTS))

from diff_collector import collect_user_changes
from snapshot import capture_snapshot


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SNAPSHOT_CLI = _load_module("snapshot_cli_under_test", MERGE_SCRIPTS / "snapshot_cli.py")
DIFF_COLLECTOR_CLI = _load_module("diff_collector_cli_under_test", MERGE_SCRIPTS / "diff_collector_cli.py")


class GeneratedFileExclusionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skills_dir = self.root / ".agents" / "skills"
        self.skill = self.skills_dir / "task-router"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("skill\n", encoding="utf-8")
        (self.skill / "config.toml").write_text("version = 1\n", encoding="utf-8")
        (self.skill / ".ghost-alice-install.json").write_text('{"managed_by":"Ghost-ALICE"}\n', encoding="utf-8")
        scripts = self.skill / "scripts"
        scripts.mkdir()
        (scripts / "helper.py").write_text("print('ok')\n", encoding="utf-8")

        generated_files = [
            self.skill / "__pycache__" / "skill.cpython-312.pyc",
            self.skill / ".pytest_cache" / "v" / "cache" / "nodeids",
            self.skill / ".mypy_cache" / "3.12" / "meta.json",
            self.skill / ".ruff_cache" / "0.9.0" / "cache",
            self.skill / "node_modules" / "pkg" / "index.js",
            self.skill / ".git" / "config",
            self.skill / ".DS_Store",
            scripts / "__pycache__" / "helper.cpython-312.pyc",
            scripts / "helper.pyc",
        ]
        for path in generated_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("generated\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_snapshot_and_diff_walkers_exclude_generated_cache_files(self) -> None:
        expected = {
            str(self.skill / "SKILL.md"),
            str(self.skill / "config.toml"),
            str(self.skill / "scripts" / "helper.py"),
        }

        for walker in [SNAPSHOT_CLI._walk_user_files, DIFF_COLLECTOR_CLI._walk_user_files]:
            walked = {str(path) for path in walker(self.skills_dir)}
            self.assertEqual(walked, expected)

    def test_generated_file_change_does_not_enter_pending_diff(self) -> None:
        snapshot = self.root / "snapshot.json"
        capture_snapshot(snapshot, SNAPSHOT_CLI._walk_user_files(self.skills_dir), "codex")

        generated = self.skill / "__pycache__" / "skill.cpython-312.pyc"
        generated.write_text("changed generated\n", encoding="utf-8")
        self.assertEqual(collect_user_changes(snapshot, DIFF_COLLECTOR_CLI._walk_user_files(self.skills_dir)), [])

        real_config = self.skill / "config.toml"
        real_config.write_text("version = 2\n", encoding="utf-8")
        changes = collect_user_changes(snapshot, DIFF_COLLECTOR_CLI._walk_user_files(self.skills_dir))
        self.assertEqual([change["path"] for change in changes], [str(real_config)])


if __name__ == "__main__":
    unittest.main()
