import { expect, test, type Page } from "@playwright/test";

const channelId = "ch-fact-scope-fixture";
const question = "首次扫描取消的运行事实是否已经核实？";

async function setup(page: Page, unlocked = true, consolidating = false) {
  const writes: Record<string, unknown>[] = [];
  const errors: string[] = [];
  const fact = { question_id: "q-fact", thread_id: "main", kind: "fact", status: "open",
    question, target_member_id: "arch", asked_by: "arch", answer: "", resolution: "" };
  const detail = { channel_id: channelId, name: "Fact scope", status: "open", seq: 1,
    members: [{ member_id: "arch", member_type: "agent", status: "active" }],
    leader_member_id: "arch", discussion: { mode: "multi_lens", default_responder_id: "arch" },
    discussions: { main: { state: "phase2_relay", requirement_message_id: "original" } },
    messages: [{ message_id: "original", thread_id: "main", role: "user", text: "Test scope control",
      ts: "2026-10-10T00:00:00Z" }],
    open_questions: [fact], owner_questionnaires: {}, provider_runs: [], reply_requests: [],
    workflow_requests: [], syntheses: [], consensus: {},
    cross_reviews: [{ request_id: "review-fact", question_id: "q-fact", outcome: "unresolved" }],
    discussion_attention: { main: { thread_id: "main", state: "phase2_relay", execution_state: "stalled",
      attention_kind: consolidating ? "consolidating" : "question", reason: "cross_review_unresolved",
      active_agent_count: 0, owner_question_count: 0, open_question_count: 1,
      completed_reply_count: 0, failed_reply_count: 0,
      question_consolidation_status: consolidating ? "consolidating" : "open" } },
  };
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/**", async route => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (req.method() !== "GET" && path.endsWith("/actions/channel-question-resolve")) {
      writes.push(req.postDataJSON().payload);
      fact.status = "resolved";
      fact.resolution = "out_of_scope";
      fact.answer = String(writes.at(-1)?.answer || "");
      await route.fulfill({ json: { ok: true, status: "resolved" } });
    } else if (path === "/api/web-session") {
      await route.fulfill({ json: { actions_enabled: unlocked, unlocked } });
    } else if (path.endsWith("/channels")) {
      await route.fulfill({ json: { channels: [detail] } });
    } else if (path.endsWith(`/${channelId}`) || path.endsWith(`/${channelId}/conversation`)) {
      await route.fulfill({ json: detail });
    } else await route.continue();
  });
  await page.goto(`/?page=channels&channel=${channelId}`);
  await page.getByTestId("channel-flow-discussion").click();
  const card = page.getByTestId("channel-fact-scope-control");
  await expect(card).toContainText(question);
  await card.locator("summary").click();
  return { card, writes, errors, detail };
}

test("Owner excludes an unresolved fact from scope through the controlled action", async ({ page }) => {
  const { card, writes, errors, detail } = await setup(page);
  const submit = card.getByRole("button", { name: "排除出本次定稿范围" });
  await expect(card).toContainText("范围排除不表示事实已经验证");
  await expect(submit).toBeDisabled();
  await card.getByLabel("范围排除理由").fill("  后续运行验收单独核实，本次仅定稿产品需求。  ");
  await submit.click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ channel_id: channelId, thread_id: "main", question_id: "q-fact",
    resolution: "out_of_scope", resolved_by: "owner:operator",
    answer: "后续运行验收单独核实，本次仅定稿产品需求。" });
  await page.reload();
  await page.getByTestId("channel-flow-discussion").click();
  await expect(page.getByTestId("channel-fact-scope-control")).toHaveCount(0);
  expect(detail.cross_reviews[0].outcome).toBe("unresolved");
  expect(errors).toEqual([]);
});

for (const boundary of ["locked", "consolidating"]) {
  test(`fact scope control is disabled when ${boundary}`, async ({ page }) => {
    const { card, writes, errors } = await setup(page, boundary !== "locked", boundary === "consolidating");
    await expect(card.getByLabel("范围排除理由")).toBeDisabled();
    await expect(card.getByRole("button", { name: "排除出本次定稿范围" })).toBeDisabled();
    expect(writes).toEqual([]);
    expect(errors).toEqual([]);
  });
}
