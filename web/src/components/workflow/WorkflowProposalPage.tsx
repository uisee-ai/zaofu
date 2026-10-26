import {
  AlertTriangle,
  Check,
  CheckCircle2,
  CircleStop,
  ExternalLink,
  FileDiff,
  GitFork,
  Info,
  MoreHorizontal,
  PackageCheck,
  Pause,
  Play,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  clarifyWorkflowRequest,
  prepareWorkflowProposal,
} from "../../api/client";
import type {
  ActionResponse,
  WorkflowRequestDetail,
} from "../../api/types";
import type { ProjectionKind } from "../../app/sharedTypes";
import {
  asRecord,
  asRecordArray,
  asStringArray,
  stringify,
  textValue,
} from "../../app/shared";
import {
  SectionHeading,
  StatusBadge,
  WorkflowDiagnostics,
  WorkflowEmpty,
  WorkflowExecutionPlan,
  WorkflowList,
} from "./WorkflowProposalParts";
import { useWorkflowRequests } from "./useWorkflowRequests";
import { WorkflowPreviewReview } from "./WorkflowPreviewReview";
import { WorkflowClarificationPanel } from "./WorkflowClarificationPanel";
import {
  diagnosticSeverity,
  expectedOutputLabel,
  numberValue,
  readinessPresentation,
  requestTitle,
  shortDigest,
  stageLevels,
  stageRoleLabel,
  stagesForCurrentFlow,
  WORKFLOW_VIEWS,
  workflowViewForRequest,
  type WorkflowRequest,
  type WorkflowView,
} from "./workflowProposalModel";

interface WorkflowProposalPageProps {
  actionReady: boolean;
  actionState: string;
  onAction: (
    action: string,
    payload: Record<string, unknown>,
  ) => Promise<ActionResponse>;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  projectId: string;
}

export function WorkflowProposalPage(props: WorkflowProposalPageProps) {
  return <WorkflowProposalWorkbench key={props.projectId} {...props} />;
}

