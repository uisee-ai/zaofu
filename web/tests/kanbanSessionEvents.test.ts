import type { RecentEvent } from "../src/api/types.js";
import {
  kanbanAgentSessionEventsFromLive,
  mergeBoundedKanbanSessionEvents,
  mergeEventsByIdentity,
} from "../src/components/orchestrator/kanbanSessionEvents.js";
import {
  parsePlanRequest,
  parsePlanResponse,
} from "../src/components/agent-session/agentUiEvent.js";
import { buildKanbanConversation } from "../src/components/agent-session/projection.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function event(seq: number, type: string, payload: Record<string, unknown>, taskId = ""): RecentEvent {
  return {
    id: `evt-${seq}`,
    seq,
    ts: `2026-06-29T12:00:${String(seq).padStart(2, "0")}.000Z`,
    type,
    task_id: taskId || null,
    payload,
  };
}

const scope = {
  projectId: "proj-a",
  conversationId: "kanban:proj-a",
  backend: "codex-headless",
  taskId: "",
};

const liveTurn = [
  event(1, "user.message", {
    source: "kanban",
    target: "kanban-agent",
    runtime_delivery: "headless",
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    message: "review docs",
  }),
  event(2, "kanban.agent.turn.created", {
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    turn_id: "turn-a",
    message_event_id: "evt-1",
  }),
  event(3, "kanban.agent.turn.delta", {
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    turn_id: "turn-a",
    message_type: "text",
    content: "working",
  }),
  event(4, "kanban.agent.reply", {
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    turn_id: "turn-a",
    answer: "done",
  }),
];

const unrelatedChannelRun = event(5, "agent.session.part.delta", {
  backend: "codex-headless",
  project_id: "proj-a",
  conversation_id: "channel:ch-a",
  thread_id: "thread-a",
  source: "channel-agent.headless",
  content: "channel-only",
});

const initialScoped = kanbanAgentSessionEventsFromLive([...liveTurn, unrelatedChannelRun], scope);
assert(initialScoped.length === 4, `should keep only kanban conversation events, got ${initialScoped.length}`);

const buffered = mergeBoundedKanbanSessionEvents([], initialScoped);
const afterPageSwitchLiveEvents: RecentEvent[] = [];
const conversationEvents = mergeEventsByIdentity(buffered, afterPageSwitchLiveEvents);
const retainedPrompt = conversationEvents.find((item) => item.type === "user.message");
const retainedReply = conversationEvents.find((item) => item.type === "kanban.agent.reply");

assert(retainedPrompt?.payload?.message === "review docs", "buffer should preserve user prompt after live events reset");
assert(retainedReply?.payload?.answer === "done", "buffer should preserve reply after live events reset");

const bounded = mergeBoundedKanbanSessionEvents([], [
  event(10, "kanban.agent.reply", { backend: "codex-headless", answer: "a" }),
  event(11, "kanban.agent.reply", { backend: "codex-headless", answer: "b" }),
  event(12, "kanban.agent.reply", { backend: "codex-headless", answer: "c" }),
  event(13, "kanban.agent.reply", { backend: "codex-headless", answer: "d" }),
], 3);
assert(bounded.map((item) => item.seq).join(",") === "11,12,13", "bounded buffer should keep newest events");

// Regression (live-stream backend-agnostic fold, consistent with b7eebff4):
// a kanban thread is one durable conversation that can span backends. A live
// codex delta must still fold into the view when the operator's selector
// currently reads claude — backend is advisory, never an exclusion. Before the
// fix this delta was dropped and the reply stayed stuck on "thinking".
const claudeScope = {
  projectId: "proj-a",
  conversationId: "kanban:proj-a",
  backend: "claude-headless",
  taskId: "",
};
const codexDeltaUnderClaudeSelector = kanbanAgentSessionEventsFromLive([
  event(20, "kanban.agent.turn.delta", {
    backend: "codex",
    provider: "codex",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    turn_id: "turn-x",
    content: "streamed",
  }),
], claudeScope);
assert(
  codexDeltaUnderClaudeSelector.length === 1,
  `codex live delta must fold under a claude selector (backend advisory), got ${codexDeltaUnderClaudeSelector.length}`,
);

const livePlanEvents = kanbanAgentSessionEventsFromLive([
  event(30, "kanban.agent.plan.requested", {
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    plan_request: { request_id: "plan-route" },
  }),
  event(31, "kanban.agent.plan.answered", {
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    request_id: "plan-route",
  }),
], scope);
assert(livePlanEvents.length === 2, "Plan request and answer events stay in the live session buffer");

