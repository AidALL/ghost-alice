#!/usr/bin/env node

import fs from "node:fs";
import crypto from "node:crypto";
import os from "node:os";
import path from "node:path";

import { isCanonicalIdentityComponent, readSessionIntentSnapshot } from "./session_intent_snapshot.mjs";

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

function userHome(env = process.env) {
  const candidates = [
    env.HOME,
    env.USERPROFILE,
    env.HOMEDRIVE && env.HOMEPATH ? `${env.HOMEDRIVE}${env.HOMEPATH}` : "",
    os.homedir(),
  ];
  for (const candidate of candidates) {
    const value = String(candidate || "").trim();
    if (value) {
      return value;
    }
  }
  return os.homedir();
}

function ghostRoot() {
  return process.env.GHOST_ALICE_GHOST_ROOT || path.join(userHome(), ".ghost-alice");
}

function assertOwnedMutation(target) {
  const owned = process.env.GHOST_ALICE_OWNED_ROOT;
  if (!owned) return;
  function resolved(candidate) {
    if (fs.existsSync(candidate)) return fs.realpathSync(candidate);
    return path.join(resolved(path.dirname(candidate)), path.basename(candidate));
  }
  const relative = path.relative(resolved(owned), resolved(target));
  if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error("Isolated mutation escapes owned destination");
  }
}

function pendingManifestPath(platform) {
  return path.join(ghostRoot(), "pending-merges", platform, "manifest.json");
}

function sessionIntentRoot(configuredRoot = "") {
  const configured = firstNonEmpty(configuredRoot, process.env.GHOST_ALICE_SESSION_INTENT_ROOT);
  if (configured) {
    return configured;
  }
  return path.join(ghostRoot(), "session-intent");
}

function sessionIntentDir(platform, sessionId, root = "") {
  if (!isCanonicalIdentityComponent(platform) || !isCanonicalIdentityComponent(sessionId)) {
    throw new Error("Invalid session intent identity");
  }
  return path.join(sessionIntentRoot(root), platform, sessionId);
}

