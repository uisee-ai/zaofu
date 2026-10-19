// Dev-only fixture page for visually verifying AgentSessionTimeline render
// states without a live backend. Reachable at `/?fixture=agent-session`
// (wired in main.tsx). Doubles as a deterministic playwright visual-regression
// target. Not linked from the app UI; harmless if hit in prod.

import { useState } from "react";
import { AgentSessionTimeline } from "./AgentSessionTimeline";
import { PlanInteractionForm } from "./AgentInteractionControls";
import { ComposerSubmitButton } from "./ComposerSubmitButton";
import { DuringRunComposerActions } from "./DuringRunComposerActions";
import { WebChatInteractionCards } from "../webchat/WebChatInteractionCards";
import { WebChatInteractionPanel } from "../webchat/WebChatInteractionPanel";
import { WebChatPendingControls } from "../webchat/WebChatPendingControls";
import {
  emptyInteractionDraft,
  interactionKey,
  type WebChatInteractionDraft,
} from "../webchat/webChatInteractionModel";
import type {
  AgentConversation,
  AgentSessionPart,
  AgentSessionPlanRequest,
  AgentSessionPlanResponse,
  AgentSessionRun,
  AgentSessionStatus,
  AgentSessionThread,
  AgentSessionTurn,
} from "./types";
import type { ComposerStatus } from "./workState";
import type {
  WebChatControlReceipt,
  WebChatInteraction,
  WebChatQueueItem,
  WebChatTurn,
} from "../../api/types";

let seq = 0;
function part(kind: AgentSessionPart["kind"], state: AgentSessionStatus, title: string, extra: Partial<AgentSessionPart> = {}): AgentSessionPart {
  seq += 1;
  return { id: `p${seq}`, runId: "r1", kind, state, title, ...extra };
}

const LONG_OUTPUT = Array.from({ length: 140 }, (_, i) => `  ${String(i).padStart(3, "0")}  log line with some detail about step ${i}`).join("\n");
const JSON_REPLY = '{"status":"healthy","details":{"surface":"shared","items":[1,2]}}';

// Streaming run: 6 tools (older fold into "See N steps", last 3 are the live
// tail) + a running tool + a thinking part.
const streamingRun: AgentSessionRun = {
  id: "r1", threadId: "main", provider: "claude-headless", memberId: "dev", status: "streaming",
  startedAt: new Date(Date.now() - 42_000).toISOString(),
  parts: [
    part("tool", "completed", "Tool read", { summary: "src/app/App.tsx", toolName: "read", startedAt: new Date(Date.now() - 40_000).toISOString(), updatedAt: new Date(Date.now() - 39_200).toISOString() }),
    part("tool", "completed", "Tool grep", { summary: "rg streaming", toolName: "grep", startedAt: new Date(Date.now() - 39_000).toISOString(), updatedAt: new Date(Date.now() - 38_800).toISOString() }),
    part("tool", "completed", "Tool edit", { summary: "ToolCard.tsx", toolName: "edit", startedAt: new Date(Date.now() - 38_000).toISOString(), updatedAt: new Date(Date.now() - 35_000).toISOString(), sourceEvent: { seq: 1, id: "e-edit", type: "agent.ui.delta", payload: { tool: "edit", args: { path: "src/ToolCard.tsx", old_string: "foo", new_string: "bar" } } } }),
    part("command", "completed", "Tool bash", { summary: "npm run build", content: LONG_OUTPUT, toolName: "bash", startedAt: new Date(Date.now() - 34_000).toISOString(), updatedAt: new Date(Date.now() - 29_500).toISOString() }),
    part("tool", "completed", "Tool read", { summary: "package.json", toolName: "read", startedAt: new Date(Date.now() - 29_000).toISOString(), updatedAt: new Date(Date.now() - 28_900).toISOString() }),
    part("tool", "streaming", "Tool web_search", { summary: "react streaming patterns", toolName: "web_search", startedAt: new Date(Date.now() - 6_000).toISOString() }),
  ],
};

