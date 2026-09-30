import { test,expect } from "@playwright/test";
import { openWorkspace,selectIssue } from "./issue-workspace-support";

test("white surfaces, precise pills, typography and original SVGs",async({page})=>{
  await openWorkspace(page);const style=await page.locator('.issue-title').first().evaluate(e=>({font:getComputedStyle(e).fontSize,weight:getComputedStyle(e).fontWeight}));expect(style).toEqual({font:"14.5px",weight:"600"});
  await expect(page.locator('.header')).toHaveCSS('background-color','rgb(255, 255, 255)');await expect(page.locator('.tab.active')).toHaveCSS('font-size','14px');
  const pill=page.locator('.row-state .group-badge').first();await expect(pill).toHaveCSS('padding','3px 8px');await expect(pill).toHaveCSS('font-weight','500');
  await selectIssue(page,128);expect(await page.locator('.context-flip-button svg path').first().getAttribute('d')).toBe('M7 6h4a3 3 0 0 1 3 3v6a3 3 0 0 0 3 3');
});
test("Delivery shows five complete rows and floats without moving content",async({page})=>{
  await openWorkspace(page);const content=await page.locator('.content').boundingBox();const host=page.locator('[data-issue-dropdown="stage-delivery"]');await host.hover();await page.waitForTimeout(280);const menu=host.locator('.stage-menu');const rows=await menu.evaluate(e=>{const box=e.getBoundingClientRect();return {height:box.height,bottom:Array.from(e.children,c=>c.getBoundingClientRect().bottom-box.top)};});expect(rows.bottom[4]).toBeLessThan(rows.height);expect(rows.bottom[5]).toBeGreaterThan(rows.height);expect(await page.locator('.content').boundingBox()).toEqual(content);
});
for(const id of ['state','label','contributor','source'])test(`${id} hover, pin, unpin, Select all toggle and Clear`,async({page})=>{
  await openWorkspace(page);const host=page.locator(`[data-issue-dropdown="${id}-filter"]`);await host.hover();await expect(host).toHaveClass(/open/);await host.locator('.dropdown-pin').click();await page.mouse.move(800,70);await expect(host).toHaveClass(/pinned/);await page.waitForTimeout(280);const box=await host.locator('.menu').boundingBox();
  const all=host.getByRole('checkbox',{name:/Select all/});await all.click();await expect(all).toHaveAttribute('aria-checked','true');await expect(page.locator('.clear-btn')).toBeEnabled();expect(await host.locator('.menu').boundingBox()).toEqual(box);await all.click();await expect(all).toHaveAttribute('aria-checked','false');await host.locator('.dropdown-pin').click();await expect(host).not.toHaveClass(/open/);
});
test("OR label selection does not select the Issue",async({page})=>{
  const api=await openWorkspace(page);const row=page.locator('[data-issue-number="128"]');await row.getByRole('button',{name:'Filter issues by label bug',exact:true}).click();await row.getByRole('button',{name:'Filter issues by label performance',exact:true}).click();await expect.poll(()=>JSON.parse(api.requests.filter(r=>r.path.endsWith('/issue-triage')).at(-1)!.params.labels||'[]')).toEqual(['bug','performance']);await expect(page.locator('.detail')).toHaveCount(0);
});
test("sort, pagination and deselection preserve or release the correct detail",async({page})=>{
  const api=await openWorkspace(page);
  // Pagination remains covered with more than the fixed 100-row default.
  for(let number=31;number<=100;number++)api.data.issues.push({...structuredClone(api.data.issues[0]),number,issue_key:`github:fixture:${number}`});
  const order=page.locator('.order-wrap');await order.hover();await order.getByRole('radio',{name:'Issue number'}).click();await order.getByRole('button',{name:'Ascending',exact:true}).click();await expect(page.locator('.issue-row').first()).toHaveAttribute('data-issue-number','31');await page.keyboard.press('Escape');await order.getByRole('button',{name:'Descending',exact:true}).click();await expect(page.locator('.issue-row').first()).toHaveAttribute('data-issue-number','132');
  await page.locator('[data-issue-number="128"]').click();await expect(page.locator('.detail')).toBeVisible();await page.locator('.detail-scroll').evaluate(e=>e.scrollTop=200);await page.getByRole('button',{name:'Next',exact:true}).click();await expect(page.locator('.issue-row')).toHaveCount(2);await expect(page.locator('.detail-kicker')).toContainText('#128');expect(await page.locator('.detail-scroll').evaluate(e=>e.scrollTop)).toBe(200);await page.getByRole('button',{name:'Previous',exact:true}).click();await page.locator('[data-issue-number="128"]').click();await expect(page.locator('.detail')).toHaveCount(0);
});
test("search and expanded Issue survive a page reload through the URL state", async ({page}) => {
  await openWorkspace(page);
  await page.getByRole('textbox',{name:'Search issues'}).fill('128');
  await expect(page.locator('.issue-row')).toHaveCount(1);
  await page.locator('[data-issue-number="128"]').click();
  await expect(page.locator('.detail-kicker')).toContainText('#128');
  await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBe('128');
  await expect.poll(() => new URL(page.url()).searchParams.get('issue')).toBe('128');
  await page.reload();
  await expect(page.locator('.detail-kicker')).toContainText('#128');
  await expect(page.getByRole('textbox',{name:'Search issues'})).toHaveValue('128');
});

