#!/usr/bin/env node

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { deriveDownstreamGateFromDecision } from "./derive_downstream_gate.mjs";

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

function pendingManifestPath(platform) {
  return path.join(userHome(), ".ghost-alice", "pending-merges", platform, "manifest.json");
}

function sessionIntentRoot(configuredRoot = "") {
  const configured = firstNonEmpty(configuredRoot, process.env.GHOST_ALICE_SESSION_INTENT_ROOT);
  if (configured) {
    return configured;
  }
  return path.join(userHome(), ".ghost-alice", "session-intent");
}

function sessionIntentDir(platform, sessionId, root = "") {
  const safePlatform = safePathComponent(platform || "codex");
  const safeSession = safePathComponent(sessionId || "unknown");
  return path.join(sessionIntentRoot(root), safePlatform, safeSession);
}

function currentSessionPointerPath(platform, root = "") {
  const safePlatform = safePathComponent(platform || "codex");
  return path.join(sessionIntentRoot(root), safePlatform, "current-session.json");
}

function safePathComponent(value, fallback = "unknown") {
  const cleaned = String(value || fallback)
    .trim()
    .replace(/[^A-Za-z0-9_.=-]+/g, "-")
    .replace(/^[.-]+|[.-]+$/g, "");
  return cleaned || fallback;
}

function readCurrentSessionPointer(platform, root = "") {
  const pointer = readJsonFile(currentSessionPointerPath(platform, root), {});
  if (!pointer || pointer.schema_version !== "session-intent-current.v1") {
    return "";
  }
  return firstNonEmpty(pointer.session_id);
}

function sessionIntentContext(platform, input, root = "") {
  const sessionId = inputSessionId(platform, input, root, "");
  if (!sessionId) {
    return null;
  }

  const pointer = readJsonFile(currentSessionPointerPath(platform, root), {});
  if (!pointer || pointer.schema_version !== "session-intent-current.v1") {
    return null;
  }
  if (safePathComponent(pointer.session_id, "") !== sessionId) {
    return null;
  }

  const statePath = firstNonEmpty(
    pointer.state_path,
    path.join(sessionIntentDir(platform, sessionId, root), "intent-state.json"),
  );
  if (!statePath) {
    return null;
  }
  return {
    sessionId,
    statePath,
    eventsPath: path.join(path.dirname(statePath), "intent-events.jsonl"),
  };
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
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
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
  return safePathComponent(firstNonEmpty(
    input.session_id,
    input.sessionId,
    input.conversation_id,
    input.thread_id,
    process.env.GHOST_ALICE_SESSION_ID,
    readCurrentSessionPointer(platform, root),
    fallback,
  ), fallback);
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

function platformUsesPreToolPermissionDecision(platform, event) {
  return ["claude", "codex"].includes(String(platform || "").toLowerCase())
    && event === "PreToolUse";
}

function payloadFor(platform, event, message) {
  if (platformUsesPreToolPermissionDecision(platform, event)) {
    return message ? { hookSpecificOutput: { hookEventName: event, additionalContext: message } } : {};
  }
  return message
    ? { continue: true, systemMessage: message, hookSpecificOutput: { hookEventName: event, additionalContext: message } }
    : { continue: true };
}

function denialPayload(platform, event, message, reason) {
  if (platformUsesPreToolPermissionDecision(platform, event)) {
    return { hookSpecificOutput: { hookEventName: event, permissionDecision: "deny", permissionDecisionReason: reason } };
  }
  return { continue: true, decision: "deny", reason, systemMessage: message, hookSpecificOutput: { hookEventName: event, additionalContext: message } };
}

function toolCheckpointEnforcementEnabled(platform = "codex", env = process.env) {
  const platformKey = String(platform || "")
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "_");
  const raw = String(
    env.GHOST_ALICE_TOOL_CHECKPOINT_ENFORCEMENT
      || env[`GHOST_ALICE_${platformKey}_TOOL_CHECKPOINT_ENFORCEMENT`]
      || env[`GHOST_ALICE_${platformKey}_ENFORCE_TOOL_CHECKPOINT`]
      || "enforce",
  ).trim().toLowerCase();
  return !new Set(["0", "false", "off", "no", "reminder"]).has(raw);
}

