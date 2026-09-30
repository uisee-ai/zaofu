import { useSyncExternalStore } from 'react';
import { editablePreferenceKeys, parsePreferences, preferenceDefaults, validPreference, type EditablePreferenceKey, type SettingsPreferences } from './preferences.js';
const KEY='zf.issuePreferences';
let current:SettingsPreferences={...preferenceDefaults};
try{current=parsePreferences(JSON.parse(localStorage.getItem(KEY)||'null'));}catch{/* Use validated defaults when storage is unavailable. */}
const listeners=new Set<()=>void>();
function applyDocumentPreferences(){
 if(typeof document==='undefined')return;
 document.documentElement.lang=({en:'en',zh:'zh-CN',ko:'ko',ja:'ja'} as const)[current.language] || 'en';
}
applyDocumentPreferences();
const changed=()=>{applyDocumentPreferences();listeners.forEach(fn=>fn());};
export function getSettingsPreferences(){return current;}
export function setSettingsPreference<K extends EditablePreferenceKey>(key:K,value:SettingsPreferences[K]){
 if(!editablePreferenceKeys.includes(key)||!validPreference(key,value))throw new Error('Invalid setting value.');
 const next={...current,[key]:value};
 // Publish only after storage succeeds; a failed save must not claim success.
 localStorage.setItem(KEY,JSON.stringify(next));current=next;changed();
}
export function useSettingsPreferences(){return useSyncExternalStore(fn=>{listeners.add(fn);return()=>{listeners.delete(fn);}},getSettingsPreferences,()=>preferenceDefaults);}
if(typeof window!=='undefined')window.addEventListener('storage',event=>{
 if(event.key===KEY){try{current=parsePreferences(JSON.parse(event.newValue||'null'));changed();}catch{/* Ignore corrupted cross-tab writes. */}}
});
export function formatSettingsTime(value:string|undefined,options: Intl.DateTimeFormatOptions={}){
 if(!value||!Number.isFinite(Date.parse(value)))return '—';
 const prefs=current;
 return new Intl.DateTimeFormat(({en:'en-GB',zh:'zh-CN',ko:'ko-KR',ja:'ja-JP'} as const)[prefs.language] || 'en-GB',{
  year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,
  ...(prefs.timeZone==='local'?{}:{timeZone:prefs.timeZone}),...options,
 }).format(new Date(value));
}