// Completed run: thinking + long command output (exercises preview/gradient) +
// a reply.
const completedRun: AgentSessionRun = {
  id: "r2", threadId: "main", provider: "claude-headless", memberId: "dev", status: "completed",
  startedAt: new Date(Date.now() - 120_000).toISOString(),
  parts: [
    // Repro: projection emits a "status-started" placeholder with state
    // "submitted" + startedAt; finalize doesn't resolve "submitted", so on a
    // DONE run it lingers — a status part must NOT show a ticking tool timer.
    part("status", "submitted", "Started", { summary: "claude-headless", startedAt: new Date(Date.now() - 119_500).toISOString() }),
    part("thinking", "completed", "Thought", { content: "Plan: read the file, run the suite, summarize failures.", startedAt: new Date(Date.now() - 119_000).toISOString() }),
    part("test_result", "failed", "Tool pytest", { summary: "2 failed, 18 passed", content: "FAILED tests/test_a.py::test_x\nFAILED tests/test_b.py::test_y\n" + LONG_OUTPUT, toolName: "pytest", startedAt: new Date(Date.now() - 118_000).toISOString(), updatedAt: new Date(Date.now() - 110_000).toISOString() }),
    part("tool", "cancelled", "Tool deploy", { summary: "cancelled before output", toolName: "deploy", startedAt: new Date(Date.now() - 109_000).toISOString(), updatedAt: new Date(Date.now() - 108_000).toISOString() }),
    part("code_preview", "completed", "src/throttle.ts", { summary: "new helper", content: "```ts\nexport function throttle(ms: number) {\n  return (fn: () => void) => fn();\n}\n```" }),
    part("diff_preview", "completed", "src/RichMarkdownText.tsx", { summary: "+3 −1", content: "```diff\n- const text = content;\n+ const text = isStreaming ? throttled : content;\n```" }),
    part("text", "completed", "Reply", { content: "Done — the build passed but **2 tests fail** in `tests/test_a.py`.\n\nThe failures look related to the streaming refactor:\n\n- `test_x` — expects the old chunked payload shape\n- `test_y` — timing assertion now races the throttle\n\nWant me to fix them, or just update the assertions?" }),
    part("text", "completed", "JSON reply", { content: JSON_REPLY }),
  ],
};

const turn: AgentSessionTurn = {
  id: "t1", threadId: "main", ts: new Date(Date.now() - 130_000).toISOString(),
  user: { id: "u1", role: "user", label: "You", content: "Build the project and report **failures** in `tests/`.", ts: new Date(Date.now() - 130_000).toISOString() },
  runs: [completedRun, streamingRun],
  cards: [{
    id: "contribution-1",
    kind: "contribution",
    title: "Structured analysis",
    body: "Use one controlled gateway and keep the state authority explicit.",
    status: "completed",
    runId: "r2",
    payload: {
      findings: [
        { id: "f1", label: "finding", text: "One authority owns state." },
        { id: "f2", label: "fact", text: "Events preserve occurrence order." },
        { id: "f3", text: "Artifacts retain complete semantic bodies." },
        { id: "f4", text: "Web remains a read-oriented projection." },
      ],
      risks: [
        { id: "r2", label: "p2", text: "Low-priority display drift." },
        { id: "r1", label: "p0", text: "Dual writes make replay diverge." },
        { id: "r3", label: "p1", text: "A stale projection hides owner input." },
      ],
      contradictions: [
        { id: "c1", text: "Two documents claim canonical authority." },
        { id: "c2", text: "A UI phase label conflicts with runtime state." },
      ],
      questions: [
        { id: "q1", label: "scope", text: "Which vertical slice ships first?" },
        { id: "q2", text: "Who owns the verification decision?" },
      ],
      source_refs: ["SPEC.md", "tasks/plan.md"],
      evidence_refs: ["trace:fixture", "test:fixture"],
      artifact_ref: "channels/ch-fixture/contracts/contribution/reply.json",
      artifact_digest: "fixture-digest",
    },
    refs: {
      artifact_ref: "channels/ch-fixture/contracts/contribution/reply.json",
      artifact_digest: "fixture-digest",
    },
  }],
};

const thread: AgentSessionThread = {
  id: "main", title: "main", status: "streaming", unseenCount: 0,
  turns: [turn], updatedAt: new Date().toISOString(),
};

const conversation: AgentConversation = {
  id: "fixture", surface: "kanban_agent", activeThreadId: "main", threads: [thread],
};

const preparingConversation: AgentConversation = {
  id: "fixture-preparing",
  surface: "kanban_agent",
  activeThreadId: "main",
  threads: [{
    id: "main",
    title: "main",
    status: "streaming",
    turns: [{
      id: "turn-preparing",
      threadId: "main",
      runs: [{
        id: "run-preparing",
        threadId: "main",
        provider: "codex-headless",
        status: "streaming",
        parts: [],
      }],
      cards: [{
        id: "control-pending-run-preparing",
        kind: "plan",
        title: "Preparing choices",
        body: "Validating the available options.",
        status: "submitted",
        runId: "run-preparing",
        threadId: "main",
        payload: { preparing: true, controlKind: "plan_request" },
      }],
    }],
  }],
};

