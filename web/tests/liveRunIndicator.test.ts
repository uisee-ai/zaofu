import {
  elapsedSecondsSince,
  formatElapsed,
  runStartTimestamp,
  toolCallCount,
} from "../src/components/agent-session/liveRunIndicator.js";
import { buildKanbanConversation } from "../src/components/agent-session/projection.js";
import type { AgentSessionPart } from "../src/components/agent-session/types.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function part(over: Partial<AgentSessionPart>): AgentSessionPart {
  return { id: "p", runId: "r", kind: "status", state: "streaming", title: "p", ...over } as AgentSessionPart;
}

// --- formatElapsed: seconds under a minute, "1m 23s" from there on ---
assert(formatElapsed(0) === "0s", "0 → 0s");
assert(formatElapsed(0.4) === "0s", "sub-second floors to 0s");
assert(formatElapsed(12.9) === "12s", `12.9 → 12s, got ${formatElapsed(12.9)}`);
assert(formatElapsed(59) === "59s", "59 stays in seconds");
assert(formatElapsed(60) === "1m 0s", `60 → 1m 0s, got ${formatElapsed(60)}`);
assert(formatElapsed(83) === "1m 23s", `83 → 1m 23s, got ${formatElapsed(83)}`);
assert(formatElapsed(-5) === "0s", "negative clamps to 0s");

// --- elapsedSecondsSince: parse guard + clamp ---
const nowMs = Date.parse("2026-07-16T00:01:00Z");
assert(elapsedSecondsSince("2026-07-16T00:00:48Z", nowMs) === 12, "12s elapsed");
assert(elapsedSecondsSince(undefined, nowMs) === undefined, "missing → undefined");
assert(elapsedSecondsSince("not-a-date", nowMs) === undefined, "unparseable → undefined");
assert(elapsedSecondsSince("2026-07-16T00:02:00Z", nowMs) === 0, "future start clamps to 0");

// --- runStartTimestamp: run.startedAt wins, else earliest part timestamp ---
assert(
  runStartTimestamp({ startedAt: "2026-07-16T00:00:00Z", parts: [part({ startedAt: "2026-07-15T00:00:00Z" })] })
    === "2026-07-16T00:00:00Z",
  "run.startedAt wins over parts",
);
assert(
  runStartTimestamp({
    parts: [
      part({ id: "a", updatedAt: "2026-07-16T00:00:05Z" }),
      part({ id: "b", startedAt: "2026-07-16T00:00:02Z" }),
    ],
  }) === "2026-07-16T00:00:02Z",
  "earliest part timestamp used when run has none",
);
assert(runStartTimestamp({ parts: [] }) === undefined, "no timestamps → undefined");

// --- toolCallCount: counts invocations, not results or non-tool parts ---
const groundingParts = [
  part({ id: "status-started", kind: "status" }),
  part({ id: "thinking", kind: "thinking" }),
  part({ id: "status-10", kind: "tool_call" }),
  part({ id: "tool-result-11", kind: "tool" }),
  part({ id: "status-12", kind: "tool_call" }),
  part({ id: "explicit-result", kind: "tool_result" }),
  part({ id: "tool-14", kind: "tool" }),
];
assert(toolCallCount(groundingParts) === 3, `2 calls + 1 generic tool = 3, got ${toolCallCount(groundingParts)}`);
assert(toolCallCount([]) === 0, "empty → 0");

// --- kanban projection keeps run.startedAt across later deltas (timer basis;
// ensureRun used to Object.assign an undefined startedAt over the recorded one) ---
const conversation = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-1",
      ts: "2026-07-16T00:00:00Z",
      type: "kanban.agent.turn.started",
      payload: { turn_id: "turn-1", thread_key: "main", backend: "codex" },
    },
    {
      seq: 2,
      id: "evt-2",
      ts: "2026-07-16T00:00:07Z",
      type: "kanban.agent.turn.delta",
      payload: { turn_id: "turn-1", thread_key: "main", message_type: "thinking", content: "planning" },
    },
  ],
});
const liveRun = conversation.threads[0]!.turns[0]!.runs[0]!;
assert(liveRun.startedAt === "2026-07-16T00:00:00Z", `run.startedAt survives deltas, got ${liveRun.startedAt}`);
assert(runStartTimestamp(liveRun) === "2026-07-16T00:00:00Z", "timer basis is the turn.started ts");

