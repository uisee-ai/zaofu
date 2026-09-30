import { useSettingsPreferences } from "../settings/useSettingsPreferences";
import { uiText } from "../settings/uiText";
import { useEffect, useRef, useState } from "react";
import { postAction } from "../api/client";
import type { IssueTriageItem } from "../api/types";
import type { IssueNotificationInput } from "./IssueTriageNotifications";
import { IssueIcon } from "./IssueIcon";
import { IssueDialog } from "./IssueOverlay";
import { IssueStateValue } from "./IssueStateBadge";
const PAUSABLE = new Set(['triaging', 'triage_recovering', 'fixing', 'fix_recovering', 'verifying']);
const PAUSED = new Set(['triage_paused', 'fix_paused']);
export function IssueTriageRunDialog({ open, projectId, issue, onClose, onNotification, onUpdated }: {
    open: boolean;
    projectId: string;
    issue: IssueTriageItem | null;
    onClose: () => void;
    onNotification: (notice: IssueNotificationInput) => void;
    onUpdated: (notice: string) => Promise<void>;
}) {
  useSettingsPreferences();
    const state = issue?.workflow?.state ?? '';
    const runId = state === 'triage_queued' ? '' : issue?.workflow?.run_id ?? '';
    const [busy, setBusy] = useState('');
    const [confirmed, setConfirmed] = useState(false);
    const [error, setError] = useState('');
    const inFlight = useRef(false);
    useEffect(() => { if (open) {
        setConfirmed(false);
        setError('');
    } }, [open, projectId, issue?.number, runId]);
    const apply = async (kind: 'pause' | 'resume' | 'cancel') => {
        if (!issue || !runId || inFlight.current || (kind === 'cancel' && !confirmed))
            return;
        if (kind === 'pause' && !PAUSABLE.has(state))
            return;
        if (kind === 'resume' && !PAUSED.has(state))
            return;
        inFlight.current = true;
        setBusy(kind);
        setError('');
        try {
            const result = await postAction(`run-${kind}`, { run_id: runId, reason: `Operator ${kind} for GitHub Issue #${issue.number}` }, projectId);
            if (!result.ok) {
                if (result.status === 'not_found' || result.status === 'invalid_transition') {
                    await onUpdated('');
                    const message = 'The Run changed. Review its latest state before trying again.';
                    onNotification({ title: 'Run state updated', message, tone: 'info' });
                    onClose();
                    return;
                }
                throw new Error(result.reason || `Unable to ${kind} this Run.`);
            }
            onClose();
            await onUpdated(kind === 'pause' ? `Pause requested for Issue #${issue.number}.` : kind === 'resume' ? `Run resumed for Issue #${issue.number}.` : `Run permanently cancelled for Issue #${issue.number}.`);
        }
        catch (cause) {
            const message = cause instanceof Error ? cause.message : String(cause);
            setError(message);
            onNotification({ title: 'Run action failed', message, tone: 'danger', persistent: true });
        }
        finally {
            inFlight.current = false;
            setBusy('');
        }
    };
    return <IssueDialog open={open} busy={Boolean(busy)} className="manage-dialog" labelledBy="issue-manage-title" describedBy="issue-manage-description" initialFocus="[data-manage-done]" onClose={onClose}>
    <header className="manage-dialog-head"><span className="manage-dialog-icon"><IssueIcon name="sliders" size="lg"/></span><div className="manage-dialog-title"><h3 id="issue-manage-title">{uiText("Manage Run")}</h3><p id="issue-manage-description">{uiText("Pause, resume, or permanently cancel the selected Run.")}</p></div><button className="dialog-close" type="button" disabled={Boolean(busy)} aria-label="Close dialog" onClick={onClose}><IssueIcon name="x"/></button></header>
    <div className="manage-dialog-body"><div className="manage-issue-card"><span className="cancel-issue-mark"><IssueIcon name="dot"/></span><div className="cancel-issue-copy"><strong>{issue?.title}</strong><span>Issue #{issue?.number}</span>{runId ? <code>{runId}</code> : null}</div><span>{state ? <IssueStateValue state={state}/> : null}</span></div>
      {PAUSED.has(state) ? <div className="manage-control-card"><strong>{uiText("Resume from checkpoint")}</strong><p>{uiText("Continue from the persisted Run state without repeating completed work.")}</p><button className="btn primary" type="button" disabled={Boolean(busy) || !runId} onClick={() => void apply('resume')}><IssueIcon name="play"/>{busy === 'resume' ? 'Resuming…' : 'Resume Run'}</button></div>
            : PAUSABLE.has(state) ? <div className="manage-control-card"><strong>{uiText("Pause after current dispatch")}</strong><p>{uiText("Pause blocks new dispatches. Already-dispatched work may finish.")}</p><button className="btn" type="button" disabled={Boolean(busy) || !runId} onClick={() => void apply('pause')}><IssueIcon name="pause"/>{busy === 'pause' ? 'Pausing…' : 'Pause Run'}</button></div> : null}
      <div className="manage-danger-card"><strong>{uiText("Permanent cancellation")}</strong><p>{uiText("This cannot be resumed. It does not roll back files already written to the local worktree.")}</p><label className="manage-confirm"><input type="checkbox" checked={confirmed} disabled={Boolean(busy) || !runId} onChange={event => setConfirmed(event.target.checked)}/>{uiText("I understand this Run is permanently cancelled and local files are not reverted.")}</label><button className="btn danger-solid" type="button" disabled={Boolean(busy) || !confirmed || !runId} onClick={() => void apply('cancel')}>{busy === 'cancel' ? <span className="button-spinner"/> : <IssueIcon name="xcircle"/>}{busy === 'cancel' ? 'Cancelling…' : 'Cancel permanently'}</button></div>
      {error ? <p className="field-error" role="alert">{error}</p> : null}
    </div><footer className="manage-dialog-actions"><button className="btn" type="button" data-manage-done disabled={Boolean(busy)} onClick={onClose}>{uiText("Done")}</button></footer>
  </IssueDialog>;
}
