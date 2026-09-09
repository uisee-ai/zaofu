import {
  clearHistoryPayload,
  discussionModePayload,
  ownerReportPayload,
  startDiscussionPayload,
  synthesisRequestPayload,
} from "../src/components/channel/channelControlActions.js";
import { openOwnerQuestionnaire } from "../src/components/channel/channelQuestionnaire.js";
import {
  channelWorkflowHandoffIdempotencyKey,
  createChannelActionAdapter,
} from "../src/app/channelActionAdapter.js";
import type { ActionResponse, ChannelDetail, Snapshot } from "../src/api/types.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

const payloads = [
  discussionModePayload("ch-thread", "feature/api", "multi_lens", "critic"),
  synthesisRequestPayload("ch-thread", "feature/api", "critic"),
  ownerReportPayload("ch-thread", "feature/api"),
  clearHistoryPayload("ch-thread", "feature/api"),
  startDiscussionPayload(
    "ch-thread",
    "feature/api",
    "Design the API",
    "msg-api",
    "multi_lens",
  ),
];
const discussionPayload = payloads[4];
assert(
  !("max_rounds" in payloads[0]),
  "discussion mode action must not inject an implicit round budget",
);
assert(
  discussionPayload.requirement_message_id === "msg-api",
  "discussion start must retain the source requirement identity",
);
assert(
  discussionPayload.message_id === undefined,
  "discussion start must use a fresh trigger message identity",
);
assert(
  discussionPayload.restart === false,
  "discussion start must not restart unless the operator asks",
);
assert(
  payloads.every((payload) => payload.thread_id === "feature/api"),
  "every channel control action must preserve the active thread",
);
assert(
  !payloads.some((payload) => payload.thread_id === "main"),
  "channel actions must not silently fall back to main",
);

const detail = {
  channel_id: "ch-thread",
  members: [],
  workflow_requests: [],
  open_questions: {
    open: {
      question_id: "q-open",
      thread_id: "feature/api",
      status: "open",
    },
    resolved: {
      question_id: "q-resolved",
      thread_id: "feature/api",
      status: "resolved",
    },
    other: {
      question_id: "q-other",
      thread_id: "main",
      status: "open",
    },
  },
  owner_questionnaires: {
    "feature/api": [
      { question_id: "q-open", question: "Open question" },
      { question_id: "q-resolved", question: "Resolved question" },
      { question_id: "q-missing", question: "Stale projection" },
    ],
  },
} as ChannelDetail;
const questionnaire = openOwnerQuestionnaire(detail, "feature/api");
assert(questionnaire.length === 1, "only one owner question remains open");
assert(questionnaire[0].question_id === "q-open", "the open question should remain");

const consolidatingDetail = {
  ...detail,
  discussion_attention: {
    "feature/api": {
      schema_version: "channel.discussion-attention.v2",
      is_derived_projection: true,
      thread_id: "feature/api",
      state: "running",
      reason: "question_consolidation_active",
      next_action: "view_activity",
      execution_state: "running",
      attention_kind: "consolidating",
      kernel_phase: "phase2_relay",
      last_outcome: "",
      participant_count: 2,
      active_agent_count: 1,
      active_reply_count: 1,
      queued_reply_count: 0,
      running_reply_count: 1,
      completed_reply_count: 2,
      failed_reply_count: 0,
      open_question_count: 1,
      owner_question_count: 0,
      total_question_count: 1,
      resolved_question_count: 0,
      question_consolidation_status: "consolidating",
      last_activity_at: "2026-09-02T00:00:00Z",
      can_drain_replies: true,
      can_synthesize: false,
      can_restart: false,
      can_review_questions: false,
      can_review_result: false,
      can_view_activity: true,
    },
  },
} as ChannelDetail;
assert(
  consolidatingDetail.discussion_attention?.["feature/api"]?.can_review_questions === false,
  "dedup consolidation must not expose raw owner questions for review",
);

