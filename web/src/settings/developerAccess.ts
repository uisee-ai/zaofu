import { useSyncExternalStore } from 'react';
import { settingsRequest, type AccessStatus } from './settingsApi';
type State=AccessStatus & {mode:'user'|'developer';loading:boolean};
let state:State={configured:false,verified:false,expires_at:null,mode:'user',loading:true};
const subscribers=new Set<()=>void>();let generation=0;
// The remembered, server-verified grant selects Developer on each page load.
// An explicit User switch is temporary for this page, not a credential revoke.
let preferredMode:State['mode']='developer';
function publish(next:State){state=next;subscribers.forEach(fn=>fn());window.dispatchEvent(new Event('zf:developer-access-changed'));}
export function getDeveloperAccess(){return state;}
export function useDeveloperAccess(){return useSyncExternalStore(fn=>{subscribers.add(fn);return()=>{subscribers.delete(fn);}},()=>state,()=>state);}
export async function refreshDeveloperAccess(){
 const request=++generation;
 try{const status=await settingsRequest<AccessStatus>('/api/settings/access');if(request!==generation)return;
  publish({...state,...status,loading:false,mode:status.verified?preferredMode:'user'});
 }catch{if(request===generation)publish({...state,verified:false,mode:'user',loading:false});}
}
export async function enterDeveloper(token?:string):Promise<boolean>{
 const request=++generation;
 // Every transition validates server state, not a stored boolean.
 const status=await settingsRequest<AccessStatus>(token?'/api/settings/access/unlock':'/api/settings/access',token?{method:'POST',body:JSON.stringify({token})}:{});
 if(request!==generation)return false;
 if(status.verified)preferredMode='developer';
 publish({...status,loading:false,mode:status.verified?'developer':'user'});return status.verified;
}
export function enterUser(){++generation;preferredMode='user';publish({...state,loading:false,mode:'user'});}
export async function forgetDeveloper(){const request=++generation;await settingsRequest('/api/settings/access/forget',{method:'POST'});if(request===generation)publish({...state,verified:false,mode:'user',expires_at:null});}
