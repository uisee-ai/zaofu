const FAST_SELF_ISSUE_ACTIONS = new Set([
  "self-issue-source-get", "self-issue-source-inspect", "self-issue-source-save",
  "self-issue-get",
  "self-issue-intake-get",
  "self-issue-session-get",
  "self-issue-session-activity-result",
  "self-issue-session-draft-version",
  "self-issue-intake-save",
  "self-issue-intake-submit",
  "self-issue-intake-dismiss",
  "self-issue-intake-attachment-remove",
  "self-issue-evidence-interrupt",
  "self-issue-evidence-resume",
  "self-issue-runtime-check",
  "self-issue-limited-continue",
]);

export function actionRequestDeadlineMs(action: string): number {
  if (action === "self-issue-dismiss") return 60_000;
  return FAST_SELF_ISSUE_ACTIONS.has(action) ? 10_000 : 0;
}

export function actionEventRefreshRunsInBackground(action: string): boolean {
  return action.startsWith("self-issue-");
}

export function actionIsReadOnlySelfIssue(action: string): boolean {
  return ["self-issue-get", "self-issue-intake-get", "self-issue-session-get",
    "self-issue-source-get", "self-issue-source-inspect",
    "self-issue-session-activity-result", "self-issue-session-draft-version"].includes(action);
}
