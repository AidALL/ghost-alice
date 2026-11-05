"""Managed Git update: fetch, fast-forward the checkout and restore it when the update fails.

The checkout is not the context gateway's code (the installer publishes a copy of `_shared` to the
runtime), so the update does not stop or start the service.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


class SourceUpdate:
    def __init__(self, root):
        self.root, self.stash, self.attempted = Path(root), None, False

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True)
        if result.returncode:
            raise RuntimeError('Git source update command failed: ' + args[0] + ': ' + result.stderr.decode(errors='replace').strip())
        return result.stdout.decode().strip()

    def prepare(self):
        if not self.root.is_absolute() or self.git('rev-parse', '--is-inside-work-tree') != 'true':
            raise ValueError('Source is not an absolute Git worktree')
        self.git('symbolic-ref', '--quiet', '--short', 'HEAD')
        self.git('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
        self.git('fetch')
        self.before_head = self.git('rev-parse', 'HEAD')
        self.target = self.git('rev-parse', '@{u}')
        self.git('merge-base', '--is-ancestor', self.before_head, self.target)
        self.before_status = self.git('status', '--porcelain=v1', '--untracked-files=all')

    def publish(self):
        if self.git('rev-parse', 'HEAD') != self.before_head or self.git('status', '--porcelain=v1', '--untracked-files=all') != self.before_status:
            raise RuntimeError('Concurrent source change prevents managed update')
        self.attempted = True
        if self.before_status:
            self.git('stash', 'push', '-u', '-m', 'ghost-alice source update backup', '--', '.')
            self.stash = self.git('rev-parse', 'refs/stash')
            print('Source local changes saved in git stash: ' + self.stash, file=sys.stderr)
            print('Review: git -C ' + str(self.root) + ' stash show -p ' + self.stash, file=sys.stderr)
        # Fetch and fast-forward feasibility were resolved before any working
        # tree mutation. This is the local part of pull --ff-only.
        self.git('merge', '--ff-only', self.target)

    def restore(self):
        if not self.attempted:
            return
        head = self.git('rev-parse', 'HEAD')
        status = self.git('status', '--porcelain=v1', '--untracked-files=all')
        if status or head not in (self.before_head, self.target):
            raise RuntimeError('Concurrent source change prevents source recovery; saved stash retained')
        if head != self.before_head:
            self.git('reset', '--keep', self.before_head)
        if self.stash:
            self.git('stash', 'apply', '--index', self.stash)
        if self.git('status', '--porcelain=v1', '--untracked-files=all') != self.before_status:
            raise RuntimeError('Restored source does not match its prior working state')

    def run_unmanaged(self):
        try:
            self.publish()
        except Exception:
            self.restore()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    operation = SourceUpdate(args.source_dir)
    try:
        operation.prepare()
        operation.run_unmanaged()
        print('Source checkout updated. Now rerun the installer: bash install.sh', file=sys.stderr)
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps(dict(status='blocked', error_code='SOURCE_UPDATE', phase='source-update', detail=str(exc))),
              file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
