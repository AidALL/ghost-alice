#!/usr/bin/env python3
"""Static validation for the pending-merge/session-start layered gate.

Statically checks whether the pending-merge self-check remains present in hooks,
bootstrap, task-router, and install guidance. Exits 1 when any check fails. CI
uses this as a regression gate.

Usage:
  python3 scripts/validate_pending_merge_layering.py        # full pending-merge layer check
  python3 scripts/validate_pending_merge_layering.py --json # JSON output for CI
"""
from __future__ import annotations
import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _stdout_can_encode(text: str) -> bool:
    encoding = sys.stdout.encoding or "utf-8"
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


def _safe_for_stdout(text: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    if not _stdout_can_encode(text):
        text = text.translate({8212: ord("-")})
    return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def _print_safe(text: str = "") -> None:
    print(_safe_for_stdout(text))


def _status_symbol(ok: bool) -> str:
    if _stdout_can_encode("✓✗"):
        return "✓" if ok else "✗"
    return "OK" if ok else "FAIL"


@dataclass
class LayerCheck:
    layer: str
    description: str
    file: Path
    pattern: str
    multiline: bool = False
    must_exist: bool = True


CHECKS: list[LayerCheck] = [
    LayerCheck(
        layer="pending-merge-session-start",
        description="install_hooks.py defines SessionStart hook constants",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"SESSION_START_MARKER\s*=",
    ),
    LayerCheck(
        layer="pending-merge-session-start",
        description="install_hooks.py maps on_session_start intent",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"['\"]on_session_start['\"]",
    ),
    LayerCheck(
        layer="pending-merge-user-prompt",
        description="install_hooks.py creates the prompt-submit pending-merge precheck command",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"HOOK_COMMAND\s*=\s*_hook_reminder_command\(",
    ),
    LayerCheck(
        layer="pending-merge-user-prompt",
        description="install_hooks.py HOOK_COMMAND body contains the word 'merge-companion'",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"merge-companion",
    ),
    LayerCheck(
        layer="pending-merge-prose-rule",
        description="AGENTS.md contains the 0-A merge-companion self-check gate",
        file=REPO / "AGENTS.md",
        pattern=r"0-A.*merge-companion",
    ),
    LayerCheck(
        layer="pending-merge-task-router-precheck",
        description="task-router/SKILL.md includes the pending-merge precheck stage",
        file=REPO / "task-router" / "SKILL.md",
        pattern=r"1\.0\s+Pending-Merge\s+Precheck",
    ),
    LayerCheck(
        layer="pending-merge-bootstrap-self-check",
        description="platforms/codex/AGENTS.md contains the self-check section",
        file=REPO / "platforms" / "codex" / "AGENTS.md",
        pattern=r"Pending\s+Skill\s+Merge\s+Self-Check",
    ),
    LayerCheck(
        layer="pending-merge-install-tail",
        description="install.sh tail contains localized User/Tech backup-review guidance",
        file=REPO / "install.sh",
        pattern=r"User:.*Please\s+review\s+backed-up\s+changes",
        multiline=True,
    ),
    LayerCheck(
        layer="pending-merge-install-tail",
        description="install.ps1 tail contains localized User/Tech backup-review guidance",
        file=REPO / "install.ps1",
        pattern=r"User:.*Please\s+review\s+backed-up\s+changes",
        multiline=True,
    ),
    LayerCheck(
        layer="pending-merge-install-tail",
        description="install.sh tail does not contain old Korean control copy",
        file=REPO / "install.sh",
        pattern=r"review\s+whether\s+unprocessed\s+pending\s+merges\s+remain\s+after\s+skill\s+updates",
        must_exist=False,
    ),
    LayerCheck(
        layer="pending-merge-install-tail",
        description="install.ps1 tail does not contain old Korean control copy",
        file=REPO / "install.ps1",
        pattern=r"review\s+whether\s+unprocessed\s+pending\s+merges\s+remain\s+after\s+skill\s+updates",
        must_exist=False,
    ),
    LayerCheck(
        layer="pending-merge-readme",
        description="diff_collector.py defines _write_readme_first",
        file=REPO / "merge-companion" / "scripts" / "diff_collector.py",
        pattern=r"def\s+_write_readme_first\s*\(",
    ),
    LayerCheck(
        layer="pending-merge-session-start-failsafe",
        description="install_hooks.py creates the SessionStart pending-merge precheck command",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"SESSION_START_COMMAND\s*=\s*_session_start_command\(",
    ),
    LayerCheck(
        layer="WEB-SEARCH-FIRST",
        description="install_hooks.py defines WEB_SEARCH_FIRST_MARKER (web-search-guard-layer-1)",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"WEB_SEARCH_FIRST_MARKER\s*=",
    ),
    LayerCheck(
        layer="WEB-SEARCH-FIRST",
        description="install_hooks.py WEB_SEARCH_FIRST_INTERNAL body contains WebSearch",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"WEB_SEARCH_FIRST_INTERNAL[\s\S]+?WebSearch",
        multiline=True,
    ),
    LayerCheck(
        layer="WEB-SEARCH-FIRST",
        description="AGENTS.md contains the external-tool web-search-first duty (web-search-guard-layer-2)",
        file=REPO / "AGENTS.md",
        pattern=r"### 10\..*Web Search",
    ),
    LayerCheck(
        layer="WEB-SEARCH-FIRST",
        description="adversarial-verification SKILL.md includes the external-tool web-search-first injection section (web-search-guard-layer-3)",
        file=REPO / "adversarial-verification" / "SKILL.md",
        pattern=r"External Tool Claim Web Search",
    ),
    LayerCheck(
        layer="WEB-SEARCH-FIRST",
        description="verification-before-completion SKILL.md contains the external-tool web-search-first gate section (web-search-guard-layer-4)",
        file=REPO / "coding-convention" / "verification-before-completion" / "SKILL.md",
        pattern=r"Rule 10|web.?search.?first|external tool claim",
    ),
    LayerCheck(
        layer="EVAL",
        description="evaluator artifact contract document exists and mentions verifier-result.json",
        file=REPO / "docs" / "policies" / "evaluator-artifact-contract.md",
        pattern=r"scenario\.json[\s\S]+trace\.json[\s\S]+report\.json[\s\S]+candidate-playbook\.md[\s\S]+verifier-result\.json",
        multiline=True,
    ),
    LayerCheck(
        layer="EVAL",
        description="adversarial-verification references the evaluator artifact contract",
        file=REPO / "adversarial-verification" / "SKILL.md",
        pattern=r"evaluator-artifact-contract\.md[\s\S]+verifier-result\.json",
        multiline=True,
    ),
    LayerCheck(
        layer="EVAL",
        description="verification-before-completion references the evaluator artifact contract",
        file=REPO / "coding-convention" / "verification-before-completion" / "SKILL.md",
        pattern=r"evaluator-artifact-contract\.md[\s\S]+verifier-result\.json",
        multiline=True,
    ),
]


def check_layer(c: LayerCheck) -> tuple[bool, str]:
    if not c.file.exists():
        return False, f"{c.layer}: file missing. {c.file}"
    text = c.file.read_text(encoding="utf-8")
    if c.file.name in ("install.sh", "install.ps1"):
        # install.sh / install.ps1 are modularized; tail/installer content may now live
        # in installer_lib/*.sh and installer_lib/*.ps1. Scan the combined installer
        # source so install.sh / install.ps1 layer checks find the content.
        ext = ".sh" if c.file.name == "install.sh" else ".ps1"
        lib_dir = c.file.parent / "installer_lib"
        if lib_dir.is_dir():
            for lib in sorted(lib_dir.glob(f"*{ext}")):
                text += "\n" + lib.read_text(encoding="utf-8")
    flags = re.MULTILINE | re.DOTALL if c.multiline else re.MULTILINE
    found = re.search(c.pattern, text, flags) is not None
    if c.must_exist and not found:
        return False, f"{c.layer}: pattern not found. {c.description} ({c.file.relative_to(REPO)})"
    if not c.must_exist and found:
        return False, f"{c.layer}: forbidden pattern found. {c.description} ({c.file.relative_to(REPO)})"
    return True, f"{c.layer}: OK. {c.description}"


def _function_source(text: str, function_name: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(text, node) or ""
    return ""


def check_session_start_failsafe() -> tuple[bool, str]:
    """Check whether the SessionStart helper keeps the failsafe that swallows hook failure."""
    f = REPO / "_shared" / "install_hooks.py"
    if not f.exists():
        return False, "pending-merge-session-start-failsafe: install_hooks.py missing"
    text = f.read_text(encoding="utf-8")
    body = _function_source(text, "_session_start_command")
    if not body:
        return False, "pending-merge-session-start-failsafe: _session_start_command definition not found"
    if "|| true" not in body:
        return False, "pending-merge-session-start-failsafe: SessionStart helper does not include the '|| true' failsafe"
    if "SESSION_START_MARKER" not in body:
        return False, "pending-merge-session-start-failsafe: SessionStart helper does not attach SESSION_START_MARKER to the command"
    return True, "pending-merge-session-start-failsafe: SessionStart helper failsafe confirmed"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true", help="Print JSON output")
    args = p.parse_args()

    results = []
    failures = []

    for c in CHECKS:
        ok, msg = check_layer(c)
        results.append({"layer": c.layer, "ok": ok, "msg": msg})
        if not ok:
            failures.append(msg)

    ok, msg = check_session_start_failsafe()
    results.append({"layer": "pending-merge-session-start-failsafe-detail", "ok": ok, "msg": msg})
    if not ok:
        failures.append(msg)

    if args.json:
        print(json.dumps({"failures": failures, "results": results}, indent=2, ensure_ascii=True))
    else:
        for r in results:
            sym = _status_symbol(r["ok"])
            _print_safe(f"{sym} {r['msg']}")
        if failures:
            _print_safe(f"\nFAIL: {len(failures)} layer validation failure(s)")
        else:
            _print_safe("\nOK: pending-merge/session-start layered gate checks passed")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
