export interface KanbanAgentHistoryParams {
  surface: "kanban_agent";
  threadId: string;
  conversationId: string;
  backend: string;
  limit: number;
}

export function kanbanAgentProjectId(activeProjectId: string, snapshotProjectId = ""): string {
  return activeProjectId || snapshotProjectId || "default";
}

export function kanbanAgentConversationId(projectId: string): string {
  return `kanban:${projectId || "default"}`;
}

// channel-kanban E2E 2026-07-09: the kanban agent conversation is per-project
// and its history is server-durable, keyed by thread_id. The default thread must
// therefore be the STABLE project-derived id, not a per-browser random key —
// otherwise a fresh browser/session lands on an empty thread and cannot see the
// existing conversation (a 20-round history was invisible from a second browser).
// The default thread id IS the project conversation id, so every session
// converges on it.
export function defaultKanbanThreadKey(activeProjectId: string, snapshotProjectId = ""): string {
  return kanbanAgentConversationId(kanbanAgentProjectId(activeProjectId, snapshotProjectId));
}

// localStorage is scoped per project so switching projects never carries a stale
// thread from a different project.
export function kanbanThreadStorageKey(activeProjectId: string, snapshotProjectId = ""): string {
  return `zf.kanbanAgentThreadKey:${kanbanAgentProjectId(activeProjectId, snapshotProjectId)}`;
}

export function kanbanAgentHistoryParams({
  backend,
  conversationId,
  limit = 160,
  threadId,
}: {
  backend: string;
  conversationId: string;
  limit?: number;
  threadId: string;
}): KanbanAgentHistoryParams {
  return {
    surface: "kanban_agent",
    threadId,
    conversationId,
    backend,
    limit,
  };
}
