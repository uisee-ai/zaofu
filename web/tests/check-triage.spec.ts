import { test, expect } from '@playwright/test';

test('the page never exposes or silently installs a developer secret', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('zf.webActionToken', 'stale-reporter-token'));
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  expect(await page.evaluate(() => (window as Window & {ZF_DEVELOPER_TOKEN?: string}).ZF_DEVELOPER_TOKEN)).toBeUndefined();
  expect(await page.evaluate(() => localStorage.getItem('zf.webActionToken'))).toBe('stale-reporter-token');
  await expect(page.locator('.rail-nav-button').filter({hasText:'Triage'})).toHaveCount(0);
});
