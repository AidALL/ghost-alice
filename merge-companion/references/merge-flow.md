# Merge Review Flow

An added company paragraph is semantic only if no structural condition applies; the staged result that the human user approved becomes `merged` after live application succeeds. A partial proposal does not mark unapplied content complete.

## Conflict

If both versions change the same region incompatibly, the response model isolates that conflict during staging. It presents the region for the human user's choice of the version with the external change, the new installed version or manual resolution. An external change is a change that the installer finds in an installed target after install and that its records do not hold; the installer does not record who made the change. After the human user chooses, the response model updates the staged result and follows the same verification, final approval by the human user and application step.

## Non-interactive execution

When neither an interactive decision from the human user nor prior applicable authorization from the human user's instructions is available, the response model or Python code written to import the helper retains the pending entry and reports the backup notice. It does not run interactive steps 2–6 or choose integration/discard. If a caller that the human user authorized (the response model or Python code that imports the merge helper) must persist the deferral, it uses `mark_decided` with `deferred` through the same import interface; no standalone merge script or implicit background actor performs this step. The entry stays available for the next review.