// Same data as a channel_group surface — verifies channel activity now folds
// via the same "See N steps" segmenter (Phase 1 #2).
const channelConversation: AgentConversation = {
  id: "fixture-ch", surface: "channel_group", activeThreadId: "main",
  threads: [{ ...thread, id: "main" }],
};

const workflowRequestConversation: AgentConversation = {
  id: "workflow-request", surface: "kanban_agent", activeThreadId: "main",
  threads: [{ id: "main", title: "main", status: "idle", turns: [{
    id: "workflow-request-turn", threadId: "main", runs: [], cards: [{
      id: "workflow-receipt", kind: "approve", title: "Start workflow", status: "completed",
      proposal: { action: "workflow-start", requestedAction: "workflow-start", payload: {
        task_id: "TASK-FIXTURE", route_id: "prd:standard",
      }, reason: "Approved by Owner", valid: true },
      payload: { resolution: "executed", status: "pending_consumer" },
    }],
  }] }],
};

const COMPOSER_STATES: ComposerStatus[] = ["idle", "submitted", "streaming", "error"];

const webChatInteraction: WebChatInteraction = {
  interaction_id: "interaction-fixture",
  turn_id: "turn-fixture",
  kind: "ask_user",
  revision: 2,
  request_digest: "fixture-digest",
  request: {
    header: "Clarify delivery",
    questions: [{
      id: "route",
      header: "Delivery route",
      question: "How should this change be delivered?",
      options: [
        { id: "direct", label: "Direct change", description: "Keep the scope narrow." },
        { id: "workflow", label: "Workflow", description: "Track the work through delivery gates." },
      ],
      isOther: true,
    }, {
      id: "evidence",
      header: "Evidence",
      question: "Which evidence must be attached?",
      options: [
        { id: "tests", label: "Focused tests" },
        { id: "browser", label: "Browser verification" },
      ],
      multi_select: true,
      allow_other: false,
    }],
  },
  status: "pending",
  currentness: { turn_revision: 3 },
  created_at: "2026-10-01T12:00:00Z",
  updated_at: "2026-10-01T12:00:00Z",
};

const webChatTurn: WebChatTurn = {
  schema_version: "webchat-turn.v1",
  turn_id: "turn-fixture",
  project_id: "fixture",
  conversation_id: "fixture",
  thread_id: "main",
  backend: "codex-headless",
  permission_profile: "workspace_writer",
  state: "running",
  current_attempt_id: "attempt-fixture",
  created_at: "2026-10-01T12:00:00Z",
  updated_at: "2026-10-01T12:00:05Z",
};

const webChatQueueItem: WebChatQueueItem = {
  queue_id: "queue-fixture",
  idempotency_key: "queue-fixture-key",
  project_id: "fixture",
  conversation_id: "fixture",
  thread_id: "main",
  message: "Keep the public API stable",
  request: { fallback_from: "steer" },
  state: "queued",
  source_turn_id: "turn-fixture",
  created_at: "2026-10-01T12:00:10Z",
  updated_at: "2026-10-01T12:00:10Z",
};

const webChatReceipts: WebChatControlReceipt[] = [{
  receipt_id: "receipt-steer-fixture",
  idempotency_key: "steer-fixture-key",
  operation: "steer",
  turn_id: "turn-fixture",
  attempt_id: "attempt-fixture",
  status: "queued_fallback",
  fallback: "queue",
  payload: { queue_id: "queue-fixture" },
  created_at: "2026-10-01T12:00:10Z",
}, {
  receipt_id: "receipt-interrupt-fixture",
  idempotency_key: "interrupt-fixture-key",
  operation: "interrupt",
  turn_id: "turn-fixture",
  attempt_id: "attempt-fixture",
  status: "accepted",
  created_at: "2026-10-01T12:00:12Z",
}];

