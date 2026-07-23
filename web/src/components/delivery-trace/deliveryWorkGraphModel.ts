import { hierarchy, tree } from "d3-hierarchy";

import type {
  DeliveryWorkClaim,
  DeliveryWorkModel,
  DeliveryWorkTask,
} from "./deliveryWorkModel.js";
import { resultStatus } from "./deliveryWorkModel.js";

const ROW_GAP = 126;
const COLUMN_GAP = 330;
const CANVAS_PADDING = 36;

export type DeliveryWorkGraphNodeKind = "goal" | "claim" | "task" | "unmapped";
export type DeliveryWorkGraphEdgeKind = "tree" | "secondary";

export interface DeliveryWorkGraphNode {
  id: string;
  kind: DeliveryWorkGraphNodeKind;
  title: string;
  reference: string;
  status: string;
  owner: string;
  implementation: string;
  verification: string;
  childCount: number;
  collapsed: boolean;
  claimId: string;
  taskId: string;
  searchText: string;
  position: { x: number; y: number };
}

export interface DeliveryWorkGraphEdge {
  id: string;
  source: string;
  target: string;
  kind: DeliveryWorkGraphEdgeKind;
}

export interface DeliveryWorkGraph {
  nodes: DeliveryWorkGraphNode[];
  edges: DeliveryWorkGraphEdge[];
}

interface WorkTreeDatum {
  id: string;
  children: WorkTreeDatum[];
}

export function buildDeliveryWorkGraph(
  model: DeliveryWorkModel,
  collapsedIds: ReadonlySet<string> = new Set(),
): DeliveryWorkGraph {
  const goalId = workGoalNodeId(model);
  const nodeById = new Map<string, Omit<DeliveryWorkGraphNode, "position">>();
  const childIdsByParent = new Map<string, string[]>();
  const claimNodeIdByClaimId = new Map<string, string>();
  const taskNodeIdByTaskId = new Map<string, string>();

  const claimIds: string[] = [];
  for (const workClaim of model.claims) {
    const claimNode = claimGraphNode(workClaim, collapsedIds);
    claimIds.push(claimNode.id);
    claimNodeIdByClaimId.set(workClaim.claim.goal_claim_id, claimNode.id);
    nodeById.set(claimNode.id, claimNode);

    const taskIds = workClaim.tasks.map((task) => {
      const taskNode = taskGraphNode(task);
      nodeById.set(taskNode.id, taskNode);
      taskNodeIdByTaskId.set(task.taskId, taskNode.id);
      return taskNode.id;
    });
    childIdsByParent.set(claimNode.id, taskIds);
  }

  if (model.unclaimedTasks.length) {
    const unmappedId = "unmapped:work";
    const collapsed = collapsedIds.has(unmappedId);
    claimIds.push(unmappedId);
    nodeById.set(unmappedId, {
      id: unmappedId,
      kind: "unmapped",
      title: "Unmapped work",
      reference: `${model.unclaimedTasks.length} task${model.unclaimedTasks.length === 1 ? "" : "s"}`,
      status: "unmapped",
      owner: "no Goal Claim",
      implementation: "current tasks",
      verification: "per task",
      childCount: model.unclaimedTasks.length,
      collapsed,
      claimId: "",
      taskId: "",
      searchText: `unmapped work ${model.unclaimedTasks.map((task) => `${task.taskId} ${task.title}`).join(" ")}`.toLowerCase(),
    });
    const taskIds = model.unclaimedTasks.map((task) => {
      const taskNode = taskGraphNode(task);
      nodeById.set(taskNode.id, taskNode);
      taskNodeIdByTaskId.set(task.taskId, taskNode.id);
      return taskNode.id;
    });
    childIdsByParent.set(unmappedId, taskIds);
  }

  const goalCollapsed = collapsedIds.has(goalId);
  nodeById.set(goalId, {
    id: goalId,
    kind: "goal",
    title: model.goal?.title || "Delivery goal",
    reference: model.goal?.goal_id || model.goal?.node_id || "goal",
    status: model.goal?.status || "unknown",
    owner: "delivery",
    implementation: `${model.summary.done}/${model.summary.total} done`,
    verification: `${model.summary.verified}/${model.summary.total} verified`,
    childCount: claimIds.length,
    collapsed: goalCollapsed,
    claimId: "",
    taskId: "",
    searchText: `${model.goal?.title || "delivery goal"} ${model.goal?.goal_id || ""}`.toLowerCase(),
  });
  childIdsByParent.set(goalId, claimIds);

  const datum = buildVisibleTree(goalId, nodeById, childIdsByParent, collapsedIds, new Set());
  const root = tree<WorkTreeDatum>().nodeSize([ROW_GAP, COLUMN_GAP])(
    hierarchy(datum, (item) => item.children),
  );

  let minBreadth = 0;
  root.each((item) => {
    minBreadth = Math.min(minBreadth, item.x);
  });

  const nodes: DeliveryWorkGraphNode[] = [];
  const visibleIds = new Set<string>();
  root.eachBefore((item) => {
    const node = nodeById.get(item.data.id);
    if (!node) return;
    visibleIds.add(node.id);
    nodes.push({
      ...node,
      position: {
        x: item.y + CANVAS_PADDING,
        y: item.x - minBreadth + CANVAS_PADDING,
      },
    });
  });

  const edges: DeliveryWorkGraphEdge[] = [];
  root.links().forEach((link) => {
    edges.push({
      id: `tree:${link.source.data.id}:${link.target.data.id}`,
      source: link.source.data.id,
      target: link.target.data.id,
      kind: "tree",
    });
  });

  for (const workClaim of model.claims) {
    const claimNodeId = claimNodeIdByClaimId.get(workClaim.claim.goal_claim_id);
    if (!claimNodeId || !visibleIds.has(claimNodeId)) continue;
    for (const task of workClaim.linkedTasks) {
      const taskNodeId = taskNodeIdByTaskId.get(task.taskId);
      if (!taskNodeId || !visibleIds.has(taskNodeId)) continue;
      edges.push({
        id: `secondary:${claimNodeId}:${taskNodeId}`,
        source: claimNodeId,
        target: taskNodeId,
        kind: "secondary",
      });
    }
  }

  return { nodes, edges };
}

