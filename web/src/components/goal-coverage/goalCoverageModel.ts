import type { GoalCoverageGraph, GoalCoverageNode } from "../../api/types";

export type GoalCoverageClaimNode = GoalCoverageNode & {
  kind: "goal_claim";
  goal_claim_id: string;
};

export function claimNodes(graph: GoalCoverageGraph | null): GoalCoverageClaimNode[] {
  if (!graph) return [];
  return graph.nodes.filter((node): node is GoalCoverageClaimNode => (
    node.kind === "goal_claim" && Boolean(node.goal_claim_id)
  ));
}

export function taskNodesById(graph: GoalCoverageGraph | null): Map<string, GoalCoverageNode> {
  const tasks = new Map<string, GoalCoverageNode>();
  for (const node of graph?.nodes ?? []) {
    if (node.kind === "task" && node.task_id) tasks.set(node.task_id, node);
  }
  return tasks;
}

export function resultNodesByTask(graph: GoalCoverageGraph | null): Map<string, GoalCoverageNode[]> {
  const results = new Map<string, GoalCoverageNode[]>();
  for (const node of graph?.nodes ?? []) {
    if (node.kind !== "verification_result" || !node.task_id) continue;
    const rows = results.get(node.task_id) ?? [];
    rows.push(node);
    results.set(node.task_id, rows);
  }
  return results;
}

export function filterClaims(
  graph: GoalCoverageGraph | null,
  query: string,
): GoalCoverageClaimNode[] {
  const claims = claimNodes(graph);
  const normalized = query.trim().toLowerCase();
  if (!normalized) return claims;
  const tasks = taskNodesById(graph);
  return claims.filter((claim) => {
    const taskText = (claim.task_ids ?? []).map((taskId) => {
      const task = tasks.get(taskId);
      return `${taskId} ${task?.title ?? ""}`;
    }).join(" ");
    return [
      claim.goal_claim_id,
      claim.title,
      claim.source_ref ?? "",
      taskText,
    ].join(" ").toLowerCase().includes(normalized);
  });
}

export function preferredClaimId(
  graph: GoalCoverageGraph | null,
  currentId: string,
): string {
  const claims = claimNodes(graph);
  if (claims.some((claim) => claim.goal_claim_id === currentId)) return currentId;
  return claims.find((claim) => claim.plan_coverage === "uncovered")?.goal_claim_id
    ?? claims.find((claim) => ["open", "blocked"].includes(claim.closure ?? ""))?.goal_claim_id
    ?? claims[0]?.goal_claim_id
    ?? "";
}

export function statusTone(status: string | undefined): "ok" | "warn" | "err" | "info" | "muted" {
  if (["closed", "done", "completed", "passed", "covered", "waived", "shipped"].includes(status ?? "")) return "ok";
  if (["blocked", "failed", "rejected", "uncovered"].includes(status ?? "")) return "err";
  if (["running", "in_progress", "active", "dispatched", "review", "test", "judge", "in_flight"].includes(status ?? "")) return "info";
  if (["open", "pending", "waiting", "stale", "unverified", "unknown", "not_started"].includes(status ?? "")) return "warn";
  return "muted";
}
