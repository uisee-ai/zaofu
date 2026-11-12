import { readableActivity, readableProgress } from "../src/components/agent-session/readableActivity.js";
import type { AgentSessionPart, AgentSessionRun } from "../src/components/agent-session/types";
import { buildKanbanConversation } from "../src/components/agent-session/projection.js";
function assert(value: unknown, message: string) { if (!value) throw new Error(message); }
const part = (id: string, content: string, refs = {}): AgentSessionPart => ({ id, runId: "r", kind: "status", title: "Status", state: "streaming", content, refs });
const base: AgentSessionRun = { id: "r", threadId: "main", status: "streaming", parts: [], sourceEvents: [] };
const old = [..."正在核对恢复规则。"].map((c, i) => part(`s${i}`, c, { raw: { method: "item/agentMessage/delta", params: { itemId: "one" } } }));
assert(readableActivity({ ...base, parts: old }).title === "正在核对恢复规则。", "legacy commentary fragments form a readable line");
const snapshots = [part("one", "正在核对资料", { raw: { public_commentary: { id: "one", snapshot: true } } }),
  part("one-new", "正在核对资料与回答", { raw: { public_commentary: { id: "one", snapshot: true } } })];
assert(readableActivity({ ...base, parts: snapshots }).notes.join("") === "正在核对资料与回答", "snapshots replace, never concatenate");
const activity = { kind: "synthesis", id: "p", title: "保存需求映射", index: 1, total: 5, completed: 1, state: "started" };
const stages = [part("p1", "保存需求映射", { raw: { activity } }), part("p2", "保存需求映射", { raw: { activity: { ...activity, state: "completed", completed: 2 } } })];
const model = readableActivity({ ...base, status: "failed", parts: stages });
assert(model.steps.length === 1 && model.completed === 2 && model.total === 5, "phase receipts update one stable stage");
assert(!model.working && model.failed && model.title.includes("未完成"), "a saved portion never becomes overall success");
assert(readableActivity(base).completed === undefined, "ordinary replies do not invent checkpoint progress");
assert(!readableProgress("交") && !readableProgress("running") && !readableProgress('{"channel_synthesis":'), "placeholders and machine contracts remain out of public progress");
assert(!readableProgress("codex-headless") && !readableProgress("claude-headless"), "transport names are not progress explanations after history replay");
const unconfirmed = { ...part("tool", "", {}), kind: "tool" as const, state: "completed" as const, toolName: "read" };
assert(readableActivity({ ...base, status: "completed", parts: [unconfirmed] }).tools[0].state === "unknown", "run completion is not a missing tool completion receipt");
const events = ["正在", "正在核对输出和重连记录。"].map((content, i) => ({ id: `public-${i}`, ts: `2026-11-12T00:00:0${i}Z`, type: "kanban.agent.turn.delta", payload: {
  turn_id: "turn", thread_key: "main", message_type: "status", part_id: "commentary-one", content,
  refs: { raw: { public_commentary: { id: "note", snapshot: true } } },
} }));
const conversation = buildKanbanConversation({ events, activeThreadId: "main" });
const kanbanRun = conversation.threads[0].turns[0].runs[0];
assert(readableActivity(kanbanRun).notes.join("") === "正在核对输出和重连记录。", "actual Kanban projection replaces full snapshots without dropping the prefix");
const replay = buildKanbanConversation({ activeThreadId: "main", events: [
  { id: "start", ts: "2026-11-12T00:00:00Z", type: "kanban.agent.turn.started", payload: { turn_id: "turn", thread_key: "main" } },
  { id: "note", ts: "2026-11-12T00:00:01Z", type: "agent.session.part.delta", payload: {
    source: "kanban-agent.headless", run_id: "turn", part_id: "commentary-one", kind: "status", state: "started", delta: "正在核对输出和重连记录。",
    refs: { raw: { public_commentary: { id: "note", snapshot: true } } },
  } },
  { id: "done", ts: "2026-11-12T00:00:02Z", type: "kanban.agent.reply", payload: { turn_id: "turn", thread_key: "main", answer: "核对完成。", status: "completed" } },
] });
const replayRun = replay.threads[0].turns[0].runs[0];
assert(replayRun.status === "completed" && readableActivity(replayRun).notes[0] === "正在核对输出和重连记录。", "durable process survives refresh without resurrecting a completed turn");
const stopped = readableActivity({ ...base, status: "cancelled", parts: [stages[0]] });
assert(!stopped.working && stopped.steps[0].state === "cancelled", "stopping also ends the current phase spinner");
console.log("readableActivity: phase receipts, commentary and ordinary runs passed");
