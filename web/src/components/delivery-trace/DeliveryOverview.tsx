import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import type { ReactNode } from "react";

import type { DeliveryTrace } from "../../api/types";
import type { PageId } from "../../app/sharedTypes";
import { dtTone } from "./DeliveryTraceViewUtils";

interface DeliveryOverviewProps {
  // PM 快赢:每个数字都是一扇门 —— 卡片可点跳到取证家。
  onOpenPage?: (page: PageId) => void;
  trace: DeliveryTrace;
  // Run Manager 修复请求(rmar-*)次数:选择器已不列它们(空驾驶舱),
  // 在 Latest Run 卡以计数呈现过程健康;取证走 Observability/Loop。
  repairCount?: number;
}

interface AttentionItem {
  label: string;
  meta: string;
  tone: "ok" | "warn" | "err" | "info" | "muted";
  count?: number;
}

export function DeliveryOverview({ onOpenPage, trace, repairCount = 0 }: DeliveryOverviewProps) {
  useSettingsPreferences();
  const tasks = taskSummary(trace);
  const run = runSummary(trace);
  const attention = attentionItems(trace);
  // A5(racing 评审):feature 已终态且 ship 就绪时,运行期旧信号降为
  // historical 展示,不再以红/警色调冒充活告警(投影级 resolved 见 B1)。
  const historical = ["done", "shipped"].includes(trace.status)
    && ["ready", "shipped", "ok", "satisfied"].includes(trace.ship.status);

  // PM 中批(operator 收敛 2026-07-11):next-step 只在"需要操作员动手"时
  // 出现 —— ok/info 变体是 hero 判决 pill 的复读,删(同数据不二渲染)。
  const nextStep = (() => {
    const ship = trace.ship.status;
    if (["blocked", "failed", "error"].includes(ship)) {
      const top = attention[0];
      return {
        tone: "err" as const,
        text: `需要你处理:${top ? `${top.label}(${top.meta})` : "ship 被阻塞"}`,
        action: onOpenPage ? { label: "去 Inbox 裁决", page: "inbox" as PageId } : undefined,
      };
    }
    return null;
  })();

  return (
    <section className="delivery-overview" data-testid="delivery-overview">
      {nextStep && (
        <div className={`dt-next-step tone-${nextStep.tone}`} data-testid="dt-next-step">
          <span>{nextStep.text}</span>
          {nextStep.action && (
            <button type="button" className="icon-button" onClick={() => onOpenPage?.(nextStep.action!.page)}>
              {nextStep.action.label}
            </button>
          )}
        </div>
      )}
      {/* Open Trace/Graph 被 mode tab 取代;Open Loop 亦删(operator
          2026-07-11):它不带上下文,与 rail 的 Loop 导航项完全等价,却独占
          一行。带 loop id 的上下文跳转仍在 Graph 节点 inspector。 */}
      <div className="delivery-overview-grid">
        <OverviewCard title="Attention" meta={historical ? `${attention.length} signals · historical` : `${attention.length} signals`} onOpen={onOpenPage && !historical && attention.length ? () => onOpenPage("inbox") : undefined}>
          {attention.length ? (
            <div className="delivery-attention-list">
              {attention.slice(0, 5).map((item, index) => (
                <div className={`delivery-attention-row tone-${historical ? "muted" : item.tone}`} key={`${item.label}-${index}`}>
                  <strong>{item.label}{(item.count ?? 1) > 1 ? ` ×${item.count}` : ""}</strong>
                  <span>{item.meta}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">{uiText("No blocking delivery signals.")}</p>
          )}
        </OverviewCard>

        <OverviewCard title="Task Progress" meta={`${tasks.total} tasks`} onOpen={onOpenPage ? () => onOpenPage("board") : undefined}>
          <div className="delivery-progress-bar" aria-label="Task progress">
            <span className="done" style={{ width: `${tasks.donePct}%` }} />
            <span className="active" style={{ width: `${tasks.activePct}%` }} />
            <span className="blocked" style={{ width: `${tasks.blockedPct}%` }} />
          </div>
          <div className="delivery-overview-metrics">
            <Metric label={uiText("Done")} value={tasks.done} />
            <Metric label={uiText("Running")} value={tasks.active} />
            <Metric label={uiText("Blocked")} value={tasks.blocked} tone={tasks.blocked ? "err" : "muted"} />
            <Metric label="Waiting" value={tasks.waiting} />
          </div>
        </OverviewCard>

        <OverviewCard title="Latest Run" meta={`${run.total} runs`} onOpen={onOpenPage ? () => onOpenPage("delivery-trace") : undefined}>
          <div className="delivery-overview-metrics">
            {historical ? (
              <Metric label="Interrupted" value={run.running} tone={run.running ? "warn" : "muted"} />
            ) : (
              <Metric label={uiText("Running")} value={run.running} tone={run.running ? "info" : "muted"} />
            )}
            <Metric label={uiText("Failed")} value={run.failed} tone={run.failed ? "err" : "muted"} />
            <Metric label={uiText("Completed")} value={run.completed} tone={run.completed ? "ok" : "muted"} />
            {repairCount > 0 && <Metric label="RM Repairs" value={repairCount} tone="warn" />}
          </div>
          <p className="delivery-overview-note">{run.latestLabel}</p>
        </OverviewCard>

      </div>
    </section>
  );
}

function OverviewCard({
  children,
  meta,
  onOpen,
  title,
}: {
  children: ReactNode;
  meta: string;
  onOpen?: () => void;
  title: string;
}) {
  return (
    <article
      className={`delivery-overview-card${onOpen ? " clickable" : ""}`}
      onClick={onOpen}
      role={onOpen ? "button" : undefined}
      tabIndex={onOpen ? 0 : undefined}
      onKeyDown={onOpen ? (event) => { if (event.key === "Enter") onOpen(); } : undefined}
    >
      <div className="delivery-overview-card-head">
        <h3>{title}</h3>
        <span>{meta}{onOpen ? " ↗" : ""}</span>
      </div>
      {children}
    </article>
  );
}

function Metric({
  label,
  tone = "info",
  value,
}: {
  label: string;
  tone?: "ok" | "warn" | "err" | "info" | "muted";
  value: number | string;
}) {
  return (
    <div className={`delivery-overview-metric tone-${tone}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function taskSummary(trace: DeliveryTrace) {
  const graph = trace.execution_graph;
  const total = graph?.task_count || graph?.nodes?.length || trace.task_map?.task_count || 0;
  const done = graph?.done_count || countTasks(trace, ["done", "passed"]);
  const active = graph?.in_progress_count || countTasks(trace, ["in_progress", "running"]);
  const blocked = graph?.blocked_count || countTasks(trace, ["blocked", "failed"]);
  const waiting = Math.max(0, total - done - active - blocked);
  const denom = Math.max(1, total);
  return {
    active,
    activePct: pct(active, denom),
    blocked,
    blockedPct: pct(blocked, denom),
    done,
    donePct: pct(done, denom),
    total,
    waiting,
  };
}

function runSummary(trace: DeliveryTrace) {
  const compact = trace.run_summary;
  const groups = trace.run_groups ?? [];
  const running = compact?.running ?? groups.filter((run) => ["running", "in_progress"].includes(run.status)).length;
  const failed = compact?.failed ?? groups.filter((run) => ["failed", "error", "blocked"].includes(run.status)).length;
  const completed = compact?.completed ?? groups.filter((run) => ["passed", "done", "ok", "completed"].includes(run.status)).length;
  return {
    failed,
    latestLabel: compact?.latest_label ?? (groups[0] ? `${groups[0].label || groups[0].group_id} · ${groups[0].status}` : "No run projected yet."),
    completed,
    running,
    total: compact?.total ?? groups.length,
  };
}

function attentionItems(trace: DeliveryTrace): AttentionItem[] {
  const out: AttentionItem[] = (trace.attention ?? []).map((item) => ({
    label: item.label,
    meta: item.meta ?? "delivery signal",
    tone: item.tone ?? "warn",
    count: item.count,
  }));
  // Ship 状态 hero 已有红 badge;Attention 行仅在 readiness 文本带有增量
  // 信息时渲染("Ship blocked · blocked" 一屏三报,2026-07-11 评审)。
  const readiness = (trace.ship.readiness || "").trim();
  if (
    ["blocked", "failed", "error"].includes(trace.ship.status)
    && readiness && readiness !== trace.ship.status
  ) {
    out.push({ label: `Ship ${trace.ship.status}`, meta: readiness, tone: "err" });
  }
  for (const item of trace.ship.missing_evidence ?? []) {
    out.push({ label: "Missing evidence", meta: `${item.task_id} · ${item.status}`, tone: "err" });
  }
  if (trace.drift_report.items.length > 0) {
    out.push({ label: `Drift ${trace.drift_report.status}`, meta: `${trace.drift_report.items.length} drift items`, tone: "warn" });
  }
  for (const diagnostic of trace.diagnostics ?? []) {
    out.push({ label: diagnostic.kind, meta: diagnostic.message, tone: dtTone(diagnostic.kind) });
  }
  // A2(racing 评审):完全相同的 (label, meta) 行聚合为一行 ×N
  // (functional_check · zf-cli 曾双行并列;Inbox 的 ×N grouped 同款)。
  const grouped = new Map<string, AttentionItem>();
  for (const item of out) {
    const key = `${item.label}::${item.meta}`;
    const existing = grouped.get(key);
    if (existing) existing.count = (existing.count ?? 1) + 1;
    else grouped.set(key, { ...item, count: 1 });
  }
  return [...grouped.values()];
}

function countTasks(trace: DeliveryTrace, statuses: string[]) {
  return (trace.execution_graph?.nodes ?? []).filter((node) => statuses.includes(node.actual.status)).length;
}

function pct(value: number, total: number) {
  return Math.max(0, Math.min(100, Math.round((value / total) * 100)));
}
