import { expect, test, type Page } from '@playwright/test';

async function workspace(page:Page){
 const state={verified:false,revision:1,failNext:false,unavailable:false,requests:[] as {method:string;path:string;body:Record<string,unknown>}[],values:{enabled:true,automatic_detection_enabled:true,browser_capture_enabled:true,browser_capture_denied_urls:[] as string[],'ingress.enabled':true,'ingress.poll_interval_seconds':300} as Record<string,boolean|number|string[]>};
 await page.addInitScript(()=>localStorage.setItem('zf.webActionToken','settings-fixture-token'));
 const intercept=async(route:import('@playwright/test').Route)=>{
  const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();
  const body=(req.postData()?req.postDataJSON():{}) as Record<string,unknown>;state.requests.push({method,path,body});
  const reply=(value:unknown,status=200)=>route.fulfill({status,json:value});
  if(path==='/api/settings/access')return reply({configured:true,verified:state.verified,expires_at:state.verified?2000000000:null});
  if(path.endsWith('/access/unlock')){
   if(body.token!=='fixture-develop-token')return reply({ok:false,reason:'Invalid Develop Token'},403);
   state.verified=true;return reply({ok:true,configured:true,verified:true,expires_at:2000000000});
  }
  if(path.endsWith('/access/forget')){state.verified=false;return reply({ok:true,verified:false});}
  if(path.endsWith('/settings/issue')){
   if(state.unavailable)return reply({detail:'not found'},404);
   if(method==='PATCH'){
    if(state.failNext){state.failNext=false;return reply({ok:false,reason:'Config changed; reload'},409);}
    if(body.config_digest!==String(state.revision))return reply({ok:false,reason:'Config changed; reload'},409);
    const changes=body.changes as typeof state.values;
    if(Object.keys(changes).some(k=>k.startsWith('ingress.'))&&!state.verified)return reply({ok:false,reason:'Developer access required'},403);
    Object.assign(state.values,changes);state.revision++;
   }
   const values=Object.fromEntries(Object.entries(state.values).filter(([key])=>state.verified||!key.startsWith('ingress.')));
   return reply({ok:true,values,config_digest:String(state.revision),config_path:'/test-project/zf.yaml',scope:'project',inherited:false,writable:true,queue_requires_restart:true,restart_required:method==='PATCH'&&Object.keys((body.changes||{}) as object).some(k=>k.startsWith('ingress.'))});
  }
  return reply({ok:false,reason:'Unexpected fixture request'},404);
 };
 // Deliberately do not intercept /src/api/client.ts as a JSON endpoint.
 await page.route('**/api/settings/**',intercept);await page.route('**/api/projects/**',intercept);
 await page.goto('/tests/fixtures/settings-workspace/index.html');
 await expect(page.locator('.settings-nav-link')).toHaveCount(5);
 await expect(page.locator('.settings-mode').getByRole('button',{name:'Developer',exact:true})).toBeEnabled();
 return state;
}
async function nav(page:Page,name:string){await page.locator('.settings-nav-link').filter({hasText:name}).click();}
async function unlock(page:Page){
 await page.locator('.settings-mode').getByRole('button',{name:'Developer',exact:true}).click();
 await page.getByRole('textbox',{name:'Develop Token',exact:true}).fill('fixture-develop-token');
 await page.getByRole('button',{name:'Unlock Developer',exact:true}).click();
 await expect(page.locator('.settings-mode').getByRole('button',{name:'Developer',exact:true})).toHaveAttribute('aria-pressed','true');
}

