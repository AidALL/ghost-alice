# manifest.json schema and entry lifecycle

## Schema v1

The installer Python code and the current-session assistant reviewing a merge use the importable functions in `../scripts/manifest_io.py`; this module has no command-line interface. The helper owns manifest persistence and the platform locking behavior stated below. The assistant owns the user's choice, staged content and live-file application.

```json
{
  "version": 1,
  "entries": [
    {
      "id": "<ISO timestamp>-<skill>-<safe_filename>",
      "platform": "claude" | "codex",
      "skill": "<skill name or 'bootstrap' 'hooks'>",
      "source_path": "<absolute path>",
      "backup_path": "<absolute path under ~/.ghost-alice/pending-merges/<platform>/>",
      "snapshot_hash": "<sha256 hex or null>",
      "current_hash": "<sha256 hex or null>",
      "decided": false,
      "decision": null | "merged" | "discarded" | "deferred",
      "created_at": "<ISO timestamp>"
    }
  ]
}
```

## Entry lifecycle

1. `diff_collector.register_changes_in_manifest` creates the entry with `decided=false` and `decision=null`.
2. After the approved staged merge is applied, the reviewer calls `manifest_io.mark_decided` with merged; the entry becomes decided=true.
3. After the approved staged change and backup are discarded, the reviewer records discarded with decided=true.
4. For later, the reviewer records deferred and retains decided=false, so the next session can surface it.
5. The manifest owner may rotate entries only after they are decided.

## Apply and record a decision

The current-session assistant takes the actual manifest path from the current-platform pending-merge result, the exact `id`, `source_path` and `backup_path` from the inspected entry, and the decision from the user's authorized choice. Resolve `SCRIPTS` to the loaded merge-companion skill's absolute `scripts` directory. Before applying staged content through the host's file tool, check the Safety boundary below and retain the approved staged result and the inspected live version. A changed live file requires reconciling that change with the approved proposal before applying it.

After the file tool confirms approved integration, use `merged`; after the approved discard completes, use `discarded`; for an explicit defer, use `deferred` without applying a live change. Bind `MANIFEST`, `ENTRY_ID` and `DECISION` to those exact values, then invoke the importable helper through the exposed shell/Python tool:

```bash
python3 - "$SCRIPTS" "$MANIFEST" "$ENTRY_ID" "$DECISION" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from manifest_io import mark_decided
mark_decided(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
print('manifest decision recorded')
PY
```

`mark_decided(path: Path, entry_id: str, decision: str)` returns `None` on success and atomically updates the matching entry. `deferred` leaves `decided=false`; the other two decisions set it true. `ManifestError` means an unknown decision or missing entry; filesystem errors also remain failures. The assistant reads the process exit status and returned error, rather than treating a proposed choice as a recorded one. If the live change succeeded but this recording failed, preserve that application evidence and repair only the manifest dependency; do not apply the same business change again. `read_manifest(Path(...))` returns the manifest object and `list_pending(Path(...))` returns pending entries; neither function approves a merge.

## Persistence behavior

The helper writes through a temporary file and os.replace. POSIX uses flock; the Windows fallback is best-effort and performs no locking. Corrupt JSON follows the silent-pass behavior and is backed up with the .corrupt-bak suffix.

## Safety boundary

Before applying the approved change, the current-session assistant verifies resolved path containment: `backup_path` belongs to the actual current-platform pending-merge directory, and `source_path` belongs to the installed skills root supplied by trusted installation context or a platform-config root the user authorized. `SKILLS_DIR` is that resolved installation root, not a guessed working directory. Example platform roots are `~/.codex/` and `~/.claude/`. The entry ID uses timestamp-skill-safe_filename; safe_filename removes slash, backslash and double-dot traversal components.

The snapshot_hash and current_hash fields identify artifacts. They do not determine which behavior is correct or authorize applying a result.
