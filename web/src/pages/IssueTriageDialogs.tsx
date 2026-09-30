import { uiText } from "../settings/uiText";
import type { IssueTriageItem } from "../api/types";
import { IssueDialog } from "./IssueOverlay";
import { IssueIcon } from "./IssueIcon";
import { IssueStateValue } from "./IssueStateBadge";
export function StartTriageDialog({ open, error, reference, issue, busy, repository, onChange, onClose, onSubmit }: {
    open: boolean;
    error: string;
    reference: string;
    issue: IssueTriageItem | null;
    busy: boolean;
    repository: string;
    onChange: (value: string) => void;
    onClose: () => void;
    onSubmit: () => void;
}) {
    return <IssueDialog open={open} busy={busy} className="start-dialog" labelledBy="issue-start-title" describedBy="issue-start-description" initialFocus={issue ? '[data-cancel]' : 'input'} onClose={onClose}>
    <header className="start-dialog-head"><span className="start-dialog-icon"><IssueIcon name="play" size="lg"/></span><div className="start-dialog-title"><h3 id="issue-start-title">{uiText("Start Triage")}</h3><p id="issue-start-description">{issue ? uiText('Queue the selected GitHub Issue for read-only triage.') : uiText('Queue a GitHub Issue for read-only triage.')}</p></div><button className="dialog-close" type="button" aria-label="Close Start Triage" disabled={busy} onClick={onClose}><IssueIcon name="x"/></button></header>
    <form onSubmit={event => { event.preventDefault(); if (!busy && reference.trim())
        onSubmit(); }}>
      <div className="start-dialog-body">{issue ? <div><span className="dialog-section-label">{uiText("Selected Issue")}</span><div className="cancel-issue-card start-issue-card"><span className="cancel-issue-mark"><IssueIcon name="dot"/></span><div className="cancel-issue-copy"><strong data-tooltip={issue.title}>{issue.title}</strong><span>Issue #{issue.number}</span></div><span><IssueStateValue state={issue.workflow?.state || issue.github_state}/></span></div></div> : <label className="start-input-field"><span>{uiText("GitHub Issue URL or number")}</span><input type="text" autoComplete="off" aria-label={uiText("GitHub Issue URL or number")} placeholder={`Example: #32 or https://github.com/${repository || 'owner/repository'}/issues/32`} value={reference} disabled={busy} onChange={event => onChange(event.target.value)}/><small className="start-field-help">{uiText("Enter one Issue number or a full GitHub Issue URL.")}</small></label>}
      {error ? <p className="issue-error" role="alert">{error}</p> : null}</div>
      <footer className="start-dialog-actions"><button data-cancel className="btn" type="button" disabled={busy} onClick={onClose}>{uiText("Cancel")}</button><button className="btn primary" type="submit" disabled={busy || !reference.trim()}>{busy ? <span className="button-spinner"/> : <IssueIcon name="play"/>}<span>{busy ? uiText('Queueing…') : uiText('Queue Triage')}</span></button></footer>
    </form>
  </IssueDialog>;
}
export function CancelQueuedDialog({ open, issue, busy, onClose, onConfirm }: {
    open: boolean;
    issue: IssueTriageItem | null;
    busy: boolean;
    onClose: () => void;
    onConfirm: () => void;
}) {
    const noun = issue?.workflow?.state === 'fix_queued' ? 'Fix' : 'Triage';
    return <IssueDialog open={open} busy={busy} className="cancel-dialog" labelledBy="issue-cancel-title" describedBy="issue-cancel-description" role="alertdialog" initialFocus="[data-keep-queued]" onClose={onClose}>
    <header className="cancel-dialog-head"><span className="cancel-dialog-icon"><IssueIcon name="xcircle" size="lg"/></span><div className="cancel-dialog-title"><h3 id="issue-cancel-title">{uiText("Cancel queued ")}{noun}?</h3><p id="issue-cancel-description">This Issue will be removed from the {noun} queue.</p></div><button className="dialog-close" aria-label="Close cancellation dialog" type="button" disabled={busy} onClick={onClose}><IssueIcon name="x"/></button></header>
    <div className="cancel-dialog-body"><div className="cancel-issue-card"><span className="cancel-issue-mark"><IssueIcon name="dot"/></span><div className="cancel-issue-copy"><strong data-tooltip={issue?.title}>{issue?.title}</strong><span>Issue #{issue?.number}</span>{issue?.workflow?.triage_id ? <code>{issue.workflow.triage_id}</code> : null}</div><div className="cancel-issue-state"><IssueStateValue state={issue?.workflow?.state || 'triage_queued'}/></div></div><p className="cancel-impact"><IssueIcon name="info"/><span>This only cancels the queued ZaoFu {noun} request. It does not close or edit the GitHub Issue, and you can queue it again later.</span></p></div>
    <footer className="cancel-dialog-actions"><button data-keep-queued className="btn" type="button" disabled={busy} onClick={onClose}>{uiText("Keep queued")}</button><button className="btn danger-solid" type="button" disabled={busy} onClick={onConfirm}>{busy ? <span className="button-spinner"/> : <IssueIcon name="xcircle"/>}<span>{busy ? uiText('Cancelling…') : `Cancel queued ${noun}`}</span></button></footer>
  </IssueDialog>;
}
