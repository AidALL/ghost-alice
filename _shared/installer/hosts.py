"""What the installer registers in the host: hooks, managed rule blocks and command wrappers.

Port of `installer_lib/install.sh:5-26, 783-847`, `installer_lib/bootstrap.sh:12-152`
and `installer_lib/platform.sh:59-61`. A later change splits this into one
module per host.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from installer import locations, report, runner

if TYPE_CHECKING:
    from installer.cli import Context

CODEX_BOOTSTRAP_MARKER = "# Ghost-ALICE Codex Bootstrap"
CODEX_MANAGED_BLOCK_BEGIN = "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->"
CODEX_MANAGED_BLOCK_END = "<!-- Ghost-ALICE managed block end: codex-bootstrap -->"
CLAUDE_BOOTSTRAP_MARKER = "# Ghost-ALICE Claude Bootstrap"
CLAUDE_MANAGED_BLOCK_BEGIN = "<!-- Ghost-ALICE managed block begin: claude-bootstrap -->"
CLAUDE_MANAGED_BLOCK_END = "<!-- Ghost-ALICE managed block end: claude-bootstrap -->"
HOOK_ACTION_LABEL = "session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace"
GLOBAL_RULE_BLOCKS = "_shared/global_rule_blocks.py"


def codex_bootstrap_source(source_root: Path) -> Path:
    return Path(source_root) / "platforms" / "codex" / "AGENTS.md"


def codex_loader_source(source_root: Path) -> Path:
    return Path(source_root) / "platforms" / "codex" / "bootstrap.md"


def claude_bootstrap_source(source_root: Path) -> Path:
    return Path(source_root) / "platforms" / "claude" / "CLAUDE.md"


def codex_hooks_supported(platform: str) -> bool:
    """Always true (`platform.sh:59-61`)."""
    return True


def install_hooks(ctx: "Context", action: str, platform: str) -> bool:
    """`install_hooks.py --core-source-root --platform --hook-shared-dir --skills-dir
    [--visibility] [--uninstall|--status] [--addon-source ...]`; install
    defaults visibility to `dynamic`; status and doctor pass no visibility
    (`install.sh:783-847`; status and doctor pass no visibility)."""
    report.emit("")
    report.info(f"Agent hook {action}... ({HOOK_ACTION_LABEL})")
    if not codex_hooks_supported(platform):
        report.info("Codex hooks are unavailable in this runtime; skipping.")
        return True
    hook_py = Path(ctx.source_root) / "_shared" / "install_hooks.py"
    if not hook_py.is_file():
        report.error(f"install_hooks.py not found: {hook_py}")
        return False
    args = ["--core-source-root", str(ctx.source_root), "--platform", platform,
            "--hook-shared-dir", str(locations.runtime_shared_dir()), "--skills-dir", str(ctx.skills_dir)]
    if action == "install":
        args.extend(["--visibility", ctx.options.visibility or "dynamic"])
    elif action == "uninstall":
        args.append("--uninstall")
    elif action == "status":
        args.append("--status")
    if action != "uninstall":
        for addon_source in ctx.addon_sources:
            args.extend(["--addon-source", str(addon_source)])
    if not runner.run_tool("_shared/install_hooks.py", *args).ok:
        report.error(f"Hook {action} failed")
        return False
    return True


def _merge_block(args: list[str], failure: str, proposed_prefix: str, updated: str) -> bool:
    result = runner.run_tool(GLOBAL_RULE_BLOCKS, *args, capture=True)
    if result.stderr:
        report.emit(result.stderr.rstrip("\n"))
    if not result.ok:
        report.error(failure)
        return False
    output = (result.stdout or "").strip()
    if output.startswith("proposed:"):
        report.warn(f"{proposed_prefix}: {output[len('proposed:'):]}")
    else:
        report.ok(updated)
    return True


def ensure_claude_bootstrap(ctx: "Context") -> bool:
    """`global_rule_blocks.py claude-merge --source --dest --proposed`; `proposed:` → warning (`bootstrap.sh:12-38`)."""
    claude_home = locations.claude_home()
    rules = claude_home / "CLAUDE.md"
    claude_home.mkdir(parents=True, exist_ok=True)
    return _merge_block(["claude-merge", "--source", str(claude_bootstrap_source(ctx.source_root)),
                         "--dest", str(rules), "--proposed", f"{rules}.ghost-alice-proposed"],
                        "Claude CLAUDE.md block merge failed; aborting install",
                        "Claude CLAUDE.md is user-owned; wrote proposed file instead",
                        "Claude CLAUDE.md bootstrap block updated")


def ensure_codex_bootstrap(ctx: "Context") -> bool:
    """`global_rule_blocks.py codex-merge --source --loader --dest --proposed` (`bootstrap.sh:86-112`)."""
    codex_home = locations.codex_home()
    agents = codex_home / "AGENTS.md"
    codex_home.mkdir(parents=True, exist_ok=True)
    return _merge_block(["codex-merge", "--source", str(codex_bootstrap_source(ctx.source_root)),
                         "--loader", str(codex_loader_source(ctx.source_root)),
                         "--dest", str(agents), "--proposed", f"{agents}.ghost-alice-proposed"],
                        "Codex AGENTS.md block merge failed; aborting install",
                        "Codex AGENTS.md is user-owned; wrote proposed file instead",
                        "Codex AGENTS.md bootstrap block updated")


def _remove_block(args: list[str], failure: str, removed: str, updated: str, require_change: bool) -> bool:
    result = runner.run_tool(GLOBAL_RULE_BLOCKS, *args, capture=True)
    if result.stderr:
        report.emit(result.stderr.rstrip("\n"))
    if not result.ok:
        report.warn(failure)
        return False
    output = (result.stdout or "").strip()
    if output.startswith("removed:"):
        report.ok(removed)
        return True
    if output.startswith("updated:"):
        report.ok(updated)
        return True
    return not require_change


def remove_claude_bootstrap(ctx: "Context", require_change: bool = False) -> bool:
    """`global_rule_blocks.py claude-remove --dest`; result prefixes decide the message (`bootstrap.sh:40-76`)."""
    rules = locations.claude_home() / "CLAUDE.md"
    if not rules.is_file():
        return not require_change
    return _remove_block(["claude-remove", "--dest", str(rules)], "Claude CLAUDE.md block removal failed",
                         "Claude CLAUDE.md bootstrap removed",
                         "Claude CLAUDE.md Ghost-ALICE block removed; user content preserved", require_change)


def remove_claude_bootstrap_if_unused(ctx: "Context", skills_root: Path) -> bool:
    from installer import selection

    if selection.has_managed_installs(ctx.source_root, skills_root):
        return False
    return remove_claude_bootstrap(ctx, True)


def remove_codex_bootstrap_if_unused(ctx: "Context", skills_root: Path) -> bool:
    from installer import selection

    if selection.has_managed_installs(ctx.source_root, skills_root):
        return False
    codex_home = locations.codex_home()
    agents = codex_home / "AGENTS.md"
    if not agents.is_file() and not (codex_home / "ghost-alice-governance.md").is_file():
        return False
    return _remove_block(["codex-remove", "--dest", str(agents)], "Codex AGENTS.md block removal failed",
                         "Codex AGENTS.md bootstrap removed",
                         "Codex AGENTS.md Ghost-ALICE block removed; user content preserved", True)


def sync_commands(ctx: "Context", mode: str = "sync", target: str = "all") -> int:
    """`commands_sync.py <catalog> <checkout .claude/commands> <mode> <target>`; a missing catalog is a warning (`install.sh:5-26`)."""
    catalog = Path(ctx.source_root) / "skill-catalog" / "skills.json"
    if not catalog.is_file():
        report.warn(f"skills.json not found: {catalog}")
        return 1
    claude_dir = Path(ctx.source_root) / ".claude" / "commands"
    return runner.run_tool("_shared/commands_sync.py", str(catalog), str(claude_dir), mode, target).returncode