export function workGoalNodeId(model: DeliveryWorkModel): string {
  return model.goal?.node_id || `goal:${model.goal?.goal_id || "delivery"}`;
}

export function workClaimNodeId(claimId: string): string {
  return `claim:${claimId}`;
}

export function workTaskNodeId(taskId: string): string {
  return `task:${taskId}`;
}

function claimGraphNode(
  workClaim: DeliveryWorkClaim,
  collapsedIds: ReadonlySet<string>,
): Omit<DeliveryWorkGraphNode, "position"> {
  const { claim } = workClaim;
  const id = workClaimNodeId(claim.goal_claim_id);
  const taskCount = workClaim.tasks.length + workClaim.linkedTasks.length;
  return {
    id,
    kind: "claim",
    title: claim.title,
    reference: claim.goal_claim_id,
    status: claim.plan_coverage || "uncovered",
    owner: taskCount ? `${taskCount} task${taskCount === 1 ? "" : "s"}` : "no owner",
    implementation: claim.execution || "pending",
    verification: claim.task_verification || "unverified",
    childCount: workClaim.tasks.length,
    collapsed: collapsedIds.has(id),
    claimId: claim.goal_claim_id,
    taskId: "",
    searchText: `${claim.goal_claim_id} ${claim.title}`.toLowerCase(),
  };
}

function taskGraphNode(task: DeliveryWorkTask): Omit<DeliveryWorkGraphNode, "position"> {
  const latest = task.tries[task.tries.length - 1];
  const implementation = task.tries.length
    ? `${task.tries.length} ${task.tries.length === 1 ? "try" : "tries"} · ${latest?.outcome || task.status}`
    : "no tries";
  return {
    id: workTaskNodeId(task.taskId),
    kind: "task",
    title: task.title,
    reference: task.taskId,
    status: task.status,
    owner: task.owner,
    implementation,
    verification: resultStatus(task),
    childCount: 0,
    collapsed: false,
    claimId: task.primaryClaimId,
    taskId: task.taskId,
    searchText: `${task.taskId} ${task.title} ${task.owner}`.toLowerCase(),
  };
}

function buildVisibleTree(
  nodeId: string,
  nodeById: ReadonlyMap<string, Omit<DeliveryWorkGraphNode, "position">>,
  childIdsByParent: ReadonlyMap<string, string[]>,
  collapsedIds: ReadonlySet<string>,
  ancestors: Set<string>,
): WorkTreeDatum {
  if (ancestors.has(nodeId)) return { id: nodeId, children: [] };
  if (!nodeById.has(nodeId) || collapsedIds.has(nodeId)) {
    return { id: nodeId, children: [] };
  }
  const nextAncestors = new Set(ancestors);
  nextAncestors.add(nodeId);
  return {
    id: nodeId,
    children: (childIdsByParent.get(nodeId) ?? []).map((childId) => (
      buildVisibleTree(childId, nodeById, childIdsByParent, collapsedIds, nextAncestors)
    )),
  };
}
