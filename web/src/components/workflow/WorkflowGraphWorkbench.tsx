import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Background, Controls, MiniMap, Position, ReactFlow, type Node, type Edge, type NodeChange, applyNodeChanges } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./workflowGraph.css";
import type { ActionResponse } from "../../api/types";
import { asRecord, asRecordArray } from "../../app/shared";
import PlanReviewReader from "../delivery-trace/PlanReviewReader";
import PlanArtifactChat from "./PlanArtifactChat";
import { activeStatus, expandedNodeIds, graphPositions, graphViews, relatedNodes, statusLabels, workflowSummaryIds, type GraphView, type SdlcGraph } from "./sdlcGraphModel";

export default function WorkflowGraphWorkbench({ projectId, requestId, revision, onAction, actionReady }: {
  projectId: string; requestId: string; revision: string;
  onAction: (action: string, payload: Record<string, unknown>) => Promise<ActionResponse>;
  actionReady: boolean;
}) {
  const [graph, setGraph] = useState<SdlcGraph | null>(null);
  const [error, setError] = useState("");
  const [view, setView] = useState<GraphView>(() => {
    const value = new URLSearchParams(location.search).get("graph");
    return graphViews.some(([id]) => id === value) ? value as GraphView : "lifecycle";
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
  const [busy, setBusy] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const positions = useRef(new Map<string, { x: number; y: number }>());
  const [canvasNodes, setCanvasNodes] = useState<Node[]>([]);
  useEffect(() => {
    positions.current.clear(); setGraph(null); setAsOf(0); setPlanId(""); setCollapsed(new Set()); setSearch(""); setStatus("");
    const query = new URLSearchParams(location.search);
    setSelected(query.get("request") === requestId ? query.get("node") || "" : "");
  }, [projectId, requestId]);
  useEffect(() => {
    let disposed = false; let pending = false;
    const controller = new AbortController();
    async function refresh() {
      if (pending) return; pending = true;
      try {
        const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/workflow-requests/${encodeURIComponent(requestId)}/graph?as_of=${asOf}`, { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error(`图读取失败 HTTP ${response.status}`);
        const next = await response.json() as SdlcGraph;
        if (!disposed) { setGraph(next); setError(""); }
      } catch (reason) { if (!disposed) setError(String(reason)); }
      finally { pending = false; }
    }
    void refresh();
    const timer = window.setInterval(() => { if (!asOf && !document.hidden) void refresh(); }, 2000);
    return () => { disposed = true; controller.abort(); window.clearInterval(timer); };
  }, [projectId, requestId, revision, asOf]);
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
  useEffect(() => {
    const layout = graphPositions(filtered.nodes, filtered.edges);
    for (const n of filtered.nodes) {
      const key = `${view}:${n.id}`;
      if (!positions.current.has(key)) {
        const pos = { ...(layout.get(n.id) || { x: 0, y: 0 }) };
        while ([...positions.current].some(([k, p]) => k.startsWith(`${view}:`) && p.x === pos.x && p.y === pos.y)) pos.y += 120;
        positions.current.set(key, pos);
      }
    }
    setCanvasNodes(filtered.nodes.map(n => ({ id: n.id,
      position: positions.current.get(`${view}:${n.id}`) || layout.get(n.id) || { x: 0, y: 0 },
      selected: n.id === selected,
      sourcePosition: Position.Right, targetPosition: Position.Left,
      className: `sdlc-node sdlc-status-${n.status}`,
      data: { label: <><small>{n.kind}</small><strong>{n.label}</strong><span>{statusLabels[n.status] || n.status}</span></> },
    })));
  }, [filtered, selected, view]);
  const canvasEdges: Edge[] = filtered.edges.map(e => ({ ...e, type: "smoothstep", label: e.event || e.relation,
    labelStyle: { fill: "#cbd5e1", fontSize: 11 }, labelBgStyle: { fill: "#1e293b" },
    animated: filtered.nodes.some(n => (n.id === e.target || n.id === e.source) && activeStatus(n.status)),
    style: { stroke: /rework|causes/.test(e.relation) ? "#d97706" : "#64748b" },
  }));
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    for (const change of changes) if (change.type === "position" && change.position) positions.current.set(`${view}:${change.id}`, change.position);
    setCanvasNodes(nodes => applyNodeChanges(changes, nodes));
  }, [view]);
  const selectedNode = graph?.nodes.find(n => n.id === selected);
  const preview = graph?.plans.find(p => p.plan_id === planId);
  async function approvePlan() {
    if (!preview || busy) return;
    setBusy(true);
    try {
      const result = await onAction(preview.actions?.approve || "plan-approve", { ...preview.action_payload, plan_id: preview.plan_id });
      if (!result.ok) setError(String(result.reason || result.status)); else setPlanId("");
    } finally { setBusy(false); }
  }
  return <section className={`sdlc-workbench${expanded ? " sdlc-expanded" : ""}`} aria-label="Workflow 图工作台">
    <header><h2>Workflow 图工作台</h2><span role="status">{asOf ? "历史回放" : "实时"} · {graph?.scope.run_id || "待启动"} · 事件 {graph?.scope.source_seq ?? "—"}</span><button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? "收回工作台" : "展开工作台"}</button></header>
    <nav aria-label="Workflow 图视角">{graphViews.map(([id, label]) => <button key={id} type="button" aria-pressed={view === id} onClick={() => {
      setView(id); const url = new URL(location.href); url.searchParams.set("graph", id); history.replaceState(null, "", url);
    }}>{label}</button>)}</nav>
    {error && <p role="alert">{error}</p>}
    {!graph && !error && <p>正在读取流程与运行关系…</p>}
    {graph && <>
      <div className="sdlc-toolbar">
        {view === "workflow" && <button type="button" aria-pressed={fullStructure} onClick={() => setFullStructure(!fullStructure)}>{fullStructure ? "返回阶段与执行概览" : "展开角色、汇聚与事件结构"}</button>}
        <label>搜索 <input aria-label="搜索图节点" placeholder="模块、需求、Agent、阶段或 ID" value={search} onChange={e => setSearch(e.target.value)} /></label>
        <label>状态 <select aria-label="图节点状态" value={status} onChange={e => setStatus(e.target.value)}><option value="">全部</option>{[...new Set(graph.nodes.map(n => n.status))].sort().map(s => <option key={s} value={s}>{statusLabels[s] || s}</option>)}</select></label>
        <label>关系范围 <select aria-label="图关系范围" value={direction} onChange={e => setDirection(e.target.value as typeof direction)}><option value="all">全部</option><option value="upstream">上游来源</option><option value="downstream">下游影响</option></select></label>
        <label>事件回放 <input aria-label="回放事件序号" type="number" min="1" max={graph.scope.head_seq} value={cursor} onChange={e => setCursor(e.target.value)} /></label>
        <button type="button" onClick={() => setAsOf(Math.min(graph.scope.head_seq, Math.max(0, Number(cursor) || 0)))}>回放</button>
        <button type="button" onClick={() => { setAsOf(0); setCursor(""); }}>回到实时</button>
        <span>{filtered.nodes.length} / {graph.nodes.length} 节点 · {filtered.edges.length} 关系 · R{graph.scope.revision} · 完整快照</span>
      </div>
      {view === "trace" && <p>下游高亮表示已声明关系可达；业务影响与未声明依赖需要 Agent / 人工评估。旧计划版本保留在下方版本记录中。</p>}
      {view === "delivery" && <p>发布：{graph.capabilities.release?.status === "observed" ? "已有交付记录，请核对节点证据" : "未知"} · 生产：{graph.capabilities.production?.reason}</p>}
      <div className="sdlc-body">
        <aside className="sdlc-tree" aria-label="图节点目录">{filtered.nodes.map(n => <button key={n.id} type="button" aria-pressed={selected === n.id} onClick={() => pick(n.id)} title={n.id}>{n.label}<small>{statusLabels[n.status] || n.status}</small></button>)}</aside>
        <div className="sdlc-canvas" data-testid="workflow-graph-canvas">
          <ReactFlow nodes={canvasNodes} edges={canvasEdges} onNodesChange={onNodesChange} onNodeClick={(_, node) => pick(node.id)} fitView minZoom={0.1} onlyRenderVisibleElements nodesConnectable={false} deleteKeyCode={null} aria-label="Workflow 关系图"><Background /><Controls showInteractive={false} /><MiniMap pannable zoomable maskColor="#0f172a99" nodeColor="#475569" style={{ background: "#1e293b" }} /></ReactFlow>
        </div>
        <aside className="sdlc-inspector" aria-label="图节点详情">
          {selectedNode ? <><h3>{selectedNode.label}</h3><p>{statusLabels[selectedNode.status] || selectedNode.status}</p><code>{selectedNode.id}</code>
            {selectedNode.kind === "operation" && <div><p>Agent：{String(selectedNode.data.role_instance || "未绑定")} · Attempt：{String(selectedNode.data.active_attempt_id || "未观测")}</p>
              <p>Provider：{String(asRecord(selectedNode.data.agent_binding).backend || "未观测")} · 模型：{String(asRecord(selectedNode.data.agent_binding).model || "未观测")} · {asRecord(selectedNode.data.agent_binding).source === "provider_usage" ? "实际用量记录" : "配置快照"}</p>
              <h4>最近活动</h4>{asRecordArray(selectedNode.data.recent_activity).slice(-5).map(e => {
                const payload = asRecord(e.payload); return <p key={String(e.id)}>{String(e.ts || "")} · {String(payload.tool_name || e.type || "")}<small>{String(payload.tool_input ? JSON.stringify(payload.tool_input).slice(0, 240) : payload.reason || "")}</small></p>;
              })}</div>}
            <button type="button" onClick={() => setCollapsed(current => {
              const next = new Set(current); if (next.has(selected)) next.delete(selected); else next.add(selected); return next;
            })}>{collapsed.has(selected) ? "展开节点" : "收起节点"}</button>
            {selectedNode.data.plan_id && <button type="button" onClick={() => setPlanId(String(selectedNode.data.plan_id))}>打开 PRD / Plan Preview</button>}
            <h4>关联</h4>{graph.edges.filter(e => e.source === selected || e.target === selected).map(e => {
              const target = e.source === selected ? e.target : e.source; return <button type="button" key={e.id} onClick={() => pick(target)}>{e.source === selected ? "→" : "←"} {e.relation} · {graph.nodes.find(n => n.id === target)?.label || target}</button>;
            })}<details><summary>来源与执行证据</summary><pre>{JSON.stringify(selectedNode.data, null, 2)}</pre></details></> : <p>选择节点，查看来源、依赖、执行尝试与证据。</p>}
        </aside>
      </div>
      {view === "timeline" && <div className="sdlc-timeline"><h3>执行区间与关键路径</h3><p>{graph.analysis.critical_path.status === "observed" ? `已观测 DAG 关键路径 ${(graph.analysis.critical_path.duration_ms! / 1000).toFixed(1)} 秒（未计入未观测等待）` : "时序或依赖证据尚不完整，暂不计算关键路径。"}</p>
        <table><thead><tr><th>Agent / Operation</th><th>开始</th><th>排队等待</th><th>耗时</th><th>状态</th></tr></thead><tbody>{graph.analysis.intervals.map(i => <tr key={i.operation_id} onClick={() => pick(`operation:${i.operation_id}`)}><td>{i.role || i.operation_id}</td><td>{i.start_ms === null ? "未知" : new Date(i.start_ms).toLocaleTimeString()}</td><td>{i.queue_wait_ms === null ? "未观测" : `${(i.queue_wait_ms / 1000).toFixed(1)}s`}</td><td>{i.duration_ms === null ? "未结束 / 未观测" : `${(i.duration_ms / 1000).toFixed(1)}s`}</td><td>{statusLabels[i.status] || i.status}</td></tr>)}</tbody></table></div>}
      <div className="sdlc-toolbar">{graph.plans.map(p => <button type="button" key={p.plan_id} onClick={() => setPlanId(p.plan_id)}>PRD / Plan Preview · {p.status}</button>)}</div>
      {preview && <section className="sdlc-plan" aria-label="Workflow PRD Plan Preview"><button type="button" onClick={() => setPlanId("")}>关闭预览</button><PlanReviewReader preview={preview} projectId={projectId} />
        <PlanArtifactChat preview={preview} projectId={projectId} disabled={!actionReady || !!asOf} onBusy={setReviewBusy} />
        {preview.status === "pending" && <button type="button" disabled={!actionReady || busy || reviewBusy || !!asOf} onClick={() => void approvePlan()}>确认计划并继续</button>}
      </section>}
      <details><summary>版本与数据可用性（{graph.diagnostics.length}）</summary>{graph.diagnostics.map((d, i) => <p key={i}>{d}</p>)}{graph.versions.map(v => <p key={v.event_id}><button type="button" onClick={() => { setAsOf(v.seq); setCursor(String(v.seq)); }}>查看事件 {v.seq} 的计划</button> · {v.admitted ? "已接纳" : "候选"} · {v.ref} · {v.digest || "未绑定摘要"}</p>)}</details>
      <details className="sdlc-events"><summary>Live Events · {graph.timeline.length}</summary>{graph.timeline.slice(-100).reverse().map(e => <p key={e.id}>{e.seq} · {new Date(e.ts).toLocaleTimeString()} · {e.type} · {e.actor} · {e.task_id}</p>)}{graph.timeline.length > 100 && <p>此处显示最近 100 条；使用回放游标查看更早事件。</p>}</details>
    </>}
  </section>;
}