const workflowDetail = {
  channel_id: "ch-handoff",
  leader_member_id: "leader-1",
  leader_revision: 4,
  members: [],
  workflow_requests: [],
  syntheses: [{
    event_id: "evt-handoff-synthesis",
    thread_id: "feature/api",
    artifact_ref: "channel-artifacts/ch-handoff/prd.md",
    artifact_digest: "sha256:canonical-prd",
    readiness_ref: "channels/ch-handoff/prd/r1-readiness.json",
    readiness_digest: "sha256:readiness",
    readiness_verdict: "ready",
    implementation_start: true,
    open_questions: [],
    source_refs: ["event:evt-requirement"],
  }],
  consensus: {
    "feature/api": {
      artifact_ref: "channel-artifacts/ch-handoff/prd.md",
      artifact_digest: "canonical-prd",
      reached_event_id: "evt-handoff-consensus",
      prd_revision: 7,
      readiness_ref: "channels/ch-handoff/prd/r1-readiness.json",
      readiness_digest: "readiness",
      readiness_verdict: "ready",
      implementation_start: true,
    },
  },
} as ChannelDetail;

const handoffIdentity = {
  backend: "codex-headless",
  channelId: "ch-handoff",
  objective: "Create the accepted Task.",
  permissionProfile: "dangerous_full",
  projectId: "project-handoff",
  taskId: "",
  threadId: "feature/api",
  threadKey: "main",
  workflowContext: {
    channel_id: "ch-handoff",
    prd_revision: 7,
    source_refs: {
      channel_id: "ch-handoff",
      channel_prd_digest: "canonical-prd",
    },
  },
};
const stableHandoffKey = channelWorkflowHandoffIdempotencyKey(handoffIdentity);
assert(
  stableHandoffKey === channelWorkflowHandoffIdempotencyKey({
    ...handoffIdentity,
    workflowContext: {
      source_refs: {
        channel_prd_digest: "canonical-prd",
        channel_id: "ch-handoff",
      },
      prd_revision: 7,
      channel_id: "ch-handoff",
    },
  }),
  "handoff identity must ignore object insertion order",
);
assert(
  stableHandoffKey !== channelWorkflowHandoffIdempotencyKey({
    ...handoffIdentity,
    objective: "Create the accepted Task with a changed scope.",
  }),
  "a changed objective must create a new planning intent",
);

const submitted: Array<{ action: string; payload: Record<string, unknown> }> = [];
const preparedTaskIds: Array<string | undefined> = [];
let refreshCount = 0;
const adapter = createChannelActionAdapter({
  activeProjectId: "project-handoff",
  channelDetail: workflowDetail,
  prepareTaskAgent: (taskId) => preparedTaskIds.push(taskId),
  readStoredBackend: () => "codex-headless",
  refreshChannelDetail: async () => { refreshCount += 1; },
  selectedChannelId: "ch-handoff",
  snapshot: {
    project: { project_id: "project-handoff" },
    runtime: {
      agent_surface: {
        backends: [{ id: "codex-headless", available: true }],
        permission_profile: "dangerous_full",
      },
    },
  } as Snapshot,
  submitAction: async (action, payload) => {
    submitted.push({ action, payload });
    return {
      ok: true,
      status: "accepted",
      action,
      reason: "accepted",
    } as ActionResponse;
  },
});
await adapter.submitWorkflowRequest(
  "",
  "Create the accepted Task.",
  "feature/api",
);
await adapter.submitWorkflowRequest(
  "",
  "Create the accepted Task.",
  "feature/api",
);
assert(
  preparedTaskIds.length === 2 && preparedTaskIds.every((taskId) => !taskId),
  "the primary empty-Task handoff must still open Kanban Agent",
);
assert(
  submitted.length === 2 && submitted.every((item) => item.action === "chat-orchestrator"),
  "Channel handoff must use the controlled Agent route",
);
assert(
  submitted[0]?.payload.idempotency_key === submitted[1]?.payload.idempotency_key,
  "repeated canonical handoff clicks must reach the server with one stable identity",
);
assert(
  refreshCount === 2,
  "accepted handoffs refresh Channel projection without submitting a second intent",
);

console.log("channelActionAdapter tests passed");