// --- terminal guard: an SSE-replayed stale delta must not flip a completed
// run back to streaming (it resurrected the live tool UI on a finished run) ---
const replayed = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-1",
      ts: "2026-07-16T00:00:00Z",
      type: "kanban.agent.turn.started",
      payload: { turn_id: "turn-1", thread_key: "main" },
    },
    {
      seq: 2,
      id: "evt-2",
      ts: "2026-07-16T00:00:20Z",
      type: "kanban.agent.reply",
      payload: { turn_id: "turn-1", thread_key: "main", answer: "done" },
    },
    {
      seq: 3,
      id: "evt-3",
      ts: "2026-07-16T00:00:20Z",
      type: "kanban.agent.turn.completed",
      payload: { turn_id: "turn-1", thread_key: "main" },
    },
    {
      // Ephemeral live-bus replay rows (no event seq) fold AFTER the
      // committed rows — the run is already terminal, so they must be
      // dropped: no status revive, no tool part, no text re-append.
      id: "live-stale-1",
      ts: "2026-07-16T00:00:05Z",
      type: "kanban.agent.turn.delta",
      payload: { turn_id: "turn-1", thread_key: "main", seq: 90, message_type: "tool_use", tool: "bash", input: { command: "ls" } },
    },
    {
      id: "live-stale-2",
      ts: "2026-07-16T00:00:06Z",
      type: "kanban.agent.turn.delta",
      payload: { turn_id: "turn-1", thread_key: "main", seq: 91, message_type: "text", content: "stray fragment" },
    },
  ],
});
const replayedRun = replayed.threads[0]!.turns[0]!.runs[0]!;
assert(replayedRun.status === "completed", `stale delta must not revive run, got ${replayedRun.status}`);
assert(!replayedRun.parts.some((p) => p.kind === "tool_call" || p.kind === "tool"), "stale tool delta dropped on a finished run");
const finalText = replayedRun.parts.find((p) => p.kind === "text");
assert(finalText?.content === "done", `stale text delta must not garble the final reply, got ${JSON.stringify(finalText?.content)}`);

// --- delta ordering: seq-less live deltas fold AFTER committed events, so
// the run joins the user.message turn (the question used to render BELOW the
// answer because the first delta created the run's turn first) ---
const ordered = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      // Live delta arrives FIRST in array order and carries no event seq.
      id: "live-delta-1",
      ts: "2026-07-16T00:00:02Z",
      type: "kanban.agent.turn.delta",
      payload: { turn_id: "turn-9", thread_key: "main", seq: 1, message_type: "thinking", content: "planning" },
    },
    {
      seq: 10,
      id: "evt-msg",
      ts: "2026-07-16T00:00:00Z",
      type: "user.message",
      payload: { target: "kanban-agent", runtime_delivery: "headless", thread_key: "main", message: "什么是 R4?" },
    },
    {
      seq: 11,
      id: "evt-created",
      ts: "2026-07-16T00:00:01Z",
      type: "kanban.agent.turn.created",
      payload: { turn_id: "turn-9", thread_key: "main", message_event_id: "evt-msg" },
    },
  ],
});
const orderedThread = ordered.threads.find((t) => t.id === "main")!;
assert(orderedThread.turns.length === 1, `question and run share ONE turn, got ${orderedThread.turns.length}`);
assert(orderedThread.turns[0]!.user?.content === "什么是 R4?", "turn carries the user question");
assert(orderedThread.turns[0]!.runs.length === 1, "run folded into the question turn");
assert(orderedThread.turns[0]!.runs[0]!.parts.some((p) => p.kind === "thinking"), "delta content reached the run");

