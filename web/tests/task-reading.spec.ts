import { expect, test } from "@playwright/test";
import { installUiFixture, uiTasks } from "./ui-alignment.fixture";

test("task conversations send with the current binding and restore only that task's history", async ({ page }, info) => {
  const { errors } = await installUiFixture(page, { snapshot: { runtime: {
    mode: "live", live: true, actions: { mutation_enabled: true, allowed: ["chat-orchestrator"] },
    web_session: { mode: "local_trusted", actions_enabled: true },
    agent_surface: { configured_backend: "codex-headless", permission_profile: "workspace_writer",
      backends: [{ id: "codex-headless", available: true, default: true }] },
  } } });
  await page.route("**/webchat/model-catalog?*", route => route.fulfill({ json: { backend: "codex-headless", models: [], session_defaults: {} } }));
  const history = new Map<string, object[]>();
  const sent: string[] = [];
  await page.route("**/agent-session/history?*", route => {
    const thread = new URL(route.request().url()).searchParams.get("thread_id")!;
    return route.fulfill({ json: { items: history.get(thread) ?? [], has_more: false } });
  });
  await page.route("**/actions/chat-orchestrator", route => {
    const p = route.request().postDataJSON().payload;
    expect(p.thread_key).toBe(`task:${p.task_id}`);
    expect(p.project_id).toBe("ui-review");
    expect(p.conversation_id).toBe("kanban:ui-review");
    sent.push(p.task_id);
    const event = (seq: number, type: string, payload: object) => ({
      id: `evt-${p.task_id}-${seq}`, seq, type, task_id: p.task_id, ts: "2026-11-23T08:00:00Z",
      payload: { ...p, ...payload },
    });
    history.set(p.thread_key, [
      event(1, "user.message", { target: "kanban-agent", runtime_delivery: "headless" }),
      event(2, "kanban.agent.turn.created", { message_event_id: `evt-${p.task_id}-1` }),
      event(3, "kanban.agent.reply", { answer: `答复 ${p.task_id}` }),
    ]);
    return route.fulfill({ json: { ok: true, status: "accepted" } });
  });
  await page.goto("/?project=ui-review&page=board&view=list");
  const chat = page.getByRole("region", { name: "任务 Agent" });
  for (const task of uiTasks.slice(0, 2)) {
    await page.locator(`button[data-task-id="${task.id}"]`).click();
    const input = chat.getByPlaceholder("询问或补充这张任务…");
    await expect(chat).not.toContainText("答复 UI-101");
    await input.fill(`进展 ${task.id}`);
    await input.press("Enter");
    await expect(chat.getByText(`答复 ${task.id}`, { exact: true })).toBeVisible();
    await expect(chat.getByTestId("agent-task-unref")).toHaveCount(0);
    await page.goBack();
  }
  await page.locator('button[data-task-id="UI-101"]').click();
  await page.reload();
  await expect(chat.getByText("答复 UI-101", { exact: true })).toBeVisible();
  await expect(chat).not.toContainText("答复 UI-102");
  const expand = page.getByRole("button", { name: "展开对话" });
  await expand.click();
  await expect(page.getByRole("button", { name: "收起对话" })).toHaveAttribute("aria-expanded", "true");
  await chat.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("task-conversation.png") });
  await page.getByRole("button", { name: "收起对话" }).click();
  await expect(expand).toHaveAttribute("aria-expanded", "false");
  await expect(chat.getByText("答复 UI-101", { exact: true })).toBeVisible();
  expect(sent).toEqual(["UI-101", "UI-102"]);
  expect(errors).toEqual([]);
});

