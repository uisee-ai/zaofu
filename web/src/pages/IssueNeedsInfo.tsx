import type { IssueTriageItem } from "../api/types";
import { IssueIcon } from "./IssueIcon";
type Clarification = NonNullable<NonNullable<IssueTriageItem['workflow']>['needs_info']>;
/**
 * Need-info is a developer-side read-only projection. The reporter answers on
 * the remote Issue; the Triage console must not answer on the reporter's behalf.
 */
export function IssueNeedsInfo({ issue, needsInfo }: {
    projectId?: string;
    issue: IssueTriageItem;
    needsInfo: Clarification;
    onUpdated?: (message: string) => Promise<void>;
    onNotification?: (notice: { title: string; message: string; tone: string; persistent?: boolean }) => void;
}) {
    const exhausted = needsInfo.status === 'needs_info_exhausted';
    const requestStatus = String((needsInfo as Clarification & { request_status?: string }).request_status || 'pending');
    const requestUrl = String((needsInfo as Clarification & { request_url?: string }).request_url || issue.html_url || '');
    return <>
    {needsInfo.reason ? <p className="context-note">{needsInfo.reason}</p> : null}
    <div className="context-note" role="status">{exhausted
      ? 'The clarification limit has been reached. A maintainer must review the reporter’s latest Issue comments before explicitly restarting Triage.'
      : requestStatus === 'published'
        ? 'Questions have been published on the remote GitHub Issue. The reporter should reply there; this developer view is read-only.'
        : 'Questions are waiting for publication to the remote GitHub Issue. This developer view is read-only; retry the controlled refresh after maintainer credentials are available.'}</div>
    <ol className="context-list">{needsInfo.questions.map(question => <li key={question.id}>{question.question}{question.required ? ' (required)' : ' (optional)'}</li>)}</ol>
    {/^https:\/\//i.test(requestUrl) ? <div className="context-actions"><a className="btn" href={requestUrl} target="_blank" rel="noopener noreferrer"><IssueIcon name="external"/>Open GitHub Issue</a></div> : null}
  </>;
}