test("developer Triage capability is sent separately from the reporter token", async ({page}) => {
  const api = await openWorkspace(page);
  await expect.poll(() => {
    const request = api.requests.find(item => item.path.endsWith("/issue-triage/summary"));
    return request?.headers["x-zf-developer-token"];
  }).toBe("fixture-token");
  const summaryRequest = api.requests.find(item => item.path.endsWith("/issue-triage/summary"));
  expect(summaryRequest?.headers["x-zf-web-token"]).toBe("fixture-token");
});

test("search and expanded Issue survive leaving and returning to Triage", async ({page}) => {
  await openWorkspace(page);
  await page.getByRole('textbox',{name:'Search issues'}).fill('128');
  await expect(page.locator('.issue-row')).toHaveCount(1);
  await page.locator('[data-issue-number="128"]').click();
  await expect(page.locator('.detail-kicker')).toContainText('#128');
  await page.getByRole('tab',{name:'RUNTIME INTERVENTIONS',exact:true}).click();
  await expect(page.getByText('Runtime interventions (unchanged)',{exact:true})).toBeVisible();
  await page.getByRole('tab',{name:'GITHUB ISSUE',exact:true}).click();
  await expect(page.locator('.detail-kicker')).toContainText('#128');
  await expect(page.getByRole('textbox',{name:'Search issues'})).toHaveValue('128');
});

test("search and all filter dimensions survive App-level navigation", async ({page}) => {
  await openWorkspace(page);
  await page.evaluate(() => window.history.replaceState({}, '', '/tests/fixtures/issue-workspace/index.html?page=loop'));
  await page.evaluate(() => window.sessionStorage.setItem('zf.issue-triage.route:fixture', JSON.stringify({query:'128',states:['open'],labels:['bug'],authors:['runtime-operator'],sources:['github_web'],orderBy:'name',direction:'asc',cursor:0,issueNumber:128})));
  await page.reload();
  await expect(page.locator('.detail-kicker')).toContainText('#128');
  await expect(page.getByRole('textbox',{name:'Search issues'})).toHaveValue('128');
  await expect(page.locator('.issue-row')).toHaveCount(1);
  await expect(page.locator('[data-issue-dropdown="label-filter"] .filter-trigger')).toContainText('1 selected');
  await expect(page.locator('[data-issue-dropdown="contributor-filter"] .filter-trigger')).toContainText('1 selected');
  await expect(page.locator('[data-issue-dropdown="source-filter"] .filter-trigger')).toContainText('1 selected');
  await expect(page.locator('[data-issue-dropdown="order"] .order-trigger')).toContainText('Issue title');
});

