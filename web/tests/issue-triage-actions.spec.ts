import { expect,test } from "@playwright/test";
import { openWorkspace,selectIssue } from "./issue-workspace-support";
test("cancellation requires confirmation, one pill and safe default focus",async({page})=>{
 const api=await openWorkspace(page);await selectIssue(page,129);await page.getByRole('button',{name:'Cancel queued Triage',exact:true}).click();const d=page.getByRole('alertdialog');await expect(d).toBeVisible();expect(api.requests.filter(r=>r.method==='POST')).toHaveLength(0);await expect(d.locator('.group-badge')).toHaveCount(1);await expect(d.locator('.group-badge .group-badge')).toHaveCount(0);await expect(d.getByRole('button',{name:'Keep queued'})).toBeFocused();for(let i=0;i<8;i++){await page.keyboard.press('Tab');expect(await d.evaluate(e=>e.contains(document.activeElement))).toBe(true);}await d.getByRole('button',{name:'Cancel queued Triage',exact:true}).click();await expect(d).toHaveCount(0);expect(api.requests.filter(r=>r.path.endsWith('/cancel-queued'))).toHaveLength(1);await expect(page.getByRole('button',{name:'Start Triage',exact:true})).toBeVisible();
});
for(const status of ['reconciled','outcome_unknown'])test(`cancellation shows persisted labels and remote sync status: ${status}`,async({page})=>{
 const api=await openWorkspace(page);api.labelSyncStatus=status;
 const issue=api.data.issues.find(item=>item.number===129)!;
 issue.labels=['bug','triage_queued'];
 await selectIssue(page,129);
 await page.getByRole('button',{name:'Cancel queued Triage',exact:true}).click();
 await page.getByRole('alertdialog').getByRole('button',{name:'Cancel queued Triage',exact:true}).click();
 await expect(page.getByRole('button',{name:'Start Triage',exact:true})).toBeVisible();
 const labels=page.locator('.detail .meta > div').filter({has:page.locator('dt').filter({hasText:/^Labels$/})});
 await expect(labels).toContainText('bug');
 if(status==='reconciled'){
  await expect(labels).toContainText('triage_cancelled');
  await expect(labels).not.toContainText('triage_queued');
 }else{
  await expect(labels).toContainText('triage_queued');
  await expect(page.getByText('Cancellation saved. GitHub label synchronization is pending; check GitHub access and refresh to retry.',{exact:true})).toBeVisible();
 }
});
test('one mirror batch renders cancelled names and colors together for every row',async({page})=>{
 const api=await openWorkspace(page);
 const ids=[129,128];
 for(const issue of api.data.issues.filter(item=>ids.includes(item.number))){
  issue.labels=['bug','triage_queued'];issue.label_colors={triage_queued:'AAAAAA'};
  issue.workflow!.state='triage_queued';
 }
 await page.getByRole('button',{name:'Refresh',exact:true}).click();
 for(const id of ids)await expect(page.locator(`[data-issue-number="${id}"] .labels`)).toContainText('triage_queued');
 await page.evaluate((numbers)=>{
  const samples:string[][]=[];
  (window as unknown as {labelBatchSamples:string[][]}).labelBatchSamples=samples;
  new MutationObserver(()=>{
   samples.push(numbers.map(number=>{
    const badge=[...document.querySelectorAll(`[data-issue-number="${number}"] .label-badge`)].find(node=>node.textContent==='triage_cancelled');
    return badge?getComputedStyle(badge).color:'queued';
   }));
  }).observe(document.querySelector('.issue-list')||document.body,{subtree:true,childList:true,attributes:true,characterData:true});
 },ids);
 for(const issue of api.data.issues.filter(item=>ids.includes(item.number))){
  issue.labels=['bug','triage_cancelled'];issue.label_colors={triage_cancelled:'B60205'};
  issue.workflow!.state='triage_cancelled';
 }
 await page.getByRole('button',{name:'Refresh',exact:true}).click();
 for(const id of ids){
  const badge=page.locator(`[data-issue-number="${id}"] .label-badge`).filter({hasText:/^triage_cancelled$/});
  await expect(badge).toHaveCSS('color','rgb(76, 1, 2)');
 }
 const samples=await page.evaluate(()=>(window as unknown as {labelBatchSamples:string[][]}).labelBatchSamples);
 expect(samples.some(row=>row.every(color=>color==='rgb(76, 1, 2)'))).toBe(true);
 expect(samples.every(row=>row.every(color=>color==='queued')||row.every(color=>color==='rgb(76, 1, 2)'))).toBe(true);
});

