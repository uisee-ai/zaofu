import { uiText } from "../settings/uiText";
import { useSettingsPreferences, formatSettingsTime } from "../settings/useSettingsPreferences";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { IssueTriageItem } from "../api/types";
import { IssueIcon } from "./IssueIcon";
import { IssuePortal } from "./IssueOverlay";
import { issueStateLabel, issueStateStage } from "./issueTriageModel";
export function IssueTriageHistory({ issue, onOpen }: {
    issue: IssueTriageItem;
    onOpen: (triageId: string) => void;
}) {
    const { historyCount } = useSettingsPreferences();
    const host = useRef<HTMLDivElement>(null);
    const trigger = useRef<HTMLButtonElement>(null);
    const popover = useRef<HTMLElement>(null);
    const [open, setOpen] = useState(false);
    const [position, setPosition] = useState({ left: 12, top: 12, width: 480, maxHeight: 340 });
    const history = useMemo(() => {
        const entries = [...(issue.workflow?.triage_history ?? [])];
        // A known ID can be shown even when the server did not include its history;
        // do not invent an attempt number, queued time or retry relationship.
        if (issue.workflow?.triage_id && !entries.some(entry => entry.triage_id === issue.workflow?.triage_id))
            entries.unshift({ triage_id: issue.workflow.triage_id, source_revision: issue.workflow.source_revision, attempt: 0, state: issue.workflow.state, queued_at: '', retry_of: '' });
        return [...new Map(entries.map(entry => [entry.triage_id, entry])).values()].sort((a, b) => {
            if (a.triage_id === issue.workflow?.triage_id)
                return -1;
            if (b.triage_id === issue.workflow?.triage_id)
                return 1;
            return b.attempt - a.attempt || b.queued_at.localeCompare(a.queued_at);
        });
    }, [issue.workflow]);
    useEffect(() => { setOpen(false); }, [issue.issue_key, issue.number]);
    const place = useCallback(() => {
        const box = host.current?.closest('.triage-meta-card')?.getBoundingClientRect() ?? host.current?.getBoundingClientRect();
        if (!box)
            return;
        const width = Math.min(box.width, window.innerWidth - 24);
        const below = window.innerHeight - box.bottom - 18;
        const above = box.top - 18;
        const useAbove = below < 190 && above > below;
        const maxHeight = Math.min(360, Math.max(100, useAbove ? above : below));
        setPosition({ left: Math.max(12, Math.min(window.innerWidth - width - 12, box.left)), top: useAbove ? box.top - maxHeight - 6 : box.bottom + 6, width, maxHeight });
    }, []);
    useEffect(() => {
        if (!open)
            return;
        place();
        const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') {
            setOpen(false);
            trigger.current?.focus();
        } };
        const outside = (event: PointerEvent) => { if (!host.current?.contains(event.target as Node) && !popover.current?.contains(event.target as Node))
            setOpen(false); };
        window.addEventListener('resize', place);
        window.addEventListener('scroll', place, true);
        document.addEventListener('keydown', escape);
        document.addEventListener('pointerdown', outside);
        return () => { window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); document.removeEventListener('keydown', escape); document.removeEventListener('pointerdown', outside); };
    }, [open, place]);
    if (!history.length)
        return <>{uiText("None")}</>;
    return <div ref={host} className="triage-history-shell">
    <div className="triage-history-preview">{history.slice(0, historyCount).map(entry => <div className={`triage-history-row ${entry.triage_id === issue.workflow?.triage_id ? 'current' : ''}`} key={entry.triage_id}><span className="triage-history-row-main"><button className="id-link" data-tooltip="Open Triage history." type="button" onClick={() => onOpen(entry.triage_id)}>{entry.triage_id}</button></span><small>{issueStateLabel(entry.state)}{entry.attempt > 0 ? ` · attempt ${entry.attempt}` : ''}</small></div>)}</div>
    {history.length > historyCount ? <button className="triage-history-toggle" ref={trigger} type="button" aria-expanded={open} aria-controls="issue-triage-history" data-tooltip={open ? 'Hide older records.' : 'View older Triage records.'} onClick={() => setOpen(value => !value)}>{open ? 'Hide history' : `View ${history.length - historyCount} more`}<IssueIcon name="chevron" className={open ? 'rotated' : ''}/></button> : null}
    {open && history.length > historyCount ? <IssuePortal><div className="issue-history-portal" style={position}><section ref={popover} id="issue-triage-history" className="triage-history-popover open" aria-label="Triage history" role="dialog"><header className="triage-history-popover-head"><div className="triage-history-popover-title"><strong>Triage history</strong><span>{history.length} records · newest first</span></div><button className="triage-history-close" type="button" aria-label="Close Triage history" onClick={() => { setOpen(false); trigger.current?.focus(); }}><IssueIcon name="x"/></button></header><div className="triage-history-popover-body">{history.map(entry => { const stage = issueStateStage(entry.state).id; return <article key={entry.triage_id} className={`triage-history-record ${entry.triage_id === issue.workflow?.triage_id ? 'current' : ''}`}><div className="triage-history-record-top"><button className="id-link" type="button" onClick={() => onOpen(entry.triage_id)}>{entry.triage_id}</button><span className="triage-history-state" style={{ color: `var(--${stage === 'repository' ? 'repo' : stage})` }}><IssueIcon name="dot"/>{issueStateLabel(entry.state)}</span></div><div className="triage-history-record-meta">{entry.attempt > 0 ? <span>{uiText("Attempt")} {entry.attempt}</span> : null}{entry.queued_at ? <span>{uiText("Queued")} {formatSettingsTime(entry.queued_at)}</span> : null}{entry.retry_of ? <span>retry of <code>{entry.retry_of}</code></span> : null}</div></article>; })}</div></section></div></IssuePortal> : null}
  </div>;
}