test("exhausted Need-info is read-only and explains the maintainer restart path", async ({page}) => {
  await openWorkspace(page);
  await selectIssue(page, 124);
  await expect(page.getByText(/No further questions will be sent automatically/i)).toBeVisible();
  await expect(page.getByRole('button', {name:/Submit information and restart Triage/i})).toHaveCount(0);
});
test("history is manual, only three rows, floating and resets across Issues",async({page})=>{
  await openWorkspace(page);await selectIssue(page,128);await expect(page.locator('.triage-history-row')).toHaveCount(3);const trigger=page.locator('.triage-history-toggle');await trigger.hover();await page.waitForTimeout(600);await expect(page.locator('.issue-history-portal')).toHaveCount(0);const box=await page.locator('.context-flip').boundingBox();await trigger.click();await expect(page.locator('.triage-history-record')).toHaveCount(12);expect(await page.locator('.context-flip').boundingBox()).toEqual(box);await page.keyboard.press('Escape');await selectIssue(page,127);await expect(page.locator('.issue-history-portal')).toHaveCount(0);
});
test("tooltip delays are 500 and 1000 milliseconds",async({page})=>{
  await openWorkspace(page);await page.getByRole('button',{name:'Refresh',exact:true}).hover();await page.waitForTimeout(300);await expect(page.locator('#issue-tooltip')).toHaveAttribute('aria-hidden','true');await page.waitForTimeout(260);await expect(page.locator('#issue-tooltip')).toHaveAttribute('aria-hidden','false');await page.locator('[data-issue-dropdown="stage-triage"]').hover();const option=page.locator('[data-issue-dropdown="stage-triage"] .stage-option').nth(1);await option.hover();await page.waitForTimeout(700);await expect(page.locator('#issue-tooltip')).toHaveAttribute('aria-hidden','true');await page.waitForTimeout(350);await expect(page.locator('#issue-tooltip')).toHaveAttribute('aria-hidden','false');
});
test("all 32 state cards have a body and a six-point back",async({page})=>{
  test.setTimeout(90000);const api=await openWorkspace(page);
  for(const item of api.data.issues){await selectIssue(page,item.number);const front=page.locator('.context-face-front');await expect(front.locator('.context-facts,.context-field,.context-list').first()).toBeAttached();if(item.workflow?.state==='triage_queued')expect(await front.locator('dt').allTextContents()).not.toContain('Run');await front.getByRole('button',{name:'View Issue lifecycle'}).click();await expect(page.locator('.context-flip')).toHaveClass(/is-flipped/);await expect(page.locator('.lifecycle-node')).toHaveCount(6);await page.locator('.context-face-back').getByRole('button',{name:'Back to current status'}).click();}
});

test("GitHub sync color is independent of a stopped runtime", async ({page}) => {
  const api = await openWorkspace(page); api.runtimeState = "stopped"; api.delayRefresh = 600;
  const dot = page.locator('.sync-dot');
  await expect(dot).toHaveClass(/current/);
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(dot).toHaveClass(/syncing/);
  await expect(dot).toHaveClass(/current/);
  await expect(dot).toHaveCSS('background-color','rgb(63, 157, 108)');
  api.failNext = true;
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(dot).toHaveClass(/error/);
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(dot).toHaveClass(/current/);
});

test("all filter totals are bare numbers, including Select all", async ({page}) => {
  await openWorkspace(page);
  for (const kind of ['state','label','contributor','source']) {
    const host = page.locator(`[data-issue-dropdown="${kind}-filter"]`); await host.hover();
    await expect(host).toHaveClass(/open/);
    const counts = host.locator('.option-count');
    expect(await counts.count()).toBeGreaterThan(1);
    for (const count of await counts.all()) {
      await expect(count).toHaveCSS('border-width','0px');
      await expect(count).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
      await expect(count).toHaveCSS('padding','0px');
      await expect(count).toHaveCSS('box-shadow','none');
    }
    await page.mouse.move(800,70); await expect(host).not.toHaveClass(/open/);
  }
});

