const test = (_name: string, run: () => void) => run();
const assert = {
  equal: (a: unknown, b: unknown) => { if (a !== b) throw new Error(`${a} !== ${b}`); },
  ok: (value: unknown) => { if (!value) throw new Error("Expected truthy"); },
  deepEqual: (a: Map<unknown, unknown> | Set<unknown>, b: Map<unknown, unknown> | Set<unknown>) => {
    if (JSON.stringify([...a]) !== JSON.stringify([...b])) throw new Error("Collections differ");
  },
};
import { workflowSummaryIds, expandedNodeIds, graphPositions, relatedNodes, type SdlcNode, type SdlcEdge } from "../src/components/workflow/sdlcGraphModel.js";

const nodes: SdlcNode[] = ["A", "B", "C"].map(id => ({ id, kind: "task", label: id, status: "pending", views: ["modules"], data: {} }));
const edges: SdlcEdge[] = ["A", "B"].map(id => ({ id, source: id, target: "C", relation: "depends_on", views: ["modules"] }));
test("shared dependency retains one node and deterministic layout across refresh", () => {
  const first = graphPositions(nodes, edges);
  assert.equal(first.size, 3);
  assert.equal(first.get("A")?.y, first.get("B")?.y);
  assert.ok(first.get("C")!.y > first.get("A")!.y);
  assert.deepEqual(first, graphPositions([...nodes].reverse(), [...edges].reverse()));
});
test("bidirectional reachability follows declared edges and terminates on rework cycles", () => {
  assert.deepEqual(relatedNodes("C", edges, "upstream"), new Set(["C", "A", "B"]));
  assert.deepEqual(relatedNodes("A", edges, "downstream"), new Set(["A", "C"]));
  const cyclic = [...edges, { ...edges[0], id: "retry", source: "C", target: "A", relation: "rework" }];
  assert.equal(graphPositions(nodes, cyclic).size, 3);
  assert.deepEqual(relatedNodes("A", cyclic, "downstream"), new Set(["A", "C"]));
});

test("collapse hides contained descendants while shared tasks remain reachable", () => {
  const contains = edges.map(e => ({ ...e, relation: "contains" }));
  assert.deepEqual(expandedNodeIds(nodes, contains, new Set(["A"])), new Set(["A", "B", "C"]));
  assert.deepEqual(expandedNodeIds(nodes, contains, new Set(["A", "B"])), new Set(["A", "B"]));
});
test("large cross-module DAG retains every identity and endpoint", () => {
  const many = Array.from({ length: 1200 }, (_, i) => ({ ...nodes[0], id: `T${i}` }));
  const relations = many.slice(1).map((n, i) => ({ ...edges[0], id: `E${i}`, source: many[Math.floor(i / 3)].id, target: n.id }));
  assert.equal(graphPositions(many, relations).size, many.length);
  assert.equal(relatedNodes("T0", relations, "downstream").size, many.length);
});


test("stage summary hides compiler details without deleting the full graph", () => {
  const all: SdlcNode[] = [
    { ...nodes[0], id: "workflow:stage:plan", kind: "workflow", data: { definition: { type: "fanout_stage" } } },
    { ...nodes[0], id: "workflow:role:planner", kind: "workflow", data: { definition: { type: "role_stage" } } },
    { ...nodes[0], id: "operation:planner", kind: "operation" },
  ];
  assert.deepEqual(workflowSummaryIds(all), new Set(["workflow:stage:plan", "operation:planner"]));
  assert.equal(all.length, 3);
  assert.equal(workflowSummaryIds(all.slice(1)).size, 2);
});
