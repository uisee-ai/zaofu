// Delivery Graph View (doc 69 §14) — zero-dep SVG DAG (reactflow/dagre
// uninstallable here → design's SVG fallback). Columns by wave, blocked_by
// edges, status-color borders, critical path thicker, <=1 priority badge.
// 3-tier interaction (§14.5.1): minimal node + hover popover + click panel.
import { type CSSProperties, useState } from "react";

import type { DeliveryTrace, DeliveryTraceNode } from "../../api/types";

const COL_W = 200, ROW_H = 88, NODE_W = 150, NODE_H = 54, PAD = 24;
const TONE: Record<string, string> = {
  done: "#16a34a", cancelled: "#16a34a",
  in_progress: "#2563eb", review: "#2563eb", test: "#2563eb", judge: "#2563eb", dispatched: "#2563eb",
  rework: "#d97706", blocked: "#dc2626", failed: "#dc2626",
};
const tone = (s: string) => TONE[s] ?? "#9ca3af";

// design 101 §10 G-TRIAGE — severity hierarchy + started/pending split.
type Sev = "critical" | "high" | "warn" | "info" | "";
function severity(n: DeliveryTraceNode): Sev {
  const a = n.actual;
  if (a.health?.stuck) return "critical";
  if (["blocked", "failed", "rework"].includes(a.status)) return "high";
  if (a.affinity?.drifted) return "warn";
  if (n.drift.length) return "info";
  return "";
}
// A node is "started" (worth showing in attention mode) if it has a known
// active/terminal status, any severity signal, or is superseded. Everything
// else is planned-but-unstarted ("pending") DAG future → collapsed by default.
function started(n: DeliveryTraceNode): boolean {
  return TONE[n.actual.status] !== undefined || severity(n) !== "" || !!n.superseded;
}

function badge(n: DeliveryTraceNode, mode: "attention" | "raw"): string {
  const a = n.actual;
  if (a.health?.stuck) return "⛔";
  if (["blocked", "failed", "rework"].includes(a.status)) return "🟥";
  if (a.affinity?.drifted) return a.affinity.drift_kind === "multi_instance" ? "⟳" : "⚠";
  // info-level drift is developer-noise — muted in attention mode (T2).
  if (n.drift.length) return mode === "attention" ? "" : "●";
  if (n.superseded) return "⊘";
  return "";
}

export function GraphView({
  onSelectNode,
  selectedNodeId,
  showInlinePanel = true,
  trace,
}: {
  onSelectNode?: (taskId: string) => void;
  selectedNodeId?: string;
  showInlinePanel?: boolean;
  trace: DeliveryTrace;
}) {
  const allNodes = trace.execution_graph.nodes;
  const edges = trace.execution_graph.edges;
  const [hover, setHover] = useState("");
  const [selected, setSelected] = useState("");
  const [mode, setMode] = useState<"attention" | "raw">("attention");
  const activeSelected = selectedNodeId ?? selected;

  // T1/T3: attention mode collapses planned-but-unstarted ("pending") nodes;
  // raw mode shows the full DAG. Layout is computed over visible nodes only
  // so collapsing leaves no gaps.
  const nodes = mode === "raw" ? allNodes : allNodes.filter((n) => started(n));
  const hiddenCount = allNodes.length - nodes.length;

  const waves = Array.from(new Set(nodes.map((n) => n.planned.wave ?? 0))).sort((a, b) => a - b);
  const pos: Record<string, { x: number; y: number }> = {};
  const rows: Record<number, number> = {};
  nodes.forEach((n) => {
    const w = n.planned.wave ?? 0;
    const col = waves.indexOf(w);
    const row = rows[w] ?? 0;
    rows[w] = row + 1;
    pos[n.task_id] = { x: PAD + col * COL_W, y: PAD + row * ROW_H };
  });
  const width = PAD * 2 + Math.max(1, waves.length) * COL_W;
  const height = PAD * 2 + Math.max(1, ...Object.values(rows)) * ROW_H;
  const byId: Record<string, DeliveryTraceNode> = {};
  nodes.forEach((n) => { byId[n.task_id] = n; });
  const sel = activeSelected ? byId[activeSelected] : null;

  const btn = (active: boolean): CSSProperties => ({
    padding: "2px 10px",
    fontSize: 12,
    border: "1px solid var(--border, #cbd5e1)",
    borderRadius: 6,
    background: active ? "#1f2937" : "transparent",
    color: active ? "#f9fafb" : "inherit",
    cursor: "pointer",
  });
  return (
    <div className="delivery-graph" data-testid="delivery-graph">
      <div
        role="group"
        aria-label="Graph view mode"
        data-testid="graph-mode-toggle"
        style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}
      >
        <button type="button" style={btn(mode === "attention")} aria-pressed={mode === "attention"} onClick={() => setMode("attention")}>
          Attention
        </button>
        <button type="button" style={btn(mode === "raw")} aria-pressed={mode === "raw"} onClick={() => setMode("raw")}>
          Raw
        </button>
        {mode === "attention" && hiddenCount > 0 ? (
          <button
            type="button"
            data-testid="graph-pending-chip"
            onClick={() => setMode("raw")}
            style={{ ...btn(false), opacity: 0.7 }}
            title="planned / unstarted nodes collapsed — click to show all"
          >
            +{hiddenCount} planned
          </button>
        ) : null}
      </div>
      <div style={{ display: "flex", gap: 12 }}>
      <div style={{ overflow: "auto", flex: 1, position: "relative" }}>
        <svg width={width} height={height} role="img" aria-label="delivery graph">
          {edges.map((e, i) => {
            const a = pos[e.from], b = pos[e.to];
            if (!a || !b) return null;
            const crit = byId[e.from]?.actual.on_critical_path && byId[e.to]?.actual.on_critical_path;
            return (
              <line key={i} x1={a.x + NODE_W} y1={a.y + NODE_H / 2} x2={b.x} y2={b.y + NODE_H / 2}
                stroke={e.status === "pending" ? "#dc2626" : "#94a3b8"}
                strokeWidth={crit ? 3 : 1.5}
                strokeDasharray={e.status === "pending" ? "5,4" : undefined} />
            );
          })}
          {nodes.map((n) => {
            const p = pos[n.task_id], c = tone(n.actual.status), bg = badge(n, mode);
            return (
              <g key={n.task_id} transform={`translate(${p.x},${p.y})`} opacity={n.superseded ? 0.45 : 1} style={{ cursor: "pointer" }}
                onMouseEnter={() => setHover(n.task_id)} onMouseLeave={() => setHover("")}
                onClick={() => {
                  if (onSelectNode) onSelectNode(n.task_id);
                  else setSelected(n.task_id);
                }} data-testid="graph-node">
                <rect width={NODE_W} height={NODE_H} rx={6}
                  fill={activeSelected === n.task_id ? "#f1f5f9" : "#fff"}
                  stroke={n.superseded ? "#9ca3af" : c}
                  strokeDasharray={n.superseded ? "4,3" : undefined}
                  strokeWidth={n.actual.on_critical_path ? 3 : 1.5} />
                <text x={10} y={21} fontSize={13} fontWeight={600} fill="#111827">{n.task_id}</text>
                {bg && <text x={NODE_W - 22} y={21} fontSize={14}>{bg}</text>}
                <text x={10} y={40} fontSize={11} fill={c}>{n.actual.status}</text>
              </g>
            );
          })}
        </svg>
        {hover && byId[hover] && <HoverCard node={byId[hover]} x={pos[hover].x} y={pos[hover].y} />}
      </div>
      {showInlinePanel && sel && <NodePanel node={sel} onClose={() => setSelected("")} />}
      </div>
    </div>
  );
}

