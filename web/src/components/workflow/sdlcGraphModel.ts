export type GraphView = "lifecycle" | "modules" | "workflow" | "trace" | "delivery" | "timeline";
export interface SdlcNode { id: string; kind: string; label: string; status: string; views: GraphView[]; data: Record<string, unknown> }
export interface SdlcEdge { id: string; source: string; target: string; relation: string; views: GraphView[]; event?: string; collapsedVia?: string[] }
export interface SdlcGraph {
  scope: { request_id: string; revision: number; run_id: string; proposal_digest: string; source_seq: number; head_seq: number; historical: boolean };
  nodes: SdlcNode[]; edges: SdlcEdge[]; diagnostics: string[];
  capabilities: Record<string, { status: string; reason?: string }>;
  plans: import("../../api/types").PlanPreview[];
  versions: { ref: string; digest: string; seq: number; event_id: string; admitted: boolean }[];
  timeline: { seq: number; id: string; ts: string; type: string; task_id?: string; actor?: string; origin: string; payload: Record<string, unknown> }[];
  analysis: { intervals: { operation_id: string; task_id: string; role: string; status: string; start_ms: number | null; end_ms: number | null; duration_ms: number | null; queue_wait_ms: number | null }[]; critical_path: { status: string; node_ids: string[]; duration_ms: number | null; method: string } };
}
export interface SdlcStructure extends Pick<SdlcGraph, "scope" | "nodes" | "edges" | "diagnostics"> {
  schema_version: "sdlc-workbench-structure.v1";
  partial: true;
  generation: string;
}
export type WorkflowGraphResult = SdlcGraph | SdlcStructure;
export const graphViews: [GraphView, string][] = [["workflow", "Graph"], ["timeline", "Timeline"], ["lifecycle", "Lifecycle"], ["modules", "Task"], ["trace", "Trace"], ["delivery", "Delivery"]];
export const statusLabels: Record<string, string> = {
  unknown: "未知", draft: "草稿", pending: "等待", running: "执行中", in_progress: "执行中", reserved: "已预留", requested: "排队中",
  settled: "操作结束", accepted: "内核已接纳", reported: "已报告", failed: "失败", blocked: "阻塞", evidence_missing: "缺少接纳证据",
  done: "已完成", confirmed: "已确认", approved: "已批准", rejected: "已拒绝", proposed: "待确认", cancelled: "已取消", queued: "排队中",
  stale: "证据已过期", closed: "已闭环", open: "未闭环", mapped: "已有任务映射", uncovered: "尚未覆盖", passed: "已通过", completed: "已结束", aggregating: "汇聚中", superseded: "已被替代",
};
export const activeStatus = (status: string) => ["running", "in_progress", "dispatched"].includes(status);

// These are presentation aliases for the built-in profiles, not stage inference.
const stageLabels: Record<string, string> = {
  "prd-scan": "需求扫描", "prd-plan": "规划", "prd-lanes-impl": "实施", "prd-lanes-verify": "验证", "prd-lanes-final": "最终验收",
  "issue-triage": "问题分析", "issue-lanes-impl": "修复", "issue-lanes-verify": "验证", "issue-lanes-final": "最终验收",
  "flow-scan": "扫描", "flow-plan": "规划", "flow-lanes-impl": "实施", "flow-lanes-verify": "验证", "flow-lanes-final": "最终验收",
  "flow-verify-bridge": "验证衔接", "flow-module-parity-scan": "模块对账", "flow-final-judge": "最终验收",
  "prd-post-verify-discovery": "验证后扫描", "issue-post-verify-discovery": "验证后扫描", "refactor-post-verify-discovery": "验证后扫描",
};
export function workflowNodeLabel(node: SdlcNode): string {
  if (node.id === "workflow:terminal:done" && node.label === "terminal done") return "结束门禁";
  if (!node.id.startsWith("workflow:stage:")) return node.label;
  const stageId = node.id.slice("workflow:stage:".length);
  return node.label === stageId ? stageLabels[stageId] || node.label : node.label;
}

