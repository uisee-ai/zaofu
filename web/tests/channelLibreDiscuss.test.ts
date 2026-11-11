import { openOwnerQuestionnaire } from "../src/components/channel/channelQuestionnaire.js";
import { buildChannelConversation } from "../src/components/agent-session/channelProjection.js";
import type { ChannelDetail } from "../src/api/types.js";

function assert(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
const pending = { question_id: "q-new", question: "刷新后保留吗？", thread_id: "main", status: "open", presentation_pending: true };
const ready = { question_id: "q-old", question: "需要登录吗？", thread_id: "main", status: "open", presentation_pending: false };
const detail = { members: [], workflow_requests: [], open_questions: [pending, ready],
  owner_questionnaires: { main: [pending, ready] },
} as unknown as ChannelDetail;
assert(openOwnerQuestionnaire(detail, "main").map((q) => q.question_id).join() === "q-old", "An unpublished new batch must not expose cards or hide older answerable cards");
pending.presentation_pending = false;
assert(openOwnerQuestionnaire(detail, "main").length === 2, "Publication must make the exact candidate answerable");
ready.status = "resolved";
assert(openOwnerQuestionnaire(detail, "main").map((q) => q.question_id).join() === "q-new", "A stale questionnaire cannot resurrect an answered card");
const conversation = buildChannelConversation({ members: [], workflow_requests: [],
  messages: [{ message_id: "repair", thread_id: "main", role: "system", member_id: "主持人", text: "internal diagnostic JSON",
    refs: { display_origin: "host_coordination", display_text: "正在修复本次格式，仍采用原材料。" } }],
} as unknown as ChannelDetail, "ch-libre", "main");
const message = conversation.threads[0]?.turns[0]?.user;
assert(message?.role === "system" && message.label === "主持人", "Automatic coordination must retain a system origin and host identity");
assert(message?.content === "正在修复本次格式，仍采用原材料。", "Chat must show the readable status rather than the protocol diagnostic");
console.log("channelLibreDiscuss: publication and coordination display passed");
