import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Controls, MiniMap, MarkerType, ReactFlow, Panel, useNodesInitialized, useReactFlow, useStore, type Node, type Edge, type NodeChange, applyNodeChanges } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./workflowGraph.css";
import type { RecentEvent } from "../../api/types";
import { useWorkflowGraph } from "./useWorkflowGraph";
import { useWorkflowExecutionSummary } from "./useWorkflowExecutionSummary";
import { useWorkflowStructureNode } from "./useWorkflowStructureNode";
import { asRecord } from "../../app/shared";
import { workflowNodeTypes } from "./WorkflowGraphNode";
import WorkflowExecutionTimeline from "./WorkflowExecutionTimeline";
import WorkflowStageDetails from "./WorkflowStageDetails";
import WorkflowOperationDetails from "./WorkflowOperationDetails";
import WorkflowTaskDetails from "./WorkflowTaskDetails";
import { operationStage, unmappedRoleOperationIds } from "./executionGraphModel";
import { executionContext, unassignedExecutionGroup } from "./executionContextModel";
import { X, Code, Maximize2, Minimize2, SlidersHorizontal } from "lucide-react";
import { activeStatus, expandedNodeIds, GraphLayoutCache, graphViews, isWaitingAuxiliaryStage, relatedNodes, statusLabels, workflowNodeLabel, workflowSummaryEdges, workflowSummaryIds, workflowSummaryPositions, type GraphView } from "./sdlcGraphModel";

const graphFitOptions = { padding: 0.22, maxZoom: 0.9, minZoom: 0.8 };

