// RunGraphView (2026-06-11 S-E) — Airflow Graph View 风格的 run-chain.v1 渲染。
// 纯 DOM + CSS grid(主链 ≤8 节点、组内 ≤6 children,不引第三方布局库):
// 主链圆角节点、fanout TaskGroup 容器(⊞/⊟)、gate 加重边、backedge 虚线回边、
// task_map_history ▒ ghost。颜色全走 styles.css token(与看板列 tone 同源)。
// T-刀①/①.5/②: id 检索框、seq 锚、双时长、sched-delay、lane pool strip、
// ⛓ causation 回放(命中链高亮 rg-causal-path,其余降透明度,ESC 退出)。
import { Fragment, useEffect, useMemo, useState } from "react";

import { getDeliveryCausationChain } from "../../api/client";
import type {
  DeliveryFlowTaskMetrics,
  DeliveryRunChainStage,
  DeliveryTaskLifecycleEntry,
  DeliveryTrace,
  DeliveryTraceNode,
  OverviewPulse,
  TaskMapHistoryEntry,
} from "../../api/types";
import { formatSeconds } from "../common/SegBar";
import { seqRangeLabel } from "./DeliveryTraceViewUtils";
import { RunGraphPoolStrip } from "./RunGraphPoolStrip";

type RgState =
  | "none" | "ready" | "queued" | "running" | "done"
  | "failed" | "blocked" | "retry" | "superseded";

const RG_GLYPH: Record<RgState, string> = {
  none: "□", ready: "▦", queued: "▤", running: "◐", done: "■",
  failed: "✖", blocked: "◫", retry: "↻", superseded: "▒",
};

const RG_STATES: RgState[] = [
  "none", "ready", "queued", "running", "done", "failed", "blocked", "retry", "superseded",
];

// Rx1 — stage 名人类化:按 ._ 切词,丢开头的命名空间 token,Title Case 连接
// (refactor.scan.requested → "Refactor Scan Requested";task_map.ready →
// "Task Map Ready")。原始事件类型保留在 title 属性。
const STAGE_NAMESPACE_TOKENS = new Set(["zaofu", "hermes", "cj", "min"]);

function humanizeStage(type: string): string {
  const tokens = type.split(/[._]/).filter(Boolean);
  let start = 0;
  while (start < tokens.length - 1 && STAGE_NAMESPACE_TOKENS.has(tokens[start].toLowerCase())) start += 1;
  const rest = tokens.slice(start);
  if (!rest.length) return type;
  return rest.map((token) => token.charAt(0).toUpperCase() + token.slice(1)).join(" ");
}

const LIFECYCLE_TO_RG: Record<string, RgState> = {
  backlog: "none", ready: "ready", queued: "queued", running: "running",
  verify: "running", done: "done", failed: "failed", blocked: "blocked",
};

const NODE_STATUS_TO_RG: Record<string, RgState> = {
  done: "done", cancelled: "done",
  in_progress: "running", review: "running", test: "running", judge: "running", dispatched: "running",
  rework: "retry", blocked: "blocked", failed: "failed", ready: "ready",
};

