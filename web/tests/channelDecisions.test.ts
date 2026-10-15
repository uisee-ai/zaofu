import { channelDecisionReceipts } from "../src/components/channel/channelDecisions.js";
import type { ChannelDetail } from "../src/api/types.js";
import type { AgentConversation } from "../src/components/agent-session/types.js";

function assert(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
const conversation: AgentConversation = { id: "c", activeThreadId: "main", surface: "channel_group", threads: [] };
function receipt(id: string, overrides: Record<string, unknown> = {}) {
  return { question_id: id, question: `Question ${id}`, status: "resolved", thread_id: "kanban:project",
    kind: "owner_decision", resolution: "answered", resolved_by: "owner:operator", asked_by: id,
    answer: "本次隔离验收采用的测试决定：采用固定测试口径。", risk_note: "", resolved_event_id: `evt-${id}`, ...overrides };
}
const rows = [
  ...Array.from({ length: 4 }, (_, i) => receipt(`fact-${i}`, { kind: "fact", resolution: "evidence", answer: `原文核证 ${i}` })),
  ...Array.from({ length: 5 }, (_, i) => receipt(`test-${i}`, { answer: `本次隔离验收的测试决定：测试口径 ${i}` })),
  receipt("duplicate-test", { answer: "本次隔离验收的测试决定：测试口径 0" }),
  ...Array.from({ length: 5 }, (_, i) => receipt(`stage-${i}`, { answer: "本轮阶段澄清（依据当前要求）：当前仅验收文档。" })),
  receipt("open", { status: "open" }), receipt("merged", { status: "merged" }), receipt("other-thread", { thread_id: "other" }),
];
function history(openQuestions: Record<string, unknown>[] | Record<string, Record<string, unknown>>) {
  return channelDecisionReceipts({ channel_id: "c", open_questions: openQuestions } as ChannelDetail, conversation, "kanban:project");
}
const before = JSON.stringify(rows);
const result = history(rows);
assert(result.recordCount === 15 && result.groups.length === 10, "15 receipts become 10 display groups, excluding open/merged/other thread");
assert(result.groups.filter((g) => g.category === "evidence").length === 4, "4 evidence groups");
assert(result.groups.filter((g) => g.category === "test").length === 5, "5 explicitly labelled test assumptions");
const stage = result.groups.find((g) => g.category === "clarification")!;
assert(stage.receipts.length === 5 && new Set(stage.receipts.map((r) => r.original.resolved_event_id)).size === 5,
  "all five member questions and event identities survive grouping");
assert(JSON.stringify(rows) === before, "read projection never changes persisted question status, answer, or sources");
assert(JSON.stringify(history(Object.fromEntries(rows.map((r) => [r.question_id, r]))).groups) === JSON.stringify(result.groups),
  "array and keyed projections produce the same history");

const separated = history([
  receipt("base"), receipt("other-owner", { resolved_by: "other-owner" }), receipt("other-kind", { kind: "fact" }),
  receipt("risk", { risk_note: "Additional risk" }), receipt("wording", { answer: "本次隔离验收采用的测试决定：采用其他测试口径。" }),
  receipt("other-origin", { thread_id: "kanban:other-project" }),
  receipt("resolution", { resolution: "assumption" }),
]);
assert(separated.groups.length === 6 && separated.recordCount === 6, "different owner, kind, risk, wording, and resolution never merge; other raw thread is excluded");
assert(history([receipt("yes-a", { answer: "同意" }), receipt("yes-b", { answer: "同意" })]).groups.length === 2,
  "identical generic short answers cannot consolidate unrelated questions");
assert(history([receipt("a", { answer: "" }), receipt("b", { answer: "" })]).groups.length === 2, "missing answers stay distinct");
assert(history([receipt("a", { resolved_by: "" }), receipt("b", { resolved_by: "" })]).groups.length === 2, "missing answer authors stay distinct");
assert(history([receipt("ordinary", { answer: "正式答复中提到测试决定：不应分类为测试假设。" })]).groups[0].category === "answer",
  "mentioning a test label inside ordinary prose is not an explicit scope label");
assert(history([receipt("ordinary", { answer: "本轮阶段澄清（当前范围）：正文", resolution: "out_of_scope" })]).groups[0].category === "out_of_scope",
  "structured resolution takes precedence over a prose label");
console.log("channelDecisions tests passed");

assert(history([receipt("deferred", { resolution: "deferred", answer: "本轮暂缓" })]).groups[0].category === "deferred",
  "explicit deferral is neither approval nor scope exclusion");
