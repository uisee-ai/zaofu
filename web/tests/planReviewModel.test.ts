const assert = { equal(a: unknown, b: unknown) { if (a !== b) throw new Error(`${a} !== ${b}`); }, doesNotThrow(fn: () => unknown) { fn(); } };
import { buildPlanReview, planArtifactUrl, resolvePlanPreviewItem } from "../src/components/delivery-trace/planReviewModel.js";
import type { OperatorInboxItem } from "../src/api/types";
import live from "./fixtures/prd205/live-plan.json" with { type: "json" };
import { planReadingSections, planTaskSpecifications } from "../src/components/delivery-trace/planReadingSections.js";
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

// Real v5 output: no execution_spec, tests live under test_matrix.tests.
const liveReview = buildPlanReview(live);
const reading = liveReview.modules.find(m => m.id === "RL-READING")!;
assert.equal(reading.scenarios.length, 21);
assert.equal(liveReview.modules.find(m => m.id === "RL-SCAFFOLD")!.scenarios.length, 0);
assert.equal(reading.scenarios[0].id, "ADD-TRIM");
assert.equal(reading.scenarios[0].evidenceStatus, "NOT_RUN");
assert.equal(reading.scenarios[0].commandIds.join(","), "C-UI,C-CANDIDATE");
assert.equal(reading.scenarios[0].sourceRefs[0], "requirement.md#已确认行为与验收");
assert.equal(reading.scenarios[0].issues.length, 0);
const excerpts = planReadingSections(live.markdown);
assert.equal(excerpts.decisions.length, 1);
assert.equal(excerpts.decisions[0].content.includes("D-3 / pending_approval"), true);
assert.equal(excerpts.pages[0].content.includes("flowchart TD"), true);
assert.equal(excerpts.overview.some(s => s.content.includes("PRD：价值")), true);
// Headings in code fences are not interpreted as authored document sections.
assert.equal(planReadingSections("# Plan\n```md\n## Decision Register\nnot a register\n```").decisions.length, 0);
for (const title of ["权威、范围与当前状态", "权威、范围与审批入口"]) {
  const authored = planReadingSections(`# 中文 Plan\n\n## ${title}\n仅交付三个文件，审批后实施。\n\n## PRD 用户结果\n原始字节不变。\n\n## Task Spec: T1\n本片说明。`);
  assert.equal(authored.overview[0].content.includes("仅交付三个文件，审批后实施。"), true);
  assert.equal(authored.overview[0].content.includes("本片说明。"), false);
  assert.equal(authored.journeys[0].content.includes("原始字节不变。"), true);
}
const ownership = buildPlanReview({ task_map: { tasks: [task, { ...task, task_id: "T2" }] }, test_matrix: {
  tests: [{ id: "only-T2", task_id: "T2", acceptance_ids: ["AC1"], given: "A", when: "B", then: "C" }],
  formal_acceptance_scenarios: [{ id: "formal-T2", task_id: "T2", acceptance_id: "AC1" }],
} });
assert.equal(ownership.modules[0].scenarios.length, 0);
assert.equal(ownership.modules[0].formalScenarios.length, 0);
assert.equal(ownership.modules[1].scenarios.length, 1);

