import {
  defaultKanbanThreadKey,
  kanbanAgentConversationId,
  kanbanAgentHistoryParams,
  kanbanAgentProjectId,
  kanbanThreadStorageKey,
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

// channel-kanban E2E 2026-07-09: a fresh browser must land on the STABLE
// project-derived thread, not a per-browser random key, or it cannot see the
// existing kanban conversation (20-round history invisible from a 2nd browser).
function testDefaultThreadIsStableProjectConversation(): void {
  assert(
    defaultKanbanThreadKey("project-a") === "kanban:project-a",
    "default thread must equal the project conversation id, not a random key",
  );
  assert(
    defaultKanbanThreadKey("", "project-from-snapshot") === "kanban:project-from-snapshot",
    "default thread must fall back to the snapshot project id",
  );
  // Two independent sessions on the same project converge on the same thread.
  assert(
    defaultKanbanThreadKey("project-a") === defaultKanbanThreadKey("project-a"),
    "same project => same default thread across sessions",
  );
}

function testThreadStorageKeyIsProjectScoped(): void {
  assert(
    kanbanThreadStorageKey("project-a") === "zf.kanbanAgentThreadKey:project-a",
    "storage key must be scoped per project so switching projects drops stale threads",
  );
  assert(
    kanbanThreadStorageKey("project-a") !== kanbanThreadStorageKey("project-b"),
    "different projects must not share a stored thread key",
  );
}

testProjectIdPrefersActiveProject();
testConversationIdIsProjectScoped();
testHistoryParamsDoNotFilterByTask();
testDefaultThreadIsStableProjectConversation();
testThreadStorageKeyIsProjectScoped();
