// Verify f760063 fix: buildChannelConversation must dedup local placeholder
// reply_request against real backend reply_request for the same
// (target_member_id, message_id), producing a single rendered run.
//
// Strategy: intercept all channel API responses BEFORE navigation and return
// a synthetic ChannelDetail that explicitly contains both:
//   - local-reply-msg-1-0  (placeholder, status="submitted")
//   - real-req-id-aaaa     (backend, status="completed", with channel.message.posted)
// Then walk the rendered DOM and count "Working" / Reply body occurrences.

const { chromium } = require('/app/node_modules/playwright');
const BASE_URL = process.env.ZF_E2E_BASE_URL || 'http://127.0.0.1:8001';
const PROJECT_ID = process.env.ZF_E2E_PROJECT_ID || 'sample-project';

const TARGET_MEMBER = 'claude-arch';
const TRIGGER_MSG_ID = 'msg-1';
const REAL_REQUEST_ID = 'real-req-aaaa';
const REPLY_BODY = 'Hello！👋 claude-arch 在线。';

const baseChannel = {
  schema_version: 'channel.detail.v1',
  channel_id: 'ch-zaofu',
  name: 'ch-zaofu',
  status: 'active',
  members: [
    {
      member_id: 'operator',
      member_type: 'operator',
      provider: '',
      backend: '',
      channel_role: 'operator',
      visibility_profile: 'planner',
      permissions: ['read', 'message', 'summarize'],
      status: 'connected',
    },
    {
      member_id: TARGET_MEMBER,
      member_type: 'provider_agent',
      provider: 'claude-headless',
      backend: 'claude-headless',
      channel_role: 'arch',
      visibility_profile: 'planner',
      permissions: ['read', 'message', 'summarize'],
      status: 'connected',
    },
  ],
  threads: { main: { thread_id: 'main' } },
  // The operator's triggering question.
  messages: [
    {
      event_id: 'evt-msg-1',
      message_id: TRIGGER_MSG_ID,
      thread_id: 'main',
      ts: '2026-06-05T07:00:00Z',
      actor: 'web',
      member_id: 'operator',
      role: 'user',
      source: 'web',
      text: '@claude-arch 你好',
    },
    // Agent's actual reply via channel.message.posted (the "real" one).
    {
      event_id: 'evt-reply-aaaa',
      message_id: `msg-${REAL_REQUEST_ID}-reply`,
      thread_id: 'main',
      ts: '2026-06-05T07:00:05Z',
      actor: TARGET_MEMBER,
      member_id: TARGET_MEMBER,
      role: 'assistant',
      source: 'channel',
      text: REPLY_BODY,
      refs: { request_id: REAL_REQUEST_ID, run_id: `run-${REAL_REQUEST_ID}` },
    },
  ],
  reply_requests: [
    // Local placeholder injected by channelDetailWithPendingMessage.
    {
      request_id: 'local-reply-msg-1-0',
      event_id: 'local-reply-msg-1-0',
      created_at: '2026-06-05T07:00:00.500Z',
      updated_at: '2026-06-05T07:00:00.500Z',
      thread_id: 'main',
      message_id: TRIGGER_MSG_ID,
      member_id: 'operator',
      target_member_id: TARGET_MEMBER,
      status: 'submitted',
      queue_state: 'ready',
      provider: 'agent',
      backend: 'agent',
      reason: `working for @${TARGET_MEMBER}`,
    },
    // Real backend reply_request for the same trigger, now completed.
    {
      request_id: REAL_REQUEST_ID,
      event_id: 'evt-req-aaaa',
      created_at: '2026-06-05T07:00:01Z',
      updated_at: '2026-06-05T07:00:05Z',
      thread_id: 'main',
      message_id: TRIGGER_MSG_ID,
      member_id: 'operator',
      target_member_id: TARGET_MEMBER,
      status: 'completed',
      queue_state: 'ready',
      provider: 'claude-headless',
      backend: 'claude-headless',
      provider_session_id: 'sess-aaaa',
      run_id: `run-${REAL_REQUEST_ID}`,
      provider_run_id: `run-${REAL_REQUEST_ID}`,
      reason: '',
    },
  ],
  workflow_requests: [],
  mentions_detected: [],
  provider_runs: [],
  agent_session_runs: [],
  context_packs: [],
  handoffs: [],
  state_updates: [],
  owner_reports: [],
  automation_reports: [],
  syntheses: [],
  attention: [],
  read_state: [],
  discussion: { mode: 'manual_mention' },
  typing: [],
  active_typing: [],
  attachments: [],
};

