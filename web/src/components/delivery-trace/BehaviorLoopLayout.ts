import type { LoopEdge, LoopModel, LoopNode, LoopNodeKind } from "./BehaviorLoopModel";

export const LOOP_LAYOUT_MODES = ["auto", "ring", "tree", "dag", "star"] as const;

export type LoopLayoutMode = typeof LOOP_LAYOUT_MODES[number];
export type ResolvedLoopLayoutMode = Exclude<LoopLayoutMode, "auto">;

export interface LoopLayoutResult {
  model: LoopModel;
  reason: string;
  resolvedMode: ResolvedLoopLayoutMode;
}

type LoopStage = "observe" | "diagnose" | "act" | "verify" | "learn";

const STAGE_ORDER: LoopStage[] = ["observe", "diagnose", "act", "verify", "learn"];
const KIND_ORDER: LoopNodeKind[] = ["trace", "behavior", "diagnosis", "eval", "improvement", "action", "verify", "learn"];

export function normalizeLoopLayoutMode(value: string): LoopLayoutMode {
  return LOOP_LAYOUT_MODES.includes(value as LoopLayoutMode) ? value as LoopLayoutMode : "auto";
}

export function layoutLoopModel(model: LoopModel, requestedMode: LoopLayoutMode): LoopLayoutResult {
  if (!model.nodes.length) {
    return { model, reason: "no loop nodes", resolvedMode: "dag" };
  }
  const auto = chooseAutoLayout(model);
  const resolvedMode = requestedMode === "auto" ? auto.mode : requestedMode;
  const nodes = applyLayout(model.nodes, resolvedMode);
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const edges = addViewEdges(model.edges, nodes, resolvedMode);
  return {
    model: {
      actions: model.actions.map((node) => nodeById.get(node.id) ?? node),
      edges,
      nodes,
      traceNodes: model.traceNodes.map((node) => nodeById.get(node.id) ?? node),
    },
    reason: requestedMode === "auto" ? auto.reason : `${resolvedMode} selected`,
    resolvedMode,
  };
}

function chooseAutoLayout(model: LoopModel): { mode: ResolvedLoopLayoutMode; reason: string } {
  const counts = countKinds(model.nodes);
  const stageCoverage = new Set(model.nodes.map(stageForNode)).size;
  const branchCount = counts.improvement + counts.action;
  if (model.nodes.length > 28 || counts.trace > 8) {
    return { mode: "dag", reason: "large trace set" };
  }
  if (branchCount >= 5) {
    return { mode: "tree", reason: "multiple candidates/actions" };
  }
  if (counts.action >= 3 && counts.trace <= 3) {
    return { mode: "star", reason: "action hub" };
  }
  if (stageCoverage >= 4) {
    return { mode: "ring", reason: "closed-loop stages" };
  }
  if (branchCount >= 3) {
    return { mode: "tree", reason: "branching loop" };
  }
  return { mode: "dag", reason: "linear evidence chain" };
}

function applyLayout(nodes: LoopNode[], mode: ResolvedLoopLayoutMode): LoopNode[] {
  if (mode === "ring") return ringLayout(nodes);
  if (mode === "tree") return treeLayout(nodes);
  if (mode === "star") return starLayout(nodes);
  return dagLayout(nodes);
}

function dagLayout(nodes: LoopNode[]): LoopNode[] {
  const groups = groupBy(nodes, (node) => node.kind);
  const columns = KIND_ORDER.filter((kind) => groups.get(kind)?.length);
  return nodes.map((node) => {
    const columnIndex = Math.max(0, columns.indexOf(node.kind));
    const group = groups.get(node.kind) ?? [node];
    const index = group.findIndex((item) => item.id === node.id);
    return withPosition(node, columnX(columnIndex, Math.max(1, columns.length)), distributedY(index, group.length));
  });
}

function treeLayout(nodes: LoopNode[]): LoopNode[] {
  const groups = groupBy(nodes, stageForNode);
  return nodes.map((node) => {
    const stage = stageForNode(node);
    const group = groups.get(stage) ?? [node];
    const index = group.findIndex((item) => item.id === node.id);
    const stageIndex = STAGE_ORDER.indexOf(stage);
    return withPosition(node, columnX(stageIndex, STAGE_ORDER.length), distributedY(index, group.length));
  });
}

function ringLayout(nodes: LoopNode[]): LoopNode[] {
  const centers: Record<LoopStage, { x: number; y: number }> = {
    observe: { x: 50, y: 14 },
    diagnose: { x: 84, y: 36 },
    act: { x: 70, y: 78 },
    verify: { x: 30, y: 78 },
    learn: { x: 16, y: 36 },
  };
  const groups = groupBy(nodes, stageForNode);
  return nodes.map((node) => {
    const stage = stageForNode(node);
    const center = centers[stage];
    const group = groups.get(stage) ?? [node];
    const index = group.findIndex((item) => item.id === node.id);
    const position = ringStagePosition(stage, center, index, group);
    return withPosition(node, position.x, position.y);
  });
}