const multiQuestionPlan = parsePlanRequest({
  plan_request: {
    request_event_id: "evt-plan-multi",
    request_id: "plan-multi",
    revision: 2,
    header: "Delivery inputs",
    questions: [
      {
        id: "route",
        question: "Which route?",
        options: [
          { id: "direct", label: "Direct", recommended: true },
          { id: "research", label: "Research" },
        ],
      },
      {
        id: "evidence",
        question: "Which evidence depth?",
        options: [
          { id: "focused", label: "Focused", recommended: true },
          { id: "broad", label: "Broad" },
        ],
      },
    ],
  },
});
assert(multiQuestionPlan?.questions.length === 2, "Plan projection should retain both questions");
assert(
  multiQuestionPlan?.questions.every((question) => question.options[0]?.recommended),
  "Plan projection should retain each recommended option",
);

const invalidPlanWithoutQuestion = parsePlanRequest({
  plan_request: {
    request_event_id: "evt-plan-invalid",
    request_id: "plan-invalid",
    revision: 1,
    header: "Channel setup",
    question_id: "decision",
    question: "",
    options: [
      { id: "three-rounds", label: "Three rounds" },
      { id: "two-rounds", label: "Two rounds" },
    ],
    valid: false,
    validation_error: "question is required",
  },
});
assert(invalidPlanWithoutQuestion !== undefined, "invalid Plan should remain visible for discussion");
assert(invalidPlanWithoutQuestion?.valid === false, "invalid Plan must stay mechanically disabled");
assert(
  invalidPlanWithoutQuestion?.question === "This Plan draft needs revision before it can be submitted.",
  "invalid Plan should get a bounded recovery question",
);
assert(
  invalidPlanWithoutQuestion?.validationError === "question is required",
  "invalid Plan should retain its validation diagnostics",
);
const explicitChannelPlan = parsePlanRequest({
  plan_request: {
    request_event_id: "evt-channel-plan",
    request_id: "plan-channel",
    revision: 1,
    header: "Channel setup",
    question_id: "mode",
    question: "Which discussion mode?",
    options: [{
      id: "multi-lens",
      label: "Multi-lens",
      submit_details: {
        template_id: "prd-clarification",
        member_count: 5,
        members: [{ role: "product_pm" }, { role: "arch" }],
        max_rounds: 4,
        mode: "multi_lens",
        engine_mode: "fanout_then_synthesis",
        routing_strategy: "blind_fanout_then_synthesis",
        first_pass_reply_count: 5,
      },
    }],
  },
});
const explicitChannelDetails = explicitChannelPlan?.options[0]?.submitDetails;
assert(explicitChannelDetails?.mode === "multi_lens", "Channel Plan retains explicit mode");
assert(
  explicitChannelDetails?.engineMode === "fanout_then_synthesis",
  "Channel Plan retains engine mapping",
);
assert(
  explicitChannelDetails?.firstPassReplyCount === 5,
  "Channel Plan retains first-pass fanout count",
);
const taskWorkflowPlan = parsePlanRequest({
  plan_request: {
    request_event_id: "evt-task-workflow-plan",
    request_id: "plan-task-workflow",
    revision: 1,
    header: "PRD workflow route",
    question_id: "prd-workflow-route",
    question: "Which route should execute the Task?",
    options: [{
      id: "delivery",
      label: "Delivery (Recommended)",
      submit_details: {
        route_id: "delivery:prd:default",
        input_keys: ["backend", "target_root"],
        preflight: "ready",
      },
    }],
  },
});
const taskWorkflowDetails = taskWorkflowPlan?.options[0]?.submitDetails;
assert(
  taskWorkflowDetails?.inputKeys?.join(",") === "backend,target_root",
  "Task Workflow Plan retains input keys",
);
assert(
  taskWorkflowDetails?.preflight === "ready",
  "Task Workflow Plan retains preflight state",
);
const adaptiveChannelPlan = parsePlanRequest({
  plan_request: {
    request_event_id: "evt-channel-adaptive-plan",
    request_id: "plan-channel-adaptive",
    revision: 1,
    header: "Channel setup",
    question_id: "mode",
    question: "Which discussion mode?",
    options: [{
      id: "prd-clarification",
      label: "PRD clarification",
      submit_details: {
        template_id: "prd-clarification",
        member_count: 4,
        members: [
          { role: "product_pm" },
          { role: "arch" },
          { role: "critic" },
          { role: "synthesizer" },
        ],
        round_policy: "synthesis_adaptive",
        profiles: [
          {
            member_id: "arch",
            channel_role: "arch",
            profile_id: "architecture-codex",
            display_name: "Architecture Codex",
          },
        ],
      },
    }],
  },
});
const adaptiveChannelDetails = adaptiveChannelPlan?.options[0]?.submitDetails;
assert(
  adaptiveChannelDetails?.roundPolicy === "synthesis_adaptive",
  "Channel Plan retains the adaptive round policy without inventing a cap",
);
assert(
  adaptiveChannelDetails?.maxRounds === 0,
  "adaptive Channel Plan has no implicit max-rounds value",
);
assert(
  adaptiveChannelDetails?.profiles?.[0]?.profileId === "architecture-codex",
  "Channel Plan retains the selected role-compatible Profile",
);
const multiQuestionResponse = parsePlanResponse({
  request_event_id: "evt-plan-multi",
  request_id: "plan-multi",
  revision: 2,
  answers: [
    { question_id: "route", option_id: "direct", answer: "Direct" },
    { question_id: "evidence", option_id: "focused", answer: "Focused" },
  ],
});
assert(
  multiQuestionResponse?.answers.length === 2,
  "Plan response projection should retain the atomic answer set",
);