// --- slim-indexed rows: turn.created without payload.message_event_id still
// anchors to the question turn via causation_id (= the user.message id) ---
const slimFold = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-q",
      ts: "2026-07-16T00:00:00Z",
      type: "user.message",
      payload: { target: "kanban-agent", runtime_delivery: "headless", thread_key: "main", message: "R3 怎么验证?" },
    },
    {
      seq: 2,
      id: "evt-created",
      ts: "2026-07-16T00:00:01Z",
      type: "kanban.agent.turn.created",
      causation_id: "evt-q",
      payload: { turn_id: "turn-slim", thread_key: "main" },
    },
    {
      seq: 3,
      id: "evt-reply",
      ts: "2026-07-16T00:00:09Z",
      type: "kanban.agent.reply",
      payload: { turn_id: "turn-slim", thread_key: "main", answer: "写退出码断言" },
    },
  ],
});
const slimThread = slimFold.threads.find((t) => t.id === "main")!;
assert(slimThread.turns.length === 1, `slim rows: question and answer share ONE turn, got ${slimThread.turns.length}`);
assert(slimThread.turns[0]!.user?.content === "R3 怎么验证?", "slim rows: turn carries the question");
assert(slimThread.turns[0]!.runs[0]!.parts.some((p) => p.kind === "text" && p.content === "写退出码断言"), "slim rows: answer folded under the question");

// --- structured proposal replies: the raw JSON remains canonical event data,
// but the conversation projection must not duplicate it beside the proposal card.
const proposalPayload = {
  action: "create-task",
  requested_action: "create-task",
  payload: { title: "Track auth timeout" },
  reason: "New work needs approval.",
  valid: true,
};
const proposalEnvelope = JSON.stringify({ action_proposal: {
  action: "create-task",
  payload: { title: "Track auth timeout" },
  reason: "New work needs approval.",
} });
const proposalOnly = buildKanbanConversation({
  activeThreadId: "main",
  events: [{
    seq: 1,
    id: "evt-proposal-only",
    ts: "2026-07-16T00:00:00Z",
    type: "kanban.agent.reply",
    payload: { turn_id: "turn-proposal-only", thread_key: "main", answer: proposalEnvelope, action_proposal: proposalPayload },
  }],
});
const proposalOnlyTurn = proposalOnly.threads[0]!.turns[0]!;
assert(!proposalOnlyTurn.runs[0]!.parts.some((p) => p.kind === "text"), "proposal-only JSON is hidden from reply text");
assert(proposalOnlyTurn.cards.some((card) => card.kind === "approve"), "proposal-only JSON still produces an Approve card");

const proposalWithProse = buildKanbanConversation({
  activeThreadId: "main",
  events: [{
    seq: 1,
    id: "evt-proposal-prose",
    ts: "2026-07-16T00:00:00Z",
    type: "kanban.agent.reply",
    payload: {
      turn_id: "turn-proposal-prose",
      thread_key: "main",
      answer: `I prepared a task for approval.\n\n\`\`\`json\n${proposalEnvelope}\n\`\`\``,
      action_proposal: proposalPayload,
    },
  }],
});
const proposalProseRun = proposalWithProse.threads[0]!.turns[0]!.runs[0]!;
assert(
  proposalProseRun.parts.find((p) => p.kind === "text")?.content === "I prepared a task for approval.",
  "proposal fenced JSON is removed while explanatory prose remains",
);

const recoveredProposal = buildKanbanConversation({
  activeThreadId: "main",
  events: [{ id: "evt-open-fence", type: "kanban.agent.reply", payload: {
    turn_id: "open-fence", thread_key: "main",
    answer: `第一段。\n\n第二段。\n\n\`\`\`json\n${proposalEnvelope}`,
    action_proposal: proposalPayload,
  } }],
});
const recoveredTurn = recoveredProposal.threads[0]!.turns[0]!;
assert(recoveredTurn.runs[0]!.parts.find((part) => part.kind === "text")?.content === "第一段。\n\n第二段。",
  "recovered open fence retains prose line breaks and hides raw control JSON");
