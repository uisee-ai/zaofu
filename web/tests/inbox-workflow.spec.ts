import { expect, test, type Page } from "@playwright/test";

function request(id: string) {
  return { request_id: id, objective: `Objective ${id}`, status: "proposed", kind: "issue", revision: 1, proposal_digest: `digest-${id}`, proposal_ref: { ref: `${id}.json` } };
}
function detail(id: string, queueStatus = "") {
  return { result: request(id), requirement: { objective: `Objective ${id}`, acceptance: ["Test this change"] },
    proposal: { approval_status: "approvable", stage_graph: { nodes: [] } }, operation: { queue_status: queueStatus },
    artifacts: { effective_config: { yaml: "spec: {}\n" } }, lifecycle: {}, links: {} };
}
async function routes(page: Page) {
  await page.route("**/api/projects/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/workflow-requests")) await route.fulfill({ json: { items: [request("A"), request("B")] } });
    else if (path.endsWith("/graph")) await route.fulfill({ json: {
      scope: { request_id: path.split("/").at(-2), source_seq: 1, head_seq: 1, revision: 1 },
      nodes: [], edges: [], plans: [], diagnostics: [], versions: [], timeline: [], capabilities: {},
    } });
    else if (path.includes("/workflow-requests/")) await route.fulfill({ json: detail(path.split("/").pop()!) });
    else await route.fulfill({ json: { ok: true, fields: {} } });
  });
}

async function openRaw(page: Page) {
  const graph = page.getByRole("region", { name: "Workflow 图工作台" });
  await graph.getByRole("button", { name: "执行 Graph", exact: true }).click();
  await graph.getByRole("button", { name: "Raw", exact: true }).click();
  await expect(page.getByTestId("workflow-start")).toBeVisible();
}

for (const failed of [false, true]) test(`late ${failed ? "error" : "detail"} cannot replace the selected Workflow or its action`, async ({ page }) => {
  await routes(page);
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/workflow-requests/A", async (route) => {
    await delayed;
    await route.fulfill({ status: failed ? 500 : 200, json: detail("A") }).catch(() => {});
  });
  await page.goto("/tests/inbox-workflow.html?surface=workflow");
  await page.getByLabel("Select workflow").selectOption("B");
  await openRaw(page);
  await expect(page.getByLabel("Select workflow")).toHaveValue("B");
  release();
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(page.getByLabel("Select workflow")).toHaveValue("B");
  await expect(page.locator(".workflow-proposal-notice.tone-error")).toHaveCount(0);
  await page.getByTestId("workflow-start").click();
  await expect(page.getByTestId("actions")).toContainText('"request_id":"B"');
});

test("project change hides old Workflow details while new identity loads", async ({ page }) => {
  await routes(page);
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/projects/b/workflow-requests", async (route) => {
    await delayed;
    await route.fulfill({ json: { items: [request("NEW")] } });
  });
  await page.goto("/tests/inbox-workflow.html");
  await openRaw(page);
  await page.getByRole("button", { name: "Switch project" }).click();
  await expect(page.getByTestId("workflow-start")).toHaveCount(0);
  release();
  await expect(page.getByLabel("Select workflow")).toHaveCount(0);
  await openRaw(page);
  await page.getByTestId("workflow-start").click();
  await expect(page.getByTestId("actions")).toContainText('"request_id":"NEW"');
});

test("an old project's delayed list cannot select an action in the new project", async ({ page }) => {
  await routes(page);
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/projects/a/workflow-requests", async (route) => {
    await delayed;
    await route.fulfill({ json: { items: [request("OLD")] } }).catch(() => {});
  });
  await page.goto("/tests/inbox-workflow.html");
  await page.getByRole("button", { name: "Switch project" }).click();
  await expect(page.getByLabel("Select workflow")).toHaveValue("A");
  release();
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(page.getByRole("option", { name: /^OLD/ })).toHaveCount(0);
  await openRaw(page);
  await page.getByTestId("workflow-start").click();
  await expect(page.getByTestId("actions")).toContainText('"project":"b"');
});

