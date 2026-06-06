# manifest.json schema and entry lifecycle

## Schema v1

Managed by manifest_io.py. Guarantees atomic write plus flock.

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

## entry lifecycle

1. Creation. diff_collector.register_changes_in_manifest appends. decided=false, decision=null
2. User decision. decision is updated through manifest_io.mark_decided
   - "merged" -> decided=true, called after the staged merge is applied to the live location
   - "discarded" -> decided=true, called after the staged change and backup are discarded
   - "deferred" -> decided=false is kept, decision="deferred" is recorded (asked again in the next session)
3. Cleanup. A decided=true entry can be rotated afterward (detailed policy is a follow-up task)

## Guarantees

- atomic write (tempfile + os.replace)
- POSIX flock lock (Windows fallback: best-effort no-op)
- corrupt JSON -> silent pass + .corrupt-bak backup

## Safety boundary

- backup_path must be under ~/.ghost-alice/pending-merges/ (path traversal defense, verified at the apply stage)
- source_path must be under SKILLS_DIR or a platform-config location (~/.codex/, ~/.claude/)
- entry id follows the timestamp-skill-safe_filename pattern. safe_filename has /, \, and .. removed
