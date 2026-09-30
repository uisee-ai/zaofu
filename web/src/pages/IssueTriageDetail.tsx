import { useSettingsPreferences } from "../settings/useSettingsPreferences";
import { uiText } from "../settings/uiText";
import { useEffect, useRef } from "react";
import { formatSettingsTime } from "../settings/useSettingsPreferences";
import { issueProjectUrl } from "./issueProjectNavigation";
import type { IssueTriageDetail as Detail } from "../api/types";
import { MarkdownText } from "../components/agent-session/MarkdownText";
import { githubMarkdownForDisplay, effectiveIssueState, issueStateLabel } from "./issueTriageModel";
import { IssueStateValue } from "./IssueStateBadge";
import { IssueIcon } from "./IssueIcon";
import { issueCreatedAt } from "./issueTimePresentation";
import { IssueIdentity, IssueAuthor } from "./IssueAuthor";
import { IssueLabelList } from "./IssueTriageLabels";
import { IssueTriageHistory } from "./IssueTriageHistory";
import { IssueStatusContext } from "./IssueTriageContext";
import type { IssueNotificationInput } from "./IssueTriageNotifications";
import type { IssueNavigation } from "./IssueTriagePage";
export function IssueTriageDetail({ projectId, detail, selectedLabels, authorStates, onClose, onLabelSelect, onOpenTask: openLocalTask, onOpenProjection: openLocalProjection, onNotification, onUpdated }: IssueNavigation & {
    projectId: string;
    detail: Detail;
    selectedLabels: string[] | null;
    authorStates: Record<string, {
        open: number;
        closed: number;
    }>;
    onClose: () => void;
    onLabelSelect: (label: string) => void;
    onNotification: (notice: IssueNotificationInput) => void;
    onUpdated: (message: string) => Promise<void>;
}) {
  useSettingsPreferences();
    const { issue } = detail;
    const owner = issue.workflow?.workflow_project_id || projectId;
    const onOpenTask = (task: string) => owner === projectId ? openLocalTask(task) : window.location.assign(issueProjectUrl(owner, { task }));
    const onOpenProjection = (kind: 'run' | 'trace', id: string) => owner === projectId ? openLocalProjection(kind, id) : window.location.assign(issueProjectUrl(owner, { kind, id }));
    const created = issueCreatedAt(issue.created_at);
    const scroll = useRef<HTMLDivElement>(null);
    useEffect(() => { if (scroll.current)
        scroll.current.scrollTop = 0; }, [issue.issue_key, issue.number]);
    const state = effectiveIssueState(issue);
    const workflow = issue.workflow;
    const workflowId = workflow?.workflow_id || '';
    const states = [...new Set([issue.github_state, workflow?.state].filter((key): key is string => Boolean(key)))];
    const key = `${projectId}:${issue.issue_key}:${issue.number}`;
    return <aside className="detail" aria-label={`Issue #${issue.number} details`} onClick={event => {
            if (!(event.target instanceof Element))
                return;
            const image = event.target.closest('img[data-streamdown="image"]');
            const link = image?.closest('a');
            if (link?.href && /^https?:/i.test(link.href)) {
                event.preventDefault();
                window.open(link.href, '_blank', 'noopener,noreferrer');
            }
        }}>
    <header className="detail-head"><div className="detail-top"><div className="detail-kicker"><span className="detail-identity-line"><span className="open-state"><IssueIcon name="clock"/>{issueStateLabel(issue.github_state)}</span><span>·</span><span>#{issue.number}</span></span><span className="detail-created-at">{uiText("Created at ")}<time dateTime={created.dateTime} data-tooltip={created.timeZone}>{created.text}</time></span></div><div className="detail-actions"><a className="btn primary" data-tooltip="Edit this Issue on GitHub." href={issue.html_url} target="_blank" rel="noopener noreferrer">{uiText("Open/Edit on GitHub ")}<IssueIcon name="external"/></a><button className="btn" type="button" data-tooltip="Close the Issue details." onClick={onClose}>{uiText("Close")}</button></div></div><h2>{issue.title}</h2></header>
    <div className="detail-scroll" ref={scroll}><dl className="meta">
      {owner !== projectId && <div className="span2"><dt>{uiText("Execution project")}</dt><dd><a className="id-link" href={issueProjectUrl(owner, { issue: issue.number })}>{issue.workflow?.workflow_project_name || owner}</a></dd></div>}
      <div><dt>{uiText("States")}</dt><dd><span className="issue-state-badges" style={{ justifyContent: 'flex-start', maxWidth: 'none' }}>{states.map(value => <IssueStateValue state={value} key={value}/>)}</span></dd></div>
      <div><dt>{uiText("Group")}</dt><dd>{issue.derived_group}</dd></div>
      <div><dt>{uiText("Workflow")}</dt><dd>{workflowId ? workflow?.run_id && state !== 'triage_queued' ? <button className="id-link" type="button" data-tooltip="Open the workflow Run." onClick={() => onOpenProjection('run', workflow.run_id)}>{workflowId}</button> : <span>{workflowId}</span> : <IssueStateValue state={state}/>}</dd></div>
      <div><dt>{uiText("Task")}</dt><dd>{workflow?.task_id ? <><button className="id-link" data-tooltip="Open this Task." type="button" onClick={() => onOpenTask(workflow.task_id)}>{workflow.task_id}</button>{workflow.task_history?.some(item => item.task_id !== workflow.task_id) ? <details className="task-evidence"><summary>{uiText("Earlier Tasks")}</summary><ul className="task-history">{workflow.task_history.filter(item => item.task_id !== workflow.task_id).map(item => <li key={item.task_id}><button className="id-link" type="button" onClick={() => onOpenTask(item.task_id)}>{item.task_id}</button></li>)}</ul></details> : null}</> : uiText('None')}</dd></div>
      <div className="span2"><dt>{uiText("ZaoFu source revision")}</dt><dd>{workflow?.source_code_revision ? <code>{workflow.source_code_revision}</code> : uiText('Unbound')}</dd></div>
      <div className="span2 triage-meta-card"><dt>{uiText("Triage")}</dt><dd><IssueTriageHistory key={key} issue={issue} onOpen={id => onOpenProjection('trace', id)}/></dd></div>
      <div><dt>{uiText("Reporter")}</dt><dd><IssueIdentity login={issue.author_login} avatarUrl={issue.author_avatar_url}/></dd></div>
      <div><dt>{uiText("Assignees")}</dt><dd className="identities">{issue.assignees?.length ? issue.assignees.map(login => <IssueIdentity key={login} login={login} avatarUrl={issue.assignee_avatar_urls?.[login]}/>) : uiText('None')}</dd></div>
      <div><dt>{uiText("Milestone")}</dt><dd>{issue.milestone || uiText('None')}</dd></div><div><dt>{uiText("Comments")}</dt><dd>{issue.comment_count}</dd></div>
      <div className="span2"><dt>{uiText("Labels")}</dt><dd>{issue.labels.length ? <IssueLabelList key={key} labels={issue.labels} colors={issue.label_colors} selected={selectedLabels} onSelect={onLabelSelect}/> : uiText('None')}</dd></div>
    </dl>
    <IssueStatusContext key={key} projectId={owner} issue={issue} delivery={detail.delivery} onOpenTask={onOpenTask} onOpenProjection={onOpenProjection} onNotification={onNotification} onUpdated={onUpdated}/>
    <section className="body-section"><div className="section-title"><span>{uiText("Issue body")}</span><span className="section-sub">{uiText("GitHub Markdown preview")}</span></div><div className="issue-body"><MarkdownText showImageDownload={false} content={githubMarkdownForDisplay(detail.body || '(No description provided.)', projectId)}/></div></section>
    <section className="body-section issue-comments"><div className="section-title"><span>Comments ({detail.comments?.length ?? issue.comment_count})</span></div>{detail.comments?.map(comment => <article className="issue-body issue-comment" key={comment.id}><header><IssueAuthor login={comment.author_login} avatarUrl={comment.author_avatar_url} stats={authorStates[comment.author_login]}/><a href={comment.html_url} target="_blank" rel="noopener noreferrer">{formatSettingsTime(comment.created_at)}</a></header><MarkdownText showImageDownload={false} content={githubMarkdownForDisplay(comment.body || '(Empty comment.)', projectId)}/></article>)}{!detail.comments?.length ? <p className="empty-comments">{uiText("No comments.")}</p> : null}</section>
    </div>
  </aside>;
}
