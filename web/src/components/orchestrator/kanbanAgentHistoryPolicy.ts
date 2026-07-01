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
