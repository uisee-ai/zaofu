export interface SelfIssueSlashAction {
  action: "self-issue-capture";
  payload: Record<string, unknown>;
}

export interface SelfIssueOAuthCallback {
  code: string;
  state: string;
}

export type SelfIssueCardLayout = "expanded" | "minimized";

export function selfIssueIntakeMountState(
  intake: unknown,
  expanded: boolean,
): { mounted: boolean; hidden: boolean } {
  const mounted = Boolean(intake);
  return { mounted, hidden: mounted && !expanded };
}

export const SELF_ISSUE_RUNTIME_STOPPED_WARNING =
  "ZaoFu runtime is stopped. Intake, Draft persistence, and committed-source inspection still work, "
  + "but fresh runtime events, logs, Traces, failure screenshots, and live reproduction evidence may be unavailable. "
  + "Start it with: cd /path_to_project && zf start";

export interface SelfIssueEvidenceControl {
  action: "self-issue-evidence-start" | "self-issue-evidence-interrupt" | "self-issue-evidence-resume";
  label: string;
  busyLabel: string;
  force?: boolean;
}

export function selfIssueCompactText(value: unknown): string {
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
}

export function selfIssueRefreshErrorIsTransient(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || "");
  return /self-issue-get timed out|Failed to fetch|AbortError/i.test(message);
}

export function selfIssueProviderLabel(mode: string): string {
  if (mode === "github") return "GitHub";
  if (mode === "both") return "GitHub & GitLab";
  return "GitLab";
}

export function selfIssueSelectDestination(
  card: Record<string, unknown>, mode: string,
): Record<string, unknown> {
  return {
    ...card,
    selected_publication_mode: mode,
    preview: undefined,
    previews: undefined,
    publication_batch: undefined,
    batch_id: undefined,
    publication_mode: undefined,
    payload_digest: undefined,
    intent_id: undefined,
    intent_ids: undefined,
    providers: undefined,
    issues: undefined,
    confirmation_id: undefined,
    status: "draft",
  };
}

export function selfIssuePreviewIsReusable(card: unknown, mode: string): boolean {
  if (!card || typeof card !== "object" || Array.isArray(card)) return false;
  const value = card as Record<string, unknown>;
  const rawBatch = value.publication_batch;
  const batch = rawBatch && typeof rawBatch === "object" && !Array.isArray(rawBatch)
    ? rawBatch as Record<string, unknown>
    : value;
  if (!["previewed", "confirmed", "published"].includes(String(batch.status || ""))) return false;
  if (String(batch.publication_mode || "") !== mode) return false;
  if (Number(batch.draft_revision || 0) !== Number(value.revision || 0)) return false;
  const rawPreviews = batch.previews ?? value.previews;
  if (!rawPreviews || typeof rawPreviews !== "object" || Array.isArray(rawPreviews)) return false;
  const previews = rawPreviews as Record<string, unknown>;
  const providers = mode === "both" ? ["gitlab", "github"] : [mode];
  return providers.every((provider) => (
    previews[provider] && typeof previews[provider] === "object"
  ));
}

export function firstMissingSelfIssueQuestion(
  questions: Array<Record<string, unknown>>,
  answers: Record<string, unknown>,
): string {
  for (const question of questions) {
    if (question.required !== true) continue;
    const id = typeof question.id === "string" ? question.id : "";
    const value = answers[id];
    if (typeof value === "string" ? !value.trim() : !value) return id;
  }
  return "";
}

export function selfIssueIntakeSubmissionBlocker(
  questions: Array<Record<string, unknown>>,
  answers: Record<string, unknown>,
  attachmentCount: number,
  attachmentDisclosureConfirmed: boolean,
): { questionId: string; reason: "required" | "attachment_disclosure" } | null {
  const missing = firstMissingSelfIssueQuestion(questions, answers);
  if (missing) return { questionId: missing, reason: "required" };
  if (attachmentCount > 0 && !attachmentDisclosureConfirmed) {
    return { questionId: "attachments_context", reason: "attachment_disclosure" };
  }
  return null;
}

