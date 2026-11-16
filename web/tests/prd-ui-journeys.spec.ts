import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";

import { prdOnlyOption, sameInvocation, terminalPlanningEvent } from "./support/prdJourneyPolicy";

const journey = process.env.ZF_UI_JOURNEY;
const ownerCheckpointRequired = process.env.ZF_UI_OWNER_CHECKPOINT === "true";
const timeout = Number(process.env.ZF_UI_TIMEOUT_MS || 1_200_000);
const turnTimeout = Number(process.env.ZF_UI_TURN_TIMEOUT_MS || (process.env.ZF_UI_PROVIDER === "fake" ? 45_000 : 600_000));
const evidence = "/zf-run/evidence";
const productInput = existsSync(`${evidence}/product-input.md`) ? readFileSync(`${evidence}/product-input.md`, "utf8") : "";
const productRequest = (stage: string) => `${stage}：\n<原始产品需求>\n${productInput}\n</原始产品需求>\n当前阶段约束：只讨论并保存 PRD 到 docs/prd/ui-requirement.md。原始产品需求中的完整 Plan Preview 是后续由 UI 启动 PRD v5 Workflow 生成的终点，不是当前聊天写 Plan 的指令；当前不要生成手工 Plan Preview 或把自己的文件分配方案提升为 PRD 要求。产品行为及原有交付时点保持不变。只操作当前隔离项目，讨论和预览不实施；之后经 Owner 单独审批允许标准受控 Git 交付、实现及独立验证。中文表达，机器键和命令不翻译。角色调用及审批历史由外部测试协议核验，不添加为产品 AC。`;

// The caller's budget covers all discussion phases and Playwright teardown.
test.describe.configure({ timeout });
type Event = { id: string; seq: number; type: string; task_id?: string; correlation_id?: string; payload?: Record<string, any> };

async function get(request: APIRequestContext, path: string) {
  const response = await request.get(path);
  if (!response.ok()) throw new Error(`${path}: ${await response.text()}`);
  return response.json();
}
// The ledger is append-only. Incremental reads keep long real-provider traces bounded.
const eventCache = new Map<string, Event[]>();
async function events(request: APIRequestContext, project: string): Promise<Event[]> {
  const all = eventCache.get(project) || [];
  eventCache.set(project, all);
  let cursor = all.length ? all[all.length - 1].seq : 0;
  for (;;) {
    const body = await get(request, `/api/projects/${project}/events?cursor=${cursor}&limit=500`);
    const rows: Event[] = body.items || [];
    const next = Math.max(cursor, ...rows.map(row => row.seq));
    all.push(...rows.filter(row => row.seq > cursor));
    if (!rows.length || next === cursor || rows.length < 500) return all;
    cursor = next;
  }
}
async function waitEvent(request: APIRequestContext, project: string, type: string, duration = turnTimeout) {
  let found: Event | undefined;
  await expect.poll(async () => {
    const rows = await events(request, project);
    if (type === "plan.approval.requested") {
      const rejected = rows.find(row => ["workflow.invoke.rejected", "run.goal.blocked", "runtime.start.failed"].includes(row.type));
      if (rejected) throw new Error(`Workflow admission rejected: ${JSON.stringify(rejected.payload)}`);
    }
    found = rows.find(row => row.type === type);
    return Boolean(found);
  }, { timeout: duration, intervals: [300, 1000, 2000] }).toBeTruthy();
  return found!;
}
async function capture(page: Page, name: string) {
  await page.screenshot({ path: `${evidence}/${name}.png`, fullPage: true });
  writeFileSync(`${evidence}/checkpoint.json`, JSON.stringify({ journey, stage: name, url: page.url() }, null, 2));
}
async function send(page: Page, text: string) {
  const dialog = page.getByRole("dialog", { name: "Kanban Agent", exact: true });
  await expect(dialog.or(page.getByRole("button", { name: "Open Kanban Agent" })).first()).toBeVisible();
  if (!await dialog.isVisible()) await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const input = dialog.locator("textarea.headless-input");
  await expect(input).toBeEnabled({ timeout: turnTimeout });
  await input.fill(text);
  const submit = dialog.getByRole("button", { name: "Send message", exact: true });
  await expect(submit).toBeEnabled({ timeout: turnTimeout });
  await submit.click();
}
async function answerRequirementQuestions(page: Page, request: APIRequestContext, project: string, waitingForInput: boolean) {
  const card = page.getByTestId("agent-card-plan").last();
  const question = card.getByTestId("ask-user-question");
  if (!waitingForInput) return;
  // The completion event can arrive before React renders the question card.
  await expect(question).toBeVisible({ timeout: turnTimeout });
  for (let index = 0; index < 6; index++) {
    const submit = question.getByTestId("ask-user-submit");
    const next = (await submit.innerText()).includes("Next question");
    // The frozen product scenario explicitly ends this discussion at the PRD.
    // A custom answer regenerates options and requires a second confirmation.
    const options = await question.locator('input[data-option-id]').evaluateAll(inputs => inputs.map(input => ({
      id: input.getAttribute("data-option-id") || "",
      label: input.getAttribute("aria-label") || "",
      action: input.getAttribute("data-submit-action") || "",
    })));
    const selected = prdOnlyOption(options);
    if (selected) {
      writeFileSync(`${evidence}/prd-endpoint-selection.json`, JSON.stringify({ selected, options }, null, 2));
      await question.getByTestId(`ask-user-option-${selected}`).check();
      await submit.click();
      if (!next) return;
      continue;
    }
    const previousSeq = (await events(request, project)).at(-1)?.seq || 0;
    const other = question.getByTestId("ask-user-option-other");
    if (await other.isVisible()) await other.check();
    await question.locator("textarea").fill(productInput ? productRequest("UI_PRD_CONFIRMED，确认以下需求，请依此回答当前所有问题") : "UI_PRD_CONFIRMED：README.md 缺失时报告路径并明确报错、非零退出、停止本次读取；存在时必须可读。使用系统现有 cat README.md 检查，不新增读取程序。当前 Kanban 讨论终点仅为 PRD，完整 Plan 留给后续 UI 启动的 PRD v5 Workflow；不要在聊天内生成 Plan。仅当前临时项目。");
    await submit.click();
    if (!next) {
      let completed: Event | undefined;
      await expect.poll(async () => {
        completed = (await events(request, project)).find(row => row.seq > previousSeq && row.type === "kanban.agent.turn.completed");
        return Boolean(completed);
      }, { timeout: turnTimeout, intervals: [300, 1000, 2000] }).toBeTruthy();
      if (completed!.payload?.status !== "waiting_input") return;
      await expect(question.getByTestId("ask-user-submit")).toBeEnabled({ timeout: turnTimeout });
    }
  }
  throw new Error("Unexpected extra requirement questions");
}

