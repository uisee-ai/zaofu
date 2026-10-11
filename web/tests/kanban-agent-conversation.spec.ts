import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const evidenceDir = process.env.ZF_PLAYWRIGHT_EVIDENCE_DIR ?? "";

type EventItem = {
  seq: number;
  id: string;
  type: string;
  task_id?: string | null;
  causation_id?: string | null;
  correlation_id?: string | null;
  payload?: Record<string, unknown>;
};

type EventPage = {
  items?: EventItem[];
  current_seq?: number;
};

type EventDetail = {
  event?: EventItem;
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

async function hydrateEvent(
  request: APIRequestContext,
  id: string,
  event: EventItem,
): Promise<EventItem> {
  const detail = await apiJson<EventDetail>(
    request,
    `/api/projects/${encodeURIComponent(id)}/events/${encodeURIComponent(event.id)}`,
  );
  expect(detail.event).toBeDefined();
  return detail.event as EventItem;
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
  const dialog = page.getByRole("dialog", { name: "Kanban Agent" });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("radiogroup", { name: "Kanban Agent permission profile" }),
  ).toHaveCount(0);
  await expect(dialog.getByText("Kanban Agent", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Agent backend: Claude/ })).toBeVisible();
}

async function sendMessage(page: Page, message: string): Promise<void> {
  const input = page.locator("textarea.headless-input");
  await input.fill(message);
  await page.getByRole("button", { name: "Send message" }).click();
}

async function captureAgent(page: Page, name: string): Promise<void> {
  if (!evidenceDir) return;
  await page.getByRole("dialog", { name: "Kanban Agent" }).screenshot({
    path: `${evidenceDir}/${name}.png`,
  });
}

