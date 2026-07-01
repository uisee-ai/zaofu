// Negative test: when ONLY the local placeholder exists (no backend
// reply_request yet — the SSE-slow fallback case), the placeholder must
// STILL render so the user sees Working for @target while waiting.

const { chromium } = require('/app/node_modules/playwright');
const BASE_URL = process.env.ZF_E2E_BASE_URL || 'http://127.0.0.1:8001';
const PROJECT_ID = process.env.ZF_E2E_PROJECT_ID || 'sample-project';

const detail = {
  schema_version: 'channel.detail.v1',
  channel_id: 'ch-zaofu',
  name: 'ch-zaofu',
  members: [
    { member_id: 'operator', member_type: 'operator', channel_role: 'operator', status: 'connected', permissions: ['read','message'] },
    { member_id: 'claude-arch', member_type: 'provider_agent', provider: 'claude-headless', backend: 'claude-headless', channel_role: 'arch', status: 'connected', permissions: ['read','message'] },
  ],
  threads: { main: { thread_id: 'main' } },
  messages: [
    {
      event_id: 'evt-msg-1', message_id: 'msg-1', thread_id: 'main',
      ts: '2026-06-05T07:00:00Z',
      actor: 'web', member_id: 'operator', role: 'user', source: 'web',
      text: '@claude-arch 你好',
    },
  ],
  reply_requests: [
    {
      request_id: 'local-reply-msg-1-0',
      event_id: 'local-reply-msg-1-0',
      created_at: '2026-06-05T07:00:00.500Z',
      updated_at: '2026-06-05T07:00:00.500Z',
      thread_id: 'main',
      message_id: 'msg-1',
      member_id: 'operator',
      target_member_id: 'claude-arch',
      status: 'submitted',
      queue_state: 'ready',
      provider: 'agent', backend: 'agent',
      reason: 'working for @claude-arch',
    },
  ],
  workflow_requests: [], mentions_detected: [], provider_runs: [], agent_session_runs: [],
  context_packs: [], handoffs: [], state_updates: [], owner_reports: [], automation_reports: [],
  syntheses: [], attention: [], read_state: [], discussion: { mode: 'manual_mention' },
  typing: [], active_typing: [], attachments: [],
};

const summary = { id: 'ch-zaofu', channel_id: 'ch-zaofu', name: 'ch-zaofu', status: 'active', message_count: 1, member_count: 2, members: detail.members, attention: [], running_replies: ['local-reply-msg-1-0'], queued_replies: [] };

(async () => {
  const browser = await chromium.launch({
    executablePath: '/ms-playwright/chromium-1222/chrome-linux64/chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1200 } });
  const page = await ctx.newPage();

  await ctx.route('**/api/**/channels/ch-zaofu*', async (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(detail),
  }));
  await ctx.route('**/api/**/channels**', async (route) => {
    if (/channels\/ch-zaofu/.test(route.request().url())) return route.fallback();
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ schema_version: 'channels.v1', seq: 1, source: 'events.jsonl', channels: [summary] }),
    });
  });

  await page.goto(
    `${BASE_URL}/?project=${PROJECT_ID}&page=channels`,
    { waitUntil: 'domcontentloaded', timeout: 20000 },
  );
  await page.waitForTimeout(4500);

  await page.screenshot({ path: '/snapshots/channel-dedup-02-placeholder-only.png', fullPage: true });

  const out = await page.evaluate(() => {
    const t = document.body.innerText;
    return {
      hasWorking: /\bWorking\b/.test(t),
      hasUserMsg: /@claude-arch 你好/.test(t),
      workingCount: (t.match(/\bWorking\b/g) || []).length,
      slice: t.slice(0, 600),
    };
  });

  console.log('=== placeholder-only scenario ===');
  console.log('  has Working badge :', out.hasWorking);
  console.log('  Working count     :', out.workingCount, '(expect exactly 1)');
  console.log('  user msg visible  :', out.hasUserMsg);
  console.log('\nPage text:\n  ' + out.slice.replace(/\n/g, '\n  '));

  const pass = out.hasWorking && out.workingCount === 1 && out.hasUserMsg;
  console.log(`\nVERIFY: ${pass ? 'PASS — placeholder still renders when alone' : 'FAIL'}`);
  await browser.close();
  process.exit(pass ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
