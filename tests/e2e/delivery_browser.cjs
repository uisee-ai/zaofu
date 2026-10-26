const { chromium } = require('/app/node_modules/playwright');
const assert = require('node:assert/strict');
const [base, scenario] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch({channel: 'chromium', headless: true, args: ['--no-sandbox']});
  const context = await browser.newContext({viewport: {width: 1280, height: 800}});
  await context.tracing.start({screenshots: true, snapshots: true});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  const field = id => page.getByTestId(id);
  const waitWrite = async (id, path, status) => {
    const result = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith(path));
    await field(id).click();
    const response = await result;
    assert.equal(response.status(), status);
    assert.ok(await field('status').isVisible());
  };
  try {
    await page.goto(base);
    await field('role').selectOption('writer');
    if (scenario === 'cargo-mini') {
      await field('asset-id').fill('浏览器目标');
      await field('box').fill('0,0,10,10');
      await waitWrite('create', '/api/assets', 201);
      await page.waitForFunction(() => document.querySelector('[data-testid="search-results"]').textContent.includes('浏览器目标'));
      await field('box').fill('1,1,20,20');
      await waitWrite('edit', '/edit', 200);
      await page.waitForFunction(() => !document.querySelector('[data-testid="search-results"]').textContent.includes('浏览器目标'));
      await waitWrite('refresh', '/refresh', 200);
      await page.waitForFunction(() => document.querySelector('[data-testid="search-results"]').textContent.includes('浏览器目标'));
    } else if (scenario === 'warehouse') {
      await field('warehouse').fill('浏览器A'); await field('sku').fill('SKU'); await field('quantity').fill('10');
      await waitWrite('seed', '/api/stock', 201);
      await field('from').fill('浏览器A'); await field('to').fill('浏览器B'); await field('transfer-quantity').fill('3');
      await waitWrite('transfer', '/api/transfers', 200);
      await page.waitForFunction(() => document.querySelector('[data-testid="stock-results"]').textContent.includes('浏览器B'));
    } else {
      await field('document-id').fill('browser-doc'); await field('title').fill('浏览器文章');
      await field('body').fill('<script>window.injected=true</script>正文');
      await waitWrite('create', '/api/documents', 201);
      await waitWrite('publish', '/publish', 409);
      await field('role').selectOption('reviewer'); await waitWrite('approve', '/approve', 200);
      await field('role').selectOption('writer'); await waitWrite('publish', '/publish', 200);
      await page.waitForFunction(() => document.querySelector('[data-testid="public-body"]').textContent.includes('正文'));
      assert.equal(await page.evaluate(() => window.injected), undefined);
    }
    assert.deepEqual(errors, []);
    await page.screenshot({path: '/evidence/workflow.png', fullPage: true});
    console.log(JSON.stringify({scenario, passed: true, url: base}));
  } finally {
    await context.tracing.stop({path: '/evidence/trace.zip'});
    await browser.close();
  }
})().catch(e => {console.error(e); process.exit(1);});
