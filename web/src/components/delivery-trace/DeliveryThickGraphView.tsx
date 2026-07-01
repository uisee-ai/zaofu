import { useMemo, useState, type CSSProperties } from "react";

import type {
  DeliveryBehaviorOverlay,
  DeliveryEvalOverlay,
  DeliveryRunTraceSpan,
  DeliveryThickGraphEdge,
  DeliveryThickGraphNode,
  DeliveryThickTrace,
  DeliveryTrace,
} from "../../api/types";
import type { PageId } from "../../app/sharedTypes";
import { clockLabel, copyText, dtTone, formatDuration } from "./DeliveryTraceViewUtils";

interface DeliveryThickGraphViewProps {
  onOpenPage?: (page: PageId) => void;
  onSelectTask?: (taskId: string) => void;
  trace: DeliveryTrace;
}

type LayerName = "plan" | "runtime" | "gate" | "behavior" | "eval" | "artifact";

const LAYER_LABELS: Record<LayerName, string> = {
  plan: "Plan",
  runtime: "Runtime",
  gate: "Gate",
  behavior: "Behavior",
  eval: "Eval",
  artifact: "Artifact",
};

export function DeliveryThickGraphView({ onOpenPage, onSelectTask, trace }: DeliveryThickGraphViewProps) {
  const thick = trace.thick_trace;
  const nodes = thick?.graph?.nodes ?? [];
  const edges = thick?.graph?.edges ?? [];
  const [layers, setLayers] = useState<Record<string, boolean>>(() => ({
    plan: true,
    runtime: true,
    gate: true,
    behavior: true,
    eval: true,
    artifact: true,
  }));
  // design 101 §10 G-TRIAGE: attention mode collapses planned-but-unstarted
  // task nodes (the "pending" DAG future); raw mode shows everything.
  const [mode, setMode] = useState<"attention" | "raw">("attention");
  const defaultNodeId = useMemo(() => defaultSelectedNode(nodes), [nodes]);
  const [selectedId, setSelectedId] = useState("");
  const selectedNode = nodes.find((node) => node.id === selectedId) ?? nodes.find((node) => node.id === defaultNodeId) ?? null;
  const isCollapsed = (node: DeliveryThickGraphNode) =>
    isPendingTaskNode(node) || isInfoDetailNode(node);
  const visibleNodes = useMemo(
    () =>
      nodes.filter(
        (node) =>
          layers[nodeLayer(node)] !== false &&
          (mode === "raw" || !isCollapsed(node)),
      ),
    [layers, nodes, mode],
  );
  const collapsedHidden = useMemo(
    () =>
      nodes.filter(
        (node) => layers[nodeLayer(node)] !== false && isCollapsed(node),
      ).length,
    [layers, nodes],
  );
  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () => edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
    [edges, visibleIds],
  );

  if (!thick || !nodes.length) {
    return null;
  }

  const layerNames = (thick.graph.layers.length ? thick.graph.layers : Object.keys(LAYER_LABELS))
    .filter((layer): layer is LayerName => layer in LAYER_LABELS);

  const selectNode = (node: DeliveryThickGraphNode) => {
    setSelectedId(node.id);
    if (node.kind === "task" && node.task_id) onSelectTask?.(node.task_id);
  };

  return (
    <section className="dt-thick" data-testid="delivery-thick-graph">
      <div className="dt-thick-head">
        <div>
          <h3>Unified Delivery Graph</h3>
          <span className="muted">
            {thick.graph.node_count} nodes / {thick.graph.edge_count} edges / {thick.span_count} spans
          </span>
        </div>
        <div className="dt-thick-layer-bar" aria-label="Delivery graph layers">
          {layerNames.map((layer) => (
            <button
              key={layer}
              type="button"
              className={`dt-thick-layer ${layers[layer] !== false ? "active" : ""}`}
              aria-pressed={layers[layer] !== false}
              onClick={() => setLayers((prev) => ({ ...prev, [layer]: prev[layer] === false }))}
            >
              {LAYER_LABELS[layer]}
              <small>{nodes.filter((node) => nodeLayer(node) === layer).length}</small>
            </button>
          ))}
        </div>
        <div
          className="dt-thick-mode-bar"
          role="group"
          aria-label="Graph view mode"
          data-testid="graph-mode-toggle"
          style={{ display: "flex", gap: 6, alignItems: "center" }}
        >
          <button
            type="button"
            className={`dt-thick-layer ${mode === "attention" ? "active" : ""}`}
            aria-pressed={mode === "attention"}
            onClick={() => setMode("attention")}
          >
            Attention
          </button>
          <button
            type="button"
            className={`dt-thick-layer ${mode === "raw" ? "active" : ""}`}
            aria-pressed={mode === "raw"}
            onClick={() => setMode("raw")}
          >
            Raw
          </button>
          {mode === "attention" && collapsedHidden > 0 ? (
            <button
              type="button"
              className="dt-thick-layer"
              data-testid="graph-pending-chip"
              title="planned task nodes + passing developer-detail nodes collapsed — click for Raw"
              onClick={() => setMode("raw")}
              style={{ opacity: 0.7 }}
            >
              +{collapsedHidden} collapsed
            </button>
          ) : null}
        </div>
      </div>

      <div className="dt-thick-grid">
        <GraphCanvas
          edges={visibleEdges}
          nodes={visibleNodes}
          onSelect={selectNode}
          selectedId={selectedNode?.id ?? ""}
        />
        <UnifiedInspector
          behaviors={thick.behaviors}
          evals={thick.evals}
          node={selectedNode}
          relatedLoopIds={trace.related_loop_ids ?? thick.related_loop_ids ?? []}
          onOpenPage={onOpenPage}
        />
      </div>

      <div className="dt-thick-bottom">
        <Waterfall spans={thick.spans} />
        <OverlayPanel thick={thick} />
      </div>
    </section>
  );
}

