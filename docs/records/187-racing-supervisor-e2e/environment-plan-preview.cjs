const fs = require('fs');
const assert = require('assert/strict');
const crypto = require('crypto');
const { chromium } = require('/app/node_modules/playwright');
const root = '/tmp/zf-racing-supervision-20261025T211020Z';
const base = 'http://127.0.0.1:8002';
const config = JSON.parse(fs.readFileSync(root + '/context.json'));
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: '/ms-playwright/chromium-1226/chrome-linux64/chrome', args: ['--no-sandbox']});
  try {
    const context = await browser.newContext({storageState: root + '/browser-state.json', viewport: {width: 1600, height: 1000}});
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    const expected = JSON.parse(fs.readFileSync(root + '/evidence/environment-plan-gate.json'));
    const initial = await (await context.request.get(`${base}/api/projects/${config.projectId}/workflow-requests/${config.requestId}/graph`)).json();
    const planIndex = initial.plans.findIndex(p => p.plan_id === expected.plan_id && p.status === 'pending');
    assert(planIndex >= 0);
    await page.goto(`${base}/?page=workflows&project=${config.projectId}&request=${config.requestId}`);
    const button = page.getByRole('region', {name: 'Workflow 图工作台', exact: true}).getByRole('button', {name: /^PRD \/ Plan Preview · /}).nth(planIndex);
    await button.waitFor();
    await button.click();
    const panel = page.getByRole('region', {name: 'Workflow PRD Plan Preview', exact: true});
    await panel.getByRole('heading', {name: '本次交付与审批范围', exact: true}).waitFor();
    await page.waitForFunction(() => document.querySelector('[data-testid="plan-review-source"]')?.textContent?.includes('冷'));
    const body = await page.getByTestId('plan-review-source').textContent();
    const response = await context.request.get(`${base}/api/projects/${config.projectId}/workflow-requests/${config.requestId}/graph`);
    assert(response.ok());
    const graph = await response.json();
    const plan = graph.plans.find(p => p.plan_id === expected.plan_id && p.status === 'pending');
    assert(plan);
    assert.notEqual(plan.plan_id, 'evt-adf3807d914c');
    assert.notEqual(plan.plan_id, 'evt-746215246543');
    assert.notEqual(plan.plan_id, 'evt-7bacfb824596');
    assert.equal(body, plan.markdown);
    const digest = crypto.createHash('sha256').update(body).digest('hex');
    assert.equal(digest, plan.artifacts.find(a => a.name === 'accepted_plan').sha256);
    const approvalEnabled = await page.getByRole('button', {name: '确认计划并继续', exact: true}).isEnabled();
    assert(approvalEnabled);
    await panel.getByRole('button', {name: '模块规格', exact: true}).click();
    await panel.getByRole('combobox', {name: '审阅模块'}).selectOption('RACE-ASSEMBLY');
    await panel.getByRole('heading', {name: 'AC-A07', exact: true}).waitFor();
    await panel.getByRole('button', {name: '审批概览', exact: true}).click();
    await panel.getByRole('heading', {name: '本次交付与审批范围', exact: true}).scrollIntoViewIfNeeded();
    await page.screenshot({path: root + '/evidence/31-environment-plan-preview.png'});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.setViewportSize({width: 390, height: 844});
    await panel.getByRole('heading', {name: '本次交付与审批范围', exact: true}).scrollIntoViewIfNeeded();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({path: root + '/evidence/32-environment-plan-mobile.png'});
    const proof = {planId: plan.plan_id, status: plan.status, bodyDigest: digest, bodyChars: body.length,
      approvalButtonEnabled: approvalEnabled, approvalClicked: false, desktopNoOverflow: true, mobileNoOverflow: true,
      runStatus: graph.nodes.find(n => n.kind === 'run')?.status, sourceSeq: graph.scope.source_seq};
    fs.writeFileSync(root + '/evidence/environment-plan-browser-proof.json', JSON.stringify(proof, null, 2) + '\n');
    console.log(JSON.stringify(proof));
  } finally { await browser.close(); }
})();
