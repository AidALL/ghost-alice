#!/usr/bin/env node

import fs from "node:fs";
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

function inputSessionId(platform, input) {
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



function reminderText(hook) {
  try {
    const data = JSON.parse(fs.readFileSync(new URL("./reminder_texts.json", import.meta.url), "utf8"));
    return typeof data[hook] === "string" ? data[hook] : "";
  } catch {
    return "";
  }
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




function taskRouterReleaseMessage(sessionId, statePath) {
  return [
    reminderText("task-router"),
    `intake-ready: current input recorded for session ${sessionId}.`,
    `Normal route only: use session_intent_ledger.py --read-intent --root ${JSON.stringify(path.dirname(path.dirname(path.dirname(statePath))))} --platform ${JSON.stringify(path.basename(path.dirname(path.dirname(statePath))))} --session-id ${JSON.stringify(sessionId)}. Read SQLite; ${statePath} is a compatibility export.`,
  ].join("\n");
}

function taskRouterReminderMessage(platform, input, root = "") {
  const sessionId = inputSessionId(platform, input);
  if (!sessionId || sessionId === "unknown") {
    return "hook-reminder: Normal-route registration is withheld until the native host supplies an exact session identity. Tool-free answers from supplied context remain available.";
  }

  const sessionDirPath = sessionIntentDir(platform, sessionId, root);
  const snapshot = readSessionIntentSnapshot(sessionDirPath, platform, sessionId);
  if (snapshot?.degraded) {
    return `hook-reminder: Normal-route registration is withheld: the session-intent ledger is degraded for session ${sessionId}. Follow session-intent-analyzer for any authorized operation that needs this ledger; a tool-free answer requires no ledger repair.`;
  }
  if (!snapshot?.latestInput) {
    return `hook-reminder: Normal-route registration is withheld until session-intent-analyzer records current input for session ${sessionId}. Tool-free answers from supplied context remain available.`;
  }
  return taskRouterReleaseMessage(sessionId, path.join(sessionDirPath, "intent-state.json"));
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
    : "";
  const message = body ? `${marker}${body}` : "";

  if (hook === "tool-checkpoint" && (event === "BeforeTool" || event === "PreToolUse")) {
    // The registered wrapper checks and audits every call. A healthy checkpoint
    // contributes no instruction context; decision-relevant failures come from it.
    emit(payloadFor(platform, event, ""));
    process.exit(0);
  }

  emit(payloadFor(platform, event, message));
} catch (error) {
  process.stderr.write(`[ghost-alice-hook] ${error instanceof Error ? error.message : String(error)}\n`);
  emit({ continue: true });
}