assert(recoveredTurn.cards.some((card) => card.kind === "approve"), "recovered proposal remains reviewable");

const resolvedProposal = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-proposal-reply",
      ts: "2026-07-16T00:00:00Z",
      type: "kanban.agent.reply",
      payload: {
        turn_id: "turn-proposal-resolved",
        thread_key: "main",
        answer: proposalEnvelope,
        action_proposal: {
          ...proposalPayload,
          proposal_event_id: "evt-proposal-source",
        },
      },
    },
    {
      seq: 2,
      id: "evt-task-created",
      ts: "2026-07-16T00:01:00Z",
      type: "task.created",
      payload: {
        proposal_event_id: "evt-proposal-source",
        request: { proposal_event_id: "evt-proposal-source" },
      },
    },
  ],
});
const resolvedProposalCard = resolvedProposal.threads[0]!.turns[0]!.cards.find((card) => card.kind === "approve");
assert(resolvedProposalCard?.status === "completed", "task receipt resolves the historical Approve card");
assert(resolvedProposalCard?.payload?.resolution === "executed", "Approve card carries executed receipt");

const ordinaryJson = buildKanbanConversation({
  activeThreadId: "main",
  events: [{
    seq: 1,
    id: "evt-ordinary-json",
    ts: "2026-07-16T00:00:00Z",
    type: "kanban.agent.reply",
    payload: { turn_id: "turn-ordinary-json", thread_key: "main", answer: '{"status":"healthy"}' },
  }],
});
assert(
  ordinaryJson.threads[0]!.turns[0]!.runs[0]!.parts.find((p) => p.kind === "text")?.content === '{"status":"healthy"}',
  "ordinary JSON without a parsed proposal remains visible",
);

const planRequest = {
  request_event_id: "evt-plan-request",
  request_id: "plan-route",
  request_digest: "digest-route",
  revision: 1,
  header: "Route",
  question_id: "route",
  question: "Which route?",
  options: [
    { id: "research", label: "Research", description: "Collect evidence.", recommended: true },
    { id: "channel", label: "Channel", description: "Discuss with roles." },
  ],
  allow_other: true,
  valid: true,
};
const planEnvelope = JSON.stringify({ plan_request: {
  header: "Route",
  question: "Which route?",
  options: planRequest.options,
} });
const planConversation = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-plan-reply",
      ts: "2026-07-16T00:00:00Z",
      type: "kanban.agent.reply",
      payload: {
        turn_id: "turn-plan",
        thread_key: "main",
        answer: planEnvelope,
        plan_request: planRequest,
      },
    },
    {
      seq: 2,
      id: "evt-plan-answer",
      ts: "2026-07-16T00:01:00Z",
      type: "kanban.agent.plan.answered",
      payload: {
        request_event_id: "evt-plan-request",
        request_id: "plan-route",
        revision: 1,
        question_id: "route",
        option_id: "research",
        answer: "Research",
      },
    },
  ],
});
const planTurn = planConversation.threads[0]!.turns[0]!;
const planCard = planTurn.cards.find((card) => card.kind === "plan");
assert(Boolean(planCard?.planRequest), "structured Plan request produces a Plan card");
assert(planCard?.status === "completed", "answered Plan request projects as completed");
assert(planCard?.planRequest?.response?.answer === "Research", "Plan response binds to the exact request");
assert(!planTurn.runs[0]!.parts.some((part) => part.kind === "text"), "Plan envelope is hidden from reply prose");