function ringStagePosition(
  stage: LoopStage,
  center: { x: number; y: number },
  index: number,
  group: LoopNode[],
): { x: number; y: number } {
  if (group.length < 3) {
    const offset = orbitOffset(index, group.length, 7);
    return { x: center.x + offset.x, y: center.y + offset.y };
  }

  const axis = ringSpreadAxis(stage);
  const spacing = ringSpreadSpacing(group);
  const range = ringSpreadRange(stage, axis);
  const coordinate = spreadCoordinate(center[axis], index, group.length, range.min, range.max, spacing);
  return axis === "x" ? { x: coordinate, y: center.y } : { x: center.x, y: coordinate };
}

function ringSpreadAxis(stage: LoopStage): "x" | "y" {
  return stage === "observe" || stage === "act" || stage === "verify" ? "x" : "y";
}

function ringSpreadRange(stage: LoopStage, axis: "x" | "y"): { min: number; max: number } {
  if (axis === "y") return { min: 16, max: 70 };
  if (stage === "observe") return { min: 18, max: 82 };
  if (stage === "verify") return { min: 14, max: 66 };
  return { min: 34, max: 86 };
}

function ringSpreadSpacing(group: LoopNode[]): number {
  const largest = Math.max(...group.map((node) => node.size), 68);
  return clamp((largest / 520) * 100 + 2, 16, 20);
}

function spreadCoordinate(center: number, index: number, count: number, min: number, max: number, spacing: number): number {
  if (count <= 1) return center;
  const span = spacing * (count - 1);
  if (span >= max - min) {
    return min + ((max - min) * index) / (count - 1);
  }
  const start = clamp(center - span / 2, min, max - span);
  return start + spacing * index;
}

function starLayout(nodes: LoopNode[]): LoopNode[] {
  const hubKinds = new Set<LoopNodeKind>(["diagnosis", "improvement", "action"]);
  const hubNodes = nodes.filter((node) => hubKinds.has(node.kind));
  const satelliteNodes = nodes.filter((node) => !hubKinds.has(node.kind));
  const satelliteCount = Math.max(1, satelliteNodes.length);
  return nodes.map((node) => {
    if (hubKinds.has(node.kind)) {
      const index = Math.max(0, hubNodes.findIndex((item) => item.id === node.id));
      return withPosition(node, 50, distributedY(index, hubNodes.length, 40, 60));
    }
    const index = Math.max(0, satelliteNodes.findIndex((item) => item.id === node.id));
    const angle = (-90 + (360 * index) / satelliteCount) * Math.PI / 180;
    return withPosition(node, 50 + Math.cos(angle) * 36, 50 + Math.sin(angle) * 36);
  });
}

function addViewEdges(edges: LoopEdge[], nodes: LoopNode[], mode: ResolvedLoopLayoutMode): LoopEdge[] {
  if (mode !== "ring") return edges;
  const observe = nodes.find((node) => stageForNode(node) === "observe");
  const learn = nodes.find((node) => stageForNode(node) === "learn");
  if (!observe || !learn) return edges;
  const id = `cycle:${learn.id}->${observe.id}`;
  if (edges.some((edge) => edge.id === id || (edge.from === learn.id && edge.to === observe.id))) return edges;
  return [...edges, { id, from: learn.id, kind: "cycle", status: "projection", to: observe.id }];
}

function stageForNode(node: LoopNode): LoopStage {
  if (node.kind === "trace") return "observe";
  if (node.kind === "action" || node.kind === "improvement") return "act";
  if (node.kind === "verify" || node.kind === "eval") return "verify";
  if (node.kind === "learn") return "learn";
  return "diagnose";
}

function countKinds(nodes: LoopNode[]): Record<LoopNodeKind, number> {
  const out = Object.fromEntries(KIND_ORDER.map((kind) => [kind, 0])) as Record<LoopNodeKind, number>;
  for (const node of nodes) out[node.kind] += 1;
  return out;
}

function groupBy<T, K>(items: T[], keyFor: (item: T) => K): Map<K, T[]> {
  const out = new Map<K, T[]>();
  for (const item of items) {
    const key = keyFor(item);
    const group = out.get(key) ?? [];
    group.push(item);
    out.set(key, group);
  }
  return out;
}

function columnX(index: number, count: number): number {
  if (count <= 1) return 50;
  return 10 + (80 * index) / (count - 1);
}

function distributedY(index: number, count: number, min = 18, max = 82): number {
  if (count <= 1) return 50;
  return min + ((max - min) * index) / (count - 1);
}

function orbitOffset(index: number, count: number, radius: number): { x: number; y: number } {
  if (count <= 1) return { x: 0, y: 0 };
  const angle = (-90 + (360 * index) / count) * Math.PI / 180;
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
}

function withPosition(node: LoopNode, x: number, y: number): LoopNode {
  return { ...node, x: clamp(x, 8, 92), y: clamp(y, 10, 90) };
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, Math.round(value * 10) / 10));
}