const channelSummary = {
  id: 'ch-zaofu',
  channel_id: 'ch-zaofu',
  name: 'ch-zaofu',
  status: 'active',
  message_count: 2,
  member_count: 2,
  pending_workflow_requests: 0,
  pending_reply_count: 0,
  members: baseChannel.members,
  attention: [],
  running_replies: [],
  queued_replies: [],
};

(async () => {
  const browser = await chromium.launch({
    executablePath: '/ms-playwright/chromium-1222/chrome-linux64/chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1200 } });
  const page = await ctx.newPage();

  // Intercept channel detail + listing requests.
  await ctx.route('**/api/**/channels/ch-zaofu*', async (route) => {
    const json = JSON.parse(JSON.stringify(baseChannel));
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(json) });
  });
  await ctx.route('**/api/**/channels**', async (route) => {
    const url = route.request().url();
    if (/channels\/ch-zaofu/.test(url)) return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        schema_version: 'channels.v1',
        generated_at: '2026-06-05T07:00:00Z',
        seq: 1,
        source: 'events.jsonl',
        channels: [channelSummary],
      }),
    });
  });

  console.log('navigate to channels page with mocked API');
  await page.goto(
    `${BASE_URL}/?project=${PROJECT_ID}&page=channels`,
    { waitUntil: 'domcontentloaded', timeout: 20000 },
  );
  await page.waitForTimeout(5000);

  await page.screenshot({ path: '/snapshots/channel-dedup-01-mocked.png', fullPage: true });

  // Count the rendered runs / status badges / reply bodies.
  const counts = await page.evaluate((REPLY) => {
    const all = document.body.innerText;
    const matches = (re) => (all.match(re) || []).length;
    return {
      replyBodyCount: matches(new RegExp(REPLY.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')),
      workingCount: matches(/\bWorking\b/g),
      doneCount: matches(/\bDone\b/g),
      replyTitleCount: matches(/\bReply\b/g),
      // Per-DOM-node check
      statusBadges: document.querySelectorAll('[class*="status"]').length,
      runs: document.querySelectorAll('[data-run-id]').length,
      parts: document.querySelectorAll('[data-part-id]').length,
      textContentSlice: all.slice(0, 800),
    };
  }, REPLY_BODY);

  console.log('\n=== DOM signal counts ===');
  console.log('  reply body occurrences :', counts.replyBodyCount, '(expect 1)');
  console.log('  "Working" occurrences  :', counts.workingCount, '(expect 0 because completed)');
  console.log('  "Done" occurrences     :', counts.doneCount, '(expect <=1)');
  console.log('  "Reply" title          :', counts.replyTitleCount);
  console.log('  status badges          :', counts.statusBadges);
  console.log('  data-run-id elements   :', counts.runs);
  console.log('  data-part-id elements  :', counts.parts);
  console.log('\nPage text head (~800 chars):\n  ', counts.textContentSlice.slice(0, 800).replace(/\n/g, '\n  '));

  // The KEY invariant: reply body appears exactly once even though the input
  // contains both a local placeholder and a real backend reply_request for
  // the same (target_member_id, message_id). Pre-fix this would have been 2.
  const pass = counts.replyBodyCount === 1;

  console.log(`\nFIX VERIFY: ${pass ? 'PASS — reply body rendered exactly once' : 'FAIL — reply body rendered ' + counts.replyBodyCount + ' times'}`);

  await browser.close();
  process.exit(pass ? 0 : 1);
})().catch((e) => { console.error('VERIFY ERROR:', e); process.exit(2); });