function HoverCard({ node, x, y }: { node: DeliveryTraceNode; x: number; y: number }) {
  const a = node.actual;
  const lines: string[] = [];
  if (a.health?.stuck) lines.push(`⛔ stuck ${a.health.heartbeat_age_seconds}s`);
  if (["failed", "rework", "blocked"].includes(a.status)) lines.push(`🟥 ${a.status}`);
  if (a.affinity?.drifted) lines.push(`⚠ ${a.affinity.planned_owner || a.affinity.planned_role || "?"}→${a.affinity.actual_owner || "?"}`);
  if (a.duration_seconds != null) lines.push(`⏱ ${a.duration_seconds}s`);
  return (
    <div style={{ position: "absolute", left: x, top: y + NODE_H + 4, zIndex: 20,
      background: "#1f2937", color: "#f9fafb", padding: "6px 10px", borderRadius: 6,
      fontSize: 12, maxWidth: 280, pointerEvents: "none" }}>
      <div style={{ fontWeight: 600 }}>{node.task_id} · {a.status}</div>
      {lines.length ? lines.map((l, i) => <div key={i}>{l}</div>) : <div className="muted">ok</div>}
    </div>
  );
}

function NodePanel({ node, onClose }: { node: DeliveryTraceNode; onClose: () => void }) {
  const a = node.actual;
  const row = (k: string, v: string) => (
    <div style={{ display: "flex", gap: 8, padding: "2px 0" }}>
      <span className="muted" style={{ minWidth: 64 }}>{k}</span>
      <span style={{ wordBreak: "break-word" }}>{v}</span>
    </div>
  );
  return (
    <div className="graph-node-panel" data-testid="graph-node-panel"
      style={{ width: 300, borderLeft: "1px solid #e5e7eb", padding: "10px 14px", fontSize: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <strong>{node.task_id} [{a.status}]</strong>
        <button type="button" onClick={onClose} aria-label="close">✕</button>
      </div>
      {row("planned", `${node.planned.owner_role || "-"} · wave ${node.planned.wave ?? "-"}`)}
      {row("actual", a.assigned_to || "-")}
      {a.affinity?.drifted && row("affinity ⚠", `${a.affinity.drift_kind} · ${a.affinity.instances_history.join("→") || a.affinity.actual_owner}`)}
      {a.agent_summary && row("agent", `launched ${a.agent_summary.launched}/${a.agent_summary.expected} · exec ${a.agent_summary.executed}`)}
      {a.duration_seconds != null && row("duration", `${a.duration_seconds}s`)}
      {a.health?.stuck && row("health ⛔", `stale ${a.health.heartbeat_age_seconds}s`)}
      {a.on_critical_path && row("★", "on critical path")}
      {!!a.changed_files?.length && row("files", a.changed_files.join(", "))}
      {!!node.drift.length && row("drift", `${node.drift.length} item(s)`)}
    </div>
  );
}
