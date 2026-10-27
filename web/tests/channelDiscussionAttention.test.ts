import type {
  ChannelDetail,
  ChannelDiscussionAttention,
} from "../src/api/types.js";
import {
  channelDiscussionControlPolicy,
  presentChannelDiscussionAttention,
  selectChannelDiscussionAttention,
  selectDiscussionStartSource,
} from "../src/components/channel/channelDiscussionAttention.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function attention(
  state: ChannelDiscussionAttention["state"],
  overrides: Partial<ChannelDiscussionAttention> = {},
): ChannelDiscussionAttention {
  return {
    schema_version: "channel.discussion-attention.v2",
    is_derived_projection: true,
    thread_id: "main",
    state,
    reason: "test",
    next_action: "view_activity",
    kernel_phase: "phase2_relay",
    last_outcome: "",
    participant_count: 4,
    active_agent_count: 0,
    active_reply_count: 0,
    queued_reply_count: 0,
    running_reply_count: 0,
    completed_reply_count: 4,
    failed_reply_count: 0,
    open_question_count: 0,
    owner_question_count: 0,
    total_question_count: 0,
    resolved_question_count: 0,
    last_activity_at: "2026-08-18T00:00:00Z",
    can_drain_replies: false,
    can_synthesize: false,
    can_restart: false,
    can_review_questions: false,
    can_review_result: false,
    can_view_activity: true,
    ...overrides,
  };
}

const requirementMessages = [
  {message_id: "original", thread_id: "main", role: "user", text: "Full PRD", refs: {evaluation_source_document: true}},
  {message_id: "review", thread_id: "main", role: "user", text: "@qa Review this draft", refs: {consensus_review_id: "creview"}},
  {message_id: "restart", thread_id: "main", role: "user", text: "@qa Review this draft", refs: {source_requirement_message_id: "review"}},
];
assert(
  selectDiscussionStartSource(requirementMessages, "main", true, "restart")?.message_id === "original",
  "restart binds the marked source document instead of an operator review prompt",
);
assert(
  selectDiscussionStartSource(requirementMessages, "main", false, "")?.message_id === "original",
  "new discussion ignores generated review prompts",
);
const ordinaryChat = [
  {message_id: "first", thread_id: "main", role: "user", text: "Original requirement"},
  {message_id: "extra", thread_id: "main", role: "user", text: "Also support undo"},
  {message_id: "other", thread_id: "other", role: "user", text: "Other thread"},
];
assert(selectDiscussionStartSource(ordinaryChat, "main", true, "first")?.message_id === "first",
  "restart keeps the original anchor without requiring a source flag");

const running = attention("running", {
  execution_state: "running",
  attention_kind: "none",
  active_agent_count: 2,
  active_reply_count: 2,
  can_drain_replies: true,
});
const runningView = presentChannelDiscussionAttention(running);
assert(runningView.label === "2 agents responding", "running state leads with real active work");
assert(runningView.summary === "", "running state does not repeat an abstract status label");
assert(runningView.action === "activity", "running opens activity details");
assert(runningView.actionLabel === "", "running state has no redundant activity button");

const needsInput = attention("needs_input", {
  execution_state: "running",
  attention_kind: "question",
  blocks_transition: "synthesis",
  active_agent_count: 2,
  active_reply_count: 2,
  owner_question_count: 3,
  open_question_count: 3,
  can_review_questions: true,
});
const needsInputView = presentChannelDiscussionAttention(needsInput);
assert(needsInputView.label === "2 agents responding", "active work remains the primary state");
assert(needsInputView.action === "questions", "owner questions open the decision shelf");
assert(needsInputView.summary.includes("3 decisions pending"), "decision count is explicit");
assert(needsInputView.summary.includes("blocks synthesis"), "the blocked transition is explicit");

const oneDecisionView = presentChannelDiscussionAttention({
  ...needsInput,
  owner_question_count: 1,
  open_question_count: 1,
});
assert(
  oneDecisionView.summary === "1 decision pending · blocks synthesis",
  "single-decision copy uses singular grammar",
);

const waiting = attention("needs_input", {
  execution_state: "ready",
  attention_kind: "question",
  blocks_transition: "synthesis",
  owner_question_count: 2,
  open_question_count: 2,
  can_review_questions: true,
});
const waitingView = presentChannelDiscussionAttention(waiting);
assert(waitingView.label === "Waiting for you", "idle owner gate becomes explicit waiting state");
assert(waitingView.actionLabel === "Answer 2", "waiting state presents a bounded decision action");

const ready = attention("ready", { can_synthesize: true });
const readyView = presentChannelDiscussionAttention(ready);
assert(readyView.label === "Ready", "ready state does not expose the kernel phase");
assert(readyView.action === "synthesize", "ready state offers synthesis");

