import { projectionPageQuery } from '../app/pageCompatibility';
/** Preserve project scope when following an Issue's execution records. */
export function issueProjectUrl(projectId: string, target: { issue?: number; task?: string; kind?: 'run' | 'trace'; id?: string }) {
  const url = new URL(window.location.href);
  url.search = '';
  url.searchParams.set('project', projectId);
  url.searchParams.set('page', target.task ? 'board' : 'issue-triage');
  if (target.issue) url.searchParams.set('issue', String(target.issue));
  if (target.task) url.searchParams.set('task', target.task);
  if (target.kind && target.id) url.search = projectionPageQuery(url.searchParams, target.kind, target.id).params.toString();
  return url.pathname + url.search;
}
