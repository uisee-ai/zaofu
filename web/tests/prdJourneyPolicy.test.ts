const assert = { equal(actual: unknown, expected: unknown) { if (actual !== expected) throw new Error(`Expected ${expected}, got ${actual}`); } };
import { prdOnlyOption, sameInvocation, terminalPlanningEvent } from "./support/prdJourneyPolicy.js";
const invoked = { id: "i", seq: 10, type: "workflow.invoke.requested", payload: { request_id: "r" } };
const event = (seq: number, type: string, id = "r") => ({ id: String(seq), seq, type, payload: { trace_id: id } });
assert.equal(sameInvocation(event(9, "human.escalate"), invoked), false);
assert.equal(terminalPlanningEvent([event(11, "prd.plan.failed")], invoked), undefined);
assert.equal(terminalPlanningEvent([event(11, "human.escalate", "other")], invoked), undefined);
assert.equal(terminalPlanningEvent([event(11, "human.escalate")], invoked)?.seq, 11);
assert.equal(terminalPlanningEvent([event(11, "human.escalate"), { ...event(12, "runtime.action.completed"), payload: { trace_id: "r", action: "stage-replan-new-generation", status: "applied", escalation_event_id: "11" } }], invoked), undefined);
for (const id of ["write_prd", "finish_prd", "random-42"]) {
  assert.equal(prdOnlyOption([{ id, label: "保存 PRD" }, { id: "unsafe", label: "启动 Workflow" }]), id);
}
assert.equal(prdOnlyOption([{ id: "x", label: "保存 PRD 并启动 Task" }]), undefined);
assert.equal(prdOnlyOption([{ id: "x", label: "保存 PRD", action: "create-task" }]), undefined);
assert.equal(prdOnlyOption([{ id: "x", label: "保存 PRD" }, { id: "y", label: "仅 PRD" }]), undefined);
console.log("PRD journey authorization and terminal detection passed");

assert.equal(prdOnlyOption([{ id: "x", label: "保存 PRD 并输出 Plan" }]), undefined);
