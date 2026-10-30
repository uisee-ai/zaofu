import { startWorkflow, workflowStartActions } from "../src/components/workflow/workflowStartModel.js";
import type { ActionResponse, WorkflowRequestDetail } from "../src/api/types.js";

const detail = { result: { request_id: "R", status: "proposed", proposal_digest: "digest", proposal_ref: { ref: "proposal" }, kind: "issue" },
  proposal: { approval_status: "approvable", change_mode: "config_change", validation_result_ref: { ref: "validation" }, proposal_id: "P" },
  lifecycle: {}, links: { intake_ref: "intake" } } as unknown as WorkflowRequestDetail;
const calls: string[] = [];
const ok = (action: string) => ({ ok: true, status: "accepted", action }) as ActionResponse;
await startWorkflow(detail, async (action, payload) => {
  if (payload.proposal_digest !== "digest") throw new Error("Start must bind one proposal across both actions");
  if (action === "workflow-config-apply" && payload.establish_delivery_baseline !== true) throw new Error("Start must establish the approved config baseline");
  calls.push(action); return ok(action);
});
if (calls.join() !== "workflow-config-apply,workflow-submit") throw new Error("Apply must precede submit");
calls.length = 0;
const failed = await startWorkflow(detail, async action => { calls.push(action); return { ...ok(action), ok: false }; });
if (failed.ok || calls.join() !== "workflow-config-apply") throw new Error("Failed apply must never submit");
const applied = { ...detail, lifecycle: { config_applied: true } } as WorkflowRequestDetail;
if (workflowStartActions(applied).map(x => x.action).join() !== "workflow-config-apply,workflow-submit") throw new Error("Retry must recover an applied but uncommitted config baseline");
for (const blocked of [
  { ...detail, proposal: { ...detail.proposal, approval_status: "blocked" } },
  { ...detail, lifecycle: { submitted: true } },
  { ...detail, links: { run_id: "run" } },
]) {
  if (workflowStartActions(blocked as WorkflowRequestDetail).length) throw new Error("Blocked or submitted preview cannot start");
}
