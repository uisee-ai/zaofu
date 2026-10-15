import { channelFlow, channelMemberLabel, channelSourceThreadId, workflowEvidenceStatus } from "../src/components/channel/channelFlow.js";
import { channelDecisionReceipts, questionTargetRun } from "../src/components/channel/channelDecisions.js";
import type { ChannelDetail } from "../src/api/types.js";
import type { AgentConversation, AgentSessionRun, AgentSessionTurn } from "../src/components/agent-session/types.js";

function assert(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
const empty = { channel_id: "ch-test", name: "test", members: [], workflow_requests: [] } as ChannelDetail;
assert(channelFlow(empty, "main").current === "requirement", "empty channel is not a completed discussion");
const prd = { thread_id: "main", artifact_ref: "prd.md", artifact_digest: "sha256:current", prd_revision: 2,
  readiness_verdict: "ready", event_id: "synthesis", summary: "Accepted PRD", open_questions: [] };
const detail = { ...empty, syntheses: [prd], consensus: { main: { ...prd, reached_event_id: "consensus" } },
  messages: [{ thread_id: "main", role: "user", text: "Original requirement" }, { thread_id: "other", role: "user", text: "Unrelated" }],
  result_receipts: [
    { ...prd, receipt_kind: "task_created", revision: 2, task_id: "T2" },
    { ...prd, receipt_kind: "task_created", revision: 2, task_id: "T3" },
    { ...prd, receipt_kind: "task_created", revision: 1, task_id: "OLD" },
    { ...prd, receipt_kind: "task_created", revision: 2, task_id: "WRONG", artifact_digest: "other" },
    { ...prd, thread_id: "other", receipt_kind: "task_created", revision: 2, task_id: "OTHER" },
    { thread_id: "main", receipt_kind: "workflow_terminal", task_id: "T2", artifact_digest: "result-digest", status: "completed" },
    { thread_id: "main", receipt_kind: "workflow_terminal", task_id: "OLD", status: "completed" },
  ], workflow_requests: [{ thread_id: "main", task_id: "T3", status: "requested" }, { thread_id: "main", task_id: "OLD", status: "requested" }],
} as ChannelDetail;
const before = JSON.stringify(detail);
const flow = channelFlow(detail, "main");
assert(flow.statuses.requirement === "Captured", "English captured state");
assert(flow.statuses.prd === "Ready", "current confirmed version is ready");
assert(channelFlow(null, "main").statuses.requirement === "Loading", "loading is not empty");
assert(flow.ready, "canonical PRD is plannable");
assert(flow.requirements.length === 1, "requirements are scoped to active thread");
assert(flow.tasks.length === 2, "only receipts matching thread/ref/digest/revision prove created Tasks");
assert(flow.workflows.length === 2, "workflow joins via Task, terminal result digest differs from PRD");
assert(flow.current === "workflow", "a bound result permits workflow navigation");
assert(JSON.stringify(detail) === before, "navigation does not mutate source");
const newDraft = channelFlow({ ...detail, syntheses: [prd, { ...prd, artifact_digest: "next", prd_revision: 3 }] }, "main");
assert(!newDraft.ready && !newDraft.tasks.length && !newDraft.workflows.length, "new draft must not inherit old approval or Task/Workflow");
assert(!channelFlow({ ...detail, open_questions: [{ thread_id: "main", status: "open" }] }, "main").ready, "reopened questions block planning");
assert(!channelFlow({ ...detail, discussion_attention: { main: { owner_question_count: 1 } } } as unknown as ChannelDetail, "main").ready, "unloaded pending questions also block planning");
assert(!channelFlow(detail, "other").ready, "another thread cannot inherit consensus");
assert(channelMemberLabel({ member_id: "arch" }) === "架构师", "technical role gets human label");
assert(channelMemberLabel({ member_id: "arch", display_name: "方案负责人" }) === "方案负责人", "custom labels win");
const origin = { ...empty, messages: [{ thread_id: "kanban:project", role: "user", text: "From intake" }], discussions: { "kanban:project": { state: "needs_input" } } };
assert(channelSourceThreadId(origin, "main") === "kanban:project", "controls preserve the source thread identity");
assert(channelFlow(origin, "main").requirements.length === 1, "folded intake retains original requirement");
assert(channelFlow(origin, "main").current === "discussion", "folded intake retains actual discussion state");
for (const [state, label] of Object.entries({ running: "Running", queued: "Queued", paused: "Paused", completed: "Completed", cancelled: "Cancelled", failed: "Failed", requested: "Unknown", accepted: "Unknown" })) {
  assert(workflowEvidenceStatus([{ status: state }]) === label, `truthful workflow state: ${state}`);
}
assert(workflowEvidenceStatus([{ status: "completed" }, { status: "requested" }]) === "Unknown", "partial results cannot complete all workflows");
assert(channelFlow(empty, "main").statuses.workflow === "Unknown", "no execution evidence is not Not started");
const delivery = { revision: 2, artifact_digest: "current", runs: [{ task_id: "T2", workflow_run_id: "run2", status: "paused", event_id: "pause", reason: "" }], plans: [], planning: {} };
assert(channelFlow(detail, "main", delivery).statuses.workflow === "Paused", "live run state is used");
assert(channelFlow(detail, "main", { ...delivery, runs: [{ ...delivery.runs[0], status: "completed" }] }).statuses.workflow === "Unknown", "one live completed run cannot hide another Task's unverified execution");
assert(channelFlow(detail, "main", { ...delivery, revision: 1 }).statuses.workflow !== "Paused", "stale delivery response is discarded");
assert(channelFlow({ ...detail, result_receipts: [], workflow_requests: [] }, "main", { ...delivery, runs: [], plans: [{ request_id: "plan", subject_type: "task_create", status: "awaiting_approval", task_id: "" }] }).statuses.task === "Awaiting approval", "Plan approval is distinct from created Task");

const run: AgentSessionRun = { id: "r1", threadId: "main", memberId: "security", status: "completed", parts: [] };
const turn: AgentSessionTurn = { id: "t1", threadId: "main", runs: [run], cards: [], user: { id: "m1", role: "user", label: "Owner", content: "需求" } };
const conversation: AgentConversation = { id: "c", activeThreadId: "main", surface: "channel_group", threads: [{ id: "main", title: "main", status: "completed", turns: [turn] }] };
const source = { sourceRunId: "r1", sourceMessageId: "m1", askedBy: "security" };
assert(questionTargetRun(source, [{ run, turn }])?.run.id === "r1", "exact run provenance wins");
assert(!questionTargetRun({ ...source, sourceRunId: "unloaded" }, [{ run, turn }]), "do not attach unloaded source to unrelated reply");
assert(!questionTargetRun({ ...source, sourceRunId: "", sourceMessageId: "other-message" }, [{ run, turn }]), "message provenance restricts member fallback");
assert(!questionTargetRun({ ...source, sourceRunId: "", sourceMessageId: "" }, [{ run, turn }, { run: { ...run, id: "r2" }, turn }]), "ambiguous author history cannot prove original run");
const decisions = channelDecisionReceipts({ ...empty, open_questions: [
  { question_id: "q", status: "resolved", asked_by: "security", source_run_id: "r1", answer: "Accepted", resolution: "answered", resolved_by: "Owner" },
  { question_id: "other", thread_id: "other", status: "resolved", asked_by: "security" },
  { question_id: "open", status: "open", asked_by: "security" },
  { question_id: "unloaded", status: "resolved", source_run_id: "absent", asked_by: "security" },
] }, conversation, "main");
assert(decisions.byRun.get("r1")?.[0].answer === "Accepted", "decision receipt uses persisted answer");
assert(decisions.byRun.get("r1")?.length === 1 && decisions.unassigned.length === 1, "thread/provenance filtering keeps unanswered and unrelated questions out");
console.log("channelFlow tests passed");

const reviewing = { ...empty, syntheses: [{ ...prd, readiness_verdict: "needs_multi_lens" }],
  consensus: { main: { ...prd, status: "proposed" } },
  synthesis_requests: [{ thread_id: "main", status: "proposed" }],
  discussion_attention: { main: { state: "running", kernel_phase: "phase3_synthesis", execution_state: "running" } },
} as unknown as ChannelDetail;
assert(channelFlow(reviewing, "main").statuses.prd === "In review", "active reviewers do not make the already published PRD still Generating");
assert(channelFlow({ ...reviewing, synthesis_requests: [...reviewing.synthesis_requests!, { thread_id: "main", status: "requested" }] }, "main").statuses.prd === "Generating", "a newer synthesis still displays generation while the old PRD is retained");

assert(channelFlow(reviewing, "main").current === "prd", "same-version review belongs to the published PRD stage");
assert(channelFlow({ ...reviewing, synthesis_requests: [...reviewing.synthesis_requests!, { thread_id: "main", status: "requested" }] }, "main").current === "discussion", "new generation must not inherit the previous PRD stage");

{
  const detail = { synthesis_requests: [{ thread_id: "main", request_id: "blocked", status: "blocked",
    reason: "semantic_source_budget_exceeded:342979>120000" }] } as unknown as ChannelDetail;
  assert(channelFlow(detail, "main").generationError.includes("342979>120000"), "blocked synthesis retains its rejection reason");
  assert(channelFlow(detail, "other").generationError === "", "rejection stays in its source thread");
  detail.synthesis_requests!.push({ thread_id: "main", request_id: "retry", status: "requested" });
  assert(channelFlow(detail, "main").generationError === "", "explicit retry retires the prior error");
}
