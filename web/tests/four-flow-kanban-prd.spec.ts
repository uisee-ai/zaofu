import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const channelId = process.env.ZF_FOUR_FLOW_CHANNEL_ID ?? "";
const evidenceDir = process.env.ZF_PLAYWRIGHT_EVIDENCE_DIR ?? "";

type EventItem = {
  seq: number;
  id: string;
  type: string;
  task_id?: string | null;
  payload?: Record<string, unknown>;
};

type EventPage = {
  items?: EventItem[];
  current_seq?: number;
};

type Snapshot = {
  project?: { project_id?: string };
};

const flows = [
  {
    kind: "prd",
    marker: "FOURFLOW_TASK_PRD",
    routeId: "delivery:prd:default",
    option: "Prd delivery (Recommended)",
  },
  {
    kind: "issue",
    marker: "FOURFLOW_TASK_ISSUE",
    routeId: "delivery:issue:default",
    option: "Issue delivery (Recommended)",
  },
  {
    kind: "refactor",
    marker: "FOURFLOW_TASK_REFACTOR",
    routeId: "delivery:refactor:default",
    option: "Refactor delivery (Recommended)",
  },
  {
    kind: "general",
    marker: "FOURFLOW_TASK_GENERAL",
    routeId: "general:scope",
    option: "General delivery (Recommended)",
  },
] as const;

test.describe.configure({ mode: "serial", timeout: 300_000 });

async function apiJson<T>(
  request: APIRequestContext,
  path: string,
): Promise<T> {
  const response = await request.get(path);
  const body = await response.json().catch(() => ({}));
  expect(
    response.ok(),
    `${path}: ${response.status()} ${JSON.stringify(body)}`,
  ).toBeTruthy();
  return body as T;
}

async function projectId(request: APIRequestContext): Promise<string> {
  const snapshot = await apiJson<Snapshot>(request, "/api/snapshot");
  const id = String(snapshot.project?.project_id ?? "");
  expect(id).not.toBe("");
  return id;
}

async function eventCursor(
  request: APIRequestContext,
  projectIdValue: string,
): Promise<number> {
  const page = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(projectIdValue)}/events?limit=1`,
  );
  return Number(page.current_seq ?? 0);
}

async function eventsAfter(
  request: APIRequestContext,
  projectIdValue: string,
  cursor: number,
): Promise<EventItem[]> {
  const page = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(projectIdValue)}/events`
      + `?cursor=${cursor}&limit=500`,
  );
  return Array.isArray(page.items) ? page.items : [];
}

async function waitForEvents(
  request: APIRequestContext,
  projectIdValue: string,
  cursor: number,
  predicate: (events: EventItem[]) => boolean,
  timeout = 90_000,
): Promise<EventItem[]> {
  let latest: EventItem[] = [];
  await expect.poll(async () => {
    latest = await eventsAfter(request, projectIdValue, cursor);
    return predicate(latest);
  }, {
    timeout,
    intervals: [100, 250, 500, 1000],
  }).toBeTruthy();
  return latest;
}

async function capture(page: Page, name: string): Promise<void> {
  if (!evidenceDir) return;
  await page.screenshot({
    fullPage: true,
    path: `${evidenceDir}/${name}.png`,
  });
}

async function openAgent(page: Page, id: string): Promise<void> {
  const dialog = page.getByRole("dialog", { name: "Kanban Agent" });
  if (await dialog.isVisible().catch(() => false)) return;
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".agent-state-pill")).toHaveText("active", {
    timeout: 30_000,
  });
}

async function createTaskAndStartFlow(
  page: Page,
  request: APIRequestContext,
  id: string,
  flow: typeof flows[number],
): Promise<string> {
  const channelPage = page.locator(".channel-page");
  const handoffCursor = await eventCursor(request, id);
  await channelPage.getByTestId("channel-workflow-objective").fill(
    `${flow.marker} create a Task from the owner-confirmed PRD.`,
  );
  await channelPage.getByTestId("channel-create-task-from-prd").click();
  const dialog = page.getByRole("dialog", { name: "Kanban Agent" });
  await expect(dialog).toBeVisible({ timeout: 30_000 });
  await expect(dialog.locator(".agent-state-pill")).toHaveText("active", {
    timeout: 30_000,
  });

  const createPlan = page.getByTestId("agent-card-plan").filter({
    hasText: flow.marker,
  }).last();
  await expect(createPlan).toBeVisible({ timeout: 45_000 });
  await createPlan.locator(
    'input[data-submit-action="create-task"][data-recommended="true"]',
  ).check();
  await createPlan.getByTestId("ask-user-submit").click();
  await expect(createPlan).toContainText("Ready for confirmation", {
    timeout: 30_000,
  });

  const createCard = page.locator(
    '[data-testid="agent-card-approve"][data-proposal-action="create-task"]',
  ).filter({ hasText: flow.marker }).last();
  await expect(createCard).toBeVisible({ timeout: 45_000 });
  await expect(createCard.getByTestId("agent-proposal-approve")).toBeEnabled();
  await createCard.getByTestId("agent-proposal-approve").click();

  const handoffEvents = await waitForEvents(
    request,
    id,
    handoffCursor,
    (events) => {
      const task = events.find((event) => (
        event.type === "task.created" && Boolean(event.task_id)
      ));
      return Boolean(task) && events.some((event) => (
        event.type === "kanban.agent.plan.requested"
        && event.task_id === task?.task_id
      ));
    },
  );
  const taskId = String(
    handoffEvents.find((event) => event.type === "task.created")?.task_id ?? "",
  );
  expect(taskId).not.toBe("");

  const plan = page.getByTestId("agent-card-plan").filter({
    hasText: flow.marker,
  }).last();
  await expect(plan).toBeVisible({ timeout: 45_000 });
  await expect(plan).toContainText(flow.routeId);
  await expect(plan).toContainText("Preflight: ready");
  await plan.locator(
    `input[data-submit-action="workflow-start"][data-route-id="${flow.routeId}"]`,
  ).check();
  await plan.getByTestId("ask-user-submit").click();
  await expect(plan).toContainText("Ready for confirmation", {
    timeout: 30_000,
  });

  const approve = page.locator(
    '[data-testid="agent-card-approve"][data-proposal-action="workflow-start"]',
  ).filter({
    hasText: flow.routeId,
  }).last();
  await expect(approve).toBeVisible({ timeout: 45_000 });
  await expect(approve).toContainText(taskId);
  await expect(approve.getByTestId("agent-proposal-approve")).toBeEnabled();
  await approve.getByTestId("agent-proposal-approve").click();

  await waitForEvents(
    request,
    id,
    handoffCursor,
    (events) => events.some((event) => (
      event.type === "workflow.invoke.requested"
      && event.task_id === taskId
    )),
    120_000,
  );
  await expect(approve).toContainText("Workflow started", {
    timeout: 45_000,
  });
  await page.getByRole("button", { name: "Minimize Kanban Agent" }).click();
  return taskId;
}

