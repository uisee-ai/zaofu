import { repairGeneratedEvidenceComment } from "./issueEvidenceMarkdown.js";
import { uiText } from '../settings/uiText.js';
import type { IssueTriageSyncState, IssueTriageItem } from "../api/types";

export const ISSUE_TRIAGE_POLL_INTERVAL_MS = 300_000;
export const ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS = 10_000;
export const ISSUE_TRIAGE_PAGE_SIZE = 100;
export const ISSUE_STATE_FILTERS = [
  ["open", "Open"],
  ["closed", "Closed"],
  ["mirrored", "Mirrored"],
  ["triage_queued", "Triage queued"],
  ["triaging", "Triaging"],
  ["triage_paused", "Triage paused"],
  ["triage_recovering", "Triage recovering"],
  ["needs_info", "Needs info"],
  ["needs_info_exhausted", "Needs info exhausted"],
  ["unverified_candidate", "Unverified candidate"],
  ["awaiting_fix_approval", "Awaiting Fix approval"],
  ["fix_queued", "Fix queued"],
  ["fixing", "Fixing"],
  ["fix_paused", "Fix paused"],
  ["fix_recovering", "Fix recovering"],
  ["verifying", "Verifying"],
  ["verified_candidate", "Verified candidate"],
  ["approved_for_pr", "Approved for PR"],
  ["owner_changes_requested", "Owner changes requested"],
  ["owner_rejected", "Owner rejected"],
  ["publication_prepared", "Publication prepared"],
  ["pr_open", "PR open"],
  ["pr_changes_requested", "PR changes requested"],
  ["pr_approved", "PR approved"],
  ["pr_closed_without_merge", "PR closed without merge"],
  ["merged", "Merged"],
  ["triage_blocked", "Triage blocked"],
  ["triage_cancelled", "Triage cancelled"],
  ["fix_blocked", "Fix blocked"],
  ["fix_cancelled", "Fix cancelled"],
  ["blocked", "Blocked"],
  ["failed", "Failed"],
] as const;

export type IssueState = typeof ISSUE_STATE_FILTERS[number][0];

export type IssueTooltipKind = "stage-state" | "default";

export function issueTooltipDelayMs(kind: IssueTooltipKind | string): number {
  return kind === "stage-state" ? 1000 : 500;
}

export type IssueSyncTone = "current" | "syncing" | "error" | "unavailable";

export function issueSyncPresentation(
  syncStatus: string,
  error: string,
  _runtimeState?: string,
): { tone: IssueSyncTone; label: string } {
  // Mirror refresh works even when the workflow runtime is stopped. The dot
  // beside “Last synced” must describe the mirror, not runtime availability.
  if (error || ["failed", "rate_limited"].includes(syncStatus)) {
    return { tone: "error", label: "GitHub refresh failed. Try Refresh again." };
  }
  if (syncStatus === "syncing") {
    return { tone: "syncing", label: "Refreshing GitHub Issues…" };
  }
  if (syncStatus === "fresh") {
    return { tone: "current", label: "GitHub Issues are up to date." };
  }
  return { tone: "unavailable", label: "GitHub Issues have not been synchronized yet." };
}

const ISSUE_STATE_LABELS = Object.fromEntries(ISSUE_STATE_FILTERS) as Record<string, string>;

export function issueStateLabel(state: string): string {
  return (ISSUE_STATE_LABELS[state] && uiText(ISSUE_STATE_LABELS[state]))
    ?? state.split("_").filter(Boolean).map((word, index) => (
      index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word
    )).join(" ");
}

export interface IssueStateStage {
  id: "repository" | "triage" | "decision" | "fix" | "delivery" | "complete" | "stopped";
  label: string;
  states: readonly IssueState[];
}

export const ISSUE_STATE_STAGES: readonly IssueStateStage[] = [
  { id: "repository", label: "Repository", states: ["open", "closed", "mirrored"] },
  { id: "triage", label: "Triage", states: ["triage_queued", "triaging", "triage_paused", "triage_recovering"] },
  { id: "decision", label: "Decision", states: ["needs_info", "needs_info_exhausted", "unverified_candidate", "awaiting_fix_approval"] },
  { id: "fix", label: "Fix", states: ["fix_queued", "fixing", "fix_paused", "fix_recovering", "verifying"] },
  { id: "delivery", label: "Delivery", states: ["verified_candidate", "approved_for_pr", "owner_changes_requested", "owner_rejected", "publication_prepared", "pr_open", "pr_changes_requested", "pr_approved", "pr_closed_without_merge"] },
  { id: "complete", label: "Complete", states: ["merged"] },
  { id: "stopped", label: "Stopped", states: ["triage_blocked", "triage_cancelled", "fix_blocked", "fix_cancelled", "blocked", "failed"] },
] as const;