function clock(ts?: string | null): string {
  if (!ts) return "—";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function durBetween(from?: string | null, to?: string | null): string | null {
  if (!from || !to) return null;
  const start = new Date(from).getTime();
  const end = new Date(to).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return formatSeconds((end - start) / 1000);
}

// Display state of one task: lifecycle history first (retry = on try ≥2 and
// not yet terminal), then execution-graph status as degraded fallback.
function taskRgState(entry?: DeliveryTaskLifecycleEntry, node?: DeliveryTraceNode): RgState {
  if (node?.superseded) return "superseded";
  const history = entry?.state_history ?? [];
  const tries = entry?.tries ?? [];
  const last = history[history.length - 1]?.state ?? "";
  if (tries.length >= 2 && tries[tries.length - 1]?.outcome === "in_flight"
    && !["done", "failed", "blocked"].includes(last)) {
    return "retry";
  }
  if (last && LIFECYCLE_TO_RG[last]) return LIFECYCLE_TO_RG[last];
  return NODE_STATUS_TO_RG[node?.actual.status ?? ""] ?? "none";
}

function stageAggState(stage: DeliveryRunChainStage, states: RgState[]): RgState {
  if (states.includes("failed")) return "failed";
  if (states.includes("blocked")) return "blocked";
  if (stage.status === "done") return "done";
  if (stage.status === "active" || states.includes("running") || states.includes("retry")) return "running";
  return "none";
}

function stateSummary(states: RgState[]): string {
  const counts = new Map<RgState, number>();
  for (const state of states) counts.set(state, (counts.get(state) ?? 0) + 1);
  return RG_STATES
    .filter((state) => counts.has(state))
    .map((state) => `${counts.get(state)}${RG_GLYPH[state]}`)
    .join(" ");
}

interface SearchHit {
  kind: "stage" | "task";
  id: string;
}

// ⛓ 回放链 state — T-刀④起提升到 DeliveryTraceTabs 共享(Trace 树 event 叶
// 同链高亮);props 缺省时退回本地 state,行为不变。
export interface CausalState {
  anchor: string;
  ids: Set<string>;
}

interface RunGraphViewProps {
  causal?: CausalState | null;
  onCausalChange?: (value: CausalState | null) => void;
  onSelectTask?: (taskId: string) => void;
  projectId?: string;
  pulse?: OverviewPulse | null;
  trace: DeliveryTrace;
}

export function RunGraphView({
  causal: causalProp,
  onCausalChange,
  onSelectTask,
  projectId,
  pulse,
  trace,
}: RunGraphViewProps) {
  const chain = trace.run_chain;
  const stages = chain?.stages ?? [];
  const [expandedMap, setExpandedMap] = useState<Record<string, boolean>>({});
  const [showGhostDiff, setShowGhostDiff] = useState(false);
  const [searchQ, setSearchQ] = useState("");
  const [searchHit, setSearchHit] = useState<SearchHit | null>(null);
  const [searchMiss, setSearchMiss] = useState(false);
  const [localCausal, setLocalCausal] = useState<CausalState | null>(null);
  const causal = onCausalChange ? causalProp ?? null : localCausal;
  const setCausal = onCausalChange ?? setLocalCausal;
  const [causalNote, setCausalNote] = useState("");
  const nodeByTask = useMemo(
    () => new Map((trace.execution_graph?.nodes ?? []).map((node) => [node.task_id, node])),
    [trace],
  );
  const lifecycleTasks = trace.task_lifecycle?.tasks ?? {};
  const metricsByTask = trace.flow_metrics?.tasks ?? {};

  // T-刀① item 5 — sched-delay: trigger.ts → earliest dispatched_at over tries.
  const schedDelay = useMemo(() => {
    const triggerTs = chain?.trigger?.ts ? new Date(chain.trigger.ts).getTime() : NaN;
    let earliest = NaN;
    for (const entry of Object.values(trace.task_lifecycle?.tasks ?? {})) {
      for (const tryItem of entry.tries) {
        if (!tryItem.dispatched_at) continue;
        const ts = new Date(tryItem.dispatched_at).getTime();
        if (!Number.isNaN(ts) && (Number.isNaN(earliest) || ts < earliest)) earliest = ts;
      }
    }
    if (Number.isNaN(triggerTs) || Number.isNaN(earliest) || earliest < triggerTs) return null;
    return formatSeconds((earliest - triggerTs) / 1000);
  }, [chain, trace]);

  // Scroll the search hit into view once it renders (group expansion happens
  // in the same state update, so the target exists by effect time).
  useEffect(() => {
    if (!searchHit) return;
    const selector = searchHit.kind === "stage"
      ? `[data-testid="rg-node-${searchHit.id}"]`
      : `[data-testid="rg-task-${searchHit.id}"]`;
    try {
      document.querySelector(selector)?.scrollIntoView({ block: "nearest", inline: "center" });
    } catch {
      // Unescapable id chars — highlight class still applies, skip scroll.
    }
  }, [searchHit]);

  useEffect(() => {
    if (!causal) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setCausal(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [causal]);

  if (!chain || chain.status === "no_stage_order" || !stages.length) {
    return (
      <div className="delivery-tab-empty" data-testid="run-graph-empty">
        <strong>No run chain projected.</strong>
        <span className="muted">run-chain.v1 appears once workflow.dag stage_order events land.</span>
      </div>
    );
  }

  // T-刀① item 3 — match within already-loaded feature data only.
  const hitTask = (taskId: string) => {
    const owner = stages.find((stage) => stage.task_ids.includes(taskId));
    if (!owner) {
      setSearchMiss(true);
      return;
    }
    setExpandedMap((prev) => ({ ...prev, [owner.stage]: true }));
    setSearchHit({ kind: "task", id: taskId });
  };
  const dispatchOwner = (match: (id: string) => boolean): string | undefined => {
    for (const [taskId, entry] of Object.entries(lifecycleTasks)) {
      if (entry.tries.some((tryItem) => tryItem.dispatch_id && match(tryItem.dispatch_id))) return taskId;
    }
    return undefined;
  };
  const runSearch = () => {
    const q = searchQ.trim();
    setSearchMiss(false);
    setSearchHit(null);
    if (!q) return;
    const lower = q.toLowerCase();
    const allTaskIds = stages.flatMap((stage) => stage.task_ids);
    const exactStage = stages.find((stage) => stage.via_event_id === q || stage.causation_id === q);
    if (exactStage) {
      setSearchHit({ kind: "stage", id: exactStage.stage });
      return;
    }
    const exactTask = allTaskIds.find((taskId) => taskId === q) ?? dispatchOwner((id) => id === q);
    if (exactTask) {
      hitTask(exactTask);
      return;
    }
    const fuzzyStage = stages.find((stage) =>
      (stage.via_event_id ?? "").toLowerCase().includes(lower)
      || (stage.causation_id ?? "").toLowerCase().includes(lower));
    const fuzzyTask = allTaskIds.find((taskId) => taskId.toLowerCase().includes(lower))
      ?? dispatchOwner((id) => id.toLowerCase().includes(lower));
    if (fuzzyTask) {
      hitTask(fuzzyTask);
      return;
    }
    if (fuzzyStage) {
      setSearchHit({ kind: "stage", id: fuzzyStage.stage });
      return;
    }
    setSearchMiss(true);
  };

  // T-刀② item 9 — causation replay: fetch chain for the stage's via_event_id;
  // re-click the same anchor (or ESC) exits; fetch failure degrades to a title note.
  const toggleCausal = (stage: DeliveryRunChainStage) => {
    const eventId = stage.via_event_id;
    if (!eventId) return;
    if (causal?.anchor === eventId) {
      setCausal(null);
      return;
    }
    setCausalNote("");
    getDeliveryCausationChain(trace.feature_id, eventId, projectId || undefined)
      .then((result) => {
        const ids = new Set((result.chain ?? []).map((entry) => entry.id).filter(Boolean));
        ids.add(eventId);
        setCausal({ anchor: eventId, ids });
      })
      .catch((error: unknown) => {
        setCausalNote(`causation fetch failed: ${String((error as Error)?.message ?? error)}`);
      });
  };

  const stageStates = stages.map((stage) =>
    stage.task_ids.map((tid) => taskRgState(lifecycleTasks[tid], nodeByTask.get(tid))));
  const stageAggs = stages.map((stage, idx) => stageAggState(stage, stageStates[idx]));
  // Rx5 — 图例按需:只列图中实际出现的状态(stage 聚合态 + 组内 task 态);
  // ≤1 项时整行不渲染(无可分辨信息)。
  const presentStates = RG_STATES.filter((state) =>
    stageAggs.includes(state) || stageStates.some((states) => states.includes(state)));

  // Backedge: any backedge_count>0 (sum) — else count of tries with rework_kind.
  let backedgeCount = 0;
  for (const metrics of Object.values(trace.flow_metrics?.tasks ?? {})) {
    backedgeCount += metrics?.backedge_count ?? 0;
  }
  if (backedgeCount === 0) {
    for (const entry of Object.values(lifecycleTasks)) {
      backedgeCount += entry.tries.filter((t) => t.rework_kind).length;
    }
  }
  // Backedge target = the impl/fanout group; source = late verify-ish stage.
  const firstGroupIdx = stages.findIndex((stage) => stage.task_ids.length > 0);
  const implIdx = firstGroupIdx >= 0
    ? firstGroupIdx
    : Math.max(0, stages.findIndex((stage) => /impl|build|dev/i.test(stage.stage)));
  let backedgeSrcIdx = -1;
  stages.forEach((stage, idx) => {
    if (idx > implIdx && /verify|judge|review|test|gate/i.test(stage.stage)) backedgeSrcIdx = idx;
  });
  if (backedgeSrcIdx <= implIdx) backedgeSrcIdx = stages.length - 1;
  const showBackedge = backedgeCount > 0 && backedgeSrcIdx > implIdx;

  // ▒ ghost: version chain ≥2 → superseded marker beside the task_map stage.
  const history = trace.task_map_history ?? [];
  const supersededVersions = history.filter((entry) => entry.superseded || !entry.is_current);
  const ghostPrev = history.length >= 2
    ? (supersededVersions[supersededVersions.length - 1] ?? history[history.length - 2])
    : null;
  const ghostCurrent = history.find((entry) => entry.is_current) ?? history[history.length - 1] ?? null;
  const ghostStageIdx = ghostPrev
    ? Math.max(0, stages.findIndex((stage) => /task_map|plan/i.test(stage.stage)))
    : -1;

  const isTerminal = (idx: number) =>
    idx === stages.length - 1 || /judge|done|terminal/i.test(stages[idx].stage);

  const causalTitle = causalNote || "trace causation chain (re-click or ESC to exit)";

  return (
    <div className="rg-panel" data-testid="run-graph">
      <div className="rg-head">
        <span title={chain.trigger ? `trigger ${chain.trigger.type} · ${chain.trigger.actor ?? "?"} · ${chain.trigger.ts ?? ""}` : `run ${chain.status}`}>run {chain.status}</span>
        {chain.trigger && (
          <span className="muted">
            · trigger {chain.trigger.type} @ {clock(chain.trigger.ts)}{chain.trigger.actor ? ` · ${chain.trigger.actor}` : ""}
            {" "}· sched-delay {schedDelay ?? "—"}
          </span>
        )}
        <span className="rg-search-wrap">
          <input
            className="rg-search"
            data-testid="rg-search"
            type="search"
            placeholder="evt- / TASK- / dispatch- id"
            aria-label="Find event, task, or dispatch id in this run graph"
            value={searchQ}
            onChange={(event) => setSearchQ(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") runSearch();
            }}
          />
          {searchMiss && <span className="rg-search-miss" data-testid="rg-search-miss">not in this feature</span>}
        </span>
      </div>
      {presentStates.length > 1 && (
        <div className="rg-legend" data-testid="rg-legend" aria-label="Run graph state legend">
          {presentStates.map((state) => (
            <span key={state} className={`rg-legend-item rg-state-${state}`}>
              <i aria-hidden="true">{RG_GLYPH[state]}</i>{state}
            </span>
          ))}
        </div>
      )}
      <RunGraphPoolStrip nodes={trace.execution_graph?.nodes ?? []} pulse={pulse} />
      <div className="rg-scroll">
        <div
          className={`rg-grid${causal ? " rg-causal-mode" : ""}`}
          style={{ gridTemplateColumns: stages.map((_, idx) => (idx > 0 ? "auto 200px" : "200px")).join(" ") }}
        >
          {stages.map((stage, idx) => {
            const states = stageStates[idx];
            const agg = stageAggs[idx];
            const defaultExpanded = stage.status === "active" || states.includes("failed");
            const expanded = expandedMap[stage.stage] ?? defaultExpanded;
            const onCausalPath = !!causal && (
              (!!stage.via_event_id && causal.ids.has(stage.via_event_id))
              || (!!stage.causation_id && causal.ids.has(stage.causation_id))
            );
            const flags = `${onCausalPath ? " rg-causal-path" : ""}${searchHit?.kind === "stage" && searchHit.id === stage.stage ? " rg-search-hit" : ""}`;
            const causalProps = {
              causalActive: causal?.anchor === stage.via_event_id && !!stage.via_event_id,
              causalTitle,
              onCausal: stage.via_event_id ? () => toggleCausal(stage) : undefined,
            };
            return (
              <Fragment key={`${stage.stage}-${idx}`}>
                {idx > 0 && (
                  <div
                    className={`rg-edge${isTerminal(idx) ? " rg-edge-gate" : ""}`}
                    style={{ gridColumn: idx * 2, gridRow: 1 }}
                    aria-hidden="true"
                  >
                    {isTerminal(idx) && <span className="rg-edge-label">gate</span>}
                  </div>
                )}
                <div className="rg-cell" style={{ gridColumn: idx * 2 + 1, gridRow: 1 }}>
                  {stage.task_ids.length > 0 ? (
                    <StageGroup
                      agg={agg}
                      expanded={expanded}
                      flags={flags}
                      lifecycleTasks={lifecycleTasks}
                      metricsByTask={metricsByTask}
                      nodeByTask={nodeByTask}
                      onSelectTask={onSelectTask}
                      onToggle={() => setExpandedMap((prev) => ({ ...prev, [stage.stage]: !expanded }))}
                      searchTaskId={searchHit?.kind === "task" ? searchHit.id : ""}
                      stage={stage}
                      states={states}
                      {...causalProps}
                    />
                  ) : (
                    <StageNode agg={agg} flags={flags} stage={stage} {...causalProps} />
                  )}
                  {idx === ghostStageIdx && ghostPrev && (
                    <button
                      type="button"
                      className="rg-ghost rg-state-superseded"
                      data-testid="rg-ghost"
                      onClick={() => setShowGhostDiff((value) => !value)}
                      title={`task_map v${ghostPrev.version} superseded · ${ghostPrev.ref}`}
                    >
                      ▒ v{ghostPrev.version} superseded
                    </button>
                  )}
                </div>
              </Fragment>
            );
          })}
          {showBackedge && (
            <div
              className="rg-backedge"
              data-testid="rg-backedge"
              style={{ gridRow: 2, gridColumn: `${implIdx * 2 + 1} / ${backedgeSrcIdx * 2 + 2}` }}
              title={`rework backedge: ${stages[backedgeSrcIdx].stage} → ${stages[implIdx].stage}`}
            >
              <span className="rg-backedge-label">↩ backedge ×{backedgeCount}</span>
            </div>
          )}
        </div>
      </div>
      {showGhostDiff && ghostPrev && (
        <GhostDiff current={ghostCurrent} nodes={trace.execution_graph?.nodes ?? []} prev={ghostPrev} />
      )}
    </div>
  );
}

interface StageCausalProps {
  causalActive: boolean;
  causalTitle: string;
  onCausal?: () => void;
}

function CausalButton({ causalActive, causalTitle, onCausal, stage }: StageCausalProps & { stage: string }) {
  if (!onCausal) return null;
  return (
    <button
      type="button"
      className={`rg-causal${causalActive ? " is-on" : ""}`}
      data-testid={`rg-causal-${stage}`}
      onClick={onCausal}
      title={causalTitle}
      aria-pressed={causalActive}
    >
      ⛓
    </button>
  );
}

function StageNode({ agg, flags, stage, ...causalProps }: StageCausalProps & {
  agg: RgState;
  flags: string;
  stage: DeliveryRunChainStage;
}) {
  const seq = seqRangeLabel(stage.seq_first, stage.seq_last);
  const dur = durBetween(stage.entered_at, stage.completed_at);
  // Rx2 — waiting(none)态:状态副行整行不渲染,状态并入 title。
  const waiting = agg === "none";
  return (
    <div
      className={`rg-node rg-state-${agg}${stage.status === "active" ? " is-active" : ""}${flags}`}
      data-testid={`rg-node-${stage.stage}`}
    >
      <div className="rg-node-header">
        <div className="rg-group-head">
          <span className="rg-node-name" title={waiting ? `${stage.stage} · ${stage.status}` : stage.stage}>
            {humanizeStage(stage.stage)}
          </span>
          <CausalButton stage={stage.stage} {...causalProps} />
        </div>
        {!waiting && (
          <span className="rg-node-sub">
            {RG_GLYPH[agg]} {stage.status}{stage.occurrences > 0 ? ` · ×${stage.occurrences}` : ""}
          </span>
        )}
        {stage.entered_at ? (
          <span className="rg-node-sub">
            {clock(stage.entered_at)}{stage.completed_at ? ` → ${clock(stage.completed_at)}` : ""}{dur ? ` · ${dur}` : ""}
          </span>
        ) : null}
        {seq && <span className="rg-node-sub rg-node-seq">{seq}</span>}
      </div>
    </div>
  );
}

function StageGroup({
  agg,
  expanded,
  flags,
  lifecycleTasks,
  metricsByTask,
  nodeByTask,
  onSelectTask,
  onToggle,
  searchTaskId,
  stage,
  states,
  ...causalProps
}: StageCausalProps & {
  agg: RgState;
  expanded: boolean;
  flags: string;
  lifecycleTasks: Record<string, DeliveryTaskLifecycleEntry>;
  metricsByTask: Record<string, DeliveryFlowTaskMetrics>;
  nodeByTask: Map<string, DeliveryTraceNode>;
  onSelectTask?: (taskId: string) => void;
  onToggle: () => void;
  searchTaskId: string;
  stage: DeliveryRunChainStage;
  states: RgState[];
}) {
  const seq = seqRangeLabel(stage.seq_first, stage.seq_last);
  const dur = durBetween(stage.entered_at, stage.completed_at);
  const waiting = agg === "none";
  const taskCount = stage.task_ids.length;
  // Rx3 — 计数文案化:`{n} tasks` + retry 追加;原 glyph 汇总降为第二副行,
  // 全 none(纯 waiting)时不显。
  const retryCount = states.filter((state) => state === "retry").length;
  const glyphSummary = states.some((state) => state !== "none") ? stateSummary(states) : "";
  return (
    <div
      className={`rg-node rg-group rg-state-${agg}${stage.status === "active" ? " is-active" : ""}${flags}`}
      data-testid={`rg-node-${stage.stage}`}
    >
      <div className="rg-node-header">
        <div className="rg-group-head">
          <span className="rg-node-name" title={waiting ? `${stage.stage} · ${stage.status}` : stage.stage}>
            {humanizeStage(stage.stage)}
          </span>
          <CausalButton stage={stage.stage} {...causalProps} />
          <button
            type="button"
            className="rg-toggle"
            data-testid={`rg-toggle-${stage.stage}`}
            onClick={onToggle}
            aria-expanded={expanded}
            title={expanded ? "collapse task group" : "expand task group"}
          >
            {expanded ? "⊟" : "⊞"}
          </button>
        </div>
        <span className="rg-node-sub">
          {taskCount} task{taskCount > 1 ? "s" : ""}{retryCount > 0 ? ` · ${retryCount} retry` : ""}
        </span>
        {glyphSummary && <span className="rg-node-sub">{glyphSummary}</span>}
        {!waiting && (
          <span className="rg-node-sub">
            {stage.status}{stage.occurrences > 0 ? ` ×${stage.occurrences}` : ""}
            {stage.entered_at ? ` · ${clock(stage.entered_at)}${stage.completed_at ? ` → ${clock(stage.completed_at)}` : ""}` : ""}{dur ? ` · ${dur}` : ""}
          </span>
        )}
        {seq && <span className="rg-node-sub rg-node-seq">{seq}</span>}
      </div>
      {expanded && (
        <div className="rg-group-tasks">
          {stage.task_ids.map((taskId, taskIdx) => {
            const node = nodeByTask.get(taskId);
            const state = states[taskIdx];
            const tries = lifecycleTasks[taskId]?.tries.length ?? 0;
            const metrics = metricsByTask[taskId];
            const lane = node?.actual.assigned_to
              || node?.actual.affinity?.actual_owner
              || node?.planned.owner_role
              || "";
            return (
              <button
                key={taskId}
                type="button"
                className={`rg-task rg-state-${state}${searchTaskId === taskId ? " rg-search-hit" : ""}`}
                data-testid={`rg-task-${taskId}`}
                onClick={() => onSelectTask?.(taskId)}
                title={`${taskId} · ${state}${tries ? ` · try×${tries}` : ""}${lane ? ` · ${lane}` : ""}`}
              >
                <span className="rg-task-glyph" aria-hidden="true">{RG_GLYPH[state]}</span>
                <span className="rg-task-id">{taskId}</span>
                {tries > 1 && <span className="rg-task-try">try×{tries}</span>}
                {lane && <span className="rg-task-lane">{lane}</span>}
                {metrics && (
                  <span className="rg-task-durs" title={`queue wait ${formatSeconds(metrics.queue_wait_seconds)} · active ${formatSeconds(metrics.active_seconds)}`}>
                    q {formatSeconds(metrics.queue_wait_seconds)} · run {formatSeconds(metrics.active_seconds)}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Two-version diff layer: refs from task_map_history; task-id sets derived
// from execution_graph (superseded nodes = dropped from the current version).
function GhostDiff({
  current,
  nodes,
  prev,
}: {
  current: TaskMapHistoryEntry | null;
  nodes: DeliveryTraceNode[];
  prev: TaskMapHistoryEntry;
}) {
  const supersededIds = nodes.filter((node) => node.superseded).map((node) => node.task_id);
  const currentIds = nodes.filter((node) => !node.superseded).map((node) => node.task_id);
  return (
    <div className="rg-ghost-diff" data-testid="rg-ghost-diff">
      <div className="rg-ghost-col">
        <strong>▒ v{prev.version} superseded</strong>
        <code title={prev.ref}>{prev.ref || prev.artifact_id}</code>
        {prev.reason && <span className="muted">{prev.reason}</span>}
        <span className="muted">superseded-only tasks ({supersededIds.length})</span>
        <span className="rg-ghost-ids">
          {supersededIds.length
            ? supersededIds.map((id) => <code key={id} title={id}>{id}</code>)
            : <span className="muted">none in graph</span>}
        </span>
      </div>
      <div className="rg-ghost-col">
        <strong>v{current?.version ?? "?"} current</strong>
        <code title={current?.ref ?? ""}>{current?.ref || current?.artifact_id || "—"}</code>
        <span className="muted">current tasks ({currentIds.length})</span>
        <span className="rg-ghost-ids">
          {currentIds.length
            ? currentIds.map((id) => <code key={id} title={id}>{id}</code>)
            : <span className="muted">none in graph</span>}
        </span>
      </div>
    </div>
  );
}
