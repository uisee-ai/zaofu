import { Fragment } from "react";
import type { IssueTriageItem } from "../api/types";
import { IssueIcon } from "./IssueIcon";
import { effectiveIssueState, issueStateLabel } from "./issueTriageModel";
import { issueStateTone } from "./issueStatePresentation";
const STAGES = ['repository', 'triage', 'decision', 'fix', 'delivery', 'complete'] as const;
const NAMES = ['Repository', 'Triage', 'Decision', 'Fix', 'Delivery', 'Complete'];
type Stage = typeof STAGES[number];
function currentStage(state: string, projected?: string): Stage {
    // Known current states are authoritative even if an older server returned
    // an incorrect lifecycle.current. Generic failures need the recorded stage.
    if (['blocked', 'failed'].includes(state) && projected && STAGES.includes(projected as Stage))
        return projected as Stage;
    if (state.startsWith('triage_') || state === 'triaging')
        return 'triage';
    if (['needs_info', 'needs_info_exhausted', 'unverified_candidate', 'awaiting_fix_approval'].includes(state))
        return 'decision';
    if (state.startsWith('fix_') || ['fixing', 'verifying'].includes(state))
        return 'fix';
    if (state === 'merged')
        return 'complete';
    if (['verified_candidate', 'approved_for_pr', 'owner_changes_requested', 'owner_rejected', 'publication_prepared'].includes(state) || state.startsWith('pr_'))
        return 'delivery';
    return 'repository';
}
/** Older servers may know that an event exists without proving completion. Keep that unknown. */
export function issueLifecycleNodes(issue: IssueTriageItem) {
    const state = effectiveIssueState(issue);
    const projected = issue.workflow?.lifecycle;
    const current = currentStage(state, projected?.current);
    return STAGES.map((stage, i) => {
        const record = projected?.stages?.[stage];
        const isCurrent = stage === current;
        const exact = isCurrent ? state : record?.state || (stage === 'repository' ? issue.github_state : '');
        const tone = isCurrent ? issueStateTone(state) : record?.outcome === 'completed' ? 'done' : record?.outcome === 'failed' ? 'error' : record?.outcome === 'attention' ? 'attention' : record?.outcome === 'active' ? 'active' : 'pending';
        const notReached = !isCurrent && i > STAGES.indexOf(current) && !record?.state && !record?.evidence_refs?.length;
        const summary = tone === 'done' ? 'Completed stage' : tone === 'active' ? 'In progress' : tone === 'attention' ? 'Needs attention' : tone === 'error' ? 'Failed / stopped' : notReached ? 'Not reached' : 'History unavailable';
        return { stage, label: NAMES[i], isCurrent, exact: exact ? issueStateLabel(exact) : notReached ? 'Not reached' : 'Not recorded', tone, summary };
    });
}
export function IssueLifecycle({ issue, onFront }: {
    issue: IssueTriageItem;
    onFront: () => void;
}) {
    const state = effectiveIssueState(issue);
    const nodes = issueLifecycleNodes(issue);
    return <section className="context-card lifecycle-card">
    <header className="lifecycle-head"><div className="lifecycle-title"><IssueIcon name="route" size="lg"/><div><h4>Issue lifecycle</h4><p>Repository to merge, summarized as one calm progression.</p></div></div><div className="lifecycle-head-actions"><span className={`lifecycle-current-state ${issueStateTone(state)}`}>{issueStateLabel(state)}</span><button className="context-flip-button" data-context-face="front" type="button" aria-label="Back to current status" data-tooltip="Return to the current status card." onClick={onFront}><IssueIcon name="flip"/></button></div></header>
    <div className="lifecycle-rail" aria-label="Issue lifecycle progression">{nodes.map((node, i) => <Fragment key={node.stage}>
      <span className={`lifecycle-node ${node.tone}${node.isCurrent ? ' current' : ''}${i === 0 ? ' edge-start' : i === nodes.length - 1 ? ' edge-end' : ''}`} tabIndex={0} role="img" aria-label={`${node.label}: ${node.exact} · ${node.summary}`} data-tooltip={`${node.label} · ${node.exact}`} data-tooltip-kind="lifecycle" data-tooltip-heading={node.label} data-tooltip-state={node.exact} data-tooltip-summary={node.summary}>
        <span className="lifecycle-dot" aria-hidden="true"/><span className="lifecycle-stage-label">{node.label}</span>
      </span>{i < nodes.length - 1 ? <span aria-hidden="true" className={`lifecycle-connector ${node.tone === 'done' && nodes[i + 1].tone === 'done' ? 'complete' : node.tone === 'done' ? 'transition' : 'future'}`}/> : null}
    </Fragment>)}</div>
    <div className="lifecycle-legend" aria-label="Lifecycle color legend">{[['done', 'Completed'], ['active', 'In progress'], ['attention', 'Needs attention'], ['error', 'Failed / stopped'], ['', 'Not reached / unknown']].map(([tone, label]) => <span className="lifecycle-legend-item" key={label}><i className={`lifecycle-legend-dot ${tone}`}/>{label}</span>)}</div>
    <p className="lifecycle-help">Hover or focus a point to see the exact state for that lifecycle group.</p>
  </section>;
}