test("a response bound to a different Request cannot expose approval actions", async ({ page }) => {
  await routes(page);
  await page.route("**/workflow-requests/A", (route) => route.fulfill({ json: detail("OTHER") }));
  await page.goto("/tests/inbox-workflow.html");
  await expect(page.locator(".workflow-proposal-notice.tone-error")).toContainText("identity mismatch");
  await expect(page.getByTestId("workflow-start")).toHaveCount(0);
});

test("slow preparation polls do not overlap or create an extra ordinary poll", async ({ page }) => {
  await page.clock.install();
  await routes(page);
  let calls = 0;
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/workflow-requests/A", async (route) => {
    calls += 1;
    if (calls > 1) await delayed;
    await route.fulfill({ json: detail("A", "running") }).catch(() => {});
  });
  await page.goto("/tests/inbox-workflow.html");
  await expect(page.getByLabel("Select workflow")).toHaveValue("A");
  await expect(page.getByRole("region", { name: "Workflow 图工作台" })).toBeVisible();
  const initial = calls;
  await page.clock.runFor(1600);
  await expect.poll(() => calls).toBe(initial + 1);
  await page.clock.runFor(16_000);
  expect(calls).toBe(initial + 1);
  release();
});

test("Inbox read state stays separate from decisions and retains current unread selection", async ({ page }) => {
  const items = [
    { id: "approval", title: "Review proposal", kind: "approval", status: "pending", unread: false, actionability: "human_required", actions: [{ action: "approve", label: "Approve" }] },
    { id: "report-a", title: "Decision request", kind: "approval", status: "pending", unread: true, read_version: "v1:a", actionability: "human_required", actions: [] },
    { id: "report-b", title: "Decision request", kind: "approval", status: "pending", unread: true, read_version: "v1:b", actionability: "human_required", actions: [] },
  ];
  const actions: string[] = [];
  let failRead = true;
  await page.route("**/api/projects/**", async (route) => {
    const action = route.request().url().split("/").pop()!;
    if (route.request().method() === "POST") {
      actions.push(action);
      if (failRead) {
        failRead = false;
        await route.fulfill({ json: { ok: false, reason: "Read was rejected" } });
        return;
      }
      const payload = route.request().postDataJSON().payload;
      for (const item of items) if (action === "inbox-all-read" || payload.item_id === item.id) item.unread = false;
      await route.fulfill({ json: { ok: true } });
    } else await route.fulfill({ json: { items, pending: items, summary: { action_required_pending: 1, unread: items.filter((item) => item.unread).length }, policy: {} } });
  });
  await page.goto("/tests/inbox-workflow.html?surface=inbox");
  const row = (id: string) => page.locator(`[data-inbox-id="${id}"]`);
  await expect(row("approval")).toContainText("Needs action");
  await expect(row("approval").locator(".operator-inbox-unread-dot")).toHaveCount(0);
  await expect(page.locator(".inbox-queue").getByText("Decision request", { exact: true })).toHaveCount(2);
  await page.getByRole("checkbox", { name: "Unread", exact: true }).check();
  await row("report-a").click();
  await expect(page.getByText("Read was rejected", { exact: true })).toBeVisible();
  await expect(row("report-a").locator(".operator-inbox-unread-dot")).toHaveCount(1);
  await row("report-a").click();
  await expect(row("report-a").locator(".operator-inbox-unread-dot")).toHaveCount(0);
  await expect(row("report-a")).toBeVisible();
  await page.getByRole("button", { name: "Mark all read" }).click();
  await page.getByRole("checkbox", { name: "Unread", exact: true }).uncheck();
  await page.getByRole("button", { name: "Needs action", exact: true }).click();
  await expect(row("approval")).toBeVisible();
  expect(actions).toEqual(["inbox-item-read", "inbox-item-read", "inbox-all-read"]);
});