function downstreamGateState(platform, input, root = "") {
  const sessionId = inputSessionId(platform, input, root, "");
  if (!sessionId) {
    return null;
  }
  const gatePath = path.join(sessionIntentDir(platform, sessionId, root), "downstream-gates.json");
  const state = readJsonFile(gatePath, null);
  if (!state || state.schema_version !== "downstream-gates.v1") {
    return null;
  }
  if (state.gate !== "jailbreak-detector") {
    return null;
  }
  const dirPath = sessionIntentDir(platform, sessionId, root);
  const latestEvent = latestIntentEvent(dirPath);
  const match = downstreamGateMatchesLatestEvent(state, latestEvent);
  if (!match.ok) {
    return { ...state, stale: true, stale_reason: match.reason, legacy: match.legacy === true };
  }
  return state;
}

function readJsonLine(line) {
  try {
    const row = JSON.parse(line);
    return row && typeof row === "object" ? row : null;
  } catch {
    return null;
  }
}

function latestIntentEvent(sessionDirPath) {
  const eventsPath = path.join(sessionDirPath, "intent-events.jsonl");
  let lines = [];
  try {
    lines = fs.readFileSync(eventsPath, "utf8").trim().split(/\r?\n/u).filter(Boolean);
  } catch {
    return null;
  }
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const row = readJsonLine(lines[index]);
    if (row && row.event === "user-input-observed") {
      return row;
    }
  }
  return null;
}

function downstreamGateMatchesLatestEvent(gate, latestEvent) {
  if (!gate) return { ok: true, legacy: true };
  if (!gate.input_event_id && !gate.input_digest) {
    return { ok: false, legacy: true, reason: "legacy downstream gate missing input lineage" };
  }
  if (!latestEvent) {
    return { ok: false, legacy: false, reason: "stale downstream gate: latest input event missing" };
  }
  if (gate.input_event_id && latestEvent.event_id && gate.input_event_id !== latestEvent.event_id) {
    return { ok: false, legacy: false, reason: "stale downstream gate: input_event_id mismatch" };
  }
  if (gate.input_digest && latestEvent.input_digest && gate.input_digest !== latestEvent.input_digest) {
    return { ok: false, legacy: false, reason: "stale downstream gate: input_digest mismatch" };
  }
  return { ok: true, legacy: false };
}

function taskRouterReleaseMessage(sessionId, statePath, gatePath, mode) {
  const gateDetail = mode === "present-nonblock"
    ? `downstream-gate: ${gatePath} contains no current block; silent allow invariant applies.`
    : `downstream-gate: ${gatePath} absent; silent allow invariant applies unless a current-lineage model block is recorded.`;
  return [
    "hook-reminder: task-router waits until session-intent preflight exists and no current-lineage block gate is recorded.",
    "Absent downstream-gates.json means silent allow unless a current-lineage model block is recorded.",
    "After release, read the ledger, decompose accepted intent into atomic meaning units, choose focus-layer micro|meso|macro|meta plus scope-reopen target on mismatch, then assign output, verification, lifecycle, and boundary skills before downstream work/tool calls.",
    "task-router consumes session-intent and jailbreak gate context; it performs routing decisions but does not infer raw user intent, own intake, or own tool permission.",
    "Every user input requires task-router, including a simple question, opinion, clarification, status comment, or follow-up.",
    "Do not skip task-router because the turn looks like answer-only conversation or because a prior turn was already routed.",
    "Do not describe the governance order as task-router before session-intent-analyzer or before the jailbreak-detector downstream gate.",
    `gate-opened: jailbreak-detector silent allow for session ${sessionId}; no current block decision recorded.`,
    `intent-ledger: read ${statePath} after session-intent preflight.`,
    gateDetail,
    "task-router-step: wait-for-jailbreak-decision → read-session-intent-ledger → atomic meaning decomposition → focus-layer/scope-reopen → skill assignment.",
  ].join("\n");
}

