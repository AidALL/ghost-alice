"""Shared fixtures for the `installer` tests: a fake source checkout, a Context and a recording runner."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Callable
from unittest import mock

from installer import cli, report
from installer.runner import ToolResult


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


def make_context(source: Path, home: Path, platform: str = "claude", **options) -> cli.Context:
    opts = cli.Options(platform=platform, **options)
    skills_dir = home / ".claude" / "skills" if platform == "claude" else home / ".agents" / "skills"
    return cli.Context(options=opts, source_root=source, platform=platform, skills_dir=skills_dir,
                       addon_sources=list(opts.addon_sources))


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
