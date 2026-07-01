import {
  kanbanAgentConversationId,
  kanbanAgentHistoryParams,
  kanbanAgentProjectId,
} from "../src/components/orchestrator/kanbanAgentHistoryPolicy.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function testProjectIdPrefersActiveProject(): void {
  assert(
    kanbanAgentProjectId("project-from-url", "") === "project-from-url",
    "active project id should be enough when snapshot is absent",
  );
  assert(
    kanbanAgentProjectId("", "project-from-snapshot") === "project-from-snapshot",
    "snapshot project id should remain the fallback",
  );
  assert(kanbanAgentProjectId("", "") === "default", "missing project id should fall back to default");
}

function testConversationIdIsProjectScoped(): void {
  assert(
    kanbanAgentConversationId("project-a") === "kanban:project-a",
    "conversation id should be scoped to the project id",
  );
}

function testHistoryParamsDoNotFilterByTask(): void {
  const params = kanbanAgentHistoryParams({
    threadId: "main",
    conversationId: "kanban:project-a",
    backend: "codex-headless",
    limit: 160,
  }) as unknown as Record<string, unknown>;

  assert(params.surface === "kanban_agent", "surface should target kanban agent history");
  assert(params.threadId === "main", "thread id should be preserved");
  assert(params.conversationId === "kanban:project-a", "conversation id should be preserved");
  assert(params.backend === "codex-headless", "backend should be preserved");
  assert(!Object.prototype.hasOwnProperty.call(params, "taskId"), "history restore must not be task-filtered");
}

testProjectIdPrefersActiveProject();
testConversationIdIsProjectScoped();
testHistoryParamsDoNotFilterByTask();
