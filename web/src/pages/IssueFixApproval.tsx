import { useSettingsPreferences } from "../settings/useSettingsPreferences";
import { uiText } from "../settings/uiText";
import { useCallback, useEffect, useState } from "react";
import { getKanbanPendingProposals, invalidateProjectReadCache, postAction, type PendingKanbanProposal } from "../api/client";
import type { IssueTriageItem } from "../api/types";
import type { IssueNotificationInput } from "./IssueTriageNotifications";
import { IssueContextFacts } from "./IssueContextParts";
import { IssueIcon } from "./IssueIcon";
/** Resolve a real, identity-bound pending proposal; never synthesize an approval. */
export function IssueFixApproval({ projectId, issue, onUpdated, onNotification, onOpenTask }: {
    projectId: string;
    issue: IssueTriageItem;
    onUpdated: (message: string) => Promise<void>;
    onNotification: (notice: IssueNotificationInput) => void;
    onOpenTask: (task: string) => void;
}) {
  useSettingsPreferences();
    const [proposal, setProposal] = useState<PendingKanbanProposal | null>(null);
    const [busy, setBusy] = useState(false);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [reloadToken, setReloadToken] = useState(0);
    const load = useCallback(async (activeRef: { current: boolean }) => {
        setLoading(true);
        setLoadError(null);
        try {
            const data = await getKanbanPendingProposals(projectId);
            if (!activeRef.current)
                return;
            const current = data.items.find(item => item.proposal_id === issue.workflow?.proposal_id && item.payload.task_id === issue.workflow?.task_id && item.valid);
            setProposal(current ?? null);
        }
        catch (cause) {
            if (activeRef.current) {
                setProposal(null);
                setLoadError(cause instanceof Error ? cause.message : 'The current proposal could not be loaded.');
            }
        }
        finally {
            if (activeRef.current)
                setLoading(false);
        }
    }, [projectId, issue.workflow?.proposal_id, issue.workflow?.task_id]);
    useEffect(() => {
        const active = { current: true };
        setProposal(null);
        void load(active);
        return () => { active.current = false; };
    }, [issue.number, load, reloadToken]);
    const apply = async (reject: boolean) => {
        if (!proposal || busy)
            return;
        setBusy(true);
        try {
            // Re-resolve immediately before execution so a superseded proposal cannot be submitted.
            invalidateProjectReadCache(projectId);
            const latest = (await getKanbanPendingProposals(projectId)).items.find(item => item.proposal_event_id === proposal.proposal_event_id && item.proposal_digest === proposal.proposal_digest && item.valid);
            if (!latest)
                throw new Error('The proposal changed. Refresh before reviewing.');
            const result = reject ? await postAction('kanban-proposal-dismiss', { project_id: projectId, proposal_event_id: latest.proposal_event_id, reason: `Owner rejected the Fix proposal for Issue #${issue.number}` }, projectId)
                : await postAction(latest.action, { ...latest.payload, project_id: projectId, proposal_event_id: latest.proposal_event_id }, projectId);
            if (!result.ok)
                throw new Error(result.reason || 'The proposal could not be reviewed.');
            await onUpdated(reject ? 'Fix proposal rejected.' : 'Fix proposal approved and queued.');
        }
        catch (cause) {
            onNotification({ title: 'Proposal review failed', message: cause instanceof Error ? cause.message : String(cause), tone: 'danger' });
        }
        finally {
            setBusy(false);
        }
    };
    const allowed = proposal?.payload.allowed_paths;
    const unavailableReason = loading
        ? 'Resolving the exact pending proposal…'
        : loadError
            ? `Proposal lookup failed: ${loadError}`
            : 'No executable current proposal is available yet. Open the Task or refresh this card.';
    return <><IssueContextFacts entries={[["Proposal", issue.workflow?.proposal_id, true], ["Allowed paths", Array.isArray(allowed) ? allowed.join(', ') : '—']]}/>{loading || loadError || !proposal ? <p className="context-note" role={loadError ? 'alert' : undefined}>{unavailableReason}</p> : null}<div className="context-actions"><button className="btn primary" type="button" disabled={!proposal || loading || busy} data-testid="issue-fix-approve" data-tooltip={!proposal ? unavailableReason : undefined} onClick={() => void apply(false)}><IssueIcon name="check"/>{busy ? 'Working…' : 'Approve and queue Fix'}</button><button className="btn danger" type="button" disabled={!proposal || loading || busy} data-testid="issue-fix-reject" onClick={() => void apply(true)}><IssueIcon name="xcircle"/>{uiText("Reject proposal")}</button>{issue.workflow?.task_id ? <button className="btn" type="button" disabled={busy} data-testid="issue-fix-open-proposal" onClick={() => onOpenTask(issue.workflow!.task_id)}><IssueIcon name="external"/>{uiText("Open proposal")}</button> : null}{!loading ? <button className="btn" type="button" disabled={busy} data-testid="issue-fix-refresh" onClick={() => setReloadToken(value => value + 1)}><IssueIcon name="refresh"/>{uiText("Refresh proposal")}</button> : null}</div></>;
}
