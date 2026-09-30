import { useEffect, useRef, useState } from 'react';
import { FolderGit2, LoaderCircle, Check } from 'lucide-react';
import { uiPair } from './uiText';
import { useSettingsPreferences } from './useSettingsPreferences';
import { settingsRequest, settingsErrorMessage } from './settingsApi';
import { SettingsRow, SettingsSection, type Notify } from './SettingsPrimitives';

type ExecutionSettings = {
  ok: boolean; valid: boolean; configured_root: string; project_root: string;
  execution_project_root: string; workflow_project_name?: string; state_dir?: string;
  config_digest: string; config_path: string; reason?: string; writable: boolean;
};
export function IssueExecutionSettings({ projectId, actionReady, notify }: {
  projectId: string; actionReady: boolean; notify: Notify;
}) {
  const { language } = useSettingsPreferences(), tr = uiPair;
  const [snapshot, setSnapshot] = useState<ExecutionSettings | null>(null);
  const [custom, setCustom] = useState(false), [path, setPath] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [saved, setSaved] = useState(false);
  const generation = useRef(0);
  const url = `/api/projects/${encodeURIComponent(projectId)}/settings/issue-execution`;
  async function load() {
    const id = ++generation.current;
    setBusy(true); setError(''); setSaved(false);
    try {
      const result = await settingsRequest<ExecutionSettings>(url);
      if (id !== generation.current) return;
      setSnapshot(result); setCustom(Boolean(result.configured_root)); setPath(result.configured_root);
    } catch (e) { if (id === generation.current) setError(settingsErrorMessage(e, language)); }
    finally { if (id === generation.current) setBusy(false); }
  }
  useEffect(() => {
    setSnapshot(null); setPath(''); setCustom(false); setSaved(false);
    if (actionReady) void load();
    return () => { ++generation.current; };
  }, [projectId, actionReady]);
  const value = custom ? path.trim() : '';
  const changed = snapshot !== null && value !== snapshot.configured_root;
  async function save() {
    if (!snapshot || !changed) return;
    const id = generation.current;
    setBusy(true); setError(''); setSaved(false);
    try {
      const result = await settingsRequest<ExecutionSettings>(url, {
        method: 'PATCH', body: JSON.stringify({ config_digest: snapshot.config_digest, execution_project_root: value }),
      });
      if (id !== generation.current) return;
      setSnapshot(result); setCustom(Boolean(result.configured_root)); setPath(result.configured_root); setSaved(true);
      notify(tr('Execution project saved', '执行项目已保存'), tr('New Issues use this project. Existing workflows retain their owner.', '新入队 Issue 使用此项目，已有流程保留原归属。'));
    } catch (e) { if (id === generation.current) setError(settingsErrorMessage(e, language)); }
    finally { if (id === generation.current) setBusy(false); }
  }
  const disabled = busy || !actionReady || !snapshot || !snapshot.writable;
  return <div>
    <SettingsSection title={tr('Diagnosis & fix project', '诊断与修复项目')} description={tr(
      'Triage, Fix, verification and delivery belong to this project. Its zf.yaml defines the runtime state directory.',
      'Triage、Fix、验证和交付归属此项目，运行状态目录由该项目的 zf.yaml 决定。',
    )} action={<FolderGit2 size={18} />}>
      <SettingsRow label={tr('Execution location', '执行位置')}>
        <select aria-label={tr('Execution location', '执行位置')} value={custom ? 'custom' : 'current'} disabled={disabled} onChange={e => { setCustom(e.target.value === 'custom'); setSaved(false); }}>
          <option value="current">{tr('Current open project (default)', '当前打开的项目（默认）')}</option>
          <option value="custom">{tr('Specify project directory', '指定项目目录')}</option>
        </select>
      </SettingsRow>
      {custom && <SettingsRow label={tr('Execution project directory', '执行项目目录')} description={tr('A project on the Agent machine containing zf.yaml.', 'Agent 机器上包含 zf.yaml 的项目目录。')}>
        <input className="settings-path-input" aria-label={tr('Execution project directory', '执行项目目录')} value={path} disabled={disabled} spellCheck={false} placeholder={snapshot?.project_root} onChange={e => { setPath(e.target.value); setSaved(false); }} />
      </SettingsRow>}
      {snapshot && <SettingsRow label={tr('Current open project', '当前打开的项目')}><span className="settings-readonly settings-path">{snapshot.project_root}</span></SettingsRow>}
      <div className="settings-card-actions">
        <button className="settings-button primary" disabled={disabled || !changed || (custom && !value)} onClick={() => void save()}>{busy ? <LoaderCircle size={15} className="spin" /> : saved ? <Check size={15} /> : null}{tr('Save execution project', '保存执行项目')}</button>
        <button className="settings-button" disabled={busy || !actionReady} onClick={() => void load()}>{tr('Reload', '重新读取')}</button>
        {busy && <span role="status">{tr('Loading / saving…', '正在读取或保存…')}</span>}
      </div>
      {error && <p role="alert" className="settings-callout error">{error}</p>}
      {!actionReady && <p className="settings-callout">{tr('Authorize project actions to manage this setting.', '请先授权当前项目操作。')}</p>}
      {saved && <p role="status" className="settings-callout">{tr('Saved. Applies to new Issues without restarting.', '已保存，无需重启，对新入队 Issue 生效。')}</p>}
    </SettingsSection>
    {snapshot && <SettingsSection title={tr('Saved execution binding', '已保存的执行归属')}>
      <SettingsRow label={tr('Project', '项目')}><span className="settings-readonly">{snapshot.workflow_project_name || '—'}</span></SettingsRow>
      <SettingsRow label={tr('Directory', '目录')}><span className="settings-readonly settings-path">{snapshot.execution_project_root}</span></SettingsRow>
      <SettingsRow label={tr('Runtime state', '运行状态目录')}><span className="settings-readonly settings-path">{snapshot.state_dir || '—'}</span></SettingsRow>
      {!snapshot.valid && <p role="alert" className="settings-callout error">{snapshot.reason}</p>}
    </SettingsSection>}
    <p className="settings-footnote">{tr('The User investigation source is configured separately. Existing Triage/Fix workflows keep their pinned project; this setting does not move running tasks.', '用户侧的调查源码目录独立配置。已有 Triage/Fix 流程保持入队时的项目，此设置不会迁移运行中的任务。')}</p>
  </div>;
}
