#!/usr/bin/env node
// Derive the downstream gate from the model-recorded security decision.
//
// Write-on-block-only: write a block gate ONLY when the model recorded a block
// that matches the current input lineage. Never overwrite an existing gate on
// allow / absent / stale. The enforcer's staleness check neutralizes prior-turn
// block gates, and absence means silent allow. This keeps the
// tool-checkpoint dispatcher a dumb reader and preserves its size/contract.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export class DownstreamGatePersistenceError extends Error {
  constructor(gate, cause) {
    super("Downstream gate persistence failed after a current model block was verified.", { cause });
    this.name = "DownstreamGatePersistenceError";
    this.gate = gate;
  }
}

let ledgerPython;

function pythonExecutable() {
  if (ledgerPython) return ledgerPython;
  const candidates = [process.env.GHOST_ALICE_PYTHON, "python3", "python"];
  for (const directory of ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]) {
    candidates.push(path.join(directory, "python3"));
    try {
      for (const name of fs.readdirSync(directory).filter((item) => /^python3\.[0-9]+$/u.test(item)).sort().reverse()) {
        candidates.push(path.join(directory, name));
      }
    } catch { /* optional interpreter directory */ }
  }
  for (const candidate of [...new Set(candidates.filter(Boolean))]) {
    const check = spawnSync(candidate, ["-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
      { encoding: "utf8", timeout: 5000, windowsHide: true });
    if (!check.error && check.status === 0) {
      ledgerPython = candidate;
      return candidate;
    }
  }
  throw new Error("Session intent reader requires Python 3.11+");
}

function ledgerScript() {
  const home = process.env.HOME || process.env.USERPROFILE
    || (process.env.HOMEDRIVE && process.env.HOMEPATH ? `${process.env.HOMEDRIVE}${process.env.HOMEPATH}` : os.homedir());
  const candidates = [
    path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "session-intent-analyzer", "scripts", "session_intent_ledger.py"),
    path.join(home, ".agents", "skills", "session-intent-analyzer", "scripts", "session_intent_ledger.py"),
    path.join(home, ".claude", "skills", "session-intent-analyzer", "scripts", "session_intent_ledger.py"),
  ];
  if (process.env.CLAUDE_CONFIG_DIR) {
    candidates.push(path.join(process.env.CLAUDE_CONFIG_DIR, "skills", "session-intent-analyzer", "scripts", "session_intent_ledger.py"));
  }
  for (const candidate of candidates) {
    try { if (fs.statSync(candidate).isFile()) return candidate; } catch { /* next install location */ }
  }
  throw new Error("Session intent reader dependency unavailable");
}