function readHookInput() {
  try {
    if (process.stdin.isTTY) {
      return {};
    }
    const raw = fs.readFileSync(0, "utf8");
    if (!raw.trim()) {
      return {};
    }
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function readJsonFile(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

function writeJsonFile(filePath, value) {
  assertOwnedMutation(filePath);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function sleepMs(ms) {
  const buffer = new SharedArrayBuffer(4);
  Atomics.wait(new Int32Array(buffer), 0, 0, ms);
}

function withJsonFileLock(filePath, callback) {
  const lockPath = `${filePath}.lock`;
  assertOwnedMutation(filePath);
  assertOwnedMutation(lockPath);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  for (let attempt = 0; attempt < 50; attempt += 1) {
    let fd = null;
    try {
      fd = fs.openSync(lockPath, "wx");
      return callback();
    } catch (error) {
      if (!error || error.code !== "EEXIST") {
        return callback();
      }
      try {
        const stat = fs.statSync(lockPath);
        if (Date.now() - stat.mtimeMs > 2000) {
          fs.unlinkSync(lockPath);
          continue;
        }
      } catch {
        // Retry after transient lock-file races.
      }
      sleepMs(10);
    } finally {
      if (fd !== null) {
        try {
          fs.closeSync(fd);
        } catch {
          // Ignore close failures; the lock file cleanup below is decisive.
        }
        try {
          fs.unlinkSync(lockPath);
        } catch {
          // A stale lock is handled by the next caller.
        }
      }
    }
  }
  return callback();
}

function firstNonEmpty(...values) {
  for (const value of values) {
    if (value === undefined || value === null) {
      continue;
    }
    const text = String(value).trim();
    if (text) {
      return text;
    }
  }
  return "";
}

function inputSessionId(platform, input, root = "", fallback = "") {
  if (!isCanonicalIdentityComponent(platform)) return "";
  for (const candidate of [
    input.session_id,
    input.sessionId,
    input.conversation_id,
    input.thread_id,
    platform === "codex" ? process.env.CODEX_THREAD_ID : "",
    process.env.GHOST_ALICE_SESSION_ID,
  ]) {
    if (candidate === undefined || candidate === null || candidate === "") continue;
    // Reject invalid native identity; do not sanitize or fall through to a
    // different session. A mutable discovery pointer cannot bind a hook call.
    return isCanonicalIdentityComponent(candidate) ? candidate : "";
  }
  return "";
}

function hasUndecidedPendingMerge(platform) {
  try {
    const raw = fs.readFileSync(pendingManifestPath(platform), "utf8");
    const manifest = JSON.parse(raw);
    const entries = Array.isArray(manifest.entries) ? manifest.entries : [];
    return entries.some((entry) => entry && entry.decided === false);
  } catch {
    return false;
  }
}

function reminderText(hook) {
  try {
    const data = JSON.parse(fs.readFileSync(new URL("./reminder_texts.json", import.meta.url), "utf8"));
    return typeof data[hook] === "string" ? data[hook] : "";
  } catch {
    return "";
  }
}

function messageFor(hook, platform) {
  if (hook !== "tool-checkpoint") {
    return "";
  }
  return reminderText("tool-checkpoint") || "tool-checkpoint: emit a [tool-checkpoint] block (intent, why).";
}

function emit(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function platformUsesPreToolContext(platform, event) {
  return ["claude", "codex"].includes(String(platform || "").toLowerCase())
    && event === "PreToolUse";
}

function platformSuppressesPreToolAllowContext(platform, event) {
  return String(platform || "").toLowerCase() === "claude" && event === "PreToolUse";
}

function payloadFor(platform, event, message) {
  if (platformSuppressesPreToolAllowContext(platform, event)) {
    return {};
  }
  if (platformUsesPreToolContext(platform, event)) {
    return message ? { hookSpecificOutput: { hookEventName: event, additionalContext: message } } : {};
  }
  return message
    ? { continue: true, systemMessage: message, hookSpecificOutput: { hookEventName: event, additionalContext: message } }
    : { continue: true };
}

function readJsonLine(line) {
  try {
    const row = JSON.parse(line);
    return row && typeof row === "object" ? row : null;
  } catch {
    return null;
  }
}

function sha256Text(value) {
  return `sha256:${crypto.createHash("sha256").update(String(value), "utf8").digest("hex")}`;
}

function latestTranscriptUserDigest(transcriptPath) {
  const filePath = String(transcriptPath || "").trim();
  if (!filePath) {
    return "";
  }
  let lines = [];
  try {
    lines = fs.readFileSync(filePath, "utf8").trim().split(/\r?\n/u).filter(Boolean);
  } catch {
    return "";
  }
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const row = readJsonLine(lines[index]);
    if (!row) {
      continue;
    }
    const message = row.message && typeof row.message === "object" ? row.message : {};
    const role = firstNonEmpty(row.role, message.role);
    const type = firstNonEmpty(row.type);
    if (role !== "user" && type !== "user") {
      continue;
    }
    const content = message.content ?? row.content;
    if (Array.isArray(content) && content.some((item) => item && typeof item === "object" && item.type === "tool_result")) {
      continue;
    }
    return sha256Text(lines[index]);
  }
  return "";
}

function taskRouterReleaseMessage(sessionId, statePath) {
  return [
    "hook-reminder: run task-router after exact session-intent preflight.",
    "Read the authoritative ledger, decompose accepted intent into atomic meaning units, choose focus-layer micro|meso|macro|meta plus scope-reopen target on mismatch, then assign output, verification, lifecycle, and boundary skills.",
    "task-router consumes session intent; it owns routing, not intake, raw intent inference, or native tool permission.",
    "Every user input requires task-router, including explanation, clarification, status, and follow-up.",
    `intake-ready: current input recorded for session ${sessionId}.`,
    `intent-ledger: use session_intent_ledger.py --read-state --root ${JSON.stringify(path.dirname(path.dirname(path.dirname(statePath))))} --platform ${JSON.stringify(path.basename(path.dirname(path.dirname(statePath))))} --session-id ${JSON.stringify(sessionId)}. Compatibility export: ${statePath} is not authoritative.`,
    "task-router-step: read-session-intent-ledger → atomic meaning decomposition → focus-layer/scope-reopen → skill assignment.",
  ].join("\n");
}

function taskRouterReminderMessage(platform, input, root = "") {
  const sessionId = inputSessionId(platform, input, root, "");
  if (!sessionId || sessionId === "unknown") {
    return "hook-reminder: task-router withheld until session-intent-analyzer observes an explicitly bound session. Do not run task-router yet.";
  }

  const sessionDirPath = sessionIntentDir(platform, sessionId, root);
  const snapshot = readSessionIntentSnapshot(sessionDirPath, platform, sessionId);
  if (snapshot?.degraded) {
    return `hook-reminder: task-router withheld: the session-intent ledger is degraded for session ${sessionId}. Repair intake before routing; do not reuse a prior-input anchor.`;
  }
  if (!snapshot?.latestInput) {
    return `hook-reminder: task-router withheld until session-intent-analyzer records current input for session ${sessionId}. Continue intake/bootstrap; do not run task-router yet.`;
  }
  return taskRouterReleaseMessage(sessionId, path.join(sessionDirPath, "intent-state.json"));
}

function toolCheckpointSurfaceStatePath() {
  return path.join(ghostRoot(), "hooks", "tool-checkpoint-surface-state.json");
}

function toolCheckpointSurfaceLineage(platform, input, root = "") {
  const sessionId = inputSessionId(platform, input, root, "");
  if (!sessionId || sessionId === "unknown") {
    return null;
  }
  const safePlatform = platform;
  const snapshot = readSessionIntentSnapshot(sessionIntentDir(platform, sessionId, root), platform, sessionId);
  const latestEvent = snapshot?.latestInput;
  const eventId = firstNonEmpty(latestEvent?.event_id);
  const inputDigest = firstNonEmpty(latestEvent?.input_digest);
  if (eventId || inputDigest) {
    return {
      scope: `${safePlatform}:${sessionId}`,
      key: `${safePlatform}:${sessionId}:intent:${eventId}:${inputDigest}`,
    };
  }
  if (snapshot?.degraded || snapshot?.lineageSource === "state") return null;
  if (safePlatform === "claude") {
    const transcriptDigest = latestTranscriptUserDigest(firstNonEmpty(input.transcript_path, input.transcriptPath));
    return {
      scope: `${safePlatform}:${sessionId}`,
      key: `${safePlatform}:${sessionId}:fallback:${transcriptDigest || "session"}`,
    };
  }
  if (!eventId && !inputDigest) {
    return null;
  }
  return null;
}

function shouldSurfaceToolCheckpoint(platform, input, root = "") {
  const lineage = toolCheckpointSurfaceLineage(platform, input, root);
  if (!lineage) {
    return true;
  }
  const statePath = toolCheckpointSurfaceStatePath();
  return withJsonFileLock(statePath, () => {
    const state = readJsonFile(statePath, {});
    const surfaces = state && typeof state.surfaces === "object" && state.surfaces
      ? state.surfaces
      : {};
    const current = surfaces[lineage.scope];
    if (current && current.lineage_key === lineage.key) {
      return false;
    }
    surfaces[lineage.scope] = {
      lineage_key: lineage.key,
      updated_at: new Date().toISOString(),
    };
    writeJsonFile(statePath, {
      schema_version: "tool-checkpoint-surface.v1",
      surfaces,
    });
    return true;
  });
}

try {
  const args = parseArgs(process.argv.slice(2));
  const platform = args.platform || "codex";
  const event = args.event || "BeforeAgent";
  const hook = args.hook || "hook-reminder";
  const input = readHookInput();
  const marker = args.marker ? `${args.marker}: ` : "";
  const body = hook === "hook-reminder"
    ? taskRouterReminderMessage(platform, input, args["session-intent-root"])
    : messageFor(hook, platform);
  const message = body ? `${marker}${body}` : "";

  if (hook === "tool-checkpoint" && (event === "BeforeTool" || event === "PreToolUse")) {
    const surfaceMessage = shouldSurfaceToolCheckpoint(platform, input, args["session-intent-root"])
      ? message
      : "";
    emit(payloadFor(platform, event, surfaceMessage));
    process.exit(0);
  }

  emit(payloadFor(platform, event, message));
} catch (error) {
  process.stderr.write(`[ghost-alice-hook] ${error instanceof Error ? error.message : String(error)}\n`);
  emit({ continue: true });
}