function WorkflowProposalWorkbench({
  actionReady,
  actionState,
  onAction,
  onOpenProjection,
  projectId,
}: WorkflowProposalPageProps) {
  const [busyAction, setBusyAction] = useState("");
  const [feedback, setFeedback] = useState<ActionResponse | null>(null);
  const [view, setView] = useState<WorkflowView>("decision");
  const reviewBusy = useRef(false);
  const { requests, selectedId, setSelectedId, detail, loading, error, loadRequests, loadDetail }
    = useWorkflowRequests(projectId, reviewBusy);
  const actionSelection = useRef(selectedId);
  actionSelection.current = selectedId;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => { setBusyAction(""); setFeedback(null); }, [selectedId]);

  async function execute(
    action:
      | "workflow-submit"
      | "workflow-reject"
      | "workflow-config-apply"
      | "workflow-cancel"
      | "run-pause"
      | "run-resume"
      | "run-cancel",
    payload: Record<string, unknown>,
  ) {
    if (busyAction || !detail || payload.request_id !== selectedId) return;
    const requestId = selectedId;
    const current = () => mounted.current && actionSelection.current === requestId;
    setBusyAction(action);
    setFeedback(null);
    try {
      const result = await onAction(action, payload);
      if (!current()) return;
      setFeedback(result);
      await loadRequests();
      await loadDetail();
    } catch (reason) {
      if (!current()) return;
      setFeedback({
        ok: false,
        status: "failed",
        action,
        reason: reason instanceof Error ? reason.message : String(reason),
      });
    } finally {
      if (current()) setBusyAction("");
    }
  }

  async function executeRequestTransition(
    action: "workflow-clarify" | "workflow-prepare",
    payload: Record<string, unknown>,
  ) {
    if (busyAction || !detail || payload.request_id !== selectedId) return;
    const requestId = selectedId;
    const current = () => mounted.current && actionSelection.current === requestId;
    setBusyAction(action);
    setFeedback(null);
    try {
      let result = action === "workflow-clarify"
        ? await clarifyWorkflowRequest(projectId, payload)
        : await prepareWorkflowProposal(projectId, payload);
      if (
        current()
        && action === "workflow-clarify"
        && result.ok === true
        && textValue(result.status) === "ready"
      ) {
        setBusyAction("workflow-prepare");
        result = await prepareWorkflowProposal(projectId, {
          request_id: payload.request_id,
          intake_ref: payload.intake_ref,
          kind: payload.kind,
          expected_revision: asRecord(result.result).revision,
          expected_requirement_digest: asRecord(result.result).requirement_spec_digest,
          allow_missing_env: true,
        });
      }
      if (!current()) return;
      setFeedback(workflowRequestFeedback(action, result));
      await loadRequests();
      await loadDetail();
    } catch (reason) {
      if (!current()) return;
      setFeedback({
        ok: false,
        status: "failed",
        action,
        reason: reason instanceof Error ? reason.message : String(reason),
      });
    } finally {
      if (current()) setBusyAction("");
    }
  }

  const requestsByView = useMemo(() => {
    const grouped: Record<WorkflowView, WorkflowRequest[]> = {
      decision: [],
      active: [],
      history: [],
    };
    for (const request of requests) {
      grouped[workflowViewForRequest(request)].push(request);
    }
    return grouped;
  }, [requests]);

  const visibleRequests = requestsByView[view];

  useEffect(() => {
    if (!requests.length) return;
    if (!visibleRequests.length) {
      const nextView = WORKFLOW_VIEWS.find((item) => requestsByView[item.id].length)?.id;
      if (nextView && nextView !== view) setView(nextView);
      return;
    }
    if (!visibleRequests.some((item) => textValue(item.request_id) === selectedId)) {
      setSelectedId(textValue(visibleRequests[0]?.request_id));
    }
  }, [requests.length, requestsByView, selectedId, view, visibleRequests]);

  return (
    <div className="workflow-proposal-page" data-testid="workflow-proposal-page">
      <header className="workflow-proposal-toolbar">
        <div>
          <h2>Workflows</h2>
          <span className="muted">
            {requests.length} workflows
            {requestsByView.decision.length
              ? ` / ${requestsByView.decision.length} awaiting decision`
              : ""}
          </span>
        </div>
        <button
          aria-label="Refresh workflow proposals"
          className="icon-button"
          title="Refresh"
          type="button"
          onClick={() => {
            void loadRequests();
            void loadDetail();
          }}
        >
          <RefreshCw aria-hidden="true" size={16} />
        </button>
      </header>

      {error ? <div className="workflow-proposal-notice tone-error">{error}</div> : null}
      {feedback ? (
        <div
          className={`workflow-proposal-notice ${feedback.ok ? "tone-ok" : "tone-error"}`}
          data-testid="workflow-proposal-feedback"
        >
          <strong>{feedback.status}</strong>
          <span>{feedback.reason || decisionFeedback(feedback)}</span>
        </div>
      ) : null}

      <nav className="workflow-view-tabs" aria-label="Workflow lifecycle">
        {WORKFLOW_VIEWS.map((item) => (
          <button
            aria-selected={view === item.id}
            className={view === item.id ? "active" : ""}
            key={item.id}
            role="tab"
            type="button"
            onClick={() => {
              setFeedback(null);
              setView(item.id);
            }}
          >
            <span>{item.label}</span>
            <strong>{requestsByView[item.id].length}</strong>
          </button>
        ))}
      </nav>

      {loading ? (
        <WorkflowEmpty title="Loading workflows" />
      ) : requests.length === 0 ? (
        <WorkflowEmpty title="No workflows" />
      ) : (
        <div className="workflow-proposal-workbench">
          <aside className="workflow-request-list" aria-label="Workflow requests">
            {visibleRequests.map((request) => {
              const requestId = textValue(request.request_id);
              const synthesisStatus = textValue(asRecord(request.operation).queue_status);
              return (
                <button
                  className={`workflow-request-row ${requestId === selectedId ? "active" : ""}`}
                  key={requestId}
                  type="button"
                  onClick={() => {
                    setFeedback(null);
                    setBusyAction("");
                    setSelectedId(requestId);
                  }}
                >
                  <span className="workflow-request-row-head">
                    <strong>{requestTitle(request)}</strong>
                    <StatusBadge status={textValue(request.status)} />
                  </span>
                  <small title={requestId}>
                    <span>{textValue(request.kind) || "flow"}</span>
                    <span>Revision {textValue(request.revision) || "1"}</span>
                    {["queued", "running"].includes(synthesisStatus) ? (
                      <span>Synthesis {synthesisStatus}</span>
                    ) : null}
                  </small>
                </button>
              );
            })}
          </aside>

          <main className="workflow-proposal-detail">
            {detail ? (
              <WorkflowPreviewReview key={selectedId} detail={detail} projectId={projectId} disabled={!actionReady}
                onBusy={(busy) => { reviewBusy.current = busy; setBusyAction(busy ? "preview-review" : ""); }}
                onRefresh={async () => { await loadRequests(); await loadDetail(); }}>
              <WorkflowProposalDetail
                actionReady={actionReady}
                actionState={actionState}
                busyAction={busyAction}
                detail={detail}
                key={selectedId}
                onApply={(payload) => void execute("workflow-config-apply", payload)}
                onApprove={(payload) => void execute("workflow-submit", payload)}
                onCancel={(payload) => void execute("workflow-cancel", payload)}
                onClarify={(payload) => void executeRequestTransition("workflow-clarify", payload)}
                onOpenRuns={(runId) => onOpenProjection("run", runId)}
                onPrepare={(payload) => void executeRequestTransition("workflow-prepare", payload)}
                onReject={(payload) => void execute("workflow-reject", payload)}
                onRunCancel={(payload) => void execute("run-cancel", payload)}
                onRunPause={(payload) => void execute("run-pause", payload)}
                onRunResume={(payload) => void execute("run-resume", payload)}
              />
              </WorkflowPreviewReview>
            ) : (
              <WorkflowEmpty title="Loading proposal detail" />
            )}
          </main>
        </div>
      )}
    </div>
  );
}

