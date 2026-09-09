import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const channelId = "ch-product-contract-e2e";

type Snapshot = {
  project?: { project_id?: string };
};

type ChannelDetail = {
  messages?: Array<Record<string, unknown>>;
  pinned_message_ids?: string[];
  unread_count?: number;
  consensus?: Record<string, Record<string, unknown>>;
  discussion_attention?: Record<string, Record<string, unknown>>;
  result_receipts?: Array<Record<string, unknown>>;
};

test.describe.configure({ mode: "serial", timeout: 120_000 });

async function json<T>(
  request: APIRequestContext,
  path: string,
): Promise<T> {
  const response = await request.get(path);
  const body = await response.json().catch(() => ({}));
  expect(
    response.ok(),
    `${path}: ${response.status()} ${JSON.stringify(body)}`,
  ).toBeTruthy();
  return body as T;
}

async function projectId(request: APIRequestContext): Promise<string> {
  const snapshot = await json<Snapshot>(request, "/api/snapshot");
  const id = String(snapshot.project?.project_id ?? "");
  expect(id).not.toBe("");
  return id;
}

async function openChannel(
  page: Page,
  id: string,
): Promise<void> {
  await page.addInitScript(({ actionToken }) => {
    window.localStorage.setItem("zf.webActionToken", actionToken);
  }, { actionToken: token });
  await page.goto(
    `/?project=${encodeURIComponent(id)}&page=channels`
      + `&channel=${encodeURIComponent(channelId)}`,
  );
  await expect(page.locator(".channel-page")).toContainText(
    "Product contract E2E",
    { timeout: 30_000 },
  );
}

function messageRow(page: Page, text: string) {
  return page.locator(".agent-turn-group").filter({ hasText: text }).last();
}

test("Channel product contract stays durable across Web actions and reload", async ({
  page,
  request,
}) => {
  expect(token).not.toBe("");
  const id = await projectId(request);
  const detailPath = (
    `/api/projects/${encodeURIComponent(id)}/channels/`
    + encodeURIComponent(channelId)
  );
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await openChannel(page, id);
  const discussionAttention = page.getByTestId("channel-discussion-attention");
  await expect(discussionAttention).toHaveAttribute(
    "data-discussion-state",
    "needs_input",
  );
  await expect(discussionAttention).toContainText("Needs input");
  await expect(discussionAttention).toContainText("Review result");
  await expect(discussionAttention).not.toContainText("Open debate");
  await expect(discussionAttention).not.toContainText("debating");
  const initialDetail = await json<ChannelDetail>(request, detailPath);
  expect(initialDetail.discussion_attention?.main?.state).toBe("needs_input");

  await discussionAttention.getByRole("button", {
    name: "Review result",
  }).click();
  await expect(page.locator(".channel-consensus-control")).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm" })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(discussionAttention).toBeVisible();
  const mobileLayout = await page.evaluate(() => {
    const timeline = document.querySelector<HTMLElement>(".channel-timeline");
    const composer = document.querySelector<HTMLElement>(".channel-composer");
    return {
      documentHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      timelineClientHeight: timeline?.clientHeight ?? 0,
      composerBottom: composer?.getBoundingClientRect().bottom ?? 0,
    };
  });
  expect(mobileLayout.documentHeight).toBeLessThanOrEqual(
    mobileLayout.viewportHeight + 2,
  );
  expect(mobileLayout.timelineClientHeight).toBeGreaterThan(20);
  expect(mobileLayout.composerBottom).toBeLessThanOrEqual(
    mobileLayout.viewportHeight + 1,
  );
  await page.setViewportSize({ width: 1280, height: 720 });

  await page.locator(".channel-tabs").getByRole("button", {
    name: "Details",
  }).click();
  await expect(page.locator(".channel-workspace-dashboard")).toContainText(
    "conversation",
  );
  await page.locator(".channel-tabs").getByRole("button", {
    name: "Chat",
  }).click();
  const userText = "Define a durable Channel product contract.";
  const userRow = messageRow(page, userText);
  await expect(userRow).toBeVisible();

  await userRow.getByRole("button", { name: "Pin message" }).click();
  await expect.poll(async () => {
    const detail = await json<ChannelDetail>(request, detailPath);
    return detail.pinned_message_ids?.length ?? 0;
  }).toBe(1);

  await userRow.getByRole("button", { name: "Reply to message" }).click();
  await expect(page.getByTestId("channel-reply-target")).toContainText(
    "operator",
  );
  const composer = page.getByRole("textbox", {
    name: "Message Product contract E2E",
  });
  await composer.fill("Owner reply preserves the exact parent message.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator(".channel-page")).toContainText(
    "Owner reply preserves the exact parent message.",
  );
  await expect.poll(async () => {
    const detail = await json<ChannelDetail>(request, detailPath);
    return (detail.messages ?? []).some((message) => (
      message.text === "Owner reply preserves the exact parent message."
      && Boolean(message.reply_to_message_id)
    ));
  }).toBeTruthy();

  let failedOnce = false;
  await page.route(
    "**/api/projects/*/actions/channel-post-message",
    async (route) => {
      if (!failedOnce) {
        failedOnce = true;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            ok: false,
            status: "temporary_unavailable",
            reason: "forced product-contract retry",
          }),
        });
        return;
      }
      await route.continue();
    },
  );
  const retryText = "CHANNEL_PRODUCT_E2E_RETRY_DRAFT";
  await composer.fill(retryText);
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator(".channel-composer-error")).toContainText(
    "forced product-contract retry",
  );
  await page.reload();
  const restoredComposer = page.getByRole("textbox", {
    name: "Message Product contract E2E",
  });
  await expect(restoredComposer).toContainText(retryText);
  await page.unroute("**/api/projects/*/actions/channel-post-message");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator(".channel-page")).toContainText(retryText);

  await page.getByTitle("Search messages").click();
  await page.getByRole("button", { name: "Mark Read" }).click();
  await expect.poll(async () => {
    const detail = await json<ChannelDetail>(request, detailPath);
    return Number(detail.unread_count ?? -1);
  }).toBe(0);

  await expect(page.locator(".channel-page")).toContainText(
    "Canonical PRD awaiting owner decision",
  );
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect.poll(async () => {
    const detail = await json<ChannelDetail>(request, detailPath);
    return Boolean(detail.consensus?.main?.human_confirmed);
  }).toBeTruthy();

  await page.getByTitle("More").click();
  await page.getByRole("button", {
    name: "Workflow",
    exact: true,
  }).click();
  await expect(page.locator(".channel-drawer")).toContainText("Linked results");
  await expect(page.locator(".channel-drawer")).toContainText(
    "workflow terminal",
  );
  await expect(page.locator(".channel-drawer")).toContainText(
    "TASK-PRODUCT-E2E",
  );
  const detail = await json<ChannelDetail>(request, detailPath);
  expect(detail.result_receipts).toHaveLength(1);
  expect(
    consoleErrors.filter((line) => (
      !line.includes("favicon")
      && !line.includes("503 (Service Unavailable)")
    )),
  ).toEqual([]);
});
