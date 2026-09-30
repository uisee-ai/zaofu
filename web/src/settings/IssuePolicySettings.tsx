import { uiPair, uiText } from './uiText';
import { useEffect, useRef, useState } from 'react';
import { Plus, X } from 'lucide-react';
import { getIssuePolicy, saveIssuePolicy, settingsErrorMessage, type IssuePolicy } from './settingsApi';
import { SettingsDialog, SettingsRow, SettingsSection, type Notify } from './SettingsPrimitives';
import { useSettingsPreferences } from './useSettingsPreferences';
export function IssuePolicySettings({projectId,page,actionReady,notify}:{projectId:string;page:'reporting'|'capture'|'queue';actionReady:boolean;notify:Notify}){
 const {language}=useSettingsPreferences(),tr=uiPair;
 const [snapshot,setSnapshot]=useState<IssuePolicy|null>(null),[draft,setDraft]=useState<IssuePolicy['values']>({}),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null);
 const [dialog,setDialog]=useState<'urls'|'review'|null>(null),[urls,setUrls]=useState(''),[urlError,setUrlError]=useState('');
 const request=useRef(0);const [retry,setRetry]=useState(0);
 useEffect(()=>{const id=++request.current;setSnapshot(null);setError(null);setDialog(null);if(!actionReady)return;
  setBusy(true);getIssuePolicy(projectId).then(value=>{if(id===request.current){setSnapshot(value);setDraft(value.values);}}).catch(e=>{if(id===request.current)setError(e);}).finally(()=>{if(id===request.current)setBusy(false);});
  return()=>{request.current++;};
 },[projectId,actionReady,retry]);
 const keys=page==='queue'?['ingress.enabled','ingress.poll_interval_seconds']:page==='reporting'?['enabled','automatic_detection_enabled']:['browser_capture_enabled','browser_capture_denied_urls'];
 const changes=Object.fromEntries(keys.filter(k=>snapshot&&JSON.stringify(draft[k])!==JSON.stringify(snapshot.values[k])).map(k=>[k,draft[k]]));
 const changed=Object.keys(changes).length>0;
 const interval=draft['ingress.poll_interval_seconds'];const invalid=page==='queue'&&(typeof interval!=='number'||!Number.isInteger(interval)||interval<60||interval>604800);
 const editable=!!snapshot?.writable&&actionReady&&!busy;
 const set=(key:string,value:IssuePolicy['values'][string])=>setDraft(old=>({...old,[key]:value}));
 function toggle(key:string,label:string,desc:string,nested=false,disabled=false){return <SettingsRow key={key} label={label} description={desc} nested={nested}><button type="button" className="settings-switch" role="switch" aria-label={label} aria-checked={draft[key]===true} disabled={!editable||disabled} onClick={()=>set(key,draft[key]!==true)}/></SettingsRow>;}
 async function save(){if(!snapshot||invalid||!changed)return;const id=request.current;setBusy(true);setError(null);try{const next=await saveIssuePolicy(projectId,snapshot,changes);if(id!==request.current)return;setSnapshot(next);setDraft(next.values);setDialog(null);notify(tr('Configuration saved','配置已保存'),next.restart_required?tr('Restart the Web service to apply queue and polling changes.','排队与同步设置在 Web 服务重启后生效。'):tr('Applied to subsequent operations; the background poller reloads settings between passes.','后续操作将使用新配置；后台轮询器会在轮询间隙自动载入。'));}catch(e){if(id===request.current){setError(e);notify(tr('Settings were not saved','设置未保存'),settingsErrorMessage(e,language),true);}}finally{if(id===request.current)setBusy(false);}}
 function addUrls(){const lines=urls.split(/\r?\n/).map((value,index)=>({value:value.trim(),index})).filter(x=>x.value);const values:string[]=[];try{
  if(!lines.length)throw new Error(tr('Enter at least one URL.','至少输入一个 URL。'));
  for(const line of lines){try{const raw=line.value;if(raw.length>2048||/[\s\\*]/.test(raw))throw Error();const u=new URL(raw);if(!['http:','https:'].includes(u.protocol)||u.username||u.password||/%(?:2f|5c|00|25)/i.test(u.pathname))throw Error();values.push(u.origin+(u.pathname.replace(/\/+$/,'')||'/'));}catch{throw new Error(`${tr('Invalid HTTP(S) URL at line','HTTP(S) URL 无效，行号')} ${line.index+1}`);}}
  const next=[...new Set([...(Array.isArray(draft.browser_capture_denied_urls)?draft.browser_capture_denied_urls:[]),...values])];if(next.length>200)throw new Error(tr('Maximum 200 URLs.','最多 200 个 URL。'));set('browser_capture_denied_urls',next);setDialog(null);setUrls('');setUrlError('');
 }catch(e){setUrlError(String((e as Error).message));}}
 if(!actionReady)return <p className="settings-callout">{tr('Authorize this project using the existing Web access controls before reading or changing its settings.','请先通过现有 Web 授权入口授权项目操作，再读取或修改配置。')}</p>;
 return <>
  {busy&&!snapshot&&<p role="status" className="settings-callout">{tr('Loading effective configuration…','正在读取实际生效配置…')}</p>}
  {error!==null&&<div className="settings-callout error" role="alert">{settingsErrorMessage(error,language)}<button className="settings-button" disabled={busy} onClick={()=>setRetry(x=>x+1)}>{tr('Reload configuration','重新读取配置')}</button></div>}
  {snapshot&&<>
   <div className="settings-policy-source"><span className="settings-badge">{snapshot.inherited?tr('Workspace policy','工作区策略'):tr('Project policy','项目策略')}</span><span>{snapshot.config_path}</span></div>
   {snapshot.inherited&&<p className="settings-callout">{tr('This project inherits the workspace policy. Changes below affect every project using that policy. The investigation source remains project-local.','当前项目继承工作区策略，下面的修改会影响使用该策略的项目；调查源码目录仍是当前项目独立设置。')}</p>}
   {page==='reporting'&&<SettingsSection title={tr('Self-Issue reporting','Self-Issue 报告')}>
    {toggle('enabled',tr('Self-Issue reporting','Self-Issue 总开关'),tr('Enable the existing self-reporting policy.','启用现有的自身问题报告策略。'))}
    {toggle('automatic_detection_enabled',tr('Automatic issue discovery','自动问题发现'),tr('Collect supported faults automatically; publishing still requires approval.','自动收集支持识别的异常，发布仍需审核。'),true,draft.enabled!==true)}
   </SettingsSection>}
   {page==='queue'&&<SettingsSection title={tr('Queue & sync','排队与同步')}>
    {toggle('ingress.enabled',tr('Auto Triage queued','自动 Triage queued'),tr('Queue eligible Issues. Turning this off never cancels existing work.','将符合条件的 Issue 排队，关闭不取消现有任务。'))}
    <SettingsRow label={tr('GitHub sync interval','GitHub 同步间隔')} description={tr('Issue status is synchronized from GitHub to ZaoFu.','Issue 状态从 GitHub 同步至 ZaoFu。')}><div className="settings-number"><input aria-label={tr('GitHub sync interval','GitHub 同步间隔')} type="number" min={60} max={604800} step={1} disabled={!editable} value={typeof interval==='number'&&Number.isFinite(interval)?interval:''} aria-invalid={invalid} onChange={e=>set('ingress.poll_interval_seconds',e.target.value===''?NaN:Number(e.target.value))}/><span>{tr('s','秒')}</span></div></SettingsRow>
    <p className="settings-callout">{tr('Saved to zf.yaml and applied between polling passes without restarting. No running task is cancelled.','保存到 zf.yaml 后在轮询间隙自动生效，无需重启，不取消运行中的任务。')}</p>
   </SettingsSection>}
   {page==='capture'&&<><SettingsSection title={tr('Browser evidence','浏览器证据')}>
    {toggle('browser_capture_enabled',tr('Allow browser capture','允许浏览器取证'),tr('Use the existing passive capture flow during Issue evaluation.','在 Issue 评估时使用现有的被动取证流程。'))}
   </SettingsSection><SettingsSection title={tr('Blocked capture URLs','禁止取证 URL')} description={tr('The same origin, this path and its child paths. Queries and fragments are ignored.','匹配同源的此路径及子路径，忽略查询参数和片段。')} action={<button className="settings-button" disabled={!editable} onClick={()=>{setUrls('');setUrlError('');setDialog('urls');}}><Plus size={15}/>{tr('Add URLs','添加 URL')}</button>}>
    <div className="settings-url-list">{Array.isArray(draft.browser_capture_denied_urls)&&draft.browser_capture_denied_urls.length?draft.browser_capture_denied_urls.map((url,index)=><div className="settings-url-row" key={url}><span>{url}</span><button className="settings-icon-button" disabled={!editable} aria-label={`${tr('Remove','删除')} ${url}`} onClick={()=>set('browser_capture_denied_urls',(draft.browser_capture_denied_urls as string[]).filter((_,i)=>i!==index))}><X size={16}/></button></div>):<p className="settings-empty">{tr('No blocked URLs configured. Other capture permissions still apply.','尚未配置禁止地址，其他取证权限仍然适用。')}</p>}</div>
   </SettingsSection><p className="settings-footnote">{tr('Rules are enforced by the existing browser evidence runner, including redirect and frame checks. They do not turn an unmatched URL into an authorized destination.','现有浏览器证据运行器会执行规则，包括重定向和 frame 检查；未命中不代表已经获得访问授权。')}</p></>}
   <footer className="settings-save-bar"><span>{changed?tr('Unsaved project changes','尚未保存的项目修改'):tr('Values read from zf.yaml','来自 zf.yaml 的实际值')}</span><div><button className="settings-button" disabled={!changed||busy} onClick={()=>{setDraft(snapshot.values);setError(null);}}>{tr('Discard','撤销')}</button><button className="settings-button primary" disabled={!changed||!editable||invalid} onClick={()=>setDialog('review')}>{tr('Review changes','检查更改')}</button></div></footer>
  </>}
  {dialog==='urls'&&<SettingsDialog title={tr('Add blocked URLs','添加禁止取证 URL')} description={tr('One HTTP(S) URL per line, up to 200. These are restrictions, not a base URL.','每行一个 HTTP(S) URL，最多 200 个；这是限制列表，不是取证入口地址。')} onClose={()=>setDialog(null)} footer={<><button className="settings-button" onClick={()=>setDialog(null)}>{tr('Cancel','取消')}</button><button className="settings-button primary" onClick={addUrls}>{tr('Add to changes','加入待保存修改')}</button></>}><textarea data-autofocus aria-label={tr('Blocked URLs','禁止 URL')} value={urls} onChange={e=>setUrls(e.target.value)} placeholder={'https://example.com/admin\nhttp://127.0.0.1:8002/private'}/>{urlError&&<p role="alert" className="settings-error">{urlError}</p>}</SettingsDialog>}
  {dialog==='review'&&<SettingsDialog title={tr('Review configuration changes','确认配置修改')} description={snapshot?.inherited?tr('Workspace-wide policy: this can affect other projects.','工作区策略：可能影响其他项目。'):tr('Only the listed fields will be changed.','只修改下面列出的字段。')} busy={busy} onClose={()=>setDialog(null)} footer={<><button className="settings-button" disabled={busy} onClick={()=>setDialog(null)}>{tr('Back','返回')}</button><button className="settings-button primary" disabled={busy||invalid} onClick={()=>void save()}>{busy?tr('Saving…','保存中…'):tr('Save configuration','保存配置')}</button></>}>
   {Object.entries(changes).map(([key,value])=><div key={key} className="settings-diff"><strong>{key}</strong><p>{JSON.stringify(snapshot?.values[key])}</p><p>→ {JSON.stringify(value)}</p></div>)}{error!==null&&<p className="settings-error" role="alert">{settingsErrorMessage(error,language)}</p>}
  </SettingsDialog>}
 </>;
}
