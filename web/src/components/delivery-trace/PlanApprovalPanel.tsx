import { PlanPreviewOverlay } from "./PlanPreviewOverlay";
import { InboxQueueItem } from "./InboxQueueItem";
import { useUiPreferences } from "../../app/UiPreferences";
import { formatDateTime } from "../../lib/uiPreferences";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  ArrowLeft,
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
import { LatestRequestGate } from "../../app/latestRequestGate";
import { resolvePlanPreviewItem } from "./planReviewModel";



type InboxView = "decisions" | "runtime_attention" | "automation" | "notification" | "all" | "resolved";
type InboxKindFilter = "all" | "plan_approval" | "human_decision" | "channel_decision" | "kanban_question" | "runtime_attention" | "approval" | "run_delivery";

const KIND_OPTIONS: Array<{ value: InboxKindFilter; label: string }> = [
  { value: "all", label: "All types" },
  { value: "plan_approval", label: "Plan Ready" },
  { value: "human_decision", label: "Human Decision" },
  { value: "channel_decision", label: "Channel Decision" },
  { value: "kanban_question", label: "Kanban Question" },
  { value: "runtime_attention", label: "Runtime Attention" },
  { value: "approval", label: "Approval" },
  { value: "run_delivery", label: "Run Delivery" },
];

const VIEW_OPTIONS: Array<{ value: InboxView; label: string }> = [
  { value: "decisions", label: "Decisions" },
  { value: "runtime_attention", label: "Runtime" },
  { value: "automation", label: "Automation" },
  { value: "notification", label: "Delivery" },
  { value: "all", label: "All" },
  { value: "resolved", label: "Resolved" },
];

