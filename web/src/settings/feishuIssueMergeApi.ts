import { settingsRequest } from './settingsApi';

export interface FeishuIssueMergeSettings {
  ok: boolean;
  enabled: boolean;
  configured: boolean;
  values: Record<string, string | boolean>;
  secrets: { app_secret_configured: boolean; action_secret_configured: boolean };
  config_digest: string;
  restart_required: boolean;
  changed_fields?: string[];
  status?: string;
  reason?: string;
}

export function getFeishuIssueMergeSettings(projectId: string): Promise<FeishuIssueMergeSettings> {
  return settingsRequest(`/api/projects/${encodeURIComponent(projectId)}/settings/feishu-issue-merge`);
}

export function saveFeishuIssueMergeSettings(projectId: string, snapshot: FeishuIssueMergeSettings, changes: Record<string, string | boolean>): Promise<FeishuIssueMergeSettings> {
  return settingsRequest(`/api/projects/${encodeURIComponent(projectId)}/settings/feishu-issue-merge`, {
    method: 'PATCH',
    body: JSON.stringify({ config_digest: snapshot.config_digest, changes }),
  });
}

export function testFeishuIssueMergeSettings(projectId: string): Promise<Record<string, unknown>> {
  return settingsRequest(`/api/projects/${encodeURIComponent(projectId)}/settings/feishu-issue-merge/test`, { method: 'POST', body: '{}' });
}
