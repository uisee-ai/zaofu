import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  AlertTriangle,
  Bell,
  Check,
  CheckCircle2,
  Eye,
  FileText,
  MessageSquare,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { getOperatorInbox, getPlanPreview, postAction } from "../../api/client";
import type { OperatorInboxItem, OperatorInboxProjection, PlanPreview } from "../../api/types";
import { MarkdownText } from "../agent-session/MarkdownText";

interface ContractHealth {
  tasks: Array<{
    task_id: string;
    status: string;
    source_anchor: string;
    rework_attempts: number;
    signals: string[];
  }>;
  summary: { total: number; flagged: number; awaiting_approval: number };
}

type InboxView = "action_required" | "runtime_attention" | "automation" | "all" | "resolved";
type InboxKindFilter = "all" | "plan_approval" | "human_decision" | "runtime_attention" | "approval";

const KIND_OPTIONS: Array<{ value: InboxKindFilter; label: string }> = [
  { value: "all", label: "All types" },
  { value: "plan_approval", label: "Plan Ready" },
  { value: "human_decision", label: "Human Decision" },
  { value: "runtime_attention", label: "Runtime Attention" },
  { value: "approval", label: "Approval" },
];

const VIEW_OPTIONS: Array<{ value: InboxView; label: string }> = [
  { value: "action_required", label: "Action" },
  { value: "runtime_attention", label: "Runtime" },
  { value: "automation", label: "Automation" },
  { value: "all", label: "All" },
  { value: "resolved", label: "Resolved" },
];

