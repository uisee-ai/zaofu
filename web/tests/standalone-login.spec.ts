import { expect, test } from "@playwright/test";

test.skip(process.env.ZF_E2E_FRESH_ACCOUNT !== "1", "Requires an isolated fresh standalone workspace");

test("local login, first use, and password change across two browsers", async ({ browser, page }) => {
  const origin = process.env.ZF_WEB_BASE_URL!;
  const other = await browser.newContext({ baseURL: origin });
  const second = await other.newPage();
  async function signIn(target: typeof page, password: string) {
    await target.getByLabel("用户名").fill("zaofu");
    await target.getByLabel("密码", { exact: true }).fill(password);
    await target.getByRole("button", { name: "登录", exact: true }).click();
  }
  try {
    await page.goto("/?page=project");
    await expect(page.locator("header")).toHaveText("ZaoFu");
    await expect(page.getByText("造", { exact: true })).toHaveCount(0);
    await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.fonts.check('600 14.5px Geist', "ZaoFu"))).toBe(true);
    const signInButton = page.getByRole("button", { name: "登录", exact: true });
    await expect(signInButton).toHaveCSS("height", "36px");
    await expect(signInButton).toHaveCSS("border-radius", "6px");
    await signIn(page, "wrong");
    await expect(page.getByRole("alert")).toContainText("用户名或密码错误");
    await page.route("**/api/web-session", route => route.fulfill({ status: 503, body: "{}" }), { times: 1 });
    await page.route("**/api/workspace/onboarding", route => route.fulfill({ status: 503, body: "{}" }), { times: 1 });
    await signIn(page, "zaofu");
    await expect(page.getByRole("alert")).toContainText("登录状态加载失败");
    await page.getByRole("button", { name: "重试" }).click();
    await expect(page.getByRole("alert")).toContainText("首次设置状态加载失败");
    await page.getByRole("button", { name: "重试" }).click();
    const wizard = page.getByTestId("welcome-wizard");
    await expect(wizard).toBeVisible();
    await page.getByTestId("welcome-backend-codex").click();
    await page.getByTestId("welcome-continue").click();
    await expect(page.getByRole("heading", { name: "Environment" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name: "Environment" })).toBeVisible();
    const logout = await page.request.post("/auth/local/logout", { headers: { Origin: origin }, data: {} });
    expect(logout.ok()).toBeTruthy();
    await page.getByRole("button", { name: "跳过此步" }).click();
    await expect(page.getByRole("heading", { name: "登录本地工作区" })).toBeVisible();
    await signIn(page, "zaofu");
    await expect(page.getByRole("heading", { name: "Environment" })).toBeVisible();
    await page.getByRole("button", { name: "跳过此步" }).click();
    await expect(page.getByRole("heading", { name: "Ready" })).toBeVisible();
    await expect(page.getByTestId("welcome-action-token")).toHaveCount(0);
    const legacy = await page.request.post("/api/workspace/onboarding", {
      headers: { Origin: origin }, data: { action: "step", step: 3 },
    });
    expect(legacy.ok()).toBeTruthy();
    await page.reload();
    await expect(page.getByRole("heading", { name: "Ready" })).toBeVisible();
    await page.getByTestId("welcome-finish").click();
    await expect(wizard).toBeHidden();
    await second.goto("/");
    await signIn(second, "zaofu");
    await expect(second.locator(".rail-account .account-switcher summary")).toHaveAccessibleName("zaofu");
    await expect(second.getByTestId("welcome-wizard")).toHaveCount(0);

    for (const button of ["Open zaofu", "Toggle Terminal"]) {
      await page.getByRole("button", { name: button }).click();
      const dialog = page.getByRole("dialog", { name: "Add/Open Project" });
      await expect(dialog).toBeVisible();
      await dialog.getByRole("button", { name: "Close" }).click();
    }
    await page.getByRole("button", { name: /^(Settings|设置)$/ }).click();
    await page.getByRole("button", { name: /^(Password|密码)$/ }).click();
    await page.getByLabel(/^(Current password|当前密码)$/).fill("zaofu");
    await page.getByLabel(/^(New password|新密码)$/).fill("browser-test-password");
    const confirm = page.getByLabel(/^(Confirm new password|确认新密码)$/);
    await confirm.fill("mismatched");
    const save = page.getByRole("button", { name: /^(Update password|修改密码)$/ });
    await save.click();
    await expect(page.getByRole("alert")).toContainText(/match|不一致/);
    await confirm.fill("browser-test-password");
    await save.click();
    await expect(page.getByRole("heading", { name: "登录本地工作区" })).toBeVisible();
    await second.reload();
    await expect(second.getByRole("heading", { name: "登录本地工作区" })).toBeVisible();
    await signIn(page, "browser-test-password");
    await page.locator(".rail-account .account-switcher summary").click();
    await page.getByRole("button", { name: /Sign out|退出登录/ }).click();
    await expect(page.getByRole("heading", { name: "登录本地工作区" })).toBeVisible();
  } finally {
    await other.close();
  }
});
