"""Regression contract for exact protected-file state across tool boundaries."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import tempfile
import unittest

GUARD = Path(__file__).resolve().parents[2] / 'boundary-contract/scripts/file_guard.py'
EXACT_TIME = 1790549365245769995
NODE = shutil.which('node') or '/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node'


def invoke(*args):
    return subprocess.run([sys.executable, '-B', str(GUARD), *args], text=True, capture_output=True)


def capture(path):
    result = invoke('capture', '--path', str(path))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert isinstance(data['token'], str)
    return data['token']


class FileGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp_path = Path(temporary.name).resolve()
        self.protected = self.tmp_path / 'protected.txt'
        self.protected.write_bytes(b'user-owned content\n')
        os.utime(self.protected, ns=(EXACT_TIME, EXACT_TIME))
        self.assertEqual(self.protected.stat().st_mtime_ns, EXACT_TIME)

    def test_exact_time_survives_real_javascript_roundtrip(self):
        protected = self.protected
        token = capture(protected)
        proc = subprocess.run([NODE, '-e', 'process.stdout.write(JSON.stringify(JSON.parse(process.argv[1])))', json.dumps({'token': token})], text=True, capture_output=True, check=True)
        transported = json.loads(proc.stdout)['token']
        result = invoke('check', '--token', transported)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['unchanged'] is True
        assert protected.read_bytes() == b'user-owned content\n'
        assert protected.stat().st_mtime_ns == EXACT_TIME


    def test_one_nanosecond_real_drift_is_not_rounded_away(self):
        protected = self.protected
        token = capture(protected)
        os.utime(protected, ns=(EXACT_TIME, EXACT_TIME + 1))
        result = invoke('check', '--token', token)
        assert result.returncode != 0
        assert 'mtime_ns' in result.stderr


    def test_changed_content_with_original_timestamp_is_rejected(self):
        protected = self.protected
        token = capture(protected)
        protected.write_bytes(b'evil-owned content\n')
        os.utime(protected, ns=(EXACT_TIME, EXACT_TIME))
        result = invoke('check', '--token', token)
        assert result.returncode != 0
        assert 'sha256' in result.stderr


    def test_replaced_file_with_same_content_and_time_is_rejected(self):
        protected = self.protected
        token = capture(protected)
        replacement = protected.with_name('replacement.txt')
        replacement.write_bytes(protected.read_bytes())
        os.utime(replacement, ns=(EXACT_TIME, EXACT_TIME))
        replacement.replace(protected)
        result = invoke('check', '--token', token)
        assert result.returncode != 0
        assert 'inode' in result.stderr


    def test_symlink_capture_is_rejected(self):
        protected = self.protected
        link = protected.with_name('alias.txt')
        link.symlink_to(protected)
        result = invoke('capture', '--path', str(link))
        assert result.returncode != 0
        assert 'symlink' in result.stderr.lower()


    def test_symlink_substitution_is_rejected(self):
        protected = self.protected
        token = capture(protected)
        target = protected.with_name('target.txt')
        protected.rename(target)
        protected.symlink_to(target)
        result = invoke('check', '--token', token)
        assert result.returncode != 0
        assert 'symlink' in result.stderr.lower()


    def test_parent_traversal_cannot_hide_symlink_target(self):
        tmp_path = self.tmp_path
        first = tmp_path / 'a'
        second = tmp_path / 'b'
        first.mkdir()
        (second / 'child').mkdir(parents=True)
        (first / 'protected.txt').write_text('wrong file')
        (second / 'protected.txt').write_text('actual protected file')
        (first / 'alias').symlink_to(second / 'child', target_is_directory=True)
        requested = first / 'alias' / '..' / 'protected.txt'
        assert requested.read_text() == 'actual protected file'
        result = invoke('capture', '--path', str(requested))
        assert result.returncode != 0
        assert 'parent traversal' in result.stderr.lower()


    def test_all_protected_files_are_checked_without_writes(self):
        protected = self.protected
        other = protected.with_name('other.txt')
        other.write_bytes(b'other')
        before = set(protected.parent.iterdir())
        result = invoke('capture', '--path', str(protected), '--path', str(other))
        assert result.returncode == 0, result.stderr
        token = json.loads(result.stdout)['token']
        assert invoke('check', '--token', token).returncode == 0
        other.write_bytes(b'change')
        assert invoke('check', '--token', token).returncode != 0
        assert set(protected.parent.iterdir()) == before


    def test_malformed_tokens_fail_closed(self):
        for token in ['', 'not-a-token', 'file-guard-v1:e30=']:
            with self.subTest(token=token):
                result = invoke('check', '--token', token)
                assert result.returncode != 0
                assert 'invalid' in result.stderr.lower()


    def test_numeric_timestamp_token_is_rejected(self):
        protected = self.protected
        token = capture(protected)
        prefix, payload = token.split(':', 1)
        record = json.loads(base64.urlsafe_b64decode(payload))
        record['files'][0]['mtime_ns'] = EXACT_TIME
        malformed = prefix + ':' + base64.urlsafe_b64encode(json.dumps(record).encode()).decode()
        result = invoke('check', '--token', malformed)
        assert result.returncode != 0
        assert 'invalid' in result.stderr.lower()
