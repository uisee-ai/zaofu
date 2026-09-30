import { uiText } from "../settings/uiText";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import type { IssueTriageItem, IssueCandidateDeliveryProjection } from "../api/types";
import { effectiveIssueState, issueStateLabel, issueStateStage, issueStateDescription, issueTriageSourceLabel } from "./issueTriageModel";
import { IssueIcon, type IssueIconName } from "./IssueIcon";
import { ISSUE_STATE_COPY, issueStateTone } from "./issueStatePresentation";
import { IssueContextFacts, IssueEvidenceList, IssueProgress } from "./IssueContextParts";
import { IssueLifecycle } from "./IssueLifecycle";
import { IssueNeedsInfo } from "./IssueNeedsInfo";
import { IssueCandidateDeliveryPanel } from "./IssueCandidateDeliveryPanel";
import { IssueFixApproval } from "./IssueFixApproval";
import type { IssueNotificationInput } from "./IssueTriageNotifications";
import type { IssueNavigation } from "./IssueTriagePage";
const STAGE_ICON: Record<string, IssueIconName> = { repository: 'clock', triage: 'dot', decision: 'warn', fix: 'wrench', delivery: 'pr', complete: 'shield', stopped: 'xcircle' };
type Props = IssueNavigation & {
    projectId: string;
    issue: IssueTriageItem;
    delivery?: IssueCandidateDeliveryProjection;
    onUpdated: (message: string) => Promise<void>;
    onNotification: (notice: IssueNotificationInput) => void;
};
function StateBody({ projectId, issue, delivery, onUpdated, onNotification, onOpenTask, onOpenProjection }: Props) {
    const state = effectiveIssueState(issue);
    const workflow = issue.workflow;
    const runId = workflow?.run_id || '';
    const triage = workflow?.triage_id || '';
    const attempt = workflow?.triage_history?.find(entry => entry.triage_id === triage)?.attempt;
    const source = delivery?.candidate;
    const facts = (entries: Array<[
        string,
        ReactNode,
        boolean?
    ]>) => <IssueContextFacts entries={entries}/>;
    const note = (text: string) => <p className="context-note">{text}</p>;
    const trace = (text: string, task = false) => <div className="context-actions"><button className="btn" type="button" disabled={task ? !workflow?.task_id : !runId && !triage} data-tooltip={task ? 'Open the Task and its evidence.' : 'Open the recorded execution trace.'} onClick={() => task ? onOpenTask(workflow!.task_id) : onOpenProjection('trace', runId || triage)}><IssueIcon name="external"/>{text}</button></div>;
    const key = `${projectId}:${issue.issue_key}:${triage}:${workflow?.needs_info?.source_revision}:${workflow?.needs_info?.round}`;
    if (['triage_queued', 'fix_queued'].includes(state) && (!triage || workflow?.queued_cancel_available === false)) {
        return <>{facts([['Queue state', 'Synced from GitHub'], ['Local queued request', 'Not available']])}{note('There is no cancellable queued request in this project. Refresh or switch to the project that queued this Issue; a GitHub label alone cannot identify a local queue entry.')}</>;
    }
    switch (state) {
        case 'open': return <>{facts([['Repository state', uiText('Open')], [uiText('Workflow'), uiText('None')]])}{note('Select Start Triage to admit this exact Issue revision into the read-only Triage workflow.')}</>;
        case 'closed': return <>{facts([['Repository state', uiText('Closed')], [uiText('Workflow'), uiText('None')]])}{note('Closing on GitHub does not delete the local mirror or its audit history.')}</>;
        case 'mirrored': return <>{facts([['Mirror source', issueTriageSourceLabel(issue.source)], ['Current revision', workflow?.source_revision || 'Observed']])}{note('Manual Start Triage can bypass the activation-time boundary while preserving repository and revision checks.')}</>;
        case 'triage_queued': return <>{facts([['Triage', triage, true], ['Admission', 'Queued'], [uiText('Workflow'), workflow?.workflow_id || 'external-issue-triage'], ['Attempt', attempt || '—']])}{note('A Run ID is assigned only after admission succeeds and the Issue enters Triaging. Cancel queued Triage removes this request from the queue without changing the GitHub Issue.')}</>;
        case 'triaging': return <><IssueProgress label="Evidence collection and proposal synthesis"/>{facts([['Run', runId, true], ['Current step', '—']])}{trace('Open Run trace')}</>;
        case 'triage_paused': return <>{facts([['Run', runId, true], ['Checkpoint', '—']])}{note('Use Manage Run to resume from the checkpoint or permanently cancel the Run.')}</>;
        case 'triage_recovering': return <><IssueProgress label="Replaying durable events and restoring dispatch"/>{facts([['Run', runId, true], ['Recovery reason', workflow?.failure_reason || '—']])}</>;
        case 'needs_info': return workflow?.needs_info ? <IssueNeedsInfo key={key} issue={issue} needsInfo={workflow.needs_info}/> : <>{facts([['Request', triage, true], ['Questions', 'Not recorded']])}{trace('Open Triage evidence')}</>;
        case 'needs_info_exhausted': return <>{facts([['Rounds used', workflow?.needs_info ? `${workflow.needs_info.round} of ${workflow.needs_info.max_rounds}` : '—'], ['Required action', 'Maintainer decision']])}{workflow?.needs_info?.reason ? note(workflow.needs_info.reason) : null}{note('No further questions will be sent automatically. A maintainer must revise the Issue, change the intake scope, or explicitly restart Triage.')}</>;
        case 'unverified_candidate': return <><IssueEvidenceList entries={[...(workflow?.verification_gaps || []).map(text => ({ text, passed: false })), ...(source?.quality_gates_passed || []).map(text => ({ text, passed: true }))]}/>{!workflow?.verification_gaps?.length && !source?.quality_gates_passed?.length ? facts([['Verification', workflow?.verification_status || 'Not recorded'], ['Candidate', source?.candidate_head_sha, true]]) : null}{note('The reporter’s product environment is not required; the maintainer decides whether the remaining evidence is acceptable.')}{trace('Review evidence', true)}</>;
        case 'awaiting_fix_approval': return <IssueFixApproval key={`${projectId}:${issue.issue_key}:${workflow?.proposal_id}`} projectId={projectId} issue={issue} onOpenTask={onOpenTask} onUpdated={onUpdated} onNotification={onNotification}/>;
        case 'fix_queued': return <>{facts([['Run', runId, true], ['Approved proposal', workflow?.proposal_id, true]])}{note('Cancel queued Fix removes this Run from the queue; it does not revoke the historical approval receipt.')}</>;
        case 'fixing': return <><IssueProgress label="Implementing and checking the approved change"/>{facts([['Run', runId, true], ['Worktree', source?.candidate_ref, true]])}{trace('Open worktree evidence', true)}</>;
        case 'fix_paused': return <>{facts([['Run', runId, true], ['Checkpoint', '—']])}{note('Use Manage Run to resume. Permanent cancellation does not revert files already written in the worktree.')}</>;
        case 'fix_recovering': return <><IssueProgress label="Restoring the approved write scope"/>{facts([['Run', runId, true], ['Recovery reason', workflow?.failure_reason || '—']])}</>;
        case 'verifying': return <><IssueProgress label="Running quality gates against the exact candidate"/><IssueEvidenceList entries={[...(source?.quality_gates_passed || []).map(text => ({ text, passed: true })), ...(source?.quality_gates_failed || []).map(text => ({ text, passed: false }))]}/>{facts([['Candidate', source?.candidate_head_sha, true], ['Run', runId, true]])}{trace('Open verification evidence', true)}</>;
        case 'verified_candidate':
        case 'approved_for_pr':
        case 'owner_changes_requested':
        case 'owner_rejected':
        case 'publication_prepared':
        case 'pr_open':
        case 'pr_changes_requested':
        case 'pr_approved':
        case 'pr_closed_without_merge':
        case 'merged':
            return delivery ? <IssueCandidateDeliveryPanel key={`${projectId}:${issue.issue_key}:${source?.candidate_head_sha}:${source?.source_revision}`} projectId={projectId} issueNumber={issue.number} state={state} githubState={issue.github_state} delivery={delivery} onOpenEvidence={() => workflow?.task_id && onOpenTask(workflow.task_id)} onNotification={onNotification} onUpdated={onUpdated}/> : <>{facts([['Candidate', '—'], ['Pull request', '—']])}{trace('Open delivery evidence', true)}</>;
        case 'triage_blocked': return <>{facts([['Diagnostic', workflow?.failure_reason], ['Disposition', 'Maintainer intervention']])}{trace('Open diagnostics')}</>;
        case 'triage_cancelled': return <>{facts([['Triage', triage, true], ['Resume', 'Not available']])}{note('The Issue may be admitted again later as a new Run for the current source revision.')}</>;
        case 'fix_blocked': return <>{facts([['Gate', workflow?.failure_event], ['Reason', workflow?.failure_reason]])}{trace('Inspect gate evidence', true)}</>;
        case 'fix_cancelled': return <>{facts([['Run', runId || 'Historical record retained', true], ['Worktree', 'Files are not auto-reverted']])}</>;
        case 'blocked': return <>{facts([['Gate', workflow?.failure_event], ['Reason', workflow?.failure_reason]])}{trace('Inspect gate evidence')}</>;
        case 'failed': return <>{facts([['Attempts', attempt || '—'], ['Last error', workflow?.failure_reason]])}{trace('Open failure trace')}</>;
        default: return facts([['State', issueStateLabel(state)], ['Triage', triage, true]]);
    }
}
export function IssueStatusContext(props: Props) {
    const { issue } = props;
    const [back, setBack] = useState(false);
    const wrapper = useRef<HTMLDivElement>(null);
    const timer = useRef<ReturnType<typeof setTimeout>>();
    useEffect(() => { setBack(false); return () => clearTimeout(timer.current); }, [issue.issue_key, issue.number]);
    const state = effectiveIssueState(issue);
    const stage = issueStateStage(state).id;
    const copy = ISSUE_STATE_COPY[state] || { title: issueStateLabel(state), description: '', icon: STAGE_ICON[stage] };
    const flip = (value: boolean) => { setBack(value); clearTimeout(timer.current); timer.current = setTimeout(() => wrapper.current?.querySelector<HTMLButtonElement>(`[data-context-face="${value ? 'front' : 'back'}"]`)?.focus({ preventScroll: true }), 310); };
    return <div ref={wrapper} className={`context-flip${back ? ' is-flipped' : ''}`} data-context-flip>
    <div className="context-flip-inner"><div className="context-face context-face-front" aria-hidden={back} ref={node => { if (node)
        node.inert = back; }}>
      <section className={`context-card ${stage === 'repository' ? 'neutral' : stage}`}><div className="context-head"><div className="context-title"><IssueIcon name={copy.icon} size="lg"/><div><h4>{copy.title}</h4><p>{state === 'fix_blocked' ? 'The Fix Run is blocked by a recorded gate or runtime condition.' : copy.description}</p></div></div><div className="context-head-actions"><span className={`context-status signal-${issueStateTone(state)}`} data-tooltip={issueStateDescription(state)}><IssueIcon name={STAGE_ICON[stage]}/>{issueStateLabel(state)}</span><button className="context-flip-button" type="button" aria-label="View Issue lifecycle" data-context-face="back" data-tooltip="View this Issue’s lifecycle history." onClick={() => flip(true)}><IssueIcon name="route"/></button></div></div>
        <StateBody {...props}/>
      </section></div><div className="context-face context-face-back" aria-hidden={!back} ref={node => { if (node)
        node.inert = !back; }}><IssueLifecycle issue={issue} onFront={() => flip(false)}/></div>
  </div></div>;
}
