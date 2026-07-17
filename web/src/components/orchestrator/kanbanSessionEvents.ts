import type { RecentEvent } from "../../api/types";

export const KANBAN_SESSION_EVENT_BUFFER_LIMIT = 800;

function textValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

export function mergeEventsByIdentity(...groups: RecentEvent[][]): RecentEvent[] {
  const byKey = new Map<string, RecentEvent>();
  for (const group of groups) {
    for (const event of group) {
      const key = event.seq !== undefined && event.seq !== null
        ? `seq:${event.seq}`
        : event.id
          ? `id:${event.id}`
          : `${event.type}:${event.ts || ""}:${JSON.stringify(event.payload || {})}`;
      byKey.set(key, event);
    }
  }
  return [...byKey.values()].sort((left, right) => {
    const leftSeq = left.seq ?? 0;
    const rightSeq = right.seq ?? 0;
    if (leftSeq || rightSeq) return leftSeq - rightSeq;
    return String(left.ts || "").localeCompare(String(right.ts || ""));
  });
}

export function isKanbanAgentSessionEvent(
  event: RecentEvent,
  args: {
    projectId?: string;
    conversationId?: string;
    backend?: string;
    taskId?: string;
  } = {},
): boolean {
  const payload = event.payload ?? {};
  const eventType = event.type || "";
  if (eventType === "user.message") {
    if (payload.target !== "kanban-agent") return false;
    if (payload.runtime_delivery !== "headless") return false;
  } else if (!(
    eventType.startsWith("kanban.agent.turn.")
    || eventType.startsWith("kanban.agent.message.")
    || eventType === "kanban.agent.reply"
    || eventType.startsWith("agent.session.")
  )) {
    return false;
  }

  const payloadProjectId = textValue(payload.project_id).trim();
  if (args.projectId && payloadProjectId && payloadProjectId !== args.projectId) return false;

  const payloadConversationId = textValue(payload.conversation_id).trim();
  if (args.conversationId && payloadConversationId && payloadConversationId !== args.conversationId) return false;

  // Backend is ADVISORY, not an exclusion (consistent with b7eebff4's
  // backend-agnostic history fold): a kanban thread is one durable
  // conversation that can span backends (codex + claude-code kanban agent).
  // Excluding a live delta because its payload.backend canonicalizes
  // differently from the current selector re-introduces the same live-stream
  // drop the SSE seq fix removes — a codex reply must still fold while the
  // selector reads claude. `args.backend` is retained for callers that scope
  // history fetches, but never gates the live fold.

  const eventTaskId = textValue(event.task_id).trim();
  if (args.taskId && eventTaskId && eventTaskId !== args.taskId) return false;

  return true;
}

export function kanbanAgentSessionEventsFromLive(
  events: RecentEvent[],
  args: Parameters<typeof isKanbanAgentSessionEvent>[1] = {},
): RecentEvent[] {
  if (!events.length) return [];
  return events.filter((event) => isKanbanAgentSessionEvent(event, args));
}

export function mergeBoundedKanbanSessionEvents(
  current: RecentEvent[],
  incoming: RecentEvent[],
  limit = KANBAN_SESSION_EVENT_BUFFER_LIMIT,
): RecentEvent[] {
  if (!incoming.length) return current;
  const merged = mergeEventsByIdentity(current, incoming);
  return merged.length > limit ? merged.slice(-limit) : merged;
}