test('browser-native zoom only: old textSize values no longer affect the UI',async({page})=>{
 await page.addInitScript(()=>localStorage.setItem('zf.issuePreferences',JSON.stringify({textSize:150,timeZone:'UTC'})));
 await workspace(page);
 await expect(page.getByRole('spinbutton',{name:'Text size',exact:true})).toHaveCount(0);
 await expect(page.getByRole('combobox',{name:'Time zone',exact:true})).toHaveValue('UTC');
 expect(await page.evaluate(()=>document.documentElement.style.fontSize)).toBe('');
 expect(await page.evaluate(()=>document.documentElement.style.getPropertyValue('--zf-text-scale'))).toBe('');
 await expect(page.locator('.settings-label').first()).toHaveCSS('font-size','13.5px');
 await page.reload();
 await expect(page.getByRole('spinbutton',{name:'Text size',exact:true})).toHaveCount(0);
});

test('verified developer is preferred on reload and re-entry; User remains a temporary choice',async({page})=>{
 const state=await workspace(page);await unlock(page);
 await nav(page,'Developer access');
 await page.getByRole('button',{name:'Return to User mode',exact:true}).click();
 await expect(page.locator('.settings-mode').getByRole('button',{name:'User',exact:true})).toHaveAttribute('aria-pressed','true');
 await page.reload();
 await expect(page.locator('.settings-mode').getByRole('button',{name:'Developer',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(page.locator('.settings-nav-link[aria-current="page"]')).toHaveText('Developer access');
 await nav(page,'Refresh & data');await page.reload();
 await expect(page.locator('.settings-nav-link[aria-current="page"]')).toHaveText('Refresh & data');
 await page.goto('about:blank');await page.goto('/tests/fixtures/settings-workspace/index.html');
 await expect(page.locator('.settings-mode').getByRole('button',{name:'Developer',exact:true})).toHaveAttribute('aria-pressed','true');
 expect(state.requests.filter(r=>r.path.endsWith('/unlock'))).toHaveLength(1);
 expect(await page.evaluate(()=>JSON.stringify(localStorage)+JSON.stringify(sessionStorage))).not.toContain('fixture-develop-token');
 state.verified=false;await page.reload();
 await expect(page.locator('.settings-mode').getByRole('button',{name:'User',exact:true})).toHaveAttribute('aria-pressed','true');
});

test('every personal preference is wired, persists, and does not PATCH project configuration',async({page})=>{
 const state=await workspace(page);
 const expected:Record<string,unknown>={};
 async function change(section:string,label:string,key:string,value:string|number|boolean){
  await nav(page,section);
  if(typeof value==='boolean')await page.getByRole('switch',{name:label,exact:true}).click();
  else if(typeof value==='number') {const field=page.getByRole('spinbutton',{name:label,exact:true});await field.fill(String(value));await field.blur();}
  else await page.getByRole('combobox',{name:label,exact:true}).selectOption(value);
  expected[key]=value;
  await expect.poll(()=>page.evaluate(k=>JSON.parse(localStorage.getItem('zf.issuePreferences')||'{}')[k],key)).toEqual(value);
 }
 await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption('dark');
 await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
 await change('Appearance','Time zone','timeZone','UTC');
 await unlock(page);
 for(const [section,label,key,value] of [
  ['Refresh & data','Page refresh interval','refreshSeconds',20],
  ['Refresh & data','Refresh when returning','refreshOnReturn',true],
  ['Refresh & data','Auto-refresh Triage','autoRefresh',false],
 ] as [string,string,string,string|number|boolean][])await change(section,label,key,value);
 await nav(page,'Runtime boundaries');await expect(page.locator('.settings-content')).toContainText('/test-state/settings');
 await page.locator('.settings-mode').getByRole('button',{name:'User',exact:true}).click();
 await change('Appearance','Language','language','zh');
 await page.reload();
 expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('zf.issuePreferences')||'{}'))).toMatchObject(expected);
 expect(state.requests.filter(r=>r.method==='PATCH')).toHaveLength(0);
});

