import type { ActionResponse, WorkflowRequestDetail } from "../../api/types";

export function workflowStartActions(detail: WorkflowRequestDetail): { action: string; payload: Record<string, unknown> }[] {
  const { result: request, proposal, lifecycle, links } = detail;
  const proposalRef = request.proposal_ref as Record<string, unknown> | undefined;
  const digest = String(request.proposal_digest || "");
  if (request.status !== "proposed" || proposal.approval_status !== "approvable" || !digest || !proposalRef?.ref
    || lifecycle.submitted || links.run_id) return [];
  const binding = { request_id: request.request_id, proposal_ref: proposalRef, proposal_digest: digest };
  const actions = [];
  if (proposal.change_mode === "config_change") {
    if (!(proposal.validation_result_ref as Record<string, unknown> | undefined)?.ref) return [];
    actions.push({ action: "workflow-config-apply", payload: {
      establish_delivery_baseline: true,
      ...binding, proposal_id: proposal.proposal_id, validation_result_ref: proposal.validation_result_ref,
      approval_ref: `web:workflow-proposal:${request.request_id}:${digest}`, idempotency_key: `workflow-config-apply:${digest}`,
    } });
  }
  actions.push({ action: "workflow-submit", payload: {
    ...binding, intake_ref: String(links.intake_ref || ""), kind: request.kind, allow_missing_env: true,
  } });
  return actions;
}

export async function startWorkflow(detail: WorkflowRequestDetail, onAction: (action: string, payload: Record<string, unknown>) => Promise<ActionResponse>): Promise<ActionResponse> {
  const actions = workflowStartActions(detail);
  if (!actions.length) throw new Error("This proposal is not ready to start. Refresh and resolve its blockers.");
  let result: ActionResponse;
  for (const step of actions) {
    result = await onAction(step.action, step.payload);
    if (!result.ok) return result;
  }
  return result!;
}