function GraphCanvas({
  edges,
  nodes,
  onSelect,
  selectedId,
}: {
  edges: DeliveryThickGraphEdge[];
  nodes: DeliveryThickGraphNode[];
  onSelect: (node: DeliveryThickGraphNode) => void;
  selectedId: string;
}) {
  const grouped = useMemo(() => {
    const out = new Map<LayerName, DeliveryThickGraphNode[]>();
    for (const node of nodes) {
      const layer = nodeLayer(node);
      if (!out.has(layer)) out.set(layer, []);
      out.get(layer)!.push(node);
    }
    return out;
  }, [nodes]);
  const layers = Array.from(grouped.keys());

  return (
    <div className="dt-thick-canvas" data-testid="dt-thick-canvas">
      {layers.map((layer) => (
        <section className="dt-thick-lane" key={layer} data-layer={layer}>
          <div className="dt-thick-lane-label">{LAYER_LABELS[layer]}</div>
          <div className="dt-thick-node-list">
            {grouped.get(layer)!.map((node) => (
              <button
                key={node.id}
                type="button"
                className={`dt-thick-node tone-${dtTone(String(node.status || ""))} ${selectedId === node.id ? "active" : ""}`}
                data-testid="dt-thick-node"
                onClick={() => onSelect(node)}
                title={node.id}
              >
                <span className="dt-thick-node-kind">{node.kind}</span>
                <strong>{node.label || node.id}</strong>
                <small>{node.status || node.task_id || node.id}</small>
              </button>
            ))}
          </div>
        </section>
      ))}
      <div className="dt-thick-edge-strip" data-testid="dt-thick-edges">
        {edges.slice(0, 18).map((edge) => (
          <span key={edge.id} className="dt-thick-edge" title={`${edge.source} -> ${edge.target}`}>
            {edge.kind}
          </span>
        ))}
        {edges.length > 18 ? <span className="dt-thick-edge muted">+{edges.length - 18}</span> : null}
      </div>
    </div>
  );
}