function WorkflowProposalDetail({
  actionReady,
  actionState,
  busyAction,
  detail,
  onApply,
  onApprove,
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
  onApply: (payload: Record<string, unknown>) => void;
  onApprove: (payload: Record<string, unknown>) => void;
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
  const approvable = (
    requestStatus === "proposed"
    && textValue(proposal.approval_status) === "approvable"
    && Boolean(proposalDigest)
    && Boolean(proposalRef.ref)
  );
  const decisionPayload = {
    request_id: requestId,
    proposal_ref: proposalRef,
    proposal_digest: proposalDigest,
  };
  const submitPayload = {
    ...decisionPayload,
    intake_ref: textValue(links.intake_ref),
    kind: textValue(request.kind),
    allow_missing_env: true,
  };
  const validationResultRef = asRecord(proposal.validation_result_ref);
  const configApplyPayload = {
    ...decisionPayload,
    proposal_id: textValue(proposal.proposal_id),
    validation_result_ref: validationResultRef,
    approval_ref: `web:workflow-proposal:${requestId}:${proposalDigest}`,
    idempotency_key: `workflow-config-apply:${proposalDigest}`,
  };
  const stages = asRecordArray(asRecord(proposal.stage_graph).nodes);
  const blockers = asRecordArray(proposal.blockers);
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
  const planStages = stagesForCurrentFlow(
    stages,
    shortFlowSpec,
    textValue(request.kind),
    textValue(proposal.flow_family),
  );
  const planLevels = stageLevels(planStages);
  const explicitDag = planStages.some((stage) => asStringArray(stage.dependencies).length > 0);
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
          <span className="eyebrow">
            {textValue(proposal.flow_family) || textValue(request.kind) || "Workflow"}
            {" / "}
            Revision {textValue(request.revision)}
          </span>
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
          {changeMode === "config_change" && !configApplied && isDecision ? (
            <button
              className="icon-button"
              data-testid="workflow-apply-config"
              disabled={!actionReady || !approvable || busyAction !== "" || !validationResultRef.ref}
              title={!actionReady ? actionState : "Validate CAS and apply this proposal to zf.yaml"}
              type="button"
              onClick={() => onApply(configApplyPayload)}
            >
              <FileDiff aria-hidden="true" size={16} />
              {busyAction === "workflow-config-apply" ? "Applying" : "Apply config"}
            </button>
          ) : null}
          {isDecision ? (
            <button
              className="icon-button primary"
              data-testid="workflow-approve-run"
              disabled={
                !actionReady
                || !approvable
                || busyAction !== ""
                || (changeMode === "config_change" && !configApplied)
              }
              title={
                !actionReady
                  ? actionState
                  : changeMode === "config_change" && !configApplied
                    ? "Apply the approved config change first"
                    : "Approve this exact proposal and start its run"
              }
              type="button"
              onClick={() => onApprove(submitPayload)}
            >
              <Play aria-hidden="true" size={16} />
              {busyAction === "workflow-submit" ? "Starting" : "Approve & Run"}
            </button>
          ) : null}
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

      {textValue(asRecord(artifacts.effective_config).yaml) ? (
        <section className="workflow-proposal-section" data-testid="workflow-yaml-preview">
          <SectionHeading icon={FileDiff} meta="redacted preview" title="Assembled zf.yaml" />
          <details>
            <summary>View complete YAML</summary>
            <pre className="workflow-proposal-code">{textValue(asRecord(artifacts.effective_config).yaml)}</pre>
          </details>
        </section>
      ) : null}

      <section className="workflow-proposal-section workflow-plan-section" data-review-field="execution_plan">
        <SectionHeading
          icon={GitFork}
          meta={`${planStages.length} of ${stages.length} stages`}
          title="Execution Plan"
        />
        <WorkflowExecutionPlan
          explicitDag={explicitDag}
          levels={planLevels}
          stages={planStages}
        />
      </section>

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

function decisionFeedback(result: ActionResponse): string {
  if (result.action === "workflow-submit" && result.ok) return "Run submitted.";
  if (result.action === "workflow-reject" && result.ok) return "Proposal rejected.";
  if (result.action === "workflow-config-apply" && result.ok) return "Config applied.";
  return result.ok ? "Action completed." : "Action failed.";
}

function workflowRequestFeedback(
  action: "workflow-clarify" | "workflow-prepare",
  response: Record<string, unknown>,
): ActionResponse {
  const result = asRecord(response.result);
  const blockers = asRecordArray(result.blockers);
  const status = textValue(response.status);
  const remainingQuestions = asStringArray(result.open_questions).length;
  const reason = textValue(response.reason)
    || blockers.map((item) => textValue(item.message) || textValue(item.title)).filter(Boolean).join("; ")
    || (action === "workflow-clarify" && status === "clarifying"
      ? `Answers saved. ${remainingQuestions} clarification question${remainingQuestions === 1 ? "" : "s"} remain.`
      : response.ok === true ? "Proposal prepared." : "Request still needs clarification.");
  return {
    ok: response.ok === true,
    status: status || (response.ok === true ? "completed" : "failed"),
    action,
    reason,
    result,
  };
}
