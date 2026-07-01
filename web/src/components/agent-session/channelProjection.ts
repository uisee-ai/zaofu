import type { ChannelDetail } from "../../api/types.js";
import type {
  AgentConversation,
  AgentPartKind,
  AgentSessionCard,
  AgentSessionPart,
  AgentSessionRun,
  AgentSessionStatus,
  AgentSessionThread,
  AgentSessionTurn,
} from "./types.js";
import {
  agentPartKindFromValue,
  agentPartTitle,
  agentRunStatus,
} from "./agentUiEvent.js";

function textValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function recordString(row: Record<string, unknown> | null | undefined, key: string, fallback = ""): string {
  const value = row?.[key];
  if (value === null || value === undefined) return fallback;
  return String(value);
}

function ensureThread(threads: Map<string, AgentSessionThread>, id: string, title?: string): AgentSessionThread {
  const threadId = id || "main";
  const existing = threads.get(threadId);
  if (existing) return existing;
  const thread: AgentSessionThread = {
    id: threadId,
    title: title || (threadId === "main" ? "main" : threadId.startsWith("member:") ? `@${threadId.slice(7)}` : threadId),
    status: "idle",
    turns: [],
    participantRefs: [],
  };
  threads.set(threadId, thread);
  return thread;
}

function ensureTurn(thread: AgentSessionThread, id: string, ts?: string): AgentSessionTurn {
  const turnId = id || `turn-${thread.turns.length + 1}`;
  const existing = thread.turns.find((item) => item.id === turnId);
  if (existing) return existing;
  const turn: AgentSessionTurn = { id: turnId, threadId: thread.id, runs: [], cards: [], ts };
  thread.turns.push(turn);
  return turn;
}

function ensureRun(turn: AgentSessionTurn, id: string, patch: Partial<AgentSessionRun>): AgentSessionRun {
  const runId = id || `run-${turn.runs.length + 1}`;
  const existing = turn.runs.find((item) => item.id === runId);
  if (existing) {
    Object.assign(existing, { ...patch, parts: existing.parts });
    return existing;
  }
  const run: AgentSessionRun = { id: runId, threadId: turn.threadId, status: "streaming", parts: [], sourceEvents: [], ...patch };
  turn.runs.push(run);
  return run;
}

function upsertPart(run: AgentSessionRun, part: AgentSessionPart): void {
  const existing = run.parts.find((item) => item.id === part.id);
  if (existing) Object.assign(existing, part);
  else run.parts.push(part);
}

function addCard(turn: AgentSessionTurn, card: AgentSessionCard): void {
  if (!turn.cards.some((item) => item.id === card.id)) turn.cards.push(card);
}

function finalizeThreads(threads: Map<string, AgentSessionThread>, activeThreadId: string): AgentSessionThread[] {
  const out = [...threads.values()];
  for (const thread of out) {
    const runs = thread.turns.flatMap((turn) => turn.runs);
    const activeRun = [...runs].reverse().find((run) => ["streaming", "submitted", "queued", "waiting_input"].includes(run.status));
    const latestRun = latestRunByUpdatedAt(runs);
    thread.activeRunId = activeRun?.id;
    thread.status = activeRun ? "streaming" : latestRun?.status ?? "idle";
    thread.unseenCount = thread.id !== activeThreadId && ["streaming", "waiting_input", "queued", "failed"].includes(thread.status) ? 1 : 0;
    thread.updatedAt = [...thread.turns].reverse().find((turn) => turn.ts)?.ts || thread.updatedAt;
  }
  return out.sort((left, right) => {
    if (left.id === activeThreadId) return -1;
    if (right.id === activeThreadId) return 1;
    return String(right.updatedAt || right.id).localeCompare(String(left.updatedAt || left.id));
  });
}

function latestRunByUpdatedAt(runs: AgentSessionRun[]): AgentSessionRun | undefined {
  return [...runs].sort((left, right) => {
    const leftTime = left.updatedAt || "";
    const rightTime = right.updatedAt || "";
    if (leftTime || rightTime) return leftTime.localeCompare(rightTime);
    return left.id.localeCompare(right.id);
  }).at(-1);
}

