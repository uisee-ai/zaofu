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
    if (!result.ok) return;
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
  ) {
    const consensus = args.channelDetail?.consensus?.[threadId] ?? {};
    const prdRevision = Number(consensus.prd_revision ?? 0);
    const readinessVerdict = String(
      consensus.readiness_verdict ?? "unassessed",
    );
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
        accept_readiness_risk: (
          readinessVerdict === "needs_owner"
          || readinessVerdict === "needs_multi_lens"
        ) || undefined,
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
    if (!planning) return;
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
    if (!result.ok) return;
    try {
      await args.refreshChannelDetail();
    } catch {
      // The accepted action is durable. SSE/live convergence will reconcile a
      // transient projection refresh failure without resubmitting the plan.
    }
  }

  async function adoptResearchResult(payload: Record<string, unknown>) {
    await args.submitAction("research-adopt", {
      ...payload,
      source: "web-channel-research-result",
    });
  }

  return {
    adoptResearchResult,
    clearHistory,
    decideConsensus,
    generateOwnerReport,
    requestSynthesis,
    resolveQuestion,
    setDiscussionMode,
    startDiscussion,
    submitWorkflowRequest,
  };
}
