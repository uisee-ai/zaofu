import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const realCodex = process.env.ZF_E2E_CODEX_HEADLESS === "1";

test.describe.configure({ timeout: 360_000 });

async function action(request: APIRequestContext, name: string, payload: Record<string, unknown> = {}) {
  const response = await request.post(`/api/actions/${name}`, {
    data: payload,
    headers: { "x-zf-web-token": token },
  });
  const body = await response.json().catch(() => ({}));
  expect(response.ok(), `${name} failed: ${response.status()} ${JSON.stringify(body)}`).toBeTruthy();
  return body as Record<string, unknown>;
}

async function json(request: APIRequestContext, path: string) {
  const response = await request.get(path);
  const body = await response.json().catch(() => ({}));
  expect(response.ok(), `${path} failed: ${response.status()} ${JSON.stringify(body)}`).toBeTruthy();
  return body as Record<string, unknown>;
}

async function primeBrowser(page: Page) {
  await page.addInitScript((value) => {
    window.localStorage.setItem("zf.webActionToken", value.token);
    window.localStorage.setItem("zf.operatorBackend", "claude-headless");
  }, { token });
}

function projectUrl(projectId: string, params: Record<string, string> = {}) {
  const query = new URLSearchParams({ project: projectId, ...params });
  return `/?${query.toString()}`;
}

