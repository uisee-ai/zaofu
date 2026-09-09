import {
  channelNeedsLiveConvergence,
} from "../src/app/channelLiveRefreshPolicy.js";
import type { ChannelDetail } from "../src/api/types.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

const settledAttention = {
  schema_version: "channel.discussion-attention.v2",
  is_derived_projection: true,
  thread_id: "main",
  state: "needs_input",
  reason: "owner_decision_required",
  next_action: "review_questions",
  execution_state: "ready",
  attention_kind: "question",
  kernel_phase: "owner_review",
  last_outcome: "",
  participant_count: 2,
  active_agent_count: 0,
  active_reply_count: 0,
  queued_reply_count: 0,
  running_reply_count: 0,
  completed_reply_count: 2,
  failed_reply_count: 0,
  open_question_count: 1,
  owner_question_count: 1,
  total_question_count: 1,
  resolved_question_count: 0,
  last_activity_at: "2026-09-03T00:00:00Z",
  can_drain_replies: false,
  can_synthesize: false,
  can_restart: false,
  can_review_questions: true,
  can_review_result: false,
  can_view_activity: true,
} satisfies NonNullable<ChannelDetail["discussion_attention"]>[string];

const settled = {
  channel_id: "ch-live",
  members: [],
  workflow_requests: [],
  discussion_attention: { main: settledAttention },
} as ChannelDetail;

assert(
  !channelNeedsLiveConvergence(null),
  "an absent Channel projection must not start a convergence poll",
);
assert(
  !channelNeedsLiveConvergence(settled),
  "settled owner input must not become a permanent poll",
);
assert(
  channelNeedsLiveConvergence({
    ...settled,
    pending_reply_count: 1,
  }),
  "pending replies need convergence",
);
assert(
  channelNeedsLiveConvergence({
    ...settled,
    queued_replies: [{ request_id: "reply-1" }],
  }),
  "queued reply rows need convergence even before a provider emits status",
);
assert(
  channelNeedsLiveConvergence({
    ...settled,
    provider_runs: [{ run_id: "provider-1", status: "running" }],
  }),
  "running provider work needs convergence",
);
assert(
  channelNeedsLiveConvergence({
    ...settled,
    discussion_attention: {
      main: {
        ...settledAttention,
        state: "running",
        execution_state: "running",
        active_agent_count: 1,
        attention_kind: "consolidating",
      },
    },
  }),
  "question consolidation needs convergence until its terminal projection arrives",
);
assert(
  channelNeedsLiveConvergence({
    ...settled,
    discussions: { main: { status: "synthesizing" } },
  }),
  "an active discussion state needs convergence when attention has not arrived yet",
);

console.log("channelLiveRefreshPolicy tests passed");