function latestCompletedChannelRunsByTarget(rows: Record<string, unknown>[]): Map<string, string> {
  const latest = new Map<string, string>();
  for (const row of rows) {
    const status = channelRunStatus(recordString(row, "live_status") || recordString(row, "status"));
    if (status !== "completed") continue;
    const target = recordString(row, "target_member_id") || recordString(row, "member_id");
    if (!target) continue;
    const key = channelRunSortKey(row);
    if (!latest.has(target) || key > String(latest.get(target) || "")) latest.set(target, key);
  }
  return latest;
}

function isStaleFailedChannelRun(
  row: Record<string, unknown>,
  currentMemberIds: Set<string>,
  latestCompletedByTarget: Map<string, string>,
): boolean {
  const status = channelRunStatus(recordString(row, "live_status") || recordString(row, "status"));
  if (status !== "failed") return false;
  const target = recordString(row, "target_member_id") || recordString(row, "member_id");
  if (!target) return false;
  if (currentMemberIds.size && !currentMemberIds.has(target)) return true;
  const latestCompleted = latestCompletedByTarget.get(target);
  return Boolean(latestCompleted && channelRunSortKey(row) < latestCompleted);
}

function channelRunSortKey(row: Record<string, unknown>): string {
  return (
    recordString(row, "updated_at")
    || recordString(row, "created_at")
    || recordString(row, "ts")
    || recordString(row, "event_id")
    || recordString(row, "request_id")
    || recordString(row, "run_id")
  );
}

