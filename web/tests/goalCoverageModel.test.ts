import type { GoalCoverageGraph } from "../src/api/types";
import {
  filterClaims,
  preferredClaimId,
  statusTone,
  taskNodesById,
} from "../src/components/goal-coverage/goalCoverageModel.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function equal(actual: unknown, expected: unknown, message: string): void {
  assert(actual === expected, `${message}: expected ${String(expected)}, got ${String(actual)}`);
}

function deepEqual(actual: unknown, expected: unknown, message: string): void {
  equal(JSON.stringify(actual), JSON.stringify(expected), message);
}

const graph: GoalCoverageGraph = {
  schema_version: "goal-coverage-graph.v1",
  coverage_mode: "explicit",
  identity: { goal_id: "GOAL-1", task_map_generation: "GEN-2" },
  currentness: { is_current_generation: true },
  summary: {
    mandatory_claims: 2,
    planned_claims: 1,
    claims_with_current_results: 1,
    closed_claims: 1,
    open_gaps: 1,
  },
  nodes: [
    { node_id: "goal:GOAL-1", kind: "goal", title: "Ship auth" },
    {
      node_id: "claim:CLAIM-A", kind: "goal_claim", goal_claim_id: "CLAIM-A",
      title: "Authentication is safe", plan_coverage: "covered", execution: "done",
      task_verification: "passed", closure: "closed", task_ids: ["TASK-A"],
    },
    {
      node_id: "claim:CLAIM-B", kind: "goal_claim", goal_claim_id: "CLAIM-B",
      title: "Replay is deterministic", plan_coverage: "uncovered", execution: "pending",
      task_verification: "unverified", closure: "open", task_ids: [],
    },
    { node_id: "task:TASK-A", kind: "task", task_id: "TASK-A", title: "Implement auth" },
  ],
  edges: [],
  diagnostics: [],
};

equal(preferredClaimId(graph, ""), "CLAIM-B", "uncovered claim is selected first");
equal(preferredClaimId(graph, "CLAIM-A"), "CLAIM-A", "current selection is stable");
deepEqual(filterClaims(graph, "implement auth").map((claim) => claim.goal_claim_id), ["CLAIM-A"], "task search");
deepEqual(filterClaims(graph, "replay").map((claim) => claim.goal_claim_id), ["CLAIM-B"], "claim search");
equal(taskNodesById(graph).get("TASK-A")?.title, "Implement auth", "task lookup");
equal(statusTone("closed"), "ok", "closed tone");
equal(statusTone("uncovered"), "err", "uncovered tone");
equal(statusTone("stale"), "warn", "stale tone");
equal(statusTone("in_progress"), "info", "in progress tone");

console.log("goalCoverageModel tests passed");
