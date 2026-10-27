import type { WebChatTurn } from "../src/api/types.js";
import { isKanbanRunWorking, kanbanHistoryBoundary } from "../src/components/orchestrator/kanbanSessionEvents.js";
import type { AgentSessionRun } from "../src/components/agent-session/types.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
const turn: WebChatTurn = {
  schema_version: "webchat-turn.v1", turn_id: "same-turn", project_id: "project",
  conversation_id: "kanban:project", thread_id: "thread", backend: "codex-headless",
  permission_profile: "workspace_writer", state: "running", current_attempt_id: "attempt-1",
  created_at: "start", updated_at: "first",
};
assert(kanbanHistoryBoundary([turn], "thread") === "[]", "streaming must not continually fetch history");
const paused = { ...turn, state: "waiting_input", pending_interaction_id: "question-1" };
const first = kanbanHistoryBoundary([paused], "thread");
assert(first !== "[]", "a persisted question must recover a missing SSE reply");
assert(kanbanHistoryBoundary([paused], "another-thread") === "[]", "another thread must not trigger recovery");
assert(kanbanHistoryBoundary([{ ...paused, current_attempt_id: "attempt-2", pending_interaction_id: "question-2" }], "thread") !== first,
  "a second question on the same turn must trigger another reconciliation");
assert(kanbanHistoryBoundary([{ ...paused, state: "completed", updated_at: "last" }], "thread") !== first,
  "completion after a question must reconcile the final reply");
const other = { ...paused, turn_id: "other-turn" };
assert(kanbanHistoryBoundary([paused, other], "thread") === kanbanHistoryBoundary([other, paused], "thread"),
  "runtime list ordering alone must not repeatedly fetch history");
console.log("kanbanHistoryBoundary tests passed");

const live: AgentSessionRun = { id: turn.turn_id, threadId: "thread", status: "streaming", parts: [], updatedAt: "2026-10-27T08:00:00Z" };
const settled = { ...paused, updated_at: "2026-10-27T08:01:00Z" };
assert(!isKanbanRunWorking(live, [settled]), "waiting for an answer must suppress stale streaming status");
assert(!isKanbanRunWorking(live, [{ ...settled, state: "completed" }]), "completed turns must not show Interrupt");
assert(isKanbanRunWorking({ ...live, updatedAt: "2026-10-27T08:02:00Z" }, [settled]),
  "a newer resumed attempt's live events must not be hidden by an older runtime poll");
assert(isKanbanRunWorking({ ...live, id: "new-turn" }, [settled]),
  "historical durable turns must not suppress a newly submitted turn");
