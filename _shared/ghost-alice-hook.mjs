#!/usr/bin/env node

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (!key.startsWith("--")) {
      continue;
    }
    const name = key.slice(2);
    const value = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : "true";
    args[name] = value;
    if (value !== "true") {
      i += 1;
    }
  }
  return args;
}

function emit(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

try {
  const args = parseArgs(process.argv.slice(2));
  const platform = String(args.platform || "codex").toLowerCase();
  const event = args.event || "BeforeAgent";
  // The registered wrapper checks and audits every call. A healthy hook contributes
  // no instruction context; decision-relevant failures come from the wrapper.
  emit(["claude", "codex"].includes(platform) && event === "PreToolUse" ? {} : { continue: true });
} catch (error) {
  process.stderr.write(`[ghost-alice-hook] ${error instanceof Error ? error.message : String(error)}\n`);
  emit({ continue: true });
}
