"""Shared fixtures for the `installer` tests: a fake source checkout, its `REPO_ROOT` patch, a Context, a recording
runner, and a copy of this checkout in its own repository for a test that runs the real installer."""
from __future__ import annotations

import atexit
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import tempfile
from typing import Callable
import unittest
from unittest import mock

from git_hook_paths import git, hook_paths
from installer import cli, report
from installer.runner import ToolResult

CHECKOUT = Path(__file__).resolve().parents[1]
_copy_template: Path | None = None


def make_source(root: Path) -> Path:
    """A minimal checkout: `_shared`, skill `alpha`, family `fam/{one,two}`, catalog, platform bootstraps."""
    (root / "_shared").mkdir(parents=True)
    (root / "_shared" / "encoding_guard.py").write_text("", encoding="utf-8")
    (root / "_shared" / "install_hooks.py").write_text("", encoding="utf-8")
    (root / "alpha").mkdir()
    (root / "alpha" / "SKILL.md").write_text('---\nname: alpha\ndescription: "Alpha does one thing."\n---\n', encoding="utf-8")
    for sub in ("one", "two"):
        (root / "fam" / sub).mkdir(parents=True)
        (root / "fam" / sub / "SKILL.md").write_text(f"---\nname: {sub}\ndescription: {sub} skill\n---\n", encoding="utf-8")
    (root / "skill-catalog").mkdir()
    (root / "skill-catalog" / "session-gates.json").write_text("{}", encoding="utf-8")
    (root / "skill-catalog" / "skills.json").write_text("{}", encoding="utf-8")
    (root / "platforms" / "claude").mkdir(parents=True)
    (root / "platforms" / "claude" / "CLAUDE.md").write_text("# Ghost-ALICE Claude Bootstrap\n", encoding="utf-8")
    (root / "platforms" / "codex").mkdir(parents=True)
    (root / "platforms" / "codex" / "AGENTS.md").write_text("# Ghost-ALICE Codex Bootstrap\n", encoding="utf-8")
    (root / "platforms" / "codex" / "bootstrap.md").write_text("loader\n", encoding="utf-8")
    return root


# PowerShell keeps its own data, cache and config under HOME (XDG locations on Linux and macOS).
POWERSHELL_HOME_STATE = (".local", ".cache", ".config")


def installer_home_effects(home: Path) -> list[str]:
    """Entries under `home` other than PowerShell's own startup state: what an installer run left there."""
    return sorted(p.name for p in Path(home).iterdir() if p.name not in POWERSHELL_HOME_STATE)


def use_source(root: Path):
    """Run the installer from the fixture checkout `root`: patch `installer.runner.REPO_ROOT`,
    the one name of the checkout the installer runs from."""
    return mock.patch("installer.runner.REPO_ROOT", Path(root))


def make_context(home: Path, platform: str = "claude", **options) -> cli.Context:
    opts = cli.Options(platform=platform, **options)
    skills_dir = home / ".claude" / "skills" if platform == "claude" else home / ".agents" / "skills"
    return cli.Context(options=opts, platform=platform, skills_dir=skills_dir, addon_sources=list(opts.addon_sources))


class FakeRunner:
    """Records every `runner.run_tool` / `runner.run_command` call and answers with scripted results."""

    def __init__(self):
        self.calls: list[tuple[str, list[str], dict]] = []
        self.responses: list[tuple[Callable[[str, list[str]], bool], ToolResult | Callable]] = []

    def respond(self, matcher, result):
        """`matcher(script, args) -> bool`; `result` is a ToolResult or a callable returning one."""
        self.responses.append((matcher, result))

    def respond_to(self, script_suffix: str, result: ToolResult, *, arg: str | None = None):
        self.respond(lambda script, args: script.endswith(script_suffix) and (arg is None or arg in args), result)

    def _answer(self, script: str, args: list[str], kwargs: dict) -> ToolResult:
        for matcher, result in self.responses:
            if matcher(script, args):
                return result(script, args, kwargs) if callable(result) else result
        return ToolResult(0, "" if kwargs.get("capture") else None, "" if kwargs.get("capture") else None)

    def run_tool(self, script, *args, **kwargs):
        args = [str(a) for a in args]
        self.calls.append((script, args, kwargs))
        return self._answer(script, args, kwargs)

    def run_command(self, argv, **kwargs):
        argv = [str(a) for a in argv]
        self.calls.append(("command", argv, kwargs))
        return self._answer("command", argv, kwargs)

    def tool_calls(self, script_suffix: str) -> list[list[str]]:
        return [args for script, args, _ in self.calls if script != "command" and script.endswith(script_suffix)]

    def commands(self) -> list[list[str]]:
        return [args for script, args, _ in self.calls if script == "command"]

    def patch(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(mock.patch("installer.runner.run_tool", self.run_tool))
        stack.enter_context(mock.patch("installer.runner.run_command", self.run_command))
        return stack


def quiet(home: Path, **kwargs) -> None:
    report.configure(verbose=kwargs.pop("verbose", False), report_child=kwargs.pop("report_child", False),
                     tty=False, progress_env="off", home=home, **kwargs)


def _template() -> Path:
    """This checkout's working tree as git sees it (tracked and untracked files that are not ignored), committed once
    in a new repository; made once per test process and removed when the process ends."""
    global _copy_template
    if _copy_template is None:
        root = Path(tempfile.mkdtemp(prefix="ghost-alice-checkout-"))
        atexit.register(shutil.rmtree, root, True)
        template = root / "ghost-alice"
        template.mkdir()
        listing = git("-C", str(CHECKOUT), "ls-files", "-z", "--cached", "--others", "--exclude-standard", check=True)
        for name in filter(None, listing.stdout.split("\0")):
            source, copy = CHECKOUT / name, template / name
            if not (source.is_symlink() or source.is_file()):
                continue
            copy.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                copy.symlink_to(os.readlink(source))
            else:
                shutil.copy2(source, copy)
        # No background maintenance: it writes into .git while a test removes the copy.
        settings = ("-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                    "-c", "user.name=Ghost-ALICE tests", "-c", "user.email=tests@example.invalid")
        for args in (("init", "-q", "-b", "main"), ("config", "maintenance.auto", "false"), ("config", "gc.auto", "0"),
                     ("add", "-A"), ("commit", "-q", "-m", "Checkout copy for tests")):
            git("-C", str(template), *settings, *args, check=True)
        _copy_template = template
    return _copy_template


def checkout_copy(test: unittest.TestCase) -> Path:
    """A copy of this checkout in its own repository, for a test that runs the real installer from it; removed when
    the test ends.

    The install writes the git hook path of the repository it runs from and the full uninstall restores it, so a test
    that installs from this checkout would change the developer's repository and every worktree that inherits its
    configuration. The copy holds the working tree as it is now, committed once on `main`. When the test ends, it
    fails if the `core.hooksPath` values of this checkout changed (`git_hook_paths.hook_paths`)."""
    before = hook_paths(CHECKOUT)
    parent = Path(tempfile.mkdtemp(prefix="ghost-alice-source-"))
    test.addCleanup(shutil.rmtree, parent, True)
    copy = parent / "ghost-alice"
    shutil.copytree(_template(), copy, symlinks=True)

    def hook_paths_unchanged() -> None:
        after = hook_paths(CHECKOUT)
        if after != before:
            raise AssertionError(f"The test changed core.hooksPath of {CHECKOUT}: {before} -> {after}")

    test.addCleanup(hook_paths_unchanged)
    return copy.resolve()
