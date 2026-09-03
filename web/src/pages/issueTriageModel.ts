import type { IssueTriageSyncState } from "../api/types";

export const ISSUE_TRIAGE_POLL_INTERVAL_MS = 300_000;
export const ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS = 10_000;
export const ISSUE_STATE_FILTERS = [
  ["open", "Open"],
  ["closed", "Closed"],
  ["mirrored", "Mirrored"],
  ["triage_queued", "Queued: triaged_queued"],
  ["triaging", "Triaging"],
  ["triage_paused", "Triage paused"],
  ["triage_recovering", "Triage recovering"],
  ["triage_blocked", "Triage blocked"],
  ["triage_cancelled", "Triage cancelled"],
  ["needs_info", "Needs info"],
  ["needs_info_exhausted", "Needs info exhausted"],
  ["awaiting_fix_approval", "Awaiting Fix approval"],
  ["fix_queued", "Queued: fix_queued"],
  ["fixing", "Fixing"],
  ["fix_paused", "Fix paused"],
  ["fix_recovering", "Fix recovering"],
  ["fix_blocked", "Fix blocked"],
  ["fix_cancelled", "Fix cancelled"],
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
  ["blocked", "Blocked"],
  ["failed", "Failed"],
] as const;

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
  "triage_queued", "triaging", "triage_paused", "triage_recovering",
  "fix_queued", "fixing", "fix_paused", "fix_recovering", "verifying",
]);

export function canManageIssueRun(state: string): boolean {
  return MANAGEABLE_RUN_STATES.has(state);
}

export function nextIssueLabelSelection(current: string[] | null, label: string): string[] | null {
  return current?.length === 1 && current[0] === label ? null : [label];
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
  const normalized = content.replace(/<img\b([^>]*)\/?\s*>/gi, (original, attributes: string) => {
    const source = attributes.match(/\bsrc\s*=\s*(["'])(.*?)\1/i)?.[2]
      ?.replaceAll("&amp;", "&");
    if (!source) return original;
    let url: URL;
    try {
      url = new URL(source);
    } catch {
      return original;
    }
    const allowed = (
      url.protocol === "https:"
      && (
        (url.hostname === "github.com" && url.pathname.startsWith("/user-attachments/"))
        || url.hostname === "user-images.githubusercontent.com"
        || url.hostname === "camo.githubusercontent.com"
      )
    );
    if (!allowed) return original;
    const alt = (attributes.match(/\balt\s*=\s*(["'])(.*?)\1/i)?.[2] || "GitHub attachment")
      .replace(/[\[\]]/g, "")
      .replaceAll("&quot;", "\"");
    return `![${alt}](${url.toString()})`;
  });
  return normalized.replace(/!\[([^\]]*)\]\((https:\/\/[^\s)]+)\)/g, (original, alt: string, source: string) => {
    let url: URL;
    try {
      url = new URL(source);
    } catch {
      return original;
    }
    const allowed = (
      (url.hostname === "github.com" && url.pathname.startsWith("/user-attachments/"))
      || url.hostname === "user-images.githubusercontent.com"
      || url.hostname === "camo.githubusercontent.com"
    );
    if (!allowed) return original;
    const proxy = `/api/projects/${encodeURIComponent(projectId)}/issue-triage/attachment?url=${encodeURIComponent(url.toString())}`;
    return `[![${alt}](${proxy})](${url.toString()})`;
  });
}