const proposalReceipts = kanbanAgentSessionEventsFromLive([
  event(39, "operator.action.proposed", {
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    proposal: { proposal_event_id: "evt-proposal" },
  }),
  event(40, "operator.action.resolved", {
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    proposal_event_id: "evt-proposal",
    resolution: "dismissed",
  }),
  event(41, "task.created", {
    proposal_event_id: "evt-proposal-2",
    request: { proposal_event_id: "evt-proposal-2" },
  }),
  event(42, "operator.action.proposed", {
    project_id: "proj-a",
    source: "cli",
    proposal: { proposal_event_id: "evt-unscoped" },
  }),
], scope);
assert(
  proposalReceipts.length === 3,
  "scoped proposal requests and receipts stay in the live session buffer without leaking CLI proposals",
);

const resultEvents = [
  event(50, "workflow.result.available", {
    schema_version: "workflow-result.v1",
    result_kind: "research_report",
    status: "available",
    project_id: "proj-a",
    origin_surface: "kanban_agent",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    request_id: "REQ-1",
    request_revision: 1,
    task_id: "TASK-1",
    workflow_run_id: "wf-1",
    terminal_event_id: "evt-terminal-1",
    artifact_ref: "research/TASK-1/result.md",
    artifact_digest: "a".repeat(64),
    summary: "Evidence-backed result.",
    origin_binding: {
      schema_version: "workflow-origin-binding.v1",
      surface: "kanban_agent",
      project_id: "proj-a",
      conversation_id: "kanban:proj-a",
      thread_key: "thread-a",
    },
  }, "TASK-1"),
  event(51, "workflow.research.adopted", {
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    request_id: "REQ-1",
    request_revision: 1,
    result_event_id: "evt-50",
    artifact_digest: "a".repeat(64),
    origin_binding: {
      schema_version: "workflow-origin-binding.v1",
      surface: "kanban_agent",
      project_id: "proj-a",
      conversation_id: "kanban:proj-a",
      thread_key: "thread-a",
    },
  }, "TASK-1"),
];
const liveResultEvents = kanbanAgentSessionEventsFromLive(resultEvents, scope);
assert(liveResultEvents.length === 2, "Kanban SSE keeps result available and adoption events");
const resultConversation = buildKanbanConversation({
  events: [
    ...liveResultEvents,
    event(52, "workflow.result.available", {
      schema_version: "workflow-result.v1",
      result_kind: "research_report",
      status: "available",
      project_id: "proj-a",
      origin_surface: "kanban_agent",
      conversation_id: "kanban:other",
      thread_key: "thread-a",
      request_id: "REQ-OTHER",
      request_revision: 1,
      task_id: "TASK-OTHER",
      workflow_run_id: "wf-other",
      terminal_event_id: "evt-terminal-other",
      artifact_ref: "research/TASK-OTHER/result.md",
      artifact_digest: "b".repeat(64),
      summary: "Other conversation result.",
    }, "TASK-OTHER"),
    event(53, "workflow.result.available", {
      schema_version: "workflow-result.v1",
      result_kind: "research_report",
      status: "available",
      project_id: "proj-a",
      origin_surface: "channel",
      channel_id: "ch-other",
      thread_id: "thread-a",
      request_id: "REQ-CHANNEL",
      request_revision: 1,
      task_id: "TASK-CHANNEL",
      workflow_run_id: "wf-channel",
      terminal_event_id: "evt-terminal-channel",
      artifact_ref: "research/TASK-CHANNEL/result.md",
      artifact_digest: "c".repeat(64),
      summary: "Channel result.",
    }, "TASK-CHANNEL"),
  ],
  activeThreadId: "thread-a",
  conversationId: "kanban:proj-a",
  projectId: "proj-a",
});
const resultCards = resultConversation.threads
  .flatMap((thread) => thread.turns)
  .flatMap((turn) => turn.cards)
  .filter((card) => card.kind === "workflow-result");
