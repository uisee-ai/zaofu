import type { DeliveryTrace } from "../src/api/types";
import {
  buildDeliveryWorkGraph,
  workClaimNodeId,
  workGoalNodeId,
  workTaskNodeId,
} from "../src/components/delivery-trace/deliveryWorkGraphModel.js";
import { buildDeliveryWorkModel } from "../src/components/delivery-trace/deliveryWorkModel.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function equal(actual: unknown, expected: unknown, message: string): void {
  assert(actual === expected, `${message}: expected ${String(expected)}, got ${String(actual)}`);
}

const trace = {
  feature_id: "GOAL-1",
  status: "in_progress",
  execution_graph: {
    nodes: [
      { task_id: "TASK-SHARED", title: "Shared", planned: { blocked_by: [] }, actual: { status: "done", assigned_to: "impl-1" }, drift: [] },
      { task_id: "TASK-B", title: "Replay", planned: { blocked_by: ["TASK-SHARED"] }, actual: { status: "running", assigned_to: "impl-2" }, drift: [] },
      { task_id: "TASK-UNMAPPED", title: "Cleanup", planned: { blocked_by: [] }, actual: { status: "pending" }, drift: [] },
    ],
    edges: [],
    waves: [],
  },
  goal_coverage_graph: {
    schema_version: "goal-coverage-graph.v1",
    coverage_mode: "explicit",
    identity: { goal_id: "GOAL-1" },
    currentness: { is_current_generation: true },
    summary: { mandatory_claims: 2, planned_claims: 2, claims_with_current_results: 1, closed_claims: 1, open_gaps: 0 },
    nodes: [
      { node_id: "goal:GOAL-1", kind: "goal", goal_id: "GOAL-1", title: "Ship boundary", status: "in_progress" },
      { node_id: "claim:CLAIM-A", kind: "goal_claim", goal_claim_id: "CLAIM-A", title: "Auth safe", plan_coverage: "covered", execution: "done", task_verification: "passed", task_ids: ["TASK-SHARED"] },
      { node_id: "claim:CLAIM-B", kind: "goal_claim", goal_claim_id: "CLAIM-B", title: "Replay safe", plan_coverage: "covered", execution: "running", task_verification: "unverified", task_ids: ["TASK-SHARED", "TASK-B"] },
      { node_id: "task:TASK-SHARED", kind: "task", task_id: "TASK-SHARED", title: "Shared", status: "done", goal_claim_ids: ["CLAIM-A", "CLAIM-B"] },
      { node_id: "task:TASK-B", kind: "task", task_id: "TASK-B", title: "Replay", status: "running", goal_claim_ids: ["CLAIM-B"] },
      { node_id: "result:shared", kind: "verification_result", task_id: "TASK-SHARED", title: "Verified", status: "passed", current: true },
    ],
    edges: [],
    diagnostics: [],
  },
} as unknown as DeliveryTrace;

const model = buildDeliveryWorkModel(trace);
const graph = buildDeliveryWorkGraph(model);
const graphAgain = buildDeliveryWorkGraph(model);
const goalId = workGoalNodeId(model);

equal(graph.nodes.length, 7, "goal, claims, canonical tasks, and unmapped hub are visible");
equal(new Set(graph.nodes.map((node) => node.id)).size, graph.nodes.length, "canonical node ids are unique");
equal(graph.edges.filter((edge) => edge.kind === "tree").length, 6, "visible nodes form one tree spine");
equal(graph.edges.filter((edge) => edge.kind === "secondary").length, 1, "multi-claim task gets one secondary edge");
assert(graph.edges.some((edge) => (
  edge.kind === "secondary"
  && edge.source === workClaimNodeId("CLAIM-B")
  && edge.target === workTaskNodeId("TASK-SHARED")
)), "secondary edge links the additional claim to the canonical task");
equal(
  JSON.stringify(graph.nodes.map((node) => [node.id, node.position])),
  JSON.stringify(graphAgain.nodes.map((node) => [node.id, node.position])),
  "layout positions are deterministic",
);

const collapsedClaim = buildDeliveryWorkGraph(model, new Set([workClaimNodeId("CLAIM-A")]));
assert(!collapsedClaim.nodes.some((node) => node.id === workTaskNodeId("TASK-SHARED")), "collapsed claim hides its primary tasks");
assert(!collapsedClaim.edges.some((edge) => edge.kind === "secondary"), "cross-link hides when its canonical task is hidden");

const collapsedGoal = buildDeliveryWorkGraph(model, new Set([goalId]));
equal(collapsedGoal.nodes.length, 1, "collapsed goal hides the full delivery subtree");
equal(collapsedGoal.nodes[0]?.id, goalId, "collapsed graph keeps the goal hub");

console.log("deliveryWorkGraphModel tests passed");