export function issueStateStageTotal(
  stage: IssueStateStage,
  counts: Record<string, number>,
): number {
  return stage.states.reduce((total, state) => total + (counts[state] ?? 0), 0);
}

export function issueStateStage(state: string): IssueStateStage {
  return ISSUE_STATE_STAGES.find((stage) => stage.states.includes(state as IssueState))
    ?? ISSUE_STATE_STAGES[0];
}

export function isIssueStateSelected(selected: string[] | null, state: string): boolean {
  return selected === null || selected.includes(state);
}

/**
 * Translate the filter's visual selection into the list API contract.
 *
 * The selection controls deliberately distinguish an explicit empty selection from
 * an explicit full selection, but both mean "do not restrict this dimension".
 * The API uses an omitted parameter for that meaning; sending [] would instead
 * mean "match nothing" on current servers.
 */
export function issueSelectionQueryValues(
  selected: readonly string[] | null,
  supportedValues: readonly string[],
): string[] | null {
  if (selected === null || selected.length === 0) return null;
  const supported = new Set(supportedValues);
  const normalized = supportedValues.filter((value) => supported.has(value) && selected.includes(value));
  return normalized.length === 0 || normalized.length === supportedValues.length ? null : normalized;
}

export function initialIssueClarificationAnswers(
  questions: ReadonlyArray<{ id: string; type: string }>,
  saved: Readonly<Record<string, string>> = {},
): Record<string, string> {
  return Object.fromEntries(questions.map((question) => [
    question.id,
    saved[question.id] ?? (question.type === "boolean" ? "Yes" : ""),
  ]));
}

const STATE_HELP: Record<string, string> = {
  open: "Open on GitHub.", closed: "Closed on GitHub; history is retained.", mirrored: "Synced from GitHub; no workflow admitted.",
  triage_queued: "Waiting to start read-only Triage.", triaging: "Collecting evidence and preparing a Fix proposal.",
  triage_paused: "Triage paused; resume in Manage Run.", triage_recovering: "ZaoFu is recovering Triage automatically.",
  needs_info: "Waiting for additional information.", needs_info_exhausted: "Clarification limit reached; maintainer action needed.",
  unverified_candidate: "Verification is incomplete; maintainer review needed.", awaiting_fix_approval: "Waiting for approval before any code changes.",
  fix_queued: "Approved Fix is waiting to start.", fixing: "Applying the approved change.", fix_paused: "Fix paused; resume in Manage Run.",
  fix_recovering: "ZaoFu is recovering the Fix automatically.", verifying: "Checking the candidate against quality gates.",
  verified_candidate: "Verified candidate is ready for Owner review.", approved_for_pr: "Owner approved this candidate for a PR.",
  owner_changes_requested: "Owner requested changes.", owner_rejected: "Owner rejected this candidate.",
  publication_prepared: "Review branch is ready for manual publication.", pr_open: "The pull request is open on GitHub.",
  pr_changes_requested: "GitHub reviewers requested changes.", pr_approved: "Approved on GitHub; waiting for manual merge.",
  pr_closed_without_merge: "The pull request closed without merging.", merged: "Merged on GitHub.",
  triage_blocked: "Triage is blocked; check the recorded reason.", triage_cancelled: "Triage was cancelled; history is retained.",
  fix_blocked: "Fix is blocked; check the recorded reason.", fix_cancelled: "Fix was cancelled; local files are not reverted.",
  blocked: "A policy or dependency is blocking this workflow.", failed: "Automatic retries ended; manual investigation needed.",
};
export function issueStateDescription(state: string): string {
  return STATE_HELP[state] ?? `Current state: ${issueStateLabel(state)}.`;
}

