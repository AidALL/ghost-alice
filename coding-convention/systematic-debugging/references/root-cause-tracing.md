# Root Cause Tracing

Use this technique when an error appears far from the input or initialization that caused it.

## Trace the failure

The agent records the failing operation, target and actual arguments. Follow each caller backward, identifying where the wrong value was created, transformed or used before initialization. Stop tracing when evidence identifies the causal source; do not change only the final consumer unless that consumer owns the violated contract.

For example, a test passes an empty project directory to a workspace initializer. The initializer later invokes Git in the source checkout. Trace the empty value through the workspace and session calls to the fixture that was read before setup. Correct that initialization and verify the original failure.

If the existing trace cannot identify the caller, add bounded diagnostics immediately before the affected operation. Capture the safe argument metadata, current directory and call stack. Do not log raw credentials or environment contents. Remove or retain diagnostics according to the task's accepted debugging requirements.

## Isolate test pollution

When a test creates an artifact in the wrong location, identify the triggering test before changing shared setup. The bundled `find-polluter.sh` sequentially invokes `npm test` for candidate files, ignores test exit statuses and stops at the first observed artifact. Use it only for an authorized compatible runner and isolated target; its result concerns artifact creation, not passing tests. Resolve it relative to this reference's directory; otherwise use the project's actual runner for the same bounded observation.

A bisection or sequential test run is justified only when its possible result changes the causal decision. Systematic-debugging and verification-before-completion own retry bounds and evidence reuse.

## Correct the source and verify

Once the source is supported, implement the correction under the accepted behavior contract and TDD applicability. Add distinct boundary validation only where another actual caller can bypass the source protection; [defense-in-depth.md](defense-in-depth.md) defines that selection.

Report the trace, causal input, correction and original-failure evidence. A call-chain hypothesis alone is not proof of a fixed result.