// Exact Task IDs and heading boundaries keep specification excerpts isolated.
const scoped = planTaskSpecifications("# Plan\n\n## Shared Spec\n共同快照协议\n\n## Task Spec: T1\n本片新增\n### 失败不变项\n保留表单\n```md\n## Task Spec: T2\n仅代码示例\n```\n## Task Spec: T10\n另一任务正文\n## Task Spec: UNKNOWN\n未知正文\n", ["T1", "T10"]);
assert.equal(scoped.shared[0].content.includes("共同快照协议"), true);
assert.equal(scoped.tasks.T1.content.includes("保留表单"), true);
assert.equal(scoped.tasks.T1.content.includes("另一任务正文"), false);
assert.equal(scoped.tasks.T10.content.includes("未知正文"), false);
assert.equal(scoped.tasks.UNKNOWN, undefined);
assert.equal(scoped.tasks.T2, undefined);
assert.equal(scoped.warnings.length, 1);
const duplicateSpecs = planTaskSpecifications("## Task Spec: T1\n第一版\n## Task Spec: T1\n第二版\n", ["T1"]);
assert.equal(duplicateSpecs.tasks.T1, undefined);
assert.equal(duplicateSpecs.warnings.length, 1);
assert.equal(Object.keys(planTaskSpecifications("## Task Spec: T1\n正文", []).tasks).length, 0);
// Real Kanban candidate 360268d3 authored explicit IDs without a colon.
// Punctuation aliases must preserve the same exact-ID and ambiguity boundaries.
const punctuationSpecs = "## Shared Spec：同版共享合同\n共享准备清理。\n## Task Spec T-SAVE\n功能入口。\n### 交接\n保留草稿。\n## Task Spec：T-ENTRY\n正式入口。\n## Task Spec T-SAVE-OTHER\n未知任务。\n";
const punctuation = planTaskSpecifications(punctuationSpecs, ["T-SAVE", "T-ENTRY"]);
assert.equal(punctuation.tasks["T-SAVE"]?.content, "## Task Spec T-SAVE\n功能入口。\n### 交接\n保留草稿。\n");
assert.equal(punctuation.tasks["T-ENTRY"]?.content, "## Task Spec：T-ENTRY\n正式入口。\n");
assert.equal(punctuation.warnings.length, 1);
assert.equal(punctuation.unassigned.length, 0);
assert.equal(planReadingSections("## Task Spec T-SAVE\n正文").opening.length, 0);
const aliasCollision = planTaskSpecifications("## Task Spec: T1\n原文。\n## Task Spec T1\n另一处。\n", ["T1"]);
assert.equal(aliasCollision.tasks.T1, undefined);
assert.equal(aliasCollision.warnings.length, 1);
assert.equal(Object.keys(planTaskSpecifications("## Task Spec T1 extra\n正文\n## Task SpecT1\n正文\n```md\n## Task Spec T2\n示例\n```", ["T1", "T2"]).tasks).length, 0);
assert.equal(liveReview.modules.find(m => m.id === "RL-READING")!.owner, "dev-lane-1");
assert.equal(liveReview.modules.find(m => m.id === "RL-READING")!.allowedPaths.includes("app/"), true);
assert.equal(liveReview.modules.find(m => m.id === "RL-READING")!.commands.length, 3);

// Real R2 uses test_id/task_ids and source-index scene_ids; task ownership stays exact.
const namedCases = buildPlanReview({ task_map: { tasks: [task] },
  test_matrix: { tests: [{ test_id: "S-add", task_ids: ["T1"], acceptance_ids: ["AC1"], command_ids: ["VC1"], given: "空列表", when: "保存", then: "新增", evidence_status: "NOT_RUN" }] },
  source_index: { traceability: [
    { scene_ids: ["S-add"], task_ids: ["T1"], source_ref: "requirement.md#add" },
    { scene_ids: ["S-add"], task_ids: ["T2"], source_ref: "foreign.md" },
    { scene_ids: ["S-add-other"], task_ids: ["T1"], source_ref: "other.md" },
  ] },
});
assert.equal(namedCases.modules[0].scenarios[0].id, "S-add");
assert.equal(namedCases.modules[0].scenarios[0].sourceRefs.join(), "requirement.md#add");
assert.equal(namedCases.modules[0].scenarios[0].issues.length, 0);