test("Channel Group and Kanban Agent real interaction audit", async ({ page, request }) => {
  const consoleErrors: string[] = [];
  const auditIssues: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  await primeBrowser(page);
  const defaultSnapshot = await json(request, "/api/snapshot");
  const defaultProject = defaultSnapshot.project as Record<string, unknown> | undefined;
  const projectId = String(defaultProject?.project_id ?? "");
  expect(projectId, "default project id is required for isolated E2E").not.toBe("");

  const suffix = Date.now().toString(36);
  const channel = await action(request, "channel-create", {
    name: `# zaofu-e2e-${suffix}`,
    channel_id: `zaofu-e2e-${suffix}`,
  });
  const channelId = String(channel.channel_id);

  const task = await action(request, "create-task", {
    title: `Interactive E2E seed ${suffix}`,
    priority: 2,
    source: "playwright-interactive-audit",
  });
  const taskId = String(task.task_id);

  await action(request, "assignment-propose", {
    task_id: taskId,
    assignee_type: "squad",
    assignee_id: channelId,
    assignee_label: `# zaofu-e2e-${suffix}`,
    channel_id: channelId,
    reason: "interactive channel audit",
  });

  for (const member of [
    ["techlead-codex", "provider_agent", "fake", "tech_leader", "planner", "channel_roles/tech-leader.md"],
    ["qa-agent", "persona_agent", "fake", "qa_analyst", "reviewer", "channel_roles/qa-analyst.md"],
    ["reviewer-agent", "persona_agent", "fake", "critic", "reviewer", "channel_roles/critic.md"],
    ["owner-delegate", "owner_delegate", "fake", "owner_delegate", "owner_report", "channel_roles/owner-delegate.md"],
  ]) {
    await action(request, "channel-invite-member", {
      channel_id: channelId,
      member_id: member[0],
      member_type: member[1],
      provider: member[2],
      backend: member[2],
      channel_role: member[3],
      visibility_profile: member[4],
      role_context_ref: member[5],
      permissions: ["read", "message", "summarize", "propose_workflow", "report_owner"],
      workflow_role_binding: member[0] === "techlead-codex" ? { role: "review", mode: "request_only" } : undefined,
    });
  }

  await page.goto(projectUrl(projectId, {
    page: "channels",
    channel: channelId,
  }));
  await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 90_000 });
  await expect(page.locator(".channel-page")).toContainText(`zaofu-e2e-${suffix}`);

  await page.getByTitle("Members").click();
  await expect(page.locator(".channel-drawer")).toContainText("techlead-codex");
  await expect(page.locator(".channel-drawer")).toContainText("qa_analyst");
  await expect(page.locator(".channel-drawer")).toContainText("owner-delegate");
  await page.getByTitle("Close drawer").click();

  const composer = page.locator(".channel-composer-input");
  await composer.fill("@qa-agent 请补充这个交互审计的最高风险。");
  await page.getByLabel("Send message").click();
  await expect(page.locator(".agent-session")).toContainText("qa-agent", { timeout: 20_000 });
  await expect(page.locator(".agent-session")).toContainText("最高风险", { timeout: 20_000 });

  await composer.fill("@all 各自给一句结论,不要触发 workflow dispatch。");
  await page.getByLabel("Send message").click();
  await expect(page.locator(".agent-session")).toContainText("techlead-codex", { timeout: 20_000 });
  await expect(page.locator(".agent-session")).toContainText("reviewer-agent", { timeout: 20_000 });

  const detailAfterChat = await json(request, `/api/channels/${encodeURIComponent(channelId)}`);
  const replyRequests = detailAfterChat.reply_requests as Array<Record<string, unknown>>;
  expect(replyRequests.some((item) => item.target_member_id === "qa-agent" && item.status === "completed")).toBeTruthy();
  expect(replyRequests.some((item) => item.target_member_id === "techlead-codex")).toBeTruthy();
  expect((detailAfterChat.context_packs as unknown[]).length).toBeGreaterThan(0);

  const synthesis = await action(request, "channel-synthesis", {
    channel_id: channelId,
    thread_id: "main",
    task_id: taskId,
    summary: "Channel participants agreed that interactive E2E is required.",
    decision: "Request review-wave through a kernel-gated workflow intent.",
    refs: { task_id: taskId },
  });
  const executionPatterns = await json(request, "/api/execution-patterns");
  const patterns = Array.isArray(executionPatterns.patterns)
    ? executionPatterns.patterns as Array<Record<string, unknown>>
    : [];
  const workflowPatternId = String(patterns[0]?.pattern_id ?? "").trim();
  const workflowInvoked = Boolean(workflowPatternId);
  if (workflowInvoked) {
    await action(request, "workflow-invoke", {
      channel_id: channelId,
      thread_id: "main",
      task_id: taskId,
      pattern_id: workflowPatternId,
      reason: "interactive audit synthesis",
      synthesis_event_id: String(synthesis.event_id ?? ""),
      expected_output: "review evidence",
    });
  }
  await action(request, "channel-owner-report", {
    channel_id: channelId,
    owner_id: "owner-delegate",
    destination: "channel",
    reason: "interactive audit summary",
  });

  await page.goto(projectUrl(projectId, {
    page: "channels",
    channel: channelId,
  }));
  await page.locator(".channel-tabs").getByRole("button", { name: /Details/ }).click();
  const channelWorkspace = page.locator(".channel-workspace-dashboard");
  await expect(channelWorkspace).toBeVisible();
  if (workflowInvoked) {
    const workflowSurface = page.getByTestId("channel-workflow-surface");
    await expect(workflowSurface).toBeVisible();
    await expect(workflowSurface).toContainText(workflowPatternId);
    const workflowEvents = await json(request, "/api/events?limit=240&event_type=workflow.invoke.requested");
    expect(JSON.stringify(workflowEvents)).toContain(workflowPatternId);
  }
  await expect(channelWorkspace).toContainText("Reports");
  const ownerReportEvents = await json(request, "/api/events?limit=240&event_type=channel.owner_report.generated");
  expect(JSON.stringify(ownerReportEvents)).toContain("channel.owner_report.generated");

  const snapshotWithRoutes = await json(request, "/api/snapshot");
  const routes = snapshotWithRoutes.assignment_routes;
  const routeText = JSON.stringify(routes);
  expect(routeText).toContain(channelId);
  expect(routeText).toContain(workflowInvoked ? "workflow_requested" : "squad_synthesis");

  const eventsBeforeKanban = await json(request, "/api/events?limit=240");
  expect(JSON.stringify(eventsBeforeKanban)).not.toContain('"type":"task.dispatched"');

  await page.goto(projectUrl(projectId));
  await expect(page.getByRole("button", { name: "Open Kanban Agent" })).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Agent backend: Claude/ })).toBeVisible();

  const input = page.getByPlaceholder("Tell me what to do...");
  const sendButton = page.getByRole("button", { name: "Send message" });
  await input.fill("请总结当前 Kanban 上有哪些任务,以及下一步建议。");
  await expect(sendButton).toHaveAttribute("title", "Send", { timeout: 60_000 });
  await sendButton.click();
  await expect(page.locator(".agent-session")).toContainText("ZF_KANBAN_AGENT_FAKE_OK", { timeout: 30_000 });
  await expect(page.locator(".agent-session")).toContainText("read_project_projection", { timeout: 30_000 });

  await page.getByRole("button", { name: "New Kanban Agent chat" }).click();
  await input.fill("请把‘修复 Channel Group 真实互动 E2E 缺口’整理成一个 task proposal。");
  await expect(sendButton).toHaveAttribute("title", "Send", { timeout: 60_000 });
  await sendButton.click();
  const proposalCards = page.locator(".agent-stacked-cards");
  try {
    await expect(proposalCards).toContainText("Create task proposal", { timeout: 8_000 });
  } catch {
    auditIssues.push("Kanban Agent 新 chat 的 action proposal 没有实时渲染；刷新页面后才继续验证。");
    await page.reload();
    const kanbanDialog = page.getByRole("dialog", { name: "Kanban Agent" });
    if (!(await kanbanDialog.isVisible().catch(() => false))) {
      await page.getByRole("button", { name: "Open Kanban Agent" }).click();
      await expect(kanbanDialog).toBeVisible();
    }
  }
  await expect(proposalCards).toContainText("Create task proposal", { timeout: 30_000 });
  await expect(page.locator(".agent-session")).toContainText("Fix Channel Group interactive E2E gap");
  await proposalCards.getByRole("button", { name: "Create Task" }).click();
  await expect.poll(async () => JSON.stringify((await json(request, "/api/snapshot")).tasks ?? {}), {
    timeout: 20_000,
  }).toContain("Fix Channel Group interactive E2E gap");

  await action(request, "agent-session-cancel", {
    conversation_id: "kanban:e2e",
    thread_id: "manual-cancel",
    run_id: "run-cancel-e2e",
    backend: "claude-headless",
    reason: "interactive audit cancel smoke",
  });

  for (const pageId of ["project", "board", "agents", "automations", "events", "backlogs", "channels", "runs", "fanouts", "traces", "workdirs", "skills", "runtime", "archives", "settings"]) {
    await page.goto(projectUrl(projectId, { page: pageId }));
    await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 60_000 });
    await expect(page.locator("body")).not.toContainText("returned 500");
  }

  await page.goto(projectUrl(projectId, { page: "agents" }));
  await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible({ timeout: 30_000 });
  await page.goto(projectUrl(projectId, { page: "events" }));
  await expect(page.getByRole("heading", { name: "Observability" })).toBeVisible({ timeout: 30_000 });

  const finalEvents = await json(request, "/api/events?limit=320");
  const eventText = JSON.stringify(finalEvents);
  expect(eventText).toContain("channel.message.posted");
  expect(eventText).toContain("kanban.agent.turn.started");
  expect(eventText).toContain("kanban.agent.turn.completed");
  expect(eventText).toContain("agent.session.run.cancelled");

  expect(consoleErrors.filter((line) => !line.includes("favicon")).join("\n")).toBe("");
  expect(auditIssues, `audit issues:\n${auditIssues.join("\n")}`).toEqual([]);
});