export function selfIssueEvidenceControls(status: string): SelfIssueEvidenceControl[] {
  if (["collecting_static", "collecting_live"].includes(status)) {
    return [{
      action: "self-issue-evidence-interrupt",
      label: "Interrupt",
      busyLabel: "Interrupting…",
    }];
  }
  if (status === "interrupted") {
    return [
      {
        action: "self-issue-evidence-resume",
        label: "Resume from checkpoint",
        busyLabel: "Resuming…",
      },
      {
        action: "self-issue-evidence-start",
        label: "Restart with fresh evidence",
        busyLabel: "Restarting…",
        force: true,
      },
    ];
  }
  if (status === "pending") {
    return [{
      action: "self-issue-evidence-start",
      label: "Start",
      busyLabel: "Starting…",
    }];
  }
  if (["failed", "completed"].includes(status)) {
    return [{
      action: "self-issue-evidence-start",
      label: "Restart",
      busyLabel: "Starting…",
      force: true,
    }];
  }
  return [];
}

export function selfIssueEvidenceBlocksPreview(status: string): boolean {
  return [
    "pending", "collecting_static", "waiting_for_runtime", "collecting_live",
  ].includes(status);
}

export function selfIssueCardLayoutStorageKey(projectId: string, draftId: string): string {
  return `zf.selfIssueCardLayout:${encodeURIComponent(projectId)}:${encodeURIComponent(draftId)}`;
}

export function selfIssueDismissCutoffStorageKey(projectId: string): string {
  return `zf.selfIssueDismissCutoff:${encodeURIComponent(projectId)}`;
}

export function selfIssueCreatedAfterCutoff(value: unknown, cutoff: string | null): boolean {
  if (!cutoff) return true;
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const createdAt = (value as Record<string, unknown>).created_at;
  if (typeof createdAt !== "string") return false;
  const created = Date.parse(createdAt);
  const boundary = Date.parse(cutoff);
  return Number.isFinite(created) && Number.isFinite(boundary) && created > boundary;
}

export function selfIssueLocalAttachmentUrl(
  projectId: string, draftId: string, digest: string,
): string {
  if (!projectId || !draftId || !/^[0-9a-f]{64}$/.test(digest)) return "";
  return `/api/projects/${encodeURIComponent(projectId)}/self-issue/attachments/`
    + `${encodeURIComponent(draftId)}/${digest}`;
}

export function selfIssueCardLayout(value: string | null): SelfIssueCardLayout {
  return value === "expanded" ? "expanded" : "minimized";
}

export function selfIssuePublishedUrl(card: unknown): string {
  return Object.values(selfIssuePublishedUrls(card))[0] ?? "";
}

export function selfIssuePublishedUrls(card: unknown): Record<string, string> {
  if (!card || typeof card !== "object" || Array.isArray(card)) return {};
  const value = card as Record<string, unknown>;
  const refs = value.issues && typeof value.issues === "object" && !Array.isArray(value.issues)
    ? value.issues as Record<string, unknown>
    : value.published_issue_refs && typeof value.published_issue_refs === "object" && !Array.isArray(value.published_issue_refs)
      ? value.published_issue_refs as Record<string, unknown>
      : {};
  const issue = value.issue && typeof value.issue === "object" && !Array.isArray(value.issue)
    ? value.issue as Record<string, unknown>
    : value.published_issue_ref && typeof value.published_issue_ref === "object" && !Array.isArray(value.published_issue_ref)
      ? value.published_issue_ref as Record<string, unknown>
      : null;
  const candidates: Record<string, unknown> = { ...refs };
  if (issue) candidates[typeof issue.provider === "string" ? issue.provider : "issue"] = issue;
  const result: Record<string, string> = {};
  for (const [provider, rawIssue] of Object.entries(candidates)) {
    if (!rawIssue || typeof rawIssue !== "object" || Array.isArray(rawIssue)) continue;
    const raw = typeof (rawIssue as Record<string, unknown>).url === "string"
      ? ((rawIssue as Record<string, unknown>).url as string).trim()
      : "";
    try {
      const url = new URL(raw);
      const expectedHost = provider === "gitlab"
        ? "gitlab.com"
        : provider === "github"
          ? "github.com"
          : "";
      if (
        url.protocol === "https:"
        && (expectedHost
          ? url.hostname === expectedHost
          : ["gitlab.com", "github.com"].includes(url.hostname))
      ) result[provider] = url.toString();
    } catch {
      // Provider refs are projections; unsafe or malformed URLs stay hidden.
    }
  }
  return result;
}

