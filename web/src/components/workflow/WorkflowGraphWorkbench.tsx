import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Controls, MiniMap, MarkerType, ReactFlow, useNodesInitialized, useReactFlow, useStore, type Node, type Edge, type NodeChange, applyNodeChanges } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./workflowGraph.css";
import type { ActionResponse, RecentEvent } from "../../api/types";
import { useWorkflowGraph } from "./useWorkflowGraph";
import { useWorkflowStructureNode } from "./useWorkflowStructureNode";
import { asRecord, asRecordArray } from "../../app/shared";
import PlanReviewReader from "../delivery-trace/PlanReviewReader";
import PlanArtifactChat from "./PlanArtifactChat";
import { workflowNodeTypes } from "./WorkflowGraphNode";
import WorkflowExecutionTimeline from "./WorkflowExecutionTimeline";
import WorkflowStageDetails from "./WorkflowStageDetails";
import { X, Code, Maximize2, Minimize2, SlidersHorizontal } from "lucide-react";
import { activeStatus, expandedNodeIds, GraphLayoutCache, graphViews, relatedNodes, statusLabels, workflowSummaryEdges, workflowSummaryIds, workflowSummaryPositions, type GraphView } from "./sdlcGraphModel";

const graphFitOptions = { padding: 0.22, maxZoom: 0.8, minZoom: 0.6 };

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
  const [showAllNodes, setShowAllNodes] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [collapsed, setCollapsed] = useState(new Set<string>());
  const [asOf, setAsOf] = useState(0);
  const [cursor, setCursor] = useState("");
  const [planId, setPlanId] = useState("");
  const [showTools, setShowTools] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [detailRequested, setDetailRequested] = useState(false);
  const positions = useRef(new Map<string, { x: number; y: number }>());
  const positionScope = useRef("");
  const layoutCache = useRef(new GraphLayoutCache());
  const [canvasScope, setCanvasScope] = useState("");
  const [canvasNodes, setCanvasNodes] = useState<Node[]>([]);
  const { graph, error: readError, updatedAt, refresh, requireFull } = useWorkflowGraph(projectId, requestId, revision, asOf, recentEvents, liveState);
  const partial = Boolean(graph && "partial" in graph && graph.partial);
  const selectedStructure = partial && graph && "partial" in graph ? graph : null;
  const structureNodeExists = Boolean(selectedStructure?.nodes.some(node => node.id === selected));
  const structureNode = useWorkflowStructureNode(projectId, requestId, asOf, selectedStructure, selected, refresh);
  const loadFull = useCallback(() => { setDetailRequested(true); requireFull(); }, [requireFull]);
  const error = actionError || readError;
  useEffect(() => {
    positions.current.clear(); setError(""); setAsOf(0); setPlanId(""); setCollapsed(new Set()); setSearch(""); setStatus(""); setDetailRequested(false);
    const query = new URLSearchParams(location.search);
    setSelected(query.get("request") === requestId ? query.get("node") || "" : "");
  }, [projectId, requestId]);
  useEffect(() => {
    if (partial && (view !== "workflow" || (selected && !structureNodeExists) || asOf)) loadFull();
    if (graph && !partial) setDetailRequested(false);
  }, [graph, partial, view, selected, structureNodeExists, asOf, loadFull]);
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
    const candidates = graph.nodes.filter(n => (n.views.includes(view) || n.id === selected) && expanded.has(n.id) && (!reachable || reachable.has(n.id))
      && (!summary || needle || summary.has(n.id) || n.id === selected)
      && (!status || n.status === status) && (!needle || `${n.id} ${n.label} ${JSON.stringify(n.data)}`.toLowerCase().includes(needle)));
    const priority = (n: typeof candidates[number]) => n.id === selected ? 0 :
      ["requirement", "goal", "run", "preparation"].includes(n.kind) ? 1 :
      ["failed", "blocked", "evidence_missing", "running", "in_progress"].includes(n.status) ? 2 :
      ["task", "module", "claim", "workflow"].includes(n.kind) ? 3 : 4;
    const stageSummary = summary && graph.nodes.some(node => node.id.startsWith("workflow:stage:"));
    const nodes = stageSummary || showAllNodes || needle || reachable || candidates.length <= 40 ? candidates :
      [...candidates].sort((a, b) => priority(a) - priority(b) || a.label.localeCompare(b.label)).slice(0, 40);
    const ids = new Set(nodes.map(n => n.id));
    const edges = stageSummary && !needle && !reachable && !status ? workflowSummaryEdges(graph.nodes, graph.edges, ids) : graph.edges.filter(e => e.views.includes(view) && ids.has(e.source) && ids.has(e.target));
    return { nodes, edges, hidden: candidates.length - nodes.length };
  }, [graph, view, direction, selected, collapsed, search, status, fullStructure, showAllNodes]);
  const layoutScope = JSON.stringify([projectId, requestId, view, direction,
    direction === "all" ? "" : selected, [...collapsed].sort(), search.trim().toLowerCase(), status, fullStructure, showAllNodes, asOf]);
  useEffect(() => {
    // Explicit navigation gets a compact layout; live polls preserve dragged nodes and the viewport.
    if (positionScope.current !== layoutScope) {
      positions.current.clear(); positionScope.current = layoutScope;
    }
    const layout = view === "workflow" && !fullStructure
      ? workflowSummaryPositions(filtered.nodes, filtered.edges)
      : layoutCache.current.get(filtered.nodes, filtered.edges);
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
      data: { node: n, horizontal: view === "workflow" && !fullStructure },
    })));
    setCanvasScope(layoutScope);
  }, [filtered, selected, view, layoutScope, fullStructure]);
  const activeNodes = useMemo(() => new Set(filtered.nodes.filter(n => activeStatus(n.status)).map(n => n.id)), [filtered.nodes]);
  const canvasEdges: Edge[] = filtered.edges.map(e => ({ ...e, type: "default",
    label: e.collapsedVia ? "折叠路径" : fullStructure || e.source === selected || e.target === selected ? e.event || e.relation : undefined,
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
  const runNode = graph?.nodes.find(n => n.kind === "run");
  const inspectedNode = partial ? structureNode.node : selectedNode;
  const plans = graph && "plans" in graph ? graph.plans : [];
  const preview = plans.find(p => p.plan_id === planId);
  const pendingPlan = plans.find(p => p.status === "pending");
  function chooseView(id: GraphView) {
    if (id !== "workflow" && partial) loadFull();
    setView(id); const url = new URL(location.href); url.searchParams.set("graph", id); history.replaceState(null, "", url);
  }
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
        <nav className="sdlc-views" aria-label="Workflow graph views">{graphViews.slice(0, 2).map(([id, label]) =>
          <button key={id} type="button" aria-pressed={view === id} onClick={() => chooseView(id)}>{label}</button>
        )}<label className="sdlc-more-views"><select aria-label="更多视图" value={view === "workflow" || view === "timeline" ? "" : view} onChange={event => { if (event.target.value) chooseView(event.target.value as GraphView); }}>
          <option value="">更多视图</option>{graphViews.slice(2).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select></label></nav>
        <div className="sdlc-heading-actions">
          <button type="button" aria-label="Graph filters" aria-expanded={showTools} onClick={() => { if (!showTools && partial) loadFull(); setShowTools(!showTools); }}><SlidersHorizontal size={15} /> Filters</button>
          {view === "workflow" && onOpenRaw && <button type="button" disabled={Boolean(asOf)} title={asOf ? "Return to live to inspect this proposal's YAML" : "View version-bound YAML"} onClick={() => { setExpanded(false); onOpenRaw(); }}><Code size={15} /> Raw</button>}
          <button type="button" onClick={() => setExpanded(!expanded)} aria-label={expanded ? "收回工作台" : "展开工作台"}>{expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
        </div>
      </header>
      <div className="sdlc-status-row">
        <span className="sdlc-live" role="status"><i className={!asOf && !error ? "live" : ""} />{asOf ? "History" : error ? "Update delayed" : "Live"} · {graph?.scope.run_id || "Preview"}{updatedAt ? ` · ${updatedAt}` : ""}</span>
        {graph?.scope.run_id && <span className="sdlc-live">Run 状态：{runNode ? statusLabels[runNode.status] || runNode.status : partial ? "尚未加载" : "未知"}</span>}
        {partial && <><span role="status">{detailRequested ? "正在读取完整运行关系和详情…" : "已显示流程结构；运行关系和计划按需读取。"}</span>
          <button type="button" onClick={() => error ? void refresh() : loadFull()} disabled={detailRequested && !error}>加载完整运行图和计划</button></>}
        {pendingPlan && !asOf && <button className="sdlc-review" type="button" title="Review pending plan" onClick={() => setPlanId(pendingPlan.plan_id)}>Review</button>}
      </div>
      {error && <p role="alert">{error} <button type="button" onClick={() => void refresh()}>Retry</button></p>}
      {!graph && !error && <p>正在读取流程与运行关系…</p>}
      {graph && <>
        {view === "workflow" && !fullStructure && <div className="sdlc-summary-note">{graph.nodes.some(node => node.id.startsWith("workflow:stage:")) ? "阶段概览：角色和执行详情已折叠；标记的路径经过原有折叠结构，条件请查看完整结构。" : "未提供阶段结构，显示当前可用的图节点。"} <button type="button" onClick={() => setFullStructure(true)}>展开流程结构</button></div>}
        {view !== "timeline" && Boolean(filtered.hidden) && <div className="sdlc-summary-note" role="status">为保持可读性，仅显示 40 个重点节点；{filtered.hidden} 个节点已折叠，连接可能不完整。 <button type="button" onClick={() => setShowAllNodes(true)}>显示全部</button></div>}
        {view !== "timeline" && showAllNodes && <div className="sdlc-summary-note"><button type="button" onClick={() => setShowAllNodes(false)}>返回重点节点</button></div>}
        {showTools && <div className="sdlc-toolbar">
          {view === "workflow" && <button type="button" aria-pressed={fullStructure} onClick={() => setFullStructure(!fullStructure)}>{fullStructure ? "返回阶段与执行概览" : "展开角色、汇聚与事件结构"}</button>}
          {view !== "timeline" && <><label>Search <input aria-label="搜索图节点" placeholder="Module, task, agent or ID" value={search} onChange={e => setSearch(e.target.value)} /></label>
          <label>Status <select aria-label="图节点状态" value={status} onChange={e => setStatus(e.target.value)}><option value="">All</option>{[...new Set(graph.nodes.map(n => n.status))].sort().map(s => <option key={s} value={s}>{statusLabels[s] || s}</option>)}</select></label>
          <label>Relations <select aria-label="图关系范围" value={direction} onChange={e => setDirection(e.target.value as typeof direction)}><option value="all">All</option><option value="upstream">Upstream</option><option value="downstream">Downstream</option></select></label></>}
          <label>History <input aria-label="回放事件序号" type="number" min="1" max={graph.scope.head_seq} value={cursor} onChange={e => setCursor(e.target.value)} /></label>
          <button type="button" onClick={() => setAsOf(Math.min(graph.scope.head_seq, Math.max(0, Number(cursor) || 0)))}>回放</button>
          <button type="button" onClick={() => { setAsOf(0); setCursor(""); }}>回到实时</button>
        </div>}
        <div className={`sdlc-body${selectedNode ? " has-inspector" : ""}`}>
          {partial && (view !== "workflow" || Boolean(search.trim())) ? <p role="status">正在读取完整运行关系…</p> : view === "timeline" && "timeline" in graph ? <WorkflowExecutionTimeline graph={graph} onSelect={pick} /> : <div className="sdlc-canvas" data-testid="workflow-graph-canvas">
            <ReactFlow key={canvasScope} nodes={canvasNodes} edges={canvasEdges} nodeTypes={workflowNodeTypes} onNodesChange={onNodesChange} onNodeClick={(_, node) => pick(node.id)} fitView fitViewOptions={graphFitOptions} minZoom={0} onlyRenderVisibleElements nodesConnectable={false} deleteKeyCode={null} aria-label="Workflow 关系图">
              <FitResizedCanvas focusId={filtered.nodes.length > 12 ? filtered.nodes.find(n => ["goal", "requirement"].includes(n.kind))?.id || filtered.nodes[0]?.id || "" : ""} /><Controls showInteractive={false} fitViewOptions={{ ...graphFitOptions, minZoom: 0 }} /><MiniMap pannable zoomable maskColor="var(--surface)" nodeColor="var(--text-tertiary)" style={{ width: 130, height: 85 }} />
            </ReactFlow>
            {!filtered.nodes.length && <p className="sdlc-empty">{graph.nodes.length ? "No nodes match these filters." : "The graph will appear as this workflow is prepared."}</p>}
          </div>}
          {selectedNode && partial && !inspectedNode && <aside className="sdlc-inspector" aria-label="图节点详情">
            <button className="sdlc-close" type="button" aria-label="Close node details" onClick={() => { setSelected(""); const url = new URL(location.href); url.searchParams.delete("node"); history.replaceState(null, "", url); }}><X size={16} /></button>
            <p role={structureNode.error ? "alert" : "status"}>{structureNode.error || "正在读取节点详情…"}</p>
            {structureNode.error && <><button type="button" onClick={structureNode.retry}>重试节点详情</button>
              <button type="button" onClick={loadFull}>读取完整运行图</button></>}
          </aside>}
          {inspectedNode && <aside className="sdlc-inspector" aria-label="图节点详情">
            <button className="sdlc-close" type="button" aria-label="Close node details" onClick={() => { setSelected(""); const url = new URL(location.href); url.searchParams.delete("node"); history.replaceState(null, "", url); }}><X size={16} /></button>
            <h3>{inspectedNode.label}</h3><p>{statusLabels[inspectedNode.status] || inspectedNode.status}</p><code>{inspectedNode.id}</code>
            {["failed", "blocked", "rejected", "evidence_missing"].includes(inspectedNode.status) && <div className="sdlc-failure-summary">
              <strong>需要关注</strong><p>{String(inspectedNode.data.reason || asRecord(inspectedNode.data.failure).reason || "具体原因尚未观测；请查看关联记录与证据。")}</p>
            </div>}
            {graph.scope.run_id && !asOf && <p><a href={`/?${new URLSearchParams({ project: projectId, page: "behavior-loop", request: requestId, expected_run: graph.scope.run_id, workflow_node: selected, ...(inspectedNode.data.task_id ? { task: String(inspectedNode.data.task_id) } : {}) })}`}>在 Loop Timeline 中查看</a></p>}
            {inspectedNode.id.startsWith("workflow:stage:") && <WorkflowStageDetails node={inspectedNode} graph={graph} partial={partial} onSelect={pick} onLoadFull={loadFull} />}
            {inspectedNode.kind === "operation" && <div><p>Agent：{String(inspectedNode.data.role_instance || "未绑定")} · Attempt：{String(inspectedNode.data.active_attempt_id || "未观测")}</p>
              <p>Provider：{String(asRecord(inspectedNode.data.agent_binding).backend || "未观测")} · 模型：{String(asRecord(inspectedNode.data.agent_binding).model || "未观测")} · {asRecord(inspectedNode.data.agent_binding).source === "provider_usage" ? "实际用量记录" : "配置快照"}</p>
              <h4>最近活动</h4>{asRecordArray(inspectedNode.data.recent_activity).slice(-5).map(e => {
                const payload = asRecord(e.payload); return <p key={String(e.id)}>{String(e.ts || "")} · {String(payload.tool_name || e.type || "")}<small>{String(payload.tool_input ? JSON.stringify(payload.tool_input).slice(0, 240) : payload.reason || "")}</small></p>;
              })}</div>}
            <button type="button" onClick={() => setCollapsed(current => { const next = new Set(current); if (next.has(selected)) next.delete(selected); else next.add(selected); return next; })}>{collapsed.has(selected) ? "展开节点" : "收起节点"}</button>
            {Boolean(inspectedNode.data.plan_id) && <button type="button" onClick={() => setPlanId(String(inspectedNode.data.plan_id))}>打开 PRD / Plan Preview</button>}
            <details><summary>关联</summary>{graph.edges.filter(e => e.source === selected || e.target === selected).map(e => {
              const target = e.source === selected ? e.target : e.source; return <button type="button" key={e.id} onClick={() => pick(target)}>{e.source === selected ? "→" : "←"} {e.relation} · {graph.nodes.find(n => n.id === target)?.label || target}</button>;
            })}</details><details><summary>来源与执行证据</summary><pre>{JSON.stringify(inspectedNode.data, null, 2)}</pre></details>
          </aside>}
        </div>
        <footer className="sdlc-footer" title={`R${graph.scope.revision} · Event ${graph.scope.source_seq}`}><span>{view === "workflow" && !fullStructure && graph.nodes.some(node => node.id.startsWith("workflow:stage:")) ? `${filtered.nodes.filter(node => node.id.startsWith("workflow:stage:") || node.id.startsWith("workflow:terminal:")).length} 个阶段/终态 · ${filtered.edges.length} 条已显示关系` : <>{partial ? "已加载的流程结构" : view === "timeline" && "timeline" in graph ? `${graph.analysis?.intervals?.length ?? 0} operations` : `${filtered.nodes.length} / ${graph.nodes.length} nodes · ${filtered.edges.length} relations`} · R{graph.scope.revision} · Event {graph.scope.source_seq}</>}</span>
          <span>{asOf ? "Historical snapshot" : "Live updates · checks every 30s"}</span></footer>
        {showTools && view !== "timeline" && <details className="sdlc-directory"><summary>Node directory</summary><div aria-label="图节点目录">{filtered.nodes.map(n => <button key={n.id} type="button" aria-pressed={selected === n.id} onClick={() => pick(n.id)} title={n.id}>{n.label}<small>{statusLabels[n.status] || n.status}</small></button>)}</div></details>}
        {plans.length > 0 && <details className="sdlc-plans"><summary>PRD / Plan documents · {plans.length}</summary>{plans.map(p => <button type="button" key={p.plan_id} onClick={() => setPlanId(p.plan_id)}>PRD / Plan Preview · {p.status}</button>)}</details>}
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

function FitResizedCanvas({ focusId }: { focusId: string }) {
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
    if (changed) void fitView({ ...graphFitOptions, ...(focusId ? { nodes: [{ id: focusId }] } : {}), minZoom: width <= 600 ? 0.65 : 0.6, duration: 150 });
  }, [width, height, initialized, fitView, focusId]);
  return null;
}