export function effectiveIssueState(repositoryState: string | IssueTriageItem, workflowState?: string): string {
  if (typeof repositoryState !== "string") return repositoryState.workflow?.state?.trim() || repositoryState.github_state;
  return workflowState?.trim() || repositoryState;
}

export function nextIssueStageSelection<T extends string>(
  selected: readonly T[] | null,
  stageStates: readonly T[],
  allStates: readonly T[],
): T[] | null {
  const current = new Set(selected ?? allStates);
  const stageSelected = stageStates.every((state) => current.has(state));
  for (const state of stageStates) {
    if (stageSelected) current.delete(state);
    else current.add(state);
  }
  const next = allStates.filter((state) => current.has(state));
  return next.length === allStates.length ? null : next;
}

export function labelExplanation(label: string): string {
  const explanations: Record<string, string> = {
    bug: "A reproducible product defect.",
    accessibility: "Accessibility and assistive technology support.",
    documentation: "Documentation, examples, or guidance.",
    duplicate: "Duplicates an existing Issue.",
    enhancement: "A feature request or product improvement.",
    "help wanted": "Community contributions are welcome.",
    invalid: "Does not meet the current intake criteria.",
    question: "A question or topic requiring discussion.",
    wontfix: "Not planned for implementation.",
    runtime: "Runtime services or execution flow.",
    "kernel/state": "Kernel state or state-machine behavior.",
    "provider/integration": "External providers or integrations.",
    "web/ui": "Web interface or interaction behavior.",
    configuration: "Configuration, initialization, or environment setup.",
    security: "Security, permissions, or sensitive data.",
    performance: "Performance, latency, or resource usage.",
    "test/regression": "Test failures or product regressions.",
    // Self-Issue publication uses the classification and severity values as
    // GitHub labels. Keep these labels explicit so they do not look like
    // arbitrary/unknown repository labels in the Issue mirror.
    unknown: "Not classified yet.",
    p0: "P0 severity: active catastrophic or security impact.",
    p1: "P1 severity: major blocking impact.",
    p2: "P2 severity: material recoverable impact.",
    p3: "P3 severity: minor impact.",
  };
  return explanations[label.toLocaleLowerCase()]
    ?? `Filter by “${label}”.`;
}

export function filterIssueStateOptions(search: string): Array<{ value: string; label: string }> {
  const needle = search.trim().toLocaleLowerCase();
  return ISSUE_STATE_FILTERS
    .filter(([value, label]) => (
      value.toLocaleLowerCase().includes(needle)
      || label.toLocaleLowerCase().includes(needle)
    ))
    .map(([value, label]) => ({ value, label }));
}

export function nextIssueStateSelection(
  selected: string[] | null,
  state: string,
): string[] {
  const active = selected ?? [];
  return active.includes(state)
    ? active.filter((value) => value !== state)
    : [...active, state];
}

export function nextIssueStateSelectAll(
  selected: string[] | null,
  visibleStates: string[],
  allStates: string[],
): string[] | null {
  const filtered = visibleStates.length !== allStates.length;
  if (!filtered) return selected === null ? [] : null;
  const active = selected ?? [];
  const visibleOnlySelected = active.length === visibleStates.length
    && visibleStates.length > 0
    && visibleStates.every((value) => active.includes(value));
  return visibleOnlySelected ? [] : visibleStates;
}

const MANAGEABLE_RUN_STATES = new Set([
  "triaging", "triage_paused", "triage_recovering",
  "fixing", "fix_paused", "fix_recovering", "verifying",
]);

export function canManageIssueRun(state: string): boolean {
  return MANAGEABLE_RUN_STATES.has(state);
}

export function nextIssueLabelSelection(current: string[] | null, label: string, available: string[] = []): string[] | null {
  if (!available.length && current?.length === 1 && current[0] === label) return null;
  const active = current === null ? (available.length ? available : []) : current;
  const next = active.includes(label)
    ? active.filter((item) => item !== label)
    : [...active, label];
  return available.length > 0 && next.length === available.length ? null : next;
}

export function issueLabelFoldCount(labels: string[]): number {
  return Math.max(0, labels.length - 3);
}

export function issueTriageNeedsRefresh(
  sync: IssueTriageSyncState | null | undefined,
  nowMs = Date.now(),
): boolean {
  if (!sync || sync.status === "never" || !sync.last_success_at) return true;
  const lastSuccess = Date.parse(sync.last_success_at);
  return !Number.isFinite(lastSuccess) || nowMs - lastSuccess >= ISSUE_TRIAGE_POLL_INTERVAL_MS;
}

