/** Personal presentation preferences, never a security capability or task store. */
export const preferenceDefaults = {
  language: 'en' as 'en' | 'zh' | 'ko' | 'ja', timeZone: 'local',
  autoRefresh: true, refreshSeconds: 10, refreshOnReturn: false,
  pageSize: 100, sortField: 'updated' as 'updated'|'created'|'number'|'name', sortDirection: 'desc' as 'asc'|'desc',
  rememberFilters: true, timestamp: 'relative' as 'relative'|'created'|'updated',
  labelCount: 3, historyCount: 3, labelPalette: 'soft' as 'soft'|'muted',
  hoverOpen: true, motion: 'standard' as 'standard'|'reduced'|'off', tooltips: true,
  tooltipDelay: 500, stageTooltipDelay: 1000, closeDelay: 105, visibleRows: 5,
  successSeconds: 5, infoSeconds: 5,
};
export type SettingsPreferences = typeof preferenceDefaults;
export type PreferenceKey = keyof SettingsPreferences;
export const editablePreferenceKeys = ['language', 'timeZone', 'autoRefresh', 'refreshSeconds', 'refreshOnReturn'] as const;
export type EditablePreferenceKey = typeof editablePreferenceKeys[number];
export const numericLimits: Partial<Record<PreferenceKey, readonly [number,number,number]>> = {
  refreshSeconds:[1,3600,1],pageSize:[1,500,1],labelCount:[1,50,1],historyCount:[1,50,1],
  tooltipDelay:[0,10000,1],stageTooltipDelay:[0,10000,1],closeDelay:[0,5000,1],visibleRows:[1,32,1],
  successSeconds:[1,120,.5],infoSeconds:[1,120,.5],
};
export const preferenceOptions: Partial<Record<PreferenceKey, readonly string[]>> = {
  language:['en','zh','ko','ja'],sortField:['updated','created','number','name'],sortDirection:['asc','desc'],
  timestamp:['relative','created','updated'],labelPalette:['soft','muted'],motion:['standard','reduced','off'],
};
export function validPreference(key:PreferenceKey, value:unknown):boolean {
  if (!Object.prototype.hasOwnProperty.call(preferenceDefaults,key)) return false;
  const range=numericLimits[key];
  if(range) return typeof value==='number' && Number.isFinite(value) && value>=range[0] && value<=range[1] && Math.abs((value-range[0])/range[2]-Math.round((value-range[0])/range[2]))<1e-7;
  const options=preferenceOptions[key];if(options) return typeof value==='string'&&options.includes(value);
  if(key==='timeZone') { if(value==='local')return true;try { if(typeof value!=='string'||!value)return false;new Intl.DateTimeFormat('en',{timeZone:value});return true;}catch{return false;} }
  return typeof value === typeof preferenceDefaults[key];
}
export function parsePreferences(value:unknown):SettingsPreferences {
  const result={...preferenceDefaults};
  if(!value||typeof value!=='object'||Array.isArray(value))return result;
  const raw=value as Record<string,unknown>;
  // Removed Settings pages must not leave invisible overrides active.
  for(const key of editablePreferenceKeys)if(validPreference(key,raw[key]))Object.assign(result,{[key]:raw[key]});
  return result;
}
