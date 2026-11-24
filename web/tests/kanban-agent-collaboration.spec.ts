import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const taskId = process.env.ZF_DOC156_TASK_ID ?? "";
const channelId = process.env.ZF_DOC156_CHANNEL_ID ?? "";
const requestId = process.env.ZF_DOC156_REQUEST_ID ?? "";
const evidenceDir = process.env.ZF_PLAYWRIGHT_EVIDENCE_DIR ?? "";

type EventItem = {
  seq: number;
  id: string;
  type: string;
  actor?: string;
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
  tasks?: Array<Record<string, unknown>>;
};

test.describe.configure({ mode: "serial", timeout: 240_000 });

async function apiJson<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(path);
  const body = await response.json().catch(() => ({}));
  expect(response.ok(), `${path}: ${response.status()} ${JSON.stringify(body)}`).toBeTruthy();
  return body as T;
}

async function projectId(request: APIRequestContext): Promise<string> {
  const snapshot = await apiJson<Snapshot>(request, "/api/snapshot");
  const id = String(snapshot.project?.project_id ?? "");
  expect(id, "isolated Doc 156 project id").not.toBe("");
  return id;
}

async function eventCursor(request: APIRequestContext, id: string): Promise<number> {
  const page = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(id)}/events?limit=1`,
  );
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
  timeout = 60_000,
): Promise<EventItem[]> {
  let latest: EventItem[] = [];
  await expect.poll(async () => {
    latest = await eventsAfter(request, id, cursor);
    return predicate(latest);
  }, { timeout, intervals: [100, 250, 500, 1000] }).toBeTruthy();
  return latest;
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
  expect(detail.event, `hydrated event ${event.id}`).toBeDefined();
  return detail.event as EventItem;
}

async function openAgent(page: Page, id: string): Promise<void> {
  await page.addInitScript(({ actionToken }) => {
    window.localStorage.clear();
    window.localStorage.setItem("zf.operatorBackend", "claude-headless");
    window.localStorage.setItem("zf.webActionToken", actionToken);
  }, { actionToken: token });
  await page.goto(`/?project=${encodeURIComponent(id)}`);
  await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Open zaofu" }).click();
  await expect(page.getByRole("dialog", { name: "zaofu" })).toBeVisible();
}

async function reopenAgent(page: Page): Promise<void> {
  await page.reload();
  await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 90_000 });
  const dialog = page.getByRole("dialog", { name: "zaofu" });
  if (!(await dialog.isVisible().catch(() => false))) {
    await page.getByRole("button", { name: "Open zaofu" }).click();
  }
  await expect(dialog).toBeVisible();
}

async function sendProposal(
  page: Page,
  message: string,
  previewText: string,
  actionLabel: string,
): Promise<void> {
  const dialog = page.getByRole("dialog", { name: "zaofu" });
  await expect(dialog.locator(".agent-state-pill")).toHaveText("active", {
    timeout: 15_000,
  });
  const input = dialog.locator("textarea.headless-input");
  await expect(input).toBeVisible({ timeout: 15_000 });
  await input.fill(message);
  await dialog.getByRole("button", { name: "Send message" }).click();
  const card = page.locator(".agent-stack-card.proposal").filter({ hasText: previewText }).last();
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card.getByRole("button", { name: actionLabel })).toBeEnabled();
}

async function approveProposal(page: Page, previewText: string, actionLabel: string): Promise<void> {
  const card = page.locator(".agent-stack-card.proposal").filter({ hasText: previewText }).last();
  await expect(card).toBeVisible({ timeout: 30_000 });
  await card.getByRole("button", { name: actionLabel }).click();
}

async function captureAgent(page: Page, name: string): Promise<void> {
  if (!evidenceDir) return;
  await page.getByRole("dialog", { name: "zaofu" }).screenshot({
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

function payloadText(event: EventItem): string {
  return JSON.stringify(event.payload ?? {});
}

test("Doc 156 Kanban Agent closes Channel, Research, adoption, and live Workflow start", async ({
  page,
  request,
}) => {
  expect(token).not.toBe("");
  expect(taskId).not.toBe("");
  expect(channelId).not.toBe("");
  expect(requestId).not.toBe("");

  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await openAgent(page, id);

  const channelRequirement = (
    "DOC156_CHANNEL_SETUP review the delivery design, identify implementation "
    + "and test risks, and produce a structured recommendation."
  );
  const setupDialog = page.getByRole("dialog", { name: "zaofu" });
  const agentInput = setupDialog.locator("textarea.headless-input");
  await agentInput.fill(channelRequirement);
  await setupDialog.getByRole("button", { name: "Send message" }).click();
  const channelPlan = page.locator(".agent-stack-card.plan").filter({
    hasText: "DOC156_CHANNEL_SETUP",
  }).last();
  await expect(channelPlan).toBeVisible({ timeout: 30_000 });
  await expect(channelPlan).toContainText("Quick change");
  await expect(channelPlan.locator(".agent-plan-recommended")).toHaveText(
    "Recommended",
  );
  await expect(channelPlan).toContainText("tech leader, dev reviewer, qa analyst");
  await expect(channelPlan).toContainText("3 members");
  await expect(channelPlan).toContainText("8 rounds");
  await expect(channelPlan).toContainText("Architecture review");
  await expect(channelPlan).not.toContainText("Other");
  await expect(
    page.locator(".agent-stack-card.approve").filter({ hasText: "quick-change" }),
  ).toHaveCount(0);
  await channelPlan.getByLabel("Quick change (Recommended)").check();
  await expect(
    channelPlan.getByRole("button", { name: "Create & start" }),
  ).toBeEnabled();
  await captureAgent(page, "03-channel-setup-plan");

  await channelPlan.getByRole("button", { name: "Create & start" }).click();
  const channelEvents = await waitForEvents(request, id, cursor, (events) => (
    events.some((event) => (
      event.type === "channel.created"
      && event.payload?.channel_id === channelId
      && (event.payload?.scope as Record<string, unknown> | undefined)?.template !== undefined
    ))
    && events.some((event) => (
      event.type === "channel.discussion.started"
      && event.payload?.channel_id === channelId
    ))
    && events.some((event) => (
      event.type === "channel.agent.reply.completed"
      && event.payload?.channel_id === channelId
    ))
  ), 90_000);
  expect(channelEvents.some((event) => (
    event.type === "channel.synthesis.proposed"
    && event.payload?.channel_id === channelId
  ))).toBe(false);
  const seededMessages = await Promise.all(channelEvents
    .filter((event) => (
      event.type === "channel.message.posted"
      && event.payload?.channel_id === channelId
    ))
    .map((event) => hydrateEvent(request, id, event)));
  const seededRequirement = seededMessages.find(
    (event) => payloadText(event).includes("DOC156_CHANNEL_SETUP"),
  );
  expect(seededRequirement).toBeDefined();
  const planAnswers = await Promise.all(channelEvents
    .filter((event) => event.type === "kanban.agent.plan.answered")
    .map((event) => hydrateEvent(request, id, event)));
  const planAnswer = planAnswers.find((event) => (
    event.payload?.applied_action === "channel-create-and-start"
  ));
  expect(planAnswer).toBeDefined();
  await expect(
    page.locator(".agent-stack-card.approve").filter({ hasText: "quick-change" }),
  ).toHaveCount(0);

  const channel = await apiJson<Record<string, unknown>>(
    request,
    `/api/channels/${encodeURIComponent(channelId)}`,
  );
  const channelText = JSON.stringify(channel);
  expect(channelText).toContain("quick-change");
  expect(channelText).toContain("tech_leader");
  expect(channelText).toContain("dev_reviewer");
  expect(channelText).toContain("qa_analyst");
  expect((channel.members as Array<Record<string, unknown>>).every(
    (member) => member.permission_profile === "read_only",
  )).toBe(true);
  expect((channel.members as Array<Record<string, unknown>>).find(
    (member) => member.member_id === "tech_leader",
  )?.permissions).toContain("propose_workflow");

  await page.getByRole("button", { name: "Minimize zaofu" }).click();
  await page.goto(
    `/?project=${encodeURIComponent(id)}&page=channels&channel=${encodeURIComponent(channelId)}`,
  );
  await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 90_000 });
  const channelPage = page.locator(".channel-page");
  await expect(channelPage).toContainText("Doc 156 live review", { timeout: 30_000 });
  await expect(channelPage).toContainText(channelId);
  await expect(channelPage).toContainText("DOC156_CHANNEL_SETUP", {
    timeout: 30_000,
  });

  await page.locator(".channel-tabs").getByRole("button", { name: "Details" }).click();
  const workflowRoles = page.locator(".channel-workspace-panel").filter({
    hasText: "Workflow Roles",
  });
  await expect(workflowRoles).toContainText("6 configured roles");
  await expect(workflowRoles).toContainText("source_researcher");
  await expect(workflowRoles).toContainText("synthesizer");
  await page.locator(".channel-tabs").getByRole("button", { name: "Chat" }).click();

  await page.getByTitle("Members").click();
  const memberDrawer = page.locator(".channel-drawer");
  await expect(memberDrawer).toContainText("tech_leader");
  const techLeaderMember = memberDrawer.locator(".channel-member-item").filter({
    hasText: "tech_leader",
  }).first();
  const techLeaderActions = techLeaderMember.locator('button[title^="Actions for "]');
  await expect(techLeaderActions).toBeVisible({ timeout: 15_000 });
  await techLeaderActions.click();
  const messageTechLeader = techLeaderMember.getByRole("button", { name: "Message" });
  await expect(messageTechLeader).toBeVisible({ timeout: 15_000 });
  await capturePage(page, "04-channel-member-selection");
  await messageTechLeader.click();
  await page.getByTitle("Close drawer").click();

  const composer = page.getByRole("textbox", {
    name: "Message Doc 156 live review",
  });
  await expect(composer).toContainText("@tech_leader");
  const channelChatMarker = "DOC156_MEMBER_CHAT";
  const channelChatCursor = await eventCursor(request, id);
  await composer.pressSequentially(
    `${channelChatMarker} review this change and reply in this channel.`,
  );
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(channelPage).toContainText(channelChatMarker, { timeout: 30_000 });
  await expect(channelPage).toContainText(
    "tech_leader received the channel request",
    { timeout: 60_000 },
  );
  const channelChatEvents = await waitForEvents(
    request,
    id,
    channelChatCursor,
    (events) => events.some((event) => (
      event.type === "channel.agent.reply.completed"
      && event.payload?.channel_id === channelId
      && event.payload?.thread_id === "main"
    )),
  );
  const channelChatReplySummary = channelChatEvents.find((event) => (
    event.type === "channel.agent.reply.completed"
    && event.payload?.channel_id === channelId
    && event.payload?.thread_id === "main"
  ));
  expect(channelChatReplySummary).toBeDefined();
  const channelChatReply = await hydrateEvent(
    request,
    id,
    channelChatReplySummary as EventItem,
  );
  expect(channelChatReply.payload?.target_member_id).toBe("tech_leader");

  const followupMarker = "DOC156_CHANNEL_CONTINUE";
  await composer.pressSequentially(
    `${followupMarker} narrow the recommendation without a manual mention.`,
  );
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(channelPage).toContainText(
    `tech_leader received the channel request: ${followupMarker}`,
    { timeout: 60_000 },
  );
  await expect.poll(async () => {
    const followedUpChannel = await apiJson<Record<string, unknown>>(
      request,
      `/api/channels/${encodeURIComponent(channelId)}`,
    );
    const followedUpChannelText = JSON.stringify(followedUpChannel);
    return (
      followedUpChannelText.includes(followupMarker)
      && followedUpChannelText.includes("default_responder")
    );
  }, { timeout: 30_000, intervals: [100, 250, 500] }).toBeTruthy();
  await expect(channelPage).toContainText(followupMarker, { timeout: 30_000 });
  await capturePage(page, "05-channel-chat");

  await page.locator(".channel-tabs").getByRole("button", { name: "Details" }).click();
  await page
    .locator(".channel-workspace-controls")
    .getByRole("button", { name: "Synthesize" })
    .click();
  const synthesisEvents = await waitForEvents(request, id, cursor, (events) => events.some((event) => (
    event.type === "channel.synthesis.proposed"
    && event.payload?.channel_id === channelId
  )), 90_000);

  await page.getByRole("button", { name: "Open zaofu" }).click();
  await expect(page.getByRole("dialog", { name: "zaofu" })).toBeVisible();
  const synthesisSummary = synthesisEvents.find((event) => (
    event.type === "channel.synthesis.proposed"
    && event.payload?.channel_id === channelId
  ));
  expect(synthesisSummary).toBeDefined();
  const synthesis = await hydrateEvent(request, id, synthesisSummary as EventItem);
  expect(synthesis.payload?.artifact_ref).toBeTruthy();
  expect(synthesis.payload?.artifact_digest).toBeTruthy();

  await sendProposal(
    page,
    "DOC156_RESEARCH_START run the fixed research fanout and wait for approval.",
    "Collect evidence for the Doc 156 delivery decision",
    "Start research",
  );
  const pendingBeforeReload = await apiJson<{ items?: unknown[] }>(
    request,
    `/api/projects/${encodeURIComponent(id)}/kanban-agent/pending-proposals`,
  );
  expect(JSON.stringify(pendingBeforeReload.items ?? [])).toContain("research-start");

  await reopenAgent(page);
  await expect(
    page.getByRole("dialog", { name: "zaofu" }).locator(".agent-session"),
  ).toContainText("DOC156_RESEARCH_START");
  await expect(
    page.locator(".agent-stack-card.proposal").filter({
      hasText: "Collect evidence for the Doc 156 delivery decision",
    }).last(),
  ).toBeVisible();
  await approveProposal(page, "Collect evidence for the Doc 156 delivery decision", "Start research");

  const researchEvents = await waitForEvents(request, id, cursor, (events) => events.some((event) => (
    event.type === "fanout.aggregate.completed"
    && event.payload?.stage_id === "research-fanout"
  )), 120_000);
  const aggregateSummary = researchEvents.find((event) => (
    event.type === "fanout.aggregate.completed"
    && event.payload?.stage_id === "research-fanout"
  ));
  expect(aggregateSummary).toBeDefined();
  const aggregate = await hydrateEvent(request, id, aggregateSummary as EventItem);
  expect(aggregate.payload?.research_artifact_ref).toBeTruthy();
  expect(aggregate.payload?.research_artifact_digest).toBeTruthy();
  const researchDispatches = researchEvents.filter((event) => (
    event.type === "fanout.child.dispatched"
    && event.payload?.stage_id === "research-fanout"
  ));
  expect(researchDispatches).toHaveLength(4);
  const resultEvents = await waitForEvents(request, id, cursor, (events) => events.some((event) => (
    event.type === "workflow.result.available"
    && event.payload?.request_id === requestId
  )));
  const resultSummary = resultEvents.find((event) => (
    event.type === "workflow.result.available"
    && event.payload?.request_id === requestId
  ));
  expect(resultSummary).toBeDefined();
  const researchResult = await hydrateEvent(request, id, resultSummary as EventItem);
  expect(researchResult.payload?.artifact_ref).toBe(
    aggregate.payload?.research_artifact_ref,
  );
  expect(researchResult.payload?.artifact_digest).toBe(
    aggregate.payload?.research_artifact_digest,
  );
  await page.getByRole("button", { name: "Minimize zaofu" }).click();
  const resultCard = channelPage
    .locator(".agent-stack-card.workflow-result")
    .filter({ hasText: String(researchResult.payload?.artifact_digest ?? "") })
    .last();
  await expect(resultCard).toBeVisible({ timeout: 30_000 });
  await expect(resultCard).toContainText("Research result");
  await expect(resultCard).toContainText(
    String(researchResult.payload?.artifact_ref ?? ""),
  );
  await expect(resultCard).toContainText(
    String(researchResult.payload?.artifact_digest ?? ""),
  );
  await expect(
    resultCard.getByRole("button", { name: "Adopt result" }),
  ).toBeEnabled();
  await capturePage(page, "06-research-result-returned");
  await page.getByRole("button", { name: "Open zaofu" }).click();
  await expect(page.getByRole("dialog", { name: "zaofu" })).toBeVisible();

  const adoption = {
    result_event_id: researchResult.id,
    task_id: String(researchResult.payload?.task_id ?? taskId),
    request_id: requestId,
    request_revision: Number(researchResult.payload?.request_revision ?? 0),
    workflow_run_id: String(researchResult.payload?.workflow_run_id ?? ""),
    terminal_event_id: String(researchResult.payload?.terminal_event_id ?? ""),
    artifact_ref: String(researchResult.payload?.artifact_ref ?? ""),
    artifact_digest: String(researchResult.payload?.artifact_digest ?? ""),
    summary: String(researchResult.payload?.summary ?? "Doc 156 browser research."),
    channel_id: channelId,
    thread_id: "main",
  };
  await sendProposal(
    page,
    `DOC156_ADOPT ${JSON.stringify(adoption)}`,
    adoption.artifact_digest,
    "Adopt research",
  );
  await approveProposal(page, adoption.artifact_digest, "Adopt research");
  const adoptionEvents = await waitForEvents(
    request,
    id,
    cursor,
    (events) => events.some((event) => event.type === "workflow.research.adopted"),
  );
  const adoptedSummary = adoptionEvents.find(
    (event) => event.type === "workflow.research.adopted",
  );
  expect(adoptedSummary).toBeDefined();
  const adopted = await hydrateEvent(request, id, adoptedSummary as EventItem);
  expect(adopted.payload?.request_id).toBe(requestId);
  expect(adopted.payload?.artifact_digest).toBe(adoption.artifact_digest);
  await page.getByRole("button", { name: "Minimize zaofu" }).click();
  await expect(resultCard).toContainText("Adopted", { timeout: 30_000 });
  await expect(
    resultCard.getByRole("button", { name: "Adopt result" }),
  ).toHaveCount(0);
  await capturePage(page, "06-research-result-adopted");
  await page.getByRole("button", { name: "Open zaofu" }).click();
  await expect(page.getByRole("dialog", { name: "zaofu" })).toBeVisible();

  const deliveryCursor = await eventCursor(request, id);
  const workflowMarker = "DOC156_WORKFLOW_PLAN";
  const dialog = page.getByRole("dialog", { name: "zaofu" });
  await expect(dialog.locator(".agent-state-pill")).toHaveText("active", {
    timeout: 15_000,
  });
  const input = dialog.locator("textarea.headless-input");
  await expect(input).toBeVisible({ timeout: 15_000 });
  await input.fill(
    `${workflowMarker} clarify which configured workflow to run before proposing it.`,
  );
  await dialog.getByRole("button", { name: "Send message" }).click();
  const workflowPlan = page.locator(".agent-stack-card.plan").filter({
    hasText: workflowMarker,
  }).last();
  await expect(workflowPlan).toBeVisible({ timeout: 30_000 });
  await expect(workflowPlan).toContainText("Delivery smoke");
  await expect(workflowPlan.locator(".agent-plan-recommended")).toHaveText(
    "Recommended",
  );
  await expect(workflowPlan).toContainText("Research fanout");
  await expect(workflowPlan).toContainText("Customize");
  await expect(
    page.locator(".agent-stack-card.approve").filter({ hasText: "delivery-smoke" }),
  ).toHaveCount(0);
  await captureAgent(page, "06-workflow-definition-plan");

  await workflowPlan.getByLabel("Delivery smoke (Recommended)").check();
  await workflowPlan.getByRole("button", { name: "Continue" }).click();
  const workflowInputPlan = page.locator(".agent-stack-card.plan").filter({
    hasText: "DOC156_WORKFLOW_INPUT",
  }).last();
  await expect(workflowInputPlan).toBeVisible({ timeout: 30_000 });
  await expect(workflowInputPlan).toContainText("Channel synthesis");
  await expect(workflowInputPlan.locator(".agent-plan-recommended")).toHaveText(
    "Recommended",
  );
  await expect(workflowInputPlan).toContainText("Task only");
  await expect(
    page.locator(".agent-stack-card.approve").filter({ hasText: "delivery-smoke" }),
  ).toHaveCount(0);
  await captureAgent(page, "07-workflow-input-plan");
  await workflowInputPlan.getByLabel(
    "Channel synthesis (Recommended)",
  ).check();
  await workflowInputPlan.getByRole("button", { name: "Continue" }).click();
  const workflowApprove = page.locator(".agent-stack-card.approve").filter({
    hasText: "delivery-smoke",
  }).last();
  await expect(workflowApprove).toBeVisible({ timeout: 30_000 });
  await expect(workflowApprove).toContainText("Confirmation");
  await expect(
    workflowApprove.getByRole("button", { name: "Start workflow" }),
  ).toBeEnabled();
  await captureAgent(page, "08-workflow-confirmation");
  await workflowApprove.getByRole("button", { name: "Start workflow" }).click();
  const deliveryEvents = await waitForEvents(request, id, deliveryCursor, (events) => (
    events.some((event) => event.type === "workflow.invoke.accepted")
    && events.some((event) => event.type === "fanout.started")
    && events.some((event) => event.type === "fanout.child.dispatched")
    && events.some((event) => event.type === "worker.state.changed")
  ), 90_000);
  const deliveryDetails = await Promise.all(deliveryEvents
    .filter((event) => [
      "workflow.invoke.accepted",
      "fanout.started",
      "fanout.child.dispatched",
      "worker.state.changed",
    ].includes(event.type))
    .map((event) => hydrateEvent(request, id, event)));
  const invoke = deliveryDetails.find((event) => (
    event.type === "workflow.invoke.accepted"
    && event.payload?.pattern_id === "delivery-smoke"
  ));
  const fanout = deliveryDetails.find((event) => (
    event.type === "fanout.started"
    && event.payload?.stage_id === "delivery-smoke"
  ));
  const dispatch = deliveryDetails.find((event) => (
    event.type === "fanout.child.dispatched"
    && event.payload?.stage_id === "delivery-smoke"
  ));
  const busy = deliveryDetails.find((event) => (
    event.type === "worker.state.changed"
    && event.payload?.instance_id === "delivery_worker"
    && event.payload?.to === "busy"
  ));
  expect(invoke).toBeDefined();
  expect(fanout).toBeDefined();
  expect(dispatch).toBeDefined();
  expect(busy).toBeDefined();
  expect(invoke?.payload?.pattern_id).toBe("delivery-smoke");
  expect(fanout?.payload?.pdd_id).toBe(taskId);
  expect(dispatch?.payload?.role_instance).toBe("delivery_worker");
  expect(dispatch?.payload?.run_id).toBeTruthy();
  expect(busy?.payload?.to).toBe("busy");
  const workflowPlanEvents = await eventsAfter(request, id, deliveryCursor);
  expect(
    workflowPlanEvents.filter((event) => event.type === "kanban.agent.plan.requested"),
  ).toHaveLength(2);
  const workflowPlanAnswers = workflowPlanEvents.filter(
    (event) => event.type === "kanban.agent.plan.answered",
  );
  expect(workflowPlanAnswers.map((event) => event.payload?.answer)).toEqual([
    "Delivery smoke (Recommended)",
    "Channel synthesis (Recommended)",
  ]);

  await waitForEvents(request, id, deliveryCursor, (events) => events.some((event) => (
    event.type === "codex.hook.user_prompt_submit"
    && event.actor === "delivery_worker"
    && Boolean(event.payload?.session_id)
  )), 120_000);

  const launchEvents = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(id)}/events?limit=500&type=worker.launch_artifact.written`,
  );
  const launchSummary = (launchEvents.items ?? []).find((event) => (
    event.payload?.instance_id === "delivery_worker"
    && event.payload?.backend === "codex"
  ));
  expect(launchSummary).toBeDefined();
  const launch = await hydrateEvent(request, id, launchSummary as EventItem);
  expect(launch.payload?.artifact_ref).toBeTruthy();

  const allEvents = await eventsAfter(request, id, cursor);
  const markers = [
    "DOC156_CHANNEL_SETUP",
    "DOC156_RESEARCH_START",
    "DOC156_ADOPT",
    workflowMarker,
  ];
  const completedTurns = markers.map((marker) => {
    const userMessage = allEvents.find((event) => (
      event.type === "user.message" && payloadText(event).includes(marker)
    ));
    expect(userMessage, `user.message for ${marker}`).toBeDefined();
    return allEvents.find((event) => (
      event.type === "kanban.agent.turn.completed"
      && event.correlation_id === userMessage?.correlation_id
    ));
  }).filter((event): event is EventItem => Boolean(event));
  expect(completedTurns).toHaveLength(4);
  const threadIds = new Set(completedTurns.map((event) => String(event.payload?.thread_id ?? "")));
  const providerSessions = new Set(
    completedTurns.map((event) => String(event.payload?.provider_session_id ?? "")),
  );
  expect(threadIds.size).toBe(1);
  expect(providerSessions.size).toBe(1);
  expect([...providerSessions][0]).not.toBe("");

  const pendingAfter = await apiJson<{ items?: unknown[] }>(
    request,
    `/api/projects/${encodeURIComponent(id)}/kanban-agent/pending-proposals`,
  );
  expect(pendingAfter.items ?? []).toHaveLength(0);

  await reopenAgent(page);
  const agentSession = page
    .getByRole("dialog", { name: "zaofu" })
    .locator(".agent-session");
  for (const marker of markers) {
    await expect(agentSession).toContainText(marker);
  }

  const productErrors = consoleErrors.filter((line) => !line.includes("favicon"));
  expect(productErrors.join("\n")).toBe("");
});
