// Live streaming fold for the channel timeline (operator report 2026-07-16):
// member replies stream token deltas over the ephemeral live bus as
// agent.session.part.delta rows (state="delta"), but the channel conversation
// was built from the read-model detail only — the run sat on "Thinking" (or a
// yellow pending dot) for the whole turn and the reply appeared in one lump
// at completion. Fold live rows into the freshly built conversation the same
// way the kanban panel folds its turn deltas. Pure function — unit tested in
// tests/channelLiveStream.test.ts.

import type { EventRecord } from "../../api/types.js";
import type { AgentConversation, AgentSessionPart, AgentSessionRun } from "./types.js";
import { agentPartKindFromValue, agentPartTitle } from "./agentUiEvent.js";

export const CHANNEL_LIVE_PART_EVENT_TYPES = new Set([
  "agent.session.part.delta",
  "channel.message.stream.delta",
]);

function textValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

function payloadOf(event: EventRecord): Record<string, unknown> {
  return event.payload && typeof event.payload === "object" ? event.payload as Record<string, unknown> : {};
}

/** Rows for this channel's live agent output, in stream order. */
export function channelLiveStreamRows(events: EventRecord[], channelId: string): EventRecord[] {
  if (!channelId) return [];
  return events
    .filter((event) => {
      if (!CHANNEL_LIVE_PART_EVENT_TYPES.has(event.type)) return false;
      return textValue(payloadOf(event).channel_id) === channelId;
    })
    .sort((left, right) => {
      const l = Number(payloadOf(left).seq ?? 0);
      const r = Number(payloadOf(right).seq ?? 0);
      if (l !== r) return l - r;
      return String(left.ts || "").localeCompare(String(right.ts || ""));
    });
}

const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "cancelled", "stale"]);

/**
 * Compact a live-row buffer that outgrew its cap: text/thinking delta rows
 * merge into ONE accumulated row per (run, kind) — dropping the oldest rows
 * blindly lost the reply's opening tokens on very long turns (P2c). Non-delta
 * rows (tool activity) pass through untouched.
 */
export function compactChannelLiveRows(rows: EventRecord[], limit: number): EventRecord[] {
  if (rows.length <= limit) return rows;
  const merged = new Map<string, EventRecord>();
  const out: EventRecord[] = [];
  for (const event of rows) {
    const payload = payloadOf(event);
    const kind = textValue(payload.kind);
    const state = textValue(payload.state);
    if (state !== "delta" || (kind !== "text" && kind !== "thinking")) {
      out.push(event);
      continue;
    }
    const runKey = textValue(payload.run_id) || textValue(payload.request_id);
    const key = `${runKey}|${kind}`;
    const existing = merged.get(key);
    if (!existing) {
      const clone: EventRecord = { ...event, payload: { ...payload } };
      merged.set(key, clone);
      out.push(clone);
      continue;
    }
    const target = existing.payload as Record<string, unknown>;
    const combined = textValue(target.delta ?? target.content) + textValue(payload.delta ?? payload.content);
    target.delta = combined;
    target.content = combined;
    // Keep the FIRST row's seq so the merged block still sorts before any
    // uncompacted tail deltas of the same run.
  }
  return out.length > limit ? out.slice(-limit) : out;
}

/**
 * Fold live stream rows into a freshly built channel conversation (mutates
 * and returns it — callers rebuild the conversation per memo pass, so the
 * fold stays idempotent):
 * - text/thinking deltas accumulate into one streaming part per kind and the
 *   run flips to "streaming" (green dot, live text growth);
 * - committed non-delta parts (tool use etc.) render as live activity rows;
 * - rows for a terminal run, or a run whose final reply already folded, are
 *   stale and dropped — a finished bubble never re-grows or shows trail.
 */
export function foldChannelLiveStream(
  conversation: AgentConversation,
  rows: EventRecord[],
  channelId: string,
): AgentConversation {
  if (!rows.length || !channelId) return conversation;
  const runsById = new Map<string, { run: AgentSessionRun; threadId: string }>();
  for (const thread of conversation.threads) {
    for (const turn of thread.turns) {
      for (const run of turn.runs) runsById.set(run.id, { run, threadId: thread.id });
    }
  }
  const streamText = new Map<string, { content: string; ts: string }>();
  const streamThinking = new Map<string, { content: string; ts: string }>();
  const touchedRuns = new Set<string>();
  for (const event of rows) {
    const payload = payloadOf(event);
    const runId = textValue(payload.run_id);
    const requestId = textValue(payload.request_id);
    // The detail row keys its run by provider run_id once known, else by the
    // request id — accept either so early deltas still find their run.
    const entry = (runId ? runsById.get(runId) : undefined)
      ?? (requestId ? runsById.get(requestId) : undefined);
    if (!entry) continue;
    const { run } = entry;
    // Key accumulation by the RESOLVED run id (the row may have matched via
    // request_id while its run_id field is empty or diverges).
    const runKey = run.id;
    if (TERMINAL_RUN_STATUSES.has(run.status)) continue;
    const hasFinalReply = run.parts.some((part) => part.kind === "text" && part.id.startsWith("message-"));
    if (hasFinalReply) continue;
    const kind = textValue(payload.kind);
    const state = textValue(payload.state);
    const content = textValue(payload.delta ?? payload.content);
    if (state === "delta" && (kind === "text" || kind === "thinking")) {
      const store = kind === "text" ? streamText : streamThinking;
      const current = store.get(runKey) ?? { content: "", ts: "" };
      store.set(runKey, { content: current.content + content, ts: event.ts || current.ts });
      touchedRuns.add(runKey);
      continue;
    }
    // Committed non-delta part (tool use / tool result …) → live activity row.
    // Status placeholders ("started"/"queued" progress rows) stay out — the
    // run header already shows the live state.
    const mappedKind = agentPartKindFromValue(kind || "status");
    if (mappedKind === "status") continue;
    const partId = textValue(payload.part_id) || `live-part-${run.parts.length + 1}`;
    if (run.parts.some((part) => part.id === partId)) continue;
    const part: AgentSessionPart = {
      id: partId,
      runId: run.id,
      kind: mappedKind,
      state: "completed",
      title: agentPartTitle(kind || "status"),
      summary: content.slice(0, 120).replace(/\s+/g, " "),
      content,
      seq: Number(payload.seq ?? 0) || undefined,
      updatedAt: event.ts,
    };
    run.parts.push(part);
    touchedRuns.add(runKey);
  }
  for (const runId of touchedRuns) {
    const entry = runsById.get(runId);
    if (!entry) continue;
    const { run, threadId } = entry;
    const thinking = streamThinking.get(runId);
    if (thinking?.content) {
      run.parts.push({
        id: `live-thinking-${runId}`,
        runId: run.id,
        kind: "thinking",
        state: "streaming",
        title: "Thinking",
        summary: thinking.content.slice(0, 96).replace(/\s+/g, " "),
        content: thinking.content,
        updatedAt: thinking.ts,
      });
    }
    const text = streamText.get(runId);
    if (text?.content) {
      run.parts.push({
        id: `live-text-${runId}`,
        runId: run.id,
        kind: "text",
        state: "streaming",
        title: "Response",
        content: text.content,
        updatedAt: text.ts,
      });
    }
    // Live output is flowing: the run owns the turn regardless of what the
    // (possibly stale) request row said — green streaming dot, not yellow
    // queued, and the thread rollup follows.
    run.status = "streaming";
    const thread = conversation.threads.find((item) => item.id === threadId);
    if (thread) {
      thread.status = "streaming";
      thread.activeRunId = run.id;
    }
  }
  return conversation;
}
