import { buildChannelConversation } from "../src/components/agent-session/channelProjection.js";
import { compactChannelLiveRows, foldChannelLiveStream } from "../src/components/agent-session/channelLiveStream.js";
import type { ChannelDetail, EventRecord } from "../src/api/types.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

for (const terminal of ["failed", "completed", "cancelled", "stale"]) {
  const detail = {
    members: [{ member_id: "arch" }],
    reply_requests: [{ request_id: "reply-1", run_id: "run-1", message_id: "message-1", target_member_id: "arch", status: terminal }],
    provider_runs: [{ request_id: "reply-1", run_id: "run-1", message_id: "message-1", target_member_id: "arch", status: terminal,
      live_status: "streaming", parts: [
        { part_id: "thinking", kind: "thinking", state: "streaming", content: "Old work" },
        { part_id: "tool-done", kind: "tool_result", state: "completed", content: "Historical evidence" },
      ] }],
  } as unknown as ChannelDetail;
  const conversation = buildChannelConversation(detail, "ch-terminal", "main");
  const run = conversation.threads.flatMap(t => t.turns).flatMap(t => t.runs)[0];
  assert(run.status === terminal, `${terminal} must dominate stale live_status, got ${run.status}`);
  assert(!run.parts.some(p => p.state === "streaming" || p.state === "submitted"), "terminal parts must stop Thinking/tool spinners too");
  assert(run.parts.find(p => p.id === "tool-done")?.state === "completed", "historical tool result must retain its status");
  assert(!conversation.threads[0].activeRunId, "terminal run must not expose an active/cancellable run");
  foldChannelLiveStream(conversation, [{ type: "agent.session.part.delta", payload: {
    channel_id: "ch-terminal", run_id: "run-1", kind: "thinking", state: "delta", delta: "Late thinking",
  } }] as EventRecord[], "ch-terminal");
  assert(run.status === terminal, "late SSE cannot revive terminal work");
  assert(!run.parts.some(p => p.content?.includes("Late thinking")), "late output must stay out of terminal history");
}

const active = buildChannelConversation({ provider_runs: [{
  run_id: "run-new", status: "pending", live_status: "streaming", target_member_id: "arch",
}] } as unknown as ChannelDetail, "ch-terminal");
assert(active.threads[0].turns[0].runs[0].status === "streaming", "active provider still supplies progress for a nonterminal request");

for (const field of ["reply_requests", "provider_runs"]) {
  const retry = buildChannelConversation({ [field]: [{
    request_id: "retry", run_id: "run-retry", run_generation: 2, status: "running", target_member_id: "arch",
  }] } as unknown as ChannelDetail, "ch-terminal");
  const rows = [
    { run_generation: 1, delta: "old " },
    { run_generation: 2, delta: "new " },
    { run_generation: 1, delta: "late old " },
    { delta: "legacy old " },
    { run_generation: 2, delta: "answer" },
  ].map((payload, seq) => ({ type: "agent.session.part.delta", payload: {
    channel_id: "ch-terminal", run_id: "run-retry", kind: "text", state: "delta", seq, ...payload,
  } })) as EventRecord[];
  foldChannelLiveStream(retry, compactChannelLiveRows(rows, 2), "ch-terminal");
  const run = retry.threads[0].turns[0].runs[0];
  assert(run.status === "streaming", "current retry must remain active");
  assert(run.parts.some(p => p.content === "new answer"), `${field}: current retry must keep its complete output`);
  assert(!run.parts.some(p => p.content?.includes("old")), `${field}: old generations cannot contaminate retry output`);
}
console.log("channelTerminalProjection tests passed");
