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
    const prior = history.get(p.thread_key) ?? [];
    const base = prior.length;
    const event = (seq: number, type: string, payload: object) => ({
      id: `evt-${p.task_id}-${seq}`, seq, type, task_id: p.task_id, ts: "2026-11-23T08:00:00Z",
      payload: { ...p, ...payload },
    });
    history.set(p.thread_key, [...prior,
      event(base + 1, "user.message", { target: "kanban-agent", runtime_delivery: "headless" }),
      event(base + 2, "kanban.agent.turn.created", { message_event_id: `evt-${p.task_id}-${base + 1}` }),
      event(base + 3, "kanban.agent.reply", { answer: base ? "第二轮答复" : `答复 ${p.task_id}` }),
    ]);
    return route.fulfill({ json: { ok: true, status: "accepted" } });
  });
  await page.goto("/?project=ui-review&page=board&view=list");
  const chat = page.getByRole("region", { name: "Task conversation" });
  for (const task of uiTasks.slice(0, 2)) {
    await page.locator(`button[data-task-id="${task.id}"]`).click();
    const input = chat.getByPlaceholder("问进展、补充背景，或提出任务调整…");
    await expect(chat).not.toContainText("答复 UI-101");
    await expect(chat.getByText("Task conversation", { exact: true })).toBeVisible();
    await input.fill(`进展 ${task.id}`);
    await input.press("Enter");
    await expect(chat.getByText(`答复 ${task.id}`, { exact: true })).toBeVisible();
    if (task.id === "UI-101") {
      await input.fill("补充背景，断点文件来自旧版。");
      await input.press("Enter");
      await expect(chat.getByText("第二轮答复", { exact: true })).toBeVisible();
    }
    await expect(chat.getByTestId("agent-task-unref")).toHaveCount(0);
    await page.goBack();
  }
  await page.locator('button[data-task-id="UI-101"]').click();
  await page.reload();
  await expect(chat.getByText("第二轮答复", { exact: true })).toBeVisible();
  await expect(chat.getByText("答复 UI-101", { exact: true })).toHaveCount(0);
  await expect(chat).not.toContainText("答复 UI-102");
  const expand = page.getByRole("button", { name: "展开对话" });
  await expect(chat.locator(".reader-chat-preview")).toHaveText("zaofu agent第二轮答复");
  await expand.click();
  await expect(chat.getByText("补充背景，断点文件来自旧版。", { exact: true })).toBeVisible();
  await expect(chat.getByText("View child activity")).toHaveCount(0);
  await expect(chat.getByText("你", { exact: true }).first()).toBeVisible();
  await expect(chat.locator(".agent-run-block").filter({ hasText: "第二轮答复" }).getByText("zaofu agent", { exact: true })).toBeVisible();
  await expect(chat.getByText("答复 UI-101", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "收起对话" })).toHaveAttribute("aria-expanded", "true");
  await chat.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("task-conversation.png") });
  await page.getByRole("button", { name: "收起对话" }).click();
  await expect(expand).toHaveAttribute("aria-expanded", "false");
  await expect(chat.getByText("第二轮答复", { exact: true })).toBeVisible();
  expect(sent).toEqual(["UI-101", "UI-101", "UI-102"]);
  expect(errors).toEqual([]);
});

