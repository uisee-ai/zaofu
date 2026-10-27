import { chromium } from '/home/uisee/workspace/zaofu/web/node_modules/playwright-core/index.mjs';
import { expect as baseExpect } from '/home/uisee/workspace/zaofu/web/node_modules/@playwright/test/index.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const expect = baseExpect.configure({ timeout: 60000 });
const browser = await chromium.launch({ headless: true, executablePath: '/ms-playwright/chromium-1226/chrome-linux64/chrome' });
try {
  const page = await browser.newPage();
  await page.addInitScript(token => { localStorage.setItem('zf.webActionToken', token); localStorage.setItem('zf.themeMode', 'light'); }, process.env.ZF_E2E_TOKEN);
  const project = 'ty-review-42cb2551', planId = 'evt-38c86009183e';
  const api = `http://127.0.0.1:8002/api/projects/${project}/plans/${planId}/preview`;
  const original = await (await page.request.get(api)).json();
  let writes = 0;
  page.on('request', request => {
    if (request.method() === 'POST' && (request.url().includes('/actions/') ||
      (request.url().endsWith('/plan-artifact-review') && request.postDataJSON()?.operation !== 'context'))) writes++;
  });
  const sizes = [{ width: 1440, height: 1000 }, { width: 1440, height: 1400 }, { width: 390, height: 844 }];
  const measurements = [];
  for (const size of sizes) {
    await page.setViewportSize(size);
    await page.goto(`http://127.0.0.1:8002/?project=${project}&page=inbox&plan=${planId}`);
    const panel = page.getByRole('dialog', { name: 'Plan preview', exact: true });
    await panel.getByRole('button', { name: '完整原文', exact: true }).click();
    const reader = panel.locator('.plan-preview-reader');
    const frame = await reader.boundingBox();
    const preview = await panel.locator('.plan-preview-markdown-pane').boundingBox();
    await panel.screenshot({ path: `/evidence/${size.width}x${size.height}-preview.png` });
    await panel.getByRole('button', { name: '引用编辑', exact: true }).click();
    const document = panel.getByTestId('plan-review-document');
    await expect(document).toBeVisible();
    const editFrame = await reader.boundingBox(), editing = await document.boundingBox();
    assert.deepEqual(editFrame, frame);
    const padding = await reader.evaluate(element => parseFloat(getComputedStyle(element).paddingBottom));
    const gap = frame.y + frame.height - padding - editing.y - editing.height;
    assert(Math.abs(gap) < 2);
    assert(editing.height >= preview.height - 40);
    assert(await reader.evaluate(element => element.scrollHeight <= element.clientHeight + 1));
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await panel.screenshot({ path: `/evidence/${size.width}x${size.height}-editing.png` });
    await panel.getByRole('navigation', { name: 'Plan document view' }).getByRole('button', { name: 'Agent', exact: true }).click();
    const agent = panel.getByRole('dialog', { name: 'Plan Agent', exact: true });
    await expect(agent).toBeVisible();
    if (original.status !== 'pending') await expect(agent.getByRole('textbox')).toBeDisabled();
    assert.equal((await document.boundingBox()).height, editing.height);
    const popup = await agent.boundingBox();
    assert(popup.y >= frame.y && popup.y + popup.height <= frame.y + frame.height);
    await panel.screenshot({ path: `/evidence/${size.width}x${size.height}-agent.png` });
    measurements.push({ size, frame, preview, editing, unusedHeight: gap, agent: popup });
  }
  const current = await (await page.request.get(api)).json();
  assert.equal(current.markdown, original.markdown); assert.equal(current.status, original.status); assert.equal(writes, 0);
  await fs.writeFile('/evidence/result.json', JSON.stringify({ passed: true, project, planId, status: original.status, measurements, agentLayout: true, planUnchanged: true, writes }, null, 2));
  console.log('Real Plan Preview and quoted editing share the full reader; three viewport sizes and Agent layout passed.');
} finally { await browser.close(); }
