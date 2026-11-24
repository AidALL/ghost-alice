"""Reproduce: mark_decided leaves a pending entry undecided when an older decided entry has the same id.

Usage: python repro_mark_decided_duplicate_id.py <merge-companion/scripts directory>
Example: python repro_mark_decided_duplicate_id.py ~/ghost-alice-baseline/merge-companion/scripts

The script builds a throwaway manifest in a temporary folder (it never touches ~/.ghost-alice):
  entry 1  id X, decided=true, decision=discarded   (left by an earlier install)
  entry 2  id X, decided=false, decision=null       (the same unchanged file found again by a later install)
It then calls mark_decided(manifest, X, "discarded") and prints how many entries are still pending.
Exit code 1 = reproduced (the pending entry stayed pending without an error), 0 = not reproduced.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def entry(decided: bool, decision, created_at: str) -> dict:
    return {
        'id': 'pending-0123456789abcdef',
        'platform': 'claude',
        'skill': 'synced',
        'source_path': '/home/user/.claude/skills/synced/example/SKILL.md',
        'backup_path': None,
        'snapshot_hash': 'sha256:aaaa',
        'current_hash': 'sha256:bbbb',
        'change_kind': 'modified',
        'relative_path': 'synced/example/SKILL.md',
        'decided': decided,
        'decision': decision,
        'created_at': created_at,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    sys.path.insert(0, str(Path(argv[0]).expanduser().resolve()))
    import manifest_io  # noqa: E402  (the module under test)

    with tempfile.TemporaryDirectory() as folder:
        manifest = Path(folder) / 'manifest.json'
        manifest.write_text(json.dumps({'version': 1, 'platform': 'claude', 'entries': [
            entry(True, 'discarded', '2026-11-12T02:18:00Z'),
            entry(False, None, '2026-11-24T07:02:16Z'),
        ]}), encoding='utf-8')
        before = len(manifest_io.list_pending(manifest))
        try:
            manifest_io.mark_decided(manifest, 'pending-0123456789abcdef', 'discarded')
            outcome = 'returned without error'
        except Exception as error:  # the fixed version may refuse an ambiguous id instead
            outcome = f'raised {type(error).__name__}: {error}'
        after = len(manifest_io.list_pending(manifest))
        entries = json.loads(manifest.read_text(encoding='utf-8'))['entries']
    print(f'module: {Path(manifest_io.__file__).resolve()}')
    print(f'pending before: {before}, mark_decided {outcome}, pending after: {after}')
    for item in entries:
        print(f"  created_at={item['created_at']} decided={item['decided']} decision={item['decision']}")
    reproduced = after == 1 and outcome == 'returned without error'
    print('REPRODUCED: the new entry stays pending although the call reported success' if reproduced
          else 'not reproduced')
    return 1 if reproduced else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
