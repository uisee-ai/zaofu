import { buildChannelConversation } from "../src/components/agent-session/channelProjection.js";
import type { ChannelDetail } from "../src/api/types.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

const detail = {
  messages: [{
    message_id: "msg-1",
    role: "user",
    text: "review *.md",
    ts: "2026-06-26T12:00:00.000Z",
  }],
  provider_runs: [{
    run_id: "run-1",
    message_id: "msg-1",
    provider: "claude-headless",
    target_member_id: "reviewer",
    status: "completed",
    updated_at: "2026-06-26T12:00:02.000Z",
    parts: [{
      id: "part-result",
      kind: "result",
      state: "completed",
      title: "Result",
      content: "**Result**\n\nfinal channel reply",
    }],
  }],
} as unknown as ChannelDetail;

const conversation = buildChannelConversation(detail, "ch-test", "main");
const part = conversation.threads[0]?.turns[0]?.runs[0]?.parts[0];

assert(part?.kind === "text", `channel result should render as text, got ${part?.kind}`);
assert(part?.title === "Response", `channel result title should be hidden reply title, got ${part?.title}`);
assert(part?.content === "final channel reply", "channel result content should be preserved");

const messageDetail = {
  messages: [{
    message_id: "msg-user",
    role: "user",
    text: "review *.md",
    ts: "2026-06-26T12:00:00.000Z",
  }, {
    message_id: "msg-assistant",
    role: "assistant",
    member_id: "pm",
    source: "claude-headless",
    text: "**Result**\n\n无新增实质 finding。",
    ts: "2026-06-26T12:00:02.000Z",
  }],
} as unknown as ChannelDetail;
const messageConversation = buildChannelConversation(messageDetail, "ch-test", "main");
const messagePart = messageConversation.threads
  .flatMap((thread) => thread.turns)
  .flatMap((turn) => turn.runs)
  .flatMap((run) => run.parts)
  .find((item) => item.content?.includes("无新增实质"));

assert(messagePart?.kind === "text", `assistant channel message should render as text, got ${messagePart?.kind}`);
assert(messagePart?.content === "无新增实质 finding。", `assistant channel message should strip Result heading, got ${messagePart?.content}`);

console.log("channelProjection.test.ts OK");


// --- operator review 2026-07-16: anti-storm notice placement + gating ---
// A plain agent reply (no @mention) blocked by auto_route_not_allowed is the
// NORMAL end of a turn — no notice at all.
const plainReplyDetail = {
  messages: [
    { message_id: "m-user", role: "user", text: "@pm 介绍下需求", ts: "2026-07-16T10:00:00Z" },
    { message_id: "m-reply", role: "assistant", member_id: "pm", source: "claude-headless", text: "需求有四条,如下。", ts: "2026-07-16T10:00:10Z", refs: { request_id: "req-1" } },
  ],
  reply_requests: [
    { request_id: "req-1", message_id: "m-user", target_member_id: "pm", status: "completed", run_id: "run-9" },
  ],
  routes: [
    { message_id: "m-reply", thread_id: "main", routing_reason: "blocked", reason: "auto_route_not_allowed", ts: "2026-07-16T10:00:11Z" },
  ],
} as unknown as ChannelDetail;
const plainConv = buildChannelConversation(plainReplyDetail, "ch-guard", "main");
const plainParts = plainConv.threads.flatMap((t) => t.turns).flatMap((t) => t.runs).flatMap((r) => r.parts);
assert(!plainParts.some((p) => p.title === "未自动扇出(防风暴)"), "plain agent reply gets NO anti-storm notice");

