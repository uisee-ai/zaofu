import { expect, test, type Locator, type Page } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";

test.describe.configure({ timeout: 120_000 });

async function expectWorkbenchLoaded(page: import("@playwright/test").Page) {
  await expect(page.locator(".status-pill.status-live"))
    .toBeVisible({ timeout: 90_000 });
}

async function graphOverlapPairs(page: Page): Promise<string[]> {
  return page.locator("[data-testid=behavior-loop-graph]").evaluate((graph) => {
    const nodes = Array.from(graph.querySelectorAll("[data-testid=behavior-loop-node]")).map((node) => {
      const element = node as HTMLElement;
      const rect = element.getBoundingClientRect();
      return {
        bottom: rect.bottom,
        label: element.querySelector("strong")?.textContent?.trim() || "",
        left: rect.left,
        right: rect.right,
        top: rect.top,
      };
    });
    const pairs: string[] = [];
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const left = nodes[i]!;
        const right = nodes[j]!;
        const overlaps = !(left.right <= right.left || right.right <= left.left || left.bottom <= right.top || right.bottom <= left.top);
        if (overlaps) pairs.push(`${left.label}<->${right.label}`);
      }
    }
    return pairs;
  });
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
  await expect(page.getByLabel("Task signal filter")).toHaveValue("focused");
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

test("Loop layout selector preserves topology deeplink", async ({ page }) => {
  await page.goto("/?page=behavior-loop&layout=ring");
  await expectWorkbenchLoaded(page);
  await expect(page.getByRole("heading", { name: "Loop", exact: true })).toBeVisible();

  const lensGroup = page.getByRole("radiogroup", { name: "Loop lens" });
  await expect(lensGroup.getByRole("radio", { name: "All loop lens" })).toHaveAttribute("aria-checked", "true");
  const graphPanel = page.locator(".behavior-loop-graph");
  await expect(graphPanel.getByText("Plan", { exact: true })).toBeVisible();
  await expect(lensGroup.getByText("Agent", { exact: true })).toBeVisible();
  await lensGroup.getByRole("radio", { name: "Agent loop lens" }).click();
  await expect(lensGroup.getByRole("radio", { name: "Agent loop lens" })).toHaveAttribute("aria-checked", "true");
  await expect(page).toHaveURL(/lens=agent/);
  await expect(page).not.toHaveURL(/node_id=/);
  await expect(page.getByText("Active Agents")).toBeVisible();
  await expect(graphPanel.getByText("Heartbeat", { exact: true })).toBeVisible();
  await page.getByTestId("loop-metric-active_agents").click();
  const lineagePanel = page.getByTestId("loop-lineage-panel");
  await expect(lineagePanel.getByRole("heading", { name: "metric: Active Agents" })).toBeVisible();
  await expect(lineagePanel.getByText(/projections/)).toBeVisible();
  await page.getByTestId("loop-stage-act").click();
  await expect(lineagePanel.getByRole("heading", { name: "stage: Briefing" })).toBeVisible();
  await expect(page).toHaveURL(/node_id=briefing/);
  await lensGroup.getByRole("radio", { name: "Verification loop lens" }).click();
  await expect(page).toHaveURL(/lens=verification/);
  await expect(page).not.toHaveURL(/node_id=/);
  await expect(graphPanel.getByText("Dev Done", { exact: true })).toBeVisible();
  await expect(graphPanel.getByText("Judge", { exact: true })).toBeVisible();
  await lensGroup.getByRole("radio", { name: "Event-driven loop lens" }).click();
  await expect(page).toHaveURL(/lens=event_driven/);
  await expect(page).not.toHaveURL(/node_id=/);
  await expect(graphPanel.getByText("Ingest", { exact: true })).toBeVisible();
  await expect(graphPanel.getByText("Ack", { exact: true })).toBeVisible();
  await expect(page.locator("[data-testid=behavior-loop-node]")).toHaveCount(5);
  const overlapPairs = await graphOverlapPairs(page);
  expect(overlapPairs).toEqual([]);
  await lensGroup.getByRole("radio", { name: "Hill-climbing loop lens" }).click();
  await expect(page).toHaveURL(/lens=hill_climbing/);
  await expect(page).not.toHaveURL(/node_id=/);
  await expect(graphPanel.getByText("Failure Trace", { exact: true })).toBeVisible();
  await expect(graphPanel.getByText("Verified", { exact: true })).toBeVisible();
  await lensGroup.getByRole("radio", { name: "All loop lens" }).click();
  await expect(page).not.toHaveURL(/lens=/);

  const layoutGroup = page.getByRole("radiogroup", { name: "Loop layout" });
  await expect(layoutGroup.getByRole("radio", { name: "Ring" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText(/Ring .*selected|Auto -> Ring|closed-loop stages/i)).toBeVisible();

  await layoutGroup.getByRole("radio", { name: "Tree" }).click();
  await expect(layoutGroup.getByRole("radio", { name: "Tree" })).toHaveAttribute("aria-checked", "true");
  await expect(page).toHaveURL(/layout=tree/);

  await layoutGroup.getByRole("radio", { name: "Auto" }).click();
  await expect(layoutGroup.getByRole("radio", { name: "Auto" })).toHaveAttribute("aria-checked", "true");
  await expect(page).not.toHaveURL(/layout=/);
  await expect(page.getByRole("heading", { name: "Graph", exact: true })).toBeVisible();
  await expect(page.locator(".behavior-loop-status")).toHaveCount(0);
  await expect(page.getByText("measure-loop.projector")).toHaveCount(0);
});

test("Loop lineage Trace action opens trace explorer deep link", async ({ page }) => {
  await page.goto("/?page=behavior-loop");
  await expectWorkbenchLoaded(page);
  await expect(page.getByRole("heading", { name: "Loop", exact: true })).toBeVisible();

  await page.getByTestId("loop-metric-delivery").click();
  const lineagePanel = page.getByTestId("loop-lineage-panel");
  await expect(lineagePanel.getByRole("heading", { name: "metric: Delivery" })).toBeVisible();
  await lineagePanel.getByRole("button", { name: "Trace" }).first().click();

  await expect(page).toHaveURL(/page=traces/);
  await expect(page).toHaveURL(/trace_id=trace-/);
  await expect(page.getByRole("heading", { name: "Observability" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Trace Detail" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Trace Summary" })).toBeVisible({ timeout: 90_000 });

  const traceId = new URL(page.url()).searchParams.get("trace_id");
  expect(traceId).toBeTruthy();
  await page.goto(`/?page=traces&trace_id=${traceId}`);
  await expect(page.getByRole("heading", { name: "Observability" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Trace Detail" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Trace Summary" })).toBeVisible({ timeout: 90_000 });
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

test("opens task drilldown as a page instead of a right sidebar", async ({ page }) => {
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
  await page.locator(".task-detail").getByRole("button", { name: "Workbench" }).click();
  await expectWorkbenchKeyValueSeparated(page.locator(".task-workbench"));
  await page.locator(".task-detail").getByRole("button", { name: "Events" }).click();
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