function UnifiedInspector({
  behaviors,
  evals,
  node,
  onOpenPage,
  relatedLoopIds,
}: {
  behaviors: DeliveryBehaviorOverlay[];
  evals: DeliveryEvalOverlay[];
  node: DeliveryThickGraphNode | null;
  onOpenPage?: (page: PageId) => void;
  relatedLoopIds: string[];
}) {
  if (!node) {
    return (
      <aside className="dt-thick-inspector" data-testid="dt-thick-inspector">
        <h3>Inspector</h3>
        <p className="muted">Select a graph node.</p>
      </aside>
    );
  }
  const linkedBehaviors = behaviors.filter((item) => node.behavior_ids?.includes(item.behavior_id));
  const linkedEvals = evals.filter((item) => node.eval_ids?.includes(item.eval_id));
  const eventIds = stringList(node.event_ids);
  const traceIds = traceIdsForNode(node);
  const primaryTraceId = traceIds[0] ?? "";
  const openLoop = (loopId: string) => {
    writeBehaviorLoopLink(loopId, stageForGraphNode(node), behaviorLoopNodeIdForGraphNode(node));
    onOpenPage?.("behavior-loop");
  };
  const openEventTrace = (traceId: string) => {
    writeTraceExplorerLink(traceId);
    onOpenPage?.("traces");
  };
  return (
    <aside className="dt-thick-inspector" data-testid="dt-thick-inspector">
      <div className="dt-thick-panel-head">
        <div>
          <h3>{node.label || node.id}</h3>
          <span className={`badge badge-${dtTone(String(node.status || ""))}`}>
            {node.status || node.kind}
          </span>
        </div>
        <button type="button" className="dt-trace-chip" onClick={() => copyText(node.id)}>
          copy
        </button>
      </div>
      <dl className="dt-thick-kv">
        <div><dt>Kind</dt><dd>{node.kind}</dd></div>
        <div><dt>ID</dt><dd title={node.id}>{node.id}</dd></div>
        {node.task_id ? <div><dt>Task</dt><dd>{node.task_id}</dd></div> : null}
        {stringList(node.task_ids).length ? <div><dt>Tasks</dt><dd>{stringList(node.task_ids).join(", ")}</dd></div> : null}
        {eventIds.length ? <div><dt>Events</dt><dd>{eventIds.join(", ")}</dd></div> : null}
      </dl>
      <div className="dt-thick-evidence-actions" aria-label="Graph evidence actions">
        <button type="button" onClick={() => onOpenPage?.("delivery-trace")}>
          Open Trace
        </button>
        <button type="button" disabled={!primaryTraceId} onClick={() => primaryTraceId && openEventTrace(primaryTraceId)}>
          Event Trace
        </button>
        <button type="button" disabled={!relatedLoopIds.length} onClick={() => relatedLoopIds[0] && openLoop(relatedLoopIds[0])}>
          Open Loop
        </button>
      </div>
      {linkedBehaviors.length ? (
        <OverlayList title="Behavior" items={linkedBehaviors} idKey="behavior_id" />
      ) : null}
      {linkedEvals.length ? (
        <OverlayList title="Eval" items={linkedEvals} idKey="eval_id" />
      ) : null}
      {relatedLoopIds.length ? (
        <div className="dt-thick-overlay-list">
          <h4>Related Loop</h4>
          {relatedLoopIds.slice(0, 4).map((loopId) => (
            <button
              className="dt-thick-overlay-row dt-thick-loop-link"
              key={loopId}
              type="button"
              onClick={() => openLoop(loopId)}
            >
              <strong>{loopId}</strong>
              <span>open</span>
              <small>observe to diagnose to act to verify to learn</small>
            </button>
          ))}
        </div>
      ) : null}
      {node.source_refs ? (
        <pre className="dt-thick-json">{JSON.stringify(node.source_refs, null, 2)}</pre>
      ) : null}
    </aside>
  );
}