test('flat user navigation, bilingual preferences and disabled Feishu integration',async({page})=>{
 await workspace(page);await expect(page.locator('[role=tablist]')).toHaveCount(0);
 await expect(page.locator('.settings-nav-link').filter({hasText:'List & details'})).toHaveCount(0);
 await page.getByRole('combobox',{name:'Language',exact:true}).selectOption('zh');
 await expect(page.locator('.settings-sidebar h1')).toHaveText('设置');
 await expect(page.locator('html')).toHaveAttribute('lang','zh-CN');
 await nav(page,'飞书');await expect(page.getByRole('button',{name:'此处暂不可用',exact:true})).toBeDisabled();
 await expect(page.locator('.settings-content')).toContainText('不采集凭据');
});


test('language switches the workspace shell immediately and persists for all four locales',async({page})=>{
 await workspace(page);
 for(const [language,lang,settings,tasks] of [
  ['zh','zh-CN','设置','任务'],
  ['ja','ja','設定','タスク'],
  ['ko','ko','설정','작업'],
  ['en','en','Settings','Tasks'],
 ]){
  const selector=page.locator('select').filter({has:page.locator('option[value="ja"]')});
  await selector.selectOption(language);
  await expect(page.locator('html')).toHaveAttribute('lang',lang);
  await expect(page.locator('.settings-sidebar h1')).toHaveText(settings);
  await expect(page.getByRole('button',{name:tasks,exact:true})).toBeVisible();
  await page.reload();
  await expect(selector).toHaveValue(language);
  await expect(page.getByRole('button',{name:tasks,exact:true})).toBeVisible();
 }
});

test('developer token failure does not change mode; server verification is reused and revocable',async({page})=>{
 const state=await workspace(page);
 await page.locator('.settings-mode').getByRole('button',{name:'Developer',exact:true}).click();
 await page.getByRole('textbox',{name:'Develop Token',exact:true}).fill('wrong');
 await page.getByRole('button',{name:'Unlock Developer',exact:true}).click();
 await expect(page.locator('.settings-dialog [role=alert]')).toContainText('Invalid');
 await expect(page.locator('.settings-mode').getByRole('button',{name:'User',exact:true})).toHaveAttribute('aria-pressed','true');
 await page.getByRole('textbox',{name:'Develop Token',exact:true}).fill('fixture-develop-token');await page.getByRole('button',{name:'Unlock Developer',exact:true}).click();
 await expect(page.locator('.settings-nav-link').filter({hasText:'Refresh & data'})).toBeVisible();
 await page.locator('.settings-mode').getByRole('button',{name:'User',exact:true}).click();
 await page.locator('.settings-mode').getByRole('button',{name:'Developer',exact:true}).click();
 await expect(page.locator('.settings-dialog')).toHaveCount(0);
 expect(state.requests.filter(r=>r.path.endsWith('/unlock'))).toHaveLength(2);
 expect(await page.evaluate(()=>JSON.stringify(localStorage))).not.toContain('fixture-develop-token');
 await nav(page,'Developer access');await page.getByRole('button',{name:'Forget developer access',exact:true}).click();
 await expect(page.locator('.settings-mode').getByRole('button',{name:'User',exact:true})).toHaveAttribute('aria-pressed','true');
 expect(state.verified).toBe(false);
});

test('numeric settings validate and persist without replacing focused inputs',async({page})=>{
 await workspace(page);await unlock(page);
 const field=page.getByRole('spinbutton',{name:'Page refresh interval'});
 await field.fill('37');await page.waitForTimeout(750);
 expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('zf.issuePreferences')||'{}').refreshSeconds)).toBe(37);
 await page.evaluate(()=>{(window as any).testInput=document.querySelector('input[aria-label="Page refresh interval"]');});
 await field.fill('0');await page.waitForTimeout(750);await expect(field).toHaveAttribute('aria-invalid','true');
 expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('zf.issuePreferences')||'{}').refreshSeconds)).toBe(37);
 expect(await page.evaluate(()=>(window as any).testInput===document.querySelector('input[aria-label="Page refresh interval"]'))).toBe(true);
});

