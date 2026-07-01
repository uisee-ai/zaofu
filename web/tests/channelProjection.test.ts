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
