import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";

type EventItem = {
  seq: number;
  id: string;
  type: string;
  causation_id?: string | null;
  correlation_id?: string | null;
  payload?: Record<string, unknown>;
};

type EventPage = {
  items?: EventItem[];
  current_seq?: number;
};

type Snapshot = {
  project?: { project_id?: string };
  tasks?: unknown;
  archive_tasks?: unknown;
};

test.describe.configure({ mode: "serial", timeout: 120_000 });

async function apiJson<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(path);
  const body = await response.json().catch(() => ({}));
  expect(response.ok(), `${path}: ${response.status()} ${JSON.stringify(body)}`).toBeTruthy();
  return body as T;
}

async function projectId(request: APIRequestContext): Promise<string> {
  const snapshot = await apiJson<Snapshot>(request, "/api/snapshot");
  const id = String(snapshot.project?.project_id ?? "");
  expect(id, "isolated E2E project id").not.toBe("");
  return id;
}

async function eventCursor(request: APIRequestContext, id: string): Promise<number> {
  const page = await apiJson<EventPage>(request, `/api/projects/${encodeURIComponent(id)}/events?limit=1`);
  return Number(page.current_seq ?? 0);
}

async function eventsAfter(
  request: APIRequestContext,
  id: string,
  cursor: number,
): Promise<EventItem[]> {
  const page = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(id)}/events?cursor=${cursor}&limit=500`,
  );
  return Array.isArray(page.items) ? page.items : [];
}

async function waitForEvents(
  request: APIRequestContext,
  id: string,
  cursor: number,
  predicate: (events: EventItem[]) => boolean,
): Promise<EventItem[]> {
  let latest: EventItem[] = [];
  await expect.poll(async () => {
    latest = await eventsAfter(request, id, cursor);
    return predicate(latest);
  }, { timeout: 30_000, intervals: [100, 200, 500, 1000] }).toBeTruthy();
  return latest;
}

function eventPayloadContains(event: EventItem, marker: string): boolean {
  return JSON.stringify(event.payload ?? {}).includes(marker);
}

function findUserMessage(events: EventItem[], marker: string): EventItem | undefined {
  return events.find((event) => event.type === "user.message" && eventPayloadContains(event, marker));
}

function eventChainForMarker(events: EventItem[], marker: string): EventItem[] {
  const user = findUserMessage(events, marker);
  if (!user) return [];
  return events.filter((event) => event.correlation_id === user.correlation_id);
}

async function primeBrowser(page: Page, withToken = true): Promise<void> {
  await page.addInitScript(({ actionToken, saveToken }) => {
    window.localStorage.clear();
    window.localStorage.setItem("zf.operatorBackend", "claude-headless");
    if (saveToken) window.localStorage.setItem("zf.webActionToken", actionToken);
  }, { actionToken: token, saveToken: withToken });
}

async function openKanbanAgent(page: Page, id: string, withToken = true): Promise<void> {
  await primeBrowser(page, withToken);
  await page.goto(`/?project=${encodeURIComponent(id)}`);
  await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Agent backend: Claude/ })).toBeVisible();
}

async function sendMessage(page: Page, message: string): Promise<void> {
  const input = page.getByPlaceholder("Tell me what to do...");
  await input.fill(message);
  await page.getByRole("button", { name: "Send message" }).click();
}

function snapshotHasTitle(snapshot: Snapshot, title: string): boolean {
  return JSON.stringify([snapshot.tasks ?? [], snapshot.archive_tasks ?? []]).includes(title);
}

test("KBA-01 missing action token fails closed before a turn is created", async ({ page, request }) => {
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const marker = `KBA_NO_TOKEN_${Date.now().toString(36)}`;

  await openKanbanAgent(page, id, false);
  const input = page.getByPlaceholder("Save action token to send...");
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByRole("alert")).toContainText(/valid action token/i);
  await expect(page.getByRole("button", { name: "Send message" })).toBeDisabled();
  await input.fill(marker);
  await input.press("Enter");
  await page.waitForTimeout(500);

  const events = await eventsAfter(request, id, cursor);
  expect(findUserMessage(events, marker)).toBeUndefined();
});

test("KBA-02 readonly stream completes without proposal or task mutation", async ({ page, request }) => {
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const before = await apiJson<Snapshot>(request, `/api/projects/${encodeURIComponent(id)}/snapshot`);
  const marker = `KBA_READONLY_${Date.now().toString(36)}`;

  await openKanbanAgent(page, id);
  await sendMessage(page, `${marker} explain the current project without changing anything`);
  await expect(page.getByRole("button", { name: "Interrupt" })).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".agent-session")).toContainText(marker, { timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Interrupt" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send message" })).toBeVisible({ timeout: 30_000 });

  const events = await waitForEvents(request, id, cursor, (items) => {
    const chain = eventChainForMarker(items, marker);
    return chain.some((event) => event.type === "kanban.agent.turn.completed");
  });
  const chain = eventChainForMarker(events, marker);
  const types = chain.map((event) => event.type);
  expect(types).toContain("user.message");
  expect(types).toContain("kanban.agent.turn.created");
  expect(types).toContain("kanban.agent.turn.started");
  expect(types).toContain("kanban.agent.turn.completed");
  expect(types).not.toContain("kanban.agent.turn.delta");
  expect(types).not.toContain("kanban.agent.action.proposed");
  expect(types).not.toContain("task.created");

  const after = await apiJson<Snapshot>(request, `/api/projects/${encodeURIComponent(id)}/snapshot`);
  expect(JSON.stringify(after.tasks ?? [])).toBe(JSON.stringify(before.tasks ?? []));
  expect(JSON.stringify(after.archive_tasks ?? [])).toBe(JSON.stringify(before.archive_tasks ?? []));
});

test("KBA-03 create-task proposal mutates canonical state only after acceptance", async ({ page, request }) => {
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const marker = `KBA_CREATE_${Date.now().toString(36)}`;
  const title = `Kanban Agent proposal ${marker}`;

  await openKanbanAgent(page, id);
  await sendMessage(page, `${marker} create a task proposal and wait for my approval`);
  const proposals = page.locator(".agent-stacked-cards");
  await expect(proposals).toContainText("Create task proposal", { timeout: 30_000 });
  await expect(proposals).toContainText(title);
  await expect(page.locator(".agent-text-part").filter({ hasText: '{"action_proposal"' })).toHaveCount(0);

  const pendingBefore = await apiJson<{ items?: unknown[] }>(
    request,
    `/api/projects/${encodeURIComponent(id)}/kanban-agent/pending-proposals`,
  );
  expect(JSON.stringify(pendingBefore.items ?? [])).toContain(title);
  const snapshotBefore = await apiJson<Snapshot>(request, `/api/projects/${encodeURIComponent(id)}/snapshot`);
  expect(snapshotHasTitle(snapshotBefore, title)).toBeFalsy();
  const beforeEvents = await eventsAfter(request, id, cursor);
  expect(beforeEvents.some((event) => event.type === "task.created")).toBeFalsy();

  await proposals.getByRole("button", { name: "Create Task" }).click();
  await expect.poll(async () => {
    const snapshot = await apiJson<Snapshot>(request, `/api/projects/${encodeURIComponent(id)}/snapshot`);
    return snapshotHasTitle(snapshot, title);
  }, { timeout: 30_000 }).toBeTruthy();

  const acceptedEvents = await waitForEvents(request, id, cursor, (items) => {
    const accepted = items.find((event) => (
      event.type === "runtime.action.accepted"
      && event.payload?.requested_action === "create-task"
    ));
    return Boolean(accepted && items.some((event) => (
      event.type === "task.created" && event.causation_id === accepted.causation_id
    )));
  });
  const proposalSeq = acceptedEvents.find((event) => (
    event.type === "kanban.agent.action.proposed" && eventPayloadContains(event, title)
  ))?.seq ?? 0;
  const accepted = acceptedEvents.find((event) => (
    event.type === "runtime.action.accepted"
    && event.payload?.requested_action === "create-task"
  ));
  const acceptedSeq = accepted?.seq ?? 0;
  const createdSeq = acceptedEvents.find((event) => (
    event.type === "task.created" && event.causation_id === accepted?.causation_id
  ))?.seq ?? 0;
  expect(proposalSeq).toBeGreaterThan(0);
  expect(acceptedSeq).toBeGreaterThan(proposalSeq);
  expect(createdSeq).toBeGreaterThan(acceptedSeq);

  const pendingAfter = await apiJson<{ items?: unknown[] }>(
    request,
    `/api/projects/${encodeURIComponent(id)}/kanban-agent/pending-proposals`,
  );
  expect(JSON.stringify(pendingAfter.items ?? [])).not.toContain(title);

  await page.reload();
  await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.locator(".agent-session")).toContainText(marker, { timeout: 30_000 });
});

test("KBA-04 two turns resume one provider session and survive reload", async ({ page, request }) => {
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const markerA = `KBA_RESUME_A_${Date.now().toString(36)}`;
  const markerB = `KBA_RESUME_B_${Date.now().toString(36)}`;

  await openKanbanAgent(page, id);
  await sendMessage(page, `${markerA} remember this marker without changing state`);
  await expect(page.locator(".agent-session")).toContainText(markerA, { timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Send message" })).toBeVisible({ timeout: 30_000 });
  await sendMessage(page, `${markerB} continue the same thread without changing state`);
  await expect(page.locator(".agent-session")).toContainText(markerB, { timeout: 30_000 });

  const events = await waitForEvents(request, id, cursor, (items) => (
    eventChainForMarker(items, markerA).some((event) => event.type === "kanban.agent.turn.completed")
    && eventChainForMarker(items, markerB).some((event) => event.type === "kanban.agent.turn.completed")
  ));
  const completedA = eventChainForMarker(events, markerA).find((event) => event.type === "kanban.agent.turn.completed");
  const completedB = eventChainForMarker(events, markerB).find((event) => event.type === "kanban.agent.turn.completed");
  expect(completedA?.payload?.thread_id).toBe(completedB?.payload?.thread_id);
  expect(completedA?.payload?.provider_session_id).toBe(completedB?.payload?.provider_session_id);

  await page.reload();
  await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.locator(".agent-session")).toContainText(markerA, { timeout: 30_000 });
  await expect(page.locator(".agent-session")).toContainText(markerB, { timeout: 30_000 });
});

test("KBA-05 interrupt cancels only the active run and the next turn recovers", async ({ page, request }) => {
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const holdMarker = `KBA_HOLD_${Date.now().toString(36)}`;
  const recoveryMarker = `KBA_RECOVER_${Date.now().toString(36)}`;

  await openKanbanAgent(page, id);
  await sendMessage(page, `${holdMarker} hold this run until interrupted`);
  await expect(page.locator(".agent-session")).toContainText(holdMarker, { timeout: 15_000 });
  const interrupt = page.getByRole("button", { name: "Interrupt" });
  await expect(interrupt).toBeVisible({ timeout: 15_000 });
  await interrupt.click();

  await waitForEvents(request, id, cursor, (items) => (
    items.some((event) => event.type === "agent.session.run.cancelled")
  ));
  await expect(page.getByRole("button", { name: "Send message" })).toBeVisible({ timeout: 30_000 });

  await sendMessage(page, `${recoveryMarker} confirm the next turn still works`);
  await expect(page.locator(".agent-session")).toContainText(recoveryMarker, { timeout: 30_000 });
  const events = await waitForEvents(request, id, cursor, (items) => (
    eventChainForMarker(items, recoveryMarker).some((event) => event.type === "kanban.agent.turn.completed")
  ));
  expect(events.filter((event) => event.type === "agent.session.run.cancelled")).toHaveLength(1);
});
