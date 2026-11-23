"""Temporary reproduction for report item 4-2 (not for commit).

Sends the same LF completion block to `publish --completion-file -` through a
shell pipe, the way an assistant would from PowerShell or Git Bash:
    Get-Content -Raw completion.md | python shim.py spec.json
The shim records the exact bytes the shell delivered and forwards them.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import test_completion_publication as publication_fixture
from test_completion_contract_v2 import _CompletionV2Fixture

SHIM = os.environ['GA_PIPE_SHIM']


class PipeCompletionRepro(_CompletionV2Fixture):
    def _publish_via(self, shell):
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
        completion_file = self.root / 'completion.md'
        completion_file.write_bytes(self.completion.encode('utf-8'))  # LF, exactly as reviewed
        out = self.root / 'pipe-result.json'
        spec = self.root / 'spec.json'
        spec.write_text(json.dumps({
            'args': base + ['publish', '--schema-version', 'autopilot-completion-receipt.v2',
                            '--receipt-token', receipt['receipt_token'], '--verified-at', self.verified_at,
                            '--evidence-source', self.evidence_source, '--completion-file', '-',
                            '--criterion-dependencies-json', json.dumps(mapping)],
            'cwd': str(self.root), 'env': source, 'out': str(out)}), encoding='utf-8')
        py = sys.executable
        if shell == 'bash':
            bash = shutil.which('bash') or r'C:\Program Files\Git\bin\bash.exe'
            cmd = [bash, '-c', 'cat "$1" | "$2" "$3" "$4"', '_',
                   completion_file.as_posix(), Path(py).as_posix(), Path(SHIM).as_posix(), spec.as_posix()]
        else:
            exe = 'pwsh' if shell == 'pwsh7' else 'powershell'
            script = (f"Get-Content -Raw -Encoding UTF8 '{completion_file}' | "
                      f"& '{py}' '{SHIM}' '{spec}'")
            cmd = [exe, '-NoProfile', '-Command', script]
        ran = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        result = json.loads(out.read_text(encoding='utf-8'))
        sent = self.completion
        got = result['received']
        print(f"\n[4-2 pipe] {shell}: sent={len(sent.encode())}B received={len(got.encode())}B "
              f"identical={got == sent} head={result['head_hex']} crlf={result['crlf_count']} lf={result['lf_count']} "
              f"non_ascii={result['non_ascii']}/{sum(b > 127 for b in sent.encode())} "
              f"tail={got[-6:]!r} exit={result['exit']} "
              f"stderr={result['stderr'].strip()[:160]}")
        return result

    def test_git_bash_pipe(self):
        self._publish_via('bash')

    def test_powershell7_pipe(self):
        self._publish_via('pwsh7')

    def test_windows_powershell51_pipe(self):
        self._publish_via('ps51')


if __name__ == '__main__':
    unittest.main()