test('capture policy is read from the server and reviewed before PATCH; errors remain visible',async({page})=>{
 const state=await workspace(page);await nav(page,'Browser capture');
 await expect(page.getByRole('button',{name:'Add URLs',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'Add URLs',exact:true}).click();
 await expect(page.getByRole('textbox',{name:'Blocked URLs',exact:true})).toBeFocused();
 await page.getByRole('textbox',{name:'Blocked URLs',exact:true}).fill('https://example.com/admin\nhttp://localhost:8002/private');
 await page.getByRole('button',{name:'Add to changes',exact:true}).click();
 expect(state.requests.filter(r=>r.method==='PATCH')).toHaveLength(0);
 await page.getByRole('button',{name:'Review changes',exact:true}).click();
 state.failNext=true;await page.getByRole('button',{name:'Save configuration',exact:true}).click();
 await expect(page.locator('.settings-dialog [role=alert]')).toContainText('Config changed');
 await page.getByRole('button',{name:'Save configuration',exact:true}).click();
 await expect(page.locator('.settings-dialog')).toHaveCount(0);
 expect(state.values.browser_capture_denied_urls).toEqual(['https://example.com/admin','http://localhost:8002/private']);
});

test('reporting and capture toggles, URL removal, discard and reload use the policy API',async({page})=>{
 const state=await workspace(page);
 async function save(){await page.getByRole('button',{name:'Review changes',exact:true}).click();await page.getByRole('button',{name:'Save configuration',exact:true}).click();await expect(page.locator('.settings-dialog')).toHaveCount(0);}
 await nav(page,'Self-Issue reporting');
 await page.getByRole('switch',{name:'Automatic issue discovery',exact:true}).click();
 await page.getByRole('switch',{name:'Self-Issue reporting',exact:true}).click();
 await expect(page.getByRole('switch',{name:'Automatic issue discovery',exact:true})).toBeDisabled();
 await save();expect(state.values.enabled).toBe(false);expect(state.values.automatic_detection_enabled).toBe(false);
 await page.reload();await expect(page.getByRole('switch',{name:'Self-Issue reporting',exact:true})).toHaveAttribute('aria-checked','false');
 await page.getByRole('switch',{name:'Self-Issue reporting',exact:true}).click();
 await page.getByRole('button',{name:'Discard',exact:true}).click();
 await expect(page.getByRole('switch',{name:'Self-Issue reporting',exact:true})).toHaveAttribute('aria-checked','false');
 state.values.browser_capture_denied_urls=['https://example.com/private'];
 await nav(page,'Browser capture');await page.getByRole('switch',{name:'Allow browser capture',exact:true}).click();
 await page.getByRole('button',{name:'Remove https://example.com/private',exact:true}).click();
 await save();expect(state.values.browser_capture_enabled).toBe(false);expect(state.values.browser_capture_denied_urls).toEqual([]);
 await page.reload();await expect(page.getByRole('switch',{name:'Allow browser capture',exact:true})).toHaveAttribute('aria-checked','false');
 await expect(page.locator('.settings-url-row')).toHaveCount(0);
 await page.getByRole('button',{name:'Add URLs',exact:true}).click();
 await page.getByRole('textbox',{name:'Blocked URLs',exact:true}).fill('file:///private');
 await page.getByRole('button',{name:'Add to changes',exact:true}).click();await expect(page.locator('.settings-dialog [role=alert]')).toContainText('Invalid HTTP(S) URL');
 await page.getByRole('button',{name:'Cancel',exact:true}).click();
 expect(state.requests.filter(r=>r.method==='PATCH')).toHaveLength(2);
});

test('only developer mode exposes the expanded background sync policy',async({page})=>{
 const state=await workspace(page);await unlock(page);await nav(page,'Queue & sync');
 const field=page.getByRole('spinbutton',{name:'GitHub sync interval'});await expect(field).toHaveValue('300');
 await field.fill('604801');await expect(page.getByRole('button',{name:'Review changes',exact:true})).toBeDisabled();
 await field.fill('604800');await page.getByRole('switch',{name:'Auto Triage queued',exact:true}).click();await page.getByRole('button',{name:'Review changes',exact:true}).click();await page.getByRole('button',{name:'Save configuration',exact:true}).click();
 await expect(page.locator('.settings-dialog')).toHaveCount(0);
 expect(state.values['ingress.poll_interval_seconds']).toBe(604800);
 expect(state.values['ingress.enabled']).toBe(false);
 await expect(page.locator('.toast').last()).toContainText('Restart the Web service');
});

test('missing backend settings API is actionable, does not fabricate defaults, and recovers after restart',async({page})=>{
 const state=await workspace(page);state.unavailable=true;await nav(page,'Self-Issue reporting');
 const alert=page.locator('.settings-content [role="alert"]');
 await expect(alert).toContainText('HTTP 404');
 await expect(alert).toContainText('Restart the Python Web');
 await expect(page.getByRole('switch')).toHaveCount(0);
 expect(state.requests.filter(r=>r.method==='PATCH')).toHaveLength(0);
 // A real server restart is represented by the endpoint becoming available.
 state.unavailable=false;await page.getByRole('button',{name:'Reload configuration',exact:true}).click();
 await expect(page.getByRole('switch',{name:'Self-Issue reporting',exact:true})).toBeVisible();
 await expect(alert).toHaveCount(0);
 await nav(page,'Browser capture');
 await expect(page.getByRole('switch',{name:'Allow browser capture',exact:true})).toBeVisible();
});

test('Settings uses the original application frame and fills it without a second border',async({page})=>{
 await workspace(page);
 const host=page.locator('.settings-host'),layout=page.locator('.settings-layout');
 await expect(layout).toHaveCSS('border-width','0px');
 await expect(layout).toHaveCSS('border-radius','0px');
 await expect(page.locator('.settings-content-inner')).toHaveCSS('max-width','none');
 async function assertFill(){
  const outer=(await host.boundingBox())!,inner=(await layout.boundingBox())!;
  for(const key of ['x','y','width','height'] as const)expect(Math.abs(outer[key]-inner[key]),key).toBeLessThanOrEqual(1);
  expect(await host.evaluate(e=>e.scrollHeight-e.clientHeight)).toBeLessThanOrEqual(1);
 }
 await assertFill();
 await page.setViewportSize({width:1536,height:740});await assertFill();
 await page.setViewportSize({width:1800,height:1100});await assertFill();
 const before=await page.locator('.settings-card').first().boundingBox();
 await page.setViewportSize({width:2100,height:1100});await assertFill();
 const after=await page.locator('.settings-card').first().boundingBox();
 expect(after!.width-before!.width).toBeGreaterThan(250);
 await page.setViewportSize({width:768,height:900});await assertFill();
 await expect(page.locator('.project-rail')).toBeVisible();
});

test('removed sections and controls stay absent, including old routes', async ({page}) => {
 await workspace(page);
 await expect(page.locator('.settings-nav-link').filter({hasText:'Notifications'})).toHaveCount(0);
 await expect(page.locator('.settings-content')).toContainText('Change ZaoFu time display.');
 await expect(page.locator('.settings-content')).not.toContainText('Issue content and evidence stay unchanged.');
 await unlock(page);
 for (const name of ['List & details','Hover & motion','Notifications']) {
  await expect(page.locator('.settings-nav-link').filter({hasText:name})).toHaveCount(0);
 }
 await expect(page.getByRole('switch',{name:'Pause when this tab is hidden'})).toHaveCount(0);
 await expect(page.getByRole('spinbutton',{name:'Issues per page'})).toHaveCount(0);
 await nav(page,'Queue & sync');
 await expect(page.getByText('Progress notifications',{exact:true})).toHaveCount(0);
 await expect(page.getByRole('switch',{name:'Show recovery updates'})).toHaveCount(0);
 await expect(page.locator('.settings-content')).toContainText('Issue status is synchronized from GitHub to ZaoFu.');
 for (const section of ['list','interaction','notifications']) {
  await page.goto(`/tests/fixtures/settings-workspace/index.html?settings=${section}`);
  await expect(page.locator('.settings-nav-link[aria-current=page]')).toHaveText('Refresh & data');
 }
});

test('Settings dark and system themes use the application card and sidebar palette', async ({page}) => {
 await workspace(page);
 await page.emulateMedia({colorScheme:'dark'});
 for (const theme of ['dark','system','light']) {
  await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
  const colors=await page.evaluate(()=>{
   const probe=document.createElement('div');probe.style.backgroundColor='var(--card)';document.body.append(probe);
   const card=getComputedStyle(probe).backgroundColor;probe.style.backgroundColor='var(--sidebar)';
   const sidebar=getComputedStyle(probe).backgroundColor;probe.remove();
   return {card,sidebar,actual:getComputedStyle(document.querySelector('.settings-card')!).backgroundColor,side:getComputedStyle(document.querySelector('.settings-sidebar')!).backgroundColor};
  });
  if(theme!=='light'){expect(colors.actual).toBe(colors.card);expect(colors.side).toBe(colors.sidebar);}
  await page.screenshot({path:`test-results/settings/settings-${theme}.png`});
 }
});

test('developer execution project is separate from user investigation source', async ({page}) => {
 await workspace(page);
 let revision=1, configured='';
 const writes:Record<string,unknown>[]=[];
 await page.route('**/api/projects/*/settings/issue-execution', async route => {
  const request=route.request();
  if(request.method()==='PATCH') {
   const body=request.postDataJSON(); writes.push(body);
   if(body.config_digest!==String(revision))return route.fulfill({status:409,json:{ok:false,reason:'Configuration changed; reload'}});
   if(body.execution_project_root==='/missing')return route.fulfill({status:422,json:{ok:false,reason:'Directory must contain zf.yaml'}});
   configured=body.execution_project_root;revision++;
  }
  return route.fulfill({json:{ok:true,valid:true,configured_root:configured,project_root:'/test-project',
   execution_project_root:configured||'/test-project',workflow_project_name:configured?'developer':'settings-fixture',
   state_dir:(configured||'/test-project')+'/runtime/state',config_digest:String(revision),config_path:'/test-project/zf.yaml',writable:true}});
 });
 await expect(page.locator('.settings-nav-link').filter({hasText:'Diagnosis & fix project'})).toHaveCount(0);
 await unlock(page); await nav(page,'Diagnosis & fix project');
 await expect(page.getByRole('combobox',{name:'Execution location',exact:true})).toHaveValue('current');
 await expect(page.locator('.settings-content')).toContainText('/test-project');
 await page.getByRole('combobox',{name:'Execution location',exact:true}).selectOption('custom');
 const path=page.getByRole('textbox',{name:'Execution project directory',exact:true});
 await path.fill('/missing'); await page.getByRole('button',{name:'Save execution project',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('Directory must contain zf.yaml');
 await path.fill('/developer'); await page.getByRole('button',{name:'Save execution project',exact:true}).click();
 await expect(page.locator('.settings-content')).toContainText('Saved. Applies to new Issues');
 expect(writes[1]).toEqual({execution_project_root:'/developer',config_digest:'1'});
 await page.reload();
 await expect(page.getByRole('textbox',{name:'Execution project directory',exact:true})).toHaveValue('/developer');
 await page.getByRole('combobox',{name:'Execution location',exact:true}).selectOption('current');
 await page.getByRole('button',{name:'Save execution project',exact:true}).click();
 await expect.poll(()=>configured).toBe('');
 await page.setViewportSize({width:800,height:900});
 await expect(page.getByRole('button',{name:'Save execution project',exact:true})).toBeVisible();
 await page.screenshot({path:test.info().outputPath('execution-project.png'),fullPage:true});
});