export default function WorkflowGraphWorkbench({ projectId, requestId, revision, goalObjective = "", onOpenRaw, recentEvents, liveState }: {
  projectId: string; requestId: string; revision: string; goalObjective?: string;
  onOpenRaw?: () => void;
  recentEvents: RecentEvent[];
  liveState: string;
}) {
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
  const [executionMode, setExecutionMode] = useState<"aggregate" | "records">("aggregate");
  const [taskMode, setTaskMode] = useState<"dependencies" | "coverage">("dependencies");
  const [taskFocus, setTaskFocus] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [collapsed, setCollapsed] = useState(new Set<string>());
  const [asOf, setAsOf] = useState(0);
  const [cursor, setCursor] = useState("");
  const [showTools, setShowTools] = useState(false);
  const positions = useRef(new Map<string, { x: number; y: number }>());
  const positionScope = useRef("");
  const layoutCache = useRef(new GraphLayoutCache());
  const [canvasScope, setCanvasScope] = useState("");
  const [canvasNodes, setCanvasNodes] = useState<Node[]>([]);
  const { graph: baseGraph, error: readError, refresh, requireFull } = useWorkflowGraph(projectId, requestId, revision, asOf, recentEvents, liveState);
  const baseStructure = baseGraph && "partial" in baseGraph && baseGraph.partial ? baseGraph : null;
  const execution = useWorkflowExecutionSummary(projectId, requestId, asOf, baseStructure,
    Boolean(baseStructure?.scope.execution_started && view === "workflow"));
  const graph = execution.summary || baseGraph;
  const partial = Boolean(graph && "partial" in graph && graph.partial);
  const executionSummary = Boolean(graph && "partial" in graph && graph.execution_summary);
  const structureOnly = partial && !executionSummary;
  const structureNodeExists = Boolean(graph?.nodes.some(node => node.id === selected));
  const selectedStructure = structureOnly && structureNodeExists ? baseStructure : null;
  const structureNode = useWorkflowStructureNode(projectId, requestId, asOf, selectedStructure, selected, refresh);
  const loadFull = requireFull;
  const error = readError || execution.error;
  useEffect(() => {
    positions.current.clear(); setAsOf(0); setCollapsed(new Set()); setSearch(""); setStatus(""); setTaskFocus(false); setTaskMode("dependencies"); setExecutionMode("aggregate");
    const query = new URLSearchParams(location.search);
    if (["lifecycle", "trace", "delivery"].includes(query.get("graph") || "")) {
      const url = new URL(location.href); url.searchParams.set("graph", "workflow"); history.replaceState(null, "", url);
    }
    setSelected(query.get("request") === requestId ? query.get("node") || "" : "");
  }, [projectId, requestId]);
  useEffect(() => {
    const waitingForExecution = Boolean(baseStructure?.scope.execution_started && view === "workflow" && !execution.unsupported && !execution.summary);
    if (partial && !readError && (execution.unsupported || view !== "workflow" || fullStructure || (selected && !structureNodeExists && !waitingForExecution) || asOf)) loadFull();
  }, [graph, partial, view, selected, structureNodeExists, asOf, loadFull, readError, execution.unsupported, execution.summary, fullStructure, baseStructure]);
  const pick = useCallback((id: string) => {
    setSelected(id); const url = new URL(location.href);
    url.searchParams.set("request", requestId); url.searchParams.set("graph", view); url.searchParams.set("node", id);
    history.replaceState(null, "", url);
  }, [requestId, view]);
  const filtered = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    const dependencyEdges = graph.edges.filter(edge => edge.relation === "depends_on" && edge.views.includes("modules"));
    const focused = view === "modules" && taskFocus && selected && graph.nodes.some(node => node.id === selected && node.kind === "task")
      ? new Set([...relatedNodes(selected, dependencyEdges, "upstream"), ...relatedNodes(selected, dependencyEdges, "downstream")]) : null;
    const reachable = direction !== "all" && selected ? relatedNodes(selected, graph.edges, direction) : null;
    const expanded = expandedNodeIds(graph.nodes, graph.edges, collapsed);
    const summary = view === "workflow" && !fullStructure ? workflowSummaryIds(graph.nodes) : null;
    if (summary && !structureOnly) {
      const stages = new Set(graph.nodes.filter(n => n.id.startsWith("workflow:stage:")).map(n => String(n.data.stage_id || n.id.slice("workflow:stage:".length))));
      const operations = new Map(graph.nodes.filter(n => n.kind === "operation" && n.data.role_instance && !stages.has(operationStage(n))).map(n => [n.id, n]));
      for (const edge of graph.edges) if (edge.relation === "executes" && edge.source.startsWith("workflow:role:") && operations.has(edge.target)) summary.add(edge.source);
    }
    const needle = search.trim().toLowerCase();
    if (summary && !needle && !status && direction === "all") {
      for (const node of graph.nodes) if (node.id !== selected && isWaitingAuxiliaryStage(node) && !graph.nodes.some(item => item.kind === "operation" && item.data.role_instance && operationStage(item) === node.data.stage_id && item.data.workflow_run_id === graph.scope.run_id)) summary.delete(node.id);
    }
    const candidates = graph.nodes.filter(n => (n.views.includes(view) || n.id === selected) && (view !== "modules" || (taskMode === "dependencies" ? n.kind === "task" : ["requirement", "goal", "claim", "task"].includes(n.kind))) && expanded.has(n.id) && (!reachable || reachable.has(n.id)) && (!focused || focused.has(n.id))
      && (!summary || needle || summary.has(n.id) || (n.id === selected && n.kind !== "operation"))
      && (!status || n.status === status) && (!needle || `${n.id} ${n.label} ${JSON.stringify(n.data)}`.toLowerCase().includes(needle)));
    const priority = (n: typeof candidates[number]) => n.id === selected ? 0 :
      ["requirement", "goal", "run", "preparation"].includes(n.kind) ? 1 :
      ["failed", "blocked", "evidence_missing", "running", "in_progress"].includes(n.status) ? 2 :
      ["task", "module", "claim", "workflow"].includes(n.kind) ? 3 : 4;
    const stageSummary = summary && graph.nodes.some(node => node.id.startsWith("workflow:stage:"));
    const nodes = view === "modules" || stageSummary || showAllNodes || needle || reachable || candidates.length <= 40 ? candidates :
      [...candidates].sort((a, b) => priority(a) - priority(b) || a.label.localeCompare(b.label)).slice(0, 40);
    const ids = new Set(nodes.map(n => n.id));
    const edges = stageSummary && !needle && !reachable && !status ? workflowSummaryEdges(graph.nodes, graph.edges, ids) : graph.edges.filter(e => e.views.includes(view) && ids.has(e.source) && ids.has(e.target) && (view !== "modules" || (taskMode === "dependencies" ? e.relation === "depends_on" : ["declares", "implemented_by"].includes(e.relation))));
    if (view === "workflow" && !fullStructure && !needle && !status && direction === "all" &&
        graph.nodes.some(node => ["workflow", "run", "preparation"].includes(node.kind))) {
      const phaseNodes = nodes.filter(node => node.kind === "workflow" && (node.id.startsWith("workflow:stage:") || node.id.startsWith("workflow:terminal:") ||
        (node.id.startsWith("workflow:role:") && unmappedRoleOperationIds(node.id, graph.nodes, graph.edges).length > 0)));
      const unassigned = unassignedExecutionGroup(graph);
      if (unassigned) phaseNodes.push(unassigned);
      const phaseIds = new Set(phaseNodes.map(node => node.id));
      const phaseEdges = edges.filter(edge => phaseIds.has(edge.source) && phaseIds.has(edge.target));
      const context = executionContext(graph, phaseNodes, phaseEdges, goalObjective);
      return { nodes: [context.goal, context.run, ...phaseNodes, context.result], edges: [...context.links, ...phaseEdges], hidden: candidates.length - nodes.length };
    }
    return { nodes, edges, hidden: candidates.length - nodes.length };
  }, [graph, view, direction, selected, collapsed, search, status, fullStructure, showAllNodes, structureOnly, taskMode, taskFocus, goalObjective]);
  const layoutScope = JSON.stringify([projectId, requestId, view, direction,
    direction === "all" ? "" : selected, [...collapsed].sort(), search.trim().toLowerCase(), status, fullStructure, showAllNodes, asOf, taskMode, taskFocus]);
  useEffect(() => {
    // Explicit navigation gets a compact layout; live polls preserve dragged nodes and the viewport.
    if (positionScope.current !== layoutScope) {
      positions.current.clear(); positionScope.current = layoutScope;
    }
    const overview = view === "workflow" && !fullStructure;
    const phaseNodes = overview ? filtered.nodes.filter(node => !node.data.context_kind) : filtered.nodes;
    const phaseEdges = overview ? filtered.edges.filter(edge => !edge.presentation) : filtered.edges;
    const layout = overview ? workflowSummaryPositions(phaseNodes, phaseEdges) : layoutCache.current.get(filtered.nodes, filtered.edges);
    const rightmost = Math.max(0, ...phaseNodes.filter(item => !item.id.startsWith("workflow:role:")).map(item => (layout.get(item.id)?.x || 0) * 1.18));
    for (const n of filtered.nodes) {
      const key = `${view}:${n.id}`;
      if (!positions.current.has(key)) {
        const base = layout.get(n.id) || { x: 0, y: 0 };
        const roleIndex = filtered.nodes.filter(item => item.id.startsWith("workflow:role:") || item.data.unassigned_execution).findIndex(item => item.id === n.id);
        const pos = !overview ? { ...base } : n.data.context_kind === "goal" ? { x: -850, y: 0 } :
          n.data.context_kind === "run" ? { x: -460, y: 0 } : n.data.context_kind === "result" ? { x: rightmost + 370, y: 0 } :
          roleIndex >= 0 ? { x: 0, y: -260 - roleIndex * 340 } : { x: base.x * 1.18, y: base.y * 1.7 };
        while ([...positions.current].some(([k, p]) => k.startsWith(`${view}:`) && p.x === pos.x && p.y === pos.y)) pos.x += 310;
        positions.current.set(key, pos);
      }
    }
    setCanvasNodes(previous => {
      const measured = new Map(previous.map(node => [node.id, node.measured]));
      // Live projections replace data, but unchanged DOM sizes may not emit a
      // new ResizeObserver entry. Retain measured sizes so nodes stay visible.
      return filtered.nodes.map(n => ({ id: n.id,
      measured: measured.get(n.id),
      position: positions.current.get(`${view}:${n.id}`) || layout.get(n.id) || { x: 0, y: 0 },
      selected: n.id === selected,
      type: overview && n.data.context_kind ? "executionContext" : overview && (n.id.startsWith("workflow:stage:") || n.id.startsWith("workflow:role:") || n.data.unassigned_execution) ? "executionStage" : "workflowCard",
      className: `sdlc-node sdlc-status-${n.status}${n.id === "workflow:terminal:done" ? " sdlc-terminal-node" : ""}`,
      data: { node: n, horizontal: view === "workflow" && !fullStructure, allNodes: graph?.nodes || [], operationIds: n.data.unassigned_execution ? n.data.operation_ids : graph && view === "workflow" && !fullStructure && n.id.startsWith("workflow:role:") ? unmappedRoleOperationIds(n.id, graph.nodes, graph.edges) : [], partial: structureOnly, mode: executionMode, onSelect: pick },
      }));
    });
    setCanvasScope(layoutScope);
  }, [filtered, selected, view, layoutScope, fullStructure, graph, structureOnly, pick, executionMode]);
  const focusIds = selected && filtered.nodes.some(n => n.id === selected) ? [selected] :
    view === "workflow" && !fullStructure ? [...filtered.nodes.filter(n => ["goal", "run"].includes(String(n.data.context_kind))), ...filtered.nodes.filter(n => n.id.startsWith("workflow:role:") || n.data.unassigned_execution).slice(0, 1), ...filtered.nodes.filter(n => n.id.startsWith("workflow:stage:")).slice(0, 1)].map(n => n.id) :
    filtered.nodes.length > 12 ? [filtered.nodes.find(n => ["goal", "requirement"].includes(n.kind))?.id || filtered.nodes[0]?.id].filter(Boolean) : [];
  const activeNodes = useMemo(() => new Set(filtered.nodes.filter(n => activeStatus(n.status)).map(n => n.id)), [filtered.nodes]);
  const canvasEdges: Edge[] = filtered.edges.map(e => ({ ...e, type: "default",
    label: e.presentation === "goal" ? "需求关联" : e.presentation === "scope" ? e.relation : e.presentation === "outcome" ? e.relation : e.collapsedVia ? "折叠路径" : fullStructure || e.source === selected || e.target === selected ? e.event || e.relation : undefined,
    labelStyle: { fill: "var(--text-secondary)", fontSize: "var(--text-10)" }, labelBgStyle: { fill: "var(--canvas)" },
    markerEnd: e.presentation === "scope" ? undefined : { type: MarkerType.ArrowClosed, color: "var(--text-tertiary)", width: 14, height: 14 },
    animated: !asOf && (view !== "workflow" || fullStructure) && !e.presentation && (activeNodes.has(e.source) || activeNodes.has(e.target)),
    style: { stroke: /rework|retry/.test(e.relation) ? "var(--warning)" : "var(--text-tertiary)", strokeWidth: 1.5, strokeDasharray: e.presentation === "scope" ? "2 7" : "5 6" },
  }));
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    for (const change of changes) if (change.type === "position" && change.position) positions.current.set(`${view}:${change.id}`, change.position);
    setCanvasNodes(nodes => applyNodeChanges(changes, nodes));
  }, [view]);
  const selectedNode = graph?.nodes.find(n => n.id === selected) || filtered.nodes.find(n => n.id === selected);
  const inspectedNode = structureOnly ? structureNode.node : selectedNode;
  const taskOperations = inspectedNode?.kind === "task" && inspectedNode.data.task_id ? (graph?.nodes.filter(n => n.kind === "operation" && n.data.task_id === inspectedNode.data.task_id && n.data.role_instance) || []).sort((a, b) => {
    const time = (node: typeof a) => { const value = Date.parse(String(node.data.last_event_at || "")); return Number.isNaN(value) ? -Infinity : value; };
    return (time(b) - time(a)) || a.id.localeCompare(b.id);
  }) : [];
  const singleTask = view === "modules" && taskMode === "dependencies" && !partial && !search && !status && direction === "all" && graph?.nodes.filter(node => node.kind === "task" && node.views.includes("modules")).length === 1
    ? graph.nodes.find(node => node.kind === "task" && node.views.includes("modules")) : undefined;
  const singleClaims = singleTask ? graph!.edges.filter(edge => edge.relation === "implemented_by" && edge.target === singleTask.id).map(edge => graph!.nodes.find(node => node.id === edge.source && node.kind === "claim")).filter((node): node is NonNullable<typeof node> => Boolean(node)) : [];
  function chooseView(id: GraphView) {
    if (id !== "workflow" && partial) loadFull();
    setView(id); const url = new URL(location.href); url.searchParams.set("graph", id); history.replaceState(null, "", url);
  }
  async function retryExecution() {
    if (execution.error.includes("HTTP 409")) await refresh();
    execution.retry();
  }
  function navigateNode(id: string, targetView: GraphView) {
    setView(targetView); setSelected(id);
    const url = new URL(location.href);
    url.searchParams.set("request", requestId); url.searchParams.set("graph", targetView); url.searchParams.set("node", id);
    history.replaceState(null, "", url);
  }
  return <section className={`sdlc-workbench${expanded ? " sdlc-expanded" : ""}`} aria-label="Workflow 图工作台">
    <div className="sdlc-main">
      <header className="sdlc-heading">
        <nav className="sdlc-views" aria-label="Workflow graph views">{graphViews.map(([id, label]) =>
          <button key={id} type="button" aria-pressed={view === id} onClick={() => chooseView(id)}>{label}</button>
        )}</nav>
        <div className="sdlc-heading-actions">
          <button type="button" aria-label="Graph filters" aria-expanded={showTools} onClick={() => { if (!showTools && partial) loadFull(); setShowTools(!showTools); }}><SlidersHorizontal size={15} /> Filters</button>
          {view === "workflow" && onOpenRaw && <button type="button" disabled={Boolean(asOf)} title={asOf ? "Return to live to inspect this proposal's YAML" : "View version-bound YAML"} onClick={() => { setExpanded(false); onOpenRaw(); }}><Code size={15} /> Raw</button>}
          <button type="button" onClick={() => setExpanded(!expanded)} aria-label={expanded ? "收回工作台" : "展开工作台"}>{expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
        </div>
      </header>
      {error && <p role="alert">{error} <button type="button" onClick={() => execution.error ? void retryExecution() : void refresh()}>Retry</button>{partial && <button type="button" onClick={loadFull}>读取完整运行图</button>}</p>}
      {!graph && !error && <p>正在读取流程与运行关系…</p>}
      {graph && <>
        {view === "workflow" && !fullStructure && <div className="sdlc-graph-mode" role="group" aria-label="执行记录显示方式"><span>执行记录</span><button type="button" aria-pressed={executionMode === "aggregate"} onClick={() => setExecutionMode("aggregate")}>聚合</button><button type="button" aria-pressed={executionMode === "records"} onClick={() => setExecutionMode("records")}>展开记录</button><span>{executionMode === "records" ? "每张卡片是一条执行记录" : "相同角色与 Task 的记录聚合"}</span></div>}
        {view === "modules" && <><div className="sdlc-graph-mode" role="group" aria-label="任务图显示方式"><button type="button" aria-pressed={taskMode === "dependencies"} onClick={() => { setTaskMode("dependencies"); setTaskFocus(false); }}>任务依赖</button><button type="button" aria-pressed={taskMode === "coverage"} onClick={() => { setTaskMode("coverage"); setTaskFocus(false); }}>Claim 覆盖</button>{taskFocus && <button type="button" onClick={() => setTaskFocus(false)}>显示全部任务</button>}</div><div className="sdlc-summary-note">{taskMode === "dependencies" ? "任务 Graph：仅显示已声明的 Task 依赖。" : "Claim 覆盖：仅显示已声明的目标、Claim 和 Task 关系。"}{taskFocus ? "当前聚焦选中任务的上下游。" : ""}</div></>}
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
          {partial && (view !== "workflow" || Boolean(search.trim())) ? <p role="status">正在读取完整运行关系…</p> : singleTask ? <section className="sdlc-single-task" aria-label="单任务概览"><h3>当前仅有一个任务</h3><p>没有其他任务可展示依赖关系。</p><button type="button" onClick={() => pick(singleTask.id)}>{singleTask.label} · {statusLabels[singleTask.status] || singleTask.status}</button><details><summary>关联 Claim · {singleClaims.length}</summary>{singleClaims.length ? singleClaims.map(claim => <p key={claim.id}>{claim.label} · {statusLabels[claim.status] || claim.status}</p>) : <p>尚无已声明的 Claim 关联。</p>}<p>任务映射不代表验收通过。</p></details></section> : view === "timeline" && "timeline" in graph ? <WorkflowExecutionTimeline graph={graph} onSelect={pick} /> : <div className="sdlc-canvas" data-testid="workflow-graph-canvas">
            <ReactFlow key={canvasScope} nodes={canvasNodes} edges={canvasEdges} nodeTypes={workflowNodeTypes} onNodesChange={onNodesChange} onNodeClick={(_, node) => { const item = node.data.node as typeof filtered.nodes[number] | undefined; if (!item?.data.unassigned_execution) pick(String(item?.data.context_target || node.id)); }} fitView fitViewOptions={{ ...graphFitOptions, ...(focusIds.length ? { nodes: focusIds.map(id => ({ id })) } : {}) }} minZoom={0} onlyRenderVisibleElements={filtered.nodes.length > 40} nodesConnectable={false} deleteKeyCode={null} aria-label="Workflow 关系图">
              <FitResizedCanvas focusIds={focusIds} focusKey={structureOnly ? "structure" : executionSummary ? "execution" : "full"} />{view === "workflow" && !fullStructure && <FitAllButton resultId={`ui:run-result:${requestId}`} />}<Controls showInteractive={false} fitViewOptions={{ ...graphFitOptions, minZoom: 0 }} /><MiniMap pannable zoomable maskColor="var(--surface)" nodeColor="var(--text-tertiary)" style={{ width: 130, height: 85 }} />
            </ReactFlow>
            {!filtered.nodes.length && <p className="sdlc-empty">{graph.nodes.length ? "No nodes match these filters." : "The graph will appear as this workflow is prepared."}</p>}
          </div>}
          {selectedNode && structureOnly && !inspectedNode && <aside className="sdlc-inspector" aria-label="图节点详情">
            <button className="sdlc-close" type="button" aria-label="Close node details" onClick={() => { setSelected(""); const url = new URL(location.href); url.searchParams.delete("node"); history.replaceState(null, "", url); }}><X size={16} /></button>
            <p role={structureNode.error ? "alert" : "status"}>{structureNode.error || "正在读取节点详情…"}</p>
            {structureNode.error && <><button type="button" onClick={structureNode.retry}>重试节点详情</button>
              <button type="button" onClick={loadFull}>读取完整运行图</button></>}
          </aside>}
          {inspectedNode && <aside className="sdlc-inspector" aria-label="图节点详情">
            <button className="sdlc-close" type="button" aria-label="Close node details" onClick={() => { setSelected(""); const url = new URL(location.href); url.searchParams.delete("node"); history.replaceState(null, "", url); }}><X size={16} /></button>
            <h3>{workflowNodeLabel(inspectedNode)}</h3><p>{statusLabels[inspectedNode.status] || inspectedNode.status}</p><code>{inspectedNode.id}</code>
            {["failed", "blocked", "rejected", "evidence_missing"].includes(inspectedNode.status) && <div className="sdlc-failure-summary">
              <strong>需要关注</strong><p>{String(inspectedNode.data.reason || asRecord(inspectedNode.data.failure).reason || "具体原因尚未观测；请查看关联记录与证据。")}</p>
            </div>}
            {graph.scope.run_id && !asOf && <p><a href={`/?${new URLSearchParams({ project: projectId, page: "behavior-loop", request: requestId, expected_run: graph.scope.run_id, workflow_node: selected, ...(inspectedNode.data.task_id ? { task: String(inspectedNode.data.task_id) } : {}) })}`}>在 Loop Timeline 中查看</a></p>}
            {(inspectedNode.id.startsWith("workflow:stage:") || inspectedNode.id.startsWith("workflow:role:")) && <WorkflowStageDetails node={inspectedNode} graph={graph} partial={structureOnly} onSelect={pick} onLoadFull={loadFull} />}
            {inspectedNode.kind === "operation" && <WorkflowOperationDetails node={inspectedNode} projectId={projectId} nodes={graph.nodes} edges={graph.edges} taskNode={inspectedNode.data.task_id ? graph.nodes.find(n => n.kind === "task" && n.data.task_id === inspectedNode.data.task_id) : undefined} onTask={id => navigateNode(id, "modules")} onOperation={pick} />}
            {inspectedNode.kind === "task" && <WorkflowTaskDetails task={inspectedNode} nodes={graph.nodes} edges={graph.edges} operations={taskOperations} onTask={id => navigateNode(id, "modules")} onOperation={id => navigateNode(id, "workflow")} onFocus={() => { setTaskMode("dependencies"); setTaskFocus(true); if (view !== "modules") navigateNode(inspectedNode.id, "modules"); }} />}
            <button type="button" onClick={() => setCollapsed(current => { const next = new Set(current); if (next.has(selected)) next.delete(selected); else next.add(selected); return next; })}>{collapsed.has(selected) ? "展开节点" : "收起节点"}</button>
            <details><summary>关联</summary>{graph.edges.filter(e => e.source === selected || e.target === selected).map(e => {
              const target = e.source === selected ? e.target : e.source; return <button type="button" key={e.id} onClick={() => pick(target)}>{e.source === selected ? "→" : "←"} {e.relation} · {graph.nodes.find(n => n.id === target)?.label || target}</button>;
            })}</details><details><summary>来源与执行证据</summary><pre>{JSON.stringify(inspectedNode.data, null, 2)}</pre></details>
          </aside>}
        </div>
        <footer className="sdlc-footer" title={`R${graph.scope.revision} · Event ${graph.scope.source_seq}`}><span>{view === "workflow" && !fullStructure && graph.nodes.some(node => node.id.startsWith("workflow:stage:")) ? `${filtered.nodes.filter(node => node.id.startsWith("workflow:stage:") || node.id.startsWith("workflow:terminal:")).length} 个阶段/终态${filtered.nodes.some(node => node.id.startsWith("workflow:role:")) ? ` · ${filtered.nodes.filter(node => node.id.startsWith("workflow:role:")).length} 个独立角色` : ""}${filtered.nodes.some(node => node.data.unassigned_execution) ? " · 未归属阶段" : ""} · ${filtered.edges.filter(edge => !edge.presentation).length} 条已显示关系` : <>{partial ? "已加载的流程结构" : view === "timeline" && "timeline" in graph ? `${graph.analysis?.intervals?.length ?? 0} operations` : `${filtered.nodes.length} / ${graph.nodes.length} nodes · ${filtered.edges.length} relations`} · R{graph.scope.revision} · Event {graph.scope.source_seq}</>}</span>
          <span>{asOf ? "Historical snapshot" : "Live updates · checks every 30s"}</span></footer>
        {showTools && view !== "timeline" && <details className="sdlc-directory"><summary>Node directory</summary><div aria-label="图节点目录">{filtered.nodes.map(n => <button key={n.id} type="button" aria-pressed={selected === n.id} onClick={() => pick(String(n.data.context_target || n.id))} title={n.id}>{n.label}<small>{statusLabels[n.status] || n.status}</small></button>)}</div></details>}
      </>}
    </div>
  </section>;
}

