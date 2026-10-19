import {
  planDiscussionBackend,
  profileRegistrationContinuation,
  shouldRefreshChannelsAfterAction,
} from "../src/app/kanbanAgentInteractionPolicy.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

assert(
  shouldRefreshChannelsAfterAction("channel-delete", { ok: false }),
  "direct Channel actions must preserve the existing refresh behavior",
);
assert(
  shouldRefreshChannelsAfterAction("kanban-plan-apply", {
    ok: true,
    applied_action: "channel-create-and-start",
    channel_id: "ch-created",
  }),
  "a successful Channel Plan Apply must refresh the Channel projection",
);
assert(
  !shouldRefreshChannelsAfterAction("kanban-plan-apply", {
    ok: true,
    applied_action: "workflow-start",
  }),
  "a Workflow Plan Apply must not trigger a Channel fetch",
);
assert(
  !shouldRefreshChannelsAfterAction("kanban-plan-apply", {
    ok: false,
    applied_action: "channel-create-and-start",
  }),
  "a rejected Channel Plan Apply must not claim a successful refresh",
);

assert(
  planDiscussionBackend("codex-headless", "claude-headless") === "codex-headless",
  "Plan discussion must prefer the backend bound to the Plan",
);
assert(
  planDiscussionBackend("claude-headless", "codex-headless") === "claude-headless",
  "a stale current selection must not override the Plan backend",
);
assert(
  planDiscussionBackend("", "codex-headless") === "codex-headless",
  "legacy Plans without backend metadata must use the current selection",
);

console.log("kanbanAgentInteractionPolicy tests passed");

const continuation = { request_event_id: "evt-plan", request_id: "plan", revision: 2 };
assert(profileRegistrationContinuation("channel-profile-register", { plan_continuation: continuation })?.revision === 2,
  "successful prerequisite registration resumes the bound setup");
assert(profileRegistrationContinuation("channel-profile-register", {}) === null, "standalone registration does not invent a setup");
assert(profileRegistrationContinuation("create-task", { plan_continuation: continuation }) === null, "other approvals do not start Channel setup");
