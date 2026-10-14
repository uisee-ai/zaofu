import type { ActionResponse, ChannelDetail, Snapshot } from "../api/types";
import {
  buildChannelWorkflowPlanningRequest,
  resolveChannelWorkflowBackend,
} from "../components/channel/workflowPlanning.js";
import {
  clearHistoryPayload,
  discussionModePayload,
  ownerReportPayload,
  startDiscussionPayload,
  synthesisRequestPayload,
} from "../components/channel/channelControlActions.js";
import {
  defaultKanbanThreadKey,
  kanbanAgentConversationId,
  kanbanAgentProjectId,
  kanbanThreadStorageKey,
} from "../components/orchestrator/kanbanAgentHistoryPolicy.js";

type SubmitAction = (
  action: string,
  payload: Record<string, unknown>,
) => Promise<ActionResponse>;

interface ChannelActionAdapterArgs {
  activeProjectId: string;
  channelDetail: ChannelDetail | null;
  prepareTaskAgent: (taskId?: string) => void;
  readStoredBackend: () => string;
  refreshChannelDetail: () => Promise<void>;
  selectedChannelId: string;
  snapshot: Snapshot | null;
  submitAction: SubmitAction;
}

function stableJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string" || typeof value === "boolean" || typeof value === "number") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .filter((key) => record[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(String(value));
}

function stableFingerprint(value: string): string {
  // FNV-1a 64 keeps an opaque, compact browser-side request identity. The
  // server remains the authority that atomically reserves/replays this key.
  let hash = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  const mask = 0xffffffffffffffffn;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= BigInt(value.charCodeAt(index));
    hash = (hash * prime) & mask;
  }
  return hash.toString(36);
}

export function channelWorkflowHandoffIdempotencyKey(args: {
  backend: string;
  channelId: string;
  objective: string;
  permissionProfile: string;
  projectId: string;
  taskId: string;
  threadId: string;
  threadKey: string;
  workflowContext: Record<string, unknown>;
}): string {
  return `channel-prd-handoff:v1:${stableFingerprint(stableJson(args))}`;
}