test("a pending Inbox poll cannot undo an accepted read acknowledgement", async ({ page }) => {
  await page.clock.install();
  let unread = true;
  let holdPoll = false;
  let held = false;
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/projects/**", async (route) => {
    if (route.request().method() === "POST") {
      unread = false;
      await route.fulfill({ json: { ok: true } });
      return;
    }
    const item = { id: "report", title: "Report", kind: "approval", status: "pending", actionability: "human_required", unread };
    if (holdPoll) { holdPoll = false; held = true; await delayed; }
    await route.fulfill({ json: { items: [item], pending: [item], summary: { unread: item.unread ? 1 : 0 }, policy: {} } }).catch(() => {});
  });
  await page.goto("/tests/inbox-workflow.html?surface=inbox");
  await expect(page.locator(".inbox-queue .operator-inbox-unread-dot")).toHaveCount(1);
  holdPoll = true;
  await page.clock.runFor(15_100);
  await expect.poll(() => held).toBe(true);
  await page.getByRole("button", { name: "Mark read", exact: true }).click();
  await expect(page.locator(".inbox-queue .operator-inbox-unread-dot")).toHaveCount(0);
  release();
  await page.clock.runFor(200);
  await expect(page.locator(".inbox-queue .operator-inbox-unread-dot")).toHaveCount(0);
});

test("Inbox row read control acknowledges only its own item", async ({ page }) => {
  const items = ["first", "second"].map(id => ({ id, title: `Report ${id}`, kind: "approval", status: "pending", actionability: "human_required", unread: true }));
  const reads: string[] = [];
  await page.route("**/api/projects/**", async route => {
    if (route.request().method() === "POST") {
      const id = route.request().postDataJSON().payload.item_id;
      reads.push(id);
      items.find(item => item.id === id)!.unread = false;
      return route.fulfill({ json: { ok: true } });
    }
    return route.fulfill({ json: { items, pending: items, summary: { unread: items.filter(item => item.unread).length }, policy: {} } });
  });
  await page.goto("/tests/inbox-workflow.html?surface=inbox");
  await page.getByRole("button", { name: "Mark read: Report second" }).click();
  await expect(page.getByRole("button", { name: "Mark read: Report second" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Mark read: Report first" })).toBeVisible();
  expect(reads).toEqual(["second"]);
  await page.setViewportSize({ width: 320, height: 844 });
  await page.locator('[data-inbox-id="first"]').click();
  await expect(page.locator(".inbox-reading-pane").getByRole("button", { name: "Mark read", exact: true })).toHaveCount(0);
  expect(reads).toEqual(["second", "first"]);
});

for (const action of ["replan-approve", "replan-reject"]) test(`Inbox owner preview waits for bound refs before ${action}`, async ({ page }) => {
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  const actions: { action: string; payload: Record<string, unknown> }[] = [];
  const item = { id: "approval:owner-review", kind: "approval", status: "pending", title: "Owner review", plan_id: "PLAN-1", created_event_id: "evt-owner-review",
    actionability: "human_required", owner_route: "plan_synth_owner_decision", approval_ref: "owner-review",
    proposal_ref: "proposal.json", eval_ref: "evaluation.json" };
  await page.route("**/api/projects/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") {
      actions.push({ action: path.split("/").pop()!, payload: route.request().postDataJSON().payload });
      await route.fulfill({ json: { ok: true } });
    } else if (path.endsWith("/operator/inbox")) {
      await delayed;
      await route.fulfill({ json: { items: [item], pending: [item], summary: { action_required_pending: 1 }, policy: {} } }).catch(() => {});
    } else await route.fulfill({ json: { plan_id: "PLAN-1", status: "pending", requested_event_id: "evt-owner-review", content_kind: "full", markdown: "# Review scope",
      refs: {}, artifacts: [], actions: { approve: "replan-approve", reject: "replan-reject" } } });
  });
  await page.goto("/tests/inbox-workflow.html?surface=inbox&plan=PLAN-1");
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  const approve = dialog.getByRole("button", { name: "Approve decisions", exact: true });
  const reject = dialog.getByRole("button", { name: "Reject", exact: true });
  await expect(approve).toBeVisible();
  await dialog.getByPlaceholder("reject reason", { exact: true }).fill("revise scope");
  await expect(approve).toBeDisabled();
  await expect(reject).toBeDisabled();
  release();
  const button = action === "replan-approve" ? approve : reject;
  await expect(button).toBeEnabled();
  await button.click();
  await expect.poll(() => actions.length).toBe(1);
  expect(actions[0]).toMatchObject({ action, payload: { approval_ref: "owner-review", proposal_ref: "proposal.json", eval_ref: "evaluation.json" } });
});

