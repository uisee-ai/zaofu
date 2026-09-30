import { expect, test, type Page } from '@playwright/test';
import { openWorkspace, selectIssue } from './issue-workspace-support';

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}
async function switchProject(page: Page, id: string) {
  await page.evaluate(id => {
    (window as unknown as { issueFixture: { setProjectId(id: string): void } }).issueFixture.setProjectId(id);
  }, id);
}

test('rows render without waiting for a slow summary', async ({ page }) => {
  await openWorkspace(page);
  const summary = gate();
  await page.route('**/api/projects/fixture/issue-triage/summary', async route => {
    await summary.promise; await route.fallback();
  });
  try {
    await page.reload(); // Cold, memory-only cache.
    await expect(page.locator('.issue-row')).toHaveCount(32);
    await expect(page.getByText('Loading Issue mirror…', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Not synchronized yet', { exact: true })).toBeVisible();
  } finally { summary.release(); }
  await expect(page.getByText('Not synchronized yet', { exact: true })).toHaveCount(0);
});

test('A to B to A restores only the correct project and detail while revalidating', async ({ page }) => {
  const api = await openWorkspace(page);
  await selectIssue(page, 128);
  const pending = gate();
  await page.route('**/api/projects/**/issue-triage**', async route => {
    await pending.promise; await route.fallback();
  });
  try {
    await switchProject(page, 'other');
    await expect(page.locator('.issue-row')).toHaveCount(0);
    await expect(page.locator('.detail')).toHaveCount(0);
    await switchProject(page, 'fixture');
    await expect(page.locator('[data-issue-number="128"]')).toBeVisible();
    await expect(page.locator('.detail-kicker')).toContainText('#128');
    await expect(page.getByText('Loading Issue mirror…', { exact: true })).toHaveCount(0);
    api.data.issues.find(item => item.number === 128)!.title = 'Updated after revalidation 128';
  } finally { pending.release(); }
  await expect(page.locator('[data-issue-number="128"] .issue-title')).toHaveText('Updated after revalidation 128');
});

test('leaving and returning shares pending reads instead of stacking requests', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('zf.issuePreferences', JSON.stringify({ autoRefresh: false })));
  await openWorkspace(page);
  const pending = gate();
  const counts: Record<string, number> = {};
  await page.route('**/api/projects/fixture/issue-triage**', async route => {
    const path = new URL(route.request().url()).pathname;
    counts[path] = (counts[path] || 0) + 1;
    await pending.promise; await route.fallback();
  });
  try {
    await page.reload();
    await expect.poll(() => Object.keys(counts).length).toBe(2);
    for (let i = 0; i < 3; i++) {
      await page.getByRole('tab', { name: 'RUNTIME INTERVENTIONS', exact: true }).click();
      await page.getByRole('tab', { name: 'GITHUB ISSUE', exact: true }).click();
    }
  } finally { pending.release(); }
  await expect(page.locator('.issue-row')).toHaveCount(32);
  expect(Object.values(counts)).toEqual([1, 1]);
});

for (const invalidation of ['cancel', 'access', 'token']) {
  test(`${invalidation} invalidates cached project views`, async ({ page }) => {
    await openWorkspace(page);
    await page.evaluate(async kind => {
      if (kind === 'cancel') {
        const modulePath = '/src/api/client.ts';
        const client = await import(modulePath);
        await client.cancelQueuedIssueTriage('fixture', 129);
      } else if (kind === 'access') {
        window.dispatchEvent(new Event('zf:developer-access-changed'));
      } else {
        localStorage.setItem('zf.webActionToken', 'different-user-token');
      }
    }, invalidation);
    const pending = gate();
    await page.route('**/api/projects/fixture/issue-triage**', async route => {
      await pending.promise; await route.fallback();
    });
    try {
      await page.getByRole('tab', { name: 'RUNTIME INTERVENTIONS', exact: true }).click();
      await page.getByRole('tab', { name: 'GITHUB ISSUE', exact: true }).click();
      await expect(page.getByText('Loading Issue mirror…', { exact: true })).toBeVisible();
      await expect(page.locator('.issue-row')).toHaveCount(0);
    } finally { pending.release(); }
    await expect(page.locator('.issue-row')).toHaveCount(32);
    if (invalidation === 'cancel') await expect(page.locator('[data-issue-number="129"]')).toContainText('Triage cancelled');
  });
}
