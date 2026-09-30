import { uiPair, uiText } from './uiText';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Monitor, Info, Camera, Folder, Link, RefreshCw, ListFilter, Shield, KeyRound, Check } from 'lucide-react';
import type { Snapshot } from '../api/types';
import type { ThemeMode } from '../app/sharedTypes';
import { postAction } from '../api/client';
import { IssueSourceSettings } from '../components/projection/IssueSourceSettings';
import { enterDeveloper, enterUser, forgetDeveloper, refreshDeveloperAccess, useDeveloperAccess } from './developerAccess';
import { useSettingsPreferences, formatSettingsTime } from './useSettingsPreferences';
import { SettingsDialog, SettingsNotices, SettingsSection, SettingsRow, type Notice } from './SettingsPrimitives';
import { PreferenceField } from './PreferenceFields';
import type { EditablePreferenceKey } from './preferences';
import { optionNames, settingsPages, type SettingsPageId } from './settingsFields';
import { IssueExecutionSettings } from './IssueExecutionSettings';
import { IssuePolicySettings } from './IssuePolicySettings';
import { FeishuIssueMergeSettings } from './FeishuIssueMergeSettings';
import './settings.css';
const icons={screen:Monitor,info:Info,camera:Camera,folder:Folder,link:Link,refresh:RefreshCw,queue:ListFilter,shield:Shield,key:KeyRound};
const fieldsFor:Partial<Record<SettingsPageId,EditablePreferenceKey[]>>={
 appearance:['language','timeZone'],
 refresh:['autoRefresh','refreshSeconds','refreshOnReturn'],
};
function readSection():SettingsPageId {const value=new URLSearchParams(location.search).get('settings');return value&&Object.prototype.hasOwnProperty.call(settingsPages,value)?value as SettingsPageId:'appearance';}
export function SettingsPage({projectId,actionReady,themeMode,onThemeModeChange,snapshot}:{projectId:string;actionReady:boolean;themeMode:ThemeMode;onThemeModeChange:(theme:ThemeMode)=>void;snapshot:Snapshot|null}){
 const prefs=useSettingsPreferences(),lang=prefs.language==='zh'?1:0,tr=uiPair;
 const access=useDeveloperAccess();const [section,setSection]=useState<SettingsPageId>(readSection);
 const [unlock,setUnlock]=useState(false),[token,setToken]=useState(''),[unlockError,setUnlockError]=useState(''),[checking,setChecking]=useState(false);
 const [notices,setNotices]=useState<Notice[]>([]),[mobileOpen,setMobileOpen]=useState(false);
 const counter=useRef(0),switchRequest=useRef(0),input=useRef<HTMLInputElement>(null),body=useRef<HTMLDivElement>(null);
 const notify=useCallback((title:string,message?:string,error=false)=>setNotices(old=>[...old,{id:++counter.current,title,message,error}]),[]);
 const dismiss=useCallback((id:number)=>setNotices(old=>old.filter(item=>item.id!==id)),[]);
 useEffect(()=>{document.documentElement.lang=({en:'en',zh:'zh-CN',ko:'ko',ja:'ja'} as const)[prefs.language] || 'en';},[prefs.language]);
 useEffect(()=>{
  if(access.loading)return;
  if(settingsPages[section].scope!==access.mode)navigate(access.mode==='developer'?'refresh':'appearance');
 },[access.loading,access.mode,access.verified,section]);
 useEffect(()=>{setUnlock(false);setToken('');setUnlockError('');++switchRequest.current;},[projectId]);
 useEffect(()=>()=>{++switchRequest.current;},[]);
 const navigate=(next:SettingsPageId)=>{
  if(settingsPages[next].scope==='developer'&&(access.mode!=='developer'||!access.verified))return;
  setSection(next);setMobileOpen(false);body.current?.scrollTo({top:0});
  const url=new URL(location.href);url.searchParams.set('settings',next);history.replaceState(history.state,'',url.pathname+url.search+url.hash);
 };
 const requestDeveloper=async(value?:string)=>{
  const id=++switchRequest.current;setChecking(true);setUnlockError('');
  try{const valid=await enterDeveloper(value);if(id!==switchRequest.current)return;
   if(valid){setUnlock(false);setToken('');setSection('refresh');}
   else{setUnlock(true);setToken('');}
  }catch(e){if(id!==switchRequest.current)return;setUnlock(true);setUnlockError(String((e as Error).message||e));setToken('');setTimeout(()=>input.current?.focus(),0);}
  finally{if(id===switchRequest.current)setChecking(false);}
 };
 const sourceAction=useCallback((action:string,payload:Record<string,unknown>)=>postAction(action,payload,projectId),[projectId]);
 const page=settingsPages[section];const sections=(Object.keys(settingsPages) as SettingsPageId[]).filter(key=>settingsPages[key].scope===access.mode);
 const visible=page.scope==='user'||(access.verified&&access.mode==='developer');
 let content;
 if(fieldsFor[section]) content=<><SettingsSection title={uiPair(...page.label)}>
  {section==='appearance'&&<SettingsRow label={tr('Theme','主题')} description={tr('Use the existing ZaoFu theme setting.','使用现有 ZaoFu 主题设置。')}><select aria-label={tr('Theme','主题')} value={themeMode} onChange={e=>onThemeModeChange(e.target.value as ThemeMode)}>{(['light','dark','system'] as const).map(value=><option key={value} value={value}>{uiPair(...optionNames[value])}</option>)}</select></SettingsRow>}
  {fieldsFor[section]?.map(name=><PreferenceField key={name} name={name} onError={message=>notify(tr('Preferences not saved','偏好未保存'),message,true)}/>)}
 </SettingsSection><p className="settings-footnote">{tr('Valid preferences save on this device. Numbers save after a short pause. Project policy is not changed.','有效偏好保存在此设备；数字输入停顿后保存，不修改项目策略。')}</p></>;
 else if(section==='source') content=<IssueSourceSettings key={projectId} projectId={projectId} actionReady={actionReady} onAction={sourceAction} onNotice={notify}/>;
 else if(section==='capture'||section==='reporting'||section==='queue') content=<IssuePolicySettings key={`${projectId}:${section}:${access.verified}`} projectId={projectId} page={section} actionReady={actionReady} notify={notify}/>;
 else if(section==='execution') content=<IssueExecutionSettings key={projectId} projectId={projectId} actionReady={actionReady} notify={notify}/>;
 else if(section==='feishu') content=<FeishuIssueMergeSettings key={projectId} projectId={projectId} actionReady={actionReady} notify={notify}/>;
 else if(section==='boundary') content=<SettingsSection title={tr('Runtime boundaries','运行边界')}>
  {[['Control plane','控制配置','zf.yaml'],['Runtime truth','运行事实','TaskStore / EventWriter'],['Web mutation','Web 修改',snapshot?.runtime.actions?.mutation_enabled?'Token / session gated':'Read only'],['Project state directory','项目状态目录',snapshot?.project.state_dir||'—'],['Queued Triage','排队 Triage',tr('No Run assigned','尚未分配 Run')],['Lifecycle','生命周期',tr('Recorded evidence only','只使用已记录证据')]].map(([en,zh,value])=><SettingsRow key={en} label={tr(en,zh)}><span className="settings-readonly settings-path">{value}</span></SettingsRow>)}
 </SettingsSection>;
 else if(section==='access') content=<SettingsSection title={tr('Developer access','开发者访问')}><SettingsRow label={tr('Verified by the server','后端已验证')} description={tr('Switch modes without entering the token again while this grant remains valid. The token is not stored in browser storage.','授权有效时切换模式无需重复输入，Token 不保存在浏览器存储中。')}><span className="settings-badge"><Check size={14}/>{tr('Verified','已验证')}</span></SettingsRow><SettingsRow label={tr('Expires','到期时间')}><span className="settings-readonly">{access.expires_at?formatSettingsTime(new Date(access.expires_at*1000).toISOString()):'—'}</span></SettingsRow><div className="settings-card-actions"><button className="settings-button" onClick={()=>{enterUser();setSection('appearance');}}>{tr('Return to User mode','返回用户模式')}</button><button className="settings-button danger" onClick={()=>void forgetDeveloper().catch(e=>notify(tr('Could not revoke access','撤销权限失败'),String(e.message),true))}>{tr('Forget developer access','清除开发者授权')}</button></div></SettingsSection>;
 return <div className="settings-root settings-layout">
  <aside className={`settings-sidebar${mobileOpen?' open':''}`} aria-label={tr('Settings navigation','设置导航')}><h1>{tr('Settings','设置')}</h1><div className="settings-mode" role="group" aria-label={tr('Interface mode','界面模式')}>
   <button aria-pressed={access.mode==='user'} disabled={checking} onClick={()=>{enterUser();setSection('appearance');}}>{tr('User','用户')}</button>
   <button aria-pressed={access.mode==='developer'} disabled={checking||access.loading} onClick={()=>{if(access.mode!=='developer')void requestDeveloper();}}>{checking?tr('Checking…','验证中…'):tr('Developer','开发者')}</button>
  </div><nav>{sections.map((key,index)=>{const entry=settingsPages[key],Icon=icons[entry.icon];return <div key={key}>{(index===0||settingsPages[sections[index-1]].group[0]!==entry.group[0])&&<h2>{uiPair(...entry.group)}</h2>}<button className="settings-nav-link" aria-current={key===section?'page':undefined} onClick={()=>navigate(key)}><Icon size={16}/><span>{uiPair(...entry.label)}</span></button></div>;})}</nav><div className="settings-sidebar-status">{access.loading?tr('Checking access…','正在验证权限…'):access.verified?tr('Developer access remembered','已记住开发者授权'):tr('User mode','用户模式')}</div></aside>
  <main className="settings-content" ref={body}><button className="settings-mobile-menu settings-button" onClick={()=>setMobileOpen(x=>!x)}>{tr('Settings sections','设置分类')}</button><div className="settings-content-inner"><div className="settings-breadcrumb">{tr('Settings','设置')} <span>›</span> {access.mode==='developer'?tr('Developer','开发者'):tr('User','用户')}</div><header className="settings-page-heading"><h2>{visible?uiPair(...page.label):tr('Settings','设置')}</h2><p>{visible?uiPair(...page.description):tr('Developer access is required.','需要开发者权限。')}</p></header><div className="settings-sections">{visible?content:null}</div></div></main>
  {unlock&&<SettingsDialog title={tr('Enter Develop Token','输入 Develop Token')} description={tr('Your current User page stays active until the server validates the token.','后端验证成功前，保持当前用户页面。')} busy={checking} onClose={()=>{setUnlock(false);setToken('');setUnlockError('');}} footer={<><button className="settings-button" disabled={checking} onClick={()=>{setUnlock(false);setToken('');}}>{tr('Cancel','取消')}</button><button className="settings-button primary" disabled={checking||!token.trim()} onClick={()=>void requestDeveloper(token.trim())}>{checking?tr('Verifying…','验证中…'):tr('Unlock Developer','进入开发者')}</button></>}><label className="settings-dialog-label" htmlFor="settings-develop-token">Develop Token</label><input ref={input} id="settings-develop-token" data-autofocus type="password" autoComplete="off" value={token} onChange={e=>setToken(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&token.trim()&&!checking)void requestDeveloper(token.trim());}}/>{unlockError&&<p className="settings-error" role="alert">{unlockError}</p>}{!access.configured&&<p className="settings-footnote">{tr('Configure ZF_WEB_DEVELOPER_TOKEN on the Web server first.','请先在 Web 服务配置 ZF_WEB_DEVELOPER_TOKEN。')}</p>}</SettingsDialog>}
  <SettingsNotices items={notices} dismiss={dismiss}/>
 </div>;
}
