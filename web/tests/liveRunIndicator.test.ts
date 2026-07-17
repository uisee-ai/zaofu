import {
  elapsedSecondsSince,
  formatElapsed,
  runStartTimestamp,
  toolCallCount,
} from "../src/components/agent-session/liveRunIndicator.js";
import { buildKanbanConversation } from "../src/components/agent-session/projection.js";
import type { AgentSessionPart } from "../src/components/agent-session/types.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function part(over: Partial<AgentSessionPart>): AgentSessionPart {
  return { id: "p", runId: "r", kind: "status", state: "streaming", title: "p", ...over } as AgentSessionPart;
}

// --- formatElapsed: seconds under a minute, "1m 23s" from there on ---
assert(formatElapsed(0) === "0s", "0 → 0s");
assert(formatElapsed(0.4) === "0s", "sub-second floors to 0s");
assert(formatElapsed(12.9) === "12s", `12.9 → 12s, got ${formatElapsed(12.9)}`);
assert(formatElapsed(59) === "59s", "59 stays in seconds");
assert(formatElapsed(60) === "1m 0s", `60 → 1m 0s, got ${formatElapsed(60)}`);
assert(formatElapsed(83) === "1m 23s", `83 → 1m 23s, got ${formatElapsed(83)}`);
assert(formatElapsed(-5) === "0s", "negative clamps to 0s");

// --- elapsedSecondsSince: parse guard + clamp ---
const nowMs = Date.parse("2026-07-16T00:01:00Z");
assert(elapsedSecondsSince("2026-07-16T00:00:48Z", nowMs) === 12, "12s elapsed");
assert(elapsedSecondsSince(undefined, nowMs) === undefined, "missing → undefined");
assert(elapsedSecondsSince("not-a-date", nowMs) === undefined, "unparseable → undefined");
assert(elapsedSecondsSince("2026-07-16T00:02:00Z", nowMs) === 0, "future start clamps to 0");

// --- runStartTimestamp: run.startedAt wins, else earliest part timestamp ---
assert(
  runStartTimestamp({ startedAt: "2026-07-16T00:00:00Z", parts: [part({ startedAt: "2026-07-15T00:00:00Z" })] })
    === "2026-07-16T00:00:00Z",
  "run.startedAt wins over parts",
);
assert(
  runStartTimestamp({
    parts: [
      part({ id: "a", updatedAt: "2026-07-16T00:00:05Z" }),
      part({ id: "b", startedAt: "2026-07-16T00:00:02Z" }),
    ],
  }) === "2026-07-16T00:00:02Z",
  "earliest part timestamp used when run has none",
);
assert(runStartTimestamp({ parts: [] }) === undefined, "no timestamps → undefined");

// --- toolCallCount: counts invocations, not results or non-tool parts ---
const groundingParts = [
  part({ id: "status-started", kind: "status" }),
  part({ id: "thinking", kind: "thinking" }),
  part({ id: "status-10", kind: "tool_call" }),
  part({ id: "tool-result-11", kind: "tool" }),
  part({ id: "status-12", kind: "tool_call" }),
  part({ id: "explicit-result", kind: "tool_result" }),
  part({ id: "tool-14", kind: "tool" }),
];
assert(toolCallCount(groundingParts) === 3, `2 calls + 1 generic tool = 3, got ${toolCallCount(groundingParts)}`);
assert(toolCallCount([]) === 0, "empty → 0");

// --- kanban projection keeps run.startedAt across later deltas (timer basis;
// ensureRun used to Object.assign an undefined startedAt over the recorded one) ---
const conversation = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-1",
      ts: "2026-07-16T00:00:00Z",
      type: "kanban.agent.turn.started",
      payload: { turn_id: "turn-1", thread_key: "main", backend: "codex" },
    },
    {
      seq: 2,
      id: "evt-2",
      ts: "2026-07-16T00:00:07Z",
      type: "kanban.agent.turn.delta",
      payload: { turn_id: "turn-1", thread_key: "main", message_type: "thinking", content: "planning" },
    },
  ],
});
const liveRun = conversation.threads[0]!.turns[0]!.runs[0]!;
assert(liveRun.startedAt === "2026-07-16T00:00:00Z", `run.startedAt survives deltas, got ${liveRun.startedAt}`);
assert(runStartTimestamp(liveRun) === "2026-07-16T00:00:00Z", "timer basis is the turn.started ts");

