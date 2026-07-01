// Reproduce kanban-agent first-message hang and capture network timing
// to discriminate A (start/input race) / B (SSE late) / C (no optimistic state).
//
// Mount /tmp/zf-shots-after-77B as /snapshots; run inside mcp/playwright:latest.

const { chromium } = require('/app/node_modules/playwright');

const REL_TS_BASE = Date.now();
const rel = () => `${(Date.now() - REL_TS_BASE).toString().padStart(6)}ms`;
const BASE_URL = process.env.ZF_E2E_BASE_URL || 'http://127.0.0.1:8001';
const PROJECT_ID = process.env.ZF_E2E_PROJECT_ID || 'sample-project';

(async () => {
  const browser = await chromium.launch({
    executablePath: '/ms-playwright/chromium-1222/chrome-linux64/chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1200 } });
  const page = await ctx.newPage();

  // Capture all network activity matching the operator session paths.
  const NET = [];
  const HOOK = (event) => (req) => {
    const url = req.url();
    if (!/\/api\/(projects\/[^/]+\/)?(operator|kanban-agent|channels)/.test(url)) return;
    NET.push({
      t: rel(),
      event,
      method: req.method ? req.method() : '',
      url: url.replace(BASE_URL, ''),
      status: req.status ? req.status() : '',
    });
  };
  page.on('request', HOOK('request'));
  page.on('response', HOOK('response'));

  // Capture SSE / EventSource as well (chromium fires response only once, but
  // we capture data events via page.evaluate later).
  page.on('console', (msg) => {
    const text = msg.text();
    if (/operator|kanban-agent|sse|stream/i.test(text)) {
      NET.push({ t: rel(), event: 'console', method: msg.type(), url: text.slice(0, 180), status: '' });
    }
  });

  console.log(`[${rel()}] navigate to project board`);
  await page.goto(
    `${BASE_URL}/?project=${PROJECT_ID}&page=board`,
    { waitUntil: 'domcontentloaded', timeout: 20000 },
  );
  await page.waitForTimeout(4000);

  await page.screenshot({ path: '/snapshots/kanban-agent-01-before-open.png', fullPage: true });

  // Find the kanban-agent chat button (bottom-right floating widget).
  console.log(`[${rel()}] looking for kanban-agent button`);
  const btnCandidates = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('button'));
    return all
      .filter((b) => /workflow|kanban|agent|chat|operator/i.test(b.textContent || '') ||
                     /workflow|kanban|agent|chat|operator/i.test(b.getAttribute('aria-label') || '') ||
                     /workflow|kanban|agent|chat|operator/i.test(b.className || ''))
      .slice(0, 8)
      .map((b) => ({
        text: (b.textContent || '').trim().slice(0, 60),
        cls: b.className.slice(0, 80),
        rect: b.getBoundingClientRect(),
      }));
  });
  console.log('button candidates:', JSON.stringify(btnCandidates, null, 2));

  // Try clicking a button that looks like the bottom-right widget.
  const opened = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('button, [role="button"]'));
    // Bottom-right floating widget — usually fixed positioning, x > 1500, y > 900.
    const widget = all.find((b) => {
      const r = b.getBoundingClientRect();
      return r.x > 1600 && r.y > 900 && r.width > 20 && r.height > 20;
    });
    if (widget) {
      widget.click();
      return { clicked: true, text: (widget.textContent || '').trim().slice(0, 80), x: widget.getBoundingClientRect().x, y: widget.getBoundingClientRect().y };
    }
    return { clicked: false };
  });
  console.log(`[${rel()}] kanban-agent widget click:`, JSON.stringify(opened));

  await page.waitForTimeout(2500);
  await page.screenshot({ path: '/snapshots/kanban-agent-02-after-open.png', fullPage: true });

  // Find the textarea / input in the kanban-agent panel.
  const inputs = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('textarea, [contenteditable="true"]'));
    return all.map((el) => {
      const r = el.getBoundingClientRect();
      return { tag: el.tagName, ph: el.placeholder || el.getAttribute('aria-placeholder') || '', x: r.x, y: r.y, w: r.width, h: r.height };
    });
  });
  console.log('input candidates:', JSON.stringify(inputs, null, 2));

  // Send the first message.
  console.log(`[${rel()}] === FIRST MESSAGE SEND ===`);
  const sendTs = rel();
  await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('textarea, [contenteditable="true"]'));
    // Prefer the largest input near the bottom of the viewport.
    const candidate = all
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      .filter((x) => x.r.y > 700 && x.r.width > 200)
      .sort((a, b) => b.r.width - a.r.width)[0];
    if (!candidate) return { ok: false };
    candidate.el.focus();
    if (candidate.el.tagName === 'TEXTAREA') {
      candidate.el.value = 'hello kanban agent, this is the first message';
      candidate.el.dispatchEvent(new Event('input', { bubbles: true }));
    } else {
      candidate.el.textContent = 'hello kanban agent, this is the first message';
      candidate.el.dispatchEvent(new Event('input', { bubbles: true }));
    }
    return { ok: true };
  });
  await page.waitForTimeout(200);

  // Press Enter (or find Send button).
  await page.keyboard.press('Enter');
  console.log(`[${rel()}] sent (Enter pressed at ${sendTs})`);
  await page.screenshot({ path: '/snapshots/kanban-agent-03-after-send.png', fullPage: true });

  // Wait and observe — does anything happen?
  for (let i = 0; i < 12; i++) {
    await page.waitForTimeout(2500);
    const visible = await page.evaluate(() => {
      const text = document.body.innerText;
      // Look for indicators of pending state or arrival of reply
      return {
        hasWorking: /\bWorking\b/.test(text),
        hasPosting: /\b(posting|sending|submitted)\b/i.test(text),
        hasErrorOrIdle: /\b(idle|error|fail)\b/i.test(text),
        last200: text.slice(-300),
      };
    });
    console.log(`[${rel()}] tick ${i+1}/12: working=${visible.hasWorking} posting=${visible.hasPosting} err=${visible.hasErrorOrIdle}`);
    if (visible.hasWorking || /\bDone\b/.test(visible.last200)) {
      await page.screenshot({ path: `/snapshots/kanban-agent-04-tick${i+1}.png`, fullPage: true });
    }
    if (/^.*Done.*Done.*$/m.test(visible.last200)) break;
  }

  console.log('\n=== NETWORK LOG ===');
  for (const n of NET) {
    console.log(`${n.t}  ${n.event.padEnd(8)} ${(n.method||'').padEnd(6)} ${n.status?String(n.status).padEnd(4):'    '} ${n.url}`);
  }

  await browser.close();
})().catch((e) => { console.error('REPRO ERROR:', e); process.exit(1); });