test("creates a two-round PRD Channel and starts four workflows from its PRD", async ({
  page,
  request,
}) => {
  expect(token).not.toBe("");
  expect(channelId).not.toBe("");
  const id = await projectId(request);
  await page.addInitScript((actionToken) => {
    window.localStorage.clear();
    window.localStorage.setItem("zf.operatorBackend", "claude-headless");
    window.localStorage.setItem("zf.webActionToken", actionToken);
    window.localStorage.setItem("zf.themeMode", "light");
  }, token);
  await page.goto(`/?project=${encodeURIComponent(id)}`);
  await expect(page.locator(".status-pill.status-live")).toBeVisible({
    timeout: 90_000,
  });
  await openAgent(page, id);

  const channelCursor = await eventCursor(request, id);
  const dialog = page.getByRole("dialog", { name: "Kanban Agent" });
  const input = dialog.locator("textarea.headless-input");
  await input.fill(
    "FOURFLOW_CHANNEL_SETUP discuss a minimal change: add one line to "
      + "README.md, preserve existing behavior, and produce the canonical PRD.",
  );
  await dialog.getByRole("button", { name: "Send message" }).click();
  const channelPlan = page.getByTestId("agent-card-plan").filter({
    hasText: "FOURFLOW_CHANNEL_SETUP",
  }).last();
  await expect(channelPlan).toBeVisible({ timeout: 45_000 });
  await expect(channelPlan).toContainText("product pm");
  await expect(channelPlan).toContainText("arch");
  await expect(channelPlan).toContainText("critic");
  await expect(channelPlan).toContainText("synthesizer");
  await expect(channelPlan).toContainText("4 members");
  await expect(channelPlan).toContainText("2 round cap");
  await channelPlan.getByTestId("ask-user-option-prd-clarification").check();
  await capture(page, "03-prd-channel-plan");
  await channelPlan.getByTestId("ask-user-submit").click();

  await waitForEvents(
    request,
    id,
    channelCursor,
    (events) => (
      events.some((event) => (
        event.type === "channel.created"
        && event.payload?.channel_id === channelId
      ))
      && events.some((event) => (
        event.type === "channel.discussion.started"
        && event.payload?.channel_id === channelId
      ))
      && events.some((event) => (
        event.type === "channel.synthesis.proposed"
        && event.payload?.channel_id === channelId
      ))
    ),
    120_000,
  );

  await page.getByRole("button", { name: "Minimize Kanban Agent" }).click();
  await page.goto(
    `/?project=${encodeURIComponent(id)}&page=channels`
      + `&channel=${encodeURIComponent(channelId)}`,
  );
  await expect(page.locator(".status-pill.status-live")).toBeVisible({
    timeout: 90_000,
  });
  const channelPage = page.locator(".channel-page");
  await expect(channelPage).toContainText("Four-flow minimal PRD", {
    timeout: 45_000,
  });
  await page.getByTitle("Members").click();
  const memberDrawer = page.locator(".channel-drawer");
  for (const member of ["product_pm", "arch", "critic", "synthesizer"]) {
    await expect(memberDrawer).toContainText(member);
  }
  await expect(memberDrawer).not.toContainText("security_reviewer");
  await capture(page, "04-prd-channel-members");
  await page.getByTitle("Close drawer").click();

  const consensusControl = page.locator(".channel-consensus-control");
  await expect(consensusControl).toContainText(
    "Canonical PRD awaiting owner decision",
    { timeout: 90_000 },
  );
  const consensusCursor = await eventCursor(request, id);
  await consensusControl.getByRole("button", { name: "Confirm" }).click();
  await waitForEvents(
    request,
    id,
    consensusCursor,
    (events) => events.some((event) => (
      event.type === "channel.consensus.reached"
      && event.payload?.channel_id === channelId
    )),
    90_000,
  );
  await capture(page, "05-canonical-prd-confirmed");
  await channelPage.getByTestId("channel-details-tab").click();
  await expect(
    channelPage.getByTestId("channel-create-task-from-prd"),
  ).toBeEnabled({ timeout: 90_000 });
  await expect(channelPage.getByTestId("channel-workflow-task")).toBeHidden();

  const taskIds: string[] = [];
  for (const flow of flows) {
    taskIds.push(await createTaskAndStartFlow(
      page,
      request,
      id,
      flow,
    ));
  }
  expect(new Set(taskIds).size).toBe(4);
  await capture(page, "06-four-workflows-started");
});
