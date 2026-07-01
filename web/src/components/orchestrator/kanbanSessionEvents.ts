import type { RecentEvent } from "../../api/types";

export const KANBAN_SESSION_EVENT_BUFFER_LIMIT = 800;

function textValue(value: unknown): string {
  if (value === null || value === undefined) return "";
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

  const payloadBackend = canonicalBackend(payload.backend || payload.provider);
  const wantedBackend = canonicalBackend(args.backend);
  if (wantedBackend && payloadBackend && payloadBackend !== wantedBackend) return false;

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
