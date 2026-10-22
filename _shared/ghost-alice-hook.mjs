#!/usr/bin/env node

// The installer registers exactly this call; it replaces every other form at install.
function isRegisteredCall(argv) {
  const values = new Map();
  for (let i = 0; i + 1 < argv.length; i += 2) {
    values.set(argv[i], argv[i + 1]);
  }
  return argv.length === 4 && values.size === 2
    && ["claude", "codex"].includes(values.get("--platform")) && values.get("--event") === "PreToolUse";
}

if (isRegisteredCall(process.argv.slice(2))) {
  // The registered wrapper checks and audits every call. A healthy hook contributes
  // no instruction context; decision-relevant failures come from the wrapper.
  process.stdout.write("{}\n");
} else {
  // A usage error exits 2 like the Python hooks; the wrapper reports it to the host as a non-blocking 1.
  process.stderr.write("[ghost-alice-hook] usage: ghost-alice-hook.mjs --platform claude|codex --event PreToolUse\n");
  process.exitCode = 2;
}
