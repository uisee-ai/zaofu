import type { FocusEvent, MouseEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, ArrowDown, ArrowUp, ChevronDown, ChevronUp, CircleDot, CircleX, Clock3, ExternalLink, GitPullRequest, Play, RefreshCw, Search, ShieldCheck, SlidersHorizontal, Star, Wrench } from "lucide-react";
import { MarkdownText } from "../components/agent-session/MarkdownText";
import {
  getIssueTriage,
  getIssueTriageDetail,
  getIssueTriageSummary,
  getProjectHealth,
  refreshIssueTriage,
  startIssueTriage,
  postAction,
} from "../api/client";
import type {
  IssueTriageDetail,
  IssueTriageItem,
  IssueTriagePageData,
  IssueTriageSummary,
} from "../api/types";
import {
  ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS,
  ISSUE_STATE_STAGES,
  ISSUE_STATE_FILTERS,
  canManageIssueRun,
  effectiveIssueState,
  filterIssueStateOptions,
  githubMarkdownForDisplay,
  isIssueStateSelected,
  issueStateStage,
  issueStateDescription,
  issueStateLabel,
  issueStateStageTotal,
  issueSyncPresentation,
  issueTooltipDelayMs,
  nextIssueStageSelection,
  nextIssueStateSelectAll,
  nextIssueStateSelection,
  nextIssueLabelSelection,
  issueTriageSourceLabel,
} from "./issueTriageModel";
import { IssueCandidateDeliveryPanel } from "./IssueCandidateDeliveryPanel";
import { IssueLabelList, githubLabelStyle } from "./IssueTriageLabels";
import { IssueTriageRunDialog } from "./IssueTriageRunDialog";

export type TriageTab = "github" | "gitlab" | "runtime";