for(const unavailable of ['missing-id','terminal-local-request'])test(`remote queued label is not a cancellable local queue: ${unavailable}`,async({page})=>{
 const api=await openWorkspace(page);
 const issue=api.data.issues.find(item=>item.number===129)!;
 if(unavailable==='missing-id'){issue.workflow!.triage_id='';issue.workflow!.run_id='';}
 else issue.workflow!.queued_cancel_available=false;
 await selectIssue(page,129);
 await expect(page.getByRole('button',{name:'No local queued request',exact:true})).toBeDisabled();
 await expect(page.getByRole('button',{name:'Cancel queued Triage',exact:true})).toHaveCount(0);
 await expect(page.locator('.context-face-front')).toContainText('There is no cancellable queued request in this project');
 expect(api.requests.filter(r=>r.path.endsWith('/cancel-queued'))).toHaveLength(0);
});
test("Start uses the selected Issue or an empty placeholder input",async({page})=>{
 const api=await openWorkspace(page);await selectIssue(page,105);await page.getByRole('button',{name:'Start Triage',exact:true}).click();const d=page.getByRole('dialog');await expect(d.locator('h3')).toHaveText('Start Triage');await expect(d.locator('h3')).toHaveCSS('text-transform','none');await expect(d.locator('.start-issue-card')).toContainText('Issue #105');await expect(d.getByRole('textbox')).toHaveCount(0);await d.getByRole('button',{name:'Queue Triage'}).click();await expect(d).toHaveCount(0);expect(api.requests.filter(r=>r.path.endsWith('/start-triage'))).toHaveLength(1);await expect(page.locator('[data-issue-number="105"] .row-state')).toContainText('Triage queued');await expect(page.locator('[data-issue-number="105"]')).toContainText('triage_queued');await page.locator('.clear-btn').click();await page.getByRole('button',{name:'Start Triage',exact:true}).click();await expect(d.getByRole('textbox')).toHaveValue('');await expect(d.getByRole('textbox')).toHaveAttribute('placeholder',/^Example:/);await page.keyboard.press('Escape');await expect(d).toHaveCount(0);
});
test("Manage Run gates permanent cancellation and calls the real Run API",async({page})=>{
 const api=await openWorkspace(page);await selectIssue(page,128);await page.getByRole('button',{name:'Manage Run',exact:true}).click();const d=page.getByRole('dialog');await expect(d.getByRole('button',{name:'Cancel permanently'})).toBeDisabled();await d.getByRole('button',{name:'Pause Run',exact:true}).click();await expect(d).toHaveCount(0);expect(api.requests.filter(r=>r.path.endsWith('/run-pause')).at(-1)?.data.payload.run_id).toBe(api.data.issues.find(x=>x.number===128)?.workflow?.run_id);expect(api.requests.filter(r=>r.path.endsWith('/run-pause')).at(-1)?.headers['x-zf-developer-token']).toBe('fixture-token');await expect(page.locator('.context-title')).toContainText('Triage paused');
});
test("Fix approval card resolves the exact proposal and keeps actions on the Issue detail",async({page})=>{
 const api=await openWorkspace(page);await selectIssue(page,122);
 const card=page.locator('.context-face-front');
 await expect(card).toContainText('A current exact proposal exists');
 await expect(card.getByTestId('issue-fix-approve')).toBeEnabled();
 await expect(card.getByTestId('issue-fix-reject')).toBeEnabled();
 await card.getByTestId('issue-fix-approve').click();
 await expect.poll(()=>api.requests.filter(r=>r.method==='POST'&&r.path.endsWith('/workflow-invoke')).length).toBe(1);
 expect(api.requests.find(r=>r.path.endsWith('/workflow-invoke'))?.data.payload.proposal_event_id).toBe('fixture-proposal');
 expect(api.requests.find(r=>r.path.endsWith('/workflow-invoke'))?.headers['x-zf-developer-token']).toBe('fixture-token');
});
test("verified candidate delivery actions stay in the current Issue card",async({page})=>{
 const api=await openWorkspace(page);await selectIssue(page,116);
 const card=page.locator('.context-face-front');
 await expect(card).toContainText('Approve for PR');
 await card.locator('textarea').fill('Scope and verification match the candidate contract.');
 await card.getByRole('button',{name:'Approve for PR',exact:true}).click();
 await expect.poll(()=>api.requests.filter(r=>r.path.endsWith('/candidate-review')).length).toBe(1);
 const request=api.requests.find(r=>r.path.endsWith('/candidate-review'))!;
 expect(request.data.verdict).toBe('approve');
 expect(request.data.candidate_sha).toBe('f1e2d3c4b5a6');
});
test("approved candidate exposes the controlled review-branch preparation action",async({page})=>{
 const api=await openWorkspace(page);await selectIssue(page,115);
 const card=page.locator('.context-face-front');
 await expect(card).toContainText('Prepare local review branch');
 await card.getByRole('button',{name:'Prepare local review branch',exact:true}).click();
 await expect.poll(()=>api.requests.filter(r=>r.path.endsWith('/prepare-publication')).length).toBe(1);
});
test("Fix worker recovery shows the durable dispatch reason",async({page})=>{
 const api=await openWorkspace(page);
 const issue=api.data.issues.find(item=>item.number===118)!;
 issue.workflow!.failure_event='fanout.child.dispatch_deferred';
 issue.workflow!.failure_reason='worker_transport_not_alive; worker_state_not_dispatchable:respawning';
 await page.getByRole('button',{name:'Refresh',exact:true}).click();
 await selectIssue(page,118);
 const card=page.locator('.context-face-front');
 await expect(card).toContainText('Fix recovering');
 await expect(card).toContainText('worker_transport_not_alive; worker_state_not_dispatchable:respawning');
});
test("PR and merged delivery states expose only controlled human handoff actions",async({page})=>{
 const api=await openWorkspace(page);
 await selectIssue(page,111);
 const card=page.locator('.context-face-front');
 await expect(card.getByRole('link',{name:'Open PR',exact:true})).toHaveAttribute('href',/\/pull\/\d+$/);
 await card.getByRole('button',{name:'Refresh PR',exact:true}).click();
 await expect.poll(()=>api.requests.filter(r=>r.path.endsWith('/pull-request/refresh')).length).toBe(1);
 await selectIssue(page,109);
 await expect(page.locator('.context-face-front')).toContainText('Human merge');
 await expect(page.getByRole('button',{name:/merge/i})).toHaveCount(0);
 await selectIssue(page,107);
 await expect(page.locator('.context-face-front')).toContainText('Automatic close');
 await expect(page.locator('.context-face-front')).toContainText('Disabled');
});
test("Need-info is a read-only developer projection linked to the remote Issue",async({page})=>{
 const api=await openWorkspace(page);await selectIssue(page,125);const c=page.locator('.context-face-front');
 await expect(c).toContainText('Questions are waiting for publication to the remote GitHub Issue');
 await expect(c.getByRole('link',{name:'Open GitHub Issue',exact:true})).toHaveAttribute('href',/github\.com\/rzwu-uisee\/zaofu-self-issue\/issues\/125$/);
 await expect(c.locator('textarea, select, form')).toHaveCount(0);
 expect(api.requests.filter(r=>r.method==='POST' && r.path.includes('needs-info'))).toHaveLength(0);
});
test("rejected requests never produce fake success and errors persist",async({page})=>{
 const api=await openWorkspace(page);await selectIssue(page,129);await page.getByRole('button',{name:'Cancel queued Triage',exact:true}).click();api.failNext=true;await page.getByRole('alertdialog').getByRole('button',{name:'Cancel queued Triage',exact:true}).click();await expect(page.locator('.toast.danger')).toBeVisible();await expect(page.getByRole('alertdialog')).toBeVisible();expect(api.data.issues.find(x=>x.number===129)!.workflow!.state).toBe('triage_queued');await page.keyboard.press('Escape');await page.waitForTimeout(5200);await expect(page.locator('.toast.danger')).toBeVisible();await page.locator('.toast-close').click();await expect(page.locator('.toast')).toHaveCount(0);
});
test("no deduplication; ten notifications scroll without shrinking cards",async({page})=>{
 await openWorkspace(page);await page.evaluate(()=>{const fixture=(window as any).issueFixture;for(let i=0;i<10;i++)fixture.notify({title:'Authorization',message:'A human must authorize this request before continuing.',tone:'warning'});});const region=page.locator('.notification-region').last();await expect(region.locator('.toast')).toHaveCount(10);const size=await region.locator('.notification-viewport').evaluate(e=>({visible:e.clientHeight,total:e.scrollHeight}));expect(size.total).toBeGreaterThan(size.visible);expect(await region.locator('.toast').last().evaluate(e=>e.scrollHeight<=e.clientHeight+1)).toBe(true);await region.locator('.toast-close').last().click();await expect(region.locator('.toast')).toHaveCount(9);
});
test("hover pauses every transient timer but not the user's manual dismiss",async({page})=>{
 await openWorkspace(page);await page.evaluate(()=>{const f=(window as any).issueFixture;for(let i=0;i<3;i++)f.notify({title:'Recovery '+i,message:'Automatically recovered.',tone:'info',duration:1200});f.notify({title:'Authorization',message:'Approval required.',tone:'warning'});});const region=page.locator('.notification-region').last();await expect(region.locator('.toast')).toHaveCount(4);await region.hover();await page.waitForTimeout(1600);await expect(region.locator('.toast')).toHaveCount(4);await page.mouse.move(40,20);await expect(region.locator('.toast')).toHaveCount(1);await expect(region.locator('.toast')).toContainText('Authorization');
});

test('cancelling a cross-project queue preserves its execution owner', async ({page}) => {
 const api=await openWorkspace(page);
 const issue=api.data.issues.find(item=>item.number===129)!;
 issue.workflow!.workflow_project_id='developer-project';
 issue.workflow!.workflow_project_name='Developer checkout';
 await selectIssue(page,129);
 const owner=page.locator('.detail .meta').getByRole('link',{name:'Developer checkout',exact:true});
 await expect(owner).toBeVisible();
 await page.getByRole('button',{name:'Cancel queued Triage',exact:true}).click();
 await page.getByRole('alertdialog').getByRole('button',{name:'Cancel queued Triage',exact:true}).click();
 await expect(page.getByRole('button',{name:'Start Triage',exact:true})).toBeVisible();
 await expect(owner).toBeVisible();
 await expect(owner).toHaveAttribute('href',/developer-project/);
});