export function PlanApprovalPanel(
  { projectId, autoOpenPlanId }: { projectId: string; autoOpenPlanId?: string | null },
) {
  const { t } = useUiPreferences();
  const [mobileDetail, setMobileDetail] = useState(false);
  const queueFocus = useRef<HTMLElement | null>(null);
  const detailPane = useRef<HTMLElement | null>(null);
  const [inbox, setInbox] = useState<OperatorInboxProjection | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [hasLoadedInbox, setHasLoadedInbox] = useState(false);
  const [inboxRefreshing, setInboxRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [rejectReason, setRejectReason] = useState<Record<string, string>>({});
  const [repairMessage, setRepairMessage] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [previewItem, setPreviewItem] = useState<OperatorInboxItem | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [actionError, setActionError] = useState("");
  const [actionNotice, setActionNotice] = useState("");
  const [view, setView] = useState<InboxView>("decisions");
  const [kindFilter, setKindFilter] = useState<InboxKindFilter>("all");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const projectIdRef = useRef(projectId);
  const inboxRefreshInFlight = useRef(false);
  const previewRequestGate = useRef(new LatestRequestGate());
  const currentPreviewItem = resolvePlanPreviewItem(preview, inbox?.items || [], previewItem);
  const previewOwnerDecision = preview?.actions?.approve === "replan-approve"
    || currentPreviewItem?.owner_route === "plan_synth_owner_decision";

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

  useEffect(() => { if (detailPane.current) detailPane.current.scrollTop = 0; }, [selectedItem?.id]);
  useEffect(() => { setMobileDetail(false); }, [view, kindFilter, query]);

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
    setMobileDetail(false);
    previewRequestGate.current.invalidate();
    setPreview(null);
    setPreviewItem(null);
    setPreviewLoading(false);
    setInbox(null);
    setHasLoadedInbox(false);
    setLoadError("");
    inboxRefreshInFlight.current = false;
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

  const refresh = useCallback(async () => {
    await refreshInbox();
  }, [refreshInbox]);

  useEffect(() => {
    void refreshInbox();
    const inboxTimer = setInterval(() => void refreshInbox(), 15000);
    return () => {
      clearInterval(inboxTimer);
    };
  }, [refreshInbox]);

  const openPreview = async (planId: string, item?: OperatorInboxItem) => {
    const ticket = previewRequestGate.current.issue();
    setPreview(null);
    setPreviewItem(item || null);
    setPreviewLoading(true);
    setActionError("");
    setActionNotice("");
    try {
      const result = await getPlanPreview(planId, projectId || undefined);
      if (previewRequestGate.current.isCurrent(ticket) && projectIdRef.current === projectId) setPreview(result);
    } catch (error) {
      if (previewRequestGate.current.isCurrent(ticket)) setActionError(String(error instanceof Error ? error.message : error));
    } finally {
      if (previewRequestGate.current.isCurrent(ticket)) setPreviewLoading(false);
    }
  };

  // Deep link `/?page=inbox&plan=<id>` (feishu Plan Ready card) → auto-open the
  // preview for that plan once on mount.
  useEffect(() => {
    if (autoOpenPlanId) void openPreview(autoOpenPlanId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => previewRequestGate.current.invalidate();
  }, [autoOpenPlanId, projectId]);

  const act = async (action: "plan-approve" | "plan-reject", planId: string) => {
    setBusy(`${action}:${planId}`);
    setActionError("");
    setActionNotice("");
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
    setActionNotice("");
    try {
      const result = await postAction("chat-orchestrator", {
        message: repairMessage[planId] || rejectReason[planId] || "请解释该 Plan 并提出需要修改的地方。",
        plan_id: planId,
        source: "operator-inbox",
        intent_type: "plan_repair",
        plan_context: preview?.plan_id === planId ? {
          plan_id: preview.plan_id,
          trace_id: preview.trace_id,
          stage_id: preview.stage_id,
          pdd_id: preview.pdd_id,
          task_map_ref: preview.refs.task_map_ref,
          source_index_ref: preview.refs.source_index_ref,
          requirement_spec_ref: preview.refs.requirement_spec_ref,
        } : undefined,
      }, projectId || undefined);
      if (result.ok === false) setActionError(result.reason || "repair chat failed");
      else setActionNotice("修改请求已提交给 Kanban Agent；Plan 仍保持待审批，等待新的候选版本。");
    } finally {
      setBusy("");
    }
  };

  const actOnItem = async (item: OperatorInboxItem, action: string, reason = "") => {
    if (!action) return;
    setBusy(`${action}:${item.id}`);
    setActionError("");
    setActionNotice("");
    try {
      const result = await postAction(action, inboxActionPayload(item, reason), projectId || undefined);
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
    if (!target?.closest("button, a, summary") && event.key === "Enter" && selectedItem?.kind === "plan_approval" && selectedItem.plan_id && selectedItem.preview?.available) {
      event.preventDefault();
      void openPreview(selectedItem.plan_id);
    }
  };

  const initialLoading = !hasLoadedInbox && !inbox;
  const pendingCount = inbox?.summary.action_required_pending ?? inbox?.pending.filter((item) => item.actionability === "human_required").length ?? 0;
  const diagnosticCount = inbox?.summary.noise_pending ?? inbox?.pending.filter((item) => item.actionability === "automation_owned").length ?? 0;
  const deliveryCount = inbox?.summary.notification_pending ?? inbox?.pending.filter((item) => item.actionability === "informational").length ?? 0;
  const totalCount = inbox?.items.length ?? 0;

  const renderRow = (item: OperatorInboxItem, detail = false) => (
    <InboxRow
      key={item.id}
      item={item}
      detail={detail}
      projectId={projectId}
      selected={item.id === selectedItem?.id}
      busy={busy}
      rejectReason={rejectReason[inboxItemReasonKey(item)] || ""}
      onRejectReason={(value) => {
        const reasonKey = inboxItemReasonKey(item);
        setRejectReason((current) => ({ ...current, [reasonKey]: value }));
      }}
      onSelect={() => { queueFocus.current = document.activeElement as HTMLElement; setSelectedId(item.id); setMobileDetail(true); if (window.matchMedia("(max-width: 760px)").matches) requestAnimationFrame(() => detailPane.current?.focus()); }}
      onPreview={() => item.plan_id && void openPreview(String(item.plan_id), item)}
      onApprove={() => item.plan_id && void act("plan-approve", String(item.plan_id))}
      onReject={() => item.plan_id && void act("plan-reject", String(item.plan_id))}
      onRepair={() => void repairChat(item)}
      onAction={(action) => void actOnItem(item, action)}
    />
  );

  return (
    <section
      className={`panel plan-approval-panel operator-inbox-panel ${mobileDetail ? "inbox-show-detail" : ""}`}
      data-testid="plan-approval-panel"
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      <div className="inbox-workspace-grid">
      <div className="inbox-queue">
      <div className="plan-approval-head operator-inbox-head">
        <div>
          <h3>{t("Inbox")}</h3>
          <span className="muted">
            {initialLoading ? t("loading") : pendingCount ? `${pendingCount} ${t("pending decisions")}` : t("clear")}
            {diagnosticCount ? ` / ${diagnosticCount} ${t("diagnostics")}` : ""}
            {deliveryCount ? ` / ${deliveryCount} ${t("deliveries")}` : ""}
            {view === "all" && totalCount ? ` / ${totalCount} ${t("total")}` : ""}
          </span>
        </div>
        <div className="operator-inbox-tools">
          <label className="operator-inbox-search">
            <Search size={14} aria-hidden="true" />
            <input
              value={query}
              aria-label={t("Search inbox")}
              placeholder={t("Search")}
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
                <option key={option.value} value={option.value}>{t(option.label)}</option>
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
                {t(option.label)}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="icon-button"
            title={t(inboxRefreshing ? "Refreshing" : "Refresh")}
            onClick={() => void refresh()}
            disabled={inboxRefreshing}
          >
            <RefreshCw size={15} />
          </button>
        </div>
      </div>

      {actionError ? <p className="error plan-approval-error">{actionError}</p> : null}
      {actionNotice ? <p className="muted plan-approval-notice">{actionNotice}</p> : null}
      {loadError ? <p className="error plan-approval-error">Inbox refresh failed: {loadError}</p> : null}

      {initialLoading ? (
        <p className="muted plan-approval-empty operator-inbox-empty">{t("Loading inbox...")}</p>
      ) : loadError && !inbox ? null : filteredItems.length === 0 ? (
        <p className="muted plan-approval-empty operator-inbox-empty">
          {t(query || kindFilter !== "all" ? "No matching inbox items." : "No inbox items in this view.")}
        </p>
      ) : (
        <div className="operator-inbox-list" role="list" aria-label="Operator inbox items">
          {(() => {
            // Human decisions stay individual and pinned first; the rest
            // groups by (kind, title) — 210 identical attention rows buried
            // the 15 real decisions on the r2 walk.
            const isDecision = (item: OperatorInboxItem) =>
              item.actionability === "human_required";
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
                    <span className="muted">{decisions.length} {t("pending decisions")}</span>
                  </div>
                ) : null}
                {decisions.map((item) => renderRow(item))}
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
                            {t("Ack all")}
                          </button>
                        ) : null}
                      </summary>
                      {items.map((item) => renderRow(item))}
                    </details>
                  )
                ))}
              </>
            );
          })()}
        </div>
      )}

      </div>
      <aside className="inbox-reading-pane" ref={detailPane} tabIndex={-1} aria-label={t("Inbox")}>
        <button type="button" className="workbench-control inbox-back" onClick={() => { setMobileDetail(false); requestAnimationFrame(() => queueFocus.current?.focus()); }}><ArrowLeft size={15} />{t("Back to inbox")}</button>
        {selectedItem ? renderRow(selectedItem, true) : <div className="inbox-reader-empty"><Bell size={26} /><p>{t("Select an item to read its details.")}</p></div>}
      </aside>
      </div>
      {preview || previewLoading ? (
        <PlanPreviewOverlay
          projectId={projectId}
          preview={preview}
          loading={previewLoading}
          busy={busy}
          rejectReason={preview ? rejectReason[preview.plan_id] || "" : ""}
          onRejectReason={(value) => {
            if (!preview) return;
            setRejectReason((current) => ({ ...current, [preview.plan_id]: value }));
          }}
          repairMessage={preview ? repairMessage[preview.plan_id] || "" : ""}
          onRepairMessage={(value) => {
            if (!preview) return;
            setRepairMessage((current) => ({ ...current, [preview.plan_id]: value }));
          }}
          onClose={() => {
            previewRequestGate.current.invalidate();
            setPreviewLoading(false);
            setPreview(null);
            setPreviewItem(null);
          }}
          onApprove={() => {
            if (!preview) return;
            if (previewOwnerDecision) {
              if (currentPreviewItem) void actOnItem(currentPreviewItem, "replan-approve");
            } else {
              void act("plan-approve", preview.plan_id);
            }
          }}
          onReject={() => {
            if (!preview) return;
            if (previewOwnerDecision) {
              if (currentPreviewItem) void actOnItem(currentPreviewItem, "replan-reject", rejectReason[preview.plan_id] || "");
            } else {
              void act("plan-reject", preview.plan_id);
            }
          }}
          onRepair={() => preview && void repairChat(currentPreviewItem || { id: preview.plan_id, kind: "plan_approval", status: preview.status, title: "Plan Ready", plan_id: preview.plan_id })}
          ownerDecision={previewOwnerDecision}
          decisionReady={!previewOwnerDecision || Boolean(currentPreviewItem?.proposal_ref && currentPreviewItem?.eval_ref)}
          actionKey={currentPreviewItem?.id || preview?.plan_id || ""}
        />
      ) : null}
    </section>
  );
}

