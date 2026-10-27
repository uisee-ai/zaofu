import { RefreshCw, X } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { clarifyWorkflowRequest, prepareWorkflowProposal } from "../../api/client";
import type { ActionResponse, RecentEvent } from "../../api/types";
import type { ProjectionKind } from "../../app/sharedTypes";
import { asRecord, asRecordArray, asStringArray, textValue } from "../../app/shared";
import { WorkflowEmpty } from "./WorkflowProposalParts";
import { WorkflowPreviewReview } from "./WorkflowPreviewReview";
import { WorkflowProposalDetail } from "./WorkflowProposalDetail";
import { useWorkflowRequests } from "./useWorkflowRequests";
import { requestTitle } from "./workflowProposalModel";
import { startWorkflow, workflowStartActions } from "./workflowStartModel";
const WorkflowGraphWorkbench = lazy(() => import("./WorkflowGraphWorkbench"));

interface WorkflowProposalPageProps {
  actionReady: boolean;
  actionState: string;
  onAction: (
    action: string,
    payload: Record<string, unknown>,
  ) => Promise<ActionResponse>;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  projectId: string;
  recentEvents?: RecentEvent[];
  liveState?: string;
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
  recentEvents = [],
  liveState = "",
}: WorkflowProposalPageProps) {
  const [busyAction, setBusyAction] = useState("");
  const [feedback, setFeedback] = useState<ActionResponse | null>(null);
  const [agentOpen, setAgentOpen] = useState(false);
  const [agentAnchor, setAgentAnchor] = useState<HTMLDivElement | null>(null);
  const [panel, setPanel] = useState<"review" | "raw" | null>(null);
  const pageRoot = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = pageRoot.current;
    if (!element) return;
    let frame = 0;
    const resize = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const top = element.getBoundingClientRect().top;
        const bottom = Math.min(window.innerHeight - 12, element.parentElement?.getBoundingClientRect().bottom ?? window.innerHeight);
        const height = Math.max(0, bottom - top);
        element.style.setProperty("--workflow-page-height", `${height}px`);
      });
    };
    const observer = new ResizeObserver(resize);
    if (element.parentElement) observer.observe(element.parentElement);
    const workspace = element.closest(".workspace");
    if (workspace) observer.observe(workspace);
    window.addEventListener("resize", resize); resize();
    return () => { observer.disconnect(); window.removeEventListener("resize", resize); cancelAnimationFrame(frame); };
  }, []);
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

  async function start() {
    if (!detail || busyAction || reviewBusy.current || !actionReady) return;
    const requestId = selectedId;
    const isCurrent = () => mounted.current && actionSelection.current === requestId;
    reviewBusy.current = true;
    setBusyAction("workflow-start"); setFeedback(null);
    try {
      const result = await startWorkflow(detail, onAction);
      if (isCurrent()) setFeedback(result);
    } catch (reason) {
      if (isCurrent()) setFeedback({ ok: false, status: "failed", action: "workflow-submit", reason: String(reason) });
    } finally {
      if (isCurrent()) {
        reviewBusy.current = false; setBusyAction("");
        await loadRequests(); await loadDetail();
      }
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

  const current = detail?.result;
  const needsAnswers = current?.status === "clarifying";
  const effectiveConfig = asRecord(detail?.artifacts.effective_config);
  const yaml = textValue(effectiveConfig.yaml);
  const submitted = Boolean(detail?.lifecycle.submitted || detail?.links.run_id);

  function selectRequest(id: string) {
    if (reviewBusy.current) return;
    setPanel(null); setAgentOpen(false); setFeedback(null); setSelectedId(id);
    const url = new URL(location.href); url.searchParams.set("request", id); url.searchParams.delete("node");
    history.replaceState(null, "", url);
  }

  return <div ref={pageRoot} className="workflow-proposal-page workflow-graph-page" data-testid="workflow-proposal-page">
    <header className="workflow-proposal-toolbar">
      <h2>Workflow</h2>
      <label className="workflow-selector">
        <select aria-label="Select workflow" value={selectedId} disabled={Boolean(busyAction)} onChange={e => selectRequest(e.target.value)}>
          {!requests.length && <option value="">No workflows</option>}
          {requests.map(item => <option key={textValue(item.request_id)} value={textValue(item.request_id)}>{requestTitle(item)} · {textValue(item.selected_flow_family) || textValue(item.kind).toUpperCase()} · {textValue(item.status)} · R{textValue(item.revision)}</option>)}
        </select>
      </label>
      {needsAnswers && <button className="icon-button" type="button" disabled={Boolean(busyAction)} onClick={() => setPanel("review")}>Answer Questions</button>}
      <button aria-label="Refresh workflows" className="icon-button" type="button" disabled={Boolean(busyAction)} onClick={() => { void loadRequests(); void loadDetail(); }}><RefreshCw size={16} /></button>
    </header>
    {error && <div className="workflow-proposal-notice tone-error" role="alert">{error}</div>}
    {feedback && <div className={`workflow-proposal-notice ${feedback.ok ? "tone-ok" : "tone-error"}`} data-testid="workflow-proposal-feedback"><strong>{feedback.status}</strong><span>{feedback.reason || decisionFeedback(feedback)}</span></div>}
    {loading ? <WorkflowEmpty title="Loading workflows" /> : !requests.length ? <WorkflowEmpty title="No workflows" /> : !detail ? <WorkflowEmpty title="Loading workflow" /> :
      <WorkflowPreviewReview key={`${projectId}:${selectedId}`} detail={detail} projectId={projectId} disabled={!actionReady}
        anchor={agentAnchor} open={agentOpen} onOpen={() => setAgentOpen(true)} onClose={() => setAgentOpen(false)}
        onBusy={busy => { reviewBusy.current = busy; setBusyAction(current => busy ? "preview-review" : current === "preview-review" ? "" : current); }}
        onRefresh={async () => { await loadRequests(); await loadDetail(); }}>
      <div className={`workflow-graph-layout${panel ? " has-panel" : ""}`}>
        <main className="workflow-graph-main">
          <Suspense fallback={<p>Loading graph…</p>}>
            <WorkflowGraphWorkbench key={`${projectId}:${selectedId}`} projectId={projectId} requestId={selectedId}
              revision={`${detail.result?.revision}:${detail.result?.proposal_digest}`} onAction={onAction} actionReady={actionReady}
              recentEvents={recentEvents} liveState={liveState}
              onOpenRaw={() => setPanel("raw")} />
          </Suspense>
        </main>
        {panel && <aside className="workflow-review-drawer" aria-label={panel === "raw" ? "Raw YAML" : "Workflow Details"}>
          <header><div><h3>{panel === "raw" ? "Raw YAML" : "Workflow Details"}</h3><small>Revision {textValue(current?.revision)} · {submitted ? "Run snapshot" : "Preview"}</small></div>
            {panel === "review" && <button type="button" disabled={Boolean(busyAction)} onClick={() => setPanel("raw")}>Raw YAML</button>}
            <button type="button" aria-label="Close workflow panel" disabled={Boolean(busyAction)} onClick={() => { setPanel(null); setAgentOpen(false); }}><X size={16} /></button>
          </header>
            {panel === "raw" ? <section className="workflow-raw-yaml" data-testid="workflow-yaml-preview">
              <p>{effectiveConfig.yaml_source === "compiled_snapshot" ? "Compiled configuration snapshot · original YAML unavailable · secrets redacted" : "Version-bound configuration · secrets redacted"}</p>
              {yaml ? <pre tabIndex={0} data-review-field="yaml">{yaml}</pre> : <p>{submitted || effectiveConfig.status === "unreadable" ? "The bound configuration snapshot is unavailable." : "YAML will be available after clarification and workflow preparation."}</p>}
              {submitted && <p>This run uses an immutable snapshot. Changes require a new reviewed proposal.</p>}
              {!submitted && yaml && <button type="button" className="primary" data-testid="workflow-start"
                disabled={!actionReady || Boolean(busyAction) || !workflowStartActions(detail).length}
                title={!actionReady ? actionState : "Apply this exact configuration if needed, then start its workflow"}
                onClick={() => void start()}>{busyAction === "workflow-start" ? "Starting…" : "Start Workflow"}</button>}
              <button type="button" data-workflow-agent-trigger aria-expanded={agentOpen} onClick={() => setAgentOpen(!agentOpen)}>Agent</button>
              {(needsAnswers || submitted || !yaml) && <button type="button" disabled={Boolean(busyAction)} onClick={() => setPanel("review")}>{needsAnswers ? "Answer Questions" : submitted ? "Run Controls" : "Prepare Workflow"}</button>}
            </section> : <WorkflowProposalDetail actionReady={actionReady} actionState={actionState} busyAction={busyAction} detail={detail}
              onCancel={payload => void execute("workflow-cancel", payload)} onClarify={payload => void executeRequestTransition("workflow-clarify", payload)}
              onOpenRuns={runId => onOpenProjection("run", runId)} onPrepare={payload => void executeRequestTransition("workflow-prepare", payload)}
              onReject={payload => void execute("workflow-reject", payload)} onRunCancel={payload => void execute("run-cancel", payload)}
              onRunPause={payload => void execute("run-pause", payload)} onRunResume={payload => void execute("run-resume", payload)} />}
          <div ref={setAgentAnchor} className="workflow-agent-anchor" />
        </aside>}
      </div>
      </WorkflowPreviewReview>}
  </div>;
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
