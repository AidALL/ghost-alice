# manifest.json schema and entry lifecycle

## Schema v1

The installer Python code and the response model reviewing a merge use the importable functions in `../scripts/manifest_io.py`; this module has no command-line interface. The helper writes the manifest and takes the locks stated below. The response model asks for the human user's choice, writes the staged content and applies it to the live file.

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

The `decided` and `decision` fields record an outcome on the pending-merge entry. They do not record who wrote that outcome or who made the choice. The lifecycle below identifies the writer for registration and for the response model's record of the human user's choice; `merge-flow.md` identifies the caller that records a non-interactive deferral.

## Entry lifecycle

1. The installer's diff collector, run by the installer's preflight, calls `diff_collector.register_changes_in_manifest` to create an entry for each backed-up external change with `decided=false` and `decision=null`. No outcome is recorded yet; the entry waits for the human user's merge, discard or defer choice.
2. After the staged merge that the human user approved is applied, the response model calls `manifest_io.mark_decided` with `merged`; the entry becomes `decided=true` as the response model's record of that outcome.
3. After the staged change and backup are discarded as the human user approved, the response model records `discarded` with `decided=true`.
4. When the human user chooses later, the response model records `deferred` and retains `decided=false`, so the next session can show the entry again. These values do not record who chose.

## Apply and record a decision

The response model takes the actual manifest path from the current host's pending-merge result, the exact `id`, `source_path` and `backup_path` from the inspected entry, and the decision from the human user's choice. Resolve `SCRIPTS` to the loaded merge-companion skill's absolute `scripts` directory. Before applying staged content through the host's file tool, check the Safety boundary below and retain the staged result that the human user approved and the inspected live version. A changed live file requires reconciling that change with the proposal that the human user approved before applying it.

After the file tool reports that the integration the human user approved completed, use `merged`; after the discard the human user approved completes, use `discarded`; when the human user explicitly defers, use `deferred` without applying a live change. Bind `MANIFEST`, `ENTRY_ID` and `DECISION` to those exact values, then invoke the importable helper through the host's shell tool:

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

`mark_decided(path: Path, entry_id: str, decision: str)` returns `None` on success and atomically updates the matching entry. `deferred` leaves `decided=false`; the other two decisions set it true. `ManifestError` means an unknown decision or missing entry; filesystem errors also remain failures. The response model reads the process exit status and returned error, rather than treating a proposed choice as a recorded one. If the live change succeeded but this recording failed, preserve the evidence of that change and repair only the manifest record; do not apply the same change again. `read_manifest(Path(...))` returns the manifest object and `list_pending(Path(...))` returns pending entries; neither function approves a merge.

## Persistence behavior

The helper writes through a temporary file and a rename. `append_entry`, `append_entry_if_absent` and `mark_decided` hold the lock on the sibling `.lock` file and wait for it at most `MANIFEST_LOCK_WAIT_S` in `manifest_io.py`; a lock held longer raises `file_lock.LockTimeout` to the caller, and a lock the file system refuses raises its `OSError`. Windows takes no lock. When a manifest holds corrupt JSON, `read_manifest` prints a warning, backs the file up with the `.corrupt-bak` suffix and returns an empty entry list.

## Safety boundary

Before applying the change that the human user approved, the response model verifies resolved path containment: `backup_path` belongs to the actual pending-merge directory of the current host, and `source_path` belongs to the installed skills root of the installation identified under the common contract's Prohibitions or a host configuration root the human user authorized. Example host configuration roots are `~/.codex/` and `~/.claude/`. The entry ID uses timestamp-skill-safe_filename; safe_filename removes slash, backslash and double-dot traversal components.

The snapshot_hash and current_hash fields identify artifacts. They do not determine which behavior is correct or authorize applying a result.
