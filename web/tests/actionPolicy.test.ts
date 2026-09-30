import {
  actionEventRefreshRunsInBackground,
  actionRequestDeadlineMs,
  actionIsReadOnlySelfIssue,
} from "../src/api/actionPolicy.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

for (const action of ["self-issue-get", "self-issue-intake-get", "self-issue-session-get",
  "self-issue-session-activity-result", "self-issue-session-draft-version"]) {
  assert(actionIsReadOnlySelfIssue(action), `${action} must not trigger whole-page refresh`);
  assert(actionRequestDeadlineMs(action) === 10_000, `${action} must have a bounded read timeout`);
}
assert(!actionIsReadOnlySelfIssue("self-issue-capture"), "capture is a mutation");
assert(!actionIsReadOnlySelfIssue("self-issue-session-save-draft"), "saving a draft is a mutation");

assert(
  actionRequestDeadlineMs("self-issue-get") === 10_000,
  "Draft reload must stop waiting when the Web/API is unavailable",
);
assert(
  actionRequestDeadlineMs("self-issue-dismiss") === 60_000,
  "complete Draft and sidecar deletion needs a bounded long-running deadline",
);
assert(
  actionRequestDeadlineMs("self-issue-intake-submit") === 10_000,
  "Intake submission must not spin indefinitely when the Web/API is unavailable",
);
assert(
  actionRequestDeadlineMs("self-issue-evidence-interrupt") === 10_000,
  "evidence interruption must have a bounded request lifetime",
);
assert(
  actionRequestDeadlineMs("self-issue-evidence-resume") === 10_000,
  "evidence resume must have a bounded request lifetime",
);
assert(
  actionRequestDeadlineMs("self-issue-runtime-check") === 10_000,
  "runtime re-check must not wait indefinitely when the Web/API is unavailable",
);
assert(
  actionRequestDeadlineMs("self-issue-limited-continue") === 10_000,
  "limited-report continuation must not wait indefinitely when the Web/API is unavailable",
);
assert(
  actionRequestDeadlineMs("self-issue-publish") === 0,
  "provider publication must retain its provider-specific request lifetime",
);
assert(
  actionEventRefreshRunsInBackground("self-issue-dismiss"),
  "Self-Issue controls must not wait for the event-list fallback refresh",
);
assert(
  !actionEventRefreshRunsInBackground("update-task"),
  "unrelated action feedback behavior must stay unchanged",
);
