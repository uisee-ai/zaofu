import type { ChannelDetail } from "../../api/types";
import type { ChannelDeliveryEvidence } from "../../api/channelDelivery";
import { canonicalChannelPrd } from "./workflowPlanning.js";
import { channelThreadId } from "../agent-session/channelProjection.js";

type Row = Record<string, unknown>;
export type ChannelFlowNode = "requirement" | "discussion" | "prd" | "task" | "workflow";
export const FLOW_LABELS: Record<ChannelFlowNode, string> = {
  requirement: "Brief", discussion: "Discussion", prd: "PRD", task: "Tasks", workflow: "Workflow",
};
export const flowText = (value: unknown): string => typeof value === "string" ? value.trim() : "";
const digest = (value: unknown) => flowText(value).replace(/^sha256:/, "");
const inThread = (row: Row, threadId: string) => (flowText(row.thread_id) || "main") === threadId;

/** Resolve an old empty-main selection only when there is one unambiguous origin. */
export function channelSourceThreadId(detail: ChannelDetail | null, visibleThreadId: string): string {
  const ids = [...new Set([
    ...Object.keys(detail?.discussions ?? {}), ...Object.keys(detail?.consensus ?? {}),
    ...Object.keys(detail?.discussion_attention ?? {}),
    ...(detail?.messages ?? []).map((row) => flowText(row.thread_id) || "main"),
  ])];
  if (ids.includes(visibleThreadId)) return visibleThreadId;
  return visibleThreadId === "main" && ids.length === 1 ? ids[0]! : channelThreadId(visibleThreadId);
}

