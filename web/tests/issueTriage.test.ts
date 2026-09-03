import {
  ISSUE_TRIAGE_POLL_INTERVAL_MS,
  ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS,
  ISSUE_STATE_FILTERS,
  canManageIssueRun,
  filterIssueStateOptions,
  issueLabelFoldCount,
  issueTriageNeedsRefresh,
  issueTriageSourceLabel,
  nextIssueLabelSelection,
  nextIssueStateSelectAll,
  nextIssueStateSelection,
} from "../src/pages/issueTriageModel.js";
import { snapshotLoadKindForPage } from "../src/app/pageLoadPolicy.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

const base = {
  status: "fresh",
  repository: "rzwu-uisee/zaofu-self-issue",
  repository_id: "123",
  last_attempt_at: "2026-08-25T00:00:00Z",
  last_success_at: "2026-08-25T00:00:00Z",
  rate_limit_remaining: 59,
  rate_limit_reset_at: "",
  error: "",
};

assert(!issueTriageNeedsRefresh(base, Date.parse(base.last_success_at) + ISSUE_TRIAGE_POLL_INTERVAL_MS - 1), "a fresh mirror must not poll early");
assert(issueTriageNeedsRefresh(base, Date.parse(base.last_success_at) + ISSUE_TRIAGE_POLL_INTERVAL_MS), "a stale mirror must reconcile");
assert(issueTriageNeedsRefresh({ ...base, status: "never", last_success_at: "" }), "an empty mirror must reconcile on entry");
assert(issueTriageSourceLabel("self_issue") === "/issue", "Self-Issue origin must be recognizable");
assert(issueTriageSourceLabel("github_web") === "GitHub", "GitHub-created Issues must be recognizable");
assert(ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS < ISSUE_TRIAGE_POLL_INTERVAL_MS, "Webhook mirror reads must be faster than GitHub API reconciliation");
assert(snapshotLoadKindForPage("issue-triage") === "none", "the Issue Triage page must not trigger an expensive Kanban snapshot");
assert(nextIssueLabelSelection(null, "p1")?.[0] === "p1", "clicking a label must select it as the sole filter");
assert(nextIssueLabelSelection(["p1"], "p1") === null, "clicking the active label must return to all labels");
assert(issueLabelFoldCount(["a", "b", "c"]) === 0, "three labels must remain visible");
assert(issueLabelFoldCount(["a", "b", "c", "d", "e"]) === 2, "labels after the first three must fold into a count");
assert(canManageIssueRun("triaging") && canManageIssueRun("triage_recovering") && canManageIssueRun("fix_recovering") && canManageIssueRun("fix_paused"), "active, recovering, and paused runs must expose Manage Run");
assert(!canManageIssueRun("triage_blocked") && !canManageIssueRun("fix_blocked"), "terminal blocked runs must expose Start Triage instead of invalid Run controls");
assert(!canManageIssueRun("verified_candidate"), "completed verification must not expose Run controls");
assert(
  ISSUE_STATE_FILTERS.filter(([, label]) => label.startsWith("Queued:"))
    .map(([value, label]) => `${value}=${label}`)
    .join(",") === "triage_queued=Queued: triaged_queued,fix_queued=Queued: fix_queued",
  "searching Queued must expose every queued workflow state without inventing an aggregate state",
);
assert(!ISSUE_STATE_FILTERS.some(([value]) => String(value) === "queued"), "Queued must be a searchable prefix, not a synthetic state");
const queuedStates = filterIssueStateOptions("Queued").map(({ value }) => value);
assert(queuedStates.join(",") === "triage_queued,fix_queued", "Queued search must return all queued states");
assert(filterIssueStateOptions("Blocked").map(({ value }) => value).join(",") === "triage_blocked,fix_blocked,blocked", "Blocked search must include phase-specific terminal states");
assert(nextIssueStateSelection(null, "triage_queued").join(",") === "triage_queued", "a state click must start a single selection");
assert(nextIssueStateSelection(["triage_queued"], "fix_queued").length === 2, "state selection must support multiple values");
const selectedQueuedStates = nextIssueStateSelectAll(
  null,
  queuedStates,
  ISSUE_STATE_FILTERS.map(([value]) => value),
);
assert(
  selectedQueuedStates?.join(",") === "triage_queued,fix_queued",
  "Select all after a Queued search must select only queued states",
);
