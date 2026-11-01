import { AlertTriangle, Check, CheckCircle2, CircleStop, ExternalLink, FileDiff, Info, MoreHorizontal, PackageCheck, Pause, Play, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import type { WorkflowRequestDetail } from "../../api/types";
import { asRecord, asRecordArray, asStringArray, stringify, textValue } from "../../app/shared";
import { SectionHeading, StatusBadge, WorkflowDiagnostics, WorkflowList } from "./WorkflowProposalParts";
import { WorkflowClarificationPanel } from "./WorkflowClarificationPanel";
import { diagnosticSeverity, expectedOutputLabel, numberValue, readinessPresentation, shortDigest, stageRoleLabel } from "./workflowProposalModel";

export function WorkflowProposalDetail({
  actionReady,
  actionState,
  busyAction,
  detail,
  onCancel,
  onClarify,
  onOpenRuns,
  onPrepare,
  onReject,
  onRunCancel,
  onRunPause,
  onRunResume,
}: {
  actionReady: boolean;
  actionState: string;
  busyAction: string;
  detail: WorkflowRequestDetail;
  onCancel: (payload: Record<string, unknown>) => void;
  onClarify: (payload: Record<string, unknown>) => void;
  onOpenRuns: (runId: string) => void;
  onPrepare: (payload: Record<string, unknown>) => void;
  onReject: (payload: Record<string, unknown>) => void;
  onRunCancel: (payload: Record<string, unknown>) => void;
  onRunPause: (payload: Record<string, unknown>) => void;
  onRunResume: (payload: Record<string, unknown>) => void;
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const request = detail.result;
  const requirement = detail.requirement;
  const proposal = detail.proposal;
  const operation = detail.operation;
  const artifacts = detail.artifacts;
  const lifecycle = detail.lifecycle;
  const links = detail.links;
  const proposalRef = asRecord(request.proposal_ref);
  const proposalDigest = textValue(request.proposal_digest);
  const requestId = textValue(request.request_id);
  const requestStatus = textValue(request.status);
  const selectedKind: Record<string, string> = { IssueFlow: "issue", PrdFlow: "prd", RefactorFlow: "refactor", Workflow: "workflow" };
  const requestKind = selectedKind[textValue(request.selected_flow_family)] || textValue(request.kind);
  const changeMode = textValue(proposal.change_mode);
  const configApplied = lifecycle.config_applied === true;
  const submitted = lifecycle.submitted === true || ["submitted", "running"].includes(requestStatus);
  const terminal = textValue(lifecycle.terminal);
  const runAdmission = asRecord(lifecycle.admission);
  const runStatus = textValue(runAdmission.status);
  const runId = textValue(runAdmission.run_id) || textValue(links.run_id);
  const approvable = requestStatus === "proposed" && proposal.approval_status === "approvable" && Boolean(proposalDigest) && Boolean(proposalRef.ref);
  const decisionPayload = {
    request_id: requestId,
    proposal_ref: proposalRef,
    proposal_digest: proposalDigest,
  };
  const stages = asRecordArray(asRecord(proposal.stage_graph).nodes);
  const preparationBlockers = asRecordArray(asRecord(detail.preparation).blockers);
  const blockers = [...asRecordArray(proposal.blockers), ...preparationBlockers];
  const preflight = asRecord(proposal.preflight);
  const diagnostics = asRecordArray(preflight.diagnostics);
  const stopDiagnostics = diagnostics.filter((item) => diagnosticSeverity(item) === "STOP");
  const warningDiagnostics = diagnostics.filter((item) => diagnosticSeverity(item) === "WARN");
  const infoDiagnostics = diagnostics.filter((item) => diagnosticSeverity(item) === "INFO");
  const severityCounts = asRecord(asRecord(preflight.summary).diagnostics);
  const blockerCount = Math.max(blockers.length, numberValue(severityCounts.STOP, stopDiagnostics.length));
  const warningCount = Math.max(
    warningDiagnostics.length,
    numberValue(severityCounts.WARN, warningDiagnostics.length),
  );
  const infoCount = Math.max(
    infoDiagnostics.length,
    numberValue(severityCounts.INFO, infoDiagnostics.length),
  );
  const closure = asRecord(proposal.closure);
  const roles = asRecordArray(closure.roles);
  const profiles = Object.entries(asRecord(closure.execution_profiles));
  const configDiff = asRecord(artifacts.config_diff);
  const shortFlowSpec = asRecord(artifacts.short_flow_spec);
  const completionProfile = asRecord(proposal.completion_profile);
  const expectedOutputs = asRecordArray(completionProfile.required_delivery_artifacts);
  const estimated = asRecord(proposal.estimated);
  const synthesisStatus = textValue(operation.queue_status);
  const synthesisCancellable = ["queued", "running"].includes(synthesisStatus);
  const cancelPayload = {
    request_id: requestId,
    operation_id: textValue(operation.operation_id),
    request_hash: (
      textValue(operation.request_hash)
      || textValue(request.synthesis_request_hash)
    ),
    reason: "cancelled by operator",
  };
  const runControlPayload = {
    run_id: runId,
    request_id: requestId,
    reason: "requested by workflow proposal operator",
  };
  const isDecision = requestStatus === "proposed";
  const runVisible = submitted
    || Boolean(terminal)
    || ["queued", "running", "paused"].includes(runStatus);
  const hasOverflowActions = (
    isDecision
    || synthesisCancellable
    || ["queued", "running", "paused"].includes(runStatus)
  );
  const readiness = readinessPresentation({
    blockerCount,
    requestStatus,
    runStatus,
    terminal,
  });
  const advancedId = `workflow-advanced-${requestId}`;

  return (
    <>
      <header className="workflow-proposal-detail-head">
        <div>
          <h3 data-review-field="requirement.objective">{textValue(requirement.objective) || requestId}</h3>
          <div className="workflow-proposal-meta">
            <StatusBadge status={requestStatus} />
            {textValue(proposal.risk_class) ? <span>{textValue(proposal.risk_class)}</span> : null}
            {synthesisStatus ? (
              <StatusBadge status={`synthesis ${synthesisStatus}`} />
            ) : null}
          </div>
        </div>
        <div className="workflow-proposal-actions">
          {runVisible ? (
            <button
              className="icon-button primary"
              data-testid="workflow-open-run"
              type="button"
              onClick={() => onOpenRuns(runId)}
            >
              <ExternalLink aria-hidden="true" size={16} />
              Open Run
            </button>
          ) : null}
          {hasOverflowActions ? (
            <details className="workflow-action-menu">
              <summary
                aria-label="More workflow actions"
                className="icon-button"
                title="More actions"
              >
                <MoreHorizontal aria-hidden="true" size={17} />
              </summary>
              <div role="menu">
                {runStatus === "running" ? (
                  <button
                    disabled={!actionReady || busyAction !== "" || !runId}
                    title={!actionReady ? actionState : "Pause new dispatch for this Run"}
                    type="button"
                    onClick={() => onRunPause(runControlPayload)}
                  >
                    <Pause aria-hidden="true" size={15} />
                    {busyAction === "run-pause" ? "Pausing" : "Pause Run"}
                  </button>
                ) : null}
                {runStatus === "paused" ? (
                  <button
                    disabled={!actionReady || busyAction !== "" || !runId}
                    title={!actionReady ? actionState : "Resume this Run"}
                    type="button"
                    onClick={() => onRunResume(runControlPayload)}
                  >
                    <Play aria-hidden="true" size={15} />
                    {busyAction === "run-resume" ? "Resuming" : "Resume Run"}
                  </button>
                ) : null}
                {synthesisCancellable ? (
                  <button
                    disabled={
                      !actionReady
                      || busyAction !== ""
                      || !cancelPayload.operation_id
                      || !cancelPayload.request_hash
                    }
                    title={!actionReady ? actionState : "Cancel workflow synthesis"}
                    type="button"
                    onClick={() => onCancel(cancelPayload)}
                  >
                    <CircleStop aria-hidden="true" size={15} />
                    {busyAction === "workflow-cancel" ? "Cancelling" : "Cancel synthesis"}
                  </button>
                ) : null}
                {isDecision ? (
                  <button
                    className="danger"
                    disabled={!actionReady || !approvable || busyAction !== ""}
                    title={!actionReady ? actionState : "Reject this exact proposal"}
                    type="button"
                    onClick={() => onReject({
                      ...decisionPayload,
                      reason: "operator rejected proposal",
                    })}
                  >
                    <X aria-hidden="true" size={15} />
                    {busyAction === "workflow-reject" ? "Rejecting" : "Reject proposal"}
                  </button>
                ) : null}
                {["queued", "running", "paused"].includes(runStatus) ? (
                  <button
                    className="danger"
                    disabled={!actionReady || busyAction !== "" || !runId}
                    title={!actionReady ? actionState : "Cancel this Run"}
                    type="button"
                    onClick={() => onRunCancel(runControlPayload)}
                  >
                    <CircleStop aria-hidden="true" size={15} />
                    {busyAction === "run-cancel" ? "Cancelling" : "Cancel Run"}
                  </button>
                ) : null}
              </div>
            </details>
          ) : null}
        </div>
      </header>

      <section
        className={`workflow-readiness tone-${readiness.tone}`}
        data-testid="workflow-readiness"
      >
        {readiness.tone === "error" ? (
          <AlertTriangle aria-hidden="true" size={19} />
        ) : readiness.tone === "info" ? (
          <Info aria-hidden="true" size={19} />
        ) : (
          <CheckCircle2 aria-hidden="true" size={19} />
        )}
        <div>
          <span className="eyebrow">Readiness</span>
          <strong>{readiness.title}</strong>
          {preparationBlockers.map((item, index) => <p key={index} data-testid="workflow-preparation-blocker">
            {textValue(item.message)} {textValue(item.fix_it)}
          </p>)}
          <small>
            {blockerCount} blockers
            {" / "}
            {warningCount} warnings
            {" / "}
            {infoCount} info
          </small>
        </div>
        {(blockerCount || warningCount || infoCount) ? (
          <button
            className="workflow-inline-action"
            type="button"
            onClick={() => {
              setAdvancedOpen(true);
              window.requestAnimationFrame(() => {
                document.getElementById(advancedId)?.scrollIntoView({
                  behavior: "smooth",
                  block: "start",
                });
              });
            }}
          >
            Review details
          </button>
        ) : null}
      </section>

      <WorkflowClarificationPanel
        actionReady={actionReady}
        actionState={actionState}
        busyAction={busyAction}
        intakeRef={textValue(links.intake_ref)}
        onClarify={onClarify}
        onPrepare={onPrepare}
        requestKind={requestKind}
        requestId={requestId}
        requestStatus={requestStatus}
        requirement={{ ...requirement, open_questions: request.open_questions ?? requirement.open_questions }}
        requirementDigest={textValue(request.requirement_spec_digest)}
      />

      {expectedOutputs.length ? (
        <section className="workflow-expected-output">
          <PackageCheck aria-hidden="true" size={19} />
          <div>
            <span className="eyebrow">Expected output</span>
            {expectedOutputs.map((output, index) => (
              <strong key={`${textValue(output.name)}-${index}`}>
                {expectedOutputLabel(output)}
              </strong>
            ))}
          </div>
        </section>
      ) : null}

      <section className="workflow-proposal-section workflow-requirements">
        <SectionHeading icon={Check} meta="confirmed requirement" title="Requirements" />
        <div>
          <div><h4>Acceptance</h4><ul>{asStringArray(requirement.acceptance).map((item, index) => <li key={index} data-review-field={`requirement.acceptance.${index}`}>{item}</li>)}</ul></div>
          <div><h4>Constraints</h4><ul>{asStringArray(requirement.constraints).map((item, index) => <li key={index} data-review-field={`requirement.constraints.${index}`}>{item}</li>)}</ul></div>
          {asStringArray(requirement.open_questions).length ? (
            <div><h4>Open questions</h4><ul>{asStringArray(requirement.open_questions).map((item, index) => <li key={index} data-review-field={`requirement.open_questions.${index}`}>{item}</li>)}</ul></div>
          ) : null}
        </div>
      </section>

      <details
        className="workflow-proposal-advanced"
        id={advancedId}
        open={advancedOpen}
        onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}
      >
        <summary>
          <span>
            <ShieldCheck aria-hidden="true" size={15} />
            <strong>Advanced audit details</strong>
          </span>
          <small>diagnostics, bindings, roles and FlowSpec</small>
        </summary>
        <div className="workflow-proposal-advanced-body">
          {(blockerCount || warningCount || infoCount) ? (
            <section className="workflow-proposal-section">
              <SectionHeading
                icon={ShieldCheck}
                meta={`${blockerCount} blockers / ${warningCount} warnings / ${infoCount} info`}
                title="Preflight diagnostics"
              />
              <WorkflowDiagnostics
                blockers={blockers.length ? blockers : stopDiagnostics}
                info={infoDiagnostics}
                warnings={warningDiagnostics}
              />
            </section>
          ) : null}

          <div className="workflow-proposal-two-column">
            <section className="workflow-proposal-section">
              <SectionHeading icon={Check} meta={`${roles.length} roles`} title="Execution Closure" />
              <dl className="workflow-proposal-kv">
                <dt>Profiles</dt>
                <dd>{profiles.map(([id]) => id).join(", ") || "direct-v1"}</dd>
                <dt>Delivery policy</dt>
                <dd>{textValue(completionProfile.delivery_policy) || "report_only"}</dd>
                <dt>Stages</dt>
                <dd>{textValue(estimated.stages) || stages.length}</dd>
                <dt>Roles</dt>
                <dd>{textValue(estimated.roles) || roles.length}</dd>
              </dl>
              <details className="workflow-advanced-subsection">
                <summary>Role mapping <span>{roles.length}</span></summary>
                <div className="workflow-proposal-role-list">
                  {roles.map((role, index) => {
                    const execution = asRecord(role.execution);
                    return (
                      <div key={`${textValue(role.name)}-${index}`}>
                        <strong>{textValue(role.name) || textValue(role.instance_id) || "role"}</strong>
                        <span>{textValue(role.backend) || "default backend"}</span>
                        <code>{textValue(execution.default_profile) || "direct-v1"}</code>
                      </div>
                    );
                  })}
                </div>
              </details>
            </section>

            <section className="workflow-proposal-section">
              <SectionHeading icon={ShieldCheck} meta={changeMode} title="Decision Binding" />
              <dl className="workflow-proposal-kv">
                <dt>Proposal</dt>
                <dd className="mono">{shortDigest(proposalDigest)}</dd>
                <dt>Requirement</dt>
                <dd className="mono">{shortDigest(textValue(request.requirement_spec_digest))}</dd>
                <dt>Effective config</dt>
                <dd className="mono">{shortDigest(textValue(asRecord(proposal.effective_config_ref).sha256))}</dd>
                <dt>Config apply</dt>
                <dd>{changeMode === "config_change" ? (configApplied ? "applied" : "required") : "not required"}</dd>
                <dt>Run</dt>
                <dd>{terminal || (submitted ? "submitted" : "not started")}</dd>
              </dl>
            </section>
          </div>

          <details className="workflow-advanced-subsection">
            <summary>Effective topology <span>{stages.length} stages</span></summary>
            <div className="workflow-effective-topology">
              {stages.map((stage, index) => (
                <div key={`${textValue(stage.id)}-${index}`}>
                  <strong>{textValue(stage.id) || `stage-${index + 1}`}</strong>
                  <span>{stageRoleLabel(stage)}</span>
                  <code>{textValue(stage.trigger) || "dependency driven"}</code>
                </div>
              ))}
            </div>
          </details>

          {textValue(configDiff.changed) === "true" ? (
            <section className="workflow-proposal-section">
              <SectionHeading icon={FileDiff} meta="changed" title="Config Diff" />
              <pre className="workflow-proposal-code">{textValue(configDiff.unified_diff)}</pre>
            </section>
          ) : null}

          <details className="workflow-advanced-subsection">
            <summary>FlowSpec <span>raw snapshot</span></summary>
            <pre className="workflow-proposal-code">{stringify(shortFlowSpec)}</pre>
          </details>
        </div>
      </details>
    </>
  );
}
