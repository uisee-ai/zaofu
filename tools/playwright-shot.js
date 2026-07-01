// Playwright kanban screenshot helper for autoresearch loop.
//
// Invoked inside mcp/playwright:latest docker image with --entrypoint node:
//   docker run --rm --entrypoint node \
//     -v <host_output_dir>:/snapshots --network host \
//     mcp/playwright:latest \
//     /snapshots/playwright-shot.js <url> <out_path>
//
// Args:
//   argv[2] = full URL of the zf web kanban (e.g. http://127.0.0.1:8765)
//   argv[3] = output PNG path inside the container (mounted to host)
//
// On success: writes PNG and prints first 4KB of visible text to stdout.
// On failure: exit code 1 with error to stderr.
//
// Chromium executablePath is pinned to mcp/playwright image layout
// (/ms-playwright/chromium-1222/chrome-linux64/chrome) and may shift
// across image versions — update here when the upstream image bumps.

const { chromium } = require('/app/node_modules/playwright');
(async () => {
  const url = process.argv[2] || 'http://127.0.0.1:8765';
  const out = process.argv[3] || '/snapshots/kanban.png';
  const browser = await chromium.launch({
    executablePath: '/ms-playwright/chromium-1222/chrome-linux64/chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const page = await browser.newPage({ viewport: { width: 1800, height: 1200 } });
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(5000);
  await page.screenshot({ path: out, fullPage: true });
  const text = await page.evaluate(() => document.body.innerText.slice(0, 4000));
  console.log('--- visible text ---');
  console.log(text);
  console.log(`saved ${out}`);
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