// Selected, unmodified fields from the admitted R2 Plan reproduce the shared case.
import shared from "./fixtures/spec206-shared.json" with { type: "json" };
const sharedReview = buildPlanReview(shared);
const featureCase = sharedReview.modules.find(m => m.id === "T-2")!.scenarios[0];
assert.equal(featureCase.issues.includes("场景命令未绑定到本任务验收项"), false);
assert.equal(featureCase.issues.length, 0);
assert.equal(featureCase.responsibilities[0].commandIds.join(), "V-CORE,V-BROWSER");
assert.equal(featureCase.responsibilities[1].commandIds.join(), "V-PRODUCT");
assert.equal(featureCase.responsibilities[1].acceptanceIds.length, 0);
assert.equal(featureCase.responsibilities[1].commands[0].definitions[0].owner, "verify-lane-2");
const assemblyCase = sharedReview.modules.find(m => m.id === "T-3")!.scenarios[0];
assert.equal(assemblyCase.issues.some(s => s.startsWith("本片责任待核对")), true);
assert.equal(assemblyCase.evidenceStatus, "NOT_RUN");
assert.equal(assemblyCase.sourceRefs.some(s => s.endsWith("requirement.md")), true);
// Counterexamples are synthetic mutations, not attributed to the real author.
function mutateShared(change: (data: typeof shared) => void) {
  const data = structuredClone(shared); change(data);
  return buildPlanReview(data).modules.find(m => m.id === "T-2")!.scenarios[0];
}
assert.equal(mutateShared(data => data.test_matrix.tests[0].acceptance_ids.push("AC-UNKNOWN")).issues.some(s => s.startsWith("验收项引用缺失")), true);
assert.equal(mutateShared(data => data.test_matrix.tests[0].command_ids.push("V-UNKNOWN")).issues.includes("场景命令未绑定到参与任务验收项"), true);
assert.equal(mutateShared(data => data.test_matrix.tests[0].task_ids.push("T-MISSING")).issues.includes("场景任务未唯一解析：T-MISSING"), true);
assert.equal(mutateShared(data => data.task_map.tasks.push(data.task_map.tasks[1])).issues.includes("场景任务未唯一解析：T-2"), true);
assert.equal(mutateShared(data => data.task_map.tasks[1].validation.commands.push(data.task_map.tasks[1].validation.commands[0])).issues.includes("验证命令定义不唯一：T-2 / V-CORE"), true);
const undeclared = mutateShared(data => {
  data.test_matrix.tests[0].acceptance_ids = ["FOREIGN"];
  data.task_map.tasks[0].acceptance_criteria[0].id = "FOREIGN";
});
assert.equal(undeclared.issues.some(s => s.startsWith("验收项引用缺失")), true);
const missingDefinitions = mutateShared(data => { data.task_map.tasks[1].validation.commands = []; });
assert.equal(missingDefinitions.responsibilities[0].commands[0].definitions.length, 0);
import { decisionStatusDistribution } from "../src/components/delivery-trace/planReviewModel.js";
assert.equal(decisionStatusDistribution(shared.task_map.owner_decision_items), "deferred 1 · confirmed 4 · pending_validation 1 · proposed 1");
assert.equal(decisionStatusDistribution([{ status: "future-value" }, {}, { status: "future-value" }]), "future-value 2 · 未声明 1");

// Exact repeated sections may fold for reading, but every original byte survives.
const repeating = planTaskSpecifications('## Task Spec: A\n\n### 本片结果\nA 专属。\n\n### 完整决定\nD-sync 保持未来待决，不阻塞本期。\n\n## Task Spec: B\n\n### 本片结果\nB 专属。\n\n### 完整决定\nD-sync 保持未来待决，不阻塞本期。\n', ['A', 'B']);
assert.equal(repeating.reading.A.filter(s => s.shared).length, 1);
assert.equal(repeating.reading.A.map(s => s.content).join(''), repeating.tasks.A.content);
assert.equal(repeating.reading.B.map(s => s.content).join(''), repeating.tasks.B.content);
const different = planTaskSpecifications('## Task Spec: A\n### 决定\nD 未关闭\n## Task Spec: B\n### 决定\nD 已关闭\n', ['A', 'B']);
assert.equal(different.reading.A.some(s => s.shared), false);

// A sibling TDD is reachable without being silently assigned to every task.
const handoff = planTaskSpecifications("## Task Spec: T1\n本片。\n## TDD and evidence\n### 恢复\n必须恢复原权限。\n## Task Spec: T2\n别片。\n", ["T1", "T2"]);
assert.equal(handoff.tasks.T1.content.includes("必须恢复"), false);
assert.equal(handoff.unassigned.length, 1);
assert.equal(handoff.unassigned[0].content.includes("必须恢复原权限"), true);
assert.equal(handoff.unassigned[0].content.includes("别片"), false);