/** Read-only navigation: no inferred completion, and no cross-version handoff. */
export function channelFlow(detail: ChannelDetail | null, threadId: string, delivery?: ChannelDeliveryEvidence) {
  threadId = channelSourceThreadId(detail, threadId);
  const canonical = canonicalChannelPrd(detail, threadId);
  const syntheses = (detail?.syntheses ?? []).filter((row) => inThread(row, threadId));
  const latest = syntheses.at(-1);
  const artifactRef = flowText(latest?.artifact_ref) || canonical.artifactRef;
  const artifactDigest = flowText(latest?.artifact_digest) || canonical.artifactDigest;
  const revision = Number(latest?.prd_revision || canonical.revision || 0);
  const openQuestions = Object.values(detail?.open_questions ?? {}).filter((row) =>
    inThread(row, threadId) && row.status === "open");
  const questionCount = Math.max(openQuestions.length, Number(detail?.discussion_attention?.[threadId]?.owner_question_count) || 0);
  // A newly synthesized draft must not inherit the previous approved PRD's CTA.
  const ready = canonical.ready && artifactRef === canonical.artifactRef
    && digest(artifactDigest) === digest(canonical.artifactDigest)
    && revision === canonical.revision && questionCount === 0;
  const receipts = (detail?.result_receipts ?? []).filter((row) => inThread(row, threadId));
  const taskReceipts = receipts.filter((row) => row.receipt_kind === "task_created"
    && Boolean(artifactRef && artifactDigest)
    && flowText(row.artifact_ref) === artifactRef
    && digest(row.artifact_digest) === digest(artifactDigest)
    && Number(row.revision) === revision && Boolean(flowText(row.task_id)));
  const tasks = [...new Map(taskReceipts.map((row) => [flowText(row.task_id), row])).values()];
  const taskIds = new Set(tasks.map((row) => flowText(row.task_id)));
  // Terminal receipts carry a RESULT digest, not the PRD digest; join via the
  // proven Task lineage. Unbound historic requests remain in Diagnostics only.
  const results = receipts.filter((row) => taskIds.has(flowText(row.task_id))
    && ["workflow_terminal", "delivery_terminal"].includes(flowText(row.receipt_kind)));
  const requests = (detail?.workflow_requests ?? []).filter((row) => inThread(row, threadId)
    && taskIds.has(flowText(row.task_id)));
  const evidence = delivery?.revision === revision && digest(delivery.artifact_digest) === digest(artifactDigest) ? delivery : undefined;
  const runs = (evidence?.runs ?? []).filter((row) => taskIds.has(row.task_id));
  const workflows: Row[] = [...requests, ...results, ...runs];
  const requirements = (detail?.messages ?? detail?.recent_messages ?? [])
    .map((row) => row as Row)
    .filter((row) => inThread(row, threadId) && (row.role === "user" || row.member_id === "operator"));
  const discussion = detail?.discussions?.[threadId];
  const attention = detail?.discussion_attention?.[threadId];
  const discussionState = flowText(attention?.state) || flowText(discussion?.state);
  const hasDiscussion = Boolean(discussionState && discussionState !== "idle");
  const taskPlans = (evidence?.plans ?? []).filter((row) => row.subject_type === "task_create");
  const workflowPlans = (evidence?.plans ?? []).filter((row) => row.subject_type === "task_workflow" && taskIds.has(row.task_id));
  const planning = evidence?.planning;
  const planningTask = planning?.task_id ? "workflow" : "task";
  const activeDiscussion = ["running", "collecting", "synthesizing"].includes(discussionState);
  const consensus = detail?.consensus?.[threadId];
  const currentConsensus = consensus?.artifact_ref === artifactRef
    && digest(consensus?.artifact_digest) === digest(artifactDigest)
    && Number(consensus?.prd_revision || 0) === revision;
  const discussionStatus = questionCount || discussionState === "needs_input" ? "Awaiting input"
    : ["blocked", "failed", "cancelled"].includes(discussionState) ? "Needs attention"
    : activeDiscussion ? "Discussing" : ["ready", "completed", "awaiting_consensus"].includes(discussionState) ? "Converged"
    : hasDiscussion ? "Unknown" : "Not started";
  const synthesisRequest = (detail?.synthesis_requests ?? []).filter((row) => inThread(row, threadId)).at(-1);
  const generationError = ["blocked", "failed"].includes(flowText(synthesisRequest?.status))
    ? flowText(synthesisRequest?.reason) || flowText(synthesisRequest?.contract_error) || "PRD generation failed"
    : "";
  const generating = (attention?.kernel_phase || discussion?.state) === "phase3_synthesis"
    && attention?.execution_state === "running"
    && !["proposed", "blocked", "failed", "cancelled"].includes(flowText(synthesisRequest?.status));
  const prdStatus = generating ? "Generating" : !artifactRef ? "Not generated" : ready ? "Ready"
    : questionCount || (currentConsensus && consensus?.status === "blocked") ? "Needs revision"
    : currentConsensus ? "In review" : latest?.readiness_verdict === "needs_multi_lens" ? "Needs revision"
    : latest?.readiness_verdict === "ready" || currentConsensus ? "In review" : "Draft";
  const workflowStatus = workflowEvidenceStatus(workflows.length ? [...taskIds].map((taskId) =>
    runs.filter((row) => row.task_id === taskId).at(-1)
    || results.filter((row) => row.task_id === taskId).at(-1)
    || requests.filter((row) => row.task_id === taskId).at(-1)
    || { status: "unknown" }) : []);
  const planningStatus = planning?.status === "planning" || planning?.status === "requested" ? "Planning"
    : planning?.status === "failed" ? "Failed" : "Unknown";
  const reviewingPrd = Boolean(artifactRef && currentConsensus && !generating
    && (attention?.kernel_phase || discussion?.state) === "phase3_synthesis");
  const current: ChannelFlowNode = questionCount || (activeDiscussion && !reviewingPrd) ? "discussion"
    : workflows.length || workflowPlans.length || (planning?.status && planningTask === "workflow") ? "workflow"
    : tasks.length || taskPlans.length || planning?.status ? "task"
    : artifactRef ? "prd" : hasDiscussion ? "discussion" : "requirement";
  const statuses: Record<ChannelFlowNode, string> = {
    requirement: detail ? requirements.length ? "Captured" : "Empty" : "Loading",
    discussion: detail ? discussionStatus : "Loading",
    prd: detail ? prdStatus : "Loading",
    task: tasks.length ? "Created" : taskPlans.length ? taskPlans.some((row) => row.status === "needs_revision") ? "Needs revision" : "Awaiting approval"
      : planning?.status && planningTask === "task" ? planningStatus : ready ? "Ready to plan" : "Not created",
    workflow: workflows.length ? workflowStatus : workflowPlans.length ? workflowPlans.some((row) => row.status === "needs_revision") ? "Needs revision" : "Awaiting approval"
      : planning?.status && planningTask === "workflow" ? planningStatus : "Unknown",
  };
  const complete: Record<ChannelFlowNode, boolean> = {
    requirement: requirements.length > 0, discussion: discussionStatus === "Converged" || ready,
    prd: ready, task: tasks.length > 0, workflow: statuses.workflow === "Completed",
  };
  return { canonical, artifactRef, artifactDigest, revision, ready, latest, requirements,
    openQuestions, questionCount, tasks, workflows, current, statuses, complete, plans: [...taskPlans, ...workflowPlans],
    hasArtifact: Boolean(artifactRef), generationError, loading: !detail };
}
export type ChannelFlow = ReturnType<typeof channelFlow>;

/** A request is not a running Workflow; one completed run does not complete all. */
export function workflowEvidenceStatus(rows: Row[]): string {
  if (!rows.length) return "Unknown";
  const states = rows.map((row) => flowText(row.status));
  if (states.some((state) => ["failed", "rejected", "blocked", "abandoned"].includes(state))) return "Failed";
  if (states.includes("paused")) return "Paused";
  if (states.includes("running")) return "Running";
  if (states.some((state) => ["queued", "queued_no_runtime"].includes(state))) return "Queued";
  if (states.every((state) => state === "completed")) return "Completed";
  if (states.every((state) => state === "cancelled")) return "Cancelled";
  return "Unknown";
}

export function channelMemberLabel(member: Row): string {
  const id = flowText(member.member_id);
  const display = flowText(member.display_name);
  if (display && display !== id) return display;
  const role = flowText(member.channel_role) || flowText(member.role) || id;
  const labels: Record<string, string> = {
    product_pm: "产品负责人", product: "产品负责人", business_owner: "业务负责人",
    arch: "架构师", architect: "架构师", security: "安全专家", synthesizer: "综合分析师",
    qa: "质量专家", designer: "设计师", kanban: "Kanban Agent",
    critic: "评审专家", security_reviewer: "安全评审",
  };
  return labels[role] || role || display || "成员";
}