function fixturePlan(
  subjectType: "task_create" | "task_workflow",
): AgentSessionPlanRequest {
  const taskCreate = subjectType === "task_create";
  return {
    requestEventId: `evt-${subjectType}`,
    requestId: `request-${subjectType}`,
    revision: 1,
    header: taskCreate ? "Create Task from PRD" : "Plan workflow",
    subjectType,
    questionId: `question-${subjectType}`,
    question: taskCreate
      ? "How should the canonical PRD become a Task?"
      : "Which workflow route should execute the Task?",
    options: taskCreate ? [
      {
        id: "full-task",
        label: "Full delivery Task (Recommended)",
        recommended: true,
        submitAction: "create-task",
        submitMode: "propose",
      },
      {
        id: "focused-task",
        label: "Focused Task",
        submitAction: "create-task",
        submitMode: "propose",
      },
      {
        id: "task-later",
        label: "No Task yet",
        submitMode: "continue",
      },
    ] : [
      {
        id: "delivery",
        label: "Delivery workflow (Recommended)",
        recommended: true,
        submitAction: "workflow-start",
        submitMode: "propose",
      },
      {
        id: "research",
        label: "Research workflow",
        submitAction: "workflow-start",
        submitMode: "propose",
      },
      {
        id: "workflow-later",
        label: "No workflow yet",
        submitMode: "continue",
      },
    ],
    allowOther: false,
    questions: [],
    valid: true,
  };
}

function fixtureMultiPlan(): AgentSessionPlanRequest {
  const questions = [
    {
      id: "route",
      header: "Delivery route",
      question: "How should the work be delivered?",
      options: [
        { id: "direct", label: "Direct (Recommended)", recommended: true },
        { id: "workflow", label: "Workflow" },
      ],
      allowOther: true,
    },
    {
      id: "evidence",
      header: "Evidence depth",
      question: "How much verification evidence is required?",
      options: [
        { id: "focused", label: "Focused (Recommended)", recommended: true },
        { id: "broad", label: "Broad" },
      ],
      allowOther: true,
    },
  ];
  return {
    ...fixturePlan("task_create"),
    requestEventId: "evt-multi-clarification",
    requestId: "request-multi-clarification",
    header: "Clarify delivery",
    subjectType: "clarification",
    questionId: questions[0].id,
    question: questions[0].question,
    options: questions[0].options,
    questions,
  };
}

function fixtureAdaptiveChannelPlan(): AgentSessionPlanRequest {
  return {
    requestEventId: "evt-adaptive-channel",
    requestId: "request-adaptive-channel",
    revision: 1,
    header: "Channel setup",
    subjectType: "channel_setup",
    questionId: "channel-template",
    question: "Which Channel template should start the discussion?",
    options: [{
      id: "prd-clarification",
      label: "PRD clarification (Recommended)",
      recommended: true,
      submitAction: "channel-create-and-start",
      submitMode: "apply",
      submitDetails: {
        templateId: "prd-clarification",
        memberCount: 4,
        roles: ["product_pm", "arch", "critic", "synthesizer"],
        roundPolicy: "synthesis_adaptive",
        profiles: [{
          memberId: "arch",
          channelRole: "arch",
          profileId: "architecture-codex",
          displayName: "Architecture Codex",
        }],
        mode: "multi_lens",
        engineMode: "fanout_then_synthesis",
        routingStrategy: "blind_fanout_then_synthesis",
        firstPassReplyCount: 4,
      },
    }],
    allowOther: false,
    questions: [],
    valid: true,
  };
}

