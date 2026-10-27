export type GraphView = "lifecycle" | "modules" | "workflow" | "trace" | "delivery" | "timeline";
export interface SdlcNode { id: string; kind: string; label: string; status: string; views: GraphView[]; data: Record<string, unknown> }
export interface SdlcEdge { id: string; source: string; target: string; relation: string; views: GraphView[]; event?: string }
export interface SdlcGraph {
  scope: { request_id: string; revision: number; run_id: string; proposal_digest: string; source_seq: number; head_seq: number; historical: boolean };
  nodes: SdlcNode[]; edges: SdlcEdge[]; diagnostics: string[];
  capabilities: Record<string, { status: string; reason?: string }>;
  plans: import("../../api/types").PlanPreview[];
  versions: { ref: string; digest: string; seq: number; event_id: string; admitted: boolean }[];
  timeline: { seq: number; id: string; ts: string; type: string; task_id?: string; actor?: string; origin: string; payload: Record<string, unknown> }[];
  analysis: { intervals: { operation_id: string; task_id: string; role: string; status: string; start_ms: number | null; end_ms: number | null; duration_ms: number | null; queue_wait_ms: number | null }[]; critical_path: { status: string; node_ids: string[]; duration_ms: number | null; method: string } };
}
export const graphViews: [GraphView, string][] = [["lifecycle", "Lifecycle"], ["modules", "Task"], ["workflow", "Dag"], ["trace", "Trace"], ["delivery", "Delivery"], ["timeline", "Timeline"]];
export const statusLabels: Record<string, string> = {
  unknown: "未知", draft: "草稿", pending: "等待", running: "执行中", in_progress: "执行中", reserved: "已预留", requested: "排队中",
  settled: "操作结束", accepted: "内核已接纳", reported: "已报告", failed: "失败", blocked: "阻塞", evidence_missing: "缺少接纳证据",
  done: "已完成", confirmed: "已确认", approved: "已批准", rejected: "已拒绝", proposed: "待确认", cancelled: "已取消", queued: "排队中",
  stale: "证据已过期", closed: "已闭环", open: "未闭环", mapped: "已有任务映射", uncovered: "尚未覆盖", passed: "已通过", completed: "已结束", aggregating: "汇聚中", superseded: "已被替代",
};
export const activeStatus = (status: string) => ["running", "in_progress", "dispatched"].includes(status);

/** Keep the stage path readable; the complete compiler graph remains selectable. */
export function workflowSummaryIds(nodes: SdlcNode[]): Set<string> {
  const hasStages = nodes.some(n => n.id.startsWith("workflow:stage:"));
  return new Set(nodes.filter(n => !hasStages || (
    !["fanout", "agent", "attempt", "run_anchor"].includes(n.kind)
    && (n.kind !== "workflow" || !["role_stage", "aggregate_stage", "external_event"].includes(
      String((n.data.definition as Record<string, unknown> | undefined)?.type)))
  )).map(n => n.id));
}

export function expandedNodeIds(nodes: SdlcNode[], edges: SdlcEdge[], collapsed: Set<string>): Set<string> {
  const containment = edges.filter(e => ["decomposes", "contains", "executes", "delegates", "attempt"].includes(e.relation));
  const children = new Set(containment.map(e => e.target));
  const visible = new Set(nodes.filter(n => !children.has(n.id)).map(n => n.id));
  const queue = [...visible];
  while (queue.length) {
    const id = queue.shift()!;
    if (collapsed.has(id)) continue;
    for (const e of containment) if (e.source === id && !visible.has(e.target)) { visible.add(e.target); queue.push(e.target); }
  }
  return visible;
}

/** Dependencies drive top-down ranks; back/rework edges never duplicate nodes. */
export function graphPositions(nodes: SdlcNode[], edges: SdlcEdge[]): Map<string, { x: number; y: number }> {
  const ids = new Set(nodes.map(n => n.id));
  const incoming = new Map(nodes.map(n => [n.id, [] as string[]]));
  for (const edge of edges) if (ids.has(edge.source) && ids.has(edge.target) && !/rework|retry/.test(edge.relation)) incoming.get(edge.target)!.push(edge.source);
  const columns = new Map<string, number>();
  const pending = new Set(ids);
  while (pending.size) {
    let ready = [...pending].filter(id => incoming.get(id)!.every(p => columns.has(p))).sort();
    // Cycles are displayed, not silently removed. A stable anchor cuts layout recursion only.
    if (!ready.length) ready = [[...pending].sort()[0]];
    for (const id of ready) {
      columns.set(id, Math.max(-1, ...incoming.get(id)!.map(p => columns.get(p) ?? -1)) + 1);
      pending.delete(id);
    }
  }
  const widths = new Map<number, number>();
  for (const rank of columns.values()) widths.set(rank, (widths.get(rank) || 0) + 1);
  const rows = new Map<number, number>();
  return new Map([...columns].sort(([a], [b]) => a.localeCompare(b)).map(([id, column]) => {
    const row = rows.get(column) || 0; rows.set(column, row + 1);
    return [id, { x: (row - (widths.get(column)! - 1) / 2) * 310, y: column * 200 }];
  }));
}

/** Status, labels and selection don't change the fixed-size dependency layout. */
export class GraphLayoutCache {
  private key = "";
  private positions = new Map<string, { x: number; y: number }>();

  get(nodes: SdlcNode[], edges: SdlcEdge[]) {
    const ids = new Set(nodes.map(node => node.id));
    const links = edges.filter(edge => ids.has(edge.source) && ids.has(edge.target) && !/rework|retry/.test(edge.relation))
      .map(edge => JSON.stringify([edge.source, edge.target])).sort();
    const key = JSON.stringify([[...ids].sort(), links]);
    if (key !== this.key) {
      this.positions = graphPositions(nodes, edges);
      this.key = key;
    }
    return this.positions;
  }
}

/** Structural reachability is confirmed; business impact beyond declared links needs review. */
export function relatedNodes(id: string, edges: SdlcEdge[], direction: "upstream" | "downstream"): Set<string> {
  const result = new Set([id]); const queue = [id];
  while (queue.length) {
    const current = queue.shift();
    for (const edge of edges) {
      const from = direction === "downstream" ? edge.source : edge.target;
      const to = direction === "downstream" ? edge.target : edge.source;
      if (from === current && !result.has(to)) { result.add(to); queue.push(to); }
    }
  }
  return result;
}