async function capturePage(page: Page, name: string): Promise<void> {
  if (!evidenceDir) return;
  await page.screenshot({
    fullPage: true,
    path: `${evidenceDir}/${name}.png`,
  });
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
  await expect(page.getByRole("button", { name: "Stop generating" })).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".agent-session")).toContainText(marker, { timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Stop generating" })).toBeVisible();
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
  expect(types).not.toContain("operator.action.proposed");
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
  await expect(proposals).toContainText("Create task", { timeout: 30_000 });
  await expect(proposals).toContainText(title);
  await expect(page.locator(".agent-text-part").filter({ hasText: '{"action_proposal"' })).toHaveCount(0);
  await captureAgent(page, "01-create-task-confirmation");

  const pendingBefore = await apiJson<{ items?: unknown[] }>(
    request,
    `/api/projects/${encodeURIComponent(id)}/kanban-agent/pending-proposals`,
  );
  expect(JSON.stringify(pendingBefore.items ?? [])).toContain(title);
  const snapshotBefore = await apiJson<Snapshot>(request, `/api/projects/${encodeURIComponent(id)}/snapshot`);
  expect(snapshotHasTitle(snapshotBefore, title)).toBeFalsy();
  const beforeEvents = await eventsAfter(request, id, cursor);
  expect(beforeEvents.some((event) => event.type === "task.created")).toBeFalsy();

  await proposals.getByRole("button", { name: "Create task" }).click();
  await expect.poll(async () => {
    const snapshot = await apiJson<Snapshot>(request, `/api/projects/${encodeURIComponent(id)}/snapshot`);
    return snapshotHasTitle(snapshot, title);
  }, { timeout: 30_000 }).toBeTruthy();
  await expect(proposals).toContainText("Task created", { timeout: 30_000 });
  await captureAgent(page, "02-create-task-approved");

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
    event.type === "operator.action.proposed" && eventPayloadContains(event, title)
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

test("KBA-03B invalid semantic evidence remains visible and non-executable", async ({ page, request }) => {
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const marker = `KBA_INVALID_${Date.now().toString(36)}`;
  const title = `Invalid Kanban Agent proposal ${marker}`;

  await openKanbanAgent(page, id);
  await sendMessage(
    page,
    `${marker} explain proposal parsing without creating anything`,
  );

  const approveCard = page.locator(".agent-stack-card.approve").filter({
    hasText: title,
  }).last();
  await expect(approveCard).toBeVisible({ timeout: 30_000 });
  await expect(approveCard).toContainText(
    "intent.source_quote must occur verbatim in the user semantic context",
  );
  await expect(
    approveCard.getByRole("button", { name: "Create task" }),
  ).toBeDisabled();
  await captureAgent(page, "03-invalid-semantic-evidence");

  const pending = await apiJson<{ items?: Array<Record<string, unknown>> }>(
    request,
    `/api/projects/${encodeURIComponent(id)}/kanban-agent/pending-proposals`,
  );
  const invalid = (pending.items ?? []).find((item) => (
    String(item.title ?? "") === title
  ));
  expect(invalid?.valid).toBe(false);
  expect(String(invalid?.validation_error ?? "")).toContain(
    "intent.source_quote must occur verbatim",
  );

  const snapshot = await apiJson<Snapshot>(
    request,
    `/api/projects/${encodeURIComponent(id)}/snapshot`,
  );
  expect(snapshotHasTitle(snapshot, title)).toBeFalsy();
  const events = await eventsAfter(request, id, cursor);
  expect(events.some((event) => (
    event.type === "task.created" && eventPayloadContains(event, title)
  ))).toBeFalsy();
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

test("KBA-PLAN Plan choice resumes into Approve and survives reload", async ({ page, request }) => {
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const marker = `KBA_PLAN_${Date.now().toString(36)}`;
  const title = `Kanban Plan delivery ${marker}`;

  await openKanbanAgent(page, id);
  await sendMessage(
    page,
    `${marker} create a task, but ask which delivery route to use first`,
  );
  const planCard = page.locator(".agent-stack-card.plan").filter({ hasText: marker }).last();
  await expect(planCard).toBeVisible({ timeout: 30_000 });
  await expect(planCard).toContainText("Question");
  await expect(planCard).toContainText("Direct");
  await expect(planCard.locator(".agent-plan-recommended")).toHaveText("Recommended");
  await expect(planCard).toContainText("Research");
  await expect(planCard).toContainText("Customize");
  await expect(planCard.getByRole("button", { name: "Chat about" })).toBeEnabled();
  await expect(planCard.getByRole("button", { name: "Continue" })).toBeDisabled();

  await planCard.getByRole("button", { name: "Chat about" }).click();
  // Simulate a stale browser preference that differs from the provider-bound
  // Plan. The discussion turn must still use the Plan's original backend.
  await page.getByRole("button", { name: "Agent backend: Claude" }).click();
  const codexOption = page.getByRole("option").filter({ hasText: "Codex" }).first();
  await codexOption.evaluate((node) => {
    const button = node as HTMLButtonElement;
    button.disabled = false;
    button.click();
  });
  await expect(page.getByRole("button", { name: "Agent backend: Codex" })).toBeVisible();
  const discussionMarker = `KBA_PLAN_DISCUSS_BACKEND_${Date.now().toString(36)}`;
  const discussionCursor = await eventCursor(request, id);
  const discussionComposer = page.getByPlaceholder("Ask about Delivery route...");
  await discussionComposer.fill(`${discussionMarker} compare the routes without answering`);
  await page.getByRole("button", { name: "Send message" }).click();
  const discussionEvents = await waitForEvents(
    request,
    id,
    discussionCursor,
    (items) => eventChainForMarker(items, discussionMarker).some(
      (event) => event.type === "kanban.agent.turn.completed",
    ),
  );
  const discussionChain = eventChainForMarker(discussionEvents, discussionMarker);
  const discussionUser = await hydrateEvent(
    request,
    id,
    discussionChain.find((event) => event.type === "user.message") as EventItem,
  );
  const discussionRequest = discussionUser.payload?.request as
    | Record<string, unknown>
    | undefined;
  expect(discussionRequest?.backend).toBe("claude-headless");
  expect(discussionChain.some(
    (event) => event.type === "kanban.agent.plan.answered",
  )).toBeFalsy();
  expect(discussionChain.some(
    (event) => event.type === "operator.action.proposed",
  )).toBeFalsy();

  await planCard.getByLabel("Direct (Recommended)").check();
  await planCard.getByRole("button", { name: "Continue" }).click();
  await expect(planCard).toContainText("Plan summary", { timeout: 30_000 });
  await expect(planCard).toContainText("Plan complete");
  await expect(planCard).toContainText("Direct (Recommended)");
  const approveCard = page.locator(".agent-stack-card.approve").filter({ hasText: title }).last();
  await expect(approveCard).toBeVisible({ timeout: 30_000 });
  await expect(approveCard).toContainText("Confirmation");
  await expect(approveCard.getByRole("button", { name: "Create task" })).toBeEnabled();
  await expect(approveCard.getByRole("button", { name: "Edit" })).toBeEnabled();
  await expect(approveCard.getByRole("button", { name: "Cancel" })).toBeEnabled();
  await expect(
    page.locator(".agent-user-message").filter({ hasText: "Plan: Delivery route" }),
  ).toHaveCount(0);

  await page.reload();
  await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const refreshedPlan = page.locator(".agent-stack-card.plan").filter({ hasText: marker }).last();
  await expect(refreshedPlan).toContainText("Direct (Recommended)", { timeout: 30_000 });
  await expect(refreshedPlan.getByRole("button", { name: "Continue" })).toHaveCount(0);
  const refreshedApprove = page.locator(".agent-stack-card.approve").filter({ hasText: title }).last();
  await expect(refreshedApprove.getByRole("button", { name: "Create task" })).toBeEnabled();
  await refreshedApprove.getByRole("button", { name: "Create task" }).click();

  await expect.poll(async () => {
    const snapshot = await apiJson<Snapshot>(
      request,
      `/api/projects/${encodeURIComponent(id)}/snapshot`,
    );
    return snapshotHasTitle(snapshot, title);
  }, { timeout: 30_000 }).toBeTruthy();
  await expect(refreshedApprove).toContainText("Task created", { timeout: 30_000 });
  await expect(refreshedApprove.getByRole("button", { name: "Create task" })).toHaveCount(0);

  const events = await waitForEvents(request, id, cursor, (items) => (
    items.some((event) => event.type === "kanban.agent.plan.answered")
    && (() => {
      const proposal = items.find((event) => (
        event.type === "operator.action.proposed"
        && eventPayloadContains(event, title)
      ));
      return Boolean(proposal && items.some((event) => (
        event.type === "task.created"
        && event.payload?.proposal_event_id === proposal.id
      )));
    })()
  ));
  expect(events.filter((event) => event.type === "kanban.agent.plan.requested")).toHaveLength(1);
  expect(events.filter((event) => event.type === "kanban.agent.plan.answered")).toHaveLength(1);
  const planAnswer = events.find((event) => event.type === "kanban.agent.plan.answered");
  expect(planAnswer?.payload?.answer).toBe("Direct (Recommended)");
  const completedTurns = events.filter((event) => event.type === "kanban.agent.turn.completed");
  expect(completedTurns).toHaveLength(3);
  expect(new Set(completedTurns.map(
    (event) => event.payload?.provider_session_id,
  )).size).toBe(1);
});

test("KBA-PLAN multiple clarification questions submit atomically", async ({
  page,
  request,
}) => {
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const marker = `KBA_MULTI_PLAN_${Date.now().toString(36)}`;

  await openKanbanAgent(page, id);
  await sendMessage(
    page,
    `${marker} clarify the route and evidence depth before continuing`,
  );
  const initialPlan = page.locator(".agent-stack-card.plan").filter({
    hasText: marker,
  }).last();
  await expect(initialPlan).toBeVisible({ timeout: 30_000 });
  const planRequestId = await initialPlan.getAttribute("data-plan-request-id");
  expect(planRequestId).toBeTruthy();
  const plan = page.locator(
    `.agent-stack-card.plan[data-plan-request-id="${planRequestId}"]`,
  ).last();
  await expect(plan).toContainText("1 of 2");
  await expect(plan).not.toContainText("2 of 2");
  await expect(plan.locator(".agent-plan-recommended")).toHaveCount(1);
  await captureAgent(page, "08-multi-question-plan");
  await plan.getByLabel("Direct").check();
  const nextButton = plan.getByRole("button", { name: "Next question" });
  await expect(nextButton).toBeEnabled();
  await nextButton.click();
  await expect(plan).toContainText("2 of 2");
  await plan.getByLabel("Focused").check();
  const continueButton = plan.getByRole("button", { name: "Continue" });
  await expect(continueButton).toBeEnabled();

  await continueButton.click();
  await expect(plan).toContainText("Plan summary", { timeout: 30_000 });
  await expect(plan).toContainText("Direct");
  await expect(plan).toContainText("Focused");
  await captureAgent(page, "09-multi-question-summary");
  const events = await waitForEvents(request, id, cursor, (items) => (
    items.some((event) => event.type === "kanban.agent.plan.answered")
    && items.some((event) => event.type === "kanban.agent.turn.completed")
  ));
  const answered = await hydrateEvent(
    request,
    id,
    events.find(
      (event) => event.type === "kanban.agent.plan.answered",
    ) as EventItem,
  );
  expect(answered.payload?.answers).toEqual([
    { question_id: "route", option_id: "direct", answer: "Direct" },
    { question_id: "evidence", option_id: "focused", answer: "Focused" },
  ]);
  expect(events.some(
    (event) => event.type === "operator.action.proposed",
  )).toBeFalsy();
});

test("KBA-CHANNEL Channel setup applies directly without a Workflow invoke", async ({
  page,
  request,
}) => {
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const channelRequest = (
    "Create a focused collaboration Channel for an API authentication review and start it."
  );
  const channelDiscussionSeed = "Review the API authentication change.";

  await page.setViewportSize({ width: 1440, height: 960 });
  await openKanbanAgent(page, id);
  await page.getByRole("button", { name: "New Kanban Agent chat" }).click();
  await page.getByRole("button", { name: "Fullscreen Kanban Agent" }).click();
  const dialog = page.getByRole("dialog", { name: "Kanban Agent" });
  await expect(dialog.getByText("Kanban Agent", { exact: true })).toBeVisible();
  await sendMessage(
    page,
    channelRequest,
  );
  const plan = page.locator(".agent-stack-card.plan").filter({ hasText: "Channel setup" }).last();
  await expect(plan).toBeVisible({ timeout: 30_000 });
  await expect(plan).toContainText("Quick change");
  await expect(plan.locator(".agent-plan-recommended")).toHaveText("Recommended");
  await expect(plan).toContainText("3 members");
  await expect(plan).toContainText("4 rounds");
  await expect(plan).toContainText("conversation");
  await expect(plan).toContainText("Initial routing: 1 responder");
  await expect(plan).toContainText("Blind first pass: 4-way fanout");
  await expect(plan).not.toContainText("Customize");
  await expect(plan.getByRole("button", { name: "Chat about" })).toBeEnabled();
  await plan.getByLabel("Quick change (Recommended)").check();
  await captureAgent(page, "03-channel-setup-plan");
  await plan.getByRole("button", { name: "Create & start" }).click();

  const events = await waitForEvents(request, id, cursor, (items) => (
    items.some((event) => event.type === "channel.created")
    && items.some((event) => event.type === "channel.discussion.started")
    && items.some((event) => event.type === "kanban.agent.plan.answered")
  ));
  const answered = await hydrateEvent(
    request,
    id,
    events.find(
      (event) => event.type === "kanban.agent.plan.answered",
    ) as EventItem,
  );
  expect(answered.payload?.applied_action).toBe("channel-create-and-start");
  expect(events.some((event) => event.type === "workflow.invoke.requested")).toBeFalsy();
  const planSummary = page.locator(".agent-stack-card.plan").filter({
    hasText: "Plan summary",
  }).last();
  await expect(planSummary).toContainText("Plan applied");
  await expect(planSummary).toContainText("Quick change (Recommended)");
  await captureAgent(page, "04-channel-plan-applied");

  const createdSummary = events.find((event) => event.type === "channel.created");
  expect(createdSummary).toBeDefined();
  const created = await hydrateEvent(request, id, createdSummary as EventItem);
  const channelId = String(created.payload?.channel_id ?? "");
  expect(channelId).not.toBe("");

  await page.getByRole("button", { name: "Minimize Kanban Agent" }).click();
  const channelButton = page.locator(".channel-nav-button").filter({
    hasText: "API authentication review",
  }).last();
  await expect(channelButton).toContainText("3", { timeout: 30_000 });
  await channelButton.click();
  const channelPage = page.locator(".channel-page");
  await expect(channelPage).toContainText("API authentication review", { timeout: 30_000 });
  await expect(channelPage).toContainText(channelDiscussionSeed, { timeout: 30_000 });
  await expect(channelPage).toContainText(channelId);
  await page.getByTitle("Members").click();
  const memberDrawer = page.locator(".channel-drawer");
  await expect(memberDrawer).toContainText("Members");
  await expect(memberDrawer).toContainText("3");
  await expect(memberDrawer).toContainText("tech_leader");
  await expect(memberDrawer).toContainText("dev_reviewer");
  await expect(memberDrawer).toContainText("qa_analyst");
  await capturePage(page, "05-channel-members");
});

test("KBA-WORKFLOW Create Task leads to Plan, then Approve, then invoke", async ({
  page,
  request,
}) => {
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const marker = `KBA_TASK_WORKFLOW_${Date.now().toString(36)}`;
  const title = `Task workflow ${marker}`;

  await openKanbanAgent(page, id);
  await sendMessage(
    page,
    `${marker} create a Task and recommend a workflow before starting it`,
  );
  const create = page.locator(".agent-stack-card.approve").filter({
    hasText: title,
  }).last();
  await expect(create).toBeVisible({ timeout: 30_000 });
  await expect(create.getByRole("button", { name: "Create task" })).toBeEnabled();
  await create.getByRole("button", { name: "Create task" }).click();

  const taskAndPlanEvents = await waitForEvents(
    request,
    id,
    cursor,
    (items) => (
      items.some((event) => event.type === "task.created")
      && items.some(
        (event) => event.type === "kanban.agent.plan.requested",
      )
    ),
  );
  const created = taskAndPlanEvents.find(
    (event) => event.type === "task.created",
  );
  expect(created?.task_id).toBeTruthy();
  const requestedPlan = taskAndPlanEvents.find(
    (event) => event.type === "kanban.agent.plan.requested",
  );
  expect(requestedPlan?.task_id).toBe(created?.task_id);
  expect(
    taskAndPlanEvents.some((event) => event.type === "workflow.invoke.requested"),
  ).toBeFalsy();

  const plan = page.locator(".agent-stack-card.plan").filter({
    hasText: marker,
  }).last();
  await expect(plan).toBeVisible({ timeout: 30_000 });
  await expect(plan).toContainText("Research first");
  await expect(plan.locator(".agent-plan-recommended")).toHaveText("Recommended");
  await expect(plan).toContainText("research / fanout reader");
  await expect(plan).toContainText("Output: research report");
  await expect(plan).toContainText("Customize");
  const chatAbout = plan.getByRole("button", { name: "Chat about" });
  await expect(chatAbout).toBeEnabled();
  await chatAbout.click();
  const discussion = page.locator(".headless-plan-discussion");
  await expect(discussion).toContainText("Workflow route");
  const composer = page.getByPlaceholder("Ask about Workflow route...");
  await expect(composer).toHaveValue("");
  await expect(plan.getByRole("button", { name: "Continue" })).toBeVisible();
  const discussionMarker = `KBA_PLAN_DISCUSS_${Date.now().toString(36)}`;
  const discussionCursor = await eventCursor(request, id);
  await composer.fill(`${discussionMarker} compare the recommended route without answering the Plan`);
  await page.getByRole("button", { name: "Send message" }).click();
  const discussionEvents = await waitForEvents(
    request,
    id,
    discussionCursor,
    (items) => eventChainForMarker(items, discussionMarker).some(
      (event) => event.type === "kanban.agent.turn.completed",
    ),
  );
  const discussionChain = eventChainForMarker(
    discussionEvents,
    discussionMarker,
  );
  const discussionUser = await hydrateEvent(
    request,
    id,
    discussionChain.find(
      (event) => event.type === "user.message",
    ) as EventItem,
  );
  const discussionRequest = discussionUser.payload?.request as
    | Record<string, unknown>
    | undefined;
  const discussionBinding = discussionRequest?.plan_discussion as
    | Record<string, unknown>
    | undefined;
  expect(discussionBinding?.request_event_id).toBe(requestedPlan?.id);
  expect(discussionChain.some(
    (event) => event.type === "kanban.agent.plan.answered",
  )).toBeFalsy();
  expect(discussionChain.some(
    (event) => event.type === "operator.action.proposed",
  )).toBeFalsy();
  await captureAgent(page, "10-workflow-chat-about");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(plan).toBeVisible();
  const mobileOverflow = await plan.evaluate((element) => (
    element.scrollWidth > element.clientWidth + 1
  ));
  expect(mobileOverflow).toBeFalsy();
  const mobileHeader = page.getByRole("dialog", { name: "Kanban Agent" }).locator(
    ".agent-shell-header",
  );
  await expect(mobileHeader.getByText("Kanban Agent", { exact: true })).toBeVisible();
  const mobileHeaderOverflow = await mobileHeader.evaluate((element) => (
    element.scrollWidth > element.clientWidth + 1
  ));
  expect(mobileHeaderOverflow).toBeFalsy();
  await captureAgent(page, "04-task-workflow-plan-mobile");
  await page.setViewportSize({ width: 1440, height: 900 });

  await plan.getByLabel("Research first (Recommended)").check();
  await captureAgent(page, "05-task-workflow-plan");
  await plan.getByRole("button", { name: "Continue" }).click();
  await expect(plan).toContainText("Plan summary", { timeout: 30_000 });
  await expect(plan).toContainText("Ready for confirmation");
  await expect(plan).toContainText("Research first (Recommended)");
  await captureAgent(page, "11-task-workflow-plan-summary");
  const approve = page.locator(".agent-stack-card.approve").filter({
    hasText: "research:fixed",
  }).last();
  await expect(approve).toBeVisible({ timeout: 30_000 });
  await expect(approve).toContainText("Confirmation");
  await expect(approve).toContainText(String(created?.task_id));
  await expect(
    approve.getByRole("button", { name: "Start workflow" }),
  ).toBeEnabled();

  const beforeApprove = await eventsAfter(request, id, cursor);
  expect(
    beforeApprove.some((event) => event.type === "workflow.invoke.requested"),
  ).toBeFalsy();
  await captureAgent(page, "06-task-workflow-confirmation");
  await approve.getByRole("button", { name: "Start workflow" }).click();

  const completed = await waitForEvents(request, id, cursor, (items) => (
    items.some((event) => (
      event.type === "workflow.invoke.requested"
      && event.task_id === created?.task_id
    ))
    && items.some(
      (event) => event.type === "operator.action.resolved",
    )
  ));
  const invoke = await hydrateEvent(
    request,
    id,
    completed.find((event) => (
      event.type === "workflow.invoke.requested"
      && event.task_id === created?.task_id
    )) as EventItem,
  );
  expect(invoke.payload?.pattern_id).toBe("research-fanout");
  expect(completed.filter((event) => (
    event.type === "workflow.invoke.requested"
    && event.task_id === created?.task_id
  ))).toHaveLength(1);
  expect(completed.filter((event) => (
    event.type === "operator.action.proposed"
    && eventPayloadContains(event, "workflow-start")
  ))).toHaveLength(1);
  await expect(approve).toContainText("Workflow start requested", {
    timeout: 30_000,
  });
  await captureAgent(page, "07-task-workflow-started");
});

test("KBA-05 stop cancels only the active run and the next turn recovers", async ({ page, request }) => {
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const holdMarker = `KBA_HOLD_${Date.now().toString(36)}`;
  const recoveryMarker = `KBA_RECOVER_${Date.now().toString(36)}`;

  await openKanbanAgent(page, id);
  await sendMessage(page, `${holdMarker} hold this run until interrupted`);
  await expect(page.locator(".agent-session")).toContainText(holdMarker, { timeout: 15_000 });
  const stop = page.getByRole("button", { name: "Stop generating" });
  await expect(stop).toBeVisible({ timeout: 15_000 });
  await stop.click();

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
