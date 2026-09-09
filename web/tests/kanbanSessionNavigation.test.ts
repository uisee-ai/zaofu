import type { RecentEvent } from "../src/api/types.js";
import { buildKanbanConversation } from "../src/components/agent-session/projection.js";
import {
  initializeKanbanThreadReads,
  kanbanThreadActivity,
  kanbanThreadReadStorageKey,
  kanbanThreadRefsStorageKey,
  kanbanThreadUnreadCounts,
  loadKanbanThreadRefs,
  markKanbanThreadRead,
  normalizeKanbanThreadRefs,
  storedActiveKanbanThread,
} from "../src/components/orchestrator/kanbanSessionNavigation.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

class MemoryStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

function event(seq: number, type: string, threadId: string, turnId = `turn-${seq}`): RecentEvent {
  return {
    seq,
    id: `event-${seq}`,
    type,
    payload: {
      conversation_id: "kanban:project-a",
      project_id: "project-a",
      thread_key: threadId,
      turn_id: turnId,
    },
  };
}

function testThreadRefsAreProjectScopedAndLegacyMigrationIsConservative(): void {
  const storage = new MemoryStorage();
  storage.setItem("zf.kanbanAgentThreads", JSON.stringify([
    { id: "kanban:project-b", title: "main" },
    { id: "kanban:project-a", title: "main" },
    { id: "project-a-active", title: "main", createdAt: "2026-09-03T00:00:00Z" },
    { id: "unknown-thread", title: "chat 2" },
  ]));

  const projectA = loadKanbanThreadRefs(storage, "project-a", "project-a-active");
  assert(projectA.length === 2, "migration should retain only the current project's default and active thread");
  assert(projectA[0]?.id === "kanban:project-a", "project default must stay first");
  assert(projectA[0]?.title === "main", "the project default is the only main thread");
  assert(projectA[1]?.id === "project-a-active", "the project-scoped active legacy thread should survive");
  assert(projectA[1]?.title === "chat 2", "a duplicate main label should be renamed deterministically");

  const projectB = loadKanbanThreadRefs(storage, "project-b", "kanban:project-b");
  assert(projectB.length === 1, "a second project must not inherit project A's random thread refs");
  assert(projectB[0]?.id === "kanban:project-b", "project B should keep only its own default thread");
  assert(
    kanbanThreadRefsStorageKey("project-a") !== kanbanThreadRefsStorageKey("project-b"),
    "thread-ref storage must be namespaced by project",
  );
  assert(
    kanbanThreadReadStorageKey("project-a") !== kanbanThreadReadStorageKey("project-b"),
    "read cursors must be namespaced by project",
  );
}

function testThreadOrderDoesNotMoveWithActiveSelection(): void {
  const refs = [
    { id: "kanban:project-a", title: "main" },
    { id: "thread-2", title: "chat 2" },
    { id: "thread-3", title: "chat 3" },
  ];
  const normalized = normalizeKanbanThreadRefs(refs, "project-a", "thread-3");
  assert(
    normalized.map((item) => item.id).join(",") === "kanban:project-a,thread-2,thread-3",
    "normalization must preserve stable navigation order",
  );

  const conversation = buildKanbanConversation({
    activeThreadId: "thread-3",
    conversationId: "kanban:project-a",
    events: [],
    knownThreads: normalized,
    projectId: "project-a",
  });
  assert(
    conversation.threads.map((item) => item.id).join(",") === "kanban:project-a,thread-2,thread-3",
    "the active thread must not be moved to the first slot by projection",
  );
}

function testUnreadCountsUseCanonicalActivitySequences(): void {
  const firstActivity = kanbanThreadActivity([
    event(10, "kanban.agent.reply", "thread-a"),
    event(11, "kanban.agent.message.delta", "thread-a"),
    event(12, "kanban.agent.reply", "thread-b", "failed-turn"),
    event(13, "kanban.agent.turn.failed", "thread-b", "failed-turn"),
  ], "kanban:project-a");
  const initialized = initializeKanbanThreadReads({
    schemaVersion: 1,
    initialized: false,
    readThrough: {},
  }, firstActivity);
  assert(
    firstActivity["thread-b"]?.length === 1,
    "a failed reply and its terminal failure event should count as one logical activity",
  );
  assert(
    Object.values(kanbanThreadUnreadCounts(initialized, firstActivity)).every((count) => count === 0),
    "installing read cursors must not mark existing history unread",
  );

  const nextActivity = kanbanThreadActivity([
    ...[
      event(10, "kanban.agent.reply", "thread-a"),
      event(12, "kanban.agent.reply", "thread-b", "failed-turn"),
      event(13, "kanban.agent.turn.failed", "thread-b", "failed-turn"),
    ],
    event(14, "kanban.agent.reply", "thread-b"),
    event(15, "workflow.result.available", "thread-b"),
  ], "kanban:project-a");
  const unread = kanbanThreadUnreadCounts(initialized, nextActivity);
  assert(unread["thread-a"] === 0, "a quiet thread should remain read");
  assert(unread["thread-b"] === 2, "canonical reply and workflow result should count as two unread activities");

  const read = markKanbanThreadRead(initialized, nextActivity, "thread-b");
  assert(
    kanbanThreadUnreadCounts(read, nextActivity)["thread-b"] === 0,
    "selecting a thread should advance its read cursor",
  );
}

function testActiveThreadStorageDefaultsPerProject(): void {
  const storage = new MemoryStorage();
  assert(
    storedActiveKanbanThread(storage, "project-a") === "kanban:project-a",
    "an empty project should use its stable conversation thread",
  );
  assert(
    storedActiveKanbanThread(storage, "project-b") === "kanban:project-b",
    "a second project should receive a different stable thread",
  );
}

testThreadRefsAreProjectScopedAndLegacyMigrationIsConservative();
testThreadOrderDoesNotMoveWithActiveSelection();
testUnreadCountsUseCanonicalActivitySequences();
testActiveThreadStorageDefaultsPerProject();
