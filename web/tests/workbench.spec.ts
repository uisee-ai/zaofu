import { expect, test, type Locator } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";

test.describe.configure({ timeout: 120_000 });

async function expectWorkbenchLoaded(page: import("@playwright/test").Page) {
  await expect(page.locator('.status-pill[title*="stream live"]'))
    .toBeVisible({ timeout: 90_000 });
}

async function expectEventTimeSeparated(row: Locator) {
  const metrics = await row.locator("span").evaluateAll((nodes) => (
    nodes.slice(0, 3).map((node) => {
      const rect = node.getBoundingClientRect();
      return {
        clientWidth: node.clientWidth,
        right: rect.right,
        scrollWidth: node.scrollWidth,
        x: rect.x,
      };
    })
  ));
  expect(metrics).toHaveLength(3);
  expect(metrics[0]!.scrollWidth).toBeLessThanOrEqual(metrics[0]!.clientWidth + 1);
  expect(metrics[1]!.scrollWidth).toBeLessThanOrEqual(metrics[1]!.clientWidth + 1);
  expect(metrics[0]!.right).toBeLessThanOrEqual(metrics[1]!.x);
  expect(metrics[1]!.right).toBeLessThanOrEqual(metrics[2]!.x);
  expect(metrics[1]!.x - metrics[0]!.right).toBeGreaterThanOrEqual(10);
  expect(metrics[2]!.x - metrics[1]!.right).toBeGreaterThanOrEqual(16);
}

async function expectWorkbenchKeyValueSeparated(workbench: Locator) {
  const panels = await workbench.locator(".key-panel").evaluateAll((nodes) => (
    nodes.map((panel) => {
      const title = panel.querySelector("h3")?.textContent ?? "";
      return Array.from(panel.querySelectorAll("dt")).map((dt) => {
        const dd = dt.nextElementSibling as HTMLElement | null;
        const keyRect = dt.getBoundingClientRect();
        const valueRect = dd?.getBoundingClientRect();
        return {
          gap: valueRect ? valueRect.x - keyRect.right : 0,
          key: dt.textContent ?? "",
          keyClientWidth: dt.clientWidth,
          keyScrollWidth: dt.scrollWidth,
          title,
        };
      });
    }).flat()
  ));
  expect(panels.length).toBeGreaterThan(0);
  for (const row of panels) {
    expect(row.keyScrollWidth, `${row.title}.${row.key} key width`).toBeLessThanOrEqual(row.keyClientWidth + 1);
    expect(row.gap, `${row.title}.${row.key} key/value gap`).toBeGreaterThanOrEqual(20);
  }
}

