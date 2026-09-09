import {
  ISSUE_TRIAGE_POLL_INTERVAL_MS,
  ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS,
  ISSUE_STATE_STAGES,
  ISSUE_STATE_FILTERS,
  canManageIssueRun,
  effectiveIssueState,
  filterIssueStateOptions,
  isIssueStateSelected,
  issueStateDescription,
  issueStateLabel,
  issueStateStageTotal,
  labelExplanation,
  issueLabelFoldCount,
  issueTriageNeedsRefresh,
  issueTriageSourceLabel,
  nextIssueLabelSelection,
  nextIssueStateSelectAll,
  nextIssueStateSelection,
  nextIssueStageSelection,
  issueSyncPresentation,
} from "../src/pages/issueTriageModel.js";
import * as issueTriageModel from "../src/pages/issueTriageModel.js";
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
assert(issueStateLabel("triage_queued") === "Triage queued", "queued Triage must use natural state wording");
assert(issueStateLabel("fix_queued") === "Fix queued", "queued Fix must use natural state wording");
assert(issueStateLabel("triage_cancelled") === "Triage cancelled", "raw state identifiers must never leak into the UI");
assert(issueStateDescription("triage_cancelled").includes("cancelled"), "each state must have a concise English hint");
assert(issueSyncPresentation("fresh", "", "stopped").tone === "unavailable", "stopped Runtime must not look like a GitHub outage");
assert(issueSyncPresentation("failed", "GitHub request failed", "running").tone === "error", "GitHub sync failures must remain red");
assert(issueSyncPresentation("fresh", "", "running").tone === "current", "running Runtime with a fresh mirror must remain current");
assert(effectiveIssueState("open", "triaging") === "triaging", "workflow state must take precedence over repository state");
assert(effectiveIssueState("closed", "") === "closed", "repository state must be the fallback");
const tooltipDelay = (issueTriageModel as unknown as {
  issueTooltipDelayMs?: (kind: string) => number;
}).issueTooltipDelayMs;
assert(typeof tooltipDelay === "function", "Issue tooltip delays must have one shared policy");
if (tooltipDelay) {
  assert(tooltipDelay("stage-state") === 1000, "stage state hints must wait one second");
  assert(tooltipDelay("default") === 500, "all other hints must wait half a second");
}
assert(!ISSUE_STATE_FILTERS.some(([value]) => String(value) === "queued"), "Queued must be a searchable prefix, not a synthetic state");
assert(
  ISSUE_STATE_STAGES.map(({ id }) => id).join(",") === "repository,triage,decision,fix,delivery,complete,stopped",
  "Issue states must be grouped in lifecycle order",
);
assert(
  ISSUE_STATE_STAGES.flatMap(({ states }) => states).join(",")
    === ISSUE_STATE_FILTERS.map(([value]) => value).join(","),
  "Every filter state must belong to exactly one stage",
);
assert(
  issueStateStageTotal(ISSUE_STATE_STAGES[1], { triage_queued: 2, triaging: 3, fix_queued: 7 }) === 5,
  "A stage total must sum only the states in that stage",
);
assert(isIssueStateSelected(null, "triaging"), "All states must visibly check every individual state");
assert(isIssueStateSelected(["triaging"], "triaging"), "An explicitly selected state must be checked");
assert(!isIssueStateSelected([], "triaging"), "No states must leave individual states unchecked");
assert(labelExplanation("performance") === "Performance, latency, or resource usage.", "Known label hints must be English");
assert(labelExplanation("unknown") === "No specific classification was assigned because the Issue assessment is incomplete or inconclusive.", "Unknown classification must explain why it appears");
assert(labelExplanation("p0") === "P0 severity: active catastrophic or security impact.", "P0 severity must have an explicit explanation");
assert(labelExplanation("P1") === "P1 severity: major blocking impact.", "P1 severity lookup must be case-insensitive");
assert(labelExplanation("p2") === "P2 severity: material recoverable impact.", "P2 severity must have an explicit explanation");
assert(labelExplanation("p3") === "P3 severity: minor impact.", "P3 severity must have an explicit explanation");
assert(labelExplanation("custom") === "GitHub label “custom” (no ZaoFu-specific description).", "Unregistered labels must state that no ZaoFu description exists");
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
const allStates = ISSUE_STATE_FILTERS.map(([value]) => value);
const triageStates = [...ISSUE_STATE_STAGES[1].states];
const withoutTriage = nextIssueStageSelection(null, triageStates, allStates);
assert(
  withoutTriage?.every((state) => !triageStates.includes(state)) === true,
  "clicking a fully selected stage must deselect every state in that stage",
);
assert(
  nextIssueStageSelection(withoutTriage, triageStates, allStates) === null,
  "clicking the deselected stage must restore every state",
);