test("opened age is from created_at and the detail keeps the exact timestamp", async ({page}) => {
  const api = await openWorkspace(page);
  const item = api.data.issues.find(item=>item.number===128)!;
  item.created_at = new Date(Date.now()-3*86400000-5000).toISOString();
  item.updated_at = new Date().toISOString();
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  const row = page.locator('[data-issue-number="128"]');
  await expect(row.locator('.row-meta')).toContainText('opened 3d ago');
  await expect(row.locator('.row-meta')).not.toContainText('updated ');
  await expect(page.locator('.row-icon svg').first()).toHaveCSS('width','18px');
  await expect(page.locator('.row-icon svg').first()).toHaveCSS('height','18px');
  await row.click();
  await expect(page.locator('.detail-created-at')).toContainText('Created at');
  await expect(page.locator('.detail-created-at time')).toHaveAttribute('datetime',item.created_at);
});

test("recorded completed stages have green rings and light lifecycle hints", async ({page}) => {
  await openWorkspace(page); await selectIssue(page,120);
  await page.getByRole('button',{name:'View Issue lifecycle',exact:true}).click();
  await expect(page.locator('.lifecycle-node.done')).toHaveCount(3);
  await expect(page.locator('.lifecycle-node.done .lifecycle-dot').first()).toHaveCSS('border-color','rgb(63, 157, 108)');
  await expect(page.locator('.lifecycle-connector.complete')).toHaveCount(2);
  await expect(page.locator('.lifecycle-connector.complete').first()).toHaveCSS('background-color','rgb(191, 227, 203)');
  const triage = page.locator('[data-tooltip-heading="Triage"]'); await triage.hover();
  await page.waitForTimeout(260);
  const tooltip = page.locator('#issue-tooltip');
  await expect(tooltip).toHaveAttribute('aria-hidden','true');
  await page.waitForTimeout(330);
  await expect(tooltip).toHaveClass(/lifecycle-hint/);
  await expect(tooltip).toHaveCSS('background-color','rgba(255, 255, 255, 0.98)');
  await expect(tooltip.locator('strong')).toHaveText('Triage');
  await expect(tooltip.locator('span')).toHaveText('Triaging');
  await expect(tooltip.locator('em')).toHaveText('Completed stage');
  await page.keyboard.press('Escape'); await expect(tooltip).toHaveAttribute('aria-hidden','true');
  await triage.focus(); await expect(tooltip).toHaveAttribute('aria-hidden','false');
  await page.locator('[data-tooltip-heading="Complete"]').hover();
  await expect(tooltip.locator('span')).toHaveText('Not reached');
});

test("cancelled Triage keeps the received Repository green without completing later stages", async ({page}) => {
  const api = await openWorkspace(page);
  const item = api.data.issues.find(item => item.number === 129)!;
  item.workflow!.state = 'triage_cancelled';
  item.workflow!.lifecycle.stages.triage.state = 'triage_cancelled';
  item.workflow!.lifecycle.stages.triage.outcome = 'attention';
  await page.getByRole('button', {name:'Refresh', exact:true}).click();
  await selectIssue(page,129);
  await page.getByRole('button', {name:'View Issue lifecycle', exact:true}).click();
  const repo = page.locator('[data-tooltip-heading="Repository"]');
  await expect(repo).toHaveClass(/done/);
  await expect(repo).toHaveAttribute('data-tooltip-state','Open');
  await expect(repo).toHaveAttribute('data-tooltip-summary','Completed stage');
  await expect(repo.locator('.lifecycle-dot')).toHaveCSS('border-color','rgb(63, 157, 108)');
  await expect(page.locator('[data-tooltip-heading="Triage"]')).toHaveClass(/attention/);
  await expect(page.locator('.lifecycle-node.pending')).toHaveCount(4);
  await expect(page.locator('.lifecycle-connector').first()).toHaveClass(/transition/);
  await expect(page.locator('.lifecycle-connector').first()).toHaveCSS('background-color','rgb(203, 213, 225)');
  await expect(page.locator('.lifecycle-connector.complete')).toHaveCount(0);
});

