import { normalizeWebActionToken } from '../api/client';
export interface AccessStatus { configured:boolean; verified:boolean; expires_at:number|null }
export interface IssuePolicy {
 ok:boolean;values:Record<string,boolean|number|string[]>;config_digest:string;config_path:string;
 scope:'project'|'workspace';inherited:boolean;writable:boolean;queue_requires_restart:boolean;
 restart_required?:boolean;changed_fields?:string[];
}
type FailureKind = 'endpoint_missing' | 'method_missing' | 'invalid_response' | 'http';
export class SettingsRequestError extends Error {
 constructor(message:string, readonly status:number, readonly path:string, readonly method:string, readonly kind:FailureKind='http') {
  super(message); this.name='SettingsRequestError';
 }
}
/** Keep the original HTTP failure; never substitute a default config after 404. */
export function settingsErrorMessage(error:unknown, language:'en'|'zh'|'ko'|'ja'):string {
 if(error instanceof SettingsRequestError){
  const detail=`${error.method} ${error.path} · HTTP ${error.status}`;
  if(error.kind==='endpoint_missing'||error.kind==='method_missing')return language==='zh'
   ? `当前连接的后端尚未提供此设置接口。请从已更新的工作树重启正在服务此页面的 Python Web 进程；使用 Vite 时同时核对 API 代理目标。\n${detail}`
   : `The connected backend does not expose this settings API. Restart the Python Web process serving this page from the updated worktree; with Vite, also check its API target.\n${detail}`;
  if(error.kind==='invalid_response')return language==='zh'
   ? `设置接口返回了非预期内容，未载入任何默认配置。请检查 Web 服务版本和 API 代理目标。\n${detail}`
   : `The settings API returned an unexpected response. No default configuration was loaded. Check the Web service version and API target.\n${detail}`;
  return `${error.message}\n${detail}`;
 }
 return error instanceof Error?error.message:String(error);
}
export async function settingsRequest<T>(url:string,init:RequestInit={}):Promise<T>{
 let token='';try{token=normalizeWebActionToken(localStorage.getItem('zf.webActionToken')||'');}catch{}
 const method=(init.method||'GET').toUpperCase();
 const response=await fetch(url,{...init,credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json',
  ...(init.body?{'Content-Type':'application/json'}:{}),...(token?{'X-ZF-Web-Token':token}:{}),...init.headers}});
 let data:Record<string,unknown>;
 try{
  const value:unknown=await response.json();
  if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Not an object');
  data=value as Record<string,unknown>;
 }catch{
  // An HTML 404/405 is also a deployment/proxy error, not a missing user setting.
  throw new SettingsRequestError('Unexpected settings response',response.status,url,method,
   response.status===404?'endpoint_missing':response.status===405?'method_missing':'invalid_response');
 }
 if(!response.ok||data.ok===false){
  const detail=data.detail;
  const nested=detail&&typeof detail==='object'&&!Array.isArray(detail)?(detail as Record<string,unknown>).reason:undefined;
  const reason=typeof data.reason==='string'?data.reason:typeof detail==='string'?detail:typeof nested==='string'?nested:`Request failed (${response.status})`;
  const missing=response.status===404&&/^not found$/i.test(reason.trim());
  throw new SettingsRequestError(reason,response.status,url,method,missing?'endpoint_missing':response.status===405?'method_missing':'http');
 }
 return data as T;
}
export async function getIssuePolicy(id:string):Promise<IssuePolicy>{
 const path=`/api/projects/${encodeURIComponent(id)}/settings/issue`;
 const value=await settingsRequest<IssuePolicy>(path);
 if(value.ok!==true||!value.values||typeof value.values!=='object'||Array.isArray(value.values)||typeof value.config_digest!=='string')
  throw new SettingsRequestError('Invalid Issue settings projection',200,path,'GET','invalid_response');
 return value;
}
export const saveIssuePolicy=(id:string,config:IssuePolicy,changes:IssuePolicy['values'])=>settingsRequest<IssuePolicy>(`/api/projects/${encodeURIComponent(id)}/settings/issue`,{
 method:'PATCH',body:JSON.stringify({config_digest:config.config_digest,changes,confirm_workspace_scope:config.inherited})
});
