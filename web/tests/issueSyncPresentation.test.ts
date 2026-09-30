import { issueSyncPresentation } from '../src/pages/issueTriageModel.js';
function assert(value:unknown,message:string):void{if(!value)throw new Error(message);}
assert(issueSyncPresentation('fresh','','stopped').tone==='current','fresh GitHub mirror is green even when runtime stopped');
assert(issueSyncPresentation('fresh','','archived').tone==='current','runtime health cannot override actual mirror success');
assert(issueSyncPresentation('syncing','').tone==='syncing','active refresh is amber');
assert(issueSyncPresentation('never','').tone==='unavailable','no completed sync is neutral');
assert(issueSyncPresentation('failed','GitHub error','stopped').tone==='error','real sync error is red');
assert(issueSyncPresentation('rate_limited','','running').tone==='error','rate-limit response is not success');
assert(issueSyncPresentation('fresh','Fetch failed').tone==='error','request error is not hidden by older success');
assert(issueSyncPresentation('unexpected','').tone==='unavailable','unknown is not reported as success');
console.log('issueSyncPresentation assertions passed');
