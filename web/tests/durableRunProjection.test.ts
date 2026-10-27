import type { EventRecord, WebChatTurn } from "../src/api/types.js";
import { buildKanbanConversation } from "../src/components/agent-session/projection.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
const binding = { project_id: "project", conversation_id: "kanban:project", thread_key: "thread",
  turn_id: "turn", backend: "codex-headless" };
function event(seq: number, type: string, payload: Record<string, unknown>): EventRecord {
  return { id: `evt-${seq}`, seq, ts: `2026-10-27T08:00:0${seq}Z`, type, payload: { ...binding, ...payload } };
}
const events = [
  event(1, "user.message", { target: "kanban-agent", runtime_delivery: "headless", message: "Prepare workflow" }),
  event(2, "kanban.agent.turn.created", { message_event_id: "evt-1" }),
  event(3, "kanban.agent.turn.delta", { control_state: "plan_request_buffering" }),
];
const durable: WebChatTurn = {
  schema_version: "webchat-turn.v1", ...binding, thread_id: "thread", permission_profile: "workspace_writer",
  state: "failed", created_at: "2026-10-27T08:00:00Z", updated_at: "2026-10-27T08:01:00Z",
};
function project(turn = durable, history = events) {
  return buildKanbanConversation({ events: history, activeThreadId: "thread", projectId: "project",
    conversationId: "kanban:project", backend: binding.backend, durableTurns: [turn] }).threads[0];
}
const failed = project();
assert(failed.turns.flatMap(turn => turn.runs).some(run => run.status === "failed"), "missing terminal event must settle failed run");
assert(!failed.turns.some(turn => turn.cards.some(card => card.payload?.preparing)), "terminal state removes stale Preparing");
assert(!failed.turns.some(turn => turn.runs.some(run => run.parts.some(part => part.state === "streaming"))), "parts must stop streaming");
assert(project({ ...durable, thread_id: "elsewhere" }).turns.some(turn => turn.runs.some(run => run.status === "streaming")),
  "different thread must not settle this run");
assert(project({ ...durable, updated_at: "2026-10-27T08:00:01Z" }).turns.some(turn => turn.runs.some(run => run.status === "streaming")),
  "newer resumed stream must survive stale runtime poll");
const question = { request_id: "question", request_event_id: "evt-4", revision: 1, question_id: "question",
  header: "Confirm", question: "Where?", options: [{ id: "existing", label: "Existing repo", submit_mode: "continue" },
    { id: "new", label: "New repo", submit_mode: "continue" }],
  allow_other: true, valid: true, turn_id: "turn", backend: binding.backend };
const withQuestion = [...events, event(4, "kanban.agent.plan.requested", { plan_request: question })];
const paused = project({ ...durable, state: "waiting_input" }, withQuestion);
assert(paused.turns.some(turn => turn.cards.some(card => card.planRequest && card.status === "waiting_input")),
  "paused question must remain answerable");
const cancelled = project({ ...durable, state: "cancelled" }, withQuestion);
assert(cancelled.turns.some(turn => turn.cards.some(card => card.planRequest && card.status === "cancelled")),
  "cancelled question must become a noninteractive receipt");
console.log("durable run projection tests passed");
