"""Temporary reproduction for report item 4-2 (not for commit).

Publishes the same completion block through the real file path
(`publish --completion-file <path>`) twice: once saved with LF line endings,
once with CRLF. Setup mirrors CompletionV2BoundaryTest.
test_v2_cli_explicit_schema_publishes_and_reuses_exact_reference.
"""
import json
import os
import subprocess
import sys
import unittest

import test_completion_publication as publication_fixture
from test_completion_contract_v2 import _CompletionV2Fixture


class CrlfCompletionFileRepro(_CompletionV2Fixture):
    def _publish_file(self, make_bytes):
        source = {**os.environ, **self.source, 'PYTHONDONTWRITEBYTECODE': '1'}
        source.pop('CODEX_THREAD_ID', None)
        base = [sys.executable, '-B', str(publication_fixture.SCRIPT)]
        prepared = subprocess.run(base + ['prepare', '--schema-version', 'autopilot-completion-receipt.v2',
            '--intent-root', str(self.root / 'intent'), '--platform', 'codex', '--session-id', 'session-1',
            '--input-event-id', 'event-1'], cwd=self.root, env=source, capture_output=True, text=True)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        receipt = json.loads(prepared.stdout)
        artifact = self.root / 'cli-report.txt'
        protected = self.root / 'cli-protected.txt'
        artifact.write_text('8')
        protected.write_text('original')
        self.proof()
        mapping = [dict(work_unit_id='report-unit', contract_revision=1, criterion_id=key, locators=[str(path)])
                   for key, path in [('report', artifact), ('protected', protected)]]
        self.assertIn('\n', self.completion)  # multi-line block, created by proof()
        data = make_bytes(self.completion)
        completion_file = self.root / 'completion.md'
        completion_file.write_bytes(data)
        args = base + ['publish', '--schema-version', 'autopilot-completion-receipt.v2',
                       '--receipt-token', receipt['receipt_token'], '--verified-at', self.verified_at,
                       '--evidence-source', self.evidence_source, '--completion-file', str(completion_file),
                       '--criterion-dependencies-json', json.dumps(mapping)]
        result = subprocess.run(args, cwd=self.root, env=source, capture_output=True, text=True)
        ending = 'CRLF' if b'\r\n' in data else 'LF'
        print(f"\n[4-2 repro] {sys.platform} line endings={ending} "
              f"bytes={len(data)} exit={result.returncode} stderr={result.stderr.strip()[:200]}")
        return result

    def test_lf_completion_file_is_accepted(self):
        result = self._publish_file(lambda text: text.encode('utf-8'))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_crlf_completion_file_is_rejected(self):
        result = self._publish_file(lambda text: text.replace('\n', '\r\n').encode('utf-8'))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('Final block must match exactly one recorded assistant review', result.stderr)


if __name__ == '__main__':
    unittest.main()
