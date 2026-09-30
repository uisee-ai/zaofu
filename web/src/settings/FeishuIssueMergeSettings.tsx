import { useEffect, useState } from 'react';
import { CheckCircle2, RefreshCw } from 'lucide-react';
import { uiPair } from './uiText';
import { SettingsRow, SettingsSection, type Notify } from './SettingsPrimitives';
import { getFeishuIssueMergeSettings, saveFeishuIssueMergeSettings, testFeishuIssueMergeSettings, type FeishuIssueMergeSettings as Snapshot } from './feishuIssueMergeApi';

const fields: Array<[string, string, string, boolean]> = [
  ['app_id', 'Feishu App ID', '飞书 App ID', false],
  ['app_secret', 'App Secret', 'App Secret', true],
  ['bot_open_id', 'Bot Open ID (optional)', 'Bot Open ID（可选）', false],
  ['chat_id', 'Issue-merge group Chat ID', 'Issue-merge 群 Chat ID', false],
  ['operator_open_id', 'Your Feishu Open ID', '你的飞书 Open ID', false],
  ['bitable_app_token', 'Issue Base app_token', 'Issue 多维表格 app_token', false],
  ['bitable_table_id', 'Issue table_id', 'Issue 数据表 table_id', false],
  ['bitable_view_id', 'Issue view_id (optional)', 'Issue 视图 view_id（可选）', false],
];

export function FeishuIssueMergeSettings({ projectId, actionReady, notify }: { projectId: string; actionReady: boolean; notify: Notify }) {
  const tr = uiPair;
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [draft, setDraft] = useState<Record<string, string | boolean>>({});
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState('');
  const [testResult, setTestResult] = useState('');
  const load = () => {
    if (!actionReady) return;
    setBusy(true); setError('');
    void getFeishuIssueMergeSettings(projectId).then(value => { setSnapshot(value); setDraft(value.values); }).catch(e => setError(String(e?.message || e))).finally(() => setBusy(false));
  };
  useEffect(load, [projectId, actionReady]);
  if (!actionReady) return <p className="settings-callout">{tr('Authorize this project using the existing Web access controls before changing Feishu settings.','请先通过现有 Web 授权入口，再修改飞书接入设置。')}</p>;
  const changes = Object.fromEntries(Object.entries(draft).filter(([key, value]) => snapshot && JSON.stringify(value) !== JSON.stringify(snapshot.values[key])));
  const save = () => {
    if (!snapshot || !Object.keys(changes).length) return;
    setBusy(true); setError('');
    void saveFeishuIssueMergeSettings(projectId, snapshot, changes).then(value => { setSnapshot(value); setDraft(value.values); notify(tr('Feishu settings saved','飞书设置已保存'), tr('Restart the Feishu bridge (or run zf start) to apply the new credentials.','请重启 Feishu bridge（或重新运行 zf start）以加载新凭证。')); }).catch(e => { setError(String(e?.message || e)); notify(tr('Feishu settings were not saved','飞书设置未保存'), String(e?.message || e), true); }).finally(() => setBusy(false));
  };
  const test = () => { setTesting(true); setTestResult(''); void testFeishuIssueMergeSettings(projectId).then(result => { setTestResult(String(result.message || result.reason || result.status || (result.ok ? 'connected' : 'failed'))); }).catch(e => setTestResult(String(e?.message || e))).finally(() => setTesting(false)); };
  return <>
    {busy && !snapshot && <p className="settings-callout">{tr('Loading Feishu Issue-merge settings…','正在读取 Feishu Issue-merge 设置…')}</p>}
    {error && <p className="settings-callout error" role="alert">{error}<button className="settings-button" onClick={load}>{tr('Reload','重新读取')}</button></p>}
    {snapshot && <>
      <SettingsSection title={tr('Issue-merge Feishu connection','Issue-merge 飞书接入')} description={tr('Only the /issue-merge flow is configured here. Feishu app permissions are granted in the Feishu developer console.','这里仅配置 /issue-merge；飞书应用权限需在飞书开发者后台自行授权。')}>
        <SettingsRow label={tr('Enable Issue-merge Feishu','启用 Issue-merge 飞书接入')} description={tr('The bridge accepts messages only from the configured group and operator.','Bridge 只接受配置群和操作者的消息。')}><button type="button" className="settings-switch" role="switch" aria-checked={draft.enabled === true} disabled={busy} onClick={() => setDraft(old => ({ ...old, enabled: old.enabled !== true }))} /></SettingsRow>
        {fields.map(([key, en, zh, password]) => <SettingsRow key={key} label={tr(en, zh)} description={key === 'app_secret' ? tr('Stored locally and never shown again after saving.','仅保存到本地，保存后不会再次显示。') : undefined}><input aria-label={tr(en, zh)} type={password ? 'password' : 'text'} autoComplete="off" placeholder={password && snapshot.secrets.app_secret_configured ? tr('Configured','已配置') : ''} value={password && snapshot.secrets.app_secret_configured && draft[key] === '' ? '' : String(draft[key] ?? '')} onChange={e => setDraft(old => ({ ...old, [key]: e.target.value }))} /></SettingsRow>)}
      </SettingsSection>
      <div className="settings-card-actions"><button className="settings-button primary" disabled={busy || !Object.keys(changes).length} onClick={save}>{tr('Save connection','保存接入')}</button><button className="settings-button" disabled={testing} onClick={test}><RefreshCw size={15} />{testing ? tr('Testing…','测试中…') : tr('Test connection','测试连接')}</button>{testResult && <span className="settings-badge"><CheckCircle2 size={14} />{testResult}</span>}</div>
      <p className="settings-footnote">{snapshot.secrets.action_secret_configured ? tr('Action callback signing secret is generated and stored by ZaoFu.','按钮回调签名密钥由 ZaoFu 自动生成并保存。') : tr('Saving will generate the callback signing secret automatically.','保存时会自动生成按钮回调签名密钥。')}</p>
    </>}
  </>;
}
