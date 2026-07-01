// Verify the fix: when kanban-agent panel opens without a saved client-side
// token, the alert "save action token" must appear IMMEDIATELY, before the
// user attempts any send (proving the bug class A/B/C from the backlog was
// actually class D — UX too quiet about the token gate).

const { chromium } = require('/app/node_modules/playwright');

const T0 = Date.now();
const rel = () => `${(Date.now() - T0).toString().padStart(6)}ms`;
const BASE_URL = process.env.ZF_E2E_BASE_URL || 'http://127.0.0.1:8001';
const PROJECT_ID = process.env.ZF_E2E_PROJECT_ID || 'sample-project';

(async () => {
  const browser = await chromium.launch({
    executablePath: '/ms-playwright/chromium-1222/chrome-linux64/chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1200 } });
  const page = await ctx.newPage();

  console.log(`[${rel()}] navigate to project board (fresh localStorage)`);
  await page.goto(
    `${BASE_URL}/?project=${PROJECT_ID}&page=board`,
    { waitUntil: 'domcontentloaded', timeout: 20000 },
  );
  await page.waitForTimeout(4000);

  console.log(`[${rel()}] click kanban-agent FAB`);
  await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('button, [role="button"]'));
    const fab = all.find((b) => {
      const r = b.getBoundingClientRect();
      return r.x > 1600 && r.y > 900 && r.width > 20 && r.height > 20;
    });
    fab?.click();
  });
  await page.waitForTimeout(2000);

  const before = await page.evaluate(() => {
    const alert = document.querySelector('.headless-composer-alert');
    const textarea = document.querySelector('.headless-input');
    return {
      alertText: alert ? alert.textContent : null,
      alertVisible: alert ? alert.offsetParent !== null : false,
      placeholder: textarea ? textarea.getAttribute('placeholder') : null,
      ariaInvalid: textarea ? textarea.getAttribute('aria-invalid') : null,
    };
  });

  console.log(`[${rel()}] === PANEL OPENED (no send yet) ===`);
  console.log('  alertText        :', JSON.stringify(before.alertText));
  console.log('  alertVisible     :', before.alertVisible);
  console.log('  placeholder      :', JSON.stringify(before.placeholder));
  console.log('  aria-invalid     :', before.ariaInvalid);

  await page.screenshot({
    path: '/snapshots/kanban-agent-fix-01-panel-open.png',
    fullPage: true,
  });

  // Verdict
  const expected = before.alertVisible
    && before.alertText
    && (
      /Save a valid action token/i.test(before.alertText)
      || /save a valid action token/i.test(before.alertText)
    );
  console.log(`\n[${rel()}] FIX VERIFY: ${expected ? 'PASS — alert visible immediately on panel open' : 'FAIL — alert missing, fix did not take effect'}`);

  await browser.close();
  process.exit(expected ? 0 : 1);
})().catch((e) => { console.error('VERIFY ERROR:', e); process.exit(2); });