// A blocked agent reply that DID @mention another member keeps the notice,
// attached to the reply's own run — not a trailing orphan turn.
const mentionReplyDetail = {
  messages: [
    { message_id: "m-user2", role: "user", text: "@pm 和 arch 对齐一下", ts: "2026-07-16T10:01:00Z" },
    { message_id: "m-reply2", role: "assistant", member_id: "pm", source: "claude-headless", text: "@arch-1 你看下接口约定?", ts: "2026-07-16T10:01:10Z", refs: { request_id: "req-2" } },
  ],
  reply_requests: [
    { request_id: "req-2", message_id: "m-user2", target_member_id: "pm", status: "completed", run_id: "run-10" },
  ],
  routes: [
    { message_id: "m-reply2", thread_id: "main", routing_reason: "blocked", reason: "auto_route_not_allowed", ts: "2026-07-16T10:01:11Z" },
  ],
} as unknown as ChannelDetail;
const mentionConv = buildChannelConversation(mentionReplyDetail, "ch-guard", "main");
const mentionThread = mentionConv.threads.find((t) => t.id === "main")!;
const replyRun = mentionThread.turns.flatMap((t) => t.runs).find((r) => r.parts.some((p) => p.id === "message-m-reply2"));
assert(replyRun, "reply run exists");
assert(replyRun!.parts.some((p) => p.title === "未自动扇出(防风暴)"), "guarded @mention notice attaches to the reply run");
assert(
  !mentionThread.turns.some((t) => t.runs.some((r) => r.id === "route-blocked-m-reply2")),
  "no orphan route-blocked turn trails the timeline",
);

// Human message nobody answers keeps the red Not routed feedback (chat-e2e F5).
const humanBlockedDetail = {
  messages: [
    { message_id: "m-human", role: "user", text: "有人在吗", ts: "2026-07-16T10:02:00Z" },
  ],
  routes: [
    { message_id: "m-human", thread_id: "main", routing_reason: "blocked", reason: "no_target", ts: "2026-07-16T10:02:01Z" },
  ],
} as unknown as ChannelDetail;
const humanConv = buildChannelConversation(humanBlockedDetail, "ch-guard", "main");
const humanParts = humanConv.threads.flatMap((t) => t.turns).flatMap((t) => t.runs).flatMap((r) => r.parts);
assert(humanParts.some((p) => p.title === "Not routed" && p.kind === "error"), "human no_target keeps the Not routed error");

console.log("channelProjection blocked-route notice tests OK");


// --- operator review 2026-07-16: a pending reply request is working state ---
// (green dot + immediate Thinking indicator), not a yellow queued dot that
// sits for the provider's cold start.
const pendingReqDetail = {
  messages: [
    { message_id: "m-pend", role: "user", text: "@pm-1 在吗", ts: "2026-07-16T14:30:00Z" },
  ],
  reply_requests: [
    { request_id: "req-pend", message_id: "m-pend", target_member_id: "pm-1", status: "pending" },
  ],
} as unknown as ChannelDetail;
const pendConv = buildChannelConversation(pendingReqDetail, "ch-pend", "main");
const pendRun = pendConv.threads.flatMap((t) => t.turns).flatMap((t) => t.runs).find((r) => r.id === "req-pend")!;
assert(pendRun, "pending request run exists");
assert(pendRun.status === "submitted", `pending request renders as working (submitted), got ${pendRun.status}`);

console.log("channelProjection pending-request tests OK");


// provider_runs pass must not overwrite the working state back to queued
// (probe 2026-07-16: DOM sat yellow 9s while the row said pending pre-start).
const pendingProviderDetail = {
  messages: [
    { message_id: "m-pp", role: "user", text: "@pm-1 ping", ts: "2026-07-16T15:00:00Z" },
  ],
  reply_requests: [
    { request_id: "req-pp", message_id: "m-pp", target_member_id: "pm-1", status: "pending", run_id: "run-pp" },
  ],
  provider_runs: [
    { run_id: "run-pp", request_id: "req-pp", message_id: "m-pp", target_member_id: "pm-1", status: "pending" },
  ],
} as unknown as ChannelDetail;
const ppConv = buildChannelConversation(pendingProviderDetail, "ch-pp", "main");
const ppRun = ppConv.threads.flatMap((t) => t.turns).flatMap((t) => t.runs).find((r) => r.id === "run-pp")!;
assert(ppRun.status === "submitted", `provider pending row stays working, got ${ppRun.status}`);

console.log("channelProjection provider-pending tests OK");