export function PlanApprovalPanel(
  { projectId, autoOpenPlanId }: { projectId: string; autoOpenPlanId?: string | null },
) {
  const [inbox, setInbox] = useState<OperatorInboxProjection | null>(null);
  const [health, setHealth] = useState<ContractHealth | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [hasLoadedInbox, setHasLoadedInbox] = useState(false);
  const [inboxRefreshing, setInboxRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [healthError, setHealthError] = useState("");
  const [rejectReason, setRejectReason] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [actionError, setActionError] = useState("");
  const [view, setView] = useState<InboxView>("action_required");
  const [kindFilter, setKindFilter] = useState<InboxKindFilter>("all");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const projectIdRef = useRef(projectId);
  const inboxRefreshInFlight = useRef(false);
  const healthRefreshInFlight = useRef(false);

  const baseItems = useMemo(() => inboxItemsForView(inbox, view), [inbox, view]);
  const filteredItems = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return baseItems.filter((item) => {
      if (kindFilter !== "all" && item.kind !== kindFilter) return false;
      if (!normalizedQuery) return true;
      return itemSearchText(item).includes(normalizedQuery);
    });
  }, [baseItems, kindFilter, query]);
  const selectedItem = useMemo(
    () => filteredItems.find((item) => item.id === selectedId) ?? filteredItems[0] ?? null,
    [filteredItems, selectedId],
  );

  useEffect(() => {
    if (!filteredItems.length) {
      if (selectedId) setSelectedId("");
      return;
    }
    if (!filteredItems.some((item) => item.id === selectedId)) {
      setSelectedId(filteredItems[0].id);
    }
  }, [filteredItems, selectedId]);

  useEffect(() => {
    projectIdRef.current = projectId;
    setInbox(null);
    setHealth(null);
    setHasLoadedInbox(false);
    setLoadError("");
    setHealthError("");
    inboxRefreshInFlight.current = false;
    healthRefreshInFlight.current = false;
  }, [projectId]);

  const refreshInbox = useCallback(async () => {
    if (inboxRefreshInFlight.current) return;
    inboxRefreshInFlight.current = true;
    setInboxRefreshing(true);
    setLoadError("");
    const requestProjectId = projectId;
    try {
      const inboxData = await getOperatorInbox(projectId || undefined);
      if (projectIdRef.current === requestProjectId) {
        setInbox(inboxData);
      }
    } catch (error) {
      if (projectIdRef.current === requestProjectId) {
        setLoadError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (projectIdRef.current === requestProjectId) {
        setHasLoadedInbox(true);
        setInboxRefreshing(false);
      }
      inboxRefreshInFlight.current = false;
    }
  }, [projectId]);

  const refreshHealth = useCallback(async () => {
    if (healthRefreshInFlight.current) return;
    healthRefreshInFlight.current = true;
    setHealthError("");
    const requestProjectId = projectId;
    try {
      const base = `/api/projects/${encodeURIComponent(projectId)}`;
      const healthRes = await fetch(`${base}/contract-health`);
      if (projectIdRef.current !== requestProjectId) {
        return;
      }
      if (healthRes.ok) {
        setHealth(await healthRes.json());
      } else {
        setHealthError(`contract-health returned ${healthRes.status}`);
      }
    } catch (error) {
      if (projectIdRef.current === requestProjectId) {
        setHealthError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      healthRefreshInFlight.current = false;
    }
  }, [projectId]);

  const refresh = useCallback(async () => {
    void refreshHealth();
    await refreshInbox();
  }, [refreshHealth, refreshInbox]);

  useEffect(() => {
    void refreshInbox();
    void refreshHealth();
    const inboxTimer = setInterval(() => void refreshInbox(), 15000);
    const healthTimer = setInterval(() => void refreshHealth(), 60000);
    return () => {
      clearInterval(inboxTimer);
      clearInterval(healthTimer);
    };
  }, [refreshHealth, refreshInbox]);

  const openPreview = async (planId: string) => {
    setPreviewLoading(true);
    setActionError("");
    try {
      setPreview(await getPlanPreview(planId, projectId || undefined));
    } catch (error) {
      setActionError(String(error instanceof Error ? error.message : error));
    } finally {
      setPreviewLoading(false);
    }
  };

  // Deep link `/?page=inbox&plan=<id>` (feishu Plan Ready card) → auto-open the
  // preview for that plan once on mount.
  useEffect(() => {
    if (autoOpenPlanId) void openPreview(autoOpenPlanId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoOpenPlanId]);

  const act = async (action: "plan-approve" | "plan-reject", planId: string) => {
    setBusy(`${action}:${planId}`);
    setActionError("");
    try {
      const payload: Record<string, unknown> = { plan_id: planId };
      if (action === "plan-reject") payload.reason = rejectReason[planId] || "";
      const result = await postAction(action, payload, projectId || undefined);
      if (result.ok === false) setActionError(result.reason || `${action} failed`);
      await refresh();
      if (preview?.plan_id === planId) {
        setPreview(await getPlanPreview(planId, projectId || undefined));
      }
    } finally {
      setBusy("");
    }
  };

  const repairChat = async (plan: OperatorInboxItem) => {
    const planId = String(plan.plan_id || "");
    if (!planId) return;
    setBusy(`repair:${planId}`);
    setActionError("");
    try {
      const result = await postAction("chat-orchestrator", {
        message: `plan ${planId} repair: ${rejectReason[planId] || "review question"}`,
        plan_id: planId,
        source: "operator-inbox",
        intent_type: "plan_repair",
      }, projectId || undefined);
      if (result.ok === false) setActionError(result.reason || "repair chat failed");
    } finally {
      setBusy("");
    }
  };

  const actOnItem = async (item: OperatorInboxItem, action: string) => {
    if (!action) return;
    setBusy(`${action}:${item.id}`);
    setActionError("");
    try {
      const result = await postAction(action, inboxActionPayload(item), projectId || undefined);
      if (result.ok === false) setActionError(result.reason || `${action} failed`);
      await refresh();
    } finally {
      setBusy("");
    }
  };

  const moveSelection = useCallback((direction: 1 | -1) => {
    if (!filteredItems.length) return;
    const currentIndex = Math.max(0, filteredItems.findIndex((item) => item.id === (selectedItem?.id || selectedId)));
    const nextIndex = (currentIndex + direction + filteredItems.length) % filteredItems.length;
    setSelectedId(filteredItems[nextIndex].id);
  }, [filteredItems, selectedId, selectedItem?.id]);

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const target = event.target as HTMLElement | null;
    if (target?.tagName === "INPUT" || target?.tagName === "SELECT" || target?.tagName === "TEXTAREA") return;
    if (event.key === "j" || event.key === "ArrowDown") {
      event.preventDefault();
      moveSelection(1);
      return;
    }
    if (event.key === "k" || event.key === "ArrowUp") {
      event.preventDefault();
      moveSelection(-1);
      return;
    }
    if (event.key === "Enter" && selectedItem?.kind === "plan_approval" && selectedItem.plan_id && selectedItem.preview?.available) {
      event.preventDefault();
      void openPreview(selectedItem.plan_id);
    }
  };

  const initialLoading = !hasLoadedInbox && !inbox;
  const pendingCount = inbox?.summary.action_required_pending ?? inbox?.pending.filter((item) => item.actionability === "human_required").length ?? 0;
  const diagnosticCount = inbox?.summary.noise_pending ?? inbox?.pending.filter((item) => item.actionability !== "human_required").length ?? 0;
  const totalCount = inbox?.items.length ?? 0;

  return (
    <section
      className="panel plan-approval-panel operator-inbox-panel"
      data-testid="plan-approval-panel"
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      <div className="plan-approval-head operator-inbox-head">
        <div>
          <h3>Inbox</h3>
          <span className="muted">
            {initialLoading ? "loading" : pendingCount ? `${pendingCount} action required` : "clear"}
            {diagnosticCount ? ` / ${diagnosticCount} diagnostics` : ""}
            {view === "all" && totalCount ? ` / ${totalCount} total` : ""}
          </span>
        </div>
        <div className="operator-inbox-tools">
          <label className="operator-inbox-search">
            <Search size={14} aria-hidden="true" />
            <input
              value={query}
              aria-label="Search inbox"
              placeholder="Search"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <label className="operator-inbox-filter">
            <SlidersHorizontal size={14} aria-hidden="true" />
            <select
              value={kindFilter}
              aria-label="Inbox kind"
              onChange={(event) => setKindFilter(event.target.value as InboxKindFilter)}
            >
              {KIND_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <div className="operator-inbox-mode" aria-label="Inbox display view">
            {VIEW_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={view === option.value ? "active" : ""}
                onClick={() => setView(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="icon-button"
            title={inboxRefreshing ? "Refreshing" : "Refresh"}
            onClick={() => void refresh()}
            disabled={inboxRefreshing}
          >
            <RefreshCw size={15} />
          </button>
        </div>
      </div>

      {actionError ? <p className="error plan-approval-error">{actionError}</p> : null}
      {loadError ? <p className="error plan-approval-error">Inbox refresh failed: {loadError}</p> : null}
      {healthError ? <p className="muted plan-approval-error">Contract health refresh failed: {healthError}</p> : null}

      {initialLoading ? (
        <p className="muted plan-approval-empty operator-inbox-empty">Loading inbox...</p>
      ) : loadError && !inbox ? null : filteredItems.length === 0 ? (
        <p className="muted plan-approval-empty operator-inbox-empty">
          {query || kindFilter !== "all" ? "No matching inbox items." : "No inbox items in this view."}
        </p>
      ) : (
        <div className="operator-inbox-list" role="list" aria-label="Operator inbox items">
          {(() => {
            const renderRow = (item: OperatorInboxItem) => (
              <InboxRow
                key={item.id}
                item={item}
                selected={item.id === selectedItem?.id}
                busy={busy}
                rejectReason={rejectReason[inboxItemReasonKey(item)] || ""}
                onRejectReason={(value) => {
                  const reasonKey = inboxItemReasonKey(item);
                  setRejectReason((current) => ({ ...current, [reasonKey]: value }));
                }}
                onSelect={() => setSelectedId(item.id)}
                onPreview={() => item.plan_id && void openPreview(String(item.plan_id))}
                onApprove={() => item.plan_id && void act("plan-approve", String(item.plan_id))}
                onReject={() => item.plan_id && void act("plan-reject", String(item.plan_id))}
                onRepair={() => void repairChat(item)}
                onAction={(action) => void actOnItem(item, action)}
              />
            );
            // Human decisions stay individual and pinned first; the rest
            // groups by (kind, title) — 210 identical attention rows buried
            // the 15 real decisions on the r2 walk.
            const isDecision = (item: OperatorInboxItem) =>
              item.kind === "plan_approval" || item.kind === "human_decision";
            const decisions = filteredItems.filter(isDecision);
            const rest = filteredItems.filter((item) => !isDecision(item));
            const groups = new Map<string, OperatorInboxItem[]>();
            for (const item of rest) {
              const key = `${item.kind}::${item.title || item.summary || item.id}`;
              const bucket = groups.get(key);
              if (bucket) bucket.push(item);
              else groups.set(key, [item]);
            }
            const ackAll = (items: OperatorInboxItem[]) => {
              for (const entry of items) {
                const action = (entry.actions ?? []).find((candidate) =>
                  String(candidate.action || "").includes("ack"));
                if (action?.action) void actOnItem(entry, String(action.action));
              }
            };
            return (
              <>
                {decisions.length > 1 ? (
                  <div className="operator-inbox-inline-actions">
                    <span className="muted">{decisions.length} pending decisions</span>
                    <button
                      type="button"
                      className="delivery-action-button"
                      disabled={Boolean(busy)}
                      onClick={() => { for (const entry of decisions) void actOnItem(entry, "human-decision-dismiss"); }}
                    >
                      Dismiss all
                    </button>
                  </div>
                ) : null}
                {decisions.map(renderRow)}
                {[...groups.entries()].map(([key, items]) => (
                  items.length === 1 ? renderRow(items[0]) : (
                    <details className="operator-inbox-group" key={key}>
                      <summary>
                        <span className="operator-inbox-group-title">{items[0].title || items[0].summary || items[0].kind}</span>
                        <span className="muted"> ×{items.length} · {items[0].kind}</span>
                        {(items[0].actions ?? []).some((candidate) => String(candidate.action || "").includes("ack")) ? (
                          <button
                            type="button"
                            className="delivery-action-button"
                            disabled={Boolean(busy)}
                            onClick={(event) => { event.preventDefault(); event.stopPropagation(); ackAll(items); }}
                          >
                            Ack all
                          </button>
                        ) : null}
                      </summary>
                      {items.map(renderRow)}
                    </details>
                  )
                ))}
              </>
            );
          })()}
        </div>
      )}

      {/* Zero flagged contracts is silence, not information — render only
          when something needs an operator (flagged / awaiting approval). */}
      {health && (health.summary.flagged > 0 || (health.summary.awaiting_approval ?? 0) > 0) ? (
        <details className="plan-contract-health operator-inbox-health" open>
          <summary>
            Contract health: {health.summary.flagged}/{health.summary.total}
            {health.summary.awaiting_approval ? `, ${health.summary.awaiting_approval} awaiting` : ""}
          </summary>
          <table>
            <thead>
              <tr>
                <th>task</th>
                <th>status</th>
                <th>source</th>
                <th>rework</th>
                <th>signals</th>
              </tr>
            </thead>
            <tbody>
              {health.tasks.map((task) => (
                <tr key={task.task_id}>
                  <td>{task.task_id}</td>
                  <td>{task.status}</td>
                  <td>{task.source_anchor}</td>
                  <td>{task.rework_attempts}</td>
                  <td>{task.signals.join(", ") || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}

      {preview || previewLoading ? (
        <PlanPreviewOverlay
          preview={preview}
          loading={previewLoading}
          busy={busy}
          rejectReason={preview ? rejectReason[preview.plan_id] || "" : ""}
          onRejectReason={(value) => {
            if (!preview) return;
            setRejectReason((current) => ({ ...current, [preview.plan_id]: value }));
          }}
          onClose={() => setPreview(null)}
          onApprove={() => preview && void act("plan-approve", preview.plan_id)}
          onReject={() => preview && void act("plan-reject", preview.plan_id)}
          onRepair={() => preview && void repairChat({ id: preview.plan_id, kind: "plan_approval", status: preview.status, title: "Plan Ready", plan_id: preview.plan_id })}
        />
      ) : null}
    </section>
  );
}

function InboxRow({
  item,
  selected,
  busy,
  rejectReason,
  onRejectReason,
  onSelect,
  onPreview,
  onApprove,
  onReject,
  onRepair,
  onAction,
}: {
  item: OperatorInboxItem;
  selected: boolean;
  busy: string;
  rejectReason: string;
  onRejectReason: (value: string) => void;
  onSelect: () => void;
  onPreview: () => void;
  onApprove: () => void;
  onReject: () => void;
  onRepair: () => void;
  onAction: (action: string) => void;
}) {
  const Icon = kindIcon(item.kind);
  const disabled = busy.endsWith(`:${inboxItemActionKey(item)}`);
  return (
    <article
      className={`operator-inbox-row ${selected ? "selected" : ""} ${item.status === "pending" ? "pending" : "resolved"}`}
      data-testid={item.kind === "plan_approval" ? "approval-card" : "operator-inbox-row"}
      role="listitem"
      onClick={onSelect}
    >
      <div className="operator-inbox-row-grid">
        <div className="operator-inbox-avatar" aria-hidden="true">
          <Icon size={17} />
          {item.status === "pending" ? <span className="operator-inbox-unread-dot" /> : null}
        </div>
        <div className="operator-inbox-row-main">
          <div className="operator-inbox-title-line">
            <span className="operator-inbox-kind">{kindLabel(item.kind)}</span>
            <strong className="operator-inbox-title">{item.title || kindLabel(item.kind)}</strong>
            <span className={`badge ${item.status === "pending" ? "badge-warn" : ""}`}>{item.status}</span>
            <time className="muted">{compactTime(item.created_ts || item.resolved_ts)}</time>
          </div>
          <p>{item.summary || inboxItemPrimaryRef(item) || "Operator action requested."}</p>
          <div className="operator-inbox-meta-line">
            {inboxItemPrimaryRef(item) ? <span className="mono">{inboxItemPrimaryRef(item)}</span> : null}
            {item.stage_id ? <span>stage {item.stage_id}</span> : null}
            {typeof item.task_count === "number" ? <span>{item.task_count} tasks</span> : null}
            {item.trace_id ? <span>trace {item.trace_id}</span> : null}
            {item.checkpoint_id ? <span>checkpoint {item.checkpoint_id}</span> : null}
            {item.category ? <span>{categoryLabel(item.category)}</span> : null}
            {item.source_role ? <span>{sourceLabel(item.source_role)}</span> : null}
            {Number(item.dedupe_count || 0) > 1 ? <span>{item.dedupe_count} grouped</span> : null}
          </div>
        </div>
      </div>
      {selected ? (
        <InboxRowActions
          item={item}
          disabled={disabled}
          rejectReason={rejectReason}
          onRejectReason={onRejectReason}
          onPreview={onPreview}
          onApprove={onApprove}
          onReject={onReject}
          onRepair={onRepair}
          onAction={onAction}
        />
      ) : null}
    </article>
  );
}

function InboxRowActions({
  item,
  disabled,
  rejectReason,
  onRejectReason,
  onPreview,
  onApprove,
  onReject,
  onRepair,
  onAction,
}: {
  item: OperatorInboxItem;
  disabled: boolean;
  rejectReason: string;
  onRejectReason: (value: string) => void;
  onPreview: () => void;
  onApprove: () => void;
  onReject: () => void;
  onRepair: () => void;
  onAction: (action: string) => void;
}) {
  if (item.kind === "plan_approval") {
    return (
      <div className="operator-inbox-inline-actions" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="delivery-action-button" onClick={onPreview} disabled={!item.preview?.available}>
          <Eye size={14} /> Preview
        </button>
        <button type="button" className="delivery-action-button" onClick={onApprove} disabled={disabled}>
          <Check size={14} /> Approve
        </button>
        <input
          className="operator-inbox-reject-input"
          value={rejectReason}
          placeholder="reject reason"
          onChange={(event) => onRejectReason(event.target.value)}
        />
        <button type="button" className="delivery-action-button" onClick={onReject} disabled={disabled || !rejectReason.trim()}>
          <X size={14} /> Reject
        </button>
        <button type="button" className="delivery-action-button" onClick={onRepair} disabled={disabled}>
          <MessageSquare size={14} /> Repair
        </button>
      </div>
    );
  }

  const actions = (item.actions ?? [])
    .map((action) => ({
      action: typeof action.action === "string" ? action.action : "",
      label: typeof action.label === "string" ? action.label : "",
    }))
    .filter((action) => action.action);
  if (!actions.length) return null;

  return (
    <div className="operator-inbox-inline-actions" onClick={(event) => event.stopPropagation()}>
      {actions.map((action) => (
        <button
          key={action.action}
          type="button"
          className="delivery-action-button"
          onClick={() => onAction(action.action)}
          disabled={disabled}
        >
          <MessageSquare size={14} /> {action.label || action.action}
        </button>
      ))}
    </div>
  );
}

function PlanPreviewOverlay({
  preview,
  loading,
  busy,
  rejectReason,
  onRejectReason,
  onClose,
  onApprove,
  onReject,
  onRepair,
}: {
  preview: PlanPreview | null;
  loading: boolean;
  busy: string;
  rejectReason: string;
  onRejectReason: (value: string) => void;
  onClose: () => void;
  onApprove: () => void;
  onReject: () => void;
  onRepair: () => void;
}) {
  const planId = preview?.plan_id || "";
  const disabled = Boolean(planId && busy.endsWith(`:${planId}`));
  return (
    <div className="plan-preview-overlay" role="dialog" aria-modal="true" aria-label="Plan preview">
      <div className="plan-preview-toolbar">
        <div>
          <strong>Plan Preview</strong>
          <span className="mono">{planId || "loading"}</span>
        </div>
        <div className="plan-preview-toolbar-actions">
          <button type="button" className="delivery-action-button" onClick={onApprove} disabled={!preview || disabled}>
            <Check size={14} /> Approve
          </button>
          <input
            value={rejectReason}
            placeholder="reject reason"
            onChange={(event) => onRejectReason(event.target.value)}
            disabled={!preview}
          />
          <button type="button" className="delivery-action-button" onClick={onReject} disabled={!preview || disabled || !rejectReason.trim()}>
            <X size={14} /> Reject
          </button>
          <button type="button" className="delivery-action-button" onClick={onRepair} disabled={!preview || disabled}>
            <MessageSquare size={14} /> Repair
          </button>
          <button type="button" className="icon-button" title="Close preview" onClick={onClose}>
            <X size={16} />
          </button>
        </div>
      </div>
      <div className="plan-preview-body">
        <main className="plan-preview-markdown-pane">
          {loading ? <p className="muted">Loading...</p> : <MarkdownText className="plan-preview-markdown" content={preview?.markdown || ""} />}
        </main>
        <aside className="plan-preview-context">
          <dl className="plan-approval-meta">
            <div><dt>status</dt><dd>{preview?.status || "-"}</dd></div>
            <div><dt>stage</dt><dd>{preview?.stage_id || "-"}</dd></div>
            <div><dt>tasks</dt><dd>{preview?.task_count ?? preview?.task_map_summary?.task_count ?? "-"}</dd></div>
            <div><dt>trace</dt><dd>{preview?.trace_id || "-"}</dd></div>
          </dl>
          <pre className="delivery-raw-block">{JSON.stringify(preview?.refs ?? {}, null, 2)}</pre>
        </aside>
      </div>
    </div>
  );
}

function itemSearchText(item: OperatorInboxItem): string {
  return [
    item.id,
    item.kind,
    item.category,
    item.actionability,
    item.source_role,
    item.owner_route,
    item.group_key,
    item.status,
    item.title,
    item.summary,
    item.approval_ref,
    item.plan_id,
    item.stage_id,
    item.trace_id,
    item.pdd_id,
    item.decision_token,
    item.checkpoint_id,
    item.fingerprint,
    item.attention_id,
  ].filter(Boolean).join(" ").toLowerCase();
}

function inboxItemReasonKey(item: OperatorInboxItem): string {
  return String(item.plan_id || item.approval_ref || item.id);
}

function inboxItemActionKey(item: OperatorInboxItem): string {
  return String(item.plan_id || item.id);
}

function inboxItemPrimaryRef(item: OperatorInboxItem): string {
  return String(item.plan_id || item.decision_token || item.attention_id || item.approval_ref || item.id || "");
}

function inboxItemsForView(inbox: OperatorInboxProjection | null, view: InboxView): OperatorInboxItem[] {
  const items = inbox?.items ?? [];
  if (view === "all") return items;
  if (view === "resolved") return items.filter((item) => item.status !== "pending" || item.category === "resolved");
  if (view === "action_required") {
    return items.filter((item) => item.status === "pending" && item.actionability === "human_required");
  }
  if (view === "runtime_attention") {
    return items.filter((item) => item.status === "pending" && item.category === "runtime_attention");
  }
  return items.filter((item) => item.status === "pending" && item.category === "automation_diagnostic");
}

function inboxActionPayload(item: OperatorInboxItem): Record<string, unknown> {
  return {
    source: "operator-inbox",
    approval_ref: item.approval_ref,
    plan_id: item.plan_id,
    decision_token: item.decision_token,
    checkpoint_id: item.checkpoint_id,
    fingerprint: item.fingerprint,
    attention_id: item.attention_id,
    created_event_id: item.created_event_id,
  };
}

function kindLabel(kind: string): string {
  if (kind === "plan_approval") return "Plan Ready";
  if (kind === "human_decision") return "Human Decision";
  if (kind === "runtime_attention") return "Runtime Attention";
  if (kind === "approval") return "Approval";
  return kind.replace(/_/g, " ");
}

function categoryLabel(category: string): string {
  if (category === "action_required") return "Action required";
  if (category === "automation_diagnostic") return "Automation";
  if (category === "runtime_attention") return "Runtime";
  if (category === "notification") return "Notification";
  if (category === "resolved") return "Resolved";
  return category.replace(/_/g, " ");
}

function sourceLabel(source: string): string {
  if (source === "run_manager") return "Run Manager";
  if (source === "autoresearch") return "Autoresearch";
  if (source === "supervisor") return "Supervisor";
  if (source === "orchestrator") return "Orchestrator";
  if (source === "worker") return "Worker";
  return source.replace(/_/g, " ");
}

function kindIcon(kind: string): LucideIcon {
  if (kind === "plan_approval") return FileText;
  if (kind === "human_decision") return ShieldCheck;
  if (kind === "runtime_attention") return AlertTriangle;
  if (kind === "approval") return CheckCircle2;
  return Bell;
}

function compactTime(value?: string): string {
  if (!value) return "-";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  const deltaMs = Date.now() - parsed;
  if (deltaMs < 0) return new Date(parsed).toLocaleString();
  const minutes = Math.floor(deltaMs / 60000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return new Date(parsed).toLocaleDateString();
}
