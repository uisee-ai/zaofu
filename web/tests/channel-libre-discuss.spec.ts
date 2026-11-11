import { test, expect } from "@playwright/test";

const cid = "ch-libre-browser";
const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST || "libre-isolated-test-token";
test.describe.configure({ mode: "serial", timeout: 90_000 });

test("quiet free discussion, visible PRD entry, input errors and manual publication", async ({ page, request }) => {
  const snapshot = await (await request.get("/api/snapshot")).json();
  const project = String(snapshot.project?.project_id || "default");
  const channelUrl = `/api/projects/${encodeURIComponent(project)}/channels/${cid}`;
  const read = async () => {
    const response = await request.get(channelUrl);
    expect(response.ok()).toBeTruthy();
    const body = await response.json(); return body.channel || body;
  };
  await page.addInitScript((value) => localStorage.setItem("zf.webActionToken", value), token);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`/?project=${encodeURIComponent(project)}&page=channels&channel=${cid}`);
  await expect(page.locator(".channel-page")).toContainText("自由讨论浏览器验收");
  await expect(page.getByTestId("libre-discussion-notice")).toContainText("你决定何时生成 PRD");
  await expect(page.getByRole("button", { name: "Start discussion", exact: true })).toBeHidden();
  expect((await read()).messages).toHaveLength(0);
  await page.getByRole("button", { name: "生成 / 查看 PRD", exact: true }).click();
  const controls = page.getByTestId("libre-prd-controls");
  await expect(controls).toBeVisible();
  await controls.getByRole("button", { name: "生成 PRD / 查看本次进度", exact: true }).click();
  await expect(controls.getByRole("alert")).toContainText("材料");
  await page.getByTitle("Close drawer", { exact: true }).click();
  const composer = page.getByRole("textbox", { name: "Message 自由讨论浏览器验收", exact: true });
  await composer.fill("我想做一个不登录的个人待办清单，在本机保存，刷新后保留。先讨论，不要自动生成 PRD。");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(async () => (await read()).reply_requests.filter((r: Record<string, unknown>) => r.status === "completed").length).toBe(1);
  const afterChat = await read();
  expect(afterChat.reply_requests[0].target_member_id).toBe("product_pm");
  expect(afterChat.synthesis_requests).toHaveLength(0);
  await page.getByRole("button", { name: "生成 / 查看 PRD", exact: true }).click();
  await controls.getByRole("textbox", { name: "补充 PRD 原材料", exact: true }).fill("删除前需要确认。不做跨设备同步。保留未决事项。");
  await controls.getByRole("checkbox").check();
  await controls.getByRole("button", { name: "生成 PRD / 查看本次进度", exact: true }).click();
  await expect.poll(async () => (await read()).syntheses.length, { timeout: 30_000 }).toBe(1);
  await expect(controls).toContainText("后续补充用于下一版");
  await expect(page.getByRole("button", { name: "Download PRD", exact: true })).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: "test-results/libre-prd-desktop.png", fullPage: true });
  await page.getByTitle("Close drawer", { exact: true }).click();
  await composer.fill("再补充：事项文字最多一百个字，这部分放到下一版。");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(async () => (await read()).messages.some((m: Record<string, unknown>) => String(m.text).includes("下一版"))).toBeTruthy();
  expect((await read()).syntheses).toHaveLength(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "生成 / 查看 PRD", exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/libre-chat-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("rejected candidate is readable separately from the formal PRD", async ({ page, request }) => {
  const snapshot = await (await request.get("/api/snapshot")).json();
  const project = String(snapshot.project?.project_id || "default");
  await page.addInitScript((value) => localStorage.setItem("zf.webActionToken", value), token);
  await page.goto(`/?project=${encodeURIComponent(project)}&page=channels&channel=ch-libre-candidate`);
  await page.getByRole("button", { name: "生成 / 查看 PRD", exact: true }).click();
  await page.getByRole("button", { name: "查看未通过校验的候选稿", exact: true }).click();
  const candidate = page.locator(".libre-prd-candidate");
  await expect(candidate).toContainText("不能用于创建任务");
  await expect(candidate).toContainText("待办清单候选稿");
  await expect(page.getByRole("button", { name: "Download PRD", exact: true })).toBeHidden();
  await page.screenshot({ path: "test-results/libre-rejected-candidate.png", fullPage: true });
});