const multiPlanRequest = {
  ...planRequest,
  request_event_id: "evt-plan-multi",
  request_id: "plan-multi",
  header: "Delivery inputs",
  questions: [
    {
      id: "route",
      header: "Route",
      question: "Which route?",
      options: planRequest.options,
      allow_other: true,
    },
    {
      id: "evidence",
      header: "Evidence",
      question: "Which evidence depth?",
      options: [
        { id: "focused", label: "Focused", recommended: true },
        { id: "broad", label: "Broad" },
      ],
      allow_other: true,
    },
  ],
};
const multiPlanEnvelope = JSON.stringify({
  plan_request: {
    header: "Delivery inputs",
    questions: multiPlanRequest.questions,
  },
});
const multiPlanConversation = buildKanbanConversation({
  activeThreadId: "main",
  events: [{
    seq: 1,
    id: "evt-plan-multi-reply",
    ts: "2026-07-16T00:00:00Z",
    type: "kanban.agent.reply",
    payload: {
      turn_id: "turn-plan-multi",
      thread_key: "main",
      answer: multiPlanEnvelope,
      plan_request: multiPlanRequest,
    },
  }],
});
assert(
  !multiPlanConversation.threads[0]!.turns[0]!.runs[0]!.parts.some(
    (part) => part.kind === "text",
  ),
  "multi-question Plan envelope is hidden from reply prose",
);

const workflowPlanRequest = {
  ...planRequest,
  request_event_id: "evt-workflow-plan",
  request_id: "plan-workflow-route",
  subject_type: "task_workflow",
  config_digest: "sha256:config",
  options: [
    {
      id: "delivery",
      label: "PRD delivery",
      recommended: true,
      submit_action: "workflow-start",
      submit_mode: "propose",
      submit_details: {
        route_id: "delivery:prd:standard",
        family: "delivery",
        topology: "multi_lane",
        roles: ["planner", "dev-lane-0", "verify-lane-0"],
        writer_roles: ["dev-lane-0"],
        verify_roles: ["verify-lane-0"],
        lane_count: 2,
        output_profile: "candidate_and_evidence",
      },
    },
    {
      id: "defer",
      label: "No workflow yet",
      submit_mode: "continue",
    },
  ],
};
const workflowPlanConversation = buildKanbanConversation({
  activeThreadId: "main",
  events: [{
    seq: 1,
    id: "evt-workflow-plan-reply",
    ts: "2026-07-16T00:00:00Z",
    type: "kanban.agent.reply",
    payload: {
      turn_id: "turn-workflow-plan",
      thread_key: "main",
      answer: "Choose a workflow route.",
      plan_request: workflowPlanRequest,
    },
  }],
});
const workflowPlan = workflowPlanConversation.threads[0]!.turns[0]!.cards[0]!.planRequest;
assert(workflowPlan?.subjectType === "task_workflow", "workflow Plan subject is projected");
assert(workflowPlan?.options[0]?.submitMode === "propose", "option-level proposal mode is projected");
assert(workflowPlan?.options[0]?.submitDetails?.routeId === "delivery:prd:standard", "route details are projected");
assert(workflowPlan?.options[0]?.submitDetails?.laneCount === 2, "lane count is projected");

