import { uiPair, uiText } from '../../settings/uiText';
import { useEffect, useRef, useState } from 'react';
import { SettingsRow, SettingsSection, type Notify } from '../../settings/SettingsPrimitives';
import { useSettingsPreferences } from '../../settings/useSettingsPreferences';

type Data = Record<string, unknown>;
const object=(value:unknown):Data=>value&&typeof value==='object'&&!Array.isArray(value)?value as Data:{};
const string=(value:unknown)=>value==null?'':String(value);
export function IssueSourceSettings({projectId,actionReady,onAction,onNotice}:{
 projectId:string;actionReady:boolean;onAction:(action:string,payload:Data)=>Promise<unknown>;onNotice?:Notify;
}){
 const {language}=useSettingsPreferences();const tr=uiPair;
 const [mode,setMode]=useState('current_project'),[path,setPath]=useState(''),[snapshot,setSnapshot]=useState<Data>({});
 const [checked,setChecked]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState(false);
 const generation=useRef(0),actionRef=useRef(onAction);actionRef.current=onAction;
 const request=async(action:string,payload:Data={})=>{const value=object(await actionRef.current(action,payload));if(value.ok===false)throw new Error(string(value.reason||value.status));return value;};
 const load=async()=>{
  const id=++generation.current;setBusy(true);setError('');setChecked(false);setSaved(false);
  try{const value=await request('self-issue-source-get');if(id!==generation.current)return;setSnapshot(value);setMode(string(value.source_mode));setPath(string(value.configured_root));}
  catch(e){if(id===generation.current)setError(e instanceof Error?e.message:String(e));}
  finally{if(id===generation.current)setBusy(false);}
 };
 useEffect(()=>{setSnapshot({});setChecked(false);setError('');setSaved(false);setBusy(false);if(actionReady)void load();return()=>{generation.current++;};},[projectId,actionReady]);
 const change=()=>{setChecked(false);setSaved(false);setError('');setSnapshot(old=>({...old,inspection:{},valid:undefined}));};
 async function run(save:boolean){
  const id=generation.current;setBusy(true);setError('');
  try{const value=await request(save?'self-issue-source-save':'self-issue-source-inspect',{
   source_mode:mode,source_root:mode==='explicit_target'?path:'',...(save?{config_digest:snapshot.config_digest}:{}),
  });if(id!==generation.current)return;
   setSnapshot(value);setChecked(value.valid===true&&!save);setSaved(save&&value.valid===true);
   if(save){setMode(string(value.source_mode));setPath(string(value.configured_root));onNotice?.(tr('Source directory saved','源码目录已保存'),tr('Applies to new Issue sessions. Existing sessions keep their pinned source.','仅新 Issue 会话生效，已有会话保持原目录。'));}
  }catch(e){if(id!==generation.current)return;setChecked(false);setError(e instanceof Error?e.message:String(e));}
  finally{if(id===generation.current)setBusy(false);}
 }
 const info=object(snapshot.inspection),targets=Array.isArray(snapshot.publication_targets)?snapshot.publication_targets.map(object):[];
 const fact=(label:string,value:unknown)=> <SettingsRow key={label} label={label}><span className="settings-readonly settings-path">{string(value)||'—'}</span></SettingsRow>;
 return <div className="settings-source">
  <SettingsSection title={tr('Issue publication repositories','Issue 发布仓库')} description={tr('Read-only effective targets for reports and evidence comments. Not the source Git origin.','报告和证据评论的实际发布目标，只读；不是源码的 Git origin。')} action={<span className="settings-badge">{tr('Read only','只读')}</span>}>
   {targets.length?targets.map((target,i)=><SettingsRow key={string(target.provider)+i} label={string(target.provider)}><span className="settings-readonly settings-path">{string(target.project)||tr('Not configured','未配置')}</span></SettingsRow>):<p className="settings-empty">{actionReady?tr('No publication target loaded. No fallback to the source origin.','未读取到发布目标，不会自动使用源码 origin。'):tr('Authorize project actions to read its configuration.','请先授权当前项目操作以读取配置。')}</p>}
  </SettingsSection>
  <SettingsSection title={tr('Local investigation source','本地调查源码')} description={tr('A directory on the Agent machine, not this browser’s computer. Changing it does not change publication targets.','路径在运行 Agent 的机器上，不是浏览器所在电脑；修改它不会改变发布目标。')}>
   <SettingsRow label={tr('Source mode','源码模式')}><select aria-label={tr('Source mode','源码模式')} disabled={busy||!actionReady} value={mode} onChange={e=>{change();setMode(e.target.value);}}>
    <option value="current_project">{tr('Current project directory','当前项目目录')}</option><option value="explicit_target">{tr('Specify local directory','指定本地源码目录')}</option>
    {!['current_project','explicit_target'].includes(mode)&&<option value={mode}>{mode} ({tr('existing configuration','已有配置')})</option>}
   </select></SettingsRow>
   {mode==='explicit_target'&&<SettingsRow label={tr('Source directory','源码路径')} description={tr('Read code, locate the issue and reproduce it within the existing limits.','用于读取代码、定位问题和有限复现。')}><input className="settings-path-input" aria-label={tr('Source directory','源码路径')} disabled={busy||!actionReady} value={path} onChange={e=>{change();setPath(e.target.value);}} placeholder="/path/to/product-checkout" spellCheck={false}/></SettingsRow>}
   <div className="settings-card-actions"><button type="button" className="settings-button" disabled={busy||!actionReady||(mode==='explicit_target'&&!path.trim())} onClick={()=>void run(false)}>{tr('Check directory','检查目录')}</button><button type="button" className="settings-button primary" disabled={busy||!actionReady||!checked||!['current_project','explicit_target'].includes(mode)} onClick={()=>void run(true)}>{tr('Save source directory','保存源码目录')}</button><button className="settings-button ghost" type="button" disabled={busy||!actionReady} onClick={()=>void load()}>{tr('Reload','重新读取')}</button>{busy&&<span role="status">{tr('Checking / saving…','正在检查或保存…')}</span>}</div>
   {error&&<p className="settings-callout error" role="alert">{error}</p>}
   {snapshot.valid===false&&<p className="settings-callout error" role="alert">{string(snapshot.reason)}</p>}
   {saved&&<p className="settings-callout" role="status">{tr('Saved for new Issue sessions. No restart is required; existing sessions are not switched.','已保存，仅新 Issue 会话生效，无需重启；不会切换已有会话。')}</p>}
  </SettingsSection>
  {Object.keys(info).length>0&&<SettingsSection title={tr('Directory inspection','目录检查结果')}>
   {fact(tr('Resolved directory','实际源码目录'),info.source_root)}{fact(tr('Git root','Git 根目录'),info.git_root||tr('Not a Git checkout','不是 Git checkout'))}
   {fact(tr('Source remote (origin)','源码仓库远端（origin）'),info.remote)}{fact(tr('Branch','分支'),info.branch)}{fact('HEAD',info.revision)}
   {fact(tr('Uncommitted changes','未提交修改'),info.dirty===true?tr('Yes, including untracked files','有，包括未跟踪文件'):info.dirty===false?tr('No','无'):tr('Unknown','无法判断'))}
   {!!info.warning&&<p className="settings-callout">{string(info.warning)}</p>}
  </SettingsSection>}
  <p className="settings-footnote">{tr('HEAD identifies this checkout, not the installed build. Reports retain both reported and investigated versions. Settings are saved in the current project’s zf.yaml.','HEAD 仅标识本地 checkout，不证明安装包版本；分析保留报告版本和调查版本，设置保存在当前项目 zf.yaml。')}</p>
 </div>;
}