function Waterfall({ spans }: { spans: DeliveryRunTraceSpan[] }) {
  const rows = useMemo(() => spanRows(spans), [spans]);
  return (
    <section className="dt-thick-waterfall" data-testid="dt-thick-waterfall">
      <div className="dt-thick-panel-head">
        <h3>Waterfall</h3>
        <span className="muted">{spans.length} spans</span>
      </div>
      {rows.length ? (
        <div className="dt-thick-wf-rows">
          {rows.slice(0, 18).map((row) => (
            <div className="dt-thick-wf-row" key={row.span.span_id}>
              <div className="dt-thick-wf-label">
                <strong>{row.span.name || row.span.run_id || row.span.span_id}</strong>
                <small>{row.span.task_id || row.span.role || row.span.kind || "runtime"}</small>
              </div>
              <div className="dt-thick-wf-track" title={`${clockLabel(row.span.started_at)} - ${clockLabel(row.span.ended_at)}`}>
                <span
                  className={`dt-thick-wf-bar tone-${dtTone(row.span.status)}`}
                  style={{
                    "--dt-wf-left": `${row.left}%`,
                    "--dt-wf-width": `${row.width}%`,
                  } as CSSProperties}
                />
              </div>
              <span className="dt-thick-wf-duration">{formatDuration(row.durationMs)}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted">No spans projected.</p>
      )}
    </section>
  );
}

function OverlayPanel({ thick }: { thick: DeliveryThickTrace }) {
  return (
    <section className="dt-thick-overlay" data-testid="dt-thick-overlay">
      <div className="dt-thick-panel-head">
        <h3>Behavior / Eval</h3>
        <span className="muted">
          {thick.behaviors.length} behavior / {thick.evals.length} eval
        </span>
      </div>
      <OverlayList title="Behavior" items={thick.behaviors} idKey="behavior_id" />
      <OverlayList title="Eval" items={thick.evals} idKey="eval_id" />
      {thick.improvement_candidates.length ? (
        <div className="dt-thick-candidates">
          <h4>Improvement candidates</h4>
          {thick.improvement_candidates.slice(0, 6).map((item) => (
            <div className="dt-thick-candidate" key={String(item.candidate_id ?? item.fingerprint ?? item.source_kind)}>
              <strong>{String(item.source_kind ?? item.kind ?? "candidate")}</strong>
              <small>{String(item.fingerprint ?? "")}</small>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function OverlayList<T extends object>({
  idKey,
  items,
  title,
}: {
  idKey: string;
  items: T[];
  title: string;
}) {
  if (!items.length) {
    return null;
  }
  return (
    <div className="dt-thick-overlay-list">
      <h4>{title}</h4>
      {items.slice(0, 8).map((item) => {
        const data = item as Record<string, unknown>;
        const id = String(data[idKey] ?? data.kind ?? "");
        const status = String(data.status ?? "");
        return (
          <div className={`dt-thick-overlay-row tone-${dtTone(status)}`} key={id}>
            <strong>{String(data.kind ?? id)}</strong>
            <span>{status || "observed"}</span>
            <small>{String(data.summary ?? data.owner_event_type ?? data.evaluator ?? "")}</small>
          </div>
        );
      })}
    </div>
  );
}

const PENDING_TASK_STATUS = new Set([
  "",
  "not_created",
  "backlog",
  "ready",
  "queued",
  "pending",
  "planned",
]);
// A task node not yet started — the planned DAG future. Collapsed in
// attention mode (design 101 §10 G-TRIAGE T1).
function isPendingTaskNode(node: DeliveryThickGraphNode): boolean {
  return (
    node.kind === "task" &&
    PENDING_TASK_STATUS.has(String(node.status || "").toLowerCase())
  );
}

// design 101 §10 G-TRIAGE T2 — severity / actionability split. A node is
// operator-actionable if it has a problem status; otherwise behavior / eval
// / artifact / span detail nodes are developer-info and are muted in
// attention mode (they only matter when something is wrong). task / stage /
// gate spine nodes are always kept.
const ACTIONABLE_STATUS = new Set([
  "failed",
  "blocked",
  "rework",
  "warn",
  "warning",
  "error",
  "stuck",
]);
const DETAIL_KINDS = new Set(["behavior", "eval", "artifact", "span"]);
function isInfoDetailNode(node: DeliveryThickGraphNode): boolean {
  return (
    DETAIL_KINDS.has(String(node.kind)) &&
    !ACTIONABLE_STATUS.has(String(node.status || "").toLowerCase())
  );
}

function defaultSelectedNode(nodes: DeliveryThickGraphNode[]): string {
  return (
    nodes.find((node) => node.kind === "behavior" && ["failed", "warn"].includes(String(node.status)))?.id
    ?? nodes.find((node) => node.kind === "eval" && ["failed", "warn"].includes(String(node.status)))?.id
    ?? nodes.find((node) => node.kind === "task")?.id
    ?? nodes[0]?.id
    ?? ""
  );
}

function nodeLayer(node: DeliveryThickGraphNode): LayerName {
  if (node.kind === "task") return "plan";
  if (node.kind === "gate") return "gate";
  if (node.kind === "behavior") return "behavior";
  if (node.kind === "eval") return "eval";
  if (node.kind === "artifact") return "artifact";
  return "runtime";
}

function writeBehaviorLoopLink(loopId: string, stage: string, nodeId = "") {
  const params = new URLSearchParams(window.location.search);
  params.set("page", "behavior-loop");
  params.set("loop_id", loopId);
  params.set("stage", stage);
  if (nodeId) params.set("node_id", nodeId);
  else params.delete("node_id");
  window.history.replaceState(null, "", `?${params.toString()}`);
}

function writeTraceExplorerLink(traceId: string) {
  const params = new URLSearchParams(window.location.search);
  params.set("page", "traces");
  params.set("trace_id", traceId);
  window.history.replaceState(null, "", `?${params.toString()}`);
}

function behaviorLoopNodeIdForGraphNode(node: DeliveryThickGraphNode): string {
  const behaviorId = node.behavior_ids?.[0];
  if (behaviorId) return `behavior:${behaviorId}`;
  const evalId = node.eval_ids?.[0];
  if (evalId) return `eval:${evalId}`;
  return "";
}

function stageForGraphNode(node: DeliveryThickGraphNode): "observe" | "diagnose" | "act" | "verify" | "learn" {
  const layer = nodeLayer(node);
  if (layer === "artifact") return "learn";
  if (layer === "gate") return "verify";
  if (layer === "behavior" || layer === "eval") return "diagnose";
  return "observe";
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item)).filter(Boolean);
}

function traceIdsForNode(node: DeliveryThickGraphNode): string[] {
  const refs = node.source_refs && typeof node.source_refs === "object" && !Array.isArray(node.source_refs)
    ? node.source_refs as Record<string, unknown>
    : {};
  return Array.from(new Set([
    String(node.trace_id || ""),
    ...stringList(node.trace_ids),
    String(refs.trace_id || ""),
    ...stringList(refs.trace_ids),
  ].filter(Boolean)));
}

function spanRows(spans: DeliveryRunTraceSpan[]): Array<{
  durationMs: number | null;
  left: number;
  span: DeliveryRunTraceSpan;
  width: number;
}> {
  const stamped = spans.map((span) => ({
    span,
    start: Date.parse(span.started_at || ""),
    end: Date.parse(span.ended_at || ""),
  }));
  const valid = stamped.filter((item) => !Number.isNaN(item.start));
  if (!valid.length) {
    return spans.map((span) => ({ span, left: 0, width: 100, durationMs: span.duration_ms ?? null }));
  }
  const min = Math.min(...valid.map((item) => item.start));
  const max = Math.max(...valid.map((item) => Number.isNaN(item.end) ? item.start : item.end));
  const spanWindow = Math.max(1, max - min);
  return stamped.map((item) => {
    if (Number.isNaN(item.start)) {
      return { span: item.span, left: 0, width: 100, durationMs: item.span.duration_ms ?? null };
    }
    const end = Number.isNaN(item.end) ? item.start : item.end;
    const durationMs = item.span.duration_ms ?? Math.max(0, end - item.start);
    return {
      span: item.span,
      left: Math.max(0, ((item.start - min) / spanWindow) * 100),
      width: Math.max(2, (Math.max(1, end - item.start) / spanWindow) * 100),
      durationMs,
    };
  });
}
