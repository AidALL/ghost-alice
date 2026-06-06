#!/usr/bin/env node
// Derive the downstream gate from the model-recorded security decision.
//
// Write-on-block-only: write a block gate ONLY when the model recorded a block
// that matches the current input lineage. Never overwrite an existing gate on
// allow / absent / stale. The enforcer's staleness check neutralizes prior-turn
// block gates, and absence means silent allow. This keeps the
// tool-checkpoint dispatcher a dumb reader and preserves its size/contract.

import fs from "node:fs";
import path from "node:path";

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

function latestIntentEvent(sessionDir) {
  let lines = [];
  try {
    lines = fs.readFileSync(path.join(sessionDir, "intent-events.jsonl"), "utf8")
      .trim()
      .split(/\r?\n/u)
      .filter(Boolean);
  } catch {
    return null;
  }
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    try {
      const row = JSON.parse(lines[index]);
      if (row && row.event === "user-input-observed") {
        return row;
      }
    } catch {
      // skip malformed line
    }
  }
  return null;
}

function decisionMatchesLatestEvent(record, latestEvent) {
  if (!latestEvent) {
    return false;
  }
  const recEvent = String(record.input_event_id || "").trim();
  const recDigest = String(record.input_digest || "").trim();
  if (recEvent && latestEvent.event_id) {
    return recEvent === latestEvent.event_id;
  }
  if (recDigest && latestEvent.input_digest) {
    return recDigest === latestEvent.input_digest;
  }
  return false;
}

// Returns the written gate object, or null when nothing is written (allow/absent/stale).
export function deriveDownstreamGateFromDecision(sessionDir, platform, sessionId) {
  const state = readJsonFile(path.join(sessionDir, "intent-state.json"), null);
  if (!state || state.schema_version !== "session-intent-ledger.v1") {
    return null;
  }
  const record = state.model_security_decision;
  if (!record || typeof record !== "object") {
    return null;
  }
  if (String(record.decision || "").trim().toLowerCase() !== "block") {
    return null;
  }
  const latestEvent = latestIntentEvent(sessionDir);
  if (!decisionMatchesLatestEvent(record, latestEvent)) {
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
    input_digest: (latestEvent && latestEvent.input_digest) || "",
    input_event_id: (latestEvent && latestEvent.event_id) || "",
    input_char_count: (latestEvent && latestEvent.input_char_count) || 0,
    updated_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
  };
  writeJsonFile(path.join(sessionDir, "downstream-gates.json"), gate);
  return gate;
}