test("optional real Codex headless interaction", async ({ page, request }) => {
  test.skip(!realCodex, "set --real-provider codex to run real provider smoke");
  await primeBrowser(page);
  await page.addInitScript(() => {
    window.localStorage.setItem("zf.operatorBackend", "codex-headless");
  });
  const snapshot = await json(request, "/api/snapshot");
  const project = snapshot.project as Record<string, unknown> | undefined;
  const projectId = String(project?.project_id ?? "");
  expect(projectId, "default project id is required for real Codex smoke").not.toBe("");
  await page.goto(projectUrl(projectId));
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toBeVisible({ timeout: 15_000 });
  const threadKey = await page.evaluate(() => window.localStorage.getItem("zf.kanbanAgentThreadKey") || "main");
  const result = await action(request, "chat-orchestrator", {
    backend: "codex-headless",
    scope: "project",
    message: "Reply with exactly ZF_KANBAN_AGENT_REAL_OK. Do not modify files.",
    project_id: projectId,
    conversation_id: `kanban:${projectId || "default"}`,
    thread_key: threadKey,
    turn_id: `real-codex-${Date.now().toString(36)}`,
  });
  expect(JSON.stringify(result)).toContain("ZF_KANBAN_AGENT_REAL_OK");
  await page.goto(projectUrl(projectId));
  const dialog = page.getByRole("dialog", { name: "Kanban Agent" });
  if (!(await dialog.isVisible().catch(() => false))) {
    await page.getByRole("button", { name: "Open Kanban Agent" }).click();
    await expect(dialog).toBeVisible({ timeout: 15_000 });
  }
  await expect(page.locator(".agent-session")).toContainText("ZF_KANBAN_AGENT_REAL_OK", { timeout: 180_000 });
});