export function issueTriageSourceLabel(source: string): string {
  if (source === "self_issue") return "/issue";
  if (source === "github_web") return "GitHub";
  return "Unknown";
}

export function githubMarkdownForDisplay(content: string, projectId: string): string {
  const withVideos = repairGeneratedEvidenceComment(content).replace(/<video\b([^>]*)>([\s\S]*?)<\/video\s*>|<video\b([^>]*)\/?\s*>/gi, (original, attributes = "", inner = "", selfClosingAttributes = "") => {
    const allAttributes = `${attributes} ${selfClosingAttributes}`;
    const source = allAttributes.match(/\bsrc\s*=\s*(["'])(.*?)\1/i)?.[2]
      || inner.match(/\bsrc\s*=\s*(["'])(.*?)\1/i)?.[2];
    if (!source || !isAllowedGithubAttachmentUrl(source)) return original;
    return `[▶ Play video](${source})`;
  });
  // GitHub's uploaded assets are often emitted as a bare UUID URL after a
  // filename list (the URL has no .webm/.png suffix). Turn those links into
  // the same inline media controls used for explicit HTML media tags. Keep
  // URLs inside existing Markdown/HTML constructs untouched.
  const withBareAttachments = withVideos.replace(/https:\/\/github\.com\/user-attachments\/assets\/[A-Za-z0-9._~-]+/gi, (url, offset: number, source: string) => {
    const previous = source.slice(Math.max(0, offset - 1), offset);
    if (/^[\[\("']$/.test(previous)) return url;
    const context = source.slice(Math.max(0, offset - 260), offset);
    if (/\.(?:mp4|webm|mov|m4v)\b/i.test(context)) return `[▶ Play video](${url})`;
    if (/\.(?:png|jpe?g|gif|webp)\b/i.test(context)) return `![GitHub attachment](${url})`;
    return url;
  });
  const normalized = withBareAttachments.replace(/<img\b([^>]*)\/?\s*>/gi, (original, attributes: string) => {
    const source = attributes.match(/\bsrc\s*=\s*(["'])(.*?)\1/i)?.[2]
      ?.replaceAll("&amp;", "&");
    if (!source) return original;
    let url: URL;
    try {
      url = new URL(source);
    } catch {
      return original;
    }
    const allowed = isAllowedGithubAttachmentUrl(url.toString());
    if (!allowed) return original;
    const alt = (attributes.match(/\balt\s*=\s*(["'])(.*?)\1/i)?.[2] || "GitHub attachment")
      .replace(/[\[\]]/g, "")
      .replaceAll("&quot;", "\"");
    return `![${alt}](${url.toString()})`;
  });
  const withVideoProxy = normalized.replace(/\[▶ Play video\]\((https:\/\/[^\s)]+)\)/g, (original, source:string) => {
    if (!isAllowedGithubAttachmentUrl(source)) return original;
    return `[▶ Play video](/api/projects/${encodeURIComponent(projectId)}/issue-triage/attachment?url=${encodeURIComponent(source)})`;
  });
  return withVideoProxy.replace(/!\[([^\]]*)\]\((https:\/\/[^\s)]+)\)/g, (original, alt: string, source: string) => {
    let url: URL;
    try {
      url = new URL(source);
    } catch {
      return original;
    }
    const allowed = isAllowedGithubAttachmentUrl(url.toString());
    if (!allowed) return original;
    const proxy = `/api/projects/${encodeURIComponent(projectId)}/issue-triage/attachment?url=${encodeURIComponent(url.toString())}`;
    return `[![${alt}](${proxy})](${url.toString()})`;
  });
}

function isAllowedGithubAttachmentUrl(source: string): boolean {
  try {
    const url = new URL(source);
    return url.protocol === "https:" && (
      (url.hostname === "github.com" && (url.pathname.startsWith("/user-attachments/") || /^\/[^/]+\/[^/]+\/releases\/download\//.test(url.pathname)))
      || url.hostname === "user-images.githubusercontent.com"
      || url.hostname === "camo.githubusercontent.com"
    );
  } catch {
    return false;
  }
}
