import type { RecentEvent } from "../src/api/types.js";
import {
  kanbanAgentSessionEventsFromLive,
  mergeBoundedKanbanSessionEvents,
  mergeEventsByIdentity,
} from "../src/components/orchestrator/kanbanSessionEvents.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function event(seq: number, type: string, payload: Record<string, unknown>, taskId = ""): RecentEvent {
  return {
    id: `evt-${seq}`,
    seq,
    ts: `2026-06-29T12:00:${String(seq).padStart(2, "0")}.000Z`,
    type,
    task_id: taskId || null,
    payload,
  };
}

const scope = {
  projectId: "proj-a",
  conversationId: "kanban:proj-a",
  backend: "codex-headless",
  taskId: "",
};

const liveTurn = [
  event(1, "user.message", {
    source: "kanban",
    target: "kanban-agent",
    runtime_delivery: "headless",
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    message: "review docs",
  }),
  event(2, "kanban.agent.turn.created", {
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    turn_id: "turn-a",
    message_event_id: "evt-1",
  }),
  event(3, "kanban.agent.turn.delta", {
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    turn_id: "turn-a",
    message_type: "text",
    content: "working",
  }),
  event(4, "kanban.agent.reply", {
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    turn_id: "turn-a",
    answer: "done",
  }),
];

const unrelatedChannelRun = event(5, "agent.session.part.delta", {
  backend: "codex-headless",
  project_id: "proj-a",
  conversation_id: "channel:ch-a",
  thread_id: "thread-a",
  source: "channel-agent.headless",
  content: "channel-only",
});

const initialScoped = kanbanAgentSessionEventsFromLive([...liveTurn, unrelatedChannelRun], scope);
assert(initialScoped.length === 4, `should keep only kanban conversation events, got ${initialScoped.length}`);

const buffered = mergeBoundedKanbanSessionEvents([], initialScoped);
const afterPageSwitchLiveEvents: RecentEvent[] = [];
const conversationEvents = mergeEventsByIdentity(buffered, afterPageSwitchLiveEvents);
const retainedPrompt = conversationEvents.find((item) => item.type === "user.message");
const retainedReply = conversationEvents.find((item) => item.type === "kanban.agent.reply");

assert(retainedPrompt?.payload?.message === "review docs", "buffer should preserve user prompt after live events reset");
assert(retainedReply?.payload?.answer === "done", "buffer should preserve reply after live events reset");

const bounded = mergeBoundedKanbanSessionEvents([], [
  event(10, "kanban.agent.reply", { backend: "codex-headless", answer: "a" }),
  event(11, "kanban.agent.reply", { backend: "codex-headless", answer: "b" }),
  event(12, "kanban.agent.reply", { backend: "codex-headless", answer: "c" }),
  event(13, "kanban.agent.reply", { backend: "codex-headless", answer: "d" }),
], 3);
assert(bounded.map((item) => item.seq).join(",") === "11,12,13", "bounded buffer should keep newest events");

console.log("kanbanSessionEvents.test.ts OK");
