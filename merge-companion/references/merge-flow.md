# 6-step UX scenario walk-through
## Contents

- [Normal path (1 entry, integrate decision)](#normal-path-1-entry-integrate-decision)
- [Conflict path (automatic merge not possible)](#conflict-path-automatic-merge-not-possible)
- [Non-interactive path (CI / non-tty)](#non-interactive-path-ci-non-tty)
- [Pattern 2 propagation path](#pattern-2-propagation-path)
- [User decision data structure](#user-decision-data-structure)


## Normal path (1 entry, integrate decision)

1. Notice: "1 user change detected since the last install: ~/.codex/AGENTS.md (manifest entry 2026-04-15T10-00-00Z-AGENTS-AGENTS.md). Backup: ~/.ghost-alice/pending-merges/codex/2026-04-15T10-00-00Z-AGENTS-AGENTS.md.bak"
2. AI classification: "Semantic tier, single-file body meaning change. A company intro paragraph was added."
3. Diff display: "The user inserted the [business name / address / representative] paragraph right after the first block of AGENTS.md. The new install made no change in the same region. Impact surface: the bootstrap entry text only, no effect on other skills." [raw diff toggle] [impact graph toggle]
4. Decision confirm: "integrate / discard / later / partial merge (advanced)"
   - User: "integrate"
5. AI staged merge: creates ~/.ghost-alice/staged/2026-04-15T10-00-00Z-AGENTS-AGENTS.md/result.md (the result of combining the user paragraph with the new install body)
6. Final confirm: "staged result preview [show content]. Apply? [Y/n]"
   - User: Y
   - Overwrite the live ~/.codex/AGENTS.md (the backup is preserved), manifest mark_decided("merged")

## Conflict path (automatic merge not possible)

- The same line is changed differently by both the user and the new install
- After step 4, the staged merge is attempted at step 5, then the conflict region is isolated
- Only the conflict region is shown to the user for a direct choice (user / new install / manual edit)
- After the decision, the staged result is updated, then step 6 continues

## Non-interactive path (CI / non-tty)

- Only the step 1 notice is printed to stderr
- Steps 2 through 6 are skipped automatically, and every entry is marked decision="deferred" (decided=false is kept)
- It triggers again in the next interactive session

## Pattern 2 propagation path

- An additional question after step 4: "Apply this merge decided in Codex to Claude as well by the same criteria?"
- If yes, an entry with the same source_path is automatically mark_decided in ~/.ghost-alice/pending-merges/{claude}/manifest.json
- For a region where the per-model output differs, the AI marks it "propagation not possible" in advance

## User decision data structure

Each decision is persisted immediately into the decision field of the manifest entry. The partial merge option is future work (in this phase 1, the three choices are integrate / discard / hold in full).