function taskRouterReminderMessage(platform, input, root = "") {
  const sessionId = inputSessionId(platform, input, root, "");
  if (!sessionId || sessionId === "unknown") {
    return "hook-reminder: task-router withheld until session-intent-analyzer writes current-session.json and the current-lineage block check can run. Do not run task-router yet.";
  }

  const sessionDirPath = sessionIntentDir(platform, sessionId, root);
  const gate = downstreamGateState(platform, { ...input, session_id: sessionId }, root);
  if (!gate) {
    const latestEvent = latestIntentEvent(sessionDirPath);
    if (!latestEvent) {
      return `hook-reminder: task-router withheld until session-intent-analyzer writes current-session.json for session ${sessionId}. Continue intake/bootstrap; do not run task-router yet.`;
    }
    const pointer = readJsonFile(currentSessionPointerPath(platform, root), {});
    const statePath = firstNonEmpty(pointer && pointer.schema_version === "session-intent-current.v1" ? pointer.state_path : "", path.join(sessionDirPath, "intent-state.json"));
    return taskRouterReleaseMessage(sessionId, statePath, path.join(sessionDirPath, "downstream-gates.json"), "absent");
  }
  if (gate.stale) {
    return `hook-reminder: jailbreak-detector downstream gate is stale for the latest input. ${gate.stale_reason}. Continue intake/routing; do not reuse the stale decision as current block/allow.`;
  }
  const decision = String(gate.decision || "unknown").trim() || "unknown";
  if (gate.opened === false || decision === "block") {
    return `hook-reminder: task-router withheld because jailbreak-detector downstream gate is not open. decision=${decision}. Do not run task-router or downstream work.`;
  }

  const pointer = readJsonFile(currentSessionPointerPath(platform, root), {});
  const statePath = firstNonEmpty(pointer && pointer.schema_version === "session-intent-current.v1" ? pointer.state_path : "", path.join(sessionDirPath, "intent-state.json"));
  return taskRouterReleaseMessage(sessionId, statePath, path.join(sessionDirPath, "downstream-gates.json"), "present-nonblock");
}

function downstreamGateDenialReason(state) {
  const decision = String(state?.decision || "").trim() || "unknown";
  const rules = Array.isArray(state?.rules) && state.rules.length
    ? ` rules=${state.rules.join(",")}.`
    : "";
  return [
    "jailbreak-detector blocked downstream tool execution.",
    `decision=${decision}.${rules}`,
    "Resolve the user-intent gate before running tools.",
  ].join(" ");
}

function toolCheckpointDecision(platform, input, sessionIntentRootArg = "") {
  if (!toolCheckpointEnforcementEnabled(platform)) {
    return null;
  }
  const downstreamGate = downstreamGateState(platform, input, sessionIntentRootArg);
  if (!downstreamGate?.stale
    && (downstreamGate?.opened === false || downstreamGate?.decision === "block")) {
    return { deny: true, reason: downstreamGateDenialReason(downstreamGate) };
  }
  return null;
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
    const gateSession = inputSessionId(platform, input, args["session-intent-root"], "");
    if (gateSession && gateSession !== "unknown") {
      deriveDownstreamGateFromDecision(sessionIntentDir(platform, gateSession, args["session-intent-root"]), platform, gateSession);
    }
    const decision = toolCheckpointDecision(platform, input, args["session-intent-root"]);
    if (decision && decision.deny) {
      emit(denialPayload(platform, event, message, decision.reason));
      process.exit(0);
    }
    if (decision && decision.suppressMessage) {
      emit(payloadFor(platform, event, ""));
      process.exit(0);
    }
    if (decision && decision.warning) {
      const warningMessage = message ? `${message}\n${decision.warning}` : decision.warning;
      emit(payloadFor(platform, event, warningMessage));
      process.exit(0);
    }
  }

  emit(payloadFor(platform, event, message));
} catch (error) {
  process.stderr.write(`[ghost-alice-hook] ${error instanceof Error ? error.message : String(error)}\n`);
  emit({ continue: true });
}
