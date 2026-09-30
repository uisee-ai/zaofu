import { useSettingsPreferences } from "../settings/useSettingsPreferences";
import { uiText } from "../settings/uiText";
import { useEffect, useState } from "react";
import { formatSettingsTime } from '../settings/useSettingsPreferences';
import { prepareIssueCandidatePublication, recordIssueCandidatePullRequest, refreshIssueCandidatePullRequest, reviewIssueCandidate } from "../api/client";
import type { IssueCandidateDeliveryProjection } from "../api/types";
import type { IssueNotificationInput } from "./IssueTriageNotifications";
import { IssueContextFacts } from "./IssueContextParts";
import { IssueIcon } from "./IssueIcon";
/** Real delivery actions rendered inside the current state card, without a second card. */
export function IssueCandidateDeliveryPanel({ projectId, issueNumber, state, githubState, delivery, onNotification, onUpdated, onOpenEvidence }: {
    projectId: string;
    issueNumber: number;
    state: string;
    githubState: string;
    delivery?: IssueCandidateDeliveryProjection;
    onNotification: (input: IssueNotificationInput) => void;
    onUpdated: (notice: string) => Promise<void>;
    onOpenEvidence: () => void;
}) {
  useSettingsPreferences();
    const candidate = delivery?.candidate;
    const handoff = delivery?.handoff;
    const pr = handoff?.pull_request;
    const [reason, setReason] = useState('');
    const [url, setUrl] = useState('');
    const [busy, setBusy] = useState('');
    const [error, setError] = useState('');
    useEffect(() => { setReason(''); setUrl(''); setError(''); }, [projectId, issueNumber, candidate?.candidate_head_sha, candidate?.source_revision]);
    const enabled = Boolean(delivery?.enabled && candidate && delivery.status !== 'stale');
    const apply = async (action: string) => {
        if (busy || !enabled || !candidate)
            return;
        setBusy(action);
        setError('');
        try {
            const identity = { candidate_sha: candidate.candidate_head_sha, source_revision: candidate.source_revision };
            const response = action === 'prepare' ? await prepareIssueCandidatePublication(projectId, issueNumber, identity)
                : action === 'record' ? await recordIssueCandidatePullRequest(projectId, issueNumber, url.trim())
                    : action === 'refresh' ? await refreshIssueCandidatePullRequest(projectId, issueNumber)
                        : await reviewIssueCandidate(projectId, issueNumber, { ...identity, verdict: action as 'approve' | 'changes_requested' | 'reject', reason });
            if (!response.ok)
                throw new Error(response.reason || 'The operation was not accepted.');
            await onUpdated(action === 'prepare' ? 'Local review branch prepared.' : action === 'record' ? 'Pull request identity verified and recorded.' : action === 'refresh' ? 'Pull request status refreshed.' : 'Owner review recorded for this candidate.');
            if (action === 'record')
                setUrl('');
        }
        catch (cause) {
            const message = cause instanceof Error ? cause.message : String(cause);
            setError(message);
            onNotification({ title: 'Delivery action failed', message, tone: 'danger', persistent: true });
        }
        finally {
            setBusy('');
        }
    };
    const short = (value?: string) => value ? value.slice(0, 12) : '—';
    const button = (action: string, label: string, icon: 'check' | 'warn' | 'xcircle' | 'pr' | 'refresh', tone = '', valid = true) => <button className={`btn ${tone}`} type="button" disabled={!enabled || Boolean(busy) || !valid} onClick={() => void apply(action)} data-tooltip={!enabled ? (delivery?.stale_reason || 'A current verified candidate is required.') : undefined}><IssueIcon name={icon} className={busy === action && action === 'refresh' ? 'spinning' : ''}/>{busy === action ? 'Working…' : label}</button>;
    const openPR = (label: string) => pr?.url ? <a className="btn" href={pr.url} target="_blank" rel="noopener noreferrer"><IssueIcon name="external"/>{label}</a> : <button className="btn" type="button" disabled data-tooltip="No pull request has been recorded."><IssueIcon name="external"/>{label}</button>;
    let body;
    switch (state) {
        case 'verified_candidate':
            body = <><IssueContextFacts entries={[["Candidate", short(candidate?.candidate_head_sha), true], ["Base", short(candidate?.candidate_base_sha), true], ["Target", delivery ? `${delivery.configured_repository}:${delivery.configured_base_branch}` : '—'], ["Merge", 'Human-controlled squash']]}/><label className="context-field"><span>{uiText("Owner review note")}</span><textarea value={reason} placeholder="Record review reasoning or requested changes" onChange={event => setReason(event.target.value)}/></label><div className="context-actions">{button('approve', 'Approve for PR', 'check', 'primary')}{button('changes_requested', 'Request changes', 'warn', '', Boolean(reason.trim()))}{button('reject', 'Reject', 'xcircle', 'danger', Boolean(reason.trim()))}</div></>;
            break;
        case 'approved_for_pr':
            body = <><IssueContextFacts entries={[["Candidate", short(candidate?.candidate_head_sha), true], ["Approval", handoff?.owner_review?.reviewed_at || '—']]}/><div className="context-actions">{button('prepare', 'Prepare local review branch', 'pr', 'primary')}</div><p className="context-note">ZaoFu creates only an immutable local review branch. Push and PR creation remain human-controlled.</p></>;
            break;
        case 'owner_changes_requested':
        case 'owner_rejected':
            body = <><IssueContextFacts entries={[["Candidate", short(candidate?.candidate_head_sha), true], [state === 'owner_rejected' ? 'Verdict' : 'Review', state === 'owner_rejected' ? 'Rejected' : 'Changes requested']]}/>{handoff?.owner_review?.reason ? <p className="context-note">{handoff.owner_review.reason}</p> : null}{state === 'owner_changes_requested' ? <div className="context-actions"><button className="btn" type="button" onClick={onOpenEvidence}><IssueIcon name="external"/>Open review receipt</button></div> : null}</>;
            break;
        case 'publication_prepared':
            body = <><IssueContextFacts entries={[["Review branch", handoff?.review_branch, true], ["Candidate", short(candidate?.candidate_head_sha), true]]}/>{handoff?.human_commands?.push ? <Command label="Push" value={handoff.human_commands.push}/> : null}{handoff?.human_commands?.create_pr ? <Command label="Open PR" value={handoff.human_commands.create_pr}/> : null}<label className="context-field"><span>Created GitHub PR URL</span><input value={url} placeholder={`https://github.com/${delivery?.configured_repository || 'owner/repository'}/pull/123`} onChange={event => setUrl(event.target.value)}/></label><div className="context-actions">{button('record', 'Record PR', 'pr', 'primary', Boolean(url.trim()))}</div></>;
            break;
        case 'pr_open':
            body = <><IssueContextFacts entries={[["Pull request", pr ? `#${pr.number} · open` : '—'], ["Review", pr ? `${pr.review_status} (${pr.review_count})` : '—'], ["Base", delivery?.configured_base_branch], ["Head", handoff?.review_branch]]}/><div className="context-actions">{openPR('Open PR')}{button('refresh', 'Refresh PR', 'refresh')}</div></>;
            break;
        case 'pr_changes_requested':
            body = <><IssueContextFacts entries={[["Pull request", pr ? `#${pr.number}` : '—'], ["GitHub review", pr ? `Changes requested (${pr.review_count})` : 'Changes requested']]}/><p className="context-note">The existing Owner approval remains an audit receipt for the old candidate; it does not authorize a changed SHA.</p><div className="context-actions">{openPR('Open PR review')}{button('refresh', 'Refresh PR', 'refresh')}</div></>;
            break;
        case 'pr_approved':
            body = <><IssueContextFacts entries={[["Pull request", pr ? `#${pr.number}` : '—'], ["GitHub review", pr ? `Approved (${pr.review_count})` : 'Approved'], ["Mergeability", '—'], ["Next action", 'Human merge']]}/><div className="context-actions">{openPR('Open approved PR')}{button('refresh', 'Refresh PR', 'refresh')}</div></>;
            break;
        case 'pr_closed_without_merge':
            body = <><IssueContextFacts entries={[["Pull request", pr ? `#${pr.number} · closed` : '—'], ["Merged", 'No']]}/><p className="context-note">A new publication handoff requires an explicit maintainer decision and current candidate identity.</p></>;
            break;
        case 'merged':
            body = <><IssueContextFacts entries={[["Pull request", pr ? `#${pr.number}` : '—'], ["Merged at", formatSettingsTime(pr?.merged_at)], ["Issue state", githubState === 'open' ? 'Open' : 'Closed'], ["Automatic close", 'Disabled']]}/><p className="context-note">The source Issue is not closed or commented on automatically.</p></>;
            break;
        default: body = null;
    }
    return <>{body}{delivery?.status === 'stale' ? <p className="issue-error" role="status">{delivery.stale_reason || 'Candidate changed. Reverify before delivery.'}</p> : null}{error ? <p className="issue-error" role="alert">{error}</p> : null}{candidate ? <details className="review-evidence"><summary>{uiText("Review evidence")}</summary><div><Evidence title="Changed paths" values={candidate.changed_paths}/><Evidence title="Passed gates" values={candidate.quality_gates_passed}/><Evidence title="Failed gates" values={candidate.quality_gates_failed}/><Evidence title="Verification commands" values={candidate.verification_commands}/><Evidence title="Unresolved risks" values={candidate.unresolved_risks}/></div></details> : null}</>;
}
function Command({ label, value }: {
    label: string;
    value: string;
}) { return <div className="context-command"><span>{label}</span><code>{value}</code></div>; }
function Evidence({ title, values }: {
    title: string;
    values: string[];
}) { return values?.length ? <div><strong>{title}</strong><ul>{values.map((value, i) => <li key={`${i}:${value}`}>{value}</li>)}</ul></div> : null; }