async function approveTask(page: Page) {
  const card = page.locator('[data-testid="agent-card-approve"][data-proposal-action="create-task"]').last();
  await expect(card).toBeVisible({ timeout: turnTimeout });
  await capture(page, "03-task-proposal");
  await card.getByTestId("agent-proposal-approve").click();
}

test(`UI ${journey}: discussion → PRD → v5 workflow → generated Plan preview`, async ({ page, request }) => {
  page.setDefaultTimeout(20_000);
  expect(["kanban", "channel"]).toContain(journey);
  const snapshot = await get(request, "/api/snapshot");
  const project = snapshot.project.project_id;
  await page.addInitScript(token => {
    localStorage.setItem("zf.webActionToken", token);
    localStorage.setItem("zf.operatorBackend", "codex-headless");
  }, process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST || "");
  await page.goto(`/?project=${project}`);
  await expect(page.getByRole("dialog", { name: "Kanban Agent", exact: true }).or(page.getByRole("button", { name: "Open Kanban Agent" })).first()).toBeVisible();
  if (!await page.getByRole("dialog", { name: "Kanban Agent", exact: true }).isVisible())
    await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  if (journey === "kanban") {
    await send(page, productInput ? productRequest("UI_PRD_DISCUSS，当前 Kanban 讨论，不创建 Channel，先确认本轮终点，暂不创建 Task") : "UI_PRD_DISCUSS：请在当前 Kanban 会话讨论需求，不创建 Channel。需求：保持 README.md 可读，缺失文件时明确报告错误。验证入口固定为系统已有 cat README.md，不新增读取程序。先问我确认失败行为和本轮终点，暂不创建 Task。仅操作当前临时项目。");
    const discussionTurn = await waitEvent(request, project, "kanban.agent.turn.completed");
    const discussionReply = await waitEvent(request, project, "kanban.agent.reply");
    expect(["completed", "waiting_input"]).toContain(discussionTurn.payload?.status);
    expect(discussionReply.payload?.turn_id).toBe(discussionTurn.payload?.turn_id);
    expect(String(discussionReply.payload?.answer || "").trim()).not.toBe("");
    await capture(page, "01-kanban-discussion");
    await answerRequirementQuestions(page, request, project, discussionTurn.payload?.status === "waiting_input");
    await send(page, productInput ? productRequest("UI_PRD_ANSWER，确认，直接写入完整 PRD，完成后回复 UI_PRD_WRITTEN") : "UI_PRD_ANSWER：确认缺失文件必须明确报错，采用现有 cat README.md 验证，不新增读取程序。当前只编写 PRD；后续完整 Plan 必须由 UI 启动 PRD v5 Workflow 生成，实施等待 Owner 审批。请现在直接写入完整 PRD 到 docs/prd/ui-requirement.md，包含场景、验收和失败语义。当前 PRD 编写和 Plan 预览阶段不要修改 zf.yaml、Git 或运行状态；" + (ownerCheckpointRequired ? "请在 PRD 登记阻塞决定 OWNER-GIT-01：未来实施是否授权标准受控 Git 提交，目前尚未批准。要求 Planner/Critic 在候选计划定稿前通过 Owner 决策检查点确认它，不能自行假定或把它移到实施后才问。现在先完整记录此未决项并写 PRD，不在当前聊天追问该项。" : "这个阶段限制不禁止未来在单独 Plan 审批后采用标准受控 Git 交付流程。") + "完成后回复 UI_PRD_WRITTEN。");
    await expect(page.locator(".agent-text-part").last()).toContainText("UI_PRD_WRITTEN", { timeout: turnTimeout });
    const prd = readFileSync("/zf-run/project/docs/prd/ui-requirement.md", "utf8");
    if (!productInput) expect(prd).toContain("README.md");
    expect(prd.length).toBeGreaterThan(500);
    writeFileSync(`${evidence}/source-prd.md`, prd);
    writeFileSync(`${evidence}/source-prd.sha256`, createHash("sha256").update(prd).digest("hex"));
    await page.reload();
    await capture(page, "02-kanban-prd-generated");
    expect((await events(request, project)).filter(row => row.type === "channel.created")).toHaveLength(0);
    await send(page, "UI_PRD_CREATE：确认 docs/prd/ui-requirement.md。请创建一个 Task 并 prepare_workflow，采用当前 PRD v5 配置生成 Workflow YAML Preview。必须将“完整 Plan 需 Owner 人工审批后才允许实施”保留到 Task/Requirement 的 constraints，Workflow 设置 plan_approval: true。只准备提案，不启动实施；保留 source_ref、验收和 target_root 当前项目。");
    await approveTask(page);
  } else {
    await send(page, productInput ? "UI_CHANNEL_SETUP：请创建 PRD Group，产品、架构、critic、synthesizer 参与，ID ui-prd-group，名称 UI PRD Group。真实运行使用已登录的 Codex 后端，禁止 fake/mock。先创建 conversation 模式的需求讨论群，具体需求随后在群内提交，不自动创建 Task。" : "UI_CHANNEL_SETUP：请创建 PRD Group，产品、架构、critic、synthesizer 参与，ID ui-prd-group，名称 UI PRD Group。真实运行使用已登录的 Codex 后端，禁止 fake/mock。先用 conversation 模式讨论 README.md 保持可读与缺失报错，不自动创建 Task。");
    const setup = page.getByTestId("agent-card-plan").last();
    await expect(setup).toBeVisible({ timeout: turnTimeout });
    await setup.locator('input[data-recommended="true"]').check();
    await setup.getByTestId("ask-user-submit").click();
    const created = await waitEvent(request, project, "channel.created");
    const channelId = String(created.payload?.channel_id);
    const threadId = String(created.payload?.thread_id || "main");
    await page.getByRole("button", { name: "Minimize Kanban Agent" }).click();
    // Pin the created thread before typing; late detail hydration can otherwise
    // switch the default thread and restore a different (empty) composer draft.
    await page.goto(`/?project=${project}&page=channels&channel=${encodeURIComponent(channelId)}&thread=${encodeURIComponent(threadId)}`);
    const input = page.getByRole("textbox", { name: "Message UI PRD Group" });
    await input.fill(productInput ? productRequest("请讨论并形成完整 PRD，后续 UI 启动 PRD v5 Workflow 生成 Plan") : "请讨论：使用项目根目录的系统 cat README.md 验证，保持文件可读；文件缺失时以原生 stderr 和非零退出明确失败，不新增读取程序。当前只形成完整 PRD，后续由 UI 启动 PRD v5 Workflow 生成完整 Plan，实施等待审批。PRD 保留完整结构和验收，按这个小需求的规模简洁表达，不引入额外业务系统。");
    await page.getByRole("button", { name: "Send message", exact: true }).click();
    await expect(input).toBeEmpty();
    await capture(page, "01-channel-discussion");
    await page.getByTitle("More", { exact: true }).click();
    await page.getByRole("combobox", { name: "Discussion mode" }).selectOption("multi_lens");
    const start = page.getByRole("button", { name: "Start discussion", exact: true });
    await expect(start).toBeEnabled({ timeout: turnTimeout });
    await start.click();
    await capture(page, "02-channel-multi-lens-started");
    await waitEvent(request, project, "channel.synthesis.proposed", timeout);
    await page.getByTitle("Close drawer", { exact: true }).click();
    await page.getByTestId("channel-prd-handoff").getByRole("button", { name: "Preview", exact: true }).click();
    const confirm = page.locator(".channel-consensus-control").getByRole("button", { name: "Confirm", exact: true });
    await expect(confirm).toBeEnabled({ timeout: turnTimeout });
    await confirm.click();
    const confirmed = await waitEvent(request, project, "channel.consensus.reached");
    writeFileSync(`${evidence}/channel-confirmation.json`, JSON.stringify(confirmed, null, 2));
    await capture(page, "02-channel-prd-confirmed");
    await page.getByText("Planning options", { exact: true }).click();
    await page.getByTestId("channel-workflow-objective").fill("UI_CHANNEL_CREATE create a Task：从当前确认的中文 PRD 创建 Task，准备 PRD v5 Workflow YAML，生成中文 Plan 后等待 Owner 人工审批；该要求必须进入 Requirement constraints 并配置 workflow.plan_approval: true。");
    await page.getByTestId("channel-create-task-from-prd").click();
    const plan = page.getByTestId("agent-card-plan").last();
    await expect(plan).toBeVisible({ timeout: turnTimeout });
    const create = plan.locator('input[data-submit-action="create-task"][data-recommended="true"]');
    await expect(create).toBeVisible({ timeout: turnTimeout });
    await create.check();
    await plan.getByTestId("ask-user-submit").click();
    await approveTask(page);
  }
  const task = await waitEvent(request, project, "task.created");
  await capture(page, "04-task-created");
  // Open the prepared request from the actual Workflow navigation.
  const minimize = page.getByRole("button", { name: "Minimize Kanban Agent" });
  if (await minimize.isVisible()) await minimize.click();
  await page.getByRole("button", { name: "Workflows", exact: true }).click();
  await expect(page.getByTestId("workflow-proposal-page")).toBeVisible({ timeout: turnTimeout });
  await page.getByRole("button", { name: "Raw", exact: true }).click();
  const raw = page.getByRole("complementary", { name: "Raw YAML", exact: true });
  // Consult the current request, not historical failure events from a recovered attempt.
  // A terminal preparation failure must not spend another provider timeout waiting for YAML.
  const preparationDeadline = Date.now() + turnTimeout;
  while (!((await raw.innerText()).includes("5.0.1")) && Date.now() < preparationDeadline) {
    const requests = await get(request, `/api/projects/${project}/workflow-requests`);
    const prepared = requests.items.find((item: Record<string, any>) => item.task_id === task.task_id);
    if (prepared && ["preparation_failed", "preparation_blocked"].includes(prepared.status)) {
      const detail = await get(request, `/api/projects/${project}/workflow-requests/${encodeURIComponent(prepared.request_id)}`);
      writeFileSync(`${evidence}/preparation-failure.json`, JSON.stringify(detail, null, 2));
      await capture(page, "05-workflow-preparation-failed");
      throw new Error(`Workflow preparation ${prepared.status}: ${JSON.stringify(detail.preparation)}`);
    }
    await page.waitForTimeout(1000);
  }
  await expect(raw).toContainText("5.0.1");
  await expect(raw).toContainText("plan_approval: true");
  await capture(page, "05-v5-yaml-preview");
  if (process.env.ZF_UI_WORKFLOW_RUNTIME === "cold") {
    writeFileSync(`${evidence}/runtime-handoff-request.json`, JSON.stringify({ project, journey }));
    await expect.poll(() => existsSync(`${evidence}/runtime-handoff.json`), { timeout: 120_000 }).toBeTruthy();
  }
  await page.getByTestId("workflow-start").click();
  const invoked = await waitEvent(request, project, "workflow.invoke.requested");
  await capture(page, "05-workflow-start-requested");
  let answeredOwner: Event | undefined;
  let approval: Event | undefined;
  const end = Date.now() + timeout;
  while (!approval && Date.now() < end) {
    const rows = (await events(request, project)).filter(row => sameInvocation(row, invoked));
    const owner = rows.find(e => e.type === "approval.requested" && e.payload?.owner_route === "plan_synth_owner_decision");
    if (owner && !answeredOwner) {
      const ownerPlan = String(owner.payload?.plan_id || owner.payload?.source_event_id || "");
      await page.goto(`/?project=${project}&page=inbox&plan=${encodeURIComponent(ownerPlan)}`);
      const preview = page.getByRole("dialog", { name: "Plan preview" });
      await expect(preview.getByText("候选计划待澄清，尚未通过 Critic", { exact: true })).toBeVisible();
      const candidate = await get(request, `/api/projects/${project}/plans/${encodeURIComponent(ownerPlan)}/preview`);
      if (!productInput) expect(candidate.markdown).toContain("README.md");
      expect(candidate.content_kind).not.toBe("unavailable");
      expect(candidate.actions.approve).toBe("replan-approve");
      // Full content describes document completeness, not Critic approval.
      expect(rows.filter(e => e.type === "plan.approval.requested")).toHaveLength(0);
      expect(rows.filter(e => e.type === "plan.approved")).toHaveLength(0);
      writeFileSync(`${evidence}/owner-candidate.json`, JSON.stringify(candidate, null, 2));
      const questions = owner.payload?.owner_decision_items;
      if (!ownerCheckpointRequired || !Array.isArray(questions) || questions.length !== 1 ||
          questions[0].decision_id !== "OWNER-GIT-01") {
        writeFileSync(`${evidence}/owner-checkpoint.json`, JSON.stringify(owner, null, 2));
        await capture(page, "06-owner-decision-required");
        throw new Error("OWNER_DECISION_REQUIRED: retained candidate; no scripted answer authorized for these questions");
      }
      await preview.getByRole("textbox", { name: "Owner 答复内容" }).fill("OWNER-GIT-01：当前仅 PRD 和 Plan 预览，禁止 Git 写操作和实施。本次仅授权依据此答复重新规划；未来必须先单独批准新版完整 Plan，之后才允许标准受控 Git 提交交付。不删除验收，不提前启动实施，也不要把阶段限制理解为永久禁止交付。");
      await capture(page, "06-owner-answer-before-submit");
      await preview.getByRole("button", { name: "提交答复并重新规划", exact: true }).click();
      answeredOwner = owner;
      writeFileSync(`${evidence}/owner-checkpoint.json`, JSON.stringify(owner, null, 2));
    }
    approval = rows.find(e => e.type === "plan.approval.requested");
    const rejection = terminalPlanningEvent(rows, invoked);
    if (rejection) {
      writeFileSync(`${evidence}/terminal-planning-event.json`, JSON.stringify(rejection, null, 2));
      await capture(page, "06-planning-terminal");
      throw new Error(`PLANNING_TERMINAL: ${JSON.stringify(rejection)}`);
    }
    if (!approval) await page.waitForTimeout(2000);
  }
  expect(approval, "generated Plan approval").toBeTruthy();
  if (ownerCheckpointRequired) expect(answeredOwner, "required real Critic Owner checkpoint").toBeTruthy();
  const planId = String(approval!.payload?.plan_id || "");
  expect(planId).not.toBe("");
  await page.goto(`/?project=${project}&page=inbox&plan=${encodeURIComponent(planId)}`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await expect(dialog).toBeVisible();
  const planBody = await get(request, `/api/projects/${project}/plans/${encodeURIComponent(planId)}/preview`);
  expect(planBody.content_profile).toBe("derived_plan_full");
  if (!productInput) expect(planBody.markdown).toContain("README.md");
  // Heading labels vary across valid Plan methods. The artifact audit verifies
  // exact generated content; Critic and the separate assessment judge its quality.
  await dialog.getByRole("button", { name: "完整原文", exact: true }).click();
  await dialog.getByRole("button", { name: "查看源码", exact: true }).click();
  await expect(dialog.locator(".plan-document-source")).toHaveText(planBody.markdown);
  const downloadReady = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "下载 Markdown", exact: true }).click();
  const download = await downloadReady;
  const downloaded = `${evidence}/generated-plan.md`;
  await download.saveAs(downloaded);
  expect(readFileSync(downloaded, "utf8")).toBe(planBody.markdown);
  await dialog.getByRole("button", { name: "全文连续阅读", exact: true }).click();
  const chapters = dialog.getByRole("combobox", { name: "计划章节", exact: true });
  if (await chapters.isVisible()) {
    const count = await chapters.locator("option").count();
    await chapters.selectOption(String(count - 1));
    await expect(dialog.getByRole("button", { name: "下一页", exact: true })).toBeDisabled();
    await expect(dialog.locator(".plan-document-source")).not.toBeEmpty();
  }
  await capture(page, "06-generated-plan-preview");
  await page.reload();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "完整原文", exact: true }).click();
  await dialog.getByRole("button", { name: "查看源码", exact: true }).click();
  await expect(dialog.locator(".plan-document-source")).toHaveText(planBody.markdown);
  await capture(page, "07-refreshed-plan-preview");
  const finalEvents = await events(request, project);
  expect(finalEvents.filter(row => row.type === "workflow.invoke.requested")).toHaveLength(1);
  expect(finalEvents.filter(row => row.type === "plan.approved")).toHaveLength(0);
  if (process.env.ZF_UI_WORKFLOW_RUNTIME === "cold") {
    expect(finalEvents.filter(row => row.type === "runtime.start.requested")).toHaveLength(1);
  }
  writeFileSync(`${evidence}/lineage.json`, JSON.stringify({ task, invoked, approval, planBody }, null, 2));
  writeFileSync(`${evidence}/events.json`, JSON.stringify(finalEvents, null, 2));
  if (process.env.ZF_UI_PROVIDER === "real") {
    const reading = [];
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      for (const [tab, key] of [["审批概览", "overview"], ["模块规格", "module"]]) {
        await dialog.getByRole("button", { name: tab, exact: true }).click();
        const pane = dialog.getByRole("region", { name: "计划审阅内容" });
        await expect(pane).toBeVisible();
        await capture(page, `08-${width}-${key}`);
        reading.push({ width, tab, text: await pane.innerText(), fits: await pane.evaluate(el => el.scrollWidth <= el.clientWidth + 1) });
      }
    }
    writeFileSync(`${evidence}/reading-observations.json`, JSON.stringify(reading, null, 2));
    for (const artifact of planBody.artifacts || []) {
      if (!["task_map", "execution_spec", "source_index", "coverage_ledger", "test_matrix", "acceptance_matrix"].includes(artifact.name)) continue;
      const response = await request.get(`/api/projects/${project}/plans/${encodeURIComponent(planId)}/artifacts/${artifact.name}`);
      expect(response.ok()).toBeTruthy();
      const bytes = await response.body();
      expect(createHash("sha256").update(bytes).digest("hex")).toBe(artifact.sha256.replace(/^sha256:/, ""));
      writeFileSync(`${evidence}/${artifact.name}.json`, bytes);
    }
  }
  if (process.env.ZF_UI_DELIVERY === "true") {
    const binding = { plan_id: planId, sha256: createHash("sha256").update(planBody.markdown).digest("hex") };
    writeFileSync(`${evidence}/preview-ready.json`, JSON.stringify(binding));
    await expect.poll(() => {
      const path = `${evidence}/execute-approved.json`;
      if (!existsSync(path)) return false;
      const approved = JSON.parse(readFileSync(path, "utf8"));
      return approved.plan_id === binding.plan_id && approved.sha256 === binding.sha256;
    }, { timeout, intervals: [2000] }).toBe(true);
    await dialog.getByRole("button", { name: "Approve", exact: true }).click();
    await capture(page, "08-plan-approved");
    await waitEvent(request, project, "run.goal.completed", timeout);
    const delivered = await events(request, project);
    writeFileSync(`${evidence}/delivery-events.json`, JSON.stringify(delivered, null, 2));
    await capture(page, "09-delivery-terminal");
  }

  writeFileSync(`${evidence}/body-completed.json`, JSON.stringify({ journey, plan_id: planId, boundary: process.env.ZF_UI_DELIVERY === "true" ? "delivery" : "pending-plan", completed_at: new Date().toISOString() }));
});