test("loads the ZaoFu workbench from FastAPI projections", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");

  await expectWorkbenchLoaded(page);
  const snapshot = await page.request.get("/api/snapshot", { timeout: 60_000 });
  expect(snapshot.ok()).toBeTruthy();
  const data = await snapshot.json();

  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  const workspaceRail = page.getByRole("region", { name: "Navigation rail" });
  await expect(workspaceRail).toBeVisible();
  await expect(workspaceRail.getByRole("heading", { name: "Control" })).toBeVisible();
  await expect(workspaceRail.getByText("Workspace", { exact: true })).toBeVisible();
  await expect(workspaceRail.getByLabel("Project")).toBeVisible();
  await expect(workspaceRail.getByText("Channels", { exact: true })).toBeVisible();
  await expect(workspaceRail.getByText("Actions", { exact: true })).toHaveCount(0);
  await expect(workspaceRail.getByRole("button", { name: "Search" })).toHaveCount(0);
  await expect(workspaceRail.getByRole("button", { name: /New Task/ })).toHaveCount(0);
  await expect(workspaceRail.getByRole("button", { name: "Kanban Agent", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /New Task/ })).toBeVisible();
  await expect(workspaceRail.getByText("Primary", { exact: true })).toHaveCount(0);
  await expect(workspaceRail.getByRole("button", { name: "Command Palette" })).toHaveCount(0);
  await expect(workspaceRail.getByRole("button", { name: "Triage" })).toHaveCount(0);
  await expect(workspaceRail.getByRole("button", { name: "Tasks" })).toBeVisible();
  await expect(workspaceRail.getByRole("button", { name: "Process" })).toHaveCount(0);
  await expect(workspaceRail.locator("summary").filter({ hasText: "Operations" })).toBeVisible();
  await expect(workspaceRail.getByRole("button", { name: "Runtime" })).toBeVisible();
  await expect(workspaceRail.locator("summary").filter({ hasText: "System" })).toBeVisible();
  await expect(workspaceRail.locator("summary").filter({ hasText: "Pinned" })).toHaveCount(0);
  await expect(workspaceRail.getByRole("button", { name: "Settings" })).toBeVisible();
  await expect(workspaceRail.getByText("state_dir")).toHaveCount(0);
  await expect(workspaceRail.getByText("preset")).toHaveCount(0);
  await expect(workspaceRail.locator(".workspace-title")).toHaveCount(0);
  await expect(workspaceRail.getByText("ZaoFu Project", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Chat", exact: true })).toHaveCount(0);
  await expect(page.getByPlaceholder("Ask Kanban Agent to create, move, inspect, or run tasks")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Kanban Agent collapsed" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Kanban Agent" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Open Kanban Agent" })).toBeVisible();
  await workspaceRail.getByRole("button", { name: "Agents" }).click();
  await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Attention Queue" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Role Fleet" })).toBeVisible();
  await workspaceRail.getByRole("button", { name: "Tasks" }).click();
  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  await expect(page.getByLabel("Task signal filter")).toHaveValue("all");
  await page.getByRole("button", { name: "List" }).click();
  await expect(page.getByRole("heading", { name: "Task List" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Inspector" })).toBeVisible();
  await expect(page.getByLabel("Task inspector")).toBeVisible();
  await page.getByRole("button", { name: "Board" }).click();
  const hiddenAgentColumns = await page.locator(".workspace").evaluate((el) =>
    getComputedStyle(el).gridTemplateColumns.split(" "),
  );
  expect(hiddenAgentColumns.length).toBe(2);
  const firstColumnWidth = await page.locator(".board-column").first().evaluate((el) =>
    Math.round(el.getBoundingClientRect().width),
  );
  expect(firstColumnWidth).toBeGreaterThanOrEqual(240);
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open Kanban Agent" })).toHaveCount(0);
  const agentPanel = page.locator(".orchestrator-panel");
  await expect(agentPanel.getByText("Scope", { exact: true })).toHaveCount(0);
  await expect(agentPanel.getByText("Delivery", { exact: true })).toHaveCount(0);
  await expect(agentPanel.getByText("PTY", { exact: true })).toHaveCount(0);
  await expect(agentPanel.getByText("Workdir", { exact: true })).toHaveCount(0);
  await expect(agentPanel.getByText(/seq \d+/)).toHaveCount(0);
  await expect(agentPanel.getByText(/headless/i)).toHaveCount(0);
  const backendTrigger = page.getByRole("button", { name: /Agent backend/ });
  await expect(backendTrigger).toBeVisible();
  await expect(backendTrigger).toContainText(/Claude|Codex/);
  await backendTrigger.click();
  const backendMenu = page.getByRole("listbox", { name: "Kanban Agent backend options" });
  await expect(backendMenu.getByRole("option", { name: /Claude/ })).toBeVisible();
  await expect(backendMenu.getByRole("option", { name: /Codex/ })).toBeVisible();
  await expect(backendMenu).not.toContainText("headless");
  await backendMenu.getByRole("option", { name: /Codex/ }).click();
  await expect(backendTrigger).toContainText("Codex");
  await expect(agentPanel.locator(".chat-surface-head")).toHaveCount(0);
  await expect(page.locator(".headless-input")).toBeVisible();
  await expect(page.locator(".terminal-fallback")).toHaveCount(0);
  await expect(page.getByLabel("Operator xterm")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Request Fanout" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();
  await expect(page.locator(".side-panel")).toHaveCount(0);
  const columns = await page.locator(".workspace").evaluate((el) =>
    getComputedStyle(el).gridTemplateColumns.split(" ").length,
  );
  expect(columns).toBe(2);

  expect(data).toHaveProperty("seq");
  expect(data).toHaveProperty("tasks");
  expect(data).toHaveProperty("roles");
  expect(data).toHaveProperty("workdirs");
  expect(data.runtime).toHaveProperty("web_session");
  expect(data.runtime).toHaveProperty("agent_surface");
  expect(data.runtime.agent_surface.session_id).toContain("kanban-agent:");
  expect(data.runtime.agent_surface).toHaveProperty("default_backend");
  expect(data.runtime.agent_surface).toHaveProperty("backends");
  expect(data.runtime.agent_surface.shared_context.project_root).toBe(data.project.root);
  expect(data.runtime.agent_surface.shared_context.state_dir).toBe(data.project.state_dir);
  expect(data.runtime.agent_surface.boundary.scheduler).toBe(false);
  expect(data.runtime.agent_surface.status_model.run_completed_implies_task_done).toBe(false);
  expect(data.runtime.agent_surface.allowed_actions).toContain("update-task");
});

test("opens project home projection", async ({ page }) => {
  await page.goto("/?page=project");

  const projectHome = page.locator(".project-home");
  await expect(projectHome.getByRole("heading", { name: "Project" })).toBeVisible();
  const boundaryDetails = projectHome.locator("details.project-boundary-details").first();
  await expect(boundaryDetails.locator("summary")).toContainText("Control Plane / Runtime State / Project Boundary");
  await boundaryDetails.locator("summary").click();
  await expect(boundaryDetails.getByRole("heading", { name: "Control Plane" })).toBeVisible();
  await expect(boundaryDetails.getByRole("heading", { name: "Runtime State" })).toBeVisible();
  await expect(boundaryDetails.getByRole("heading", { name: "Project Boundary" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open Kanban Agent" })).toBeVisible();
});

test("Loop V2 remains canonical and fills the dashboard workspace", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.addInitScript(() => window.localStorage.setItem("zf.loopV1", "1"));
  await page.goto("/?page=behavior-loop&v=1");
  await expectWorkbenchLoaded(page);
  const loopPage = page.getByTestId("loop-page-v2");
  await expect(loopPage).toBeVisible();
  await expect(page.locator("[data-testid=behavior-loop-graph]")).toHaveCount(0);
  await expect(page.getByTestId("loop-hero")).toHaveCount(0);
  await expect(page.getByTestId("loop-inbox-action")).toHaveCount(0);
  await expect(loopPage.getByRole("heading", { name: "Loop", exact: true })).toBeVisible();

  const [loopBox, projectionBox] = await Promise.all([
    loopPage.boundingBox(),
    page.locator(".projection-scroll").boundingBox(),
  ]);
  expect(loopBox).not.toBeNull();
  expect(projectionBox).not.toBeNull();
  expect(loopBox!.width).toBeGreaterThanOrEqual(projectionBox!.width - 1);
  expect(Math.abs(loopBox!.x - projectionBox!.x)).toBeLessThan(1);
  await page.screenshot({ path: testInfo.outputPath("loop-dashboard-desktop.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  const [mobileLoopBox, mobileProjectionBox] = await Promise.all([
    loopPage.boundingBox(),
    page.locator(".projection-scroll").boundingBox(),
  ]);
  expect(mobileLoopBox).not.toBeNull();
  expect(mobileProjectionBox).not.toBeNull();
  expect(mobileLoopBox!.width).toBeGreaterThanOrEqual(mobileProjectionBox!.width - 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("loop-dashboard-mobile.png"), fullPage: true });
});

test("settings exposes appearance theme controls", async ({ page }) => {
  await page.goto("/?page=settings");
  await expectWorkbenchLoaded(page);
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Appearance" })).toBeVisible();

  const themeGroup = page.getByRole("radiogroup", { name: "Theme" });
  await expect(themeGroup.getByRole("radio", { name: /System/ })).toBeVisible();
  await expect(themeGroup.locator(".theme-preview")).toHaveCount(3);
  await themeGroup.getByRole("radio", { name: /Light/ }).click();
  await expect(themeGroup.getByRole("radio", { name: /Light/ })).toHaveAttribute("aria-checked", "true");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await themeGroup.getByRole("radio", { name: /Dark/ }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("opens task drilldown as a page instead of a right sidebar", async ({ page }, testInfo) => {
  let title = "";
  let taskId = "";
  if (token) {
    title = `Workbench drilldown ${Date.now()}`;
    const created = await page.request.post("/api/actions/create-task", {
      data: { title, source: "playwright" },
      headers: { "X-ZF-Web-Token": token },
    });
    expect(created.ok()).toBeTruthy();
    taskId = String((await created.json()).task_id);
  } else {
    const snapshot = await page.request.get("/api/snapshot");
    const data = await snapshot.json();
    for (const item of [...data.tasks, ...data.archive_tasks]) {
      const id = String(item.id ?? "");
      if (!id) continue;
      const detail = await page.request.get(`/api/tasks/${encodeURIComponent(id)}`);
      if (detail.ok()) {
        taskId = id;
        title = String(item.title ?? "");
        break;
      }
    }
  }
  test.skip(!taskId, "task drilldown needs at least one projected task");

  await page.goto("/");
  await expectWorkbenchLoaded(page);
  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  if (title) {
    await page.getByLabel("Task signal filter").selectOption("all");
    await page.locator(".task-open").filter({ hasText: title }).click();
  } else {
    await page.goto(`/?page=task&task=${encodeURIComponent(taskId)}`);
  }

  await expect(page.getByRole("heading", { name: "Task", exact: true })).toBeVisible();
  await expect(
    page.locator(".task-detail").getByText(taskId, { exact: true }).first(),
  ).toBeVisible({ timeout: 15_000 });
  const detailTabs = page.locator(".task-detail .tab-row");
  await expect(detailTabs.getByRole("button")).toHaveCount(4);
  await expect(detailTabs.getByRole("button", { name: "Summary" })).toBeVisible();
  await expect(detailTabs.getByRole("button", { name: "Activity" })).toBeVisible();
  await expect(detailTabs.getByRole("button", { name: "Evidence" })).toBeVisible();
  await expect(detailTabs.getByRole("button", { name: "Advanced" })).toBeVisible();
  await expect(page.getByTestId("task-summary-view")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("task-detail-summary.png"), fullPage: true });
  await detailTabs.getByRole("button", { name: "Activity" }).click();
  await expect(page.getByTestId("task-activity-view")).toBeVisible();
  await detailTabs.getByRole("button", { name: "Evidence" }).click();
  await expect(page.getByTestId("task-evidence-view")).toBeVisible();
  await detailTabs.getByRole("button", { name: "Advanced" }).click();
  await expect(page.getByTestId("task-advanced-view")).toBeVisible();
  await expect(page.locator(".task-detail").getByRole("button", { name: "Agent" })).toBeVisible();
  await expect(page.getByLabel("Move task status")).toHaveCount(0);
  await expect(page.locator(".task-card-actions")).toHaveCount(0);
  await page.locator(".task-detail").getByRole("button", { name: "Agent" }).click();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toBeVisible();
  await expect(page.locator(".orchestrator-panel .chat-surface-head")).toHaveCount(0);
  await expect(page.locator(".side-panel")).toHaveCount(0);
  await page.getByRole("button", { name: "Minimize Kanban Agent" }).click();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toHaveCount(0);
  await page.locator(".task-detail").getByRole("button", { name: "Board" }).click();
  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
});

test("keeps event timeline readable and inspector structured", async ({ page }) => {
  await page.goto("/?page=events");
  await expectWorkbenchLoaded(page);
  await expect(page.getByRole("heading", { name: "Observability" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Event Views" })).toBeVisible();
  await expect(page.locator(".event-list:not(.compact-events) .event-row").first())
    .toBeVisible();
  await expect(page.getByRole("heading", { name: "Event Stream" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "JSON Inspector" })).toBeVisible();
  await expect(page.getByLabel("Event inspector")).toBeVisible();
  await expect(page.locator(".event-inspector-summary")).toBeVisible();
  await expect(page.getByLabel("Audit projection summary")).toBeVisible();
  await expectEventTimeSeparated(page.locator(".event-list:not(.compact-events) .event-row").first());

  await page.goto("/");
  await expectWorkbenchLoaded(page);
  await page.getByLabel("Task signal filter").selectOption("all");
  await page.locator(".task-open").first().click();
  await expect(page.getByRole("heading", { name: "Task", exact: true })).toBeVisible();
  await page.locator(".task-detail").getByRole("button", { name: "Advanced" }).click();
  await expectWorkbenchKeyValueSeparated(page.locator(".task-workbench"));
  await page.locator(".task-detail").getByRole("button", { name: "Activity" }).click();
  await expect(page.locator(".compact-events .event-row").first()).toBeVisible();
  await expectEventTimeSeparated(page.locator(".compact-events .event-row").first());
});

test("opens task-scoped events as a task lens", async ({ page }) => {
  await page.goto("/");
  await expectWorkbenchLoaded(page);
  const eventsResponse = await page.request.get("/api/events?limit=120");
  expect(eventsResponse.ok()).toBeTruthy();
  const eventsData = (await eventsResponse.json()) as { items?: Array<{ task_id?: string | null }> };
  const taskId = eventsData.items?.find((event) => event.task_id)?.task_id ?? "";
  test.skip(!taskId, "requires at least one task-scoped event");

  await page.goto(`/?page=events&task=${encodeURIComponent(taskId)}`);
  await expectWorkbenchLoaded(page);
  await expect(page.getByRole("heading", { name: "Observability" })).toBeVisible();
  await expect(page.getByLabel("Active event filters")).toContainText(`task:${taskId}`);
  await expect(page.getByRole("heading", { name: "Event Stream" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "JSON Inspector" })).toBeVisible();
  await page.getByLabel("Event saved views").getByRole("button", { name: /Runtime/ }).click();
  await expect(page.getByLabel("Active event filters")).toContainText(`task:${taskId}`);
});

test("shows board action lock when dragging without an action token", async ({ page }) => {
  test.setTimeout(180_000);
  test.skip(!token, "ZF_WEB_ACTION_TOKEN_FOR_TEST is required for board mutation E2E setup");
  const title = `Locked drag ${Date.now()}`;
  const created = await page.request.post("/api/actions/create-task", {
    data: { title, source: "playwright" },
    headers: { "X-ZF-Web-Token": token },
  });
  expect(created.ok()).toBeTruthy();
  const taskId = String((await created.json()).task_id);

  await page.addInitScript(() => {
    window.localStorage.removeItem("zf.webActionToken");
  });
  await page.goto("/");
  await expectWorkbenchLoaded(page);
  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  await page.getByLabel("Task signal filter").selectOption("all");
  await expect(page.getByText("board actions: token needed")).toBeVisible();

  const source = page.locator(".task-card").filter({ hasText: title }).first();
  const target = page.locator('.board-column[data-column-id="in_progress"]');
  await expect(target).toBeVisible();
  await source.scrollIntoViewIfNeeded();
  await expect(source).toBeVisible();
  const sourceBox = await source.boundingBox();
  const targetBox = await target.boundingBox();
  expect(sourceBox).toBeTruthy();
  expect(targetBox).toBeTruthy();
  await page.mouse.move(
    sourceBox!.x + sourceBox!.width / 2,
    sourceBox!.y + Math.min(sourceBox!.height / 2, 28),
  );
  await page.mouse.down();
  await page.mouse.move(
    sourceBox!.x + sourceBox!.width / 2 + 24,
    sourceBox!.y + Math.min(sourceBox!.height / 2, 28) + 12,
    { steps: 5 },
  );
  await page.mouse.move(
    targetBox!.x + targetBox!.width / 2,
    sourceBox!.y + Math.min(sourceBox!.height / 2, 28),
    { steps: 20 },
  );
  await page.mouse.up();

  await expect(page.getByText(new RegExp(`Cannot move ${taskId}: board actions are token needed`))).toBeVisible();
  const snapshot = await page.request.get("/api/snapshot", { timeout: 30_000 });
  const data = await snapshot.json();
  const task = data.tasks.find((item: { id?: string }) => item.id === taskId);
  expect(task?.status).toBe("backlog");

  const lockNotice = page.locator(".board-action-notice");
  await lockNotice.getByPlaceholder("action token").fill(token);
  await lockNotice.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("board actions: token needed")).toHaveCount(0);

  await source.scrollIntoViewIfNeeded();
  const retrySourceBox = await source.boundingBox();
  const retryTargetBox = await target.boundingBox();
  expect(retrySourceBox).toBeTruthy();
  expect(retryTargetBox).toBeTruthy();
  await page.mouse.move(
    retrySourceBox!.x + retrySourceBox!.width / 2,
    retrySourceBox!.y + Math.min(retrySourceBox!.height / 2, 28),
  );
  await page.mouse.down();
  await page.mouse.move(
    retrySourceBox!.x + retrySourceBox!.width / 2 + 24,
    retrySourceBox!.y + Math.min(retrySourceBox!.height / 2, 28) + 12,
    { steps: 5 },
  );
  await page.mouse.move(
    retryTargetBox!.x + retryTargetBox!.width / 2,
    retrySourceBox!.y + Math.min(retrySourceBox!.height / 2, 28),
    { steps: 20 },
  );
  await page.mouse.up();
  await expect.poll(async () => {
    const nextSnapshot = await page.request.get("/api/snapshot", { timeout: 30_000 });
    const nextData = await nextSnapshot.json();
    const nextTask = nextData.tasks.find((item: { id?: string }) => item.id === taskId);
    return nextTask?.status ?? "";
  }, { timeout: 45_000 }).toBe("in_progress");
});

test("moves a board card by drag and drop through the controlled action path", async ({ page }) => {
  test.setTimeout(180_000);
  test.skip(!token, "ZF_WEB_ACTION_TOKEN_FOR_TEST is required for board mutation E2E");
  const title = `Drag move ${Date.now()}`;
  const created = await page.request.post("/api/actions/create-task", {
    data: { title, source: "playwright" },
    headers: { "X-ZF-Web-Token": token },
  });
  expect(created.ok()).toBeTruthy();
  const taskId = String((await created.json()).task_id);

  await page.addInitScript((value) => {
    window.localStorage.setItem("zf.webActionToken", value);
  }, token);
  await page.goto("/");
  await expectWorkbenchLoaded(page);
  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  await page.getByLabel("Task signal filter").selectOption("all");

  const source = page.locator(".task-card").filter({ hasText: title }).first();
  const target = page.locator('.board-column[data-column-id="in_progress"]');
  await expect(target).toBeVisible();
  await source.scrollIntoViewIfNeeded();
  await expect(source).toBeVisible();

  const sourceBox = await source.boundingBox();
  const targetBox = await target.boundingBox();
  expect(sourceBox).toBeTruthy();
  expect(targetBox).toBeTruthy();
  await page.mouse.move(
    sourceBox!.x + sourceBox!.width / 2,
    sourceBox!.y + Math.min(sourceBox!.height / 2, 28),
  );
  await page.mouse.down();
  await page.mouse.move(
    sourceBox!.x + sourceBox!.width / 2 + 24,
    sourceBox!.y + Math.min(sourceBox!.height / 2, 28) + 12,
    { steps: 5 },
  );
  await page.mouse.move(
    targetBox!.x + targetBox!.width / 2,
    sourceBox!.y + Math.min(sourceBox!.height / 2, 28),
    { steps: 20 },
  );
  await page.mouse.up();

  await expect.poll(async () => {
    const detail = await page.request.get(`/api/tasks/${taskId}`, { timeout: 30_000 });
    const data = await detail.json();
    return data.task?.status ?? "";
  }, { timeout: 45_000 }).toBe("in_progress");
  await page.goto("/");
  await expectWorkbenchLoaded(page);
  await page.getByLabel("Task signal filter").selectOption("all");
  await page.getByPlaceholder("filter tasks").fill(title);
  await expect(
    page.locator('.board-column[data-column-id="in_progress"] .task-card').filter({ hasText: title }),
  ).toBeVisible({ timeout: 60_000 });
});
