import { buildPlanTrace, comparePlanTrace } from "../src/components/delivery-trace/planTraceModel.js";
import { planDocumentSections } from "../src/components/delivery-trace/planDocumentSections.js";
const equal = (a: unknown, b: unknown) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${JSON.stringify(a)} != ${JSON.stringify(b)}`); };
const artifacts = {
  source_index: { traceability: [{ source_id: "S1", statement: "库存不得重复扣减" }, { source_id: "S2" }, { source_id: "S3" }] },
  coverage_ledger: { entries: [{ source_id: "S1", disposition: "covered", task_ids: ["T1"], acceptance_ids: ["A1"], verification_command_ids: ["C1"], evidence_status: "NOT_RUN" }, { source_id: "S2", disposition: "pending_decision" }] },
  task_map: { tasks: [{ task_id: "T1", title: "库存" }] },
  acceptance_matrix: { acceptance: [{ id: "A1", statement: "重复请求不重复扣减", evidence_paths: ["evidence/stock.json"] }] },
  test_matrix: { commands: [{ id: "C1", command: "test inventory" }] },
};
const trace = buildPlanTrace(artifacts);
equal(trace.map(r => r.disposition), ["covered", "pending_decision", "unmapped"]);
equal(trace[0].evidenceStatus, "NOT_RUN"); equal(trace[0].issues, []);
equal(trace[0].evidence, ["evidence/stock.json"]);
const invalid = buildPlanTrace({ ...artifacts, task_map: { tasks: [] } });
equal(invalid[0].issues, ["Task 不存在：T1"]);
const duplicate = buildPlanTrace({ ...artifacts, coverage_ledger: { entries: [artifacts.coverage_ledger.entries[0], artifacts.coverage_ledger.entries[0]] } });
equal(duplicate[0].disposition, "conflict");
equal(duplicate[0].issues.includes("来源或覆盖记录重复，需核对"), true);
equal(comparePlanTrace(trace, trace), []);
equal(comparePlanTrace(trace, [{ ...trace[0], statement: "修改后的需求" }, trace[1]]).map(d => [d.id, d.kind]), [["S1", "修改"], ["S3", "移除"]]);
const many = buildPlanTrace({ source_index: { rows: Array.from({ length: 2500 }, (_, i) => ({ source_id: `S${i}` })) } });
equal(many.length, 2500); equal(many[2499].id, "S2499");
const document = '# 产品\n\n概要\n\n<a id="stock"></a>\n\n## 库存\n\n拒绝重复\n\n```md\n# 不是章节\n```\n';
const sections = planDocumentSections(document);
equal(sections.map(s => s.title), ["产品", "库存"]);
equal(sections[1].anchor, "stock"); equal(sections.map(s => s.content).join(""), document);
console.log("planTraceModel.test.ts OK");