const standaloneWorkflowPlan = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-workflow-origin",
      ts: "2026-07-16T00:00:00Z",
      type: "user.message",
      payload: {
        target: "kanban-agent",
        runtime_delivery: "headless",
        thread_key: "main",
        message: "Create a Task and recommend its workflow.",
      },
    },
    {
      seq: 2,
      id: "evt-workflow-turn",
      ts: "2026-07-16T00:00:01Z",
      type: "kanban.agent.turn.created",
      causation_id: "evt-workflow-origin",
      payload: {
        turn_id: "turn-workflow",
        thread_key: "main",
        message_event_id: "evt-workflow-origin",
      },
    },
    {
      seq: 3,
      id: "evt-workflow-plan",
      ts: "2026-07-16T00:00:02Z",
      type: "kanban.agent.plan.requested",
      task_id: "TASK-WORKFLOW",
      payload: {
        thread_key: "main",
        plan_request: {
          ...workflowPlanRequest,
          turn_id: "turn-workflow",
          originating_message_event_id: "evt-workflow-origin",
        },
      },
    },
    {
      seq: 4,
      id: "evt-workflow-proposal",
      ts: "2026-07-16T00:00:03Z",
      type: "operator.action.proposed",
      task_id: "TASK-WORKFLOW",
      payload: {
        turn_id: "turn-workflow",
        thread_key: "main",
        proposal: {
          proposal_event_id: "evt-workflow-proposal",
          proposal_id: "proposal-workflow",
          proposal_digest: "digest-workflow",
          revision: 1,
          action: "workflow-start",
          requested_action: "workflow-start",
          payload: {
            task_id: "TASK-WORKFLOW",
            route_id: "delivery:prd:standard",
            objective: "Run the Task.",
          },
          reason: "The owner selected this route.",
          valid: true,
        },
      },
    },
  ],
});
const standaloneTurn = standaloneWorkflowPlan.threads[0]!.turns.find(
  (turn) => turn.id === "evt-workflow-origin",
);
assert(Boolean(standaloneTurn?.cards[0]?.planRequest), "standalone Plan event renders a durable Plan card");
assert(standaloneTurn?.cards[0]?.planRequest?.subjectType === "task_workflow", "standalone Task workflow Plan keeps its subject");
assert(standaloneTurn?.cards.some((card) => (
  card.kind === "approve"
  && card.proposal?.action === "workflow-start"
)), "standalone Plan proposal renders an inline Approve card");

const planContinuationConversation = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-plan-reply",
      ts: "2026-07-16T00:00:00Z",
      type: "kanban.agent.reply",
      payload: {
        turn_id: "turn-plan",
        thread_key: "main",
        answer: planEnvelope,
        plan_request: planRequest,
      },
    },
    {
      seq: 2,
      id: "evt-plan-answer",
      ts: "2026-07-16T00:01:00Z",
      type: "kanban.agent.plan.answered",
      payload: {
        request_event_id: "evt-plan-request",
        request_id: "plan-route",
        revision: 1,
        question_id: "route",
        option_id: "research",
        answer: "Research",
      },
    },
    {
      seq: 3,
      id: "evt-plan-continuation-message",
      causation_id: "evt-plan-answer",
      ts: "2026-07-16T00:02:00Z",
      type: "user.message",
      payload: {
        target: "kanban-agent",
        runtime_delivery: "headless",
        thread_key: "main",
        message: "Plan: Route\nQuestion: Which route?\nAnswer: Research",
        request: {
          plan_response: {
            request_event_id: "evt-plan-request",
            request_id: "plan-route",
            revision: 1,
            question_id: "route",
            option_id: "research",
            answer: "Research",
          },
        },
      },
    },
    {
      seq: 4,
      id: "evt-plan-continuation-created",
      causation_id: "evt-plan-continuation-message",
      ts: "2026-07-16T00:02:01Z",
      type: "kanban.agent.turn.created",
      payload: {
        turn_id: "turn-plan-continuation",
        thread_key: "main",
      },
    },
    {
      seq: 5,
      id: "evt-plan-continuation-reply",
      ts: "2026-07-16T00:02:02Z",
      type: "kanban.agent.reply",
      payload: {
        turn_id: "turn-plan-continuation",
        thread_key: "main",
        answer: "I prepared the selected route.",
      },
    },
  ],
});
const continuationTurn = planContinuationConversation.threads[0]!.turns.find((turn) => (
  turn.id === "evt-plan-continuation-message"
));
assert(continuationTurn?.user === undefined, "internal Plan continuation does not render a protocol-shaped user bubble");
assert(
  continuationTurn?.runs[0]?.parts.some((part) => part.content === "I prepared the selected route."),
  "internal Plan continuation still renders the resumed agent reply",
);

