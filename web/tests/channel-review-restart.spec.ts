import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const channelId = "ch-review-restart-fixture";
const ts = "2026-10-08T00:00:00Z";

function fixture(blocked: boolean) {
  const prd = { thread_id: "main", artifact_ref: "artifacts/prd.json", artifact_digest: "fixture-digest",
    prd_revision: 2, event_id: "synthesis", readiness_verdict: "needs_multi_lens",
    readiness_ref: "readiness.json", readiness_digest: "risk-digest", summary: "Current PRD", open_questions: [] };
  return {
    channel_id: channelId, name: "Review and restart", status: "open", seq: 1,
    members: [{ member_id: "product_pm", member_type: "agent", status: "active" }],
    leader_member_id: "product_pm", discussion: { mode: "multi_lens", default_responder_id: "product_pm" },
    discussions: { main: { state: "blocked", requirement_message_id: "review" } },
    messages: [
      { message_id: "original", thread_id: "main", role: "user", text: "Preserve the full original requirement.", ts,
        refs: { evaluation_source_document: true } },
      { message_id: "review", thread_id: "main", role: "user", text: "Review this generated draft.", ts,
        refs: { consensus_review_id: "review-fixture" } },
    ],
    provider_runs: [], reply_requests: [], workflow_requests: [], open_questions: [], owner_questionnaires: {},
    syntheses: [prd], consensus: { main: { ...prd, reached_event_id: "", human_confirmed: false,
      blocked: blocked ? [{ reason: "Required acceptance is missing" }] : [] } },
    discussion_attention: { main: { thread_id: "main", state: "blocked", execution_state: "blocked",
      attention_kind: "none", reason: "discussion_stalled", active_agent_count: 0, owner_question_count: 0,
      open_question_count: 0, completed_reply_count: 0, failed_reply_count: 0, can_restart: true } },
  };
}

async function setup(page: Page, request: APIRequestContext, detail: ReturnType<typeof fixture>) {
  const projects = await (await request.get("/api/workspace/projects")).json();
  const writes: Array<{ path: string; payload: Record<string, unknown> }> = [];
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/**", async route => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (req.method() !== "GET") {
      writes.push({ path, payload: req.postDataJSON().payload });
      await route.fulfill({ json: { ok: true, status: "accepted" } });
    } else if (path === "/api/web-session") {
      await route.fulfill({ json: { actions_enabled: true, unlocked: true } });
    } else if (path.endsWith("/channels")) {
      await route.fulfill({ json: { channels: [detail] } });
    } else if (path.endsWith(`/${channelId}/prd-preview`)) {
      const prd = detail.syntheses[0];
      await route.fulfill({ json: { channel_id: channelId, thread_id: "main", revision: prd.prd_revision,
        artifact_ref: prd.artifact_ref, artifact_digest: prd.artifact_digest, markdown: "# Full PRD", title: "PRD",
        readiness: { ref: prd.readiness_ref, digest: prd.readiness_digest, verdict: prd.readiness_verdict,
          implementation_start: false, reason: "Acceptance requires review", gaps: ["Acceptance missing"], risks: ["Review pending"] } } });
    } else if (path.endsWith(`/${channelId}/conversation`) || path.endsWith(`/${channelId}`)) {
      await route.fulfill({ json: detail });
    } else await route.continue();
  });
  await page.goto(`/?project=${encodeURIComponent(String(projects.active_project_id))}&page=channels&channel=${channelId}`);
  await expect(page.getByTestId("channel-flow-nav")).toBeVisible();
  return { writes, errors };
}

test("review blockers hide owner confirmation until the revised PRD clears review", async ({ page, request }) => {
  const detail = fixture(true);
  const state = await setup(page, request, detail);
  await page.getByTestId("channel-flow-prd").click();
  const panel = page.getByTestId("channel-consensus-control");
  await expect(panel).toContainText("Review blockers require another PRD revision");
  await expect(panel.getByRole("checkbox")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: /Confirm|确认|^Block$/ })).toHaveCount(0);
  expect(state.writes).toEqual([]);
  detail.consensus.main.blocked = [];
  await page.reload();
  await page.getByTestId("channel-flow-prd").click();
  await expect(panel.getByRole("checkbox")).toBeVisible();
  await expect(panel.getByRole("button", { name: "接受风险并确认", exact: true })).toBeDisabled();
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("discussion restart sends the original requirement through the controlled action", async ({ page, request }) => {
  const detail = fixture(true);
  const state = await setup(page, request, detail);
  await page.getByTitle("More", { exact: true }).click();
  await page.getByRole("button", { name: "Restart discussion", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0].path).toMatch(/\/actions\/channel-discussion-start$/);
  expect(state.writes[0].payload).toMatchObject({ channel_id: channelId, thread_id: "main", restart: true,
    requirement_message_id: "original", message: detail.messages[0].text });
  expect(state.errors).toEqual([]);
});

test("rejected continuation explains roster mismatch and preserves restart recovery", async ({ page, request }) => {
  const detail = fixture(false);
  Object.assign(detail.discussion_attention.main, {
    reason: "next_round_rejected", next_round_rejection_reason: "next_round_targets_not_in_current_roster",
  });
  const state = await setup(page, request, detail);
  await page.getByTestId("channel-flow-discussion").click();
  const activity = page.getByTestId("channel-discussion-activity");
  await expect(activity).toContainText("PRD 已生成，补充讨论受阻");
  await expect(activity).toContainText("续轮成员不在本轮冻结名单中");
  await activity.getByRole("button", { name: "Restart discussion", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0].payload).toMatchObject({ restart: true, requirement_message_id: "original" });
  expect(state.errors).toEqual([]);
});

test("restart uses the latest explicitly marked Owner requirement", async ({ page, request }) => {
  const detail = fixture(true);
  detail.messages.push({ message_id: "amended", thread_id: "main", role: "user", ts,
    text: "Preserve PostgreSQL. Permit only an isolated verification Task.",
    refs: { evaluation_source_document: true } });
  const state = await setup(page, request, detail);
  await page.getByTestId("channel-flow-discussion").click();
  await page.getByRole("button", { name: "Restart discussion", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0].payload).toMatchObject({ restart: true,
    requirement_message_id: "amended", message: detail.messages.at(-1)?.text });
  expect(state.errors).toEqual([]);
});
