import { buildChannelConversation } from "../src/components/agent-session/channelProjection.js";
import { channelLiveStreamRows, foldChannelLiveStream } from "../src/components/agent-session/channelLiveStream.js";
import type { ChannelDetail } from "../src/api/types.js";
import type { EventRecord } from "../src/api/types.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function deltaRow(over: Partial<EventRecord> & { payload: Record<string, unknown> }): EventRecord {
  return { type: "agent.session.part.delta", id: `live-${Math.random().toString(16).slice(2, 8)}`, ts: "2026-07-16T12:00:05Z", ...over } as EventRecord;
}

// Working channel run: request pending (used to render a yellow queued dot).
const detail = {
  messages: [
    { message_id: "m-user", role: "user", text: "@pm-1 讲讲 R1", ts: "2026-07-16T12:00:00Z" },
  ],
  reply_requests: [
    { request_id: "req-1", message_id: "m-user", target_member_id: "pm-1", status: "pending", run_id: "run-live-1" },
  ],
} as unknown as ChannelDetail;

const rows = [
  deltaRow({ payload: { channel_id: "ch-live", run_id: "run-live-1", kind: "text", state: "delta", delta: "R1 的要点是", seq: 3 } }),
  deltaRow({ payload: { channel_id: "ch-live", run_id: "run-live-1", kind: "text", state: "delta", delta: "……先分词再计数。", seq: 4 } }),
  deltaRow({ payload: { channel_id: "ch-live", run_id: "run-live-1", kind: "thinking", state: "delta", delta: "先读 spec", seq: 2 } }),
  deltaRow({ payload: { channel_id: "ch-live", run_id: "run-live-1", kind: "tool_use", state: "completed", part_id: "tool_use-0001", content: "Read spec-notes.md", seq: 1 } }),
  // status placeholder rows never become parts
  deltaRow({ payload: { channel_id: "ch-live", run_id: "run-live-1", kind: "status", state: "started", content: "started", seq: 0 } }),
  // other channel is ignored
  deltaRow({ payload: { channel_id: "ch-other", run_id: "run-live-1", kind: "text", state: "delta", delta: "不相关", seq: 9 } }),
];

const scoped = channelLiveStreamRows(rows, "ch-live");
assert(scoped.length === 5, `channel scoping keeps 5 rows, got ${scoped.length}`);
assert(scoped[0]!.payload!.seq === 0, "rows sorted by payload seq");

const conv = foldChannelLiveStream(
  buildChannelConversation(detail, "ch-live", "main"),
  scoped,
  "ch-live",
);
const run = conv.threads.flatMap((t) => t.turns).flatMap((t) => t.runs).find((r) => r.id === "run-live-1")!;
assert(run, "run found");
assert(run.status === "streaming", `live deltas flip run to streaming (green), got ${run.status}`);
const liveText = run.parts.find((p) => p.id === "live-text-run-live-1");
assert(liveText?.content === "R1 的要点是……先分词再计数。", `text accumulates in seq order, got ${JSON.stringify(liveText?.content)}`);
assert(liveText?.state === "streaming", "live text renders as streaming");
assert(run.parts.some((p) => p.id === "tool_use-0001" && p.kind === "tool_call"), "committed tool part folds as live activity");
assert(!run.parts.some((p) => p.kind === "status" && p.summary === "started"), "status placeholder rows dropped");
const thread = conv.threads.find((t) => t.turns.some((turn) => turn.runs.includes(run)))!;
assert(thread.status === "streaming", "thread rollup follows the live run");

// request_id fallback: run keyed by request id before provider run id is known.
const earlyDetail = {
  messages: [{ message_id: "m-user2", role: "user", text: "@pm-1 hi", ts: "2026-07-16T12:01:00Z" }],
  reply_requests: [{ request_id: "req-early", message_id: "m-user2", target_member_id: "pm-1", status: "pending" }],
} as unknown as ChannelDetail;
const earlyConv = foldChannelLiveStream(
  buildChannelConversation(earlyDetail, "ch-live", "main"),
  [deltaRow({ payload: { channel_id: "ch-live", run_id: "", request_id: "req-early", kind: "text", state: "delta", delta: "早期", seq: 1 } })],
  "ch-live",
);
const earlyRun = earlyConv.threads.flatMap((t) => t.turns).flatMap((t) => t.runs).find((r) => r.id === "req-early")!;
assert(earlyRun?.status === "streaming", "request_id fallback reaches the run");
assert(earlyRun.parts.some((p) => p.id === "live-text-req-early" && p.content === "早期"), "fallback text folds");

// Terminal run: stale rows dropped — a finished bubble never re-grows.
const doneDetail = {
  messages: [
    { message_id: "m-user3", role: "user", text: "@pm-1 done?", ts: "2026-07-16T12:02:00Z" },
    { message_id: "m-reply3", role: "assistant", member_id: "pm-1", text: "最终回复。", ts: "2026-07-16T12:02:30Z", refs: { request_id: "req-done" } },
  ],
  reply_requests: [
    { request_id: "req-done", message_id: "m-user3", target_member_id: "pm-1", status: "completed", run_id: "run-done" },
  ],
} as unknown as ChannelDetail;
const doneConv = foldChannelLiveStream(
  buildChannelConversation(doneDetail, "ch-live", "main"),
  [deltaRow({ payload: { channel_id: "ch-live", run_id: "run-done", kind: "text", state: "delta", delta: "stale 尾巴", seq: 7 } })],
  "ch-live",
);
const doneRun = doneConv.threads.flatMap((t) => t.turns).flatMap((t) => t.runs).find((r) => r.id === "run-done")!;
assert(doneRun.status === "completed", "terminal run stays completed");
assert(!doneRun.parts.some((p) => p.id.startsWith("live-text")), "no live part re-grows a finished reply");

console.log("channelLiveStream.test.ts OK");

// --- P2c: buffer compaction — long replies must not lose opening tokens ---
import { compactChannelLiveRows } from "../src/components/agent-session/channelLiveStream.js";

const manyDeltas: EventRecord[] = Array.from({ length: 1000 }, (_, i) => deltaRow({
  id: `live-many-${i}`,
  payload: { channel_id: "ch-live", run_id: "run-long", kind: "text", state: "delta", delta: `t${i};`, seq: i + 1 },
}));
const toolRow = deltaRow({ id: "live-tool-x", payload: { channel_id: "ch-live", run_id: "run-long", kind: "tool_use", state: "completed", part_id: "tool_use-0002", content: "grep", seq: 1001 } });
const compacted = compactChannelLiveRows([...manyDeltas, toolRow], 800);
assert(compacted.length === 2, `1000 deltas + 1 tool compact to 2 rows, got ${compacted.length}`);
const mergedText = compacted.find((r) => (r.payload as Record<string, unknown>).kind === "text")!;
const mergedContent = String((mergedText.payload as Record<string, unknown>).delta);
assert(mergedContent.startsWith("t0;t1;"), "opening tokens preserved");
assert(mergedContent.endsWith("t998;t999;"), "tail tokens preserved");
assert(compactChannelLiveRows(manyDeltas.slice(0, 10), 800).length === 10, "under the cap: untouched");

console.log("channelLiveStream compaction tests OK");
