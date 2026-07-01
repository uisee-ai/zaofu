import { layoutLoopModel } from "../src/components/delivery-trace/BehaviorLoopLayout.js";
import { buildMeasureGraphModel, measureGraphLabels } from "../src/components/delivery-trace/BehaviorMeasureGraphModel.js";
import { buildLoopModel, nodeLabel } from "../src/components/delivery-trace/BehaviorLoopModel.js";
import type { LoopNode } from "../src/components/delivery-trace/BehaviorLoopModel.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

const spans = Array.from({ length: 6 }, (_, index) => ({
  trace_id: "trace-1",
  span_id: `span-${index}`,
  task_id: `TASK-${index}`,
  status: index === 2 ? "failed" : "completed",
  raw_event_refs: [`event-${index}`],
  name: `worker ${index}`,
}));

const thick = {
  schema_version: "delivery-thick-trace.v1",
  generated_at: "2026-06-17T00:00:00+00:00",
  target: { id: "F-1" },
  graph: { node_count: 0, edge_count: 0, layers: [], nodes: [], edges: [] },
  spans,
  span_count: spans.length,
  behaviors: [],
  evals: [],
  artifacts: [],
  improvement_candidates: [],
  diagnostics: [],
  related_loop_ids: ["loop-1"],
};

const projection = {
  schema_version: "loop.v1",
  generated_at: "2026-06-17T00:00:00+00:00",
  summary: {
    total: 1,
    open: 1,
    verifying: 0,
    recovered: 0,
    exhausted: 0,
    behavior_count: 1,
    eval_count: 0,
    candidate_count: 0,
    by_kind: {},
  },
  loops: [{ loop_id: "loop-1", status: "open", kind: "dispatch", feature_ids: ["F-1"], task_ids: ["TASK-2"] }],
  behaviors: [{
    behavior_id: "behavior-1",
    loop_id: "loop-1",
    kind: "worker_idle_ready",
    status: "failed",
    task_ids: ["TASK-2"],
    event_ids: ["event-2"],
    summary: "ready task had no worker",
  }],
  evals: [],
  diagnoses: [],
  candidates: [],
  actions: [],
  verifications: [],
  learning: [],
};

const model = buildLoopModel(thick, projection, "F-1");
const graphTraces = model.nodes.filter((node) => node.kind === "trace");
const behavior = model.nodes.find((node) => node.kind === "behavior");
const aggregate = graphTraces[0];

assert(model.traceNodes.length === 6, `raw trace feed keeps 6 nodes, got ${model.traceNodes.length}`);
assert(graphTraces.length === 1, `graph folds traces into 1 node, got ${graphTraces.length}`);
assert(aggregate?.id === "trace:aggregate", `aggregate id mismatch: ${aggregate?.id}`);
assert(aggregate.eventIds.includes("event-0") && aggregate.eventIds.includes("event-5"), "aggregate keeps event refs");
assert(aggregate.taskIds.includes("TASK-0") && aggregate.taskIds.includes("TASK-5"), "aggregate keeps task refs");
assert(model.edges.some((edge) => edge.from === "trace:aggregate" && edge.to === behavior?.id), "aggregate connects to behavior");
assert(nodeLabel(aggregate) === "Trace", "trace label is semantic");
assert(behavior && nodeLabel(behavior) === "Signal", "behavior label is semantic");

function measureProjection(activeLens: string, labels: string[], layout = "ring") {
  const stages = labels.map((label) => ({
    id: label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""),
    label,
    value: "1",
    detail: `${label} detail`,
    tone: "info",
  }));
  return {
    schema_version: "measure-loop.v1",
    generated_at: "2026-06-17T00:00:00+00:00",
    active_lens: activeLens,
    lenses: [],
    summary: {},
    metrics: [],
    stages,
    graph: {
      layout_hint: layout,
      node_count: stages.length,
      edge_count: Math.max(0, stages.length - 1),
      nodes: stages.map((stage) => ({ id: stage.id, kind: "stage", label: stage.label, status: stage.tone, value: stage.value })),
      edges: stages.slice(1).map((stage, index) => ({
        from: stages[index]!.id,
        to: stage.id,
        kind: "next",
        status: "projection",
      })),
    },
    feed: [],
  };
}

const allLabels = measureGraphLabels(measureProjection("all", ["Plan", "Dispatch", "Work", "Verify", "Rework/Ship"]));
assert(allLabels.includes("Plan") && allLabels.includes("Dispatch") && allLabels.includes("Work"), "all lens graph labels delivery stages");
const agentLabels = measureGraphLabels(measureProjection("agent", ["Briefing", "Work", "Heartbeat", "Complete/Emit"], "star"));
assert(agentLabels.includes("Briefing") && agentLabels.includes("Heartbeat"), "agent lens graph labels agent lifecycle");
const eventLabels = measureGraphLabels(measureProjection("event_driven", ["Event Ingest", "Reactor", "Decision", "Dispatch", "Worker Ack"], "dag"));
assert(eventLabels.includes("Ingest") && eventLabels.includes("Ack"), "event-driven lens graph labels event lifecycle");
const eventRing = buildMeasureGraphModel(measureProjection("event_driven", ["Event Ingest", "Reactor", "Decision", "Dispatch", "Worker Ack"], "dag"));
const eventRingNodes = layoutLoopModel(eventRing!, "ring").model.nodes;
assert(!overlaps(eventRingNodes, "Reactor", "Decision"), "forced ring keeps Reactor and Decision separated");
assert(!overlaps(eventRingNodes, "Reactor", "Ack"), "forced ring keeps Reactor and Ack separated");
assert(!overlaps(eventRingNodes, "Decision", "Ack"), "forced ring keeps Decision and Ack separated");
const hillGraph = buildMeasureGraphModel(measureProjection("hill_climbing", ["Failure Trace", "Pattern", "Diagnosis", "Proposal", "Verified Improvement"]));
assert(hillGraph?.nodes.some((node) => node.displayLabel === "Verified"), "hill-climbing lens compacts verified improvement label");
assert(hillGraph?.edges.some((edge) => edge.from && edge.to), "measure graph adapter keeps edges");

console.log("behaviorLoopModel.test.ts OK");

function overlaps(nodes: LoopNode[], leftLabel: string, rightLabel: string): boolean {
  const left = nodes.find((node) => (node.displayLabel || node.label) === leftLabel);
  const right = nodes.find((node) => (node.displayLabel || node.label) === rightLabel);
  assert(left && right, `missing overlap nodes ${leftLabel}/${rightLabel}`);
  return boxOverlaps(bounds(left!), bounds(right!));
}

function bounds(node: LoopNode): { left: number; top: number; right: number; bottom: number } {
  const x = (node.x / 100) * 531;
  const y = (node.y / 100) * 570;
  return {
    left: x - node.size / 2,
    top: y - node.size / 2,
    right: x + node.size / 2,
    bottom: y + node.size / 2,
  };
}

function boxOverlaps(
  left: { left: number; top: number; right: number; bottom: number },
  right: { left: number; top: number; right: number; bottom: number },
): boolean {
  return !(left.right <= right.left || right.right <= left.left || left.bottom <= right.top || right.bottom <= left.top);
}
