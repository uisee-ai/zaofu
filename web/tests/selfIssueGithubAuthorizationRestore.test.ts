import * as selfIssue from "../src/components/orchestrator/selfIssue.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

const restore = (selfIssue as unknown as Record<string, unknown>)[
  "selfIssueRestoreGithubAuthorization"
];
assert(
  typeof restore === "function",
  "a confirmed Draft must expose GitHub Device Flow restoration behavior",
);

const restored = (restore as (
  draft: Record<string, unknown>, transactionId: string,
) => Record<string, unknown>)({
  draft_id: "sid-1",
  publication_state: "confirmed",
  publication_batch: {
    status: "confirmed",
    confirmation_id: "confirm-1",
    publication_mode: "github",
  },
}, "ghd-1");

assert(restored.transaction_id === "ghd-1", "the saved transaction id must be restored");
assert(
  restored.status === "authorization_pending",
  "restored authorization must restart polling after a Draft refresh",
);

const shouldForget = (selfIssue as unknown as Record<string, unknown>)[
  "selfIssueGithubAuthorizationShouldForget"
];
assert(typeof shouldForget === "function", "terminal Device Flow results must be recognized");
assert(
  (shouldForget as (status: string) => boolean)("expired_token"),
  "an expired transaction must not be restored forever",
);
assert(
  !(shouldForget as (status: string) => boolean)("authorization_pending"),
  "an authorization still pending must continue polling",
);
assert(
  !(shouldForget as (status: string) => boolean)("slow_down"),
  "a throttled authorization poll must keep its transaction",
);
assert(
  (shouldForget as (status: string) => boolean)("partially_published"),
  "a completed authorization must be forgotten even when media recovery remains",
);

const pollCanStart = (selfIssue as unknown as Record<string, unknown>)[
  "selfIssueGithubAuthorizationPollCanStart"
];
assert(
  typeof pollCanStart === "function",
  "GitHub authorization polling must expose an in-flight transaction guard",
);
assert(
  !(pollCanStart as (transactionId: string, inFlightTransactionId: string) => boolean)(
    "ghd-1", "ghd-1",
  ),
  "the same GitHub transaction must not be polled concurrently",
);
assert(
  (pollCanStart as (transactionId: string, inFlightTransactionId: string) => boolean)(
    "ghd-2", "ghd-1",
  ),
  "a different GitHub transaction may start after the card changes",
);

const pollShouldReset = (selfIssue as unknown as Record<string, unknown>)[
  "selfIssueGithubAuthorizationPollShouldReset"
];
assert(
  typeof pollShouldReset === "function",
  "a stale or consumed GitHub transaction must expose recoverable reset behavior",
);
assert(
  (pollShouldReset as (status: string, reason: string) => boolean)(
    "invalid_self_issue_request",
    "GitHub Device Flow transaction is invalid, consumed, or session-bound",
  ),
  "a consumed transaction must be cleared instead of surfacing a terminal warning",
);
assert(
  !(pollShouldReset as (status: string, reason: string) => boolean)(
    "authorization_pending", "",
  ),
  "a pending transaction must continue polling",
);
