import { expect, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
const seed = JSON.parse(readFileSync(new URL("./fixtures/issue-workspace/data.json", import.meta.url), "utf8"));
export interface RequestRecord { method:string; path:string; params:Record<string,string>; data:Record<string,any>; headers:Record<string,string> }
/** All routes are intercepted. No test may authorize, cancel, publish or merge on a live backend. */
export async function openWorkspace(page:Page) {
  const data = structuredClone(seed);
  const requests:RequestRecord[] = [];
  const api = { requests, data, failNext:false, authorization:false, delayDetail:0, delayRefresh:0, runtimeState:"running", labelSyncStatus:"reconciled", intake:{status:"idle",error:""}, comments:[] as unknown[] };
  await page.addInitScript(() => localStorage.setItem("zf.webActionToken", "fixture-token"));
  // Scope the fixture intercept to the actual API prefix. A broad **/api/**
  // glob also matches the production module path /src/api/client.ts.
  await page.route("**/api/projects/**", async (route:Route) => {
    const req=route.request(), url=new URL(req.url()), path=url.pathname;
    const params=Object.fromEntries(url.searchParams), method=req.method();
    const body=req.postData() ? req.postDataJSON() : {};
    requests.push({method,path,params,data:body,headers:req.headers()});
    const respond=(value:unknown,status=200)=>route.fulfill({status,json:value});
    if (method==="POST" && api.failNext) {api.failNext=false;await respond({ok:false,status:"error",reason:"Request is no longer current."},409);return;}
    if (method === "POST" && path.endsWith("/refresh")) {
      if (api.delayRefresh) await new Promise(resolve => setTimeout(resolve, api.delayRefresh));
      data.sync.status = "fresh"; data.sync.error = ""; data.sync.last_success_at = new Date().toISOString();
      await respond({ok:true,status:"fresh"}); return;
    }
    if (path.endsWith("/health/summary")) {await respond({runtime_state:api.runtimeState});return;}
    if (path.endsWith("/issue-triage/summary")) {
      const states:Record<string,number>={},labels:Record<string,number>={},authors:Record<string,number>={},sources:Record<string,number>={};
      for (const item of data.issues) {
        for (const state of new Set([item.github_state,item.workflow?.state||item.github_state])) states[state]=(states[state]||0)+1;
        for (const label of item.labels) labels[label]=(labels[label]||0)+1;
        authors[item.author_login]=(authors[item.author_login]||0)+1;sources[item.source]=(sources[item.source]||0)+1;
      }
      await respond({schema_version:"issue-triage-summary.v1",repository:data.sync.repository,repository_url:"https://github.com/owner/repository",new_issue_url:"https://github.com/owner/repository/issues/new",total:data.issues.length,states,labels,authors,sources,groups:{},label_colors:{},author_states:{},sync:data.sync,intake:api.intake});return;
    }
    if (path.endsWith("/issue-triage")) {
      let items=data.issues.filter(item=>[item.title,item.number,item.author_login,item.source,...item.labels].join(" ").toLowerCase().includes((params.q||"").toLowerCase()));
      for (const kind of ["states","labels","authors","sources"]) if (params[kind]) {
        const chosen=JSON.parse(params[kind]) as string[];
        items=items.filter(item=>(kind==="states"?[item.github_state,item.workflow?.state||"mirrored"]:kind==="labels"?item.labels:kind==="authors"?[item.author_login]:[item.source]).some(value=>chosen.includes(value)));
      }
      const field=({updated:"updated_at",created:"created_at",name:"title",number:"number"} as const)[params.order_by as "updated"]||"updated_at";
      items.sort((a,b)=>{const x=a[field],y=b[field];return (x<y?-1:x>y?1:0)*(params.order_direction==="asc"?1:-1);});
      const total=items.length,cursor=Number(params.cursor||0),limit=Number(params.limit||30);
      await respond({schema_version:"issue-triage-page.v1",repository:data.sync.repository,items:items.slice(cursor,cursor+limit),total,cursor,next_cursor:cursor+limit<total?cursor+limit:null,sync:data.sync});return;
    }
    const detail=path.match(/\/issue-triage\/(\d+)$/);
    if (detail) {
      const item=data.issues.find(item=>item.number===Number(detail[1]))!;
      if(api.delayDetail) await new Promise(resolve=>setTimeout(resolve,api.delayDetail));
      await respond({schema_version:"issue-triage-detail.v1",issue:item,body:"## DESCRIBE THE BUG\n\n"+item.body,comments:api.comments,delivery:(data.deliveries as Record<string,unknown>)[detail[1]],trust:"untrusted_external_input"});return;
    }
    if (path.endsWith("/pending-proposals")) {await respond({items:data.issues.filter(item=>item.workflow?.state==="awaiting_fix_approval").map(item=>({proposal_id:item.workflow!.proposal_id,proposal_event_id:"fixture-proposal",proposal_digest:"fixture-digest",valid:true,action:"workflow-invoke",payload:{task_id:item.workflow!.task_id,source_revision:item.workflow!.source_revision,allowed_paths:["web/src/pages"]}}))});return;}
    if (path.endsWith("/cancel-queued")) {
      const number=Number(path.match(/issue-triage\/(\d+)/)![1]);const item=data.issues.find(item=>item.number===number)!;item.workflow!.state=item.workflow!.state==="fix_queued"?"fix_cancelled":"triage_cancelled";
      if(api.labelSyncStatus==="reconciled") item.labels=[...item.labels.filter(label=>!['triage_queued','fix_queued'].includes(label)),item.workflow!.state];
      await respond({ok:true,status:"cancelled",lifecycle_label_sync:{status:api.labelSyncStatus}});return;
    }
    if (path.endsWith("/start-triage")) {
      const number=Number(JSON.stringify(body).match(/(?:#|issues\/)(\d+)/)![1]);const item=data.issues.find(item=>item.number===number)!;
      item.workflow=item.workflow||structuredClone(data.issues.find(item=>item.number===129)!.workflow!);item.workflow.state="triage_queued";item.workflow.run_id="";item.labels=[...item.labels.filter(label=>!label.startsWith("triage_")),"triage_queued"];
      await respond({ok:true,queued:true,issue_number:number,issue:item});return;
    }
    if (method==="POST") {
      const action=path.split("/").pop()!,payload=body.payload||body;
      if(action.startsWith("run-")){const item=data.issues.find(item=>item.workflow?.run_id===payload.run_id)!;const isFix=item.workflow!.state.startsWith("fix")||item.workflow!.state==="verifying";item.workflow!.state=action==="run-pause"?(isFix?"fix_paused":"triage_paused"):action==="run-resume"?(isFix?"fixing":"triaging"):(isFix?"fix_cancelled":"triage_cancelled");}
      if (action==="external-issue-needs-info-submit"&&api.authorization){await respond({ok:false,status:"authorization_required",result:{status:"authorization_required"}});return;}
      if(action.endsWith("github-device-start")){await respond({ok:true,result:{status:"authorization_required",transaction_id:"fixture-device",verification_uri:"https://github.com/login/device",user_code:"TEST-CODE",interval:5}});return;}
      if(action.endsWith("github-device-poll")){await respond({ok:true,result:{status:"authorization_pending",retry_after:5}});return;}
      await respond({ok:true,status:"accepted"});return;
    }
    await respond({ok:true,items:[]});
  });
  await page.goto("/tests/fixtures/issue-workspace/index.html");
  await expect(page.locator(".issue-row")).toHaveCount(32);
  return api;
}
export async function selectIssue(page:Page,number:number){await page.getByRole("textbox",{name:"Search issues"}).fill(String(number));const row=page.locator(`[data-issue-number="${number}"]`);await expect(page.locator(".issue-row")).toHaveCount(1);await row.click();await expect(page.locator(".detail-kicker")).toContainText(`#${number}`);}
