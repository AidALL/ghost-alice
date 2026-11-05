# Merge Review Flow

An added company paragraph is semantic only if no structural condition applies; its approved staged result becomes `merged` after live application succeeds. A partial proposal does not mark unapplied content complete.

## Conflict

If both versions change the same region incompatibly, the response model isolates that conflict during staging. It presents the region for the user's choice of user version, new installed version or manual resolution. After resolving the choice, it updates the staged result and follows the same verification and final approval/application step.

## Non-interactive execution

When no interactive user decision or prior applicable authorization is available, the response model or Python code written to import the helper retains the pending entry and reports the backup notice. It does not run interactive steps 2–6 or choose integration/discard. If an authorized caller must persist the deferral, it uses `mark_decided` with `deferred` through the same import interface; no standalone merge script or implicit background actor performs this step. The entry stays available for the next review.