// Actual serialized Channel decisions previously rendered blank IDs and bodies.
import realDecisions from "./fixtures/spec206-real-decisions.json" with { type: "json" };
import { planDecisionView } from "../src/components/delivery-trace/planDecisionView.js";
const ownerDecision = planDecisionView(realDecisions.items[4]);
assert.equal(ownerDecision.id, "DL-05");
assert.equal(ownerDecision.question, realDecisions.items[4].decision);
assert.equal(ownerDecision.deadline, "创建/派发 CLI-001 之前");
assert.equal(ownerDecision.sources[0], realDecisions.items[4].source_ref);
assert.equal(ownerDecision.closure, realDecisions.items[4].closure_evidence);
assert.equal(decisionStatusDistribution(realDecisions.items).includes("decided_source 4"), true);
const aliased = planDecisionView({ decision_id: "A", id: "B", question: "Q", decision: "D", options: ["one", { label: "two" }, null], resolution: "still needs verification", evidence_refs: ["x"], source_ref: "x" });
assert.equal(aliased.id, "A");
assert.equal(aliased.options.join(), "one,two");
assert.equal(aliased.sources.length, 1);
assert.equal(aliased.resolution, "still needs verification");
const channelHeadings = "## authority 与产品规格（PRD）\n目标。\n## Shared Spec：技术设计（SDD，CLI-CONTRACT-v1）\n规则。\n## Task Spec: CLI-001\n本片。\n";
assert.equal(planReadingSections(channelHeadings).overview.length, 1);
assert.equal(planTaskSpecifications(channelHeadings, ["CLI-001"]).shared.length, 1);
assert.equal(planTaskSpecifications(channelHeadings, ["CLI-001"]).unassigned.length, 1);

const unfamiliarOpening = "## 权威、来源和授权\n等待 Owner。\n### 来源\n保留原文。\n```md\n## 伪标题\n```\n## Task Spec: T1\n本片。\n";
assert.equal(planReadingSections(unfamiliarOpening).overview.length, 0);
assert.equal(planReadingSections(unfamiliarOpening).opening[0].content, unfamiliarOpening.slice(0, unfamiliarOpening.indexOf("## Task Spec:")));
assert.equal(planReadingSections("## Task Spec: T1\n仅本片。\n").opening.length, 0);
assert.equal(planReadingSections("# 无二级章节\n原文。\n").opening.length, 0);
assert.equal(planReadingSections("## 共享 Task Spec\n共享。\n").opening.length, 0);
assert.equal(planTaskSpecifications("## 共享 Task Spec\n共同条件。\n", []).shared[0].content, "## 共享 Task Spec\n共同条件。\n");
assert.equal(planDecisionView({ id: "D-001", proposed_resolution: "仍需 Owner 决定" }).resolution, "仍需 Owner 决定");
assert.equal(planDecisionView({ resolution: "作者结论", proposed_resolution: "候选" }).resolution, "作者结论");

