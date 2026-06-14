import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source, installer_ps1_source


REPO_ROOT = Path(__file__).resolve().parents[2]
ENCODING_GUARD = REPO_ROOT / "_shared" / "encoding_guard.py"
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"


def _load_guard():
    if not ENCODING_GUARD.exists():
        raise AssertionError("_shared/encoding_guard.py must exist")
    spec = importlib.util.spec_from_file_location("encoding_guard_under_test", ENCODING_GUARD)
    if spec is None or spec.loader is None:
        raise AssertionError("encoding_guard.py must be importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _extract_bash_function(source: str, name: str) -> str:
    lines = source.splitlines(keepends=True)
    start = None
    for idx, line in enumerate(lines):
        if line.startswith(f"{name}() {{"):
            start = idx
            break
    if start is None:
        raise AssertionError(f"Function not found: {name}")

    body = []
    depth = 0
    for line in lines[start:]:
        body.append(line)
        depth += line.count("{")
        depth -= line.count("}")
        if depth == 0:
            return "".join(body)
    raise AssertionError(f"Function did not terminate: {name}")


def _extract_powershell_function(source: str, name: str) -> str:
    lines = source.splitlines(keepends=True)
    start = None
    for idx, line in enumerate(lines):
        if line.startswith(f"function {name} "):
            start = idx
            break
    if start is None:
        raise AssertionError(f"Function not found: {name}")

    body = []
    depth = 0
    for line in lines[start:]:
        body.append(line)
        depth += line.count("{")
        depth -= line.count("}")
        if depth == 0:
            return "".join(body)
    raise AssertionError(f"Function did not terminate: {name}")


class EncodingGuardTest(unittest.TestCase):
    def test_valid_utf8_korean_skill_markdown_passes(self) -> None:
        guard = _load_guard()
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_md = Path(temp_dir) / "korean-skill" / "SKILL.md"
            skill_md.parent.mkdir()
            skill_md.write_text(
                "---\nname: sample-skill\ndescription: Sample skill.\n---\n\n# Sample skill\n",
                encoding="utf-8",
            )

            self.assertEqual(guard.validate_paths([skill_md]), [])

    def test_invalid_utf8_reports_path_and_byte_offset(self) -> None:
        guard = _load_guard()
        with tempfile.TemporaryDirectory() as temp_dir:
            bad = Path(temp_dir) / "bad.md"
            bad.write_bytes(b"valid\n\xff\n")

            issues = guard.validate_paths([bad])

            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].code, "invalid-utf8")
            self.assertEqual(issues[0].byte_offset, 6)
            self.assertIn("bad.md", str(issues[0].path))

    def test_cp949_only_markdown_is_rejected_not_redecoded(self) -> None:
        guard = _load_guard()
        with tempfile.TemporaryDirectory() as temp_dir:
            bad = Path(temp_dir) / "cp949-only.md"
            bad.write_bytes(bytes([0xc7, 0xd1, 0xb1, 0xdb, 0x0a]))

            issues = guard.validate_paths([bad])

            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].code, "invalid-utf8")

    def test_skill_md_requires_yaml_frontmatter(self) -> None:
        guard = _load_guard()
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_md = Path(temp_dir) / "no-frontmatter" / "SKILL.md"
            skill_md.parent.mkdir()
            skill_md.write_text("# No frontmatter\n", encoding="utf-8")

            issues = guard.validate_paths([skill_md])

            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].code, "missing-frontmatter")

    def test_json_and_toml_parse_errors_are_hard_failures(self) -> None:
        guard = _load_guard()
        with tempfile.TemporaryDirectory() as temp_dir:
            bad_json = Path(temp_dir) / "hooks.json"
            bad_toml = Path(temp_dir) / "config.toml"
            bad_json.write_text("{not json", encoding="utf-8")
            bad_toml.write_text("[features\nhooks = true\n", encoding="utf-8")

            issues = guard.validate_paths([bad_json, bad_toml])

            self.assertEqual({issue.code for issue in issues}, {"invalid-json", "invalid-toml"})

    def test_powershell_script_requires_utf8_bom(self) -> None:
        guard = _load_guard()
        with tempfile.TemporaryDirectory() as temp_dir:
            ps1 = Path(temp_dir) / "install.ps1"
            ps1.write_text("Write-Host 'Cafe'\n", encoding="utf-8")

            issues = guard.validate_paths([ps1])

            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].code, "missing-utf8-bom")

    def test_repo_scan_ignores_tmp_local_artifacts(self) -> None:
        guard = _load_guard()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "AGENTS.md").write_text("# Project rules\n", encoding="utf-8")
            local_run = root / ".tmp" / "local-model-experiments"
            local_run.mkdir(parents=True)
            (local_run / "http-models.json").write_text("not json", encoding="utf-8")
            (local_run / "Activate.ps1").write_text("Write-Host 'local venv'\n", encoding="utf-8")

            issues = guard.validate_repo(root)

            self.assertEqual(issues, [])

    def test_cli_returns_nonzero_for_invalid_semantic_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bad = Path(temp_dir) / "bad.md"
            bad.write_bytes(b"\xff")

            result = subprocess.run(
                [sys.executable, str(ENCODING_GUARD), "--paths", str(bad)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid-utf8", result.stderr)
            self.assertIn("bad.md", result.stderr)

    def test_cli_accepts_current_repository_semantic_assets(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ENCODING_GUARD), "--repo-root", str(REPO_ROOT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_installers_run_encoding_guard_before_merge_preflight(self) -> None:
        sh = installer_bash_source()
        ps1 = installer_ps1_source()
        install_sh = _extract_bash_function(sh, "install")
        install_ps1 = _extract_powershell_function(ps1, "Invoke-Install")

        self.assertIn("_run_encoding_guard_before_install", sh)
        self.assertLess(install_sh.index("check_source_health"), install_sh.index("_run_encoding_guard_before_install"))
        self.assertLess(
            install_sh.index("_run_encoding_guard_before_install"),
            install_sh.index("_run_preflight_before_install"),
        )

        self.assertIn("function Invoke-EncodingGuardBeforeInstall", ps1)
        self.assertLess(install_ps1.index("Test-SourceHealth"), install_ps1.index("Invoke-EncodingGuardBeforeInstall"))
        self.assertLess(
            install_ps1.index("Invoke-EncodingGuardBeforeInstall"),
            install_ps1.index("Invoke-PreflightBeforeInstall"),
        )


if __name__ == "__main__":
    unittest.main()