function compactStat(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function githubAvatarUrl(login: string, supplied?: string): string {
  if (supplied?.startsWith("https://")) return supplied;
  return `https://github.com/${encodeURIComponent(login)}.png?size=40`;
}

function issueStatePresentation(state: string): {
  className: string;
  icon: typeof CircleDot;
} {
  const stage = issueStateStage(state);
  const icons = {
    repository: Clock3,
    triage: CircleDot,
    decision: AlertTriangle,
    fix: Wrench,
    delivery: GitPullRequest,
    complete: ShieldCheck,
    stopped: CircleX,
  } as const;
  return { className: `stage-${stage.id}`, icon: icons[stage.id] };
}

function IssueStateValue({ state }: { state: string }) {
  const presentation = issueStatePresentation(state);
  const Icon = presentation.icon;
  return (
    <span className={`issue-detail-state ${presentation.className}`}>
      <Icon aria-hidden="true" size={14} />
      {issueStateLabel(state)}
    </span>
  );
}

function GithubMark({ size = 15 }: { size?: number }) {
  return (
    <svg aria-hidden="true" height={size} viewBox="0 0 16 16" width={size}>
      <path
        d="M8 0a8 8 0 0 0-2.53 15.59c.4.08.55-.17.55-.38v-1.49c-2.23.49-2.7-1.08-2.7-1.08-.36-.93-.89-1.17-.89-1.17-.73-.5.05-.49.05-.49.81.06 1.23.83 1.23.83.72 1.23 1.88.88 2.34.67.07-.52.28-.88.51-1.08-1.78-.2-3.65-.89-3.65-3.96 0-.88.31-1.59.83-2.15-.08-.2-.36-1.02.08-2.12 0 0 .68-.22 2.2.82a7.65 7.65 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.52.56.83 1.27.83 2.15 0 3.08-1.87 3.75-3.66 3.95.29.25.54.74.54 1.5v2.22c0 .21.15.46.55.38A8 8 0 0 0 8 0Z"
        fill="currentColor"
      />
    </svg>
  );
}

export function IssueTriagePage({
  projectId,
  runtimeInterventions,
  tab,
  onTabChange,
  onOpenTask,
  onOpenProjection,
}: {
  projectId: string;
  runtimeInterventions: ReactNode;
  tab: TriageTab;
  onTabChange: (tab: TriageTab) => void;
  onOpenTask: (taskId: string) => void;
  onOpenProjection: (kind: "run" | "trace", id: string) => void;
}) {
  return (
    <section className="issue-triage-shell">
      <div className="issue-triage-tabs" role="tablist" aria-label="Triage domain">
        <button
          className={tab === "github" ? "active" : ""}
          role="tab"
          type="button"
          aria-selected={tab === "github"}
          onClick={() => onTabChange("github")}
        >
          GITHUB ISSUE
        </button>
        <button
          className={tab === "gitlab" ? "active" : ""}
          role="tab"
          type="button"
          aria-selected={tab === "gitlab"}
          onClick={() => onTabChange("gitlab")}
        >
          GITLAB ISSUE
        </button>
        <button
          className={tab === "runtime" ? "active" : ""}
          role="tab"
          type="button"
          aria-selected={tab === "runtime"}
          onClick={() => onTabChange("runtime")}
        >
          RUNTIME INTERVENTIONS
        </button>
      </div>
      {tab === "github" ? <IssuesTab projectId={projectId} onOpenTask={onOpenTask} onOpenProjection={onOpenProjection} /> : tab === "gitlab" ? <GitlabPlaceholder /> : runtimeInterventions}
    </section>
  );
}

function GitlabPlaceholder() {
  return <section className="issue-triage-placeholder" aria-label="GitLab Issue" />;
}

function IssuesTab({
  projectId,
  onOpenTask,
  onOpenProjection,
}: {
  projectId: string;
  onOpenTask: (taskId: string) => void;
  onOpenProjection: (kind: "run" | "trace", id: string) => void;
}) {
  const [summary, setSummary] = useState<IssueTriageSummary | null>(null);
  const [page, setPage] = useState<IssueTriagePageData | null>(null);
  const [detail, setDetail] = useState<IssueTriageDetail | null>(null);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [selectedStates, setSelectedStates] = useState<string[] | null>(null);
  const [selectedLabels, setSelectedLabels] = useState<string[] | null>(null);
  const [selectedAuthors, setSelectedAuthors] = useState<string[] | null>(null);
  const [source, setSource] = useState("");
  const [orderBy, setOrderBy] = useState<"created" | "updated" | "created_name" | "updated_name">("created");
  const [orderDirection, setOrderDirection] = useState<"asc" | "desc">("desc");
  const [cursor, setCursor] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [startDialogOpen, setStartDialogOpen] = useState(false);
  const [startIssueReference, setStartIssueReference] = useState("");
  const [startingTriage, setStartingTriage] = useState(false);
  const [startError, setStartError] = useState("");
  const [manageRunOpen, setManageRunOpen] = useState(false);
  const [triageNotice, setTriageNotice] = useState("");
  const [error, setError] = useState("");
  const [runtimeState, setRuntimeState] = useState("unknown");
  const [floatingTooltip, setFloatingTooltip] = useState<{
    text: string;
    left: number;
    top: number;
    above: boolean;
  } | null>(null);
  const floatingTooltipTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const floatingTooltipTarget = useRef<HTMLElement | null>(null);

  const filters = useMemo(() => ({
    q: appliedQuery,
    states: selectedStates === null ? "" : JSON.stringify(selectedStates),
    label: selectedLabels?.length === 1 ? selectedLabels[0] : "",
    labels: selectedLabels === null ? "" : JSON.stringify(selectedLabels),
    author: selectedAuthors?.length === 1 ? selectedAuthors[0] : "",
    authors: selectedAuthors === null ? "" : JSON.stringify(selectedAuthors),
    source,
    order_by: orderBy,
    order_direction: orderDirection,
    cursor,
    limit: 50,
  }), [appliedQuery, cursor, orderBy, orderDirection, selectedAuthors, selectedLabels, selectedStates, source]);

  const labelColors = useMemo(() => {
    const merged = { ...(summary?.label_colors ?? {}) };
    for (const item of page?.items ?? []) Object.assign(merged, item.label_colors);
    return merged;
  }, [page?.items, summary?.label_colors]);

  const load = useCallback(async () => {
    const [nextSummary, nextPage, health] = await Promise.all([
      getIssueTriageSummary(projectId),
      getIssueTriage(projectId, filters),
      getProjectHealth(projectId).catch(() => null),
    ]);
    setSummary(nextSummary);
    setPage(nextPage);
    if (health?.runtime_state) setRuntimeState(health.runtime_state);
    return nextSummary;
  }, [filters, projectId]);

  const refresh = useCallback(async (showSpinner = true) => {
    if (showSpinner) setRefreshing(true);
    setError("");
    try {
      await refreshIssueTriage(projectId, showSpinner);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      await load().catch(() => undefined);
    } finally {
      if (showSpinner) setRefreshing(false);
    }
  }, [load, projectId]);

  useEffect(() => {
    let active = true;
    setError("");
    void load()
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load().catch(() => undefined);
    }, ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const openDetail = async (issueNumber: number) => {
    if (detail?.issue.number === issueNumber) {
      setDetail(null);
      return;
    }
    setError("");
    try {
      setDetail(await getIssueTriageDetail(projectId, issueNumber));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const resetCursor = (setter: (value: string) => void, value: string) => {
    setter(value);
    setCursor(0);
    setDetail(null);
  };

  const selectLabel = (label: string) => {
    setSelectedLabels((current) => nextIssueLabelSelection(current, label));
    setCursor(0);
    setDetail(null);
  };

  const refreshSelected = async (issueNumber: number, notice: string) => {
    await load();
    setDetail(await getIssueTriageDetail(projectId, issueNumber));
    setTriageNotice(notice);
  };

  const openStartDialog = () => {
    setStartIssueReference(detail ? `#${detail.issue.number}` : "");
    setStartError("");
    setTriageNotice("");
    setStartDialogOpen(true);
  };

  const submitManualTriage = async () => {
    const reference = startIssueReference.trim();
    if (!reference) {
      setStartError("Enter a GitHub Issue URL or number.");
      return;
    }
    setStartingTriage(true);
    setStartError("");
    try {
      const result = await startIssueTriage(projectId, reference);
      await load();
      setDetail(await getIssueTriageDetail(projectId, result.issue_number));
      setTriageNotice(result.queued
        ? `Read-only Triage queued for GitHub Issue #${result.issue_number}.`
        : `GitHub Issue #${result.issue_number} is already queued for this revision.`);
      setStartDialogOpen(false);
    } catch (cause) {
      setStartError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setStartingTriage(false);
    }
  };

  const selectedWorkflowState = detail?.issue.workflow?.state ?? "";

  const clearFloatingTooltip = () => {
    if (floatingTooltipTimer.current) clearTimeout(floatingTooltipTimer.current);
    floatingTooltipTimer.current = null;
    floatingTooltipTarget.current = null;
    setFloatingTooltip(null);
  };
  const showFloatingTooltip = (target: EventTarget | null) => {
    const element = target instanceof Element
      ? target.closest<HTMLElement>("[data-tooltip]")
      : null;
    const text = element?.dataset.tooltip?.trim();
    if (!element || !text) return;
    if (floatingTooltipTarget.current === element) return;
    clearFloatingTooltip();
    floatingTooltipTarget.current = element;
    const delay = issueTooltipDelayMs(
      element.closest(".issue-state-stage-options") ? "stage-state" : "default",
    );
    floatingTooltipTimer.current = setTimeout(() => {
      if (floatingTooltipTarget.current !== element || !element.isConnected) return;
      const rect = element.getBoundingClientRect();
      const estimatedWidth = Math.min(240, Math.max(120, text.length * 7));
      const left = Math.max(12, Math.min(window.innerWidth - estimatedWidth - 12, rect.left + rect.width / 2 - estimatedWidth / 2));
      const above = rect.bottom + 72 > window.innerHeight;
      floatingTooltipTimer.current = null;
      setFloatingTooltip({ text, left, top: above ? rect.top - 7 : rect.bottom + 7, above });
    }, delay);
  };
  const hideFloatingTooltip = (currentTarget: EventTarget | null, relatedTarget: EventTarget | null) => {
    if (
      currentTarget instanceof Element
      && relatedTarget instanceof Node
      && currentTarget.closest("[data-tooltip]")?.contains(relatedTarget)
    ) return;
    clearFloatingTooltip();
  };

  useEffect(() => () => {
    if (floatingTooltipTimer.current) clearTimeout(floatingTooltipTimer.current);
  }, []);

  return (
    <section
      className="issue-triage-page floating-tooltips"
      onMouseOver={(event: MouseEvent<HTMLElement>) => showFloatingTooltip(event.target)}
      onMouseOut={(event: MouseEvent<HTMLElement>) => hideFloatingTooltip(event.target, event.relatedTarget)}
      onFocus={(event: FocusEvent<HTMLElement>) => showFloatingTooltip(event.target)}
      onBlur={(event: FocusEvent<HTMLElement>) => hideFloatingTooltip(event.target, event.relatedTarget)}
    >
      <header className="issue-triage-header">
        <p className="muted issue-triage-description">Read-only in ZaoFu. Edit labels, state, assignees, or content on GitHub.</p>
        <div className="button-row issue-triage-toolbar-actions">
          <SyncStatus summary={summary} error={error} runtimeState={runtimeState} />
          <button className="icon-button issue-triage-tooltip" data-tooltip="Fetch the latest Issue metadata and comments from GitHub" disabled={refreshing} type="button" onClick={() => void refresh()}>
            <RefreshCw aria-hidden="true" className={refreshing ? "spinning" : ""} size={15} />
            {refreshing ? "Syncing…" : "Refresh"}
          </button>
          {detail && canManageIssueRun(selectedWorkflowState) ? (
            <button
              className={`icon-button issue-triage-tooltip ${selectedWorkflowState.includes("queued") ? "danger" : ""}`}
              data-tooltip={selectedWorkflowState.includes("queued") ? "Permanently cancel this queued Run" : "Pause, resume, or permanently cancel this Run"}
              type="button"
              onClick={() => { setTriageNotice(""); setManageRunOpen(true); }}
            >
              {selectedWorkflowState.includes("queued") ? <CircleX aria-hidden="true" size={15} /> : <SlidersHorizontal aria-hidden="true" size={15} />}
              {selectedWorkflowState === "triage_queued" ? "Cancel queued Triage" : selectedWorkflowState === "fix_queued" ? "Cancel queued Fix" : "Manage Run"}
            </button>
          ) : (
            <button className="icon-button primary issue-triage-tooltip" data-tooltip="Manually queue the selected or specified GitHub Issue for read-only Triage" disabled={startingTriage} type="button" onClick={openStartDialog}>
              <Play aria-hidden="true" size={15} />
              Start Triage
            </button>
          )}
          {summary ? (
            <a className="issue-triage-repository-action" href={summary.repository_url} target="_blank" rel="noopener noreferrer">
              <span className="issue-triage-repository-label"><GithubMark /> View Repo</span>
              <span className="issue-triage-repository-stat" title={`${summary.sync.star_count ?? 0} GitHub stars`}>
                <Star aria-hidden="true" size={14} /> {compactStat(summary.sync.star_count ?? 0)}
              </span>
            </a>
          ) : null}
          {summary ? (
            <a className="icon-button" href={summary.new_issue_url} target="_blank" rel="noopener noreferrer">
              New issue <ExternalLink aria-hidden="true" size={14} />
            </a>
          ) : null}
        </div>
      </header>

      {triageNotice ? <div className="issue-triage-action-notice" role="status">{triageNotice}</div> : null}

      <IssueStateStages
        counts={summary?.states ?? {}}
        selected={selectedStates}
        onChange={(next) => { setSelectedStates(next); setCursor(0); setDetail(null); }}
      />

      <div className="issue-triage-filters">
        <OrderFilter
          orderBy={orderBy}
          direction={orderDirection}
          onOrderBy={(value) => { setOrderBy(value); setCursor(0); setDetail(null); }}
          onDirection={(value) => { setOrderDirection(value); setCursor(0); setDetail(null); }}
        />
        <form onSubmit={(event) => { event.preventDefault(); setCursor(0); setAppliedQuery(query.trim()); }}>
          <Search aria-hidden="true" size={15} />
          <input aria-label="Search issues" placeholder="Search title, number, author, or label" value={query} onChange={(event) => setQuery(event.target.value)} />
        </form>
        <StateFilter
          counts={summary?.states ?? {}}
          selected={selectedStates}
          onChange={(next) => { setSelectedStates(next); setCursor(0); setDetail(null); }}
        />
        <LabelFilter
          colors={labelColors}
          counts={summary?.labels ?? {}}
          selected={selectedLabels}
          onChange={(next) => {
            setSelectedLabels(next);
            setCursor(0);
            setDetail(null);
          }}
        />
        <ContributorFilter
          counts={summary?.authors ?? {}}
          selected={selectedAuthors}
          onChange={(next) => { setSelectedAuthors(next); setCursor(0); setDetail(null); }}
        />
        <select aria-label="Source" value={source} onChange={(event) => resetCursor(setSource, event.target.value)}>
          <option value="">All sources</option><option value="self_issue">/issue</option><option value="github_web">GitHub</option>
        </select>
      </div>

      <div className={`issue-triage-content ${detail ? "with-detail" : ""}`}>
        <div className="issue-triage-list" aria-busy={loading}>
          {loading ? <p className="empty-text">Loading Issue mirror…</p> : null}
          {!loading && !page?.items.length ? <p className="empty-text">No matching GitHub Issues.</p> : null}
          {page?.items.map((item) => {
            const effectiveState = effectiveIssueState(item.github_state, item.workflow?.state);
            const presentation = issueStatePresentation(effectiveState);
            const StateIcon = presentation.icon;
            return <div
              aria-label={`Open Issue ${item.number}`}
              className={`issue-triage-row ${detail?.issue.number === item.number ? "selected" : ""}`}
              key={item.issue_key}
              role="button"
              tabIndex={0}
              onClick={() => void openDetail(item.number)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  void openDetail(item.number);
                }
              }}
            >
              <span
                aria-label={`This issue is ${issueStateLabel(effectiveState)}.`}
                className={`issue-state-indicator issue-triage-tooltip ${presentation.className}`}
                data-tooltip={issueStateDescription(effectiveState)}
              >
                <StateIcon aria-hidden="true" size={16} />
              </span>
              <span className="issue-triage-row-main">
                <strong>{item.title}</strong>
                <span className="muted issue-triage-row-meta">
                  <span>#{item.number} ·</span>
                  <AuthorIdentity
                    login={item.author_login}
                    avatarUrl={item.author_avatar_url}
                    states={summary?.author_states?.[item.author_login]}
                  />
                  <span>· {issueTriageSourceLabel(item.source)} · updated {new Date(item.updated_at).toLocaleString()}</span>
                </span>
                <IssueLabelList labels={item.labels} colors={item.label_colors} onSelect={selectLabel} />
              </span>
              <span
                className={`issue-group-badge issue-triage-tooltip ${presentation.className}`}
                data-tooltip={item.workflow
                  ? `Workflow: ${issueStateLabel(effectiveState)}${item.workflow.task_id ? ` · ${item.workflow.task_id}` : ""}`
                  : issueStateDescription(effectiveState)}
              ><StateIcon aria-hidden="true" size={14} />{issueStateLabel(effectiveState)}</span>
            </div>
          })}
          <div className="issue-triage-pagination">
            <button className="icon-button" disabled={!page || page.cursor === 0} type="button" onClick={() => { setCursor(Math.max(0, cursor - 50)); setDetail(null); }}>Previous</button>
            <span className="muted">{page ? `${page.cursor + (page.items.length ? 1 : 0)}–${page.cursor + page.items.length} of ${page.total}` : "0 issues"}</span>
            <button className="icon-button" disabled={page?.next_cursor == null} type="button" onClick={() => { setCursor(page?.next_cursor ?? cursor); setDetail(null); }}>Next</button>
          </div>
        </div>
        {detail ? <IssueDetail projectId={projectId} detail={detail} authorStates={summary?.author_states ?? {}} onClose={() => setDetail(null)} onLabelSelect={selectLabel} onOpenTask={onOpenTask} onOpenProjection={onOpenProjection} onUpdated={(notice) => refreshSelected(detail.issue.number, notice)} /> : null}
      </div>
      {startDialogOpen ? (
        <StartTriageDialog
          error={startError}
          issueReference={startIssueReference}
          repository={summary?.repository ?? "configured GitHub repository"}
          selectedIssue={detail?.issue}
          starting={startingTriage}
          onChange={setStartIssueReference}
          onClose={() => { if (!startingTriage) setStartDialogOpen(false); }}
          onSubmit={() => void submitManualTriage()}
        />
      ) : null}
      {manageRunOpen && detail ? (
        <IssueTriageRunDialog
          projectId={projectId}
          issue={detail.issue}
          onClose={() => setManageRunOpen(false)}
          onUpdated={(notice) => refreshSelected(detail.issue.number, notice)}
        />
      ) : null}
      {floatingTooltip && typeof document !== "undefined" ? createPortal(
        <div
          className={`issue-floating-tooltip ${floatingTooltip.above ? "above" : ""}`}
          role="tooltip"
          style={{ left: floatingTooltip.left, top: floatingTooltip.top }}
        >
          {floatingTooltip.text}
        </div>,
        document.body,
      ) : null}
    </section>
  );
}

function StartTriageDialog({
  error,
  issueReference,
  repository,
  selectedIssue,
  starting,
  onChange,
  onClose,
  onSubmit,
}: {
  error: string;
  issueReference: string;
  repository: string;
  selectedIssue?: IssueTriageDetail["issue"];
  starting: boolean;
  onChange: (value: string) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel issue-triage-start-modal" role="dialog" aria-modal="true" aria-labelledby="issue-triage-start-title">
        <header className="section-heading">
          <div>
            <h2 id="issue-triage-start-title">Start Issue Triage</h2>
            <span className="muted">Manually admit one Issue into the read-only Triage workflow.</span>
          </div>
          <button className="icon-button" disabled={starting} type="button" onClick={onClose}>Close</button>
        </header>
        <form className="modal-body issue-triage-start-body" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
          {selectedIssue ? (
            <div className="issue-triage-start-selection">
              <span className="muted">Selected Issue #{selectedIssue.number}</span>
              <strong>{selectedIssue.title}</strong>
            </div>
          ) : null}
          <label htmlFor="issue-triage-start-reference">GitHub Issue URL or number</label>
          <input
            autoFocus
            className="filter-input"
            id="issue-triage-start-reference"
            placeholder={`https://github.com/${repository}/issues/123 or #123`}
            value={issueReference}
            onChange={(event) => onChange(event.target.value)}
          />
          <p className="muted issue-triage-start-help">
            Historical Issues are allowed. If the Issue is not mirrored yet, ZaoFu fetches only this Issue from {repository} before queueing Triage.
          </p>
          <div className="issue-triage-start-safety">
            This starts read-only Triage only. It does not edit GitHub or automatically start a Fix workflow.
          </div>
          {error ? <div className="issue-triage-start-error" role="alert">{error}</div> : null}
          <div className="button-row issue-triage-start-actions">
            <button className="icon-button" disabled={starting} type="button" onClick={onClose}>Cancel</button>
            <button className="icon-button primary" disabled={starting || !issueReference.trim()} type="submit">
              <Play aria-hidden="true" size={15} />
              {starting ? "Starting…" : "Start Triage"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function AuthorIdentity({
  login,
  avatarUrl,
  states,
}: {
  login: string;
  avatarUrl?: string;
  states?: { open: number; closed: number };
}) {
  const profileUrl = `https://github.com/${encodeURIComponent(login)}`;
  const [popover, setPopover] = useState<{ left: number; top: number; above: boolean } | null>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelOpen = () => {
    if (openTimer.current) clearTimeout(openTimer.current);
    openTimer.current = null;
  };
  const cancelClose = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };
  const openPopover = (element: HTMLElement) => {
    cancelClose();
    if (popover || openTimer.current) return;
    openTimer.current = setTimeout(() => {
      openTimer.current = null;
      if (!element.isConnected) return;
      const rect = element.getBoundingClientRect();
      const width = 230;
      const left = Math.max(12, Math.min(window.innerWidth - width - 12, rect.left - 12));
      const above = rect.top >= 150;
      setPopover({ left, top: above ? rect.top - 7 : rect.bottom + 7, above });
    }, issueTooltipDelayMs("default"));
  };
  const scheduleClose = () => {
    cancelOpen();
    cancelClose();
    closeTimer.current = setTimeout(() => setPopover(null), 100);
  };
  useEffect(() => () => {
    cancelOpen();
    cancelClose();
  }, []);
  const card = <span
    className={`issue-author-popover floating ${popover?.above ? "above" : ""}`}
    role="tooltip"
    style={popover ? { left: popover.left, top: popover.top } : undefined}
    onClick={(event) => event.stopPropagation()}
    onMouseEnter={cancelClose}
    onMouseLeave={scheduleClose}
  >
    <span className="issue-author-popover-heading">
      <img src={githubAvatarUrl(login, avatarUrl)} alt="" loading="lazy" referrerPolicy="no-referrer" />
      <strong>{login}</strong>
    </span>
    <span className="issue-author-popover-stats">{states?.open ?? 0} open · {states?.closed ?? 0} closed issues</span>
    <a href={profileUrl} rel="noopener noreferrer" target="_blank" onClick={(event) => event.stopPropagation()}>
      <GithubMark /> View GitHub profile
    </a>
  </span>;
  return <span
    className="issue-author-identity"
    onClick={(event) => event.stopPropagation()}
    onFocus={(event) => openPopover(event.currentTarget)}
    onMouseEnter={(event) => openPopover(event.currentTarget)}
    onMouseLeave={scheduleClose}
  >
    <img
      className="issue-author-avatar"
      src={githubAvatarUrl(login, avatarUrl)}
      alt={`${login} avatar`}
      loading="lazy"
      referrerPolicy="no-referrer"
    />
    <span className="issue-author-login">{login}</span>
    {popover && typeof document !== "undefined" ? createPortal(card, document.body) : null}
  </span>;
}

function OrderFilter({
  orderBy,
  direction,
  onOrderBy,
  onDirection,
}: {
  orderBy: "created" | "updated" | "created_name" | "updated_name";
  direction: "asc" | "desc";
  onOrderBy: (value: "created" | "updated" | "created_name" | "updated_name") => void;
  onDirection: (value: "asc" | "desc") => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="issue-order-filter" onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
    }}>
      <button aria-expanded={open} aria-label="Order issues" className="issue-order-filter-trigger" type="button" onClick={() => setOpen((value) => !value)}>
        <SlidersHorizontal aria-hidden="true" size={16} />
      </button>
      {open ? <div className="issue-order-filter-menu">
        <strong>Ordering</strong>
        <div className="issue-order-filter-controls">
          <div className="issue-order-direction" aria-label="Sort direction">
            <button aria-label="Ascending" className={direction === "asc" ? "active" : ""} type="button" onClick={() => onDirection("asc")}><ArrowUp size={15} /></button>
            <button aria-label="Descending" className={direction === "desc" ? "active" : ""} type="button" onClick={() => onDirection("desc")}><ArrowDown size={15} /></button>
          </div>
          <select aria-label="Order field" value={orderBy} onChange={(event) => onOrderBy(event.target.value as "created" | "updated" | "created_name" | "updated_name")}>
            <option value="created">Date created</option>
            <option value="updated">Date updated</option>
            <option value="created_name">Name at creation</option>
            <option value="updated_name">Name after update</option>
          </select>
        </div>
      </div> : null}
    </div>
  );
}

function IssueStateStages({
  counts,
  selected,
  onChange,
}: {
  counts: Record<string, number>;
  selected: string[] | null;
  onChange: (states: string[] | null) => void;
}) {
  const allStates = ISSUE_STATE_FILTERS.map(([value]) => value);
  const [openStages, setOpenStages] = useState<Set<string>>(() => new Set());
  const toggle = (state: string) => {
    const next = nextIssueStateSelection(selected, state);
    onChange(next.length === allStates.length ? null : next);
  };
  const toggleOpen = (stageId: string) => {
    setOpenStages((current) => {
      const next = new Set(current);
      if (next.has(stageId)) next.delete(stageId);
      else next.add(stageId);
      return next;
    });
  };
  return (
    <div className="issue-state-stages" aria-label="Issue lifecycle stages">
      {ISSUE_STATE_STAGES.map((stage) => {
        const presentation = issueStatePresentation(stage.states[0]);
        const Icon = presentation.icon;
        const open = openStages.has(stage.id);
        const allStageStatesSelected = stage.states.every((state) => isIssueStateSelected(selected, state));
        const stateCountLabel = `${stage.states.length} state${stage.states.length === 1 ? "" : "s"}`;
        return (
          <section className={`issue-state-stage ${presentation.className} ${open ? "open" : ""}`} key={stage.id}>
            <button
              aria-pressed={allStageStatesSelected}
              className="issue-state-stage-select issue-triage-tooltip"
              data-tooltip={`${allStageStatesSelected ? "Exclude" : "Include"} every state in the ${stage.label} stage.`}
              type="button"
              onClick={() => onChange(nextIssueStageSelection(selected, stage.states, allStates))}
            >
              <span><Icon aria-hidden="true" size={15} />{stage.label}</span>
              <strong>{issueStateStageTotal(stage, counts)}</strong>
            </button>
            <button
              aria-expanded={open}
              aria-label={`${open ? "Collapse" : "Expand"} ${stage.label} states`}
              className="issue-state-stage-toggle"
              type="button"
              onClick={() => toggleOpen(stage.id)}
            >
              <span>{stateCountLabel}</span>
              <ChevronDown aria-hidden="true" className="issue-state-stage-chevron" size={14} />
            </button>
            {open ? (
              <div className="issue-state-stage-options" role="group" aria-label={`${stage.label} states`}>
                {stage.states.map((state) => (
                  <button
                    aria-pressed={isIssueStateSelected(selected, state)}
                    className={`issue-triage-tooltip ${isIssueStateSelected(selected, state) ? "selected" : ""}`}
                    data-tooltip={issueStateDescription(state)}
                    key={state}
                    type="button"
                    onClick={() => toggle(state)}
                  >
                    <span>{issueStateLabel(state)}</span><strong>{counts[state] ?? 0}</strong>
                  </button>
                ))}
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

function StateFilter({
  counts,
  selected,
  onChange,
}: {
  counts: Record<string, number>;
  selected: string[] | null;
  onChange: (states: string[] | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const options: Array<{ value: string; label: string }> = ISSUE_STATE_FILTERS.map(
    ([value, label]) => ({ value, label }),
  );
  const optionValues = options.map(({ value }) => value);
  const visible = filterIssueStateOptions(search);
  const visibleValues = visible.map(({ value }) => value);
  const visibleOnlySelected = visibleValues.length > 0
    && visibleValues.every((value) => isIssueStateSelected(selected, value));
  const selectAllChecked = visibleOnlySelected;
  const buttonLabel = selected === null
    ? "All states"
    : selected.length === 0
      ? "No states"
      : selected.length === 1
        ? "1 state"
        : `${selected.length} states`;

  const commitSelection = (next: string[]) => {
    onChange(next.length === optionValues.length ? null : next);
  };
  const toggle = (value: string) => {
    commitSelection(nextIssueStateSelection(selected, value));
  };
  const toggleVisible = () => {
    onChange(nextIssueStateSelectAll(selected, visibleValues, optionValues));
  };

  return (
    <div
      className="issue-label-filter issue-state-filter"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        className="issue-label-filter-trigger"
        type="button"
        onClick={() => setOpen((value) => !value)}
      >
        <span>{buttonLabel}</span>
        {open ? <ChevronUp aria-hidden="true" size={14} /> : <ChevronDown aria-hidden="true" size={14} />}
      </button>
      {open ? (
        <div className="issue-label-filter-menu" role="menu">
          <input
            aria-label="Filter states"
            placeholder="Filter states…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <button
            aria-checked={selectAllChecked}
            className="issue-label-filter-all"
            disabled={!visibleValues.length}
            role="menuitemcheckbox"
            type="button"
            onClick={toggleVisible}
          >
            <input checked={selectAllChecked} readOnly tabIndex={-1} type="checkbox" />
            <strong>Select all</strong>
          </button>
          <div className="issue-label-filter-options">
            {visible.map(({ value, label }) => (
              <button
                aria-checked={isIssueStateSelected(selected, value)}
                className="issue-label-filter-option"
                key={value}
                role="menuitemcheckbox"
                type="button"
                onClick={() => toggle(value)}
              >
                <input checked={isIssueStateSelected(selected, value)} readOnly tabIndex={-1} type="checkbox" />
                {(() => { const presentation = issueStatePresentation(value); const Icon = presentation.icon; return <span className={`issue-filter-state ${presentation.className}`}><Icon aria-hidden="true" size={14} />{label}</span>; })()}
                <span className="muted">{counts[value] ?? 0}</span>
              </button>
            ))}
            {!visible.length ? <span className="muted issue-label-filter-empty">No matching states</span> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ContributorFilter({
  counts,
  selected,
  onChange,
}: {
  counts: Record<string, number>;
  selected: string[] | null;
  onChange: (authors: string[] | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const options = useMemo(() => Object.keys(counts).sort((left, right) => left.localeCompare(right)), [counts]);
  const visible = options.filter((item) => item.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const active = selected === null ? options : selected.filter((item) => options.includes(item));
  const allSelected = selected === null || (options.length > 0 && active.length === options.length);
  const buttonLabel = selected === null ? "All contributors" : selected.length === 0 ? "No contributors" : `${selected.length} contributor${selected.length === 1 ? "" : "s"}`;
  const toggle = (item: string) => {
    const next = (selected === null || active.includes(item))
      ? active.filter((value) => value !== item)
      : [...active, item];
    onChange(next.length === options.length ? null : next);
  };
  return <div className="issue-label-filter issue-contributor-filter" onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
  }}>
    <button aria-expanded={open} aria-haspopup="menu" className="issue-label-filter-trigger" type="button" onClick={() => setOpen((value) => !value)}>
      <span>{buttonLabel}</span>{open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
    </button>
    {open ? <div className="issue-label-filter-menu" role="menu">
      <input aria-label="Filter contributors" placeholder="Filter contributors…" value={search} onChange={(event) => setSearch(event.target.value)} />
      <button
        aria-checked={allSelected}
        className="issue-label-filter-all"
        role="menuitemcheckbox"
        type="button"
        onClick={() => onChange(allSelected ? [] : null)}
      ><input checked={allSelected} readOnly tabIndex={-1} type="checkbox" /><strong>Select all</strong></button>
      <div className="issue-label-filter-options">
        {visible.map((item) => <button
          aria-checked={selected === null || active.includes(item)}
          className="issue-label-filter-option issue-contributor-filter-option"
          key={item}
          role="menuitemcheckbox"
          type="button"
          onClick={() => toggle(item)}
        >
          <input checked={selected === null || active.includes(item)} readOnly tabIndex={-1} type="checkbox" />
          <span className="issue-contributor-name"><img className="issue-author-avatar" src={githubAvatarUrl(item)} alt="" loading="lazy" />{item}</span>
          <span className="muted">{counts[item]}</span>
        </button>)}
      </div>
    </div> : null}
  </div>;
}

function SyncStatus({ summary, error, runtimeState }: { summary: IssueTriageSummary | null; error: string; runtimeState: string }) {
  const sync = summary?.sync;
  const presentation = issueSyncPresentation(sync?.status ?? "never", error, runtimeState);
  return (
    <div className={`issue-triage-sync ${presentation.tone}`}>
      <span className={`issue-triage-sync-dot ${presentation.tone}`} title={presentation.label} aria-label={presentation.label} />
      <span className="muted">{sync?.last_success_at ? `Last synced ${new Date(sync.last_success_at).toLocaleString()}` : "Not synchronized yet"}</span>
    </div>
  );
}

function LabelFilter({
  colors,
  counts,
  selected,
  onChange,
}: {
  colors: Record<string, string>;
  counts: Record<string, number>;
  selected: string[] | null;
  onChange: (labels: string[] | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const options = useMemo(() => Object.keys(counts).sort((left, right) => left.localeCompare(right)), [counts]);
  const visible = options.filter((item) => item.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const activeLabels = selected === null ? options : selected.filter((item) => options.includes(item));
  const allSelected = selected === null || (options.length > 0 && activeLabels.length === options.length);
  const buttonLabel = selected === null
    ? "All labels"
    : selected.length === 0
      ? "No labels"
      : `${selected.length} label${selected.length === 1 ? "" : "s"}`;

  const toggleLabel = (item: string) => {
    const checked = selected === null || activeLabels.includes(item);
    const next = checked
      ? activeLabels.filter((value) => value !== item)
      : [...activeLabels, item];
    onChange(next.length === options.length ? null : next);
  };

  return (
    <div
      className="issue-label-filter"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        className="issue-label-filter-trigger"
        type="button"
        onClick={() => setOpen((value) => !value)}
      >
        <span>{buttonLabel}</span>
        {open ? <ChevronUp aria-hidden="true" size={14} /> : <ChevronDown aria-hidden="true" size={14} />}
      </button>
      {open ? (
        <div className="issue-label-filter-menu" role="menu">
          <input
            aria-label="Filter labels"
            placeholder="Filter labels…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <button
            aria-checked={allSelected}
            className="issue-label-filter-all"
            role="menuitemcheckbox"
            type="button"
            onClick={() => onChange(allSelected ? [] : null)}
          >
            <input
              checked={allSelected}
              readOnly
              tabIndex={-1}
              type="checkbox"
            />
            <strong>Select all</strong>
          </button>
          <div className="issue-label-filter-options">
            {visible.map((item) => (
              <button
                aria-checked={selected === null || activeLabels.includes(item)}
                className="issue-label-filter-option"
                key={item}
                role="menuitemcheckbox"
                type="button"
                onClick={() => toggleLabel(item)}
              >
                <input
                  checked={selected === null || activeLabels.includes(item)}
                  readOnly
                  tabIndex={-1}
                  type="checkbox"
                />
                <span className="badge issue-label-badge" style={githubLabelStyle(item, colors)}>{item}</span>
                <span className="muted">{counts[item]}</span>
              </button>
            ))}
            {!visible.length ? <span className="muted issue-label-filter-empty">No matching labels</span> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function IssueDetail({
  projectId,
  detail,
  authorStates,
  onClose,
  onLabelSelect,
  onOpenTask,
  onOpenProjection,
  onUpdated,
}: {
  projectId: string;
  detail: IssueTriageDetail;
  authorStates: Record<string, { open: number; closed: number }>;
  onClose: () => void;
  onLabelSelect: (label: string) => void;
  onOpenTask: (taskId: string) => void;
  onOpenProjection: (kind: "run" | "trace", id: string) => void;
  onUpdated: (notice: string) => Promise<void>;
}) {
  const { issue } = detail;
  const workflowId = issue.workflow?.workflow_id || "";
  const workflowRunId = issue.workflow?.run_id || "";
  return (
    <aside
      className="issue-triage-detail"
      onClick={(event) => {
        if (!(event.target instanceof Element)) return;
        if (event.target.closest('button[title="Download image"]')) {
          event.preventDefault();
          return;
        }
        const image = event.target.closest('img[data-streamdown="image"]');
        const link = image?.closest("a");
        if (link?.href) {
          event.preventDefault();
          const opened = window.open(link.href, "_blank");
          if (opened) opened.opener = null;
        }
      }}
    >
      <header className="issue-triage-detail-header">
        <span className="muted">GitHub Issue #{issue.number}</span>
        <div className="button-row issue-triage-detail-actions">
          <a className="icon-button primary" href={issue.html_url} target="_blank" rel="noopener noreferrer">Open/Edit on Github <ExternalLink aria-hidden="true" size={14} /></a>
          <button className="icon-button" type="button" onClick={onClose}>Close</button>
        </div>
        <h3>{issue.title}</h3>
      </header>
      <dl className="issue-triage-meta">
        <div><dt>State</dt><dd><IssueStateValue state={issue.github_state} /></dd></div>
        <div><dt>Group</dt><dd>{issue.derived_group}</dd></div>
        <div><dt>Workflow</dt><dd>{workflowId ? <button className="issue-detail-id-link" type="button" onClick={() => onOpenProjection("run", workflowRunId || workflowId)}>{workflowId}</button> : <IssueStateValue state={issue.workflow?.state ?? "mirrored"} />}</dd></div>
        <div><dt>Task</dt><dd>{issue.workflow?.task_id ? (
          <>
            <button className="issue-detail-id-link" type="button" onClick={() => onOpenTask(issue.workflow?.task_id ?? "")}>{issue.workflow.task_id}</button>
            {issue.workflow.task_history?.some((entry) => entry.task_id !== issue.workflow?.task_id) ? (
              <ul className="issue-triage-history issue-task-history">
                {issue.workflow.task_history
                  .filter((entry) => entry.task_id !== issue.workflow?.task_id)
                  .map((entry) => (
                    <li key={entry.task_id}>
                      <button className="issue-detail-id-link" type="button" onClick={() => onOpenTask(entry.task_id)}>{entry.task_id}</button>
                    </li>
                  ))}
              </ul>
            ) : null}
          </>
        ) : "None"}</dd></div>
        <div><dt>ZaoFu source revision</dt><dd>{issue.workflow?.source_code_revision ? <code>{issue.workflow.source_code_revision}</code> : "Unbound"}</dd></div>
        <div className="issue-triage-detail-triage"><dt>Triage</dt><dd>
          {issue.workflow?.triage_id ? (
            <>
              <button className="issue-detail-id-link" type="button" onClick={() => onOpenProjection("trace", issue.workflow?.triage_id ?? "")}>{issue.workflow.triage_id}</button>
              {issue.workflow.triage_history?.length ? (
                <ul className="issue-triage-history">
                  {issue.workflow.triage_history.map((entry) => (
                    <li key={entry.triage_id}>
                      <button className="issue-detail-id-link" type="button" onClick={() => onOpenProjection("trace", entry.triage_id)}>{entry.triage_id}</button>
                      <span>{issueStateLabel(entry.state)} · attempt {entry.attempt}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : "None"}
        </dd></div>
        <div><dt>Reporter</dt><dd><IssueDetailIdentity login={issue.author_login} avatarUrl={issue.author_avatar_url} /></dd></div>
        <div><dt>Assignees</dt><dd className="issue-detail-identities">{issue.assignees.length
          ? issue.assignees.map((login) => <IssueDetailIdentity avatarUrl={issue.assignee_avatar_urls?.[login]} key={login} login={login} />)
          : "None"}</dd></div>
        <div><dt>Milestone</dt><dd>{issue.milestone || "None"}</dd></div>
        <div><dt>Comments</dt><dd>{issue.comment_count}</dd></div>
        <div className="issue-triage-label-field"><dt>Labels</dt><dd>{issue.labels.length ? (
          <IssueLabelList labels={issue.labels} colors={issue.label_colors} onSelect={onLabelSelect} />
        ) : "None"}</dd></div>
      </dl>
      {issue.workflow?.needs_info ? (
        <NeedsInfoCard
          projectId={projectId}
          issueNumber={issue.number}
          triageId={issue.workflow.triage_id}
          needsInfo={issue.workflow.needs_info}
          onUpdated={onUpdated}
        />
      ) : null}
      {issue.workflow?.state === "unverified_candidate" ? (
        <section className="issue-unverified-candidate-card" role="status">
          <h4>Candidate awaiting maintainer review</h4>
          <p>The Issue and source scope are sufficient to continue, but dynamic verification is incomplete. The reporter's product environment is not required.</p>
          {issue.workflow.verification_gaps?.length ? (
            <ul>{issue.workflow.verification_gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul>
          ) : null}
        </section>
      ) : null}
      <IssueCandidateDeliveryPanel projectId={projectId} issueNumber={issue.number} delivery={detail.delivery} onUpdated={onUpdated} />
      <div className="issue-triage-body">
        <div className="issue-triage-section-heading"><h4>Issue body</h4><span className="muted">GitHub Markdown preview</span></div>
        <div className="issue-triage-markdown">
          <MarkdownText content={githubMarkdownForDisplay(detail.body || "(No description provided.)", projectId)} />
        </div>
      </div>
      <section className="issue-triage-comments">
        <h4>Comments ({detail.comments?.length ?? issue.comment_count})</h4>
        {detail.comments?.map((comment) => <article className="issue-triage-comment" key={comment.id}>
          <header>
            <AuthorIdentity
              login={comment.author_login}
              avatarUrl={comment.author_avatar_url}
              states={authorStates[comment.author_login]}
            />
            <a href={comment.html_url} rel="noopener noreferrer" target="_blank">
              {new Date(comment.created_at).toLocaleString()}
            </a>
          </header>
          <div className="issue-triage-markdown">
            <MarkdownText content={githubMarkdownForDisplay(comment.body || "(Empty comment.)", projectId)} />
          </div>
        </article>)}
        {!detail.comments?.length ? <p className="muted">No comments.</p> : null}
      </section>
    </aside>
  );
}

function NeedsInfoCard({
  projectId,
  issueNumber,
  triageId,
  needsInfo,
  onUpdated,
}: {
  projectId: string;
  issueNumber: number;
  triageId: string;
  needsInfo: NonNullable<NonNullable<IssueTriageItem["workflow"]>["needs_info"]>;
  onUpdated: (notice: string) => Promise<void>;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [authorization, setAuthorization] = useState<{
    transaction_id: string;
    user_code: string;
    verification_uri: string;
    retry_after?: number;
  } | null>(null);
  const [sessionId] = useState(() => (
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `clarification-${Date.now()}`
  ));

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await postAction("external-issue-needs-info-submit", {
        issue_number: issueNumber,
        triage_id: triageId,
        answers,
      }, projectId);
      const record = response.result ?? response;
      if (response.status === "authorization_required" || record.status === "authorization_required") {
        const started = await postAction("external-issue-needs-info-github-device-start", {
          issue_number: issueNumber,
          triage_id: triageId,
          answers,
          session_id: sessionId,
        }, projectId);
        const auth = (started.result ?? started) as Record<string, unknown>;
        if (String(auth.status) !== "authorization_required" || !auth.transaction_id) {
          throw new Error(String(started.reason || "GitHub authorization could not be started"));
        }
        setAuthorization({
          transaction_id: String(auth.transaction_id),
          user_code: String(auth.user_code || ""),
          verification_uri: String(auth.verification_uri || ""),
          retry_after: Number(auth.interval || 5),
        });
        return;
      }
      if (!response.ok) throw new Error(response.reason || "Clarification was not published");
      await onUpdated("Additional information was published to GitHub and a new Triage was submitted.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const pollAuthorization = async () => {
    if (!authorization) return;
    setBusy(true);
    setError("");
    try {
      const response = await postAction("external-issue-needs-info-github-device-poll", {
        transaction_id: authorization.transaction_id,
        session_id: sessionId,
      }, projectId);
      const record = (response.result ?? response) as Record<string, unknown>;
      if (["authorization_pending", "slow_down"].includes(String(record.status))) {
        setAuthorization({ ...authorization, retry_after: Number(record.retry_after || 5) });
      } else if (response.ok && String(record.status).includes("published")) {
        setAuthorization(null);
        await onUpdated("GitHub authorization completed; the information was published and Triage restarted.");
      } else if (!response.ok) {
        throw new Error(response.reason || "GitHub authorization failed");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!authorization) return undefined;
    const timer = window.setTimeout(() => void pollAuthorization(), Math.max(5, authorization.retry_after || 5) * 1000);
    return () => window.clearTimeout(timer);
  }, [authorization]);

  return <section className={`issue-needs-info-card ${needsInfo.status === "needs_info_exhausted" ? "exhausted" : ""}`}>
    <h4>Additional information required</h4>
    <p>{needsInfo.reason}</p>
    {needsInfo.status === "needs_info_exhausted" ? (
      <p className="muted">The maximum of {needsInfo.max_rounds} clarification rounds was reached. Maintainer action is required.</p>
    ) : authorization ? (
      <div className="issue-needs-info-authorization">
        <p>Complete authorization on GitHub. Device code: <strong>{authorization.user_code}</strong></p>
        <a href={authorization.verification_uri} target="_blank" rel="noopener noreferrer">Open GitHub authorization</a>
        <button type="button" disabled={busy} onClick={() => void pollAuthorization()}>Check authorization status</button>
      </div>
    ) : <>
      {needsInfo.questions.map((question) => (
        <label className="issue-needs-info-question" key={question.id}>
          <span>{question.question}{question.required ? " *" : ""}</span>
          {question.type === "single_choice" ? (
            <select value={answers[question.id] || ""} onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))}>
              <option value="">Select an option</option>
              {(question.options || []).map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          ) : question.type === "boolean" ? (
            <select value={answers[question.id] || ""} onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))}>
              <option value="">Select an option</option><option value="Yes">Yes</option><option value="No">No</option>
            </select>
          ) : <textarea rows={question.type === "long_text" ? 3 : 1} value={answers[question.id] || ""} onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))} />}
        </label>
      ))}
      <button className="primary" type="button" disabled={busy} onClick={() => void submit()}>{busy ? "Publishing…" : "Submit information and restart Triage"}</button>
    </>}
    {error ? <div className="issue-triage-error">{error}</div> : null}
  </section>;
}

function IssueDetailIdentity({ login, avatarUrl }: { login: string; avatarUrl?: string }) {
  return <a
    className="issue-detail-identity"
    href={`https://github.com/${encodeURIComponent(login)}`}
    rel="noopener noreferrer"
    target="_blank"
  >
    <img src={githubAvatarUrl(login, avatarUrl)} alt={`${login} avatar`} loading="lazy" referrerPolicy="no-referrer" />
    <span>{login}</span>
  </a>;
}