test("narrow task reading shows the blocker once and keeps metadata off the page", async ({ page }, info) => {
  const { writes, errors } = await installUiFixture(page, { locked: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?project=ui-review&page=task&task=UI-104&agent=open");
  const reader = page.getByRole("region", { name: "任务详情" });
  await expect(reader.getByRole("heading", { level: 1 })).toHaveText(uiTasks[3]!.title);
  await expect(reader.locator(".reader-status")).toContainText("Waiting for the owner's acceptance criteria.");
  await expect(reader.getByText("Waiting for the owner's acceptance criteria.", { exact: false })).toHaveCount(1);
  await expect(reader.getByRole("complementary")).toHaveCount(0);
  await expect(reader.getByRole("heading", { name: "交付结果" })).toHaveCount(0);
  await expect(reader.getByText(/执行路线与更多动态|执行现场|^Contract$|^动态$/)).toHaveCount(0);
  const attrs = reader.getByRole("button", { name: "任务属性", exact: true });
  await attrs.click();
  await expect(reader.getByText("优先级", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(attrs).toBeFocused();
  await expect(page.locator(".headless-input")).toHaveCount(1);
  await page.keyboard.press("Control+k");
  await page.getByRole("dialog", { name: "Command Palette" }).getByRole("button", { name: "zaofu" }).click();
  await expect(reader.locator(".headless-input")).toBeFocused();
  await expect(page.getByRole("dialog", { name: "zaofu" })).toHaveCount(0);
  await reader.getByRole("button", { name: "返回看板" }).focus();
  await reader.locator(".reader-article").evaluate(el => el.scrollTop = 0);
  await page.screenshot({ path: info.outputPath("task-mobile.png") });
  await expect(reader.getByTestId("task-evidence-view")).toHaveCount(0);
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

test("a task refresh preserves both drafts and updates only openable delivery links", async ({ page }) => {
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
    return route.fulfill({ json: { task, contract: { ...contract, behavior: `${contract.behavior} · ${reads}` },
      deliverables: reads > 1 ? [{name: "focus.md", sha256: "a".repeat(64)}] : [], events: [] } });
  });
  await page.goto("/?project=ui-review&page=board&task=UI-101");
  const reader = page.getByRole("region", { name: "任务详情" });
  await expect(reader).toContainText("检查标题和键盘焦点 · 1");
  await expect(reader.getByRole("listitem")).toHaveText(["可以返回", "可继续操作"]);
  await expect(reader.getByRole("heading", { name: "交付结果" })).toHaveCount(0);
  await reader.getByLabel("编辑任务", { exact: true }).click();
  const title = reader.getByRole("textbox", { name: "Title", exact: true });
  await title.fill("尚未保存的标题");
  const composer = reader.locator(".headless-input");
  await page.keyboard.press("Escape");
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
  await expect(reader.getByRole("link", { name: "focus.md" })).toHaveAttribute("href", /projects\/ui-review\/tasks\/UI-101\/deliverables\/a{64}$/);
  await reader.getByLabel("编辑任务", { exact: true }).click();
  await expect(title).toHaveValue("尚未保存的标题");
  await expect(composer).toHaveValue("尚未发送的追问");
  expect(reads).toBe(2);
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});

test("result links only expose usable outputs and keep diagnostics off the reading path", async ({ page }) => {
  await installUiFixture(page);
  const task = uiTasks[0];
  const heavy: string[] = [];
  page.on("request", request => {
    if (/\/tasks\/UI-101\/(timeline|diff)\b/.test(request.url())) heavy.push(request.url());
  });
  await page.route("**/tasks/UI-101", route => route.fulfill({ json: { task, events: [],
    contract: { acceptance: "exit_code=0", verification: "npm test",
      acceptance_evidence: { 0: ["evt-internal"] } },
    deliverables: [{ name: "review.md", sha256: "a".repeat(64) }],
    handoff_summary: { evidence_refs: [
      {kind: "accepted_artifact_refs", path: "https://example.com/repo/pull/42"},
      {kind: "stale_artifact_refs", status: "superseded", path: "https://example.com/old"},
    ] },
  } }));
  await page.goto("/?project=ui-review&page=board&task=UI-101");
  const reader = page.getByRole("region", { name: "任务详情" });
  await expect(reader.getByRole("link", { name: "review.md" })).toHaveAttribute("href", /deliverables\/a{64}$/);
  await expect(reader.getByRole("heading", { name: "验收条件" })).toHaveCount(0);
  await reader.getByText("另 1 项").click();
  await expect(reader.getByRole("link", { name: "PR #42" })).toHaveAttribute("href", "https://example.com/repo/pull/42");
  await expect(reader.getByRole("article")).not.toContainText(/evt-internal|npm test|exit_code=0|example.com\/old/);
  await expect(reader.getByText("Verification", { exact: true })).toBeHidden();
  expect(heavy).toEqual([]);
});

for (const mode of ["local_account", "controller_session", "read_only"]) {
  test(`task and board honor the current ${mode} session over a cached token snapshot`, async ({ page }) => {
    const writable = mode !== "read_only";
    const { writes } = await installUiFixture(page, { locked: true, snapshot: { runtime: {
      mode: "live", actions: { mutation_enabled: true, allowed: ["chat-orchestrator"] },
      web_session: { mode: "token_required", actions_enabled: false },
    } } });
    await page.route("**/api/web-session", route => route.fulfill({ json: {
      mode, actions_enabled: writable, unlocked: true, username: "zaofu",
      local_account: mode === "local_account", controller_managed: mode !== "local_account",
    } }));
    await page.goto("/?project=ui-review&page=board&task=UI-101");
    const panel = page.getByRole("region", { name: "Task conversation" });
    const input = panel.locator("textarea.headless-input");
    await expect(input).toBeVisible();
    await expect(page.getByPlaceholder("action token", { exact: true })).toHaveCount(0);
    await input.fill("检查当前任务");
    const send = panel.getByRole("button", { name: "Send", exact: true });
    if (writable) await expect(send).toBeEnabled();
    else {
      await expect(send).toBeDisabled();
      await input.press("Enter");
      await expect(panel.getByRole("alert")).toContainText("当前会话仅可阅读对话");
      expect(writes).toEqual([]);
    }
    await expect(send).not.toHaveAttribute("title", /token/i);
    const footer = page.locator(".rail-account");
    await footer.getByLabel("zaofu", {exact: true}).click();
    await expect(footer.getByRole("button", {name: /Sign out|退出登录/})).toBeVisible();
    await expect(footer.getByRole("link", {name: /Password|密码/})).toHaveCount(mode === "local_account" ? 1 : 0);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "返回看板" }).click();
    await expect(page.locator(".board-access")).toHaveCount(0);
  });
}

test("compact metadata works at each viewport without surfacing runtime IDs", async ({ page }, info) => {
  const task = { ...uiTasks[0]!, blocked_by: ["TASK-DATASET"], fanout: { workflow_run_id: "workflow-run-" + "abcdef".repeat(12) },
    links: { trace: "task:trace-" + "12345".repeat(12), candidate: "PDD-0123456789" } };
  await installUiFixture(page, { snapshot: { tasks: [task] } });
  await page.route("**/tasks/UI-101", route => route.fulfill({ json: {
    task, contract: { acceptance: "返回时恢复筛选", acceptance_criteria: ["返回时恢复筛选"] }, events: [],
  } }));
  await page.goto("/?project=ui-review&page=board&task=UI-101");
  const reader = page.getByRole("region", { name: "任务详情" });
  for (const width of [1440, 960, 390]) {
    await page.setViewportSize({ width, height: 900 });
    const properties = reader.getByRole(width >= 1200 ? "complementary" : "dialog", { name: "任务属性" });
    if (width < 1200) {
      await expect(properties).toBeHidden();
      await reader.getByRole("button", { name: "任务属性", exact: true }).click();
    }
    await expect(properties.getByRole("link", { name: "TASK-DATASET" })).toBeInViewport({ ratio: 1 });
    await expect(reader).not.toContainText("workflow-run-");
    await expect(reader).not.toContainText("PDD-0123456789");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    if (width < 1200) await page.keyboard.press("Escape");
    await expect(page.getByText("返回时恢复筛选", { exact: true })).toHaveCount(1);
    await page.screenshot({ path: info.outputPath(`layout-${width}.png`) });
  }
});

for (const width of [1440, 960, 390]) {
  test(`long task keeps the empty composer in view at ${width}px`, async ({ page }, info) => {
    await installUiFixture(page);
    const task = uiTasks[0];
    await page.route("**/tasks/UI-101", route => route.fulfill({ json: {
      task, contract: { behavior: Array.from({ length: 68 }, (_, i) => `第 ${i + 1} 项交付说明`).join("\n\n") }, events: [],
    } }));
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/?project=ui-review&page=board&task=UI-101");
    const reader = page.getByRole("region", { name: "任务详情" });
    const article = reader.locator(".reader-article");
    const chat = reader.getByRole("region", { name: "Task conversation" });
    const input = chat.getByRole("textbox", { name: "任务消息" });
    await expect(article).toContainText("第 68 项交付说明");
    await expect(input).toBeInViewport({ ratio: 1 });
    await expect(chat.getByRole("button", { name: "Send", exact: true })).toHaveText("Send");
    await expect(input).not.toBeFocused();
    await expect(chat.getByText("还没有任务对话")).toHaveCount(0);
    await expect(chat.getByTestId("agent-task-ref")).toHaveCount(0);
    expect((await chat.boundingBox())!.height).toBeLessThan(108);
    const before = (await input.boundingBox())!;
    await expect(reader.getByRole("complementary")).toHaveCount(width >= 1200 ? 1 : 0);
    expect(await article.evaluate(el => el.scrollTop)).toBe(0);
    await page.screenshot({ path: info.outputPath(`composer-${width}.png`) });
    await article.evaluate(el => { el.scrollTop = el.scrollHeight; });
    expect(await article.evaluate(el => el.scrollTop)).toBeGreaterThan(300);
    expect(Math.abs((await input.boundingBox())!.y - before.y)).toBeLessThan(1);
    await input.fill("留在正文底部的追问");
    await expect(input).toBeFocused();
  });
}

test("execution progress distinguishes automatic stages, live workers and human evidence gates", async ({ page }) => {
  const task = { ...uiTasks[0], status: "backlog", assigned_to: "obsolete-worker", blocked_by: [] as string[] };
  const { writes } = await installUiFixture(page, { snapshot: { tasks: [task] } });
  let pipeline: Record<string, unknown> | null = null;
  await page.route("**/tasks/UI-101", route => route.fulfill({ json: {
    task, contract: { evidence_contract: { required_manual_evidence: ["reports/test-data.json"] } },
    handoff_summary: { current_state: task.status, pipeline, completed: pipeline ? [
      { kind: "admitted_result", stage: "impl", semantic_verdict: "passed" },
    ] : [] },
    task_run_panel: { active_operation: pipeline
      ? { active: true, state: "in_progress", dispatch_id: "expired", instance_id: "obsolete-worker" } : null },
    events: [],
  } }));
  const url = "/?project=ui-review&page=board&task=UI-101";
  const progress = page.locator(".reader-status");
  await page.goto(url);
  await expect(progress).toContainText("未进入流程");
  await page.getByLabel("编辑任务", { exact: true }).click();
  await expect(page.getByLabel("Title", { exact: true })).toBeVisible();
  await expect(page.locator(".task-reader").getByText(/Propose Assignment|Assignment Intent|dispatch-task|request-verify|request-review|ship-candidate|Assignee/)).toHaveCount(0);
  const base = { status: "current", pipeline_stage: "verify_ready", current_stage: "verify", owner: { instance_id: "verify-2" } };
  for (const [fields, label, worker] of [
    [{}, "等待验证", false],
    [{ operation_status: "reserved", operation_id: "op1" }, "等待验证", false],
    [{ pipeline_stage: "verify_active", operation_status: "running", operation_id: "op1", dispatch_id: "d1" }, "验证中", true],
    [{ pipeline_stage: "verify_active", operation_status: "running", operation_id: "op1" }, "验证中", false],
    [{ pipeline_stage: "verify_active", operation_status: "running", dispatch_id: "d1", owner: { role: "verify" } }, "验证中", false],
    [{ pipeline_stage: "verify_active", operation_status: "suspended", operation_id: "op1" }, "验证已暂停", false],
    [{ pipeline_stage: "dependency_blocked" }, "等待依赖", false],
    [{ status: "unavailable" }, "执行状态待确认", false],
  ] as const) {
    pipeline = { ...base, ...fields };
    await page.reload();
    await expect(progress).toContainText(label);
    await expect(progress).not.toContainText("实现已通过");
    const properties = page.getByRole("complementary", { name: "任务属性" });
    if (worker) await expect(properties).toContainText("verify-2");
    else await expect(properties).not.toContainText("verify-2");
    await expect(progress).not.toContainText("verify-2");
    await expect(progress).not.toContainText("obsolete-worker");
    await expect(page.getByRole("link", { name: "查看运行处置请求" })).toHaveCount(0);
  }
  task.blocked_by = ["UI-102", "UI-103"];
  pipeline = { status: "current", pipeline_stage: "dependency_blocked", blockers: ["UI-102"] };
  await page.reload();
  await expect(progress.getByRole("button", { name: "UI-102" })).toBeVisible();
  await expect(progress).not.toContainText("UI-103");
  pipeline = { status: "current", pipeline_stage: "external_gate_waiting" };
  await page.reload();
  await expect(progress).toContainText("待你补充证据");
  await expect(page.locator(".reader-status")).toContainText("reports/test-data.json");
  const resolve = page.getByRole("link", { name: "查看运行处置请求" });
  await resolve.click();
  await expect(page).toHaveURL(/obs_tab=operations/);
  expect(writes).toEqual([]);
});

test("legacy task access unfolds only on request and preserves the draft", async ({ page }) => {
  const { writes } = await installUiFixture(page, { locked: true });
  await page.goto("/?project=ui-review&page=board&task=UI-101");
  const chat = page.getByRole("region", { name: "Task conversation" });
  const input = chat.getByRole("textbox", { name: "任务消息" });
  const token = chat.getByPlaceholder("action token", { exact: true });
  await input.fill("保留草稿");
  await expect(token).toHaveCount(0);
  await expect(chat.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
  await chat.getByRole("button", { name: "解锁发送" }).click();
  await expect(token).toBeVisible();
  await chat.getByRole("button", { name: "收起解锁" }).click();
  await expect(input).toHaveValue("保留草稿");
  expect(writes).toEqual([]);
});

for (const receipt of ["completed", "accepted", "wrong-task", "failed"]) {
  test(`a proposed task edit previews the change and reports its ${receipt} receipt`, async ({ page }, info) => {
    const { writes } = await installUiFixture(page, { snapshot: { runtime: {
      mode: "live", actions: { mutation_enabled: true, allowed: ["chat-orchestrator", "update-task"] },
    } } });
    const payload = { task_id: "UI-101", contract: { acceptance_criteria: ["恢复上一次筛选", { text: "兼容旧版断点" }] } };
    let applied = false;
    const updates: object[] = [];
    await page.route("**/actions/update-task", route => {
      updates.push(route.request().postDataJSON().payload);
      applied = receipt === "completed";
      return route.fulfill({ json: { action: "update-task", status: receipt === "wrong-task" ? "completed" : receipt,
        ok: receipt !== "failed", task_id: receipt === "wrong-task" ? "UI-102" : "UI-101",
        event_id: "evt-update", reason: receipt === "failed" ? "任务版本已变更" : "" } });
    });
    await page.route("**/kanban-agent/pending-proposals", route => route.fulfill({ json: { items: applied ? [] : [{
      thread_key: "task:UI-101", proposal_event_id: "evt-acceptance", action: "update-task",
      title: "补充验收条件", valid: true, payload,
    }] } }));
    await page.goto("/?project=ui-review&page=board&task=UI-101");
    const chat = page.getByRole("region", { name: "Task conversation" });
    await expect(chat).toContainText("待确认");
    await expect(chat).toContainText("恢复上一次筛选");
    await expect(chat).toContainText("兼容旧版断点");
    await expect(chat).not.toContainText("验收条件已更新");
    expect(updates).toEqual([]);
    expect(writes).toEqual([]);
    await chat.getByRole("button", { name: "确认更新", exact: true }).click();
    const expected = receipt === "completed" ? "验收条件已更新" : receipt === "failed" ? "任务版本已变更" : "等待任务更新回执";
    await expect(chat.getByRole(receipt === "failed" ? "alert" : "status").filter({ hasText: expected })).toBeVisible();
    if (receipt !== "completed") await expect(chat).not.toContainText("验收条件已更新");
    expect(updates).toHaveLength(1);
    expect(updates[0]).toMatchObject({ ...payload, proposal_event_id: "evt-acceptance", thread_id: "task:UI-101" });
    if (receipt === "completed") await page.screenshot({ path: info.outputPath("task-update-receipt.png") });
  });
}

test("task properties remember the desktop choice and open related work within the project", async ({ page }) => {
  const { writes } = await installUiFixture(page);
  const [task, related] = uiTasks;
  await page.route("**/tasks/UI-101", route => route.fulfill({ json: {
    task, contract: { source_title: "训练工作台" }, events: [],
    properties: { source: { kind: "task", id: related.id, title: related.title }, upstream: [],
      downstream: [{ id: related.id, title: related.title, status: "backlog", waiting: true }],
      children: [{ id: related.id, title: related.title, status: "done" }],
      milestone: { label: "进入阻塞", at: new Date(Date.now() - 35 * 60000).toISOString() } },
  } }));
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/?project=ui-review&page=task&task=UI-101");
  const sidebar = page.getByRole("complementary", { name: "任务属性" });
  await expect(sidebar).toContainText("35 分钟前");
  await expect(sidebar.getByRole("heading", { name: "下游任务" })).toBeVisible();
  await expect(sidebar).toContainText("等待本任务");
  await expect(sidebar.getByText("1 / 1 已完成")).toBeVisible();
  await sidebar.getByRole("button", { name: "收起任务属性" }).click();
  await page.reload();
  await expect(sidebar).toHaveCount(0);
  const toggle = page.getByRole("button", { name: "任务属性", exact: true });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await page.setViewportSize({ width: 960, height: 900 });
  await toggle.click();
  const drawer = page.getByRole("dialog", { name: "任务属性" });
  await expect(drawer).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(toggle).toBeFocused();
  await page.setViewportSize({ width: 1440, height: 960 });
  await expect(sidebar).toHaveCount(0);
  await toggle.click();
  await sidebar.getByRole("link", { name: related.title }).first().click();
  await expect(page.getByRole("region", { name: "任务详情" }).getByRole("heading", { level: 1 })).toHaveText(related.title);
  await expect(page).toHaveURL(/task=UI-102/);
  expect(writes).toEqual([]);
});