// --- terminal guard: an SSE-replayed stale delta must not flip a completed
// run back to streaming (it resurrected the live tool UI on a finished run) ---
const replayed = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-1",
      ts: "2026-07-16T00:00:00Z",
      type: "kanban.agent.turn.started",
      payload: { turn_id: "turn-1", thread_key: "main" },
    },
    {
      seq: 2,
      id: "evt-2",
      ts: "2026-07-16T00:00:20Z",
      type: "kanban.agent.reply",
      payload: { turn_id: "turn-1", thread_key: "main", answer: "done" },
    },
    {
      seq: 3,
      id: "evt-3",
      ts: "2026-07-16T00:00:20Z",
      type: "kanban.agent.turn.completed",
      payload: { turn_id: "turn-1", thread_key: "main" },
    },
    {
      // Ephemeral live-bus replay rows (no event seq) fold AFTER the
      // committed rows — the run is already terminal, so they must be
      // dropped: no status revive, no tool part, no text re-append.
      id: "live-stale-1",
      ts: "2026-07-16T00:00:05Z",
      type: "kanban.agent.turn.delta",
      payload: { turn_id: "turn-1", thread_key: "main", seq: 90, message_type: "tool_use", tool: "bash", input: { command: "ls" } },
    },
    {
      id: "live-stale-2",
      ts: "2026-07-16T00:00:06Z",
      type: "kanban.agent.turn.delta",
      payload: { turn_id: "turn-1", thread_key: "main", seq: 91, message_type: "text", content: "stray fragment" },
    },
  ],
});
const replayedRun = replayed.threads[0]!.turns[0]!.runs[0]!;
assert(replayedRun.status === "completed", `stale delta must not revive run, got ${replayedRun.status}`);
assert(!replayedRun.parts.some((p) => p.kind === "tool_call" || p.kind === "tool"), "stale tool delta dropped on a finished run");
const finalText = replayedRun.parts.find((p) => p.kind === "text");
assert(finalText?.content === "done", `stale text delta must not garble the final reply, got ${JSON.stringify(finalText?.content)}`);

// --- delta ordering: seq-less live deltas fold AFTER committed events, so
// the run joins the user.message turn (the question used to render BELOW the
// answer because the first delta created the run's turn first) ---
const ordered = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      // Live delta arrives FIRST in array order and carries no event seq.
      id: "live-delta-1",
      ts: "2026-07-16T00:00:02Z",
      type: "kanban.agent.turn.delta",
      payload: { turn_id: "turn-9", thread_key: "main", seq: 1, message_type: "thinking", content: "planning" },
    },
    {
      seq: 10,
      id: "evt-msg",
      ts: "2026-07-16T00:00:00Z",
      type: "user.message",
      payload: { target: "kanban-agent", runtime_delivery: "headless", thread_key: "main", message: "什么是 R4?" },
    },
    {
      seq: 11,
      id: "evt-created",
      ts: "2026-07-16T00:00:01Z",
      type: "kanban.agent.turn.created",
      payload: { turn_id: "turn-9", thread_key: "main", message_event_id: "evt-msg" },
    },
  ],
});
const orderedThread = ordered.threads.find((t) => t.id === "main")!;
assert(orderedThread.turns.length === 1, `question and run share ONE turn, got ${orderedThread.turns.length}`);
assert(orderedThread.turns[0]!.user?.content === "什么是 R4?", "turn carries the user question");
assert(orderedThread.turns[0]!.runs.length === 1, "run folded into the question turn");
assert(orderedThread.turns[0]!.runs[0]!.parts.some((p) => p.kind === "thinking"), "delta content reached the run");

// --- slim-indexed rows: turn.created without payload.message_event_id still
// anchors to the question turn via causation_id (= the user.message id) ---
const slimFold = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-q",
      ts: "2026-07-16T00:00:00Z",
      type: "user.message",
      payload: { target: "kanban-agent", runtime_delivery: "headless", thread_key: "main", message: "R3 怎么验证?" },
    },
    {
      seq: 2,
      id: "evt-created",
      ts: "2026-07-16T00:00:01Z",
      type: "kanban.agent.turn.created",
      causation_id: "evt-q",
      payload: { turn_id: "turn-slim", thread_key: "main" },
    },
    {
      seq: 3,
      id: "evt-reply",
      ts: "2026-07-16T00:00:09Z",
      type: "kanban.agent.reply",
      payload: { turn_id: "turn-slim", thread_key: "main", answer: "写退出码断言" },
    },
  ],
});
const slimThread = slimFold.threads.find((t) => t.id === "main")!;
assert(slimThread.turns.length === 1, `slim rows: question and answer share ONE turn, got ${slimThread.turns.length}`);
assert(slimThread.turns[0]!.user?.content === "R3 怎么验证?", "slim rows: turn carries the question");
assert(slimThread.turns[0]!.runs[0]!.parts.some((p) => p.kind === "text" && p.content === "写退出码断言"), "slim rows: answer folded under the question");

console.log("liveRunIndicator.test.ts OK");