export function selfIssuePublicationLocked(card: unknown): boolean {
  if (!card || typeof card !== "object" || Array.isArray(card)) return false;
  const value = card as Record<string, unknown>;
  return [value.publication_state, value.status].some((status) => (
    ["publishing", "partially_published", "published", "outcome_unknown"].includes(
      String(status || ""),
    )
  ));
}

export function selfIssueTargetLocked(card: unknown): boolean {
  if (!card || typeof card !== "object" || Array.isArray(card)) return false;
  const policy = (card as Record<string, unknown>).target_policy;
  return Boolean(
    policy
    && typeof policy === "object"
    && !Array.isArray(policy)
    && (policy as Record<string, unknown>).locked === true,
  );
}

export function selfIssueOAuthContinuation(card: unknown): Record<string, string> {
  if (!card || typeof card !== "object" || Array.isArray(card)) return {};
  const value = card as Record<string, unknown>;
  const intentId = typeof value.intent_id === "string" ? value.intent_id.trim() : "";
  const batchId = typeof value.batch_id === "string" ? value.batch_id.trim() : "";
  const confirmationId = typeof value.confirmation_id === "string" ? value.confirmation_id.trim() : "";
  const preparationId = typeof value.preparation_id === "string" ? value.preparation_id.trim() : "";
  const attachmentConfirmationId = typeof value.attachment_confirmation_id === "string"
    ? value.attachment_confirmation_id.trim()
    : "";
  if (preparationId && attachmentConfirmationId) {
    return { preparation_id: preparationId, confirmation_id: attachmentConfirmationId };
  }
  if (batchId && confirmationId) {
    return { batch_id: batchId, confirmation_id: confirmationId };
  }
  return intentId && confirmationId
    ? { intent_id: intentId, confirmation_id: confirmationId }
    : {};
}

export function selfIssueSlashAction(message: string): SelfIssueSlashAction | null {
  const trimmed = message.trim();
  if (trimmed !== "/issue" && !trimmed.startsWith("/issue ")) return null;
  const argument = trimmed.slice("/issue".length).trim();
  return {
    action: "self-issue-capture",
    payload: {
      description: argument,
    },
  };
}

export function selfIssueOAuthCallback(search: string): SelfIssueOAuthCallback | null {
  const query = new URLSearchParams(search);
  const code = query.get("code")?.trim() ?? "";
  const state = query.get("state")?.trim() ?? "";
  return code && state ? { code, state } : null;
}

export function withoutSelfIssueOAuthCallback(search: string): string {
  const query = new URLSearchParams(search);
  query.delete("code");
  query.delete("state");
  return query.toString();
}

export function selfIssueOAuthSession(): string {
  const key = "zf.selfIssueOAuthSession";
  // OAuth callbacks may complete in a newly opened tab.  sessionStorage is
  // scoped to that tab, so the callback could not see the transaction created
  // by the original ZaoFu page.  Keep only the opaque transaction id in
  // localStorage; the provider state/code still validates the transaction.
  const existing = window.localStorage.getItem(key);
  if (existing) return existing;
  const created = window.crypto?.randomUUID?.() ?? `oauth-${Date.now().toString(36)}`;
  window.localStorage.setItem(key, created);
  return created;
}

export function selfIssueGithubTransactionStorageKey(projectId: string, draftId: string): string {
  return `zf.selfIssueGithubTransaction:${encodeURIComponent(projectId)}:${encodeURIComponent(draftId)}`;
}
