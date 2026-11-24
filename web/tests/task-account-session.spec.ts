import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

// The target is a disposable account workspace with TASK-TRAIN and a scripted chat provider.
const passwordFile = process.env.ZF_E2E_TASK_LOGIN_PASSWORD_FILE;
test.skip(!passwordFile, "Requires the isolated password-login task fixture");

test("password login authorizes a task conversation and logout revokes it", async ({ page }, info) => {
  test.setTimeout(90_000);
  const origin = process.env.ZF_WEB_BASE_URL!;
  const beforeLogin = await page.request.get("/api/web-session");
  expect(beforeLogin.status()).toBe(401);
  await page.goto("/?page=board&task=TASK-TRAIN");
  await page.getByLabel("用户名").fill("zaofu");
  await page.getByLabel("密码", { exact: true }).fill(readFileSync(passwordFile!, "utf8").trim());
  await page.getByRole("button", { name: "登录", exact: true }).click();

  const reader = page.getByRole("region", { name: "任务详情" });
  await expect(reader.getByRole("heading", { level: 1 })).toContainText("checkpoint", { timeout: 30_000 });
  const session = await (await page.request.get("/api/web-session")).json();
  expect(session).toMatchObject({ mode: "local_account", actions_enabled: true, requires_token: false });
  const chat = reader.getByRole("region", { name: "任务 Agent" });
  await expect(chat.getByPlaceholder("action token", { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem("zf.webActionToken"))).toBeNull();

  await page.setViewportSize({ width: 1440, height: 960 });
  const account = page.locator(".rail-account .account-switcher summary");
  await expect(account.locator(".account-name")).toHaveText("zaofu");
  await expect(account).toBeInViewport({ ratio: 1 });
  const desktop = (await account.boundingBox())!;
  expect(desktop.x).toBeLessThan(30);
  expect(desktop.y).toBeGreaterThan(880);
  await page.getByRole("button", {name: /Collapse sidebar|收起侧栏/}).click();
  await expect(account.locator(".account-name")).toBeHidden();
  await account.click();
  const password = page.getByRole("link", {name: /Password|密码/});
  await expect(password).toBeInViewport({ratio: 1});
  await password.focus();
  await page.keyboard.press("Escape");
  await expect(account).toBeFocused();
  await page.getByRole("button", {name: /Expand sidebar|展开侧栏/}).click();
  await expect(reader.getByRole("heading", { name: "验收条件" })).toBeVisible();
  await page.screenshot({ path: info.outputPath("desktop.png") });
  const input = chat.getByPlaceholder("询问或补充这项任务…");
  await input.fill("请确认本任务接收到了这条追问。");
  const sent = page.waitForResponse(r => r.url().endsWith("/actions/chat-orchestrator") && r.request().method() === "POST");
  await chat.getByRole("button", { name: "Send message" }).click();
  const response = await sent;
  const headers = await response.request().allHeaders();
  expect(headers["x-zf-web-token"]).toBeUndefined();
  expect(headers.authorization).toBeUndefined();
  expect(headers.cookie).toMatch(/zf_local_/);
  expect(response.ok()).toBeTruthy();
  expect(await response.json()).toMatchObject({ ok: true });
  await expect(chat.getByText("已收到任务追问（验证用回复）。", { exact: true }).first()).toBeVisible({ timeout: 40_000 });
  await chat.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("conversation.png") });

  await page.setViewportSize({ width: 390, height: 844 });
  await reader.locator(".reader-article").evaluate(el => { el.scrollTop = 0; });
  await reader.getByRole("button", { name: "属性", exact: true }).click();
  await expect(reader.getByRole("complementary")).toContainText("TASK-DATASET");
  await page.screenshot({ path: info.outputPath("mobile.png") });
  await expect(account).toBeHidden();
  await page.getByRole("button", {name: /Open navigation|打开导航/}).click();
  await expect(account.locator(".account-name")).toBeInViewport({ ratio: 1 });
  expect((await account.boundingBox())!.y).toBeGreaterThan(760);
  await account.click();
  const logoutButton = page.getByRole("button", { name: /Sign out|退出登录/ });
  await expect(logoutButton).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: info.outputPath("account-menu-mobile.png") });
  const loggedOut = page.waitForResponse(r => r.url().endsWith("/auth/local/logout") && r.request().method() === "POST");
  await logoutButton.click();
  const logout = await loggedOut;
  expect(logout.ok()).toBeTruthy();
  const denied = await page.request.post(response.url(), { headers: { Origin: origin }, data: response.request().postDataJSON() });
  expect(denied.status()).toBe(401);
  await expect(page.getByRole("button", { name: "登录", exact: true })).toBeVisible({ timeout: 20_000 });
});
