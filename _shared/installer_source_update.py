"""Managed Git update as a reversible participant in installer coordination."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

from context_gateway_install import coordinate_payload_replacement


class SourceUpdate:
    def __init__(self, root):
        self.root, self.stash, self.attempted = Path(root), None, False
        self.temporary = tempfile.TemporaryDirectory(prefix='ghost-alice-source-stage-')
        self.stage_root = Path(self.temporary.name)
        self.renewed_paths = ()

    def git(self, *args, raw=False):
        result = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True)
        if result.returncode:
            raise RuntimeError('Git source update command failed: ' + args[0] + ': ' + result.stderr.decode(errors='replace').strip())
        return result.stdout if raw else result.stdout.decode().strip()

    def prepare(self):
        if not self.root.is_absolute() or self.git('rev-parse', '--is-inside-work-tree') != 'true':
            raise ValueError('Source is not an absolute Git worktree')
        self.branch = self.git('symbolic-ref', '--quiet', '--short', 'HEAD')
        self.upstream = self.git('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
        self.git('fetch')
        self.before_head = self.git('rev-parse', 'HEAD')
        self.target = self.git('rev-parse', '@{u}')
        self.git('merge-base', '--is-ancestor', self.before_head, self.target)
        self.before_status = self.git('status', '--porcelain=v1', '--untracked-files=all')
        changed = set()
        for args in (('diff', '--no-renames', '--name-only', '-z', 'HEAD', self.target),
                     ('diff', '--no-renames', '--name-only', '-z', 'HEAD'), ('ls-files', '--others', '--exclude-standard', '-z')):
            changed.update(p for p in self.git(*args, raw=True).decode().split('\0') if p)
        self.changed_paths = tuple(self.root / p for p in sorted(changed))
        self.renewed_paths = tuple(path.resolve() for path in self.changed_paths)
        archive = self.git('archive', '--format=tar', self.target, raw=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            stream.extractall(self.stage_root, filter='data')

    @property
    def mappings(self):
        return ((self.stage_root, self.root),)

    @property
    def destinations(self):
        return self.changed_paths

    @property
    def recovery_reference(self):
        return str(self.root) + ' at ' + self.before_head

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

    def cleanup(self):
        self.temporary.cleanup()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    operation = SourceUpdate(args.source_dir)
    try:
        operation.prepare()
        if operation.destinations:
            coordinate_payload_replacement(operation)
        else:
            operation.run_unmanaged()
        print('Source checkout updated. Now rerun the installer: bash install.sh', file=sys.stderr)
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps(dict(status=getattr(exc, 'outcome', 'blocked'),
            error_code=getattr(exc, 'code', 'GATEWAY_PRECONDITION'),
            phase=getattr(exc, 'phase', 'source-update'), detail=str(exc))), file=sys.stderr)
        return getattr(exc, 'exit_code', 2)
    finally:
        operation.cleanup()


if __name__ == '__main__':
    raise SystemExit(main())