test("an old Plan preview cannot approve a newer owner request", async ({ page }) => {
  const item = { id: "approval:owner-review", kind: "approval", status: "pending", title: "Owner review", plan_id: "PLAN-1",
    created_event_id: "evt-new", actionability: "human_required", owner_route: "plan_synth_owner_decision",
    approval_ref: "owner-review", proposal_ref: "proposal.json", eval_ref: "evaluation.json" };
  await page.route("**/api/projects/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/operator/inbox")) await route.fulfill({ json: { items: [item], pending: [item], summary: { action_required_pending: 1 }, policy: {} } });
    else await route.fulfill({ json: { plan_id: "PLAN-1", status: "pending", requested_event_id: "evt-old", content_kind: "full",
      markdown: "# Old review", refs: {}, artifacts: [], actions: { approve: "replan-approve", reject: "replan-reject" } } });
  });
  await page.goto("/tests/inbox-workflow.html?surface=inbox&plan=PLAN-1");
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await expect(dialog.getByRole("button", { name: "Approve decisions", exact: true })).toBeDisabled();
});

test("Board summarizes blockers while List retains details and outcomes", async ({ page }) => {
  await page.goto("/tests/inbox-workflow.html?surface=board");
  const card = page.locator('[data-task-id="TASK-1"]');
  await expect(card).toContainText("Ship checkout");
  await expect(card).toContainText("Blocked");
  await expect(card).not.toContainText("Waiting for owner");
  await expect(card.locator(".task-assignee-avatar")).toHaveAttribute("title", "@dev-1");
  await expect(card).not.toContainText(/internal-|Internal verify|secret-skill|rework|ctx|fanout/);
  await expect(page.locator('[data-task-id="TASK-2"]')).toContainText("cancelled");
  await expect(page.locator('[data-task-id="TASK-3"]')).not.toContainText("fanout_queue");
  await page.getByRole("button", { name: "List", exact: true }).click();
  const rows = page.locator(".workbench-task-list");
  await expect(rows).toContainText("Waiting for owner");
  await expect(rows).not.toContainText(/internal-|tokens|ctx|tok |fanout_queue/);
  await rows.locator(".workbench-task-row").filter({ hasText: "Ship checkout" }).click();
  await expect(page.getByRole("dialog", { name: "Task details" })).toContainText("internal-route");
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});


for (const failed of [false, true]) test(`a late Workflow ${failed ? "error" : "response"} cannot reset an active Preview review`, async ({ page }) => {
  await page.clock.install();
  await routes(page);
  let reads = 0;
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  let completed!: () => void;
  const answered = new Promise<void>(resolve => { completed = resolve; });
  await page.route("**/workflow-requests/A", async route => {
    reads++;
    const value = detail("A");
    if (reads > 1) {
      await delayed;
      value.result.revision = 2;
      value.result.proposal_digest = "new-generation";
    }
    await route.fulfill({ status: reads > 1 && failed ? 500 : 200, json: value }).catch(() => {});
    if (reads > 1) completed();
  });
  await page.route("**/workflow-preview-review", route => {
    const body = route.request().postDataJSON();
    return route.fulfill({ json: body.operation === "context"
      ? { ok: true, fields: { "requirement.acceptance.0": { value: "Test this change", editable: true } } }
      : { ok: true, draft_ref: { ref: "fixture-draft" }, changes: [{ field: "requirement.acceptance.0", before: "Test this change", after: "Keep this draft" }] } });
  });
  try {
    await page.goto("/tests/inbox-workflow.html");
    await openRaw(page);
    await expect(page.getByTestId("workflow-start")).toBeEnabled();
    await expect(page.getByRole("region", { name: "Workflow 图工作台" })).toBeVisible();
    await page.clock.runFor(8100);
    await expect.poll(() => reads).toBe(2);
    await page.getByRole("button", { name: "Agent", exact: true }).click();
    await page.getByLabel("Preview discussion").fill("Keep the pending review");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByTestId("review-diff")).toContainText("Keep this draft");
    release(); await answered;
    await page.clock.runFor(200);
    await expect(page.getByTestId("review-diff")).toContainText("Keep this draft");
    await expect(page.getByTestId("workflow-start")).toBeDisabled();
    await expect(page.locator(".workflow-proposal-notice.tone-error")).toHaveCount(0);
  } finally { release(); }
});
