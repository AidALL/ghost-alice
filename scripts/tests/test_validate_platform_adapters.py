"""Tests for platform adapter compliance validation."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate_platform_adapters.py"


def valid_records() -> list[dict[str, object]]:
    return [
        {
            "id": "claude",
            "state": "native",
            "supported_assets": ["CLAUDE.md", "skills", "settings.json hooks"],
            "unsupported_surfaces": [],
            "install_or_onramp": "bash install.sh --platform claude",
            "verification_commands": ["python3 scripts/validate_entrypoints.py --json"],
            "risk_notes": ["Native hook support still requires installed settings verification"],
            "last_verified_at": "2026-05-13",
            "owner": "Ghost-ALICE",
            "source_docs": ["docs/policies/installer-platform-compatibility-matrix.md"],
        },
        {
            "id": "codex",
            "state": "instruction-backed",
            "supported_assets": ["AGENTS.md", "skills", "hooks.json + config.toml hooks feature"],
            "unsupported_surfaces": ["visible Skill tool surface"],
            "install_or_onramp": "bash install.sh --platform codex",
            "verification_commands": ["python3 scripts/validate_entrypoints.py --json"],
            "risk_notes": ["Codex hook event names in hooks.json are install-surface configuration, not runtime firing proof; require hook payload evidence, runtime smoke, or hookless/manual fallback before claiming gate completion"],
            "last_verified_at": "2026-06-04",
            "owner": "Ghost-ALICE",
            "source_docs": ["docs/policies/installer-platform-compatibility-matrix.md"],
        },
        {
            "id": "terminal-only",
            "state": "terminal-only",
            "supported_assets": ["AGENTS.md style policy text", "skills"],
            "unsupported_surfaces": ["native hook runtime", "managed skill activation policy"],
            "install_or_onramp": "read project AGENTS.md and install skills manually",
            "verification_commands": ["python3 scripts/validate_skills.py --json"],
            "risk_notes": ["No native hook runtime is assumed"],
            "last_verified_at": "2026-05-13",
            "owner": "Ghost-ALICE",
            "source_docs": ["docs/policies/installer-platform-compatibility-matrix.md"],
        },
    ]


class PlatformAdapterValidatorTests(unittest.TestCase):
    def write_fixture(self, records: list[dict[str, object]]) -> Path:
        temp_dir = Path(tempfile.mkdtemp(prefix="platform-adapters-test-"))
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        catalog = temp_dir / "platform-adapters.json"
        policy = temp_dir / "policy.md"
        catalog.write_text(json.dumps({"adapters": records}, indent=2), encoding="utf-8")
        policy.write_text("claude codex terminal-only\n", encoding="utf-8")
        return catalog

    def run_validator(self, catalog: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), "--catalog", str(catalog), "--repo-root", str(ROOT)],
            capture_output=True,
            text=True,
        )

    def test_valid_adapter_records_pass(self) -> None:
        result = self.run_validator(self.write_fixture(valid_records()))

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_codex_must_not_claim_native_hook_parity(self) -> None:
        records = valid_records()
        records[1]["state"] = "native"
        result = self.run_validator(self.write_fixture(records))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("codex", result.stdout + result.stderr)
        self.assertIn("native", result.stdout + result.stderr)

    def test_codex_must_distinguish_event_config_from_runtime_firing(self) -> None:
        records = valid_records()
        records[1]["risk_notes"] = ["Require hook payload evidence before gate claims"]
        result = self.run_validator(self.write_fixture(records))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime firing proof", result.stdout + result.stderr)

    def test_required_fields_are_enforced(self) -> None:
        records = valid_records()
        del records[0]["verification_commands"]
        result = self.run_validator(self.write_fixture(records))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verification_commands", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