import { decisionReadingSummary, decisionStatusLabel } from "../src/components/delivery-trace/planDecisionView.js";
import { taskCommandReferences } from "../src/components/delivery-trace/planCommandView.js";
const decisionsBefore = [{ status: "proposed", blocking: false }, { status: "pending_validation", blocking: false }, { status: "pending_approval", blocking: true }, { status: "future-status" }];
const snapshot = JSON.stringify(decisionsBefore);
assert.equal(decisionReadingSummary(decisionsBefore), "待确认提议 1 · 待验证 1 · 待审批 1 · 未识别状态：future-status 1 · 明确阻塞 1");
assert.equal(decisionStatusLabel({ status: "not_run" }), "尚未执行");
assert.equal(JSON.stringify(decisionsBefore), snapshot);
const readTask = { task_id: "READ", acceptance_criteria: [{ id: "R", verification_command_ids: ["LOCAL", "FINAL", "MISSING"] }], validation: { commands: [{ id: "LOCAL", command: "read test", owner: "task_verify", producer_task_id: "READ" }] } };
const sizeTask = { task_id: "SIZE", validation: { commands: [{ id: "FINAL", command: "both test", owner: "candidate_verify", producer_task_id: "SIZE" }] } };
const refs = taskCommandReferences([readTask, sizeTask], readTask);
assert.equal(refs.map(r => r.label).join(";"), "本任务 · 本片验证;其他任务 SIZE · 候选集成验证;定义缺失，请核对工程附件");
assert.equal(taskCommandReferences([readTask, sizeTask, sizeTask], readTask)[1].definitions.length, 2);
assert.equal(buildPlanReview({ task_map: { tasks: [readTask, sizeTask] } }).modules[0].criteria[0].commandReferences[1].label, "其他任务 SIZE · 候选集成验证");
const changeText = "## 本次变更\nMODIFIED：错误格式。\n### 保留\n原字节不变。\n## Task Spec: READ\n读取。";
const changeSections = planReadingSections(changeText).changes;
assert.equal(changeSections.length, 1);
assert.equal(changeSections[0].content.includes("原字节不变"), true);
assert.equal(changeSections[0].content.includes("读取。"), false);
assert.equal(planReadingSections("```md\n## 本次变更\n假的标题\n```").changes.length, 0);


{
  const review = buildPlanReview({ execution_spec: { shared: { human_review: {
    overview_markdown: "交付三个 CLI。", changes_markdown: "仅修改 upper 示例，其他义务保留。",
    risks_markdown: "输入编码风险仍需验证。",
  } } } });
  assert.equal(review.overview, "交付三个 CLI。");
  assert.equal(review.changes, "仅修改 upper 示例，其他义务保留。");
  assert.equal(review.risks, "输入编码风险仍需验证。");
  assert.equal(buildPlanReview({}).changes, "");
}


assert.equal(planDecisionView({ id: "D-03", statement: "Owner 批准后才允许实现", status: "pending", blocking: true }).question, "Owner 批准后才允许实现");

// Actual producer source_acceptance_ids resolve through unique admitted source IDs.
const atomicMap = { tasks: [{ task_id: "T1", acceptance_criteria: [{ id: "LOCAL", source_acceptance_ids: ["AC-SOURCE"] }] }] };
const atomicIndex = { traceability: [{ source_id: "AC-SOURCE", source_ref: "prd.md#AC-SOURCE" }] };
assert.equal(buildPlanReview({ task_map: atomicMap, source_index: atomicIndex }).modules[0].criteria[0].sourceRefs[0], "prd.md#AC-SOURCE");
const ambiguousIndex = { traceability: [...atomicIndex.traceability, { source_id: "AC-SOURCE", source_ref: "other.md" }] };
const ambiguousSource = buildPlanReview({ task_map: atomicMap, source_index: ambiguousIndex });
assert.equal(ambiguousSource.modules[0].criteria[0].sourceRefs.length, 0);
assert.equal(ambiguousSource.warnings.some(w => w.includes("未唯一解析")), true);
assert.equal(buildPlanReview({ task_map: atomicMap, source_index: { traceability: [{ ...atomicIndex.traceability[0], task_ids: ["T2"] }] } }).modules[0].criteria[0].sourceRefs.length, 0);
const ledgerMap = { tasks: [{ task_id: "T1", acceptance_criteria: [{ id: "LOCAL" }] }, { task_id: "T2", acceptance_criteria: [{ id: "LOCAL" }] }] };
const ledgerReview = buildPlanReview({ task_map: ledgerMap, source_index: atomicIndex, coverage_ledger: { entries: [{ source_id: "AC-SOURCE", task_ids: ["T1"], acceptance_ids: ["LOCAL"] }] } });
assert.equal(ledgerReview.modules[0].criteria[0].sourceRefs[0], "prd.md#AC-SOURCE");
assert.equal(ledgerReview.modules[1].criteria[0].sourceRefs.length, 0);