assert(resultCards.length === 1, "Kanban result events fold into one result card");
assert(resultCards[0]?.status === "completed", "Kanban adoption completes the result card");
assert(
  (resultCards[0]?.payload?.adoptPayload as Record<string, unknown>)?.request_id === "REQ-1",
  "Kanban result card retains exact adoption lineage",
);
assert(
  resultCards[0]?.refs?.artifact_ref === "research/TASK-1/result.md"
  && resultCards[0]?.refs?.artifact_digest === "a".repeat(64),
  "Kanban result card exposes the immutable artifact ref and digest",
);

// A resumed Plan answer can make the provider reply part of the hidden answer
// turn while the durable Plan remains bound to the original owner message.
// Both events carry the same request_event_id; render the canonical request
// once instead of exposing two independently clickable cards across turns.
const resumedPlanRequest = {
  request_event_id: "evt-plan-next",
  request_id: "plan-next",
  revision: 1,
  header: "Channel setup",
  question_id: "depth",
  question: "Choose discussion depth",
  originating_message_event_id: "evt-60",
  turn_id: "turn-answer",
  options: [
    { id: "standard", label: "Standard", recommended: true },
    { id: "quick", label: "Quick" },
  ],
};
const resumedPlanConversation = buildKanbanConversation({
  events: [
    event(60, "user.message", {
      source: "kanban",
      target: "kanban-agent",
      runtime_delivery: "headless",
      project_id: "proj-a",
      conversation_id: "kanban:proj-a",
      thread_key: "thread-a",
      message: "Original requirement",
    }),
    event(61, "user.message", {
      source: "kanban",
      target: "kanban-agent",
      runtime_delivery: "headless",
      project_id: "proj-a",
      conversation_id: "kanban:proj-a",
      thread_key: "thread-a",
      message: "Plan answer",
      request: { plan_response: { request_event_id: "evt-plan-prior" } },
    }),
    event(62, "kanban.agent.turn.created", {
      backend: "codex-headless",
      project_id: "proj-a",
      conversation_id: "kanban:proj-a",
      thread_key: "thread-a",
      turn_id: "turn-answer",
      message_event_id: "evt-61",
    }),
    event(63, "kanban.agent.reply", {
      backend: "codex-headless",
      project_id: "proj-a",
      conversation_id: "kanban:proj-a",
      thread_key: "thread-a",
      turn_id: "turn-answer",
      answer: "Next Plan",
      plan_request: resumedPlanRequest,
    }),
    event(64, "kanban.agent.plan.requested", {
      backend: "codex-headless",
      project_id: "proj-a",
      conversation_id: "kanban:proj-a",
      thread_key: "thread-a",
      turn_id: "turn-answer",
      plan_request: resumedPlanRequest,
    }),
  ],
  activeThreadId: "thread-a",
  conversationId: "kanban:proj-a",
  projectId: "proj-a",
});
const resumedPlanCards = resumedPlanConversation.threads
  .flatMap((thread) => thread.turns)
  .flatMap((turn) => turn.cards)
  .filter((card) => (
    card.kind === "plan"
    && card.planRequest?.requestEventId === "evt-plan-next"
  ));
assert(
  resumedPlanCards.length === 1,
  `canonical resumed Plan should render once, got ${resumedPlanCards.length}`,
);

console.log("kanbanSessionEvents.test.ts OK");