export function isWaitingAuxiliaryStage(node: SdlcNode): boolean {
  const id = node.id.slice("workflow:stage:".length);
  if (!node.id.startsWith("workflow:stage:") || !["prd-post-verify-discovery", "issue-post-verify-discovery", "refactor-post-verify-discovery"].includes(id) || node.status !== "pending") return false;
  const definition = node.data.definition;
  const trigger = definition && typeof definition === "object" ? (definition as Record<string, unknown>).trigger : undefined;
  return trigger === undefined || trigger === "flow.discovery.requested";
}

/** Keep the stage path readable; the complete compiler graph remains selectable. */
export function workflowSummaryIds(nodes: SdlcNode[]): Set<string> {
  const hasStages = nodes.some(n => n.id.startsWith("workflow:stage:"));
  return new Set(nodes.filter(n => !hasStages || (
    n.id.startsWith("workflow:stage:") || n.id.startsWith("workflow:terminal:")
  )).map(n => n.id));
}

/** Fold only paths through known compiled workflow nodes, never infer dependencies. */
export function workflowSummaryEdges(nodes: SdlcNode[], edges: SdlcEdge[], visible: Set<string>): SdlcEdge[] {
  const workflow = new Set(nodes.filter(node => node.kind === "workflow").map(node => node.id));
  const outgoing = new Map<string, SdlcEdge[]>();
  for (const edge of edges) if (edge.views.includes("workflow") && workflow.has(edge.source) && workflow.has(edge.target)) {
    const list = outgoing.get(edge.source) ?? []; list.push(edge); outgoing.set(edge.source, list);
  }
  const result = edges.filter(edge => edge.views.includes("workflow") && visible.has(edge.source) && visible.has(edge.target));
  const connected = new Set(result.map(edge => `${edge.source}\0${edge.target}`));
  for (const source of visible) {
    if (!workflow.has(source)) continue;
    const queue: Array<{ id: string; path: string[] }> = [{ id: source, path: [] }];
    const visited = new Set([source]);
    for (let i = 0; i < queue.length; i++) {
      const current = queue[i];
      for (const edge of outgoing.get(current.id) ?? []) {
        const target = edge.target;
        if (target === source) continue;
        if (visible.has(target)) {
          const key = `${source}\0${target}`;
          if (current.path.length && !connected.has(key)) {
            result.push({ id: `folded:${source}:${target}`, source, target, relation: "折叠路径", views: ["workflow"], collapsedVia: current.path });
            connected.add(key);
          }
        } else if (!target.startsWith("workflow:stage:") && !target.startsWith("workflow:terminal:") && !visited.has(target)) {
          visited.add(target); queue.push({ id: target, path: [...current.path, target] });
        }
      }
    }
  }
  return result;
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

/** Keep the connected stage path prominent; detached or folded routes stay separate. */
export function workflowSummaryPositions(nodes: SdlcNode[], edges: SdlcEdge[]): Map<string, { x: number; y: number }> {
  const remaining = new Set(nodes.map(node => node.id));
  const groups: string[][] = [];
  while (remaining.size) {
    const root = remaining.values().next().value as string;
    const queue = [root]; const group: string[] = []; remaining.delete(root);
    while (queue.length) {
      const id = queue.shift()!; group.push(id);
      for (const edge of edges) {
        const next = edge.source === id ? edge.target : edge.target === id ? edge.source : "";
        if (next && remaining.delete(next)) queue.push(next);
      }
    }
    groups.push(group.sort());
  }
  groups.sort((a, b) => b.length - a.length || a[0].localeCompare(b[0]));
  const result = new Map<string, { x: number; y: number }>();
  let top = 0;
  for (const group of groups) {
    const members = new Set(group);
    const base = graphPositions(nodes.filter(node => members.has(node.id)), edges);
    const ys = [...base.values()].map(position => position.x * 0.65);
    const minY = Math.min(0, ...ys); const maxY = Math.max(0, ...ys);
    for (const [id, position] of base) result.set(id, { x: position.y * 1.55, y: top + position.x * 0.65 - minY });
    top += maxY - minY + 200;
  }
  return result;
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
