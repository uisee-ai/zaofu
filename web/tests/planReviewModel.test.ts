const assert = { equal(a: unknown, b: unknown) { if (a !== b) throw new Error(`${a} !== ${b}`); }, doesNotThrow(fn: () => unknown) { fn(); } };
import { buildPlanReview, planArtifactUrl, resolvePlanPreviewItem } from "../src/components/delivery-trace/planReviewModel.js";
import type { OperatorInboxItem } from "../src/api/types";
const task = { task_id: "T1", title: "调整库存", source_ref: "spec.md#stock", acceptance_criteria: [{ id: "AC1", statement: "只扣差额", verification_command_ids: ["VC1"] }] };
const legacy = buildPlanReview({ task_map: { tasks: [task], owner_decision_items: [{ decision_id: "Q1", affected_task_ids: ["T1"], status: "pending" }] }, source_inventory: { pages: [{ id: "A01", task_id: "T1" }], forms: [] } });
assert.equal(legacy.modules[0].specification, "");
assert.equal(legacy.modules[0].scenarios.length, 0);
assert.equal(legacy.modules[0].criteria[0].statement, "只扣差额");
assert.equal(legacy.modules[0].decisions.length, 1);
assert.equal(legacy.pageCount, 1);
assert.equal(legacy.formCount, 0);
assert.equal(buildPlanReview({}).pageCount, null);
for (const malformed of [null, "invalid", [], { tasks: [null, 42, []] }]) assert.doesNotThrow(() => buildPlanReview({ task_map: malformed }));
const rich = { shared: { human_review: { overview_markdown: "目标", journeys_markdown: "流程" } }, tasks: [{ task_id: "T1", body: { human_review: { module_spec_markdown: "业务规格", scenarios: [{ id: "S1", given: "库存10，原消耗3", when: "目标5", then: "库存8", acceptance_ids: ["AC1"], command_ids: ["VC1"], source_refs: ["spec.md#stock"] }, { id: "S2", acceptance_ids: ["AC-OTHER"] }] } } }] };
const result = buildPlanReview({ task_map: { tasks: [task] }, execution_spec: rich });
assert.equal(result.overview, "目标");
assert.equal(result.modules[0].scenarios[0].issues.length, 0);
assert.equal(result.modules[0].scenarios[1].issues.length, 4);
const duplicate = buildPlanReview({ task_map: { tasks: [task] }, execution_spec: { tasks: [...rich.tasks, ...rich.tasks, { task_id: "foreign" }] } });
assert.equal(duplicate.modules[0].specification, "");
assert.equal(duplicate.warnings.length, 2);
assert.equal(planArtifactUrl("p/q", "a?b", "task_map"), "/api/projects/p%2Fq/plans/a%3Fb/artifacts/task_map");
// A large Plan must not inherit the old summary's first-50-task truncation.
assert.equal(buildPlanReview({ task_map: { tasks: Array.from({ length: 80 }, (_, i) => ({ ...task, task_id: `T${i}` })) } }).modules.length, 80);

const preview = { plan_id: "plan-owner" };
const ownerItem = { id: "approval-owner", plan_id: "plan-owner", owner_route: "plan_synth_owner_decision", proposal_ref: "proposal.json", eval_ref: "evaluation.json" } as OperatorInboxItem;
const unrelated = { ...ownerItem, plan_id: "other-plan" };
// A deep link opens before Inbox finishes loading; later data must supply its identity.
assert.equal(resolvePlanPreviewItem(preview, [], unrelated), null);
assert.equal(resolvePlanPreviewItem(preview, [unrelated, ownerItem]), ownerItem);
assert.equal(resolvePlanPreviewItem(null, [ownerItem], ownerItem), null);
const refreshed = { ...ownerItem, status: "resolved" };
assert.equal(resolvePlanPreviewItem(preview, [refreshed], ownerItem), refreshed);

// Production-shaped plural bindings and author aliases remain readable.
const production = buildPlanReview({
  task_map: { tasks: [task], owner_decision_items: [{ decision_id: "Q1", affected_task_ids: ["T1"] }] },
  source_inventory: { pages: [{ id: "P1", task_ids: ["T1", "T2"] }], forms: [{ id: "F1", task_ids: ["T1"] }] },
  source_index: { traceability: [{ source_id: "SRC1", source_ref: "spec.md#stock" }] },
  test_matrix: { formal_acceptance_scenarios: [{ id: "E01", task_id: "T1", acceptance_id: "AC1", trigger: "重试", expected: "不重复" }] },
  execution_spec: { tasks: [{ task_id: "T1", body: { human_review: { scenarios: [{ name: "只扣一次", given: "库存10", when: "重试", then: "库存8", acceptance_ids: ["AC1"], command_ids: ["VC1"], source_ids: ["SRC1"] }] } } }] },
});
assert.equal(production.modules[0].pages.length, 1);
assert.equal(production.modules[0].forms.length, 1);
assert.equal(production.modules[0].scenarios[0].title, "只扣一次");
assert.equal(production.modules[0].scenarios[0].sourceRefs[0], "spec.md#stock");
assert.equal(production.modules[0].scenarios[0].issues.length, 0);
assert.equal(production.modules[0].formalScenarios[0].id, "E01");