test("narrow task reading keeps attributes optional and evidence in reach", async ({ page }, info) => {
  const { writes, errors } = await installUiFixture(page, { locked: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?project=ui-review&page=task&task=UI-104&agent=open");
  const reader = page.getByRole("region", { name: "任务详情" });
  await expect(reader.getByRole("heading", { level: 1 })).toHaveText(uiTasks[3]!.title);
  await expect(reader.locator(".reader-status")).toContainText("Waiting for the owner's acceptance criteria.");
  const attrs = reader.getByRole("button", { name: "属性", exact: true });
  await expect(attrs).toHaveAttribute("aria-expanded", "false");
  await attrs.click();
  await expect(reader.getByRole("complementary")).toContainText("dev-2");
  await attrs.click();
  await expect(page.locator(".headless-input")).toHaveCount(1);
  await page.keyboard.press("Control+k");
  await page.getByRole("dialog", { name: "Command Palette" }).getByRole("button", { name: "Kanban Agent" }).click();
  await expect(reader.locator(".headless-input")).toBeFocused();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toHaveCount(0);
  await reader.getByRole("button", { name: "返回看板" }).focus();
  await reader.locator(".reader-article").evaluate(el => el.scrollTop = 0);
  await page.screenshot({ path: info.outputPath("task-mobile.png") });
  await reader.getByRole("button", { name: "查看验证依据" }).click();
  await expect(reader.getByTestId("task-evidence-view")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await reader.getByRole("button", { name: "返回看板" }).click();
  await expect(page.getByRole("region", { name: "任务详情" })).toHaveCount(0);
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});

test("returning to a filtered long list restores scroll, focus and browser history", async ({ page }) => {
  const tasks = Array.from({ length: 48 }, (_, i) => ({
    ...uiTasks[0], id: `L-${100 + i}`, title: `阅读记录 ${i + 1}`,
  }));
  await installUiFixture(page, { locked: true, snapshot: { tasks } });
  await page.route("**/tasks/L-*", route => {
    const id = new URL(route.request().url()).pathname.split("/").pop();
    return route.fulfill({ json: { task: tasks.find(t => t.id === id), contract: {}, events: [] } });
  });
  await page.goto("/?project=ui-review&page=board&view=list");
  const list = page.locator(".workbench-task-list");
  const filter = page.getByLabel("Search tasks", { exact: true });
  await filter.fill("阅读记录");
  await expect(list.locator(".workbench-task-row")).toHaveCount(tasks.length);
  const row = list.locator(".workbench-task-row").nth(26);
  await row.evaluate(el => el.scrollIntoView({ block: "center" }));
  const offset = await list.evaluate(el => el.scrollTop);
  expect(offset).toBeGreaterThan(0);
  await row.click();
  const reader = page.getByRole("region", { name: "任务详情" });
  await expect(reader.getByRole("heading", { level: 1 })).toHaveText(tasks[26]!.title);
  await page.goBack();
  await expect(row).toBeFocused();
  await expect(filter).toHaveValue("阅读记录");
  expect(await list.evaluate(el => el.scrollTop)).toBe(offset);
  await page.goForward();
  await expect(reader.getByRole("heading", { level: 1 })).toHaveText(tasks[26]!.title);
  await reader.getByRole("button", { name: "返回看板" }).click();
  await expect(row).toBeFocused();
  expect(await list.evaluate(el => el.scrollTop)).toBe(offset);
});

test("a task refresh preserves drafts and shows the latest bound acceptance evidence", async ({ page }) => {
  const { writes, errors } = await installUiFixture(page, { locked: true });
  const task = uiTasks[0]!;
  let reads = 0;
  const contract = {
    behavior: "检查标题和键盘焦点",
    acceptance_criteria: ["可以返回", { id: "AC-2", text: "可继续操作" }],
    acceptance_evidence: { "0": ["evt-back"], "AC-2": ["evt-focus"] },
  };
  await page.route("**/tasks/UI-101", route => {
    reads++;
    return route.fulfill({ json: { task, contract: { ...contract, behavior: `${contract.behavior} · ${reads}` }, events: [] } });
  });
  await page.goto("/?project=ui-review&page=board&task=UI-101");
  const reader = page.getByRole("region", { name: "任务详情" });
  await expect(reader).toContainText("检查标题和键盘焦点 · 1");
  const criterion = reader.getByRole("listitem").filter({ hasText: "可以返回" });
  await criterion.locator("summary").click();
  await expect(criterion).toContainText("evt-back");
  const second = reader.getByRole("listitem").filter({ hasText: "可继续操作" });
  await second.locator("summary").click();
  await expect(second).toContainText("evt-focus");

  await reader.getByText("任务操作", { exact: true }).click();
  const title = reader.getByRole("textbox", { name: "Title", exact: true });
  await title.fill("尚未保存的标题");
  const composer = reader.locator(".headless-input");
  await composer.fill("尚未发送的追问");
  await page.evaluate(() => {
    for (const [seq, taskId, type] of [[21, "UI-102", "verify.failed"], [22, "UI-101", "worker.heartbeat"]]) {
      window.dispatchEvent(new CustomEvent("fixture:sse", { detail: { seq, task_id: taskId, type, payload: {} } }));
    }
  });
  // Longer than the task read's coalescing window.
  await page.waitForTimeout(1750);
  expect(reads).toBe(1);
  await page.evaluate(() => {
    for (const seq of [23, 24]) window.dispatchEvent(new CustomEvent("fixture:sse", {
      detail: { seq, task_id: "UI-101", type: "verify.passed", payload: { summary: "已复核焦点" } },
    }));
  });
  await expect(reader).toContainText("检查标题和键盘焦点 · 2");
  await expect(reader.locator(".reader-events")).toContainText("已复核焦点");
  await expect(title).toHaveValue("尚未保存的标题");
  await expect(composer).toHaveValue("尚未发送的追问");
  expect(reads).toBe(2);
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});