const permissionRetryConversation = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-permission-retry-message",
      ts: "2026-07-16T00:03:00Z",
      type: "user.message",
      payload: {
        target: "kanban-agent",
        runtime_delivery: "headless",
        thread_key: "main",
        message: "Implement the requested change.",
        permission_escalation_retry_for: "evt-sandbox-failed",
      },
    },
    {
      seq: 2,
      id: "evt-permission-retry-created",
      causation_id: "evt-permission-retry-message",
      ts: "2026-07-16T00:03:01Z",
      type: "kanban.agent.turn.created",
      payload: {
        turn_id: "turn-permission-retry",
        thread_key: "main",
      },
    },
    {
      seq: 3,
      id: "evt-permission-retry-reply",
      causation_id: "evt-permission-retry-message",
      ts: "2026-07-16T00:03:02Z",
      type: "kanban.agent.reply",
      payload: {
        turn_id: "turn-permission-retry",
        thread_key: "main",
        answer: "The change is complete.",
      },
    },
  ],
});
const permissionRetryTurn = permissionRetryConversation.threads[0]!.turns.find((turn) => (
  turn.id === "evt-permission-retry-message"
));
assert(
  permissionRetryTurn?.user === undefined,
  "one-turn permission retry does not duplicate the original user message",
);
assert(
  permissionRetryTurn?.runs[0]?.parts.some((part) => part.content === "The change is complete."),
  "one-turn permission retry still renders the agent result",
);

const revisedPlanConversation = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-plan-reply-r1",
      ts: "2026-07-16T00:00:00Z",
      type: "kanban.agent.reply",
      payload: {
        turn_id: "turn-plan-r1",
        thread_key: "main",
        answer: planEnvelope,
        plan_request: {
          ...planRequest,
          request_event_id: "evt-plan-request-r1",
        },
      },
    },
    {
      seq: 2,
      id: "evt-plan-reply-r2",
      ts: "2026-07-16T00:01:00Z",
      type: "kanban.agent.reply",
      payload: {
        turn_id: "turn-plan-r2",
        thread_key: "main",
        answer: planEnvelope,
        plan_request: {
          ...planRequest,
          request_event_id: "evt-plan-request-r2",
          request_digest: "digest-route-r2",
          revision: 2,
        },
      },
    },
    {
      seq: 3,
      id: "evt-plan-answer-r2",
      ts: "2026-07-16T00:02:00Z",
      type: "kanban.agent.plan.answered",
      payload: {
        request_event_id: "evt-plan-request-r2",
        request_id: "plan-route",
        revision: 2,
        question_id: "route",
        option_id: "channel",
        answer: "Channel",
      },
    },
  ],
});
const revisedPlanCards = revisedPlanConversation.threads[0]!.turns
  .flatMap((turn) => turn.cards)
  .filter((card) => card.kind === "plan");
const oldPlanCard = revisedPlanCards.find((card) => (
  card.planRequest?.requestEventId === "evt-plan-request-r1"
));
const newPlanCard = revisedPlanCards.find((card) => (
  card.planRequest?.requestEventId === "evt-plan-request-r2"
));
assert(oldPlanCard?.status === "stale", "older Plan revision projects as superseded");
assert(!oldPlanCard?.planRequest?.response, "new answer does not bind to an older Plan revision");
assert(newPlanCard?.status === "completed", "answered latest Plan revision is completed");
assert(newPlanCard?.planRequest?.response?.answer === "Channel", "latest Plan revision gets its exact answer");

const preparingConversation = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-preparing-message",
      ts: "2026-07-16T00:04:00Z",
      type: "user.message",
      payload: {
        target: "kanban-agent",
        runtime_delivery: "headless",
        thread_key: "main",
        message: "Which workflow should we use?",
      },
    },
    {
      seq: 2,
      id: "evt-preparing-created",
      causation_id: "evt-preparing-message",
      ts: "2026-07-16T00:04:01Z",
      type: "kanban.agent.turn.created",
      payload: { turn_id: "turn-preparing", thread_key: "main" },
    },
    {
      id: "live-preparing",
      ts: "2026-07-16T00:04:02Z",
      type: "kanban.agent.turn.delta",
      payload: {
        turn_id: "turn-preparing",
        thread_key: "main",
        message_type: "status",
        content: "Preparing choices...",
        control_state: "plan_request_buffering",
      },
    },
  ],
});
const preparingTurn = preparingConversation.threads[0]!.turns.find(
  (turn) => turn.id === "evt-preparing-message",
);
assert(
  preparingTurn?.cards.some((card) => card.payload?.preparing === true),
  "typed control signal projects a non-submittable preparing card",
);
assert(
  !preparingTurn?.runs[0]?.parts.some((part) => part.content?.includes("plan_request")),
  "control protocol never becomes visible response text",
);

