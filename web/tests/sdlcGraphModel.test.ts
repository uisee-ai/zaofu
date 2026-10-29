const test = (_name: string, run: () => void) => run();
const assert = {
  equal: (a: unknown, b: unknown) => { if (a !== b) throw new Error(`${a} !== ${b}`); },
  ok: (value: unknown) => { if (!value) throw new Error("Expected truthy"); },
  deepEqual: (a: Map<unknown, unknown> | Set<unknown>, b: Map<unknown, unknown> | Set<unknown>) => {
    if (JSON.stringify([...a]) !== JSON.stringify([...b])) throw new Error("Collections differ");
  },
};
import { workflowSummaryEdges, workflowSummaryIds, workflowSummaryPositions, expandedNodeIds, GraphLayoutCache, graphPositions, relatedNodes, type SdlcNode, type SdlcEdge } from "../src/components/workflow/sdlcGraphModel.js";

const nodes: SdlcNode[] = ["A", "B", "C"].map(id => ({ id, kind: "task", label: id, status: "pending", views: ["modules"], data: {} }));
const edges: SdlcEdge[] = ["A", "B"].map(id => ({ id, source: id, target: "C", relation: "depends_on", views: ["modules"] }));
test("shared dependency retains one node and deterministic layout across refresh", () => {
  const first = graphPositions(nodes, edges);
  assert.equal(first.size, 3);
  assert.equal(first.get("A")?.y, first.get("B")?.y);
  assert.ok(first.get("C")!.y > first.get("A")!.y);
  assert.deepEqual(first, graphPositions([...nodes].reverse(), [...edges].reverse()));
});
test("status refresh reuses layout while topology and visible membership invalidate it", () => {
  const cache = new GraphLayoutCache();
  const first = cache.get(nodes, edges);
  const changed = nodes.map(n => ({ ...n, status: "running", label: "updated", data: { detail: "new" } }));
  assert.equal(cache.get(changed, [...edges].reverse()), first);
  const next = cache.get(changed, edges.slice(1));
  assert.ok(next !== first);
  assert.deepEqual(next, graphPositions(changed, edges.slice(1)));
  const filtered = cache.get(changed.slice(1), edges);
  assert.equal(filtered.has("A"), false);
  assert.deepEqual(filtered, graphPositions(changed.slice(1), edges));
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
  assert.deepEqual(workflowSummaryIds(all), new Set(["workflow:stage:plan"]));
  assert.equal(all.length, 3);
  assert.equal(workflowSummaryIds(all.slice(1)).size, 2);
});

test("folded edges follow declared workflow paths through branches and cycles only", () => {
  const ids = ["workflow:stage:A", "workflow:stage:B", "workflow:stage:C", "workflow:stage:D", "workflow:role:X", "workflow:role:Y", "operation:Z"];
  const graph: SdlcNode[] = ids.map(id => ({ ...nodes[0], id, kind: id.startsWith("operation:") ? "operation" : "workflow", views: ["workflow"] }));
  const links: SdlcEdge[] = [[0, 4], [4, 1], [4, 5], [5, 2], [5, 4], [0, 6], [6, 3]].map(([a, b], i) => ({ id: `edge-${i}`, source: ids[a], target: ids[b], relation: "declared", views: ["workflow"] }));
  const result = workflowSummaryEdges(graph, links, workflowSummaryIds(graph));
  assert.deepEqual(new Set(result.map(e => `${e.source}->${e.target}`)), new Set([`${ids[0]}->${ids[1]}`, `${ids[0]}->${ids[2]}`]));
  assert.ok(result.every(e => (e.collapsedVia?.length || 0) > 0));
  const direct = { id: "direct", source: ids[0], target: ids[1], relation: "declared", views: ["workflow"] as const };
  const merged = workflowSummaryEdges(graph, [...links, { ...direct, views: [...direct.views] }], workflowSummaryIds(graph));
  assert.equal(merged.filter(e => e.source === ids[0] && e.target === ids[1]).length, 1);
  assert.equal(merged.find(e => e.target === ids[1])?.id, "direct");
});

test("summary puts the connected stage path left to right and keeps unrelated stages separate", () => {
  const stages = ["scan", "plan", "impl", "final", "done", "unlinked"].map(id => ({ ...nodes[0], id, label: id, kind: "workflow" }));
  const links = [{ ...edges[0], source: "scan", target: "plan" }, { ...edges[1], source: "plan", target: "impl" }, { ...edges[0], id: "last", source: "final", target: "done" }];
  const layout = workflowSummaryPositions(stages, links);
  assert.ok(layout.get("scan")!.x < layout.get("plan")!.x);
  assert.ok(layout.get("plan")!.x < layout.get("impl")!.x);
  assert.ok(layout.get("unlinked")!.y > layout.get("scan")!.y);
  assert.ok(layout.get("final")!.x < layout.get("done")!.x);
  assert.equal(layout.get("final")!.y, layout.get("done")!.y);
  assert.ok(layout.get("unlinked")!.y > layout.get("final")!.y);
  assert.deepEqual(layout, workflowSummaryPositions([...stages].reverse(), [...links].reverse()));
});
