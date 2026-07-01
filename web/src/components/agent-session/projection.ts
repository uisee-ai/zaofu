import type { EventRecord } from "../../api/types";
import type {
  AgentConversation, AgentSessionCard, AgentSessionPart, AgentSessionRun,
  AgentSessionThread, AgentSessionThreadRef, AgentSessionTurn,
} from "./types";
import {
  agentDeltaContent as uiDeltaContent,
  agentDeltaKind as uiDeltaKind,
  agentToolTitle,
  eventSourceRefs,
  parseActionProposal,
} from "./agentUiEvent";

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
function canonicalBackend(value: unknown): string {
  const raw = textValue(value).trim();
  if (raw === "claude" || raw === "claude-code" || raw === "claude-code-headless" || raw === "claude_headless") {
    return "claude-headless";
  }
  if (raw === "codex" || raw === "codex-cli" || raw === "codex-app-server" || raw === "codex_headless") {
    return "codex-headless";
  }
  return raw;
}
function ensureThread(
  threads: Map<string, AgentSessionThread>,
  id: string,
  title?: string,
): AgentSessionThread {
  const threadId = id || "main";
  const existing = threads.get(threadId);
  if (existing) {
    if (title && existing.title === existing.id) existing.title = title;
    return existing;
  }
  const thread: AgentSessionThread = {
    id: threadId,
    title: title || (threadId === "main" ? "main" : shortThreadTitle(threadId)),
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
function ensureRun(
  turn: AgentSessionTurn,
  id: string,
  patch: Partial<AgentSessionRun> = {},
): AgentSessionRun {
  const runId = id || `run-${turn.runs.length + 1}`;
  const existing = turn.runs.find((item) => item.id === runId);
  if (existing) {
    const nextPatch = { ...patch, parts: existing.parts };
    if (existing.status === "cancelled" && patch.status !== "cancelled") {
      nextPatch.status = "cancelled";
      nextPatch.stale = true;
    }
    Object.assign(existing, nextPatch);
    return existing;
  }
  const run: AgentSessionRun = {
    id: runId,
    threadId: turn.threadId,
    status: "streaming",
    parts: [],
    sourceEvents: [],
    ...patch,
  };
  turn.runs.push(run);
  return run;
}
function upsertPart(run: AgentSessionRun, part: AgentSessionPart): AgentSessionPart {
  const existing = run.parts.find((item) => item.id === part.id);
  if (!existing) {
    run.parts.push(part);
    return part;
  }
  existing.state = part.state || existing.state;
  existing.title = part.title || existing.title;
  existing.summary = part.summary || existing.summary;
  existing.content = part.content ?? existing.content;
  existing.seq = part.seq ?? existing.seq;
  existing.updatedAt = part.updatedAt || existing.updatedAt;
  existing.sourceEvent = part.sourceEvent || existing.sourceEvent;
  existing.refs = part.refs || existing.refs;
  return existing;
}

function appendPartContent(run: AgentSessionRun, partId: string, patch: Omit<AgentSessionPart, "id" | "content">, content: string): void {
  const existing = run.parts.find((item) => item.id === partId);
  if (existing) {
    if (content) {
      existing.content = `${existing.content || ""}${content}`;
    }
    existing.state = patch.state;
    existing.updatedAt = patch.updatedAt || existing.updatedAt;
    existing.seq = patch.seq ?? existing.seq;
    existing.sourceEvent = patch.sourceEvent || existing.sourceEvent;
    return;
  }
  upsertPart(run, { id: partId, content, ...patch });
}

function addCard(turn: AgentSessionTurn, card: AgentSessionCard): void {
  if (!turn.cards.some((item) => item.id === card.id)) {
    turn.cards.push(card);
  }
}

function shortThreadTitle(threadId: string): string {
  if (threadId === "main") return "main";
  if (threadId.startsWith("member:")) return `@${threadId.slice("member:".length)}`;
  if (threadId.length <= 10) return threadId;
  return `chat ${threadId.slice(0, 4)}`;
}

function applyDelta(run: AgentSessionRun, event: EventRecord, payload: Record<string, unknown>): void {
  const kind = uiDeltaKind(payload);
  const type = textValue(payload.message_type || payload.type).trim();
  const seq = Number(payload.seq || event.seq || run.parts.length + 1);
  const tool = textValue(payload.tool).trim();
  const content = uiDeltaContent(payload);
  const refs = eventSourceRefs(event, recordValue(payload.refs));
  if (kind === "text") {
    appendPartContent(run, "text", {
      runId: run.id,
      kind,
      state: "streaming",
      title: "Response",
      seq,
      updatedAt: event.ts,
      sourceEventId: event.id,
      sourceEventSeq: event.seq,
      sourceEvent: event,
      refs,
    }, content);
    return;
  }
  if (kind === "thinking") {
    appendPartContent(run, "thinking", {
      runId: run.id,
      kind,
      state: "streaming",
      title: "Thinking",
      summary: content.slice(0, 96).replace(/\s+/g, " "),
      seq,
      startedAt: run.startedAt || event.ts,
      updatedAt: event.ts,
      sourceEventId: event.id,
      sourceEventSeq: event.seq,
      sourceEvent: event,
      refs,
    }, content);
    return;
  }
  if (type === "tool_result") {
    upsertPart(run, {
      id: `tool-result-${seq}`,
      runId: run.id,
      kind: "tool",
      state: "completed",
      title: "Tool result",
      summary: content.slice(0, 120).replace(/\s+/g, " "),
      content,
      seq,
      updatedAt: event.ts,
      sourceEventId: event.id,
      sourceEventSeq: event.seq,
      sourceEvent: event,
      refs,
    });
    return;
  }
  if (kind === "question") {
    upsertPart(run, {
      id: `question-${seq}`,
      runId: run.id,
      kind,
      state: "waiting_input",
      title: "Question",
      summary: content.slice(0, 120).replace(/\s+/g, " "),
      content,
      seq,
      updatedAt: event.ts,
      sourceEventId: event.id,
      sourceEventSeq: event.seq,
      sourceEvent: event,
      refs,
    });
    return;
  }
  upsertPart(run, {
    id: kind === "tool" ? `tool-${seq}` : `status-${seq}`,
    runId: run.id,
    kind,
    state: kind === "tool" ? "streaming" : "submitted",
    title: kind === "tool" || kind === "tool_call" || kind === "tool_result" ? agentToolTitle(tool) : "Status",
    summary: content || tool,
    content,
    seq,
    toolName: tool,
    startedAt: event.ts,
    updatedAt: event.ts,
    sourceEventId: event.id,
    sourceEventSeq: event.seq,
    sourceEvent: event,
    refs,
  });
}

function finalizeThreads(threads: Map<string, AgentSessionThread>, activeThreadId: string): AgentSessionThread[] {
  const out = [...threads.values()];
  for (const thread of out) {
    const runs = thread.turns.flatMap((turn) => turn.runs);
    const activeRun = [...runs].reverse().find((run) => run.status === "streaming" || run.status === "submitted");
    const failedRun = [...runs].reverse().find((run) => run.status === "failed");
    thread.activeRunId = activeRun?.id;
    thread.status = activeRun ? "streaming" : failedRun ? "failed" : runs.some((run) => run.status === "completed") ? "completed" : "idle";
    thread.unseenCount = thread.id !== activeThreadId && ["streaming", "waiting_input", "queued", "failed"].includes(thread.status) ? 1 : 0;
    thread.updatedAt = [...thread.turns].reverse().find((turn) => turn.ts)?.ts || thread.updatedAt;
    for (const run of runs) {
      if (run.status === "streaming" && activeRun && run.id !== activeRun.id) {
        run.status = "stale";
        run.stale = true;
      }
      for (const part of run.parts) {
        // Resolve lingering in-progress parts (streaming OR submitted — e.g.
        // the "status-started" placeholder) so a finished run shows no part
        // stuck "running".
        const inProgress = part.state === "streaming" || part.state === "submitted";
        if (run.status === "completed" && inProgress) part.state = "completed";
        if (run.status === "failed" && inProgress) part.state = "failed";
      }
    }
  }
  return out.sort((left, right) => {
    if (left.id === activeThreadId) return -1;
    if (right.id === activeThreadId) return 1;
    return String(left.updatedAt || left.id).localeCompare(String(right.updatedAt || right.id));
  });
}

export function buildKanbanConversation(args: {
  events: EventRecord[];
  activeThreadId: string;
  knownThreads?: AgentSessionThreadRef[];
  taskId?: string;
  backend?: string;
  projectId?: string;
}): AgentConversation {
  const threads = new Map<string, AgentSessionThread>();
  const turnToMessage = new Map<string, string>();
  for (const ref of args.knownThreads ?? []) {
    ensureThread(threads, ref.id, ref.title);
  }
  ensureThread(threads, args.activeThreadId || "main", "main");
  const backendFilter = canonicalBackend(args.backend);
  const accepted = args.events.slice().sort((left, right) => (left.seq ?? 0) - (right.seq ?? 0));
  for (const event of accepted) {
    const payload = event.payload ?? {};
    const payloadBackend = canonicalBackend(payload.backend);
    const payloadProjectId = textValue(payload.project_id).trim();
    if (args.projectId && payloadProjectId && payloadProjectId !== args.projectId) continue;
    if (backendFilter && payloadBackend && payloadBackend !== backendFilter) continue;
    if (args.taskId && event.task_id && event.task_id !== args.taskId) continue;
    const threadId = textValue(payload.thread_key || payload.thread_id || args.activeThreadId || "main") || "main";
    if (event.type === "user.message") {
      if (payload.target !== "kanban-agent" || payload.runtime_delivery !== "headless") continue;
      const thread = ensureThread(threads, threadId);
      const turn = ensureTurn(thread, textValue(event.id || event.seq), event.ts);
      turn.user = {
        id: textValue(event.id || event.seq),
        role: "user",
        label: "You",
        content: textValue(payload.message),
        ts: event.ts,
        sourceEvent: event,
      };
      thread.updatedAt = event.ts;
      continue;
    }
    if (!event.type.startsWith("kanban.agent.turn.") && event.type !== "kanban.agent.reply" && event.type !== "agent.session.run.cancelled") {
      continue;
    }
    const turnId = textValue(payload.turn_id || payload.run_id || event.id || event.seq);
    const messageEventId = textValue(payload.message_event_id);
    if (messageEventId) turnToMessage.set(turnId, messageEventId);
    const thread = ensureThread(threads, threadId);
    const turn = ensureTurn(thread, turnToMessage.get(turnId) || `turn-${turnId}`, event.ts);
    const run = ensureRun(turn, turnId, {
      provider: payloadBackend || backendFilter,
      status: event.type.endsWith(".completed") ? "completed" : event.type.endsWith(".failed") ? "failed" : "streaming",
      startedAt: event.type.endsWith(".started") ? event.ts : undefined,
      updatedAt: event.ts,
      providerSessionId: textValue(payload.provider_session_id),
    });
    run.sourceEvents?.push(event);
    if (event.type === "kanban.agent.turn.started" || event.type === "kanban.agent.turn.created") {
      upsertPart(run, {
        id: "status-started",
        runId: run.id,
        kind: "status",
        state: "submitted",
        title: event.type.endsWith(".created") ? "Queued" : "Started",
        summary: payloadBackend || backendFilter,
        startedAt: event.ts,
        updatedAt: event.ts,
        sourceEvent: event,
      });
    } else if (event.type === "kanban.agent.turn.delta") {
      if (run.status === "cancelled") {
        upsertPart(run, {
          id: `stale-${event.seq ?? run.parts.length + 1}`,
          runId: run.id,
          kind: "status",
          state: "stale",
          title: "Stale delta ignored",
          summary: textValue(payload.message_type || payload.type || "delta"),
          updatedAt: event.ts,
          sourceEvent: event,
        });
      } else {
        applyDelta(run, event, payload);
        if (uiDeltaKind(payload) === "question") {
          addCard(turn, {
            id: `question-${run.id}-${event.seq ?? turn.cards.length + 1}`,
            kind: "question",
            title: "Agent needs input",
            body: uiDeltaContent(payload),
            status: "waiting_input",
            runId: run.id,
            threadId: thread.id,
            actionLabel: "Answer",
            refs: eventSourceRefs(event, recordValue(payload.refs)),
          });
        }
      }
    } else if (event.type === "kanban.agent.reply") {
      if (run.status !== "cancelled") {
        run.status = textValue(payload.status) === "failed" ? "failed" : "completed";
      }
      run.updatedAt = event.ts;
      run.providerSessionId = textValue(payload.provider_session_id) || run.providerSessionId;
      run.usage = recordValue(payload.usage) ?? undefined;
      const answer = textValue(payload.answer || payload.error).trim();
      if (answer) {
        upsertPart(run, {
          id: payload.error ? "text-error" : "text",
          runId: run.id,
          kind: payload.error ? "error" : "text",
          state: run.status,
          title: payload.error ? "Error" : "Response",
          content: answer,
          updatedAt: event.ts,
          sourceEventId: event.id,
          sourceEventSeq: event.seq,
          sourceEvent: event,
          refs: eventSourceRefs(event, recordValue(payload.refs)),
        });
      }
      const proposal = parseActionProposal(payload);
      if (proposal) {
        run.proposal = proposal;
        addCard(turn, {
          id: `proposal-${run.id}`,
          kind: "proposal",
          title: proposal.action === "create-task" ? "Create task proposal" : "Action proposal",
          body: proposal.reason,
          runId: run.id,
          threadId: thread.id,
          actionLabel: proposal.action === "create-task" ? "Create Task" : "Run action",
          proposal,
          refs: eventSourceRefs(event, recordValue(payload.refs)),
        });
      }
    } else if (event.type === "agent.session.run.cancelled") {
      run.status = "cancelled";
      run.updatedAt = event.ts;
      upsertPart(run, {
        id: "status-cancelled",
        runId: run.id,
        kind: "status",
        state: "cancelled",
        title: "Cancel requested",
        summary: textValue(payload.reason || "operator requested cancel"),
        updatedAt: event.ts,
        sourceEvent: event,
      });
    } else {
      if (run.status !== "cancelled") {
        run.status = event.type.endsWith(".failed") ? "failed" : "completed";
      }
      run.updatedAt = event.ts;
    }
  }
  return {
    id: `kanban:${args.projectId || "default"}`,
    projectId: args.projectId,
    surface: "kanban_agent",
    activeThreadId: args.activeThreadId || "main",
    threads: finalizeThreads(threads, args.activeThreadId || "main"),
  };
}

export { buildChannelConversation } from "./channelProjection";