const preparedPlanConversation = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-prepared-message",
      ts: "2026-07-16T00:05:00Z",
      type: "user.message",
      payload: {
        target: "kanban-agent",
        runtime_delivery: "headless",
        thread_key: "main",
        message: "Which workflow should we use?",
      },
    },
    {
      seq: 2,
      id: "evt-prepared-created",
      causation_id: "evt-prepared-message",
      ts: "2026-07-16T00:05:01Z",
      type: "kanban.agent.turn.created",
      payload: { turn_id: "turn-prepared", thread_key: "main" },
    },
    {
      seq: 3,
      id: "evt-prepared-plan",
      ts: "2026-07-16T00:05:03Z",
      type: "kanban.agent.plan.requested",
      payload: {
        thread_key: "main",
        plan_request: {
          ...planRequest,
          request_event_id: "evt-prepared-plan",
          turn_id: "turn-prepared",
          originating_message_event_id: "evt-prepared-message",
        },
      },
    },
    {
      id: "live-prepared",
      ts: "2026-07-16T00:05:02Z",
      type: "kanban.agent.turn.delta",
      payload: {
        turn_id: "turn-prepared",
        thread_key: "main",
        message_type: "status",
        content: "Preparing choices...",
        control_state: "plan_request_buffering",
      },
    },
  ],
});
const preparedTurn = preparedPlanConversation.threads[0]!.turns.find(
  (turn) => turn.id === "evt-prepared-message",
);
assert(
  preparedTurn?.cards.some((card) => Boolean(card.planRequest)),
  "canonical plan request replaces the preparing state",
);
assert(
  !preparedTurn?.cards.some((card) => card.payload?.preparing === true),
  "preparing card does not survive canonical replacement",
);

const invalidControlConversation = buildKanbanConversation({
  activeThreadId: "main",
  events: [
    {
      seq: 1,
      id: "evt-invalid-message",
      ts: "2026-07-16T00:06:00Z",
      type: "user.message",
      payload: {
        target: "kanban-agent",
        runtime_delivery: "headless",
        thread_key: "main",
        message: "Ask me a question.",
      },
    },
    {
      seq: 2,
      id: "evt-invalid-created",
      causation_id: "evt-invalid-message",
      ts: "2026-07-16T00:06:01Z",
      type: "kanban.agent.turn.created",
      payload: { turn_id: "turn-invalid", thread_key: "main" },
    },
    {
      seq: 3,
      id: "evt-invalid-reply",
      ts: "2026-07-16T00:06:03Z",
      type: "kanban.agent.reply",
      payload: {
        turn_id: "turn-invalid",
        thread_key: "main",
        answer: "The request was invalid.",
      },
    },
    {
      id: "live-invalid",
      ts: "2026-07-16T00:06:02Z",
      type: "kanban.agent.turn.delta",
      payload: {
        turn_id: "turn-invalid",
        thread_key: "main",
        message_type: "status",
        content: "Preparing choices...",
        control_state: "plan_request_buffering",
      },
    },
  ],
});
assert(
  !invalidControlConversation.threads[0]!.turns.flatMap((turn) => turn.cards)
    .some((card) => card.payload?.preparing === true),
  "terminal reply clears preparing state when no valid plan is produced",
);

console.log("liveRunIndicator.test.ts OK");
