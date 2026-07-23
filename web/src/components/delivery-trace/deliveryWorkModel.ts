import type {
  DeliveryTaskTry,
  DeliveryTrace,
  DeliveryTraceNode,
  GoalCoverageNode,
} from "../../api/types";
import {
  claimNodes,
  resultNodesByTask,
  taskNodesById,
  type GoalCoverageClaimNode,
} from "../goal-coverage/goalCoverageModel.js";

const DONE_STATUSES = new Set(["done", "completed", "passed", "shipped", "cancelled"]);
const RUNNING_STATUSES = new Set(["in_progress", "running", "review", "test", "judge", "dispatched"]);

export interface DeliveryWorkTask {
  taskId: string;
  title: string;
  status: string;
  owner: string;
  blockedBy: string[];
  claimIds: string[];
  primaryClaimId: string;
  alsoClaimIds: string[];
  tries: DeliveryTaskTry[];
  results: GoalCoverageNode[];
  evidenceRefs: string[];
  executionNode: DeliveryTraceNode | null;
}

export interface DeliveryWorkClaim {
  claim: GoalCoverageClaimNode;
  tasks: DeliveryWorkTask[];
  linkedTasks: DeliveryWorkTask[];
}

export interface DeliveryWorkModel {
  goal: GoalCoverageNode | null;
  claims: DeliveryWorkClaim[];
  unclaimedTasks: DeliveryWorkTask[];
  tasks: DeliveryWorkTask[];
  summary: {
    total: number;
    done: number;
    running: number;
    blocked: number;
    verified: number;
  };
}

export function buildDeliveryWorkModel(trace: DeliveryTrace): DeliveryWorkModel {
  const graph = trace.goal_coverage_graph ?? null;
  const claims = claimNodes(graph);
  const claimById = new Map(claims.map((claim) => [claim.goal_claim_id, claim]));
  const graphTasks = taskNodesById(graph);
  const resultsByTask = resultNodesByTask(graph);
  const executionByTask = new Map(
    (trace.execution_graph?.nodes ?? []).map((node) => [node.task_id, node]),
  );
  const orderedTaskIds = unique([
    ...graphTasks.keys(),
    ...executionByTask.keys(),
  ]);

  const tasks = orderedTaskIds.map((taskId): DeliveryWorkTask => {
    const task = graphTasks.get(taskId);
    const executionNode = executionByTask.get(taskId) ?? null;
    const inferredClaimIds = claims
      .filter((claim) => claim.task_ids?.includes(taskId))
      .map((claim) => claim.goal_claim_id);
    const claimIds = unique([
      ...(task?.goal_claim_ids ?? []),
      ...inferredClaimIds,
    ]).filter((claimId) => claimById.has(claimId));
    const primaryClaimId = claimIds[0] ?? "";
    const results = resultsByTask.get(taskId) ?? [];
    return {
      taskId,
      title: task?.title || executionNode?.title || taskId,
      status: executionNode?.actual.status || task?.status || "pending",
      owner: task?.owner
        || executionNode?.actual.assigned_to
        || executionNode?.planned.owner_instance
        || executionNode?.planned.owner_role
        || "unassigned",
      blockedBy: unique(executionNode?.planned.blocked_by ?? []),
      claimIds,
      primaryClaimId,
      alsoClaimIds: claimIds.slice(1),
      tries: trace.task_lifecycle?.tasks?.[taskId]?.tries ?? [],
      results,
      evidenceRefs: unique([
        ...(executionNode?.actual.evidence_events ?? []),
        ...results.flatMap((result) => result.evidence_refs ?? []),
      ]),
      executionNode,
    };
  });

  const workClaims = claims.map((claim): DeliveryWorkClaim => ({
    claim,
    tasks: tasks.filter((task) => task.primaryClaimId === claim.goal_claim_id),
    linkedTasks: tasks.filter((task) => task.alsoClaimIds.includes(claim.goal_claim_id)),
  }));
  const unclaimedTasks = tasks.filter((task) => !task.primaryClaimId);

  return {
    goal: graph?.nodes.find((node) => node.kind === "goal") ?? null,
    claims: workClaims,
    unclaimedTasks,
    tasks,
    summary: {
      total: tasks.length,
      done: tasks.filter((task) => DONE_STATUSES.has(task.status)).length,
      running: tasks.filter((task) => RUNNING_STATUSES.has(task.status)).length,
      blocked: tasks.filter((task) => ["blocked", "failed"].includes(task.status)).length,
      verified: tasks.filter((task) => task.results.some((result) => (
        result.current !== false && result.status === "passed"
      ))).length,
    },
  };
}

export function latestTry(task: DeliveryWorkTask): DeliveryTaskTry | null {
  return task.tries[task.tries.length - 1] ?? null;
}

export function resultStatus(task: DeliveryWorkTask): string {
  return task.results.find((result) => result.current !== false)?.status
    ?? task.results[0]?.status
    ?? "unverified";
}

function unique(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}
