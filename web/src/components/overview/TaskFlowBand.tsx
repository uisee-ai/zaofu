// TASK FLOW band on Overview — overview-pulse.v1 task_flow. Main chain
// Todo → In Progress → Verify → Done with transition rates and the WIP cap;
// blocked is a side pocket, never a chain stage; Verify→Done is gated.
// Null flow falls back to the legacy taskCounts cards (no behavior loss).
import type { OverviewWhyNot, Task, TaskFlowPulse, TaskFlowStats } from "../../api/types";
import type { BoardColumnId } from "../kanban/board";
import { taskColumn } from "../kanban/board";

// null = no data (renders as "—"; no-data is never shown as 0)
function fmtAge(value: number | null | undefined): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  return `${Math.round(value / 3600)}h`;
}

function fmtCount(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "—";
}

function fmtRate(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)}/h` : "—";
}

function FlowNode({ label, count, extra, age, onClick }: {
  label: string;
  count: string;
  extra?: string;
  age?: string | null;
  onClick: () => void;
}) {
  const empty = count === "0" || count === "—";
  // ③ 定宽节点内文 ellipsis,完整值兜底进 title。
  const title = `${label} ${count}${extra ? ` · ${extra}` : ""}${age ? ` · ${age}` : ""}`;
  return (
    <button className={`task-flow-node${empty ? " is-empty" : ""}`} type="button" onClick={onClick} title={title}>
      <span className="task-flow-node-line">
        {label} <strong className="mono">{count}</strong>
        {extra ? <span className="muted mono">{` · ${extra}`}</span> : null}
      </span>
      {age ? <span className="muted task-flow-node-age">{age}</span> : null}
    </button>
  );
}

function FlowEdge({ label, title, tone }: { label: string; title?: string; tone?: "gate" }) {
  return (
    <span className={`task-flow-edge${tone ? ` is-${tone}` : ""}`} title={title}>
      <span className="task-flow-edge-line" aria-hidden="true" />
      <span className="task-flow-edge-label mono">{label}</span>
    </span>
  );
}

interface FallbackCount {
  id: BoardColumnId;
  title: string;
  count: number;
}

interface TaskFlowBandProps {
  flow: TaskFlowPulse | null | undefined;
  whyNot: OverviewWhyNot | null | undefined;
  fallbackCounts: ReadonlyArray<FallbackCount>;
  fallbackFlowStats?: TaskFlowStats | null;
  onOpenPage: (page: "board") => void;
  onOpenTask: (taskId: string) => void;
  tasks: Task[];
}

export function TaskFlowBand({
  flow,
  whyNot,
  fallbackCounts,
  fallbackFlowStats,
  onOpenPage,
  onOpenTask,
  tasks,
}: TaskFlowBandProps) {
  if (!flow) {
    const countFor = (id: BoardColumnId) => fallbackCounts.find((item) => item.id === id)?.count ?? 0;
    const allZero = fallbackCounts.every((item) => !item.count) && tasks.length === 0;
    if (allZero) {
      // An all-zero flow rail is pure chrome; keep one quiet line instead of a band.
      return (
        <section className="pulse-band-section" data-testid="overview-task-flow-band">
          <span className="muted">No task flow yet.</span>
        </section>
      );
    }
    const openColumn = (id: BoardColumnId) => {
      const firstTask = tasks.find((task) => taskColumn(task) === id);
      if (firstTask) onOpenTask(firstTask.id);
      else onOpenPage("board");
    };
    const inProgressAge = fmtAge(fallbackFlowStats?.oldest_in_progress_seconds);
    const blockedAge = fmtAge(fallbackFlowStats?.oldest_blocked_seconds);
    return (
      <section className="pulse-band-section" data-testid="overview-task-flow-band">
        <div className="task-flow-band task-flow-band-fallback">
          <div className="task-flow-chain">
            <span className="pulse-band-label section-title">TASK FLOW</span>
            {/* ③ 等宽节点单行轨道:窄屏横滚,禁换行挤压。 */}
            <div className="task-flow-chain-rail">
              <FlowNode count={fmtCount(countFor("ready"))} label="Todo" onClick={() => openColumn("ready")} />
              <FlowEdge label="start" />
              <FlowNode
                age={inProgressAge ? `oldest ${inProgressAge}` : null}
                count={fmtCount(countFor("in_progress"))}
                label="In Progress"
                onClick={() => openColumn("in_progress")}
              />
              <FlowEdge label="handoff" />
              <FlowNode count={fmtCount(countFor("testing"))} label="Verify" onClick={() => openColumn("testing")} />
              <FlowEdge label="gate" tone="gate" />
              <FlowNode count={fmtCount(countFor("done"))} label="Done" onClick={() => openColumn("done")} />
            </div>
          </div>
          <div className="task-flow-meta">
            <button
              className={`task-flow-pocket${countFor("blocked") > 0 ? " is-warn" : ""}`}
              type="button"
              onClick={() => openColumn("blocked")}
            >
              blocked side pocket {fmtCount(countFor("blocked"))}
              {blockedAge ? ` · oldest ${blockedAge}` : ""}
            </button>
            <span className="task-flow-whynot">flow projection pending · board counts shown</span>
          </div>
        </div>
      </section>
    );
  }

  const columns = flow.columns ?? {};
  const oldest = flow.oldest_age_seconds ?? {};
  const rates = flow.transitions_per_hour ?? {};
  const wip = flow.wip ?? null;
  const wipExtra = wip && typeof wip.used === "number"
    ? `WIP ${wip.used}/${typeof wip.capacity === "number" ? wip.capacity : "—"}`
    : undefined;
  const windowHours = typeof flow.window_hours === "number" ? flow.window_hours : null;
  const rateHint = windowHours != null ? `per hour over trailing ${windowHours}h` : undefined;
  const ageOf = (value: number | null | undefined): string | null => {
    const formatted = fmtAge(value);
    return formatted ? `oldest ${formatted}` : null;
  };
  const openBoard = () => onOpenPage("board");

  const rework = flow.rework_backedge_per_hour;
  const reworkWarn = typeof rework === "number" && rework > 0;

  const pocket = flow.blocked_side_pocket ?? [];
  const blockedCount = typeof columns.blocked === "number" ? columns.blocked : pocket.length;
  const topPocket = pocket[0];
  const pocketAge = fmtAge(topPocket?.age_seconds);

  const summary = typeof whyNot?.summary === "string" && whyNot.summary ? whyNot.summary : null;
  const whyNotWarn = summary != null && summary !== "dispatching_normally";
  const firstReason = whyNot?.notifications?.[0]?.reason;

  return (
    <section className="pulse-band-section" data-testid="overview-task-flow-band">
      <div className="task-flow-band">
        <div className="task-flow-chain">
          <span className="pulse-band-label section-title">TASK FLOW</span>
          {/* ③ 等宽节点单行轨道:窄屏横滚,禁换行挤压。 */}
          <div className="task-flow-chain-rail">
            <FlowNode age={ageOf(oldest.todo)} count={fmtCount(columns.todo)} label="Todo" onClick={openBoard} />
            <FlowEdge label={fmtRate(rates.todo_to_in_progress)} title={rateHint} />
            <FlowNode
              age={ageOf(oldest.in_progress)}
              count={fmtCount(columns.in_progress)}
              extra={wipExtra}
              label="In Progress"
              onClick={openBoard}
            />
            <FlowEdge label={fmtRate(rates.in_progress_to_verify)} title={rateHint} />
            <FlowNode age={ageOf(oldest.verify)} count={fmtCount(columns.verify)} label="Verify" onClick={openBoard} />
            <FlowEdge label="gate" title={`done gate: ${flow.done_gate || "unknown"}`} tone="gate" />
            <FlowNode count={fmtCount(columns.done)} label="Done" onClick={openBoard} />
            <span className={`task-flow-rework mono${reworkWarn ? " is-warn" : ""}`}>
              <span>Rework</span>
              <strong>{fmtRate(rework)}</strong>
            </span>
          </div>
        </div>
        <div className="task-flow-meta">
          <button
            className={`task-flow-pocket${blockedCount > 0 ? " is-warn" : ""}`}
            type="button"
            onClick={() => {
              if (topPocket?.task_id) onOpenTask(topPocket.task_id);
              else openBoard();
            }}
          >
            blocked (side pocket) {fmtCount(blockedCount)}
            {topPocket ? ` — ${topPocket.reason || "blocked"}${pocketAge ? ` · ${pocketAge}` : ""}` : ""}
          </button>
          <span className={`task-flow-whynot${whyNotWarn ? " is-warn" : ""}`}>
            why-not: {summary ?? "—"}
            {whyNotWarn && firstReason ? ` — ${firstReason}` : ""}
          </span>
        </div>
      </div>
    </section>
  );
}