test("search keeps only the outer focus ring", async ({page}) => {
  await openWorkspace(page);
  const input = page.locator('.search input');
  await input.focus();
  await expect(input).toHaveCSS('box-shadow', 'none');
  await expect(input).toHaveCSS('outline-width', '0px');
  await expect(page.locator('.search')).not.toHaveCSS('box-shadow', 'none');
});

test("refresh remains busy after leaving and returning to the Issue tab", async ({page}) => {
  const api = await openWorkspace(page);
  api.delayRefresh = 2000;
  await page.getByRole('button', {name:'Refresh',exact:true}).click();
  await expect(page.getByRole('button', {name:'Refresh',exact:true})).toBeDisabled();
  await page.getByRole('tab', {name:'GITLAB ISSUE',exact:true}).click();
  await page.getByRole('tab', {name:'GITHUB ISSUE',exact:true}).click();
  await expect(page.getByRole('button', {name:'Refresh',exact:true})).toBeDisabled();
  await expect(page.getByRole('button', {name:'Refresh',exact:true})).toBeEnabled();
  expect(api.requests.filter(r=>r.path.endsWith('/issue-triage/refresh'))).toHaveLength(1);
});

test("Kanban chat covers the expanded stage menu", async ({page}) => {
  await openWorkspace(page);
  await page.locator('.stage.delivery').hover();
  await page.evaluate(() => {
    const shell=document.createElement('div');shell.className='agent-page-shell docked';
    shell.style.cssText='inset:0;width:100vw;height:100vh;pointer-events:auto;background:white';
    document.body.append(shell);
  });
  expect(await page.locator('.stage.delivery .stage-menu').evaluate(e=>{
    const r=e.getBoundingClientRect();return document.elementFromPoint(r.x+20,r.y+20)?.classList.contains('agent-page-shell');
  })).toBe(true);
});


test("background evidence failure stays visible without marking GitHub sync failed", async ({page}) => {
  const api = await openWorkspace(page);
  api.intake = {status:'failed',error:'Attachment processing failed; refresh to retry.'};
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(page.getByText('Issue evidence processing failed',{exact:true})).toBeVisible();
  await expect(page.locator('.sync-dot')).toHaveClass(/current/);
  await page.waitForTimeout(6000);
  await expect(page.getByText('Issue evidence processing failed',{exact:true})).toBeVisible();
});

test("legacy evidence comments render media after nested code and keep PDF download", async ({page}) => {
  const api = await openWorkspace(page);
  api.comments = [{id:'evidence',author_login:'reporter',created_at:'2026-09-14T00:00:00Z',html_url:'https://github.com/owner/repository/issues/128#issuecomment-1',body:[
    '<!-- zf-self-issue-evidence:example -->','## ZaoFu evidence','','Original artifact downloads:','',
    '- [report.pdf](https://github.com/owner/repository/releases/download/evidence/report.pdf)','',
    '### notes.md','','```text','Example markdown','```text','nested sample','```','end of notes','```','',
    'The following image/video files are attached to this comment:','','- recording.webm','- screenshot.png','',
    'The following files could not be attached automatically:','','- report.pdf: GitHub CLI uploads images and videos only.','',
    'https://github.com/user-attachments/assets/example-video','',
    '![screenshot](https://github.com/user-attachments/assets/example-image)',
  ].join('\n')}];
  await selectIssue(page,128);
  const comment = page.locator('.issue-comment');
  await expect(comment.locator('video')).toHaveAttribute('src',/issue-triage\/attachment/);
  await expect(comment.getByRole('link',{name:'report.pdf',exact:true})).toHaveAttribute('href',/releases\/download/);
  await expect(comment).not.toContainText('GitHub CLI uploads images and videos only');
  await expect(comment.locator('img[alt="screenshot"]')).toHaveCount(1);
});
