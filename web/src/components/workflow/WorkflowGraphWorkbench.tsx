import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Controls, MiniMap, MarkerType, ReactFlow, useNodesInitialized, useReactFlow, useStore, type Node, type Edge, type NodeChange, applyNodeChanges } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./workflowGraph.css";
import type { ActionResponse, RecentEvent } from "../../api/types";
import { useWorkflowGraph } from "./useWorkflowGraph";
import { asRecord, asRecordArray } from "../../app/shared";
import PlanReviewReader from "../delivery-trace/PlanReviewReader";
import PlanArtifactChat from "./PlanArtifactChat";
import { workflowNodeTypes } from "./WorkflowGraphNode";
import { X, Code, Maximize2, Minimize2, SlidersHorizontal } from "lucide-react";
import { activeStatus, expandedNodeIds, GraphLayoutCache, graphViews, relatedNodes, statusLabels, workflowSummaryIds, type GraphView } from "./sdlcGraphModel";

const graphFitOptions = { padding: 0.22, maxZoom: 0.8, minZoom: 0 };

export default function WorkflowGraphWorkbench({ projectId, requestId, revision, onAction, actionReady, onOpenRaw, recentEvents, liveState }: {
  projectId: string; requestId: string; revision: string;
  onAction: (action: string, payload: Record<string, unknown>) => Promise<ActionResponse>;
  actionReady: boolean;
  onOpenRaw?: () => void;
  recentEvents: RecentEvent[];
  liveState: string;
}) {
  const [actionError, setError] = useState("");
  const [view, setView] = useState<GraphView>(() => {
    const value = new URLSearchParams(location.search).get("graph");
    return graphViews.some(([id]) => id === value) ? value as GraphView : "workflow";
  });
  const [selected, setSelected] = useState(() => new URLSearchParams(location.search).get("node") || "");
  const [direction, setDirection] = useState<"all" | "upstream" | "downstream">("all");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [fullStructure, setFullStructure] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [collapsed, setCollapsed] = useState(new Set<string>());
  const [asOf, setAsOf] = useState(0);
  const [cursor, setCursor] = useState("");
  const [planId, setPlanId] = useState("");
  const [showTools, setShowTools] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const positions = useRef(new Map<string, { x: number; y: number }>());
  const positionScope = useRef("");
  const layoutCache = useRef(new GraphLayoutCache());
  const [canvasScope, setCanvasScope] = useState("");
  const [canvasNodes, setCanvasNodes] = useState<Node[]>([]);
  const { graph, error: readError, updatedAt, refresh } = useWorkflowGraph(projectId, requestId, revision, asOf, recentEvents, liveState);
  const error = actionError || readError;
  useEffect(() => {
    positions.current.clear(); setError(""); setAsOf(0); setPlanId(""); setCollapsed(new Set()); setSearch(""); setStatus("");
    const query = new URLSearchParams(location.search);
    setSelected(query.get("request") === requestId ? query.get("node") || "" : "");
  }, [projectId, requestId]);
  const pick = useCallback((id: string) => {
    setSelected(id); const url = new URL(location.href);
    url.searchParams.set("request", requestId); url.searchParams.set("graph", view); url.searchParams.set("node", id);
    history.replaceState(null, "", url);
  }, [requestId, view]);
  const filtered = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    const reachable = direction !== "all" && selected ? relatedNodes(selected, graph.edges, direction) : null;
    const expanded = expandedNodeIds(graph.nodes, graph.edges, collapsed);
    const summary = view === "workflow" && !fullStructure ? workflowSummaryIds(graph.nodes) : null;
    const needle = search.trim().toLowerCase();
    const nodes = graph.nodes.filter(n => (n.views.includes(view) || n.id === selected) && expanded.has(n.id) && (!reachable || reachable.has(n.id))
      && (!summary || summary.has(n.id) || n.id === selected)
      && (!status || n.status === status) && (!needle || `${n.id} ${n.label} ${JSON.stringify(n.data)}`.toLowerCase().includes(needle)));
    const ids = new Set(nodes.map(n => n.id));
    return { nodes, edges: graph.edges.filter(e => e.views.includes(view) && ids.has(e.source) && ids.has(e.target)) };
  }, [graph, view, direction, selected, collapsed, search, status, fullStructure]);
  const layoutScope = JSON.stringify([projectId, requestId, view, direction,
    direction === "all" ? "" : selected, [...collapsed].sort(), search.trim().toLowerCase(), status, fullStructure, asOf]);
  useEffect(() => {
    // Explicit navigation gets a compact layout; live polls preserve dragged nodes and the viewport.
    if (positionScope.current !== layoutScope) {
      positions.current.clear(); positionScope.current = layoutScope;
    }
    const layout = layoutCache.current.get(filtered.nodes, filtered.edges);
    for (const n of filtered.nodes) {
      const key = `${view}:${n.id}`;
      if (!positions.current.has(key)) {
        const pos = { ...(layout.get(n.id) || { x: 0, y: 0 }) };
        while ([...positions.current].some(([k, p]) => k.startsWith(`${view}:`) && p.x === pos.x && p.y === pos.y)) pos.x += 310;
        positions.current.set(key, pos);
      }
    }
    setCanvasNodes(filtered.nodes.map(n => ({ id: n.id,
      position: positions.current.get(`${view}:${n.id}`) || layout.get(n.id) || { x: 0, y: 0 },
      selected: n.id === selected,
      type: "workflowCard",
      className: `sdlc-node sdlc-status-${n.status}`,
      data: { node: n },
    })));
    setCanvasScope(layoutScope);
  }, [filtered, selected, view, layoutScope]);
  const activeNodes = useMemo(() => new Set(filtered.nodes.filter(n => activeStatus(n.status)).map(n => n.id)), [filtered.nodes]);
  const canvasEdges: Edge[] = filtered.edges.map(e => ({ ...e, type: "default",
    label: fullStructure || e.source === selected || e.target === selected ? e.event || e.relation : undefined,
    labelStyle: { fill: "var(--text-secondary)", fontSize: "var(--text-10)" }, labelBgStyle: { fill: "var(--canvas)" },
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--text-tertiary)", width: 14, height: 14 },
    animated: !asOf && (activeNodes.has(e.source) || activeNodes.has(e.target)),
    style: { stroke: /rework|retry/.test(e.relation) ? "var(--warning)" : "var(--text-tertiary)", strokeWidth: 1.5, strokeDasharray: "5 6" },
  }));
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    for (const change of changes) if (change.type === "position" && change.position) positions.current.set(`${view}:${change.id}`, change.position);
    setCanvasNodes(nodes => applyNodeChanges(changes, nodes));
  }, [view]);
  const selectedNode = graph?.nodes.find(n => n.id === selected);
  const preview = graph?.plans.find(p => p.plan_id === planId);
  const pendingPlan = graph?.plans.find(p => p.status === "pending");
  async function approvePlan() {
    if (!preview || busy) return;
    setBusy(true);
    try {
      const result = await onAction(preview.actions?.approve || "plan-approve", { ...preview.action_payload, plan_id: preview.plan_id });
      if (!result.ok) setError(String(result.reason || result.status));
      else { setPlanId(""); await refresh(); }
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  }
  return <section className={`sdlc-workbench${expanded ? " sdlc-expanded" : ""}`} aria-label="Workflow 图工作台">
    <div className="sdlc-main">
      <header className="sdlc-heading">
        <nav className="sdlc-views" aria-label="Workflow graph views">{graphViews.map(([id, label]) =>
          <button key={id} type="button" aria-pressed={view === id} onClick={() => {
            setView(id); const url = new URL(location.href); url.searchParams.set("graph", id); history.replaceState(null, "", url);
          }}>{label}</button>
        )}</nav>
        <div className="sdlc-heading-actions">
          <button type="button" aria-label="Graph filters" aria-expanded={showTools} onClick={() => setShowTools(!showTools)}><SlidersHorizontal size={15} /> Filters</button>
          {view === "workflow" && onOpenRaw && <button type="button" disabled={Boolean(asOf)} title={asOf ? "Return to live to inspect this proposal's YAML" : "View version-bound YAML"} onClick={() => { setExpanded(false); onOpenRaw(); }}><Code size={15} /> Raw</button>}
          <button type="button" onClick={() => setExpanded(!expanded)} aria-label={expanded ? "收回工作台" : "展开工作台"}>{expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
        </div>
      </header>
      <div className="sdlc-status-row">
        <span className="sdlc-live" role="status"><i className={!asOf && !error ? "live" : ""} />{asOf ? "History" : error ? "Update delayed" : "Live"} · {graph?.scope.run_id || "Preview"}{updatedAt ? ` · ${updatedAt}` : ""}</span>
        {pendingPlan && !asOf && <button className="sdlc-review" type="button" title="Review pending plan" onClick={() => setPlanId(pendingPlan.plan_id)}>Review</button>}
      </div>
      {error && <p role="alert">{error} <button type="button" onClick={() => void refresh()}>Retry</button></p>}
      {!graph && !error && <p>正在读取流程与运行关系…</p>}
      {graph && <>
        {showTools && <div className="sdlc-toolbar">
          {view === "workflow" && <button type="button" aria-pressed={fullStructure} onClick={() => setFullStructure(!fullStructure)}>{fullStructure ? "返回阶段与执行概览" : "展开角色、汇聚与事件结构"}</button>}
          <label>Search <input aria-label="搜索图节点" placeholder="Module, task, agent or ID" value={search} onChange={e => setSearch(e.target.value)} /></label>
          <label>Status <select aria-label="图节点状态" value={status} onChange={e => setStatus(e.target.value)}><option value="">All</option>{[...new Set(graph.nodes.map(n => n.status))].sort().map(s => <option key={s} value={s}>{statusLabels[s] || s}</option>)}</select></label>
          <label>Relations <select aria-label="图关系范围" value={direction} onChange={e => setDirection(e.target.value as typeof direction)}><option value="all">All</option><option value="upstream">Upstream</option><option value="downstream">Downstream</option></select></label>
          <label>History <input aria-label="回放事件序号" type="number" min="1" max={graph.scope.head_seq} value={cursor} onChange={e => setCursor(e.target.value)} /></label>
          <button type="button" onClick={() => setAsOf(Math.min(graph.scope.head_seq, Math.max(0, Number(cursor) || 0)))}>回放</button>
          <button type="button" onClick={() => { setAsOf(0); setCursor(""); }}>回到实时</button>
        </div>}
        <div className={`sdlc-body${selectedNode ? " has-inspector" : ""}`}>
          <div className="sdlc-canvas" data-testid="workflow-graph-canvas">
            <ReactFlow key={canvasScope} nodes={canvasNodes} edges={canvasEdges} nodeTypes={workflowNodeTypes} onNodesChange={onNodesChange} onNodeClick={(_, node) => pick(node.id)} fitView fitViewOptions={graphFitOptions} minZoom={0} onlyRenderVisibleElements nodesConnectable={false} deleteKeyCode={null} aria-label="Workflow 关系图">
              <FitResizedCanvas /><Controls showInteractive={false} fitViewOptions={graphFitOptions} /><MiniMap pannable zoomable maskColor="var(--surface)" nodeColor="var(--text-tertiary)" style={{ width: 130, height: 85 }} />
            </ReactFlow>
            {!filtered.nodes.length && <p className="sdlc-empty">{graph.nodes.length ? "No nodes match these filters." : "The graph will appear as this workflow is prepared."}</p>}
          </div>
          {selectedNode && <aside className="sdlc-inspector" aria-label="图节点详情">
            <button className="sdlc-close" type="button" aria-label="Close node details" onClick={() => { setSelected(""); const url = new URL(location.href); url.searchParams.delete("node"); history.replaceState(null, "", url); }}><X size={16} /></button>
            <h3>{selectedNode.label}</h3><p>{statusLabels[selectedNode.status] || selectedNode.status}</p><code>{selectedNode.id}</code>
            {selectedNode.kind === "operation" && <div><p>Agent：{String(selectedNode.data.role_instance || "未绑定")} · Attempt：{String(selectedNode.data.active_attempt_id || "未观测")}</p>
              <p>Provider：{String(asRecord(selectedNode.data.agent_binding).backend || "未观测")} · 模型：{String(asRecord(selectedNode.data.agent_binding).model || "未观测")} · {asRecord(selectedNode.data.agent_binding).source === "provider_usage" ? "实际用量记录" : "配置快照"}</p>
              <h4>最近活动</h4>{asRecordArray(selectedNode.data.recent_activity).slice(-5).map(e => {
                const payload = asRecord(e.payload); return <p key={String(e.id)}>{String(e.ts || "")} · {String(payload.tool_name || e.type || "")}<small>{String(payload.tool_input ? JSON.stringify(payload.tool_input).slice(0, 240) : payload.reason || "")}</small></p>;
              })}</div>}
            <button type="button" onClick={() => setCollapsed(current => { const next = new Set(current); if (next.has(selected)) next.delete(selected); else next.add(selected); return next; })}>{collapsed.has(selected) ? "展开节点" : "收起节点"}</button>
            {Boolean(selectedNode.data.plan_id) && <button type="button" onClick={() => setPlanId(String(selectedNode.data.plan_id))}>打开 PRD / Plan Preview</button>}
            <h4>关联</h4>{graph.edges.filter(e => e.source === selected || e.target === selected).map(e => {
              const target = e.source === selected ? e.target : e.source; return <button type="button" key={e.id} onClick={() => pick(target)}>{e.source === selected ? "→" : "←"} {e.relation} · {graph.nodes.find(n => n.id === target)?.label || target}</button>;
            })}<details><summary>来源与执行证据</summary><pre>{JSON.stringify(selectedNode.data, null, 2)}</pre></details>
          </aside>}
        </div>
        <footer className="sdlc-footer"><span>{filtered.nodes.length} / {graph.nodes.length} nodes · {filtered.edges.length} relations · R{graph.scope.revision} · Event {graph.scope.source_seq}</span>
          <span>{asOf ? "Historical snapshot" : "Live updates · checks every 30s"}</span></footer>
        {showTools && <details className="sdlc-directory"><summary>Node directory</summary><div aria-label="图节点目录">{filtered.nodes.map(n => <button key={n.id} type="button" aria-pressed={selected === n.id} onClick={() => pick(n.id)} title={n.id}>{n.label}<small>{statusLabels[n.status] || n.status}</small></button>)}</div></details>}
        {graph.plans.length > 0 && <details className="sdlc-plans"><summary>PRD / Plan documents · {graph.plans.length}</summary>{graph.plans.map(p => <button type="button" key={p.plan_id} onClick={() => setPlanId(p.plan_id)}>PRD / Plan Preview · {p.status}</button>)}</details>}
        {preview && <section className="sdlc-plan" aria-label="Workflow PRD Plan Preview">
          <header className="sdlc-plan-toolbar"><strong>Plan Preview</strong>
            {preview.status === "pending" && <button type="button" disabled={!actionReady || busy || reviewBusy || !!asOf} onClick={() => void approvePlan()}>确认计划并继续</button>}
            <button type="button" disabled={reviewBusy || busy} onClick={() => setPlanId("")}>关闭预览</button>
          </header>
          <PlanArtifactChat preview={preview} projectId={projectId} disabled={!actionReady || !!asOf} onBusy={setReviewBusy} onApplied={refresh} />
          <details className="plan-supporting-review"><summary>审批概览与附件</summary><p className="muted">这里是只读审批资料；修改请在上方正文选中文字并引用到 Agent。</p><PlanReviewReader preview={preview} projectId={projectId} /></details>
        </section>}
      </>}
    </div>
  </section>;
}

function FitResizedCanvas() {
  const width = useStore(state => state.width);
  const height = useStore(state => state.height);
  const initialized = useNodesInitialized();
  const { fitView } = useReactFlow();
  const previous = useRef("");
  useEffect(() => {
    if (!width || !height || !initialized) return;
    const size = `${width}:${height}`;
    const changed = previous.current !== size;
    previous.current = size;
    // On narrow canvases, keep cards readable; the fit control still shows the whole graph.
    if (changed) void fitView({ ...graphFitOptions, minZoom: width <= 600 ? 0.65 : 0, duration: 150 });
  }, [width, height, initialized, fitView]);
  return null;
}