function FitResizedCanvas({ focusIds, focusKey }: { focusIds: string[]; focusKey: string }) {
  const width = useStore(state => state.width);
  const height = useStore(state => state.height);
  const initialized = useNodesInitialized();
  const nodes = useStore(state => state.nodes);
  const { fitView } = useReactFlow();
  const previous = useRef("");
  const seenModes = useRef(new Set<string>());
  useEffect(() => {
    if (!width || !height || !initialized || focusIds.some(id => !nodes.some(node => node.id === id))) return;
    const size = `${width}:${height}`;
    const firstDetail = focusKey !== "structure" && !seenModes.current.has(focusKey);
    if (focusKey !== "structure") seenModes.current.add(focusKey);
    const changed = previous.current !== size || firstDetail;
    previous.current = size;
    // Fit the first detailed graph, but do not reset the viewport on live polls.
    if (changed) void fitView({ ...graphFitOptions, ...(focusIds.length ? { nodes: focusIds.map(id => ({ id })) } : {}), minZoom: 0.8, duration: 150 });
  }, [width, height, initialized, fitView, focusIds, focusKey, nodes]);
  return null;
}

function FitAllButton({ resultId }: { resultId: string }) {
  const { fitView } = useReactFlow();
  return <Panel position="top-right"><button className="sdlc-fit-all" type="button" onClick={() => void fitView({ ...graphFitOptions, nodes: [{ id: resultId }], minZoom: 0, duration: 150 })}>查看运行结果</button> <button className="sdlc-fit-all" type="button" onClick={() => void fitView({ ...graphFitOptions, minZoom: 0, duration: 150 })}>查看全图</button></Panel>;
}
