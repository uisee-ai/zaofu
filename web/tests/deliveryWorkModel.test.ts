import type { DeliveryTrace } from "../src/api/types";
import {
  buildDeliveryWorkModel,
  latestTry,
  resultStatus,
} from "../src/components/delivery-trace/deliveryWorkModel.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function equal(actual: unknown, expected: unknown, message: string): void {
  assert(actual === expected, `${message}: expected ${String(expected)}, got ${String(actual)}`);
}

function deepEqual(actual: unknown, expected: unknown, message: string): void {
  equal(JSON.stringify(actual), JSON.stringify(expected), message);
}

const trace = {
  feature_id: "GOAL-1",
  status: "in_progress",
  execution_graph: {
    nodes: [
      {
        task_id: "TASK-SHARED",
        title: "Implement shared boundary",
        planned: { blocked_by: [] },
        actual: { status: "done", assigned_to: "impl-1", evidence_events: ["evt-shared"] },
        drift: [],
      },
      {
        task_id: "TASK-B",
        title: "Implement replay",
        planned: { blocked_by: ["TASK-SHARED"] },
        actual: { status: "in_progress", assigned_to: "impl-2", evidence_events: [] },
        drift: [],
      },
      {
        task_id: "TASK-UNMAPPED",
        title: "Legacy cleanup",
        planned: { blocked_by: [] },
        actual: { status: "pending", assigned_to: "impl-3", evidence_events: [] },
        drift: [],
      },
    ],
    edges: [],
    waves: [],
  },
  task_lifecycle: {
    schema_version: "task-lifecycle.v1",
    tasks: {
      "TASK-SHARED": {
        state_history: [],
        tries: [
          { try: 1, outcome: "failed", dispatch_id: "d1", gate_results: [{ type: "verify", passed: false }] },
          { try: 2, outcome: "done", dispatch_id: "d2", gate_results: [{ type: "verify", passed: true }] },
        ],
      },
    },
  },
  goal_coverage_graph: {
    schema_version: "goal-coverage-graph.v1",
    coverage_mode: "explicit",
    identity: { goal_id: "GOAL-1", task_map_generation: "GEN-1" },
    currentness: { is_current_generation: true },
    summary: {
      mandatory_claims: 2,
      planned_claims: 2,
      claims_with_current_results: 1,
      closed_claims: 1,
      open_gaps: 1,
    },
    nodes: [
      { node_id: "goal:GOAL-1", kind: "goal", title: "Ship shared boundary", status: "rejected" },
      {
        node_id: "claim:CLAIM-A", kind: "goal_claim", goal_claim_id: "CLAIM-A",
        title: "Authorization is safe", plan_coverage: "covered", execution: "done",
        task_verification: "passed", closure: "closed", task_ids: ["TASK-SHARED"],
      },
      {
        node_id: "claim:CLAIM-B", kind: "goal_claim", goal_claim_id: "CLAIM-B",
        title: "Replay is deterministic", plan_coverage: "covered", execution: "running",
        task_verification: "unverified", closure: "open", task_ids: ["TASK-SHARED", "TASK-B"],
      },
      {
        node_id: "task:TASK-SHARED", kind: "task", task_id: "TASK-SHARED",
        title: "Implement shared boundary", status: "done", goal_claim_ids: ["CLAIM-A", "CLAIM-B"],
      },
      {
        node_id: "task:TASK-B", kind: "task", task_id: "TASK-B",
        title: "Implement replay", status: "in_progress", goal_claim_ids: ["CLAIM-B"],
      },
      {
        node_id: "result:shared", kind: "verification_result", task_id: "TASK-SHARED",
        title: "Shared boundary verified", status: "passed", result_ref: "artifact://shared",
        evidence_refs: ["artifact://proof"], current: true,
      },
    ],
    edges: [],
    diagnostics: [],
  },
} as unknown as DeliveryTrace;

const model = buildDeliveryWorkModel(trace);
const claimA = model.claims.find((claim) => claim.claim.goal_claim_id === "CLAIM-A")!;
const claimB = model.claims.find((claim) => claim.claim.goal_claim_id === "CLAIM-B")!;
const shared = model.tasks.find((task) => task.taskId === "TASK-SHARED")!;

equal(model.tasks.length, 3, "all canonical execution tasks are retained");
deepEqual(claimA.tasks.map((task) => task.taskId), ["TASK-SHARED"], "shared task has one primary claim");
deepEqual(claimB.tasks.map((task) => task.taskId), ["TASK-B"], "second claim owns its primary task");
deepEqual(claimB.linkedTasks.map((task) => task.taskId), ["TASK-SHARED"], "shared task is a reference under another claim");
equal(model.claims.flatMap((claim) => claim.tasks).filter((task) => task.taskId === "TASK-SHARED").length, 1, "shared task renders canonically once");
deepEqual(model.unclaimedTasks.map((task) => task.taskId), ["TASK-UNMAPPED"], "unmapped execution work stays visible");
equal(latestTry(shared)?.try, 2, "latest try is selected by lifecycle order");
equal(resultStatus(shared), "passed", "current verification result drives task status");
deepEqual(shared.evidenceRefs, ["evt-shared", "artifact://proof"], "evidence references are deduplicated");
equal(model.summary.done, 1, "done tasks are summarized");
equal(model.summary.running, 1, "running tasks are summarized");
equal(model.summary.verified, 1, "current passed results are summarized");

console.log("deliveryWorkModel tests passed");