const blocked = attention("blocked", {
  failed_reply_count: 1,
  can_restart: true,
});
const blockedView = presentChannelDiscussionAttention(blocked);
assert(blockedView.label === "Blocked", "failed discussion is blocked");
assert(blockedView.summary.includes("1 reply failure"), "failure count is readable");

const stalledReviewView = presentChannelDiscussionAttention(attention("blocked", {
  reason: "discussion_stalled",
  execution_state: "blocked",
  attention_kind: "none",
  last_outcome: "stalled",
}));
assert(stalledReviewView.label === "PRD review blocked", "revision stall is not an owner decision");
assert(stalledReviewView.summary.includes("diagnosis"), "stalled review asks for diagnosis");

const done = attention("done");
assert(!presentChannelDiscussionAttention(done).visible, "completed discussion leaves the main chat quiet");

const detail = {
  members: [],
  workflow_requests: [],
  discussion_attention: {
    main: done,
    feature: { ...needsInput, thread_id: "feature" },
  },
} as unknown as ChannelDetail;
assert(
  selectChannelDiscussionAttention(detail, "feature")?.thread_id === "feature",
  "selected actionable thread wins",
);
assert(
  selectChannelDiscussionAttention(detail, "main")?.thread_id === "main",
  "another thread cannot replace the selected conversation status",
);

const foldedPreferred = {
  members: [],
  workflow_requests: [],
  discussion_attention: {
    main: attention("running", { thread_id: "main", owner_question_count: 0 }),
    "kanban:newloshu": attention("needs_input", {
      thread_id: "kanban:newloshu",
      attention_kind: "question",
      execution_state: "ready",
      owner_question_count: 2,
      open_question_count: 2,
    }),
  },
} as unknown as ChannelDetail;
assert(
  selectChannelDiscussionAttention(foldedPreferred, "main")?.thread_id === "main",
  "transport and main threads have independent decision state",
);

const runningPolicy = channelDiscussionControlPolicy(running, "phase2_relay", true);
assert(!runningPolicy.canStartOrRestart, "running work cannot be restarted");
assert(runningPolicy.canDrainReplies, "active replies can be drained");
assert(!runningPolicy.canSynthesize, "running work cannot be synthesized early");

const readyPolicy = channelDiscussionControlPolicy(ready, "phase2_relay", true);
assert(readyPolicy.canSynthesize, "ready discussion can synthesize");
assert(!readyPolicy.canStartOrRestart, "ready discussion cannot restart");

const blockedPolicy = channelDiscussionControlPolicy(blocked, "phase2_relay", true);
assert(blockedPolicy.canStartOrRestart, "blocked discussion can restart");
assert(blockedPolicy.restart, "blocked discussion sends explicit restart intent");
assert(blockedPolicy.startLabel === "Restart discussion", "blocked active session uses restart copy");
const closedStalledPolicy = channelDiscussionControlPolicy(blocked, "idle", true);
assert(closedStalledPolicy.canStartOrRestart, "closed stalled discussion can restart");
assert(closedStalledPolicy.restart, "closed stalled session keeps restart intent after kernel returns idle");
assert(closedStalledPolicy.startLabel === "Restart discussion", "closed stalled session keeps restart copy");

const idlePolicy = channelDiscussionControlPolicy(null, "idle", true);
assert(idlePolicy.canStartOrRestart, "idle thread with a requirement can start");
assert(!idlePolicy.restart, "fresh idle discussion does not send restart intent");
assert(idlePolicy.startLabel === "Start discussion", "idle thread uses start copy");

console.log("channelDiscussionAttention.test.ts OK");

assert(selectChannelDiscussionAttention(detail, "new-thread") === null, "new threads do not inherit another conversation status");

const missingContribution = presentChannelDiscussionAttention(attention("blocked", {
  reason: "contribution_missing", missing_frozen_members: ["dba"], can_restart: true,
}));
assert(missingContribution.summary.includes("dba") && missingContribution.action === "restart",
  "missing role has an actionable recovery without bypassing the contribution gate");

const rejectedRound = presentChannelDiscussionAttention(attention("blocked", {
  reason: "next_round_rejected", next_round_rejection_reason: "next_round_targets_not_in_current_roster",
}));
assert(rejectedRound.label.includes("PRD 已生成"), "a rejected next round must retain the existing PRD");
assert(rejectedRound.summary.includes("冻结名单"), "explain the actual roster rejection");
assert(rejectedRound.action === "restart", "roster recovery uses an explicit restart");
const pendingReview = presentChannelDiscussionAttention(attention("blocked", {reason: "synthesis_review_pending"}));
assert(pendingReview.label.includes("评审尚未启动"), "do not describe a present PRD as missing");
assert(pendingReview.action === "result", "keep the PRD reviewable");