export function AgentSessionFixturePage() {
  const [interrupted, setInterrupted] = useState("");
  const [duringRunMessage, setDuringRunMessage] = useState("");
  const [duringRunAction, setDuringRunAction] = useState("");
  const [webChatQueue, setWebChatQueue] = useState<WebChatQueueItem[]>([webChatQueueItem]);
  const [webChatQueueBusy, setWebChatQueueBusy] = useState("");
  const [webChatQueueDraft, setWebChatQueueDraft] = useState("");
  const [webChatInteractionActive, setWebChatInteractionActive] = useState(true);
  const [webChatInteractionDraft, setWebChatInteractionDraft] = useState<WebChatInteractionDraft>(emptyInteractionDraft());
  const [webChatInteractionResponse, setWebChatInteractionResponse] = useState<Record<string, unknown> | null>(null);
  const [taskPlanResponse, setTaskPlanResponse] = useState<AgentSessionPlanResponse>();
  const [workflowPlanResponse, setWorkflowPlanResponse] = useState<AgentSessionPlanResponse>();
  const [multiPlanResponse, setMultiPlanResponse] = useState<AgentSessionPlanResponse>();
  const [adaptiveChannelPlanResponse, setAdaptiveChannelPlanResponse] = useState<AgentSessionPlanResponse>();
  const taskPlan = fixturePlan("task_create");
  const workflowPlan = fixturePlan("task_workflow");
  const multiPlan = fixtureMultiPlan();
  const adaptiveChannelPlan = fixtureAdaptiveChannelPlan();
  return (
    <div style={{ padding: 24, maxWidth: 980, margin: "0 auto" }}>
      <h2>AgentSessionTimeline fixture</h2>
      <p className="muted">Dev-only render harness — streaming run (fold + tail), completed run (long output / 4-state tools), thinking.</p>

      <h3 style={{ marginTop: 24 }}>ComposerSubmitButton — all states</h3>
      <div style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 8 }}>
        {COMPOSER_STATES.map((status) => (
          <span key={status} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <code>{status}</code>
            <ComposerSubmitButton
              className="headless-send-button"
              status={status}
              onStop={() => setInterrupted(`interrupted @ ${status}`)}
            />
          </span>
        ))}
      </div>
      {interrupted ? <p data-testid="interrupt-echo" className="muted">{interrupted}</p> : null}

      <h3 style={{ marginTop: 24 }}>During-run composer actions</h3>
      <div className="headless-composer" data-testid="fx-during-run-composer" style={{ maxWidth: 620 }}>
        <textarea
          aria-label="During-run fixture message"
          className="headless-input"
          placeholder="Add guidance while the agent is working"
          value={duringRunMessage}
          onChange={(event) => setDuringRunMessage(event.target.value)}
        />
        <div className="headless-composer-footer">
          <span style={{ flex: 1 }} />
          <DuringRunComposerActions
            canCancel
            canInterrupt
            canSteer={false}
            hasMessage={Boolean(duringRunMessage.trim())}
            primaryAction="queue"
            onCancel={() => setDuringRunAction("stop")}
            onQueue={() => {
              setDuringRunAction("queue");
              setDuringRunMessage("");
            }}
            onSteer={() => setDuringRunAction("steer")}
            onSteerSooner={() => {
              setDuringRunAction("steer-sooner");
              setDuringRunMessage("");
            }}
          />
        </div>
      </div>
      {duringRunAction ? <p className="muted" data-testid="during-run-action-echo">{duringRunAction}</p> : null}

      <h3 style={{ marginTop: 24 }}>Durable queue and control receipts</h3>
      <div className="headless-composer" data-testid="fx-webchat-pending" style={{ maxWidth: 620 }}>
        <WebChatPendingControls
          busyId={webChatQueueBusy}
          composerHasDraft={Boolean(webChatQueueDraft.trim())}
          queue={webChatQueue}
          receipts={webChatReceipts}
          turns={[webChatTurn]}
          onCancel={(item) => {
            setWebChatQueueBusy(item.queue_id);
            setWebChatQueue((current) => current.map((candidate) => (
              candidate.queue_id === item.queue_id ? { ...candidate, state: "cancelled" } : candidate
            )));
            setWebChatQueueBusy("");
          }}
          onRestore={(item) => {
            setWebChatQueueBusy(item.queue_id);
            setWebChatQueueDraft(item.message);
            setWebChatQueue((current) => current.map((candidate) => (
              candidate.queue_id === item.queue_id ? { ...candidate, state: "cancelled" } : candidate
            )));
            setWebChatQueueBusy("");
          }}
        />
        <textarea
          aria-label="Restored queued message"
          className="headless-input"
          placeholder="Queued guidance returns here"
          value={webChatQueueDraft}
          onChange={(event) => setWebChatQueueDraft(event.target.value)}
        />
      </div>

      <h3 style={{ marginTop: 24 }}>Durable Ask User interaction</h3>
      <div data-testid="fx-webchat-interaction-thread">
        <WebChatInteractionCards
          activeKey={webChatInteractionActive ? interactionKey(webChatInteraction) : ""}
          drafts={{ [interactionKey(webChatInteraction)]: webChatInteractionDraft }}
          errors={{}}
          interactions={webChatInteractionResponse ? [] : [webChatInteraction]}
          onCompose={() => setWebChatInteractionActive(true)}
          onDraftChange={(_, draft) => setWebChatInteractionDraft(draft)}
          onRespond={(_, response) => {
            setWebChatInteractionResponse(response);
            setWebChatInteractionActive(false);
          }}
        />
      </div>
      <div className="headless-composer" data-testid="fx-webchat-interaction" style={{ maxWidth: 620, marginTop: 8 }}>
        {webChatInteractionActive && !webChatInteractionResponse ? (
          <WebChatInteractionPanel
            draft={webChatInteractionDraft}
            interaction={webChatInteraction}
            mode="composer"
            onCollapse={() => setWebChatInteractionActive(false)}
            onDraftChange={setWebChatInteractionDraft}
            onRespond={(response) => {
              setWebChatInteractionResponse(response);
              setWebChatInteractionActive(false);
            }}
          />
        ) : null}
      </div>
      {webChatInteractionResponse ? (
        <pre data-testid="webchat-interaction-response">{JSON.stringify(webChatInteractionResponse)}</pre>
      ) : null}

      <h3 style={{ marginTop: 24 }}>Plan options - Task creation</h3>
      <div data-testid="fx-task-plan-options">
        <PlanInteractionForm
          request={{ ...taskPlan, response: taskPlanResponse }}
          onSubmit={setTaskPlanResponse}
        />
      </div>

      <h3 style={{ marginTop: 24 }}>Plan options - Workflow ignition</h3>
      <div data-testid="fx-workflow-plan-options">
        <PlanInteractionForm
          request={{ ...workflowPlan, response: workflowPlanResponse }}
          onSubmit={setWorkflowPlanResponse}
        />
      </div>

      <h3 style={{ marginTop: 24 }}>Plan options - Multiple questions</h3>
      <div data-testid="fx-multi-plan-options">
        <PlanInteractionForm
          request={{ ...multiPlan, response: multiPlanResponse }}
          onChatAbout={() => setInterrupted("plan discussion")}
          onSubmit={setMultiPlanResponse}
        />
      </div>

      <h3 style={{ marginTop: 24 }}>Plan options - Adaptive Channel discussion</h3>
      <div data-testid="fx-adaptive-channel-plan-options">
        <PlanInteractionForm
          request={{ ...adaptiveChannelPlan, response: adaptiveChannelPlanResponse }}
          onSubmit={setAdaptiveChannelPlanResponse}
        />
      </div>

      <h3 style={{ marginTop: 24 }}>Timeline — kanban_agent(orchestrator 同款 props)</h3>
      <div data-testid="fx-kanban-compact">
        <AgentSessionTimeline
          activeThreadId="main"
          conversation={conversation}
          compact
          compactRunHeader
          collapseCompletedRunDetails
          minimalRunActivity
          showRunDetails={false}
          showRunProvider={false}
        />
      </div>

      <h3 style={{ marginTop: 24 }}>Preparing choices — control JSON hidden</h3>
      <div data-testid="fx-workflow-request">
        <AgentSessionTimeline activeThreadId="main" conversation={workflowRequestConversation} compact />
      </div>
      <div data-testid="fx-kanban-preparing">
        <AgentSessionTimeline
          activeThreadId="main"
          conversation={preparingConversation}
          compact
          compactRunHeader
          minimalRunActivity
          showRunDetails={false}
          showRunProvider={false}
        />
      </div>

      <h3 style={{ marginTop: 24 }}>Timeline — channel_group(折叠 + 居中列)</h3>
      <div className="channel-page-chat" data-testid="fx-channel-compact">
        <AgentSessionTimeline activeThreadId="main" compact conversation={channelConversation} collapseCompletedRunDetails minimalRunActivity />
      </div>

      <h3 style={{ marginTop: 24 }}>Timeline — kanban fullscreen(点 preview ⤢ → 分屏预览)</h3>
      <div data-testid="fx-kanban-fs" style={{ height: 420, display: "flex" }}>
        <AgentSessionTimeline
          activeThreadId="main"
          conversation={conversation}
          allowPreviewSplit
          collapseCompletedRunDetails
          minimalRunActivity
          showRunDetails={false}
          showRunProvider={false}
        />
      </div>

      <h3 style={{ marginTop: 24 }}>Timeline — channel fullscreen(分屏预览同样支持)</h3>
      <div data-testid="fx-channel-fs" className="channel-page-chat" style={{ height: 420, display: "flex" }}>
        <AgentSessionTimeline
          activeThreadId="main"
          conversation={channelConversation}
          allowPreviewSplit
          collapseCompletedRunDetails
          minimalRunActivity
        />
      </div>
    </div>
  );
}
