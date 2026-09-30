import { uiPair, uiText } from './uiText';
import { useEffect, useRef, useState } from 'react';
import { numericLimits, preferenceOptions, validPreference, type EditablePreferenceKey } from './preferences';
import { fieldCopy, optionNames } from './settingsFields';
import { SettingsRow } from './SettingsPrimitives';
import { useSettingsPreferences, setSettingsPreference } from './useSettingsPreferences';
export function PreferenceField({name,onError}:{name:EditablePreferenceKey;onError:(message:string)=>void}){
 const prefs=useSettingsPreferences(),lang=prefs.language==='zh'?1:0,info=fieldCopy[name],range=numericLimits[name],options=preferenceOptions[name];
 const value=prefs[name];const [input,setInput]=useState(String(value)),[error,setError]=useState('');
 const timer=useRef<ReturnType<typeof setTimeout>>();const pending=useRef<unknown>(undefined);const latest=useRef(onError);latest.current=onError;
 function persist(next:unknown){if(!validPreference(name,next)){setError(uiPair('Enter a valid value within the range.','请输入有效范围内的值。'));return;}try{setSettingsPreference(name,next as never);pending.current=undefined;setError('');}catch{const text=uiPair('Browser preferences could not be saved.','浏览器无法保存偏好。');setError(text);latest.current(text);}}
 useEffect(()=>{setInput(String(value));},[value]);
 useEffect(()=>()=>{clearTimeout(timer.current);if(pending.current!==undefined&&validPreference(name,pending.current)){try{setSettingsPreference(name,pending.current as never);}catch{latest.current('Browser preferences could not be saved.');}}},[name]);
 const disabled=(['refreshSeconds','refreshOnReturn'].includes(name)&&!prefs.autoRefresh);
 const save=(next:unknown)=>{clearTimeout(timer.current);persist(next);};
 let control;
 if(range) control=<div className="settings-number-wrap"><div className="settings-number"><input type="number" aria-label={uiPair(...info.label)} min={range[0]} max={range[1]} step={range[2]} disabled={disabled} value={input} aria-invalid={!!error} onChange={e=>{
  setInput(e.target.value);clearTimeout(timer.current);const n=e.target.value===''?NaN:Number(e.target.value);pending.current=n;
  if(!validPreference(name,n)){setError(`${range[0]}–${range[1]} (${range[2]})`);return;}setError('');timer.current=setTimeout(()=>persist(n),650);
 }} onBlur={()=>{if(pending.current!==undefined)save(pending.current);}}/><span>{info.unit?uiPair(...info.unit):undefined}</span></div><small>{range[0]}–{range[1]}</small></div>;
 else if(options||name==='timeZone')control=<select aria-label={uiPair(...info.label)} value={String(value)} onChange={e=>save(e.target.value)}>{(options||['local','UTC','Asia/Shanghai','Asia/Tokyo','America/New_York','Europe/London']).map(x=><option key={x} value={x}>{optionNames[x]?uiPair(...optionNames[x]):x}</option>)}</select>;
 else control=<button type="button" className="settings-switch" role="switch" aria-label={uiPair(...info.label)} aria-checked={Boolean(value)} disabled={disabled} onClick={()=>save(!value)}/>;
 return <SettingsRow label={uiPair(...info.label)} description={uiPair(...info.description)}><div className="settings-field-body">{control}{error&&<small className="settings-error" role="alert">{error}</small>}</div></SettingsRow>;
}