export function createChannelActionAdapter(args: ChannelActionAdapterArgs) {
  const channelId = () => args.selectedChannelId || "ch-zaofu";

  async function cancelProviderRun(runId: string) {
    const run = args.channelDetail?.provider_runs?.find((item) => item.run_id === runId);
    const threadId = String(run?.provider_thread_id || "");
    if (!threadId) throw new Error("Provider identity unavailable. Refresh and retry.");
    const result = await args.submitAction("agent-session-cancel", {
      run_id: runId, thread_id: threadId, conversation_id: channelId(),
      channel_id: channelId(), backend: run?.backend || run?.provider,
    });
    if (!result.ok) throw new Error(result.reason || "Provider cancellation failed");
    await args.refreshChannelDetail();
  }

  async function decideMemberInvitation(proposal: Record<string, unknown>, decision: "approved" | "rejected") {
    const result = await args.submitAction("channel-invite-member", {
      channel_id: channelId(), thread_id: proposal.thread_id,
      proposal_id: proposal.proposal_id, member_id: proposal.member_id, decision,
    });
    if (!result.ok) throw new Error(result.reason || "Member invitation failed");
    await args.refreshChannelDetail();
  }

  async function setDiscussionMode(
    threadId: string,
    mode: string,
    defaultResponderId?: string,
  ) {
    await args.submitAction(
      "channel-discussion-mode",
      discussionModePayload(
        channelId(),
        threadId,
        mode,
        defaultResponderId
          ?? String(args.channelDetail?.discussion?.default_responder_id ?? ""),
      ),
    );
  }

  async function requestSynthesis(
    threadId: string,
    targetMemberId?: string,
  ) {
    await args.submitAction(
      "channel.synthesis.request",
      synthesisRequestPayload(channelId(), threadId, targetMemberId),
    );
  }

  async function generateOwnerReport(threadId: string) {
    await args.submitAction(
      "channel.owner_report.request",
      ownerReportPayload(channelId(), threadId),
    );
  }

  async function evaluatePrd(threadId: string, revision: number, artifactRef: string, artifactDigest: string, role: "full" | "focused", retry: boolean) {
    const result = await args.submitAction("channel-prd-evaluate", {
      channel_id: channelId(), thread_id: threadId, prd_revision: revision,
      artifact_ref: artifactRef, artifact_digest: artifactDigest, role,
      retry,
      source: "web-channel-prd-preview",
    });
    if (!result.ok) throw new Error(result.reason || "PRD evaluation request failed");
  }

  async function clearHistory(threadId: string) {
    await args.submitAction(
      "channel-clear-history",
      clearHistoryPayload(channelId(), threadId),
    );
  }

  async function startDiscussion(
    threadId: string,
    message: string,
    messageId: string,
    mode: string,
    restart = false,
  ) {
    await args.submitAction(
      "channel-discussion-start",
      startDiscussionPayload(
        channelId(), threadId, message, messageId, mode, restart,
      ),
    );
  }

  async function resolveQuestion(
    questionId: string,
    threadId: string,
    resolution: string,
    answer: string,
  ) {
    const result = await args.submitAction("channel-question-resolve", {
      channel_id: channelId(),
      thread_id: threadId,
      question_id: questionId,
      resolution,
      answer,
      resolved_by: "owner:operator",
      source: "web-channel-question",
    });
    if (!result.ok) throw new Error(result.reason || "Decision was not accepted; please retry.");
    try {
      await args.refreshChannelDetail();
    } catch {
      // The accepted action remains authoritative; live projection recovery
      // can still converge after a transient refresh failure.
    }
  }

  async function decideConsensus(
    decision: "confirm" | "block",
    threadId: string,
    artifactRef: string,
    artifactDigest: string,
    blocker = "",
    acceptReadinessRisk = false,
  ) {
    const consensus = args.channelDetail?.consensus?.[threadId] ?? {};
    const prdRevision = Number(consensus.prd_revision ?? 0);
    await args.submitAction(
      decision === "confirm"
        ? "channel-consensus-confirm"
        : "channel-consensus-block",
      {
        channel_id: channelId(),
        thread_id: threadId,
        artifact_ref: artifactRef,
        artifact_digest: artifactDigest,
        prd_revision: prdRevision,
        accept_readiness_risk: decision === "confirm" && acceptReadinessRisk || undefined,
        member_id: "owner:operator",
        blocker_question: blocker || undefined,
        source: "web-channel-consensus",
      },
    );
  }

  async function submitWorkflowRequest(
    taskId: string,
    objective: string,
    threadId: string,
  ) {
    const planning = buildChannelWorkflowPlanningRequest({
      channelId: channelId(),
      detail: args.channelDetail,
      objective,
      taskId,
      threadId,
    });
    if (!planning) throw new Error("The current PRD is not ready for planning. Refresh and review it first.");
    const snapshotProjectId = args.snapshot?.project?.project_id || "";
    const projectId = kanbanAgentProjectId(
      args.activeProjectId,
      snapshotProjectId,
    );
    const defaultThread = defaultKanbanThreadKey(
      args.activeProjectId,
      snapshotProjectId,
    );
    const threadKey = typeof window === "undefined"
      ? defaultThread
      : window.localStorage.getItem(kanbanThreadStorageKey(projectId))
        || defaultThread;
    const agentSurface = args.snapshot?.runtime.agent_surface;
    const availableBackends = (agentSurface?.backends ?? [])
      .filter((item) => item.available !== false)
      .map((item) => item.id);
    const backend = resolveChannelWorkflowBackend({
      availableBackends,
      configuredBackends: [
        agentSurface?.configured_backend,
        agentSurface?.default_backend,
        agentSurface?.backend,
        ...availableBackends,
      ],
      storedBackend: args.readStoredBackend(),
    });
    const permissionProfile = agentSurface?.permission_profile || "dangerous_full";
    const idempotencyKey = channelWorkflowHandoffIdempotencyKey({
      backend,
      channelId: channelId(),
      objective,
      permissionProfile,
      projectId,
      taskId,
      threadId,
      threadKey,
      workflowContext: planning.workflowContext,
    });
    // The empty-Task primary path creates its Task inside the Agent plan, so
    // it still needs to reveal and focus the Agent before a Task id exists.
    args.prepareTaskAgent(taskId || undefined);
    const result = await args.submitAction("chat-orchestrator", {
      backend,
      permission_profile: permissionProfile,
      dangerous_ack: permissionProfile === "dangerous_full" || undefined,
      scope: "project",
      project_id: projectId,
      conversation_id: kanbanAgentConversationId(projectId),
      thread_key: threadKey,
      task_id: taskId,
      message: planning.message,
      workflow_context: planning.workflowContext,
      idempotency_key: idempotencyKey,
      source: "web-channel-workflow-plan",
    });
    if (!result.ok) throw new Error(String(result.reason || "Planning request failed. Please retry."));
    try {
      await args.refreshChannelDetail();
    } catch {
      // The accepted action is durable. SSE/live convergence will reconcile a
      // transient projection refresh failure without resubmitting the plan.
    }
  }

  async function requestProjectProfile(objective: string, threadId: string) {
    const projectId = kanbanAgentProjectId(args.activeProjectId, args.snapshot?.project?.project_id || "");
    const threadKey = defaultKanbanThreadKey(args.activeProjectId, args.snapshot?.project?.project_id || "");
    const backend = resolveChannelWorkflowBackend({ storedBackend: args.readStoredBackend() });
    args.prepareTaskAgent();
    const result = await args.submitAction("chat-orchestrator", {
      backend, permission_profile: "read_only", scope: "project", project_id: projectId,
      conversation_id: kanbanAgentConversationId(projectId), thread_key: threadKey,
      channel_id: channelId(), channel_thread_id: threadId,
      message: `请为当前项目生成或复用 Channel Profile。角色需求：${objective}\n`
        + `来源频道 ${channelId()}，讨论线程 ${threadId}。先检查项目目录；无合适配置时生成 channel-profile-register 提案，`
        + "包括项目角色 prompt 和有效 Skill 引用。提案需绑定原频道、原线程及主席版本，批准后注册并参评。"
        + "不要自行修改配置、声称已入群、创建 Task 或启动 Workflow。",
      source: "web-channel-project-profile",
    });
    if (!result.ok) throw new Error(result.reason || "项目角色生成请求失败");
  }

  async function adoptResearchResult(payload: Record<string, unknown>) {
    await args.submitAction("research-adopt", {
      ...payload,
      source: "web-channel-research-result",
    });
  }

  return {
    cancelProviderRun,
    adoptResearchResult,
    clearHistory,
    decideConsensus,
    decideMemberInvitation,
    generateOwnerReport,
    evaluatePrd,
    requestSynthesis,
    resolveQuestion,
    setDiscussionMode,
    startDiscussion,
    submitWorkflowRequest,
    requestProjectProfile,
  };
}