export function buildChannelConversation(detail: ChannelDetail | null, selectedChannelId: string, activeThreadId = "main"): AgentConversation {
  const threads = new Map<string, AgentSessionThread>();
  ensureThread(threads, "main", "main");
  const rawThreads = recordValue(detail?.threads);
  for (const key of Object.keys(rawThreads ?? {})) ensureThread(threads, key);
  const messages = (detail?.messages ?? detail?.recent_messages ?? []).filter((item): item is Record<string, unknown> => Boolean(recordValue(item)));
  const memberIds = new Set(
    (detail?.members ?? [])
      .filter((item): item is Record<string, unknown> => Boolean(recordValue(item)))
      .map((item) => recordString(item, "member_id"))
      .filter(Boolean),
  );
  const rawRequests = (detail?.reply_requests ?? []).filter((item): item is Record<string, unknown> => Boolean(recordValue(item)));
  const rawProviderRuns = (detail?.provider_runs ?? detail?.agent_session_runs ?? []).filter((item): item is Record<string, unknown> => Boolean(recordValue(item)));
  const latestCompletedByTarget = latestCompletedChannelRunsByTarget([...rawRequests, ...rawProviderRuns]);
  // Local placeholder reply_requests are produced by App.tsx
  // channelDetailWithPendingMessage (id prefix "local-reply-") to show a
  // "Working" badge before the backend's channel.agent.reply.requested event
  // makes its way through SSE. Once the real backend entry arrives with the
  // same (target_member_id, message_id) the local placeholder must be
  // dropped — otherwise both render as independent runs inside the same
  // turn and the user sees a duplicated Working / Done / reply body
  // (channel review w4xl2gi11 follow-up; user-reported 2026-06-05).
  const realRequestKeys = new Set(
    rawRequests
      .filter((item) => !recordString(item, "request_id").startsWith("local-"))
      .map((item) =>
        `${recordString(item, "target_member_id")}|${recordString(item, "message_id")}`,
      ),
  );
  const dedupedRequests = rawRequests.filter((item) => {
    const id = recordString(item, "request_id");
    if (!id.startsWith("local-")) return true;
    const key = `${recordString(item, "target_member_id")}|${recordString(item, "message_id")}`;
    return !realRequestKeys.has(key);
  });
  const requests = dedupedRequests.filter((item) => !isStaleFailedChannelRun(item, memberIds, latestCompletedByTarget));
  const providerRuns = rawProviderRuns.filter((item) => !isStaleFailedChannelRun(item, memberIds, latestCompletedByTarget));
  const requestById = new Map(requests.map((item) => [recordString(item, "request_id"), item]));
  for (const message of messages) {
    const threadId = recordString(message, "thread_id", "main");
    const role = recordString(message, "role") || (recordString(message, "member_id") ? "assistant" : "user");
    const messageId = recordString(message, "message_id") || recordString(message, "event_id") || `${threadId}-${messages.indexOf(message)}`;
    const rawText = recordString(message, "text") || recordString(message, "message") || recordString(message, "summary");
    const text = role === "assistant" ? stripChannelResultHeading(rawText) : rawText;
    const thread = ensureThread(threads, threadId);
    if (role === "assistant") {
      const refs = recordValue(message.refs);
      const requestId = textValue(refs?.request_id || messageId);
      const request = requestById.get(requestId);
      const runId = textValue(refs?.run_id || request?.run_id || request?.provider_run_id || requestId);
      const turn = ensureTurn(thread, recordString(request, "message_id", messageId), recordString(message, "ts"));
      const run = ensureRun(turn, runId, {
        provider: recordString(message, "source") || recordString(request, "provider") || recordString(request, "backend"),
        memberId: recordString(message, "member_id") || recordString(request, "target_member_id"),
        status: "completed",
        updatedAt: recordString(message, "ts"),
      });
      upsertPart(run, {
        id: `message-${messageId}`,
        runId: run.id,
        kind: "text",
        state: "completed",
        title: "Reply",
        content: text,
        updatedAt: recordString(message, "ts"),
        refs: refs ?? undefined,
      });
    } else {
      const turn = ensureTurn(thread, messageId, recordString(message, "ts"));
      const origin = recordValue(message.origin);
      turn.user = {
        id: messageId,
        role: role === "system" ? "system" : "user",
        label: recordString(message, "member_id") || recordString(message, "actor") || (role === "system" ? "System" : "You"),
        content: text,
        ts: recordString(message, "ts"),
        memberId: recordString(message, "member_id"),
        provider: recordString(message, "source"),
        refs: recordValue(message.refs) ?? undefined,
        origin: origin && recordString(origin, "channel")
          ? { channel: recordString(origin, "channel"), chat_id: recordString(origin, "chat_id") }
          : undefined,
      };
    }
    thread.updatedAt = recordString(message, "ts") || thread.updatedAt;
  }
  for (const request of requests) {
    const threadId = recordString(request, "thread_id", "main");
    const requestId = recordString(request, "request_id") || recordString(request, "event_id");
    const runId = recordString(request, "run_id") || recordString(request, "provider_run_id") || requestId;
    const thread = ensureThread(threads, threadId);
    const turn = ensureTurn(thread, recordString(request, "message_id") || requestId, recordString(request, "created_at"));
    const status = channelRunStatus(recordString(request, "status", "pending"));
    const run = ensureRun(turn, runId, {
      provider: recordString(request, "provider") || recordString(request, "backend"),
      memberId: recordString(request, "target_member_id"),
      status,
      updatedAt: recordString(request, "updated_at") || recordString(request, "created_at"),
      providerSessionId: recordString(request, "provider_session_id"),
    });
    if (status !== "completed") {
      upsertPart(run, {
        id: `reply-${requestId}-status`,
        runId: run.id,
        kind: status === "failed" ? "error" : status === "streaming" || status === "submitted" ? "thinking" : "status",
        state: status,
        title: channelStatusTitle(status),
        summary: recordString(request, "reason") || channelStatusSummary(status, recordString(request, "target_member_id")),
        updatedAt: recordString(request, "updated_at") || recordString(request, "created_at"),
      });
    }
    thread.updatedAt = run.updatedAt || thread.updatedAt;
  }
  for (const providerRun of providerRuns) {
    const runId = recordString(providerRun, "run_id") || recordString(providerRun, "provider_run_id");
    if (!runId) continue;
    const threadId = recordString(providerRun, "thread_id", "main");
    const requestId = recordString(providerRun, "request_id") || runId;
    const thread = ensureThread(threads, threadId);
    const turn = ensureTurn(
      thread,
      recordString(providerRun, "message_id") || requestId,
      recordString(providerRun, "created_at") || recordString(providerRun, "started_at"),
    );
    const status = channelRunStatus(recordString(providerRun, "live_status") || recordString(providerRun, "status", "pending"));
    const run = ensureRun(turn, runId, {
      provider: recordString(providerRun, "provider") || recordString(providerRun, "backend"),
      memberId: recordString(providerRun, "target_member_id") || recordString(providerRun, "member_id"),
      status,
      updatedAt: recordString(providerRun, "updated_at") || recordString(providerRun, "started_at"),
      providerSessionId: recordString(providerRun, "provider_session_id"),
    });
    const parts = Array.isArray(providerRun.parts)
      ? providerRun.parts.filter((item): item is Record<string, unknown> => Boolean(recordValue(item)))
      : [];
    for (const part of parts) {
      const rawKind = recordString(part, "kind", "status");
      const rawState = recordString(part, "state", status);
      const kind = channelPartKind(rawKind);
      const title = isChannelFinalResult(rawKind)
        ? channelPartTitle(rawKind)
        : recordString(part, "title") || channelPartTitle(rawKind);
      const hasFinalReply = run.parts.some((item) =>
        item.kind === "text"
        && item.state === "completed"
        && item.id.startsWith("message-")
      );
      if (kind === "text" && (hasFinalReply || rawState === "delta")) continue;
      const partId = recordString(part, "part_id") || recordString(part, "id") || `part-${run.parts.length + 1}`;
      upsertPart(run, {
        id: partId,
        runId: run.id,
        kind,
        state: channelRunStatus(rawState),
        title,
        summary: stripChannelResultHeading(recordString(part, "summary")),
        content: kind === "text" ? stripChannelResultHeading(recordString(part, "content")) : recordString(part, "content"),
        seq: Number(part.seq || 0) || undefined,
        toolCallId: recordString(part, "tool_call_id") || undefined,
        toolName: recordString(part, "tool_name") || undefined,
        updatedAt: recordString(part, "updated_at") || recordString(providerRun, "updated_at"),
        refs: recordValue(part.refs) ?? undefined,
      });
    }
    if (!parts.length && ["streaming", "submitted", "queued", "waiting_input"].includes(status)) {
      upsertPart(run, {
        id: `provider-${runId}-status`,
        runId: run.id,
        kind: status === "streaming" || status === "submitted" ? "thinking" : "status",
        state: status,
        title: channelStatusTitle(status),
        summary: recordString(providerRun, "reason") || channelStatusSummary(status, recordString(providerRun, "target_member_id") || recordString(providerRun, "member_id")),
        updatedAt: run.updatedAt,
      });
    }
    thread.updatedAt = run.updatedAt || thread.updatedAt;
  }
  return { id: `channel:${selectedChannelId}`, surface: "channel_group", activeThreadId, threads: finalizeThreads(threads, activeThreadId) };
}

