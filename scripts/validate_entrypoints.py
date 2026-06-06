#!/usr/bin/env python3
"""
validate_entrypoints.py. Governance entrypoint reliability smoke test.

Checks in CI whether task-router, using-coding-convention, and the verification
pipeline are actually runnable. This script is a thin automatic guard for
"are rule documents placed so they can really load and trigger?" It does not
judge semantic matching quality; it checks only existence, shape, and consistency.

Usage:
    python3 scripts/validate_entrypoints.py           # run every check
    python3 scripts/validate_entrypoints.py --json    # JSON output for CI

Dependencies:
    Python 3.8+ standard library only.
    Requires prebuilt skill-catalog/skills.json. Fails when missing.

Exit codes:
    0: every check passed
    1: I/O error
    2: one or more ERROR findings
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ENTRYPOINT_SKILLS = {
    "task-router": "task-router/SKILL.md",
    "using-coding-convention": "coding-convention/using-coding-convention/SKILL.md",
}

SSOT_RULES_FILE = "AGENTS.md"
SSOT_IMPORT_FILES = ["CLAUDE.md"]
PLATFORM_PORTS = [
    "platforms/codex/AGENTS.md",
]

REQUIRED_SSOT_PHRASES = [
    ("rule 0", r"###\s*0\.\s*Task\s*Routing\s*Gate"),
    ("task-router name reference", r"task-router"),
    ("first tool call rule", r"first\s+tool\s+call"),
]

REQUIRED_TASK_ROUTER_PHRASES = [
    ("QUALITY-RATIONALE block", r"<QUALITY-RATIONALE>"),
    ("ROUTING-CONTRACT block", r"<ROUTING-CONTRACT>"),
    ("meta:\\* calls", r"meta:\*"),
]

REQUIRED_USING_CC_PHRASES = [
    ("QUALITY-RATIONALE block", r"<QUALITY-RATIONALE>"),
    ("USE-CONTRACT block", r"<USE-CONTRACT>"),
    ("1% rule", r"1%"),
]


class Finding:
    __slots__ = ("severity", "area", "check", "message")

    def __init__(self, severity: str, area: str, check: str, message: str):
        self.severity = severity
        self.area = area
        self.check = check
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "area": self.area,
            "check": self.check,
            "message": self.message,
        }


def check_entrypoint_skills_exist(repo: Path, findings: list[Finding]) -> None:
    for name, rel in ENTRYPOINT_SKILLS.items():
        path = repo / rel
        if not path.is_file():
            findings.append(
                Finding("ERROR", "entrypoint", "exist", f"{rel} is missing. Entrypoint skill is absent.")
            )


def check_ssot_rules(repo: Path, findings: list[Finding]) -> None:
    ssot = repo / SSOT_RULES_FILE
    if not ssot.is_file():
        findings.append(
            Finding("ERROR", "ssot", "exist", f"{SSOT_RULES_FILE} is missing. Philosophy SSOT is absent.")
        )
        return
    text = ssot.read_text(encoding="utf-8")
    for label, pattern in REQUIRED_SSOT_PHRASES:
        if not re.search(pattern, text):
            findings.append(
                Finding(
                    "ERROR",
                    "ssot",
                    "phrase",
                    f"{SSOT_RULES_FILE} is missing the '{label}' phrase. pattern: {pattern!r}",
                )
            )


def check_ssot_imports(repo: Path, findings: list[Finding]) -> None:
    """Check whether CLAUDE.md imports @AGENTS.md."""
    for rel in SSOT_IMPORT_FILES:
        path = repo / rel
        if not path.is_file():
            findings.append(
                Finding("WARNING", "ssot-import", "exist", f"{rel} is missing. Platform loader is absent.")
            )
            continue
        text = path.read_text(encoding="utf-8").strip()
        if "@AGENTS.md" not in text and "AGENTS.md" not in text:
            findings.append(
                Finding(
                    "ERROR",
                    "ssot-import",
                    "chain",
                    f"{rel} does not load AGENTS.md. Add `@AGENTS.md`.",
                )
            )


SSOT_RULE_HEADER_PATTERN = re.compile(r"(?m)^###\s*(\d+)\.\s*(.+)$")


def extract_rule_headers(text: str) -> dict[int, str]:
    """Return rule headers in `### N. Title` form as a {number: title} dictionary."""
    out: dict[int, str] = {}
    for m in SSOT_RULE_HEADER_PATTERN.finditer(text):
        try:
            num = int(m.group(1))
        except ValueError:
            continue
        out[num] = m.group(2).strip()
    return out


def check_platform_ports(repo: Path, findings: list[Finding]) -> None:
    """Check that platform port files include rules 0-7 and stay synchronized with SSOT AGENTS.md."""
    ssot_path = repo / SSOT_RULES_FILE
    ssot_rules: dict[int, str] = {}
    if ssot_path.is_file():
        ssot_rules = extract_rule_headers(ssot_path.read_text(encoding="utf-8"))

    for rel in PLATFORM_PORTS:
        path = repo / rel
        if not path.is_file():
            findings.append(
                Finding(
                    "WARNING",
                    "platform-port",
                    "exist",
                    f"{rel} is missing. Platform port is absent.",
                )
            )
            continue
        text = path.read_text(encoding="utf-8")

        # The rule 0 Task Routing Gate keeps a keyword match as a strong signal.
        if not re.search(r"###\s*0\.\s*Task\s*Routing\s*Gate", text):
            findings.append(
                Finding(
                    "ERROR",
                    "platform-port",
                    "rule-0",
                    f"{rel} is missing the rule 0 Task Routing Gate section. Platform sessions lack that gate.",
                )
            )
        if "task-router" not in text:
            findings.append(
                Finding(
                    "ERROR",
                    "platform-port",
                    "mention",
                    f"{rel} does not mention task-router. The port does not explicitly point to the entrypoint skill.",
                )
            )

        # Parity: every rule number + title from AGENTS.md must appear in the port.
        if ssot_rules:
            port_rules = extract_rule_headers(text)
            for num in sorted(ssot_rules.keys()):
                ssot_title = ssot_rules[num]
                if num not in port_rules:
                    findings.append(
                        Finding(
                            "ERROR",
                            "platform-port",
                            "parity-missing",
                            f"{rel} is missing rule {num}. SSOT title is '{ssot_title}'. Synchronization required.",
                        )
                    )
                elif port_rules[num] != ssot_title:
                    port_title = port_rules[num]
                    findings.append(
                        Finding(
                            "ERROR",
                            "platform-port",
                            "parity-title",
                            f"{rel} rule {num} title drift. SSOT='{ssot_title}' port='{port_title}'. Synchronization required.",
                        )
                    )
            # Also catch rules present only in the port and absent from the SSOT.
            for num in sorted(port_rules.keys()):
                if num not in ssot_rules:
                    port_title = port_rules[num]
                    findings.append(
                        Finding(
                            "WARNING",
                            "platform-port",
                            "parity-extra",
                            f"{rel} has rule {num} '{port_title}', but AGENTS.md does not. Update the SSOT or correct the port.",
                        )
                    )


def check_task_router_body(repo: Path, findings: list[Finding]) -> None:
    path = repo / ENTRYPOINT_SKILLS["task-router"]
    if not path.is_file():
        return  # Already caught by the existence check.
    text = path.read_text(encoding="utf-8")
    for label, pattern in REQUIRED_TASK_ROUTER_PHRASES:
        if not re.search(pattern, text):
            findings.append(
                Finding(
                    "ERROR",
                    "task-router",
                    "body",
                    f"task-router/SKILL.md is missing '{label}'. pattern: {pattern!r}",
                )
            )


def check_using_cc_body(repo: Path, findings: list[Finding]) -> None:
    path = repo / ENTRYPOINT_SKILLS["using-coding-convention"]
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    for label, pattern in REQUIRED_USING_CC_PHRASES:
        if not re.search(pattern, text):
            findings.append(
                Finding(
                    "ERROR",
                    "using-coding-convention",
                    "body",
                    f"using-coding-convention/SKILL.md is missing '{label}'. pattern: {pattern!r}",
                )
            )


def check_catalog_contains_entrypoints(repo: Path, findings: list[Finding]) -> None:
    catalog_path = repo / "skill-catalog" / "skills.json"
    if not catalog_path.is_file():
        findings.append(
            Finding(
                "ERROR",
                "catalog",
                "exist",
                "skill-catalog/skills.json is missing. Run build_catalog.py first.",
            )
        )
        return
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        findings.append(
            Finding("ERROR", "catalog", "parse", f"skill-catalog/skills.json parse failed: {e}")
        )
        return
    names = {s["name"] for s in catalog.get("skills", [])}
    for required in ENTRYPOINT_SKILLS:
        if required not in names:
            findings.append(
                Finding(
                    "ERROR",
                    "catalog",
                    "entry",
                    f"catalog does not register '{required}'. Rerun build_catalog.py.",
                )
            )


def check_validator_scripts_runnable(repo: Path, findings: list[Finding]) -> None:
    """Check whether build_catalog.py --check and validate_skills.py actually run.
    If --check mode is unavailable, only attempt import."""
    scripts = [
        ("build_catalog.py", ["python3", "scripts/build_catalog.py", "--check"]),
        ("validate_skills.py", ["python3", "scripts/validate_skills.py", "--json"]),
        ("validate_entrypoints.py", None),
    ]
    for name, cmd in scripts:
        script_path = repo / "scripts" / name.split(" ")[0]
        if not script_path.is_file():
            findings.append(
                Finding(
                    "ERROR",
                    "validator",
                    "exist",
                    f"scripts/{name} is missing. Validation entrypoint is absent.",
                )
            )
            continue
        if cmd is None:
            continue
        try:
            result = subprocess.run(
                cmd,
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode not in (0, 2):
                # 0: OK, 2: validate_skills.py ERROR findings. Both mean execution itself succeeded.
                findings.append(
                    Finding(
                        "ERROR",
                        "validator",
                        "runnable",
                        f"scripts/{name} execution failed. exit {result.returncode}. stderr: {result.stderr[:200]}",
                    )
                )
        except subprocess.TimeoutExpired:
            findings.append(
                Finding(
                    "ERROR",
                    "validator",
                    "timeout",
                    f"scripts/{name} exceeded 60 seconds. Possible infinite loop.",
                )
            )
        except OSError as e:
            findings.append(
                Finding(
                    "ERROR",
                    "validator",
                    "oserror",
                    f"scripts/{name} cannot execute: {e}",
                )
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    findings: list[Finding] = []

    check_entrypoint_skills_exist(repo, findings)
    check_ssot_rules(repo, findings)
    check_ssot_imports(repo, findings)
    check_platform_ports(repo, findings)
    check_task_router_body(repo, findings)
    check_using_cc_body(repo, findings)
    check_catalog_contains_entrypoints(repo, findings)
    check_validator_scripts_runnable(repo, findings)

    error_count = sum(1 for f in findings if f.severity == "ERROR")
    warning_count = sum(1 for f in findings if f.severity == "WARNING")

    if args.json:
        print(
            json.dumps(
                {
                    "error_count": error_count,
                    "warning_count": warning_count,
                    "findings": [f.to_dict() for f in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"Entrypoint smoke test: repo={repo}")
        for f in findings:
            print(f"  [{f.severity}] {f.area} / {f.check}: {f.message}")
        print(f"Result: ERROR {error_count}, WARNING {warning_count}")

    return 2 if error_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
