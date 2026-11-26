import { expect, test } from "@playwright/test";
import { installUiFixture } from "./ui-alignment.fixture";

const loginRequired = {
  status: 401, headers: { "X-ZF-Login": "required" }, json: { error: "local_login_required" },
};

for (const source of ["session bootstrap", "task in a legacy tab"] as const) {
  test(`a local login challenge during ${source} reopens the current task URL`, async ({ page }) => {
    await installUiFixture(page);
    let challenged = false;
    await page.route(url => url.pathname === "/", route => {
      if (challenged && route.request().isNavigationRequest()) {
        return route.fulfill({ contentType: "text/html; charset=utf-8", body: "<h1>登录本地工作区</h1>" });
      }
      return route.continue();
    });
    await page.route("**/api/web-session", route => {
      if (source === "session bootstrap") {
        challenged = true;
        return route.fulfill(loginRequired);
      }
      return route.fulfill({ json: { mode: "token_required", unlocked: false, actions_enabled: false } });
    });
    if (source === "task in a legacy tab") {
      await page.route("**/tasks/UI-101", route => {
        challenged = true;
        return route.fulfill(loginRequired);
      });
      await page.goto("/?project=ui-review&page=board&view=list");
      await page.locator('button[data-task-id="UI-101"]').click();
    } else {
      await page.goto("/?project=ui-review&page=board&task=UI-101");
    }
    await expect(page.getByRole("heading", { name: "登录本地工作区" })).toBeVisible();
    const query = new URL(page.url()).searchParams;
    expect(query.get("project")).toBe("ui-review");
    expect(query.get("task")).toBe("UI-101");
  });
}

test("a task access error without a login challenge keeps the reader and retry action", async ({ page }) => {
  await installUiFixture(page);
  await page.route("**/tasks/UI-101", route => route.fulfill({ status: 401, json: { error: "action_denied" } }));
  await page.goto("/?project=ui-review&page=board&task=UI-101");
  const alert = page.getByRole("region", { name: "任务详情" }).getByRole("alert").filter({ hasText: "任务详情读取失败" });
  await expect(alert).toContainText("401");
  await expect(alert.getByRole("button", { name: "重试" })).toBeVisible();
});

test("the selected task contract is readable while execution history is pending", async ({ page }) => {
  await installUiFixture(page);
  let releaseDetail!: () => void;
  const delayed = new Promise<void>(resolve => { releaseDetail = resolve; });
  const task = { id: "UI-101", title: "任务正文验收", status: "in_progress", assigned_to: "",
    blocked_reason: "", retry_count: 0, phase: null, created_at: "", contract: { behavior: "从这项任务的合同读取正文。" } };
  await page.route("**/api/projects/ui-review/tasks/UI-101/record", route => route.fulfill({ json: task }));
  await page.route("**/api/projects/ui-review/tasks/UI-101", async route => {
    await delayed;
    await route.fulfill({ json: { task, contract: { behavior: "完整执行详情已经返回。" }, events: [] } });
  });
  try {
    await page.goto("/?project=ui-review&page=board&task=UI-101");
    await expect(page.locator("article").getByText(task.contract.behavior, { exact: true })).toBeVisible();
  } finally {
    releaseDetail();
  }
  await expect(page.locator("article").getByText("完整执行详情已经返回。", { exact: true })).toBeVisible();
});
