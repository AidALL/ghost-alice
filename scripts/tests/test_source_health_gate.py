import json
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "install.ps1"
BOOTSTRAP_SOURCE_UPDATE = ROOT / "scripts" / "bootstrap-source-update.sh"
SCRIPTS_DIR = ROOT / "scripts"
SHARED_DIR = ROOT / "_shared"

import sys

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SHARED_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _installer_source import installer_bash_source, installer_ps1_source


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


def _find_test_bash() -> str | None:
    candidates = [
        shutil.which("bash"),
        shutil.which("bash.exe"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        normalized = path.as_posix().lower()
        if normalized.endswith("/windows/system32/bash.exe"):
            continue
        if normalized.endswith("/appdata/local/microsoft/windowsapps/bash.exe"):
            continue
        return str(path)

    return None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


class SourceHealthGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bash = _find_test_bash()
        self.git = shutil.which("git")

    def _make_repo(self) -> Path:
        temp_dir = Path(tempfile.mkdtemp())
        _git(temp_dir, "init")
        _git(temp_dir, "config", "user.email", "tests@example.invalid")
        _git(temp_dir, "config", "user.name", "Tests")
        (temp_dir / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        _git(temp_dir, "add", "tracked.txt")
        _git(temp_dir, "commit", "-m", "baseline")
        return temp_dir

    def _make_repo_with_upstream(self) -> tuple[Path, Path]:
        temp_root = Path(tempfile.mkdtemp())
        remote = temp_root / "origin.git"
        remote.mkdir()
        _git(remote, "init", "--bare")
        # Pin the bare remote's default branch to 'main' so clones, pushes, and
        # origin/main lookups work regardless of the system git init.defaultBranch
        # (which is 'master' when unset). symbolic-ref is portable across git versions.
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

        repo = temp_root / "work"
        subprocess.run(
            ["git", "clone", str(remote), str(repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(repo, "config", "user.email", "tests@example.invalid")
        _git(repo, "config", "user.name", "Tests")
        (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(repo, "commit", "-m", "baseline")
        _git(repo, "push", "-u", "origin", "HEAD")
        return repo, remote

    def _add_commit(self, repo: Path, message: str, content: str) -> None:
        (repo / "tracked.txt").write_text(content, encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(repo, "commit", "-m", message)

    def _run_bash_source_health(self, repo: Path) -> subprocess.CompletedProcess[str]:
        if not self.bash:
            self.skipTest("No non-WSL bash executable available for install.sh source health test")
        if not self.git:
            self.skipTest("git executable is required for source health tests")

        source = installer_bash_source()
        function_bundle = _extract_bash_function(source, "check_source_health")

        with tempfile.TemporaryDirectory() as temp_dir:
            runner = Path(temp_dir) / "runner.sh"
            runner.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    set -u

                    SKIP_SOURCE_HEALTH=0
                    SCRIPT_DIR="{repo.as_posix()}"

                    t() {{
                      if [ "$#" -ge 2 ]; then
                        printf "%s" "$2"
                      elif [ "$#" -ge 1 ]; then
                        printf "%s" "$1"
                      fi
                    }}
                    info() {{ printf "INFO:%s\\n" "$*" >&2; }}
                    warn() {{ printf "WARN:%s\\n" "$*" >&2; }}
                    error() {{ printf "ERROR:%s\\n" "$*" >&2; }}

                    {function_bundle}

                    cd "{repo.as_posix()}"
                    check_source_health
                    """
                ),
                encoding="utf-8",
            )

            return subprocess.run(
                [self.bash, str(runner)],
                capture_output=True,
                text=True,
            )

    def _run_bash_source_update(self, repo: Path) -> subprocess.CompletedProcess[str]:
        if not self.bash:
            self.skipTest("No non-WSL bash executable available for install.sh source update test")
        if not self.git:
            self.skipTest("git executable is required for source update tests")

        source = installer_bash_source()
        function_bundle = _extract_bash_function(source, "update_source_checkout")

        with tempfile.TemporaryDirectory() as temp_dir:
            runner = Path(temp_dir) / "runner.sh"
            runner.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    set -u

                    SCRIPT_DIR="{repo.as_posix()}"

                    t() {{
                      if [ "$#" -ge 2 ]; then
                        printf "%s" "$2"
                      elif [ "$#" -ge 1 ]; then
                        printf "%s" "$1"
                      fi
                    }}
                    info() {{ printf "INFO:%s\\n" "$*" >&2; }}
                    warn() {{ printf "WARN:%s\\n" "$*" >&2; }}
                    error() {{ printf "ERROR:%s\\n" "$*" >&2; }}
                    ok() {{ printf "OK:%s\\n" "$*" >&2; }}

                    {function_bundle}

                    cd "{repo.as_posix()}"
                    update_source_checkout
                    """
                ),
                encoding="utf-8",
            )

            return subprocess.run(
                [self.bash, str(runner)],
                capture_output=True,
                text=True,
            )

    def test_install_sh_source_health_passes_clean_repo(self) -> None:
        repo = self._make_repo()

        result = self._run_bash_source_health(repo)

        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_install_sh_source_health_reports_branch_head_and_no_upstream(self) -> None:
        repo = self._make_repo()
        _git(repo, "checkout", "-b", "feature/source-health")

        result = self._run_bash_source_health(repo)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Source health: branch=feature/source-health", result.stderr)
        self.assertRegex(result.stderr, r"head=[0-9a-f]{7,}")
        self.assertIn("upstream=none", result.stderr)

    def test_install_sh_source_health_reports_detached_head(self) -> None:
        repo = self._make_repo()
        _git(repo, "checkout", "--detach")

        result = self._run_bash_source_health(repo)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Source health: branch=DETACHED", result.stderr)
        self.assertIn("upstream=none", result.stderr)

    def test_install_sh_source_health_warns_on_tracked_dirty_repo_by_default(self) -> None:
        repo = self._make_repo()
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")

        result = self._run_bash_source_health(repo)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("tracked local changes", result.stderr)
        self.assertIn("continuing", result.stderr)
        self.assertIn("pending-merges", result.stderr)

    def test_install_sh_source_health_allows_untracked_files(self) -> None:
        repo = self._make_repo()
        (repo / "untracked.txt").write_text("local note\n", encoding="utf-8")

        result = self._run_bash_source_health(repo)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("untracked", result.stderr)

    def test_install_sh_source_health_warns_on_diverged_upstream_with_local_commits(self) -> None:
        repo, remote = self._make_repo_with_upstream()
        self._add_commit(repo, "local change", "local\n")

        other = remote.parent / "other"
        subprocess.run(
            ["git", "clone", str(remote), str(other)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(other, "config", "user.email", "tests@example.invalid")
        _git(other, "config", "user.name", "Tests")
        self._add_commit(other, "upstream change", "upstream\n")
        _git(other, "push", "origin", "HEAD")
        _git(repo, "fetch", "origin")

        result = self._run_bash_source_health(repo)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("diverged from its upstream", result.stderr)
        self.assertIn("continuing because local commits may be intentional", result.stderr)

    def test_install_sh_source_health_blocks_behind_upstream(self) -> None:
        repo, remote = self._make_repo_with_upstream()

        other = remote.parent / "other"
        subprocess.run(
            ["git", "clone", str(remote), str(other)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(other, "config", "user.email", "tests@example.invalid")
        _git(other, "config", "user.name", "Tests")
        self._add_commit(other, "upstream change", "upstream\n")
        _git(other, "push", "origin", "HEAD")
        _git(repo, "fetch", "origin")

        result = self._run_bash_source_health(repo)

        self.assertNotEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("behind its upstream", result.stderr)
        self.assertIn("git fetch", result.stderr)

    def test_install_sh_update_source_stashes_dirty_checkout_before_fast_forward(self) -> None:
        repo, remote = self._make_repo_with_upstream()

        other = remote.parent / "other"
        subprocess.run(
            ["git", "clone", str(remote), str(other)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(other, "config", "user.email", "tests@example.invalid")
        _git(other, "config", "user.name", "Tests")
        self._add_commit(other, "upstream change", "upstream\n")
        _git(other, "push", "origin", "HEAD")

        (repo / "tracked.txt").write_text("local dirty edit\n", encoding="utf-8")
        (repo / "local-note.txt").write_text("keep me\n", encoding="utf-8")

        result = self._run_bash_source_update(repo)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual((repo / "tracked.txt").read_text(encoding="utf-8"), "upstream\n")
        self.assertFalse((repo / "local-note.txt").exists())
        stash_list = subprocess.run(
            ["git", "-C", str(repo), "stash", "list"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("ghost-alice source update backup", stash_list)
        self.assertIn("Source local changes saved in git stash", result.stderr)
        self.assertIn("stash show -p", result.stderr)
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout,
            subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "origin/main"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout,
        )

    def test_bootstrap_source_update_rescues_dirty_old_checkout_without_local_install_sh(self) -> None:
        if not self.bash:
            self.skipTest("No non-WSL bash executable available for bootstrap source update test")
        if not self.git:
            self.skipTest("git executable is required for bootstrap source update tests")

        repo, remote = self._make_repo_with_upstream()

        other = remote.parent / "other"
        subprocess.run(
            ["git", "clone", str(remote), str(other)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(other, "config", "user.email", "tests@example.invalid")
        _git(other, "config", "user.name", "Tests")
        self._add_commit(other, "upstream change", "upstream\n")
        _git(other, "push", "origin", "HEAD")

        (repo / "tracked.txt").write_text("local dirty edit\n", encoding="utf-8")
        (repo / "local-note.txt").write_text("keep me\n", encoding="utf-8")

        raw_pull = subprocess.run(
            ["git", "-C", str(repo), "pull", "--ff-only"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(raw_pull.returncode, 0, msg=raw_pull.stdout + raw_pull.stderr)
        self.assertEqual((repo / "tracked.txt").read_text(encoding="utf-8"), "local dirty edit\n")

        result = subprocess.run(
            [
                self.bash,
                str(BOOTSTRAP_SOURCE_UPDATE),
                "--source-dir",
                str(repo),
                "--no-install",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertEqual((repo / "tracked.txt").read_text(encoding="utf-8"), "upstream\n")
        self.assertFalse((repo / "local-note.txt").exists())
        stash_list = subprocess.run(
            ["git", "-C", str(repo), "stash", "list"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("ghost-alice source update backup", stash_list)
        self.assertIn("Source local changes saved in git stash", result.stderr)
        self.assertIn("stash show -p", result.stderr)
        self.assertIn("Install step skipped", result.stderr)

    def test_bootstrap_source_update_can_run_from_fetched_remote_blob(self) -> None:
        if not self.bash:
            self.skipTest("No non-WSL bash executable available for bootstrap source update test")
        if not self.git:
            self.skipTest("git executable is required for bootstrap source update tests")

        repo, remote = self._make_repo_with_upstream()
        script_relpath = "scripts/bootstrap-source-update.sh"
        script_source = BOOTSTRAP_SOURCE_UPDATE.read_text(encoding="utf-8")

        maintainer = remote.parent / "maintainer"
        subprocess.run(
            ["git", "clone", str(remote), str(maintainer)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(maintainer, "config", "user.email", "tests@example.invalid")
        _git(maintainer, "config", "user.name", "Tests")
        (maintainer / "scripts").mkdir()
        (maintainer / script_relpath).write_text(script_source, encoding="utf-8")
        _git(maintainer, "add", script_relpath)
        _git(maintainer, "commit", "-m", "add bootstrap updater")
        _git(maintainer, "push", "origin", "HEAD")

        other = remote.parent / "other"
        subprocess.run(
            ["git", "clone", str(remote), str(other)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(other, "config", "user.email", "tests@example.invalid")
        _git(other, "config", "user.name", "Tests")
        self._add_commit(other, "upstream change", "upstream\n")
        _git(other, "push", "origin", "HEAD")

        (repo / "tracked.txt").write_text("local dirty edit\n", encoding="utf-8")
        (repo / "local-note.txt").write_text("keep me\n", encoding="utf-8")

        raw_pull = subprocess.run(
            ["git", "-C", str(repo), "pull", "--ff-only"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(raw_pull.returncode, 0, msg=raw_pull.stdout + raw_pull.stderr)

        command = (
            f'cd "{repo.as_posix()}" && '
            "git fetch origin main && "
            "git show FETCH_HEAD:scripts/bootstrap-source-update.sh | "
            f'"{self.bash}" -s -- --source-dir "{repo.as_posix()}" --no-install'
        )
        result = subprocess.run(
            [self.bash, "-lc", command],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertEqual((repo / "tracked.txt").read_text(encoding="utf-8"), "upstream\n")
        self.assertFalse((repo / "local-note.txt").exists())
        self.assertIn("Source local changes saved in git stash", result.stderr)
        self.assertIn("Install step skipped", result.stderr)

    def test_installer_update_guidance_uses_safe_source_update_entrypoints(self) -> None:
        bash = installer_bash_source()
        ps1 = installer_ps1_source()

        self.assertIn("bash install.sh --update-source", bash)
        self.assertIn(".\\install.cmd -UpdateSource", ps1)
        self.assertNotIn("To update skills: cd ${SCRIPT_DIR} && git pull --ff-only", bash)
        self.assertNotIn(
            "With symlink install, git pull --ff-only automatically updates skills.",
            bash,
        )
        self.assertNotIn("To update skills: cd $ScriptDir; git pull --ff-only", ps1)
        self.assertNotIn(
            "Junction installs update automatically after git pull --ff-only.",
            ps1,
        )

    def test_install_ps1_declares_safe_source_update_entrypoint(self) -> None:
        body = installer_ps1_source()
        source_update_body = _extract_powershell_function(body, "Update-SourceCheckout")

        self.assertIn("[switch]$UpdateSource", body)
        self.assertIn("git stash push -u", source_update_body)
        self.assertIn("pull --ff-only", source_update_body)
        self.assertIn("Source local changes saved in git stash", source_update_body)
        self.assertIn("if ($UpdateSource)", body)
        self.assertLess(body.index("if ($UpdateSource)"), body.index("Initialize-GitHooks"))

    def test_install_sh_calls_source_health_before_copying_targets(self) -> None:
        source = installer_bash_source()
        install_body = _extract_bash_function(source, "install")

        self.assertNotIn("ALLOW_DIRTY_SOURCE", source)
        self.assertNotIn("--allow-dirty-source", source)

        self.assertLess(install_body.index("check_source_health"), install_body.index("install_shared"))

    def test_install_ps1_declares_source_health_gate(self) -> None:
        body = installer_ps1_source()
        install_body = _extract_powershell_function(body, "Invoke-Install")
        source_health_body = _extract_powershell_function(body, "Test-SourceHealth")

        self.assertNotIn("AllowDirtySource", body)
        self.assertIn("[switch]$SkipSourceHealth", body)
        self.assertIn("function Test-SourceHealth", body)
        self.assertNotIn("AllowDirtySource", source_health_body)
        self.assertIn("rev-list --left-right --count", source_health_body)
        self.assertIn("continuing because local commits may be intentional", source_health_body)
        self.assertIn("pending-merges", source_health_body)
        self.assertNotIn("throw \"Source tree has tracked local changes\"", source_health_body)
        self.assertLess(install_body.index("Test-SourceHealth"), install_body.index("Install-Shared -SkillsRoot $SkillsDir"))

    def test_install_sh_omits_legacy_repo_migration(self) -> None:
        body = installer_bash_source()

        self.assertNotIn("LEGACY_REPO_NAMES", body)
        self.assertNotIn("REPO_RENAME_MIGRATION_ONLY", body)
        self.assertNotIn("LOCAL_CHECKOUT_RENAME_ONLY", body)
        self.assertNotIn("LOCAL_CHECKOUT_RENAMED_TO", body)
        self.assertNotIn("is_legacy_checkout_basename", body)
        self.assertNotIn("local_checkout_rename", body)
        self.assertNotIn("convert_legacy_repo_url", body)
        self.assertNotIn("update_notice", body)
        self.assertNotIn("repo_remote_migration", body)
        self.assertNotIn("--repo-rename-migration-only", body)
        self.assertNotIn("--rename-local-checkout", body)

    def test_post_merge_hook_does_not_run_brand_migration(self) -> None:
        hook = (ROOT / "hooks" / "post-merge").read_text(encoding="utf-8")

        self.assertNotIn("--repo-rename-migration-only", hook)
        self.assertNotIn("LOCAL_CHECKOUT_RENAMED_TO=", hook)
        self.assertNotIn("repo rename migration", hook)
        self.assertIn("Codex copy-mode install detected", hook)

    def test_install_ps1_omits_legacy_repo_migration(self) -> None:
        body = INSTALL_PS1.read_text(encoding="utf-8-sig")

        self.assertNotIn("[switch]$RepoRenameMigrationOnly", body)
        self.assertNotIn("[switch]$RenameLocalCheckout", body)
        self.assertNotIn("$script:LegacyRepoNames", body)
        self.assertNotIn("$script:LocalCheckoutRenamedTo", body)
        self.assertNotIn("function Test-LegacyCheckoutBasename", body)
        self.assertNotIn("LocalCheckoutRename", body)
        self.assertNotIn("function Convert-LegacyRepoUrl", body)
        self.assertNotIn("UpdateNotice", body)
        self.assertNotIn("RepoMigration", body)
        self.assertNotIn("-RepoRenameMigrationOnly", body)
        self.assertNotIn("-RenameLocalCheckout", body)
        self.assertNotIn("Move-Item -LiteralPath $ScriptDir", body)
        self.assertIn("Ghost-ALICE", body)


class HarnessAuditSourceHealthTest(unittest.TestCase):
    def make_skill(self, repo: Path, name: str, description: str | None = None) -> None:
        skill_dir = repo / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        desc = description or f"{name} test skill trigger."
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: \"{desc}\"\n---\n\n# {name}\n\n## Notice\n\nThis is a test skill.\n",
            encoding="utf-8",
        )

    def write_installers(self, repo: Path, skills: list[str]) -> None:
        (repo / "install.sh").write_text(
            "ALL_SKILLS=(\n" + "".join(f"  {name}\n" for name in skills) + ")\n",
            encoding="utf-8",
        )
        (repo / "install.ps1").write_text(
            "$AllSkills = @(\n" + "".join(f'    "{name}"\n' for name in skills) + ")\n",
            encoding="utf-8-sig",
        )

    def test_install_list_and_catalog_mismatch_is_detected(self) -> None:
        from validate_skills import Issue, validate_install_list_sync

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.make_skill(repo, "skill-a")
            self.make_skill(repo, "skill-b")
            self.write_installers(repo, ["skill-a", "unknown-skill"])

            issues: list[Issue] = []
            validate_install_list_sync(repo, issues)

        messages = [issue.message for issue in issues]
        self.assertTrue(any("skill-b" in message and "install.sh" in message for message in messages))
        self.assertTrue(any("unknown-skill" in message and "catalog" in message for message in messages))

    def test_duplicate_description_token_collision_warns(self) -> None:
        from validate_skills import Issue, validate_description_collisions

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            desc = "repeat work pattern analysis report evidence workflow sequence trigger"
            self.make_skill(repo, "skill-a", desc)
            self.make_skill(repo, "skill-b", desc)

            issues: list[Issue] = []
            validate_description_collisions(repo, issues)

        self.assertTrue(any(issue.rule == "description.collision" for issue in issues))

    def test_long_reference_without_toc_is_error(self) -> None:
        from validate_skills import Issue, validate_phase4_references

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.make_skill(repo, "skill-a")
            ref_dir = repo / "skill-a" / "references"
            ref_dir.mkdir()
            (ref_dir / "long-reference.md").write_text(
                "# Long Reference\n\n" + "\n".join(f"line {i}" for i in range(301)) + "\n",
                encoding="utf-8",
            )

            issues: list[Issue] = []
            validate_phase4_references(repo / "skill-a" / "SKILL.md", issues)

        self.assertTrue(
            any(issue.rule == "4-1" and issue.severity == "ERROR" for issue in issues),
            msg=[issue.to_dict() for issue in issues],
        )

    def test_live_direct_skill_directory_is_detected(self) -> None:
        from install_doctor import _skill_layout_audit

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            live = root / "home" / ".claude" / "skills"
            self.make_skill(repo, "skill-a")
            (live / "skill-a").mkdir(parents=True)
            (live / "skill-a" / "SKILL.md").write_text(
                "---\nname: skill-a\ndescription: test\n---\n",
                encoding="utf-8",
            )

            findings = _skill_layout_audit(live, repo)

        rendered = json.dumps(findings, ensure_ascii=False)
        self.assertIn("skill-a", rendered)
        self.assertIn("direct-directory", rendered)


if __name__ == "__main__":
    unittest.main()
