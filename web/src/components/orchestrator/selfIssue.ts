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
  "Project runtime is stopped. Intake, Draft persistence, and committed-source inspection still work, "
  + "but fresh runtime events, logs, Traces, failure screenshots, and live reproduction evidence may be unavailable. "
  + "Start it with: cd /path_to_project && zf start";

export interface SelfIssueEvidenceControl {
  action: "self-issue-evaluation-start" | "self-issue-evaluation-cancel" | "self-issue-limited-continue";
  label: string;
  busyLabel: string;
  force?: boolean;
}

export function selfIssueCompactText(value: unknown): string {
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
}

export function selfIssueUnderstandingStatus(card: unknown): string {
  if (!card || typeof card !== "object" || Array.isArray(card)) return "unknown";
  const analysis = (card as Record<string, unknown>).analysis;
  if (!analysis || typeof analysis !== "object" || Array.isArray(analysis)) return "unknown";
  const understanding = (analysis as Record<string, unknown>).understanding;
  if (!understanding || typeof understanding !== "object" || Array.isArray(understanding)) {
    return "unknown";
  }
  return selfIssueCompactText((understanding as Record<string, unknown>).status) || "unknown";
}

export function selfIssueEvaluationLocked(card: unknown): boolean {
  if (!card || typeof card !== "object" || Array.isArray(card)) return false;
  const enrichment = (card as Record<string, unknown>).enrichment;
  return Boolean(
    enrichment
    && typeof enrichment === "object"
    && !Array.isArray(enrichment)
    && (enrichment as Record<string, unknown>).evaluation_locked === true
  );
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
  const draft = value.draft && typeof value.draft === "object" && !Array.isArray(value.draft)
    ? value.draft as Record<string, unknown>
    : undefined;
  const rawBatch = value.publication_batch ?? draft?.publication_batch;
  const batch = rawBatch && typeof rawBatch === "object" && !Array.isArray(rawBatch)
    ? rawBatch as Record<string, unknown>
    : (draft ?? value);
  if (!["previewed", "confirmed", "published"].includes(String(batch.status || ""))) return false;
  if (String(batch.publication_mode || "") !== mode) return false;
  const cardRevision = Number(value.revision || draft?.revision || 0);
  if (Number(batch.draft_revision || 0) !== cardRevision) return false;
  const rawPreviews = batch.previews ?? value.previews ?? draft?.previews;
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
  if (["collecting_static", "collecting_live", "waiting_for_runtime"].includes(status)) {
    return [{
      action: "self-issue-evaluation-cancel",
      label: "Cancel evaluation",
      busyLabel: "Cancelling…",
    }];
  }
  if (status === "interrupted") {
    return [
      {
        action: "self-issue-evaluation-start",
        label: "Start a new evaluation",
        busyLabel: "Restarting…",
        force: true,
      },
    ];
  }
  if (status === "pending") {
    return [{
      action: "self-issue-evaluation-start",
      label: "Start",
      busyLabel: "Starting…",
    }];
  }
  if (["failed", "completed"].includes(status)) {
    return [{
      action: "self-issue-evaluation-start",
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

export function selfIssuePublicationIsPartial(card: unknown): boolean {
  if (!card || typeof card !== "object" || Array.isArray(card)) return false;
  const value = card as Record<string, unknown>;
  return [value.publication_state, value.status].some((status) => (
    String(status || "") === "partially_published"
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
      direct_report: true,
      portable_session: true,
    },
  };
}

/** Detect the reporter's browser platform without coupling the skill to a host. */
export function detectBrowserEnvironment(): Record<string, string> {
  if (typeof navigator === "undefined") {
    return { os: "unknown", version: "unknown", arch: "unknown", source: "browser_detector" };
  }
  const userAgent = String(navigator.userAgent || "");
  const userAgentData = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData;
  const platform = String(
    userAgentData?.platform
      || navigator.platform || "",
  );
  const os = /android/i.test(userAgent) ? "Android"
    : /iphone|ipad|ipod/i.test(userAgent) ? "iOS"
      : /mac/i.test(platform) || /mac os/i.test(userAgent) ? "macOS"
        : /win/i.test(platform) || /windows/i.test(userAgent) ? "Windows"
          : /linux/i.test(platform) || /linux/i.test(userAgent) ? "Linux"
            : platform || "unknown";
  const version = os === "Windows"
    ? ((/Windows NT\s+([0-9.]+)/i.exec(userAgent)?.[1]) || "unknown")
    : os === "macOS"
      ? ((/Mac OS X\s*([0-9_\.]+)/i.exec(userAgent)?.[1]?.replaceAll("_", ".")) || "unknown")
      : os === "Android"
        ? ((/Android\s+([0-9.]+)/i.exec(userAgent)?.[1]) || "unknown")
        : os === "iOS"
          ? ((/OS\s+([0-9_]+)/i.exec(userAgent)?.[1]?.replaceAll("_", ".")) || "unknown")
          : "unknown";
  return {
    os,
    version,
    arch: String(userAgentData?.platform || navigator.platform || "unknown"),
    source: "browser_detector",
    detected_at: new Date().toISOString(),
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

export function selfIssueRestoreGithubAuthorization(
  draft: Record<string, unknown>, transactionId: string,
): Record<string, unknown> {
  const rawBatch = draft.publication_batch;
  const batch = rawBatch && typeof rawBatch === "object" && !Array.isArray(rawBatch)
    ? rawBatch as Record<string, unknown>
    : {};
  const publicationState = String(draft.publication_state || batch.status || "");
  const publicationMode = String(draft.publication_mode || batch.publication_mode || "");
  if (
    !transactionId
    || draft.transaction_id
    || publicationState !== "confirmed"
    || !["github", "both"].includes(publicationMode)
  ) return draft;
  return {
    ...draft,
    provider: "github",
    status: "authorization_pending",
    transaction_id: transactionId,
  };
}

export function selfIssueGithubAuthorizationShouldForget(status: string): boolean {
  return [
    "access_denied",
    "expired_token",
    "confirmation_expired",
    "stale_confirmation",
    "published",
    "partially_published",
    "publish_failed",
    "outcome_unknown",
  ].includes(status);
}

export function selfIssueGithubAuthorizationPollCanStart(
  transactionId: string, inFlightTransactionId: string,
): boolean {
  return Boolean(transactionId) && transactionId !== inFlightTransactionId;
}

export function selfIssueGithubAuthorizationPollShouldReset(
  status: string, reason: string,
): boolean {
  return status === "invalid_self_issue_request"
    && reason.includes("GitHub Device Flow transaction is invalid");
}

export function selfIssueGithubEvidenceRetryTarget(
  card: Record<string, unknown>,
): { provider: string; intentId: string } | null {
  const rawBatch = card.publication_batch;
  const batch = rawBatch && typeof rawBatch === "object" && !Array.isArray(rawBatch)
    ? rawBatch as Record<string, unknown>
    : {};
  const rawComments = batch.evidence_comments;
  const comments = rawComments && typeof rawComments === "object" && !Array.isArray(rawComments)
    ? rawComments as Record<string, unknown>
    : {};
  const rawGithub = comments.github;
  const github = rawGithub && typeof rawGithub === "object" && !Array.isArray(rawGithub)
    ? rawGithub as Record<string, unknown>
    : {};
  if (!["publish_failed", "outcome_unknown"].includes(String(github.status || ""))) {
    return null;
  }
  const rawIntentIds = card.intent_ids ?? batch.intent_ids;
  const intentIds = rawIntentIds && typeof rawIntentIds === "object" && !Array.isArray(rawIntentIds)
    ? rawIntentIds as Record<string, unknown>
    : {};
  const intentId = String(intentIds.github || card.intent_id || "").trim();
  return intentId ? { provider: "github", intentId } : null;
}