function readLedger(sessionDir, platform, sessionId, mode) {
  const result = spawnSync(pythonExecutable(), [ledgerScript(), mode,
    "--root", path.dirname(path.dirname(sessionDir)), "--platform", platform, "--session-id", sessionId],
    { encoding: "utf8", timeout: 10000, maxBuffer: 16 * 1024 * 1024, windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
  if (result.error || result.status !== 0) {
    throw new Error("Authoritative session intent read failed");
  }
  return JSON.parse(result.stdout);
}

function writeJsonFile(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

export function isCanonicalIdentityComponent(value) {
  return typeof value === "string" && value.length > 0 && value.length <= 120
    && /^[A-Za-z0-9_.=-]+$/u.test(value) && !/^[.-]|[.-]$/u.test(value);
}

function identityMatches(value, platform, sessionId, schema = "") {
  return value && typeof value === "object" && !Array.isArray(value)
    && value.platform === platform && value.session_id === sessionId
    && (!schema || value.schema_version === schema);
}

function latestIntentEvent(sessionDir, platform, sessionId) {
  const rows = readLedger(sessionDir, platform, sessionId, "--read-events");
  if (!Array.isArray(rows)) throw new Error("Invalid session event reader result");
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const row = rows[index];
    if (row && row.event === "user-input-observed") {
      // A mixed latest input makes legacy freshness unknown. Do not revive
      // an older own-session input from before a foreign/headerless row.
      if (!identityMatches(row, platform, sessionId)) return null;
      const eventId = typeof row.event_id === "string" ? row.event_id : "";
      const digest = typeof row.input_digest === "string" ? row.input_digest : "";
      return eventId || digest ? { ...row, event_id: eventId, input_digest: digest, authoritative: false } : null;
    }
  }
  return null;
}

export function readSessionIntentSnapshot(sessionDir, platform, sessionId) {
  if (!isCanonicalIdentityComponent(platform) || !isCanonicalIdentityComponent(sessionId)) return null;
  try {
    const state = readLedger(sessionDir, platform, sessionId, "--read-state");
    if (!identityMatches(state, platform, sessionId, "session-intent-ledger.v1")) return null;
    // Failed bound intake means the persisted input can belong to a prior turn.
    // This invalidates freshness; it does not create a model security decision.
    if (fs.existsSync(path.join(sessionDir, "ledger-degraded.json"))) {
      return { state, lineageSource: "degraded", degraded: true, latestInput: null };
    }
    const anchorFields = ["ledger_revision", "latest_input_event_id", "latest_input_digest", "latest_input_char_count"];
    if (anchorFields.some((key) => Object.hasOwn(state, key))) {
      // The state is the authority even while its audit projection is pending.
      // An incomplete/invalid new anchor must never revive an older audit row.
      const id = state.latest_input_event_id;
      const digest = state.latest_input_digest;
      const valid = Number.isSafeInteger(state.ledger_revision) && state.ledger_revision >= 0
        && typeof id === "string" && typeof digest === "string"
        && id.length > 0 && digest.length > 0
        && Number.isSafeInteger(state.latest_input_char_count) && state.latest_input_char_count >= 0;
      return { state, lineageSource: "state", latestInput: valid ? {
        event: "user-input-observed", platform, session_id: sessionId,
        event_id: id, input_digest: digest, input_char_count: state.latest_input_char_count,
        authoritative: true,
      } : null };
    }
    return { state, lineageSource: "legacy-audit", latestInput: latestIntentEvent(sessionDir, platform, sessionId) };
  } catch {
    // Read failures invalidate freshness but never invent a model block or
    // silently replace SQLite authority with a JSON compatibility projection.
    process.stderr.write("[ghost-alice-hook] authoritative session intent reader degraded\n");
    return { state: {}, lineageSource: "degraded", degraded: true, latestInput: null };
  }
}

export function inputLineageMatches(record, latestInput) {
  if (!record || !latestInput) return false;
  const recEvent = typeof record.input_event_id === "string" ? record.input_event_id : "";
  const recDigest = typeof record.input_digest === "string" ? record.input_digest : "";
  if (recEvent && recEvent !== latestInput.event_id) return false;
  if (latestInput.event_id) {
    if (!recEvent || recEvent !== latestInput.event_id) return false;
  } else if (latestInput.authoritative || !recDigest || recDigest !== latestInput.input_digest) {
    return false;
  }
  if (recDigest && recDigest !== latestInput.input_digest) return false;
  return true;
}

// Returns the written gate object, or null when nothing is written (allow/absent/stale).
export function deriveDownstreamGateFromDecision(sessionDir, platform, sessionId,
                                                 snapshot = readSessionIntentSnapshot(sessionDir, platform, sessionId)) {
  if (!snapshot) return null;
  const { state, latestInput } = snapshot;
  const record = state.model_security_decision;
  if (!record || typeof record !== "object") {
    return null;
  }
  if (String(record.decision || "").trim().toLowerCase() !== "block") {
    return null;
  }
  if (!inputLineageMatches(record, latestInput)) {
    return null;
  }
  const gate = {
    schema_version: "downstream-gates.v1",
    platform,
    session_id: sessionId,
    gate: "jailbreak-detector",
    decision: "block",
    opened: false,
    rules: Array.isArray(record.risk_flags) ? record.risk_flags.map(String) : [],
    evidence_summary: "carried model block decision; raw prompt omitted",
    input_digest: latestInput.input_digest || "",
    input_event_id: latestInput.event_id || "",
    input_char_count: latestInput.input_char_count || 0,
    updated_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
  };
  try {
    writeJsonFile(path.join(sessionDir, "downstream-gates.json"), gate);
  } catch (error) {
    throw new DownstreamGatePersistenceError(gate, error);
  }
  return gate;
}
