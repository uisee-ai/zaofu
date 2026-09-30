import { expect, test, type Page } from '@playwright/test';
import { openWorkspace, selectIssue } from './issue-workspace-support';

async function preferences(page: Page, values: Record<string, unknown>) {
  await page.evaluate(values => {
    const key = 'zf.issuePreferences';
    const next = JSON.stringify({ ...JSON.parse(localStorage.getItem(key) || '{}'), ...values });
    localStorage.setItem(key, next);
    window.dispatchEvent(new StorageEvent('storage', { key, newValue: next }));
  }, values);
}

for (const theme of ['light', 'dark', 'system'] as const) {
  test(`Triage surfaces and portals follow ${theme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await openWorkspace(page);
    await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
    await selectIssue(page, 128);
    const scheme = theme === 'light' ? 'light' : 'dark';
    await expect(page.locator('.issue-workspace').first()).toHaveCSS('color-scheme', scheme);
    const surfaces = ['.header', '.stage.triage', '.search', '.list', '.detail', '.issue-body'];
    for (const selector of surfaces) {
      const surface = page.locator(`.issue-workspace ${selector}`).first();
      await expect(surface).toBeVisible();
      const light = await surface.evaluate(el => getComputedStyle(el).backgroundColor);
      expect(light, selector).not.toBe('rgba(0, 0, 0, 0)');
      if (scheme === 'dark') expect(light, selector).not.toBe('rgb(255, 255, 255)');
    }
    await page.screenshot({ path: `test-results/settings/triage-page-${theme}.png` });
    await page.getByRole('button', { name: 'Manage Run', exact: true }).click();
    await expect(page.locator('.modal')).toHaveCSS('color-scheme', scheme);
    if (scheme === 'dark') await expect(page.locator('.modal')).not.toHaveCSS('background-color', 'rgb(255, 255, 255)');
    await page.screenshot({ path: `test-results/settings/triage-${theme}.png` });
  });
}

for (const seconds of [2, 7]) {
  test(`Triage refresh reads backend at ${seconds}s and stops when disabled`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('zf.issuePreferences', JSON.stringify({ autoRefresh: false })));
    const api = await openWorkspace(page);
    await page.clock.install();
    await page.clock.pauseAt(new Date());
    const reads = () => api.requests.filter(r => r.path.endsWith('/issue-triage')).length;
    await preferences(page, { autoRefresh: true, refreshSeconds: seconds, refreshOnReturn: false });
    const before = reads();
    await page.clock.runFor(seconds * 1000 - 1);
    expect(reads()).toBe(before);
    await page.clock.runFor(1);
    await expect.poll(reads).toBe(before + 1);
    await preferences(page, { autoRefresh: false });
    const stopped = reads();
    await page.clock.runFor(30000);
    expect(reads()).toBe(stopped);
    expect(api.requests.filter(r => r.method === 'POST')).toHaveLength(0);
  });
}

for (const enabled of [false, true]) {
  test(`refresh on returning ${enabled} controls visibility refresh`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('zf.issuePreferences', JSON.stringify({ autoRefresh: false })));
    const api = await openWorkspace(page);
    await preferences(page, { autoRefresh: true, refreshSeconds: 3600, refreshOnReturn: enabled });
    const reads = () => api.requests.filter(r => r.path.endsWith('/issue-triage')).length;
    const before = reads();
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      document.dispatchEvent(new Event('visibilitychange'));
      Object.defineProperty(document, 'hidden', { configurable: true, value: false });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    if (enabled) await expect.poll(reads).toBe(before + 1);
    else { await page.waitForTimeout(250); expect(reads()).toBe(before); }
    await preferences(page, { autoRefresh: false });
    const stopped = reads();
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await page.waitForTimeout(250);
    expect(reads()).toBe(stopped);
  });
}

test('retired preferences cannot override fixed list, hover and notification defaults', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('zf.issuePreferences', JSON.stringify({ pageSize: 1, labelCount: 1, historyCount: 9, hoverOpen: false, visibleRows: 1, successSeconds: 90, infoSeconds: 90, showRecovery: false })));
  const api = await openWorkspace(page);
  expect(api.requests.some(r => r.params.limit === '100')).toBe(true);
  const stage = page.locator('[data-issue-dropdown="stage-delivery"]');
  await stage.hover(); await expect(stage).toHaveClass(/open/);
  await page.mouse.move(0, 0);
  for (const tone of ['success', 'info']) {
    await page.evaluate(tone => (window as any).issueFixture.notify({ title: `fixed-${tone}`, message: 'duration', tone, category: 'recovery' }), tone);
  }
  await expect(page.locator('.toast')).toHaveCount(2);
  await page.mouse.move(0, 0);
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 7000 });
});

test('hidden tabs no longer pause the configured refresh timer', async ({page}) => {
 await page.addInitScript(()=>localStorage.setItem('zf.issuePreferences',JSON.stringify({autoRefresh:false,pauseHidden:true})));
 const api=await openWorkspace(page);
 await page.clock.install();await page.clock.pauseAt(new Date());
 await page.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,value:true}));
 await preferences(page,{autoRefresh:true,refreshSeconds:2});
 const reads=()=>api.requests.filter(r=>r.path.endsWith('/issue-triage')).length;
 const before=reads();await page.clock.runFor(2000);
 await expect.poll(reads).toBe(before+1);
});

test('timezone changes Issue dates plus shared log, trace and Settings displays', async ({ page }) => {
  await openWorkspace(page);
  await selectIssue(page, 128);
  await preferences(page, { timeZone: 'UTC' });
  const utc = await page.locator('.detail-created-at time').textContent();
  await preferences(page, { timeZone: 'Asia/Shanghai' });
  await expect(page.locator('.detail-created-at time')).not.toHaveText(utc!);
  const values = await page.evaluate(async () => {
    const shared = await import('/src/lib/format.ts' as string);
    const settings = await import('/src/settings/useSettingsPreferences.ts' as string);
    const traces = await import('/src/components/traces/traceModel.ts' as string);
    const stamp = '2026-09-28T00:00:00Z';
    return { shared: shared.formatTime(stamp), settings: settings.formatSettingsTime(stamp), traces: traces.formatTimestamp(stamp) };
  });
  expect(values.shared).toBe('08:00:00');
  expect(values.settings).toContain('08:00:00');
  expect(values.traces).toContain('08:00');
});