function channelRunStatus(value: string): AgentSessionStatus {
  return agentRunStatus(value);
}

function channelStatusTitle(status: AgentSessionStatus): string {
  if (status === "streaming" || status === "submitted") return "Working";
  if (status === "queued" || status === "waiting_input") return "Waiting";
  if (status === "failed") return "Failed";
  if (status === "completed") return "Done";
  if (status === "cancelled") return "Cancelled";
  if (status === "stale") return "Stale";
  return "Waiting";
}

function channelStatusSummary(status: AgentSessionStatus, memberId: string): string {
  const target = memberId ? `@${memberId}` : "agent";
  if (status === "streaming" || status === "submitted") return `${target} is working`;
  if (status === "queued" || status === "waiting_input") return `${target} is waiting`;
  if (status === "failed") return `${target} failed`;
  return status;
}

function channelPartKind(value: string): AgentPartKind {
  if (isChannelFinalResult(value)) return "text";
  return agentPartKindFromValue(value);
}

function channelPartTitle(value: string): string {
  if (isChannelFinalResult(value)) return "Response";
  return agentPartTitle(value);
}

function isChannelFinalResult(value: string): boolean {
  return value.trim().toLowerCase() === "result";
}

function stripChannelResultHeading(value: string): string {
  let out = value;
  for (let index = 0; index < 3; index += 1) {
    const next = out.replace(/^\s*(?:[。.!?]\s*)?(?:#{1,6}\s*)?(?:\*\*)?Result(?:\*\*)?\s*(?:[:：])?(?:\r?\n|$)+/i, "");
    if (next === out) break;
    out = next;
  }
  return out.trimStart();
}