function InboxRow({
  item,
  detail,
  projectId,
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
  detail: boolean;
  projectId: string;
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
  const { t } = useUiPreferences();
  const Icon = kindIcon(item.kind);
  const disabled = busy.endsWith(`:${inboxItemActionKey(item)}`);
  if (!detail) return <InboxQueueItem item={item} selected={selected} kind={t(kindLabel(item.kind))} primaryRef={inboxItemPrimaryRef(item)} onSelect={onSelect} />;
  return (
    <article
      className={`operator-inbox-row inbox-reading-card ${selected ? "selected" : ""} ${item.status === "pending" ? "pending" : "resolved"}`}
      data-testid={item.kind === "plan_approval" ? "approval-card" : "operator-inbox-row"}
      role="listitem"
    >
      <div className="operator-inbox-row-grid">
        <div className="operator-inbox-avatar" aria-hidden="true">
          <Icon size={17} />
          {item.status === "pending" ? <span className="operator-inbox-unread-dot" /> : null}
        </div>
        <div className="operator-inbox-row-main">
          <div className="operator-inbox-title-line">
            <span className="operator-inbox-kind">{t(kindLabel(item.kind))}</span>
            <strong className="operator-inbox-title">{item.title || kindLabel(item.kind)}</strong>
            <span className={`badge ${item.status === "pending" ? "badge-warn" : ""}`}>{t(item.status)}</span>
            <time className="muted">{formatDateTime(item.created_ts || item.resolved_ts)}</time>
          </div>
          <p>{item.summary || inboxItemPrimaryRef(item) || "Operator action requested."}</p>
          <div className="operator-inbox-meta-line">
            {inboxItemPrimaryRef(item) ? <span className="mono">{inboxItemPrimaryRef(item)}</span> : null}
            {item.stage_id ? <span>stage {item.stage_id}</span> : null}
            {typeof item.task_count === "number" ? <span>{item.task_count} tasks</span> : null}
            {item.trace_id ? <span>trace {item.trace_id}</span> : null}
            {item.checkpoint_id ? <span>checkpoint {item.checkpoint_id}</span> : null}
            {item.run_id ? <span>run {item.run_id}</span> : null}
            {item.channel_id ? <span>channel {item.channel_id}</span> : null}
            {item.thread_id ? <span>thread {item.thread_id}</span> : null}
            {typeof item.revision === "number" ? <span>revision {item.revision}</span> : null}
            {typeof item.question_count === "number" && item.question_count > 1 ? <span>{item.question_count} questions</span> : null}
            {item.category ? <span>{categoryLabel(item.category)}</span> : null}
            {item.source_role ? <span>{sourceLabel(item.source_role)}</span> : null}
            {Number(item.dedupe_count || 0) > 1 ? <span>{item.dedupe_count} grouped</span> : null}
          </div>
        </div>
      </div>
      {selected ? (
        <>
          {item.deep_link ? (
            <div className="operator-inbox-inline-actions">
              <a className="delivery-action-button" href={inboxDeepLink(item.deep_link, projectId)}>
                {item.deep_link_label || t("Open source")}
              </a>
            </div>
          ) : null}
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
        </>
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
  const { t } = useUiPreferences();
  if (item.owner_route === "plan_synth_owner_decision" && item.plan_id) {
    const actions = (item.actions ?? [])
      .map((action) => ({
        action: typeof action.action === "string" ? action.action : "",
        label: typeof action.label === "string" ? action.label : "",
      }))
      .filter((action) => action.action);
    return (
      <div className="operator-inbox-inline-actions" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="delivery-action-button" onClick={onPreview} disabled={!item.preview?.available}>
          <Eye size={14} /> {t("Preview")}
        </button>
        {actions.map((action) => (
          <button key={action.action} type="button" className="delivery-action-button" onClick={() => onAction(action.action)} disabled={disabled}>
            <Check size={14} /> {t(action.label || action.action)}
          </button>
        ))}
      </div>
    );
  }
  if (item.kind === "plan_approval") {
    return (
      <div className="operator-inbox-inline-actions" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="delivery-action-button" onClick={onPreview} disabled={!item.preview?.available}>
          <Eye size={14} /> {t("Preview")}
        </button>
        <button type="button" className="delivery-action-button" onClick={onApprove} disabled={disabled}>
          <Check size={14} /> {t("Approve")}
        </button>
        <input
          className="operator-inbox-reject-input"
          value={rejectReason}
          aria-label={t("Reject reason")}
          placeholder={t("Reject reason")}
          onChange={(event) => onRejectReason(event.target.value)}
        />
        <button type="button" className="delivery-action-button" onClick={onReject} disabled={disabled || !rejectReason.trim()}>
          <X size={14} /> {t("Reject")}
        </button>
        <button type="button" className="delivery-action-button" onClick={onRepair} disabled={disabled}>
          <MessageSquare size={14} /> {t("Repair")}
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
          <MessageSquare size={14} /> {t(action.label || action.action)}
        </button>
      ))}
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
    item.question_id,
    item.channel_id,
    item.thread_id,
    item.asked_by,
    item.request_id,
    item.subject_type,
    item.conversation_id,
  ].filter(Boolean).join(" ").toLowerCase();
}

function inboxItemReasonKey(item: OperatorInboxItem): string {
  return String(item.plan_id || item.approval_ref || item.id);
}

function inboxItemActionKey(item: OperatorInboxItem): string {
  return String(item.plan_id || item.id);
}

function inboxItemPrimaryRef(item: OperatorInboxItem): string {
  return String(item.plan_id || item.decision_token || item.question_id || item.request_id || item.attention_id || item.approval_ref || item.id || "");
}

function inboxItemsForView(inbox: OperatorInboxProjection | null, view: InboxView): OperatorInboxItem[] {
  const items = inbox?.items ?? [];
  if (view === "all") return items;
  if (view === "resolved") return items.filter((item) => item.status !== "pending" || item.category === "resolved");
  if (view === "decisions") {
    return items.filter((item) => item.status === "pending" && item.actionability === "human_required");
  }
  if (view === "runtime_attention") {
    return items.filter((item) => item.status === "pending" && item.category === "runtime_attention");
  }
  if (view === "notification") {
    return items.filter((item) => item.status === "pending" && item.category === "notification");
  }
  return items.filter((item) => item.status === "pending" && item.category === "automation_diagnostic");
}

function inboxActionPayload(item: OperatorInboxItem, reason = ""): Record<string, unknown> {
  return {
    source: "operator-inbox",
    approval_ref: item.approval_ref,
    plan_id: item.plan_id,
    decision_token: item.decision_token,
    checkpoint_id: item.checkpoint_id,
    workflow_run_id: item.workflow_run_id,
    fanout_id: item.fanout_id,
    source_event_id: item.source_event_id,
    proposal_ref: item.proposal_ref,
    eval_ref: item.eval_ref,
    candidate_task_map_ref: item.candidate_task_map_ref,
    fingerprint: item.fingerprint,
    attention_id: item.attention_id,
    created_event_id: item.created_event_id,
    ...(reason.trim() ? { reason: reason.trim() } : {}),
  };
}

function kindLabel(kind: string): string {
  if (kind === "plan_approval") return "Plan Ready";
  if (kind === "human_decision") return "Human Decision";
  if (kind === "channel_decision") return "Channel Decision";
  if (kind === "kanban_question") return "Kanban Question";
  if (kind === "runtime_attention") return "Runtime Attention";
  if (kind === "approval") return "Approval";
  if (kind === "run_delivery") return "Run Delivery";
  return kind.replace(/_/g, " ");
}

function categoryLabel(category: string): string {
  if (category === "action_required") return "Decision";
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
  if (source === "channel") return "Channel";
  if (source === "kanban_agent") return "Kanban Agent";
  return source.replace(/_/g, " ");
}

function kindIcon(kind: string): LucideIcon {
  if (kind === "plan_approval") return FileText;
  if (kind === "human_decision") return ShieldCheck;
  if (kind === "channel_decision") return MessageSquare;
  if (kind === "kanban_question") return MessageSquare;
  if (kind === "runtime_attention") return AlertTriangle;
  if (kind === "approval") return CheckCircle2;
  return Bell;
}


function inboxDeepLink(value: string, projectId: string): string {
  const target = new URL(value, window.location.href);
  if (projectId) target.searchParams.set("project", projectId);
  if (/^https?:\/\//i.test(value)) return target.toString();
  return `${target.pathname}${target.search}${target.hash}`;
}
