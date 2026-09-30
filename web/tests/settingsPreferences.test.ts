import { numericLimits, preferenceDefaults, validPreference, parsePreferences, type PreferenceKey } from '../src/settings/preferences.js';
function assert(value:unknown,message:string):asserts value{if(!value)throw new Error(message);}
let checks=0;
for(const key of Object.keys(preferenceDefaults) as PreferenceKey[]){assert(validPreference(key,preferenceDefaults[key]),key+' default');checks++;}
for(const [key,limit] of Object.entries(numericLimits) as [PreferenceKey,readonly [number,number,number]][]){
 const [min,max,step]=limit;
 for(const value of [min,max]){assert(validPreference(key,value),key+' boundary');checks++;}
 for(const value of [NaN,Infinity,min-step,max+step,min+step/2,String(min),null]){assert(!validPreference(key,value),key+' invalid '+value);checks++;}
}
assert(validPreference('timeZone','Asia/Shanghai'),'Shanghai timezone');assert(!validPreference('timeZone','Invalid/Zone'),'invalid timezone');
assert(!validPreference('__proto__' as PreferenceKey,{}),'prototype key not writable');
const value=parsePreferences({pageSize:500,developerVerified:true,language:'not-real',textSize:150});
assert(value.pageSize===100&&value.language==='en','retired overrides and invalid values use defaults');
const retired=parsePreferences({pageSize:1,sortField:'number',sortDirection:'asc',rememberFilters:false,timestamp:'updated',labelCount:1,historyCount:9,labelPalette:'muted',hoverOpen:false,motion:'off',tooltips:false,tooltipDelay:1,stageTooltipDelay:1,closeDelay:1,visibleRows:1,successSeconds:90,infoSeconds:90,showRecovery:false,pauseHidden:true});
assert(JSON.stringify(retired)===JSON.stringify(preferenceDefaults),'all removed controls remain at fixed defaults');
assert(!('textSize' in value),'retired text size preference must be ignored');
assert(!('developerVerified' in value),'not an authorization store');
assert(parsePreferences(null).refreshSeconds===10,'safe missing state');
console.log(`${checks+6} Settings preference assertions passed.`);
