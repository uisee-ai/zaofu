import {
  buildChannelWorkflowPlanningRequest,
  canonicalChannelPrd,
  resolveChannelWorkflowBackend,
} from "../src/components/channel/workflowPlanning.js";
import type { ChannelDetail } from "../src/api/types.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  assert(
    JSON.stringify(actual) === JSON.stringify(expected),
    `${message}: ${JSON.stringify(actual)}`,
  );
}

const detail = {
  channel_id: "ch-prd",
  name: "prd",
  leader_member_id: "leader-1",
  leader_revision: 4,
  members: [],
  workflow_requests: [],
  syntheses: [{
    event_id: "evt-synthesis",
    thread_id: "main",
    artifact_ref: "channel-artifacts/ch-prd/prd.md",
    artifact_digest: "sha256:canonical",
    readiness_ref: "channels/ch-prd/prd/r1-readiness.json",
    readiness_digest: "sha256:readiness",
    readiness_verdict: "ready",
    implementation_start: true,
    open_questions: [],
    source_refs: ["event:evt-requirement", "channel:ch-prd/main"],
  }],
  consensus: {
    main: {
      artifact_ref: "channel-artifacts/ch-prd/prd.md",
      artifact_digest: "canonical",
      reached_event_id: "evt-consensus",
      prd_revision: 7,
      readiness_ref: "channels/ch-prd/prd/r1-readiness.json",
      readiness_digest: "readiness",
      readiness_verdict: "ready",
      implementation_start: true,
    },
  },
} as ChannelDetail;

const canonical = canonicalChannelPrd(detail);
assert(canonical.ready, "canonical PRD should be ready");
assertEqual(canonical.synthesisEventId, "evt-synthesis", "synthesis id");

const request = buildChannelWorkflowPlanningRequest({
  channelId: "ch-prd",
  detail,
  objective: "Implement the accepted PRD.",
  taskId: "TASK-42",
});
assert(request, "planning request should be built");
assert(request.message.includes("TASK-42"), "message should bind the Task");
assert(request.message.includes("不要直接启动 workflow"), "message should preserve Plan boundary");
assertEqual(request.workflowContext.source_refs, {
  channel_id: "ch-prd",
  thread_id: "main",
  synthesis_event_id: "evt-synthesis",
  channel_consensus_event_id: "evt-consensus",
  channel_prd_ref: "channel-artifacts/ch-prd/prd.md",
  channel_prd_digest: "canonical",
}, "source refs");
assertEqual(request.workflowContext.artifact_refs, [{
  kind: "channel_prd",
  ref: "channel-artifacts/ch-prd/prd.md",
  digest: "canonical",
  source_event_id: "evt-synthesis",
  source_refs: ["event:evt-requirement", "channel:ch-prd/main"],
}], "artifact refs");
assertEqual(request.workflowContext.channel_member_id, "leader-1", "leader identity");
assertEqual(request.workflowContext.leader_revision, 4, "leader revision");
assertEqual(request.workflowContext.prd_revision, 7, "PRD revision");

const taskCreateRequest = buildChannelWorkflowPlanningRequest({
  channelId: "ch-prd",
  detail,
  objective: "Create an implementation Task from the accepted PRD.",
  taskId: "",
});
assert(taskCreateRequest, "Task-create planning request should be built");
assert(
  taskCreateRequest.message.includes("subject_type=task_create"),
  "empty task id should request a task_create Plan",
);
assert(
  taskCreateRequest.message.includes("workflow_plan 必须有 2-3 个选项"),
  "Task-create request should prepare a bounded Workflow Plan",
);
assert(
  taskCreateRequest.message.includes("Task 确认只能创建 Task 并发布第二个 Workflow Plan"),
  "Task-create request should preserve the second confirmation boundary",
);
const { expected_output: _taskCreateOutput, ...taskCreateAuthority } = (
  taskCreateRequest.workflowContext
);
const { expected_output: _workflowOutput, ...workflowAuthority } = (
  request.workflowContext
);
assertEqual(
  taskCreateAuthority,
  workflowAuthority,
  "Task-create and workflow planning must carry identical Channel authority",
);

const unresolved = {
  ...detail,
  consensus: {
    main: {
      artifact_ref: "channel-artifacts/ch-prd/prd.md",
      artifact_digest: "canonical",
    },
  },
} as ChannelDetail;
assert(!canonicalChannelPrd(unresolved).ready, "unresolved consensus should not be canonical");
assertEqual(buildChannelWorkflowPlanningRequest({
  channelId: "ch-prd",
  detail: unresolved,
  objective: "",
  taskId: "TASK-42",
}), null, "unresolved consensus should block planning");

const implementationBlocked = {
  ...detail,
  syntheses: [{
    ...(detail.syntheses?.[0] ?? {}),
    implementation_start: false,
  }],
} as ChannelDetail;
assert(
  canonicalChannelPrd(implementationBlocked).ready,
  "implementation_start=false should still allow Task/workflow planning",
);
assert(buildChannelWorkflowPlanningRequest({
  channelId: "ch-prd",
  detail: implementationBlocked,
  objective: "Plan the approved work without starting implementation.",
  taskId: "",
}), "planning-ready PRD should produce a Task-create request");

const ownerAcceptedReadiness = {
  ...detail,
  syntheses: [{
    ...(detail.syntheses?.[0] ?? {}),
    readiness_verdict: "needs_owner",
    implementation_start: false,
  }],
  consensus: {
    main: {
      ...(detail.consensus?.main ?? {}),
      readiness_verdict: "needs_owner",
      implementation_start: false,
      human_confirmed: true,
      risk_accepted: true,
      confirmed_readiness_ref: "channels/ch-prd/prd/r1-readiness.json",
      confirmed_readiness_digest: "sha256:readiness",
    },
  },
} as ChannelDetail;
assert(
  canonicalChannelPrd(ownerAcceptedReadiness).ready,
  "exact owner risk acceptance should authorize the matching Channel PRD",
);
assert(
  buildChannelWorkflowPlanningRequest({
    channelId: "ch-prd",
    detail: ownerAcceptedReadiness,
    objective: "Create the accepted Task.",
    taskId: "",
  }),
  "exact owner risk acceptance should enable Task planning",
);

const mismatchedOwnerAcceptance = {
  ...ownerAcceptedReadiness,
  consensus: {
    main: {
      ...(ownerAcceptedReadiness.consensus?.main ?? {}),
      confirmed_readiness_digest: "sha256:other-readiness",
    },
  },
} as ChannelDetail;
assert(
  !canonicalChannelPrd(mismatchedOwnerAcceptance).ready,
  "a risk acceptance for another readiness artifact must not authorize planning",
);

assertEqual(resolveChannelWorkflowBackend({
  storedBackend: "claude-headless",
}), "claude-headless", "light snapshot should retain the selected backend");
assertEqual(resolveChannelWorkflowBackend({
  availableBackends: ["codex-headless"],
  configuredBackends: ["claude-code", "codex"],
  storedBackend: "claude-headless",
}), "codex-headless", "unavailable stored backend should use an available config route");
assertEqual(resolveChannelWorkflowBackend({
  configuredBackends: ["claude-code"],
}), "claude-headless", "configured provider should map to its headless transport");
assertEqual(resolveChannelWorkflowBackend({}), "codex-headless", "empty context keeps the legacy fallback");

console.log("channelWorkflowPlanning tests passed");
