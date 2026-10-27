import { expect, test, type Page } from "@playwright/test";

const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const requestId = process.env.ZF_WORKFLOW_PROPOSAL_REQUEST_ID ?? "REQ-PW-WORKFLOW";
const seedKey = `workflow-proposal-browser-seed:${requestId}`;

test.describe.configure({ mode: "serial", timeout: 60_000 });
test.skip(!actionToken, "Workflow Proposal E2E requires an isolated action token.");

async function expectNoViewportOverflow(page: Page) {
  const metrics = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
}

async function expectVisibleControlsInViewport(page: Page) {
  const metrics = await page.locator(".workflow-review-drawer > header button:visible, .workflow-proposal-actions button:visible").evaluateAll(
    (buttons) => {
      const viewportWidth = document.documentElement.clientWidth;
      return buttons.map((button) => {
        const rect = button.getBoundingClientRect();
        return {
          left: rect.left,
          right: rect.right,
          scrollWidth: (button as HTMLElement).scrollWidth,
          width: rect.width,
          viewportWidth,
        };
      });
    },
  );
  expect(metrics.length).toBeGreaterThan(0);
  for (const metric of metrics) {
    expect(metric.left).toBeGreaterThanOrEqual(0);
    expect(metric.right).toBeLessThanOrEqual(metric.viewportWidth + 1);
    expect(metric.scrollWidth).toBeLessThanOrEqual(metric.width + 2);
  }
}

test.beforeAll(async ({ request }) => {
  const response = await request.post(
    "/api/projects/default/actions/workflow-request",
    {
      headers: {
        "x-idempotency-key": seedKey,
        "x-zf-web-token": actionToken,
      },
      data: {
        project_id: "default",
        idempotency_key: seedKey,
        actor: "playwright",
        payload: {
          request_id: requestId,
          kind: "issue",
          objective: "Fix checkout expiry and retain an auditable regression test.",
          acceptance: [
            "An active checkout session remains valid.",
            "The regression suite proves the expiry boundary.",
          ],
          constraints: ["Do not change the public session API."],
          backend: "mock",
          allow_missing_env: true,
        },
      },
    },
  );
  expect(response.status(), await response.text()).toBe(202);
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    window.localStorage.clear();
    window.localStorage.setItem("zf.webActionToken", token);
    window.localStorage.setItem("zf.themeMode", "light");
  }, actionToken);
});

test("Raw labels legacy compiled snapshots and unavailable run evidence", async ({ page, request }) => {
  const response = await request.get(`/api/projects/default/workflow-requests/${requestId}`);
  expect(response.ok()).toBeTruthy();
  const original = await response.json();
  let effectiveConfig: Record<string, unknown> = {
    config: { project: { name: "legacy-run" } },
    yaml: "project:\n  name: legacy-run\n",
    yaml_source: "compiled_snapshot",
  };
  // Browser projection fixtures only; no running proposal is rewritten.
  await page.route(`**/api/projects/*/workflow-requests/${requestId}`, route => route.fulfill({ json: {
    ...original,
    lifecycle: { ...original.lifecycle, submitted: true },
    artifacts: { ...original.artifacts, effective_config: effectiveConfig },
  } }));
  await page.goto(`/?page=workflows&project=default&request=${requestId}`);
  await page.getByRole("button", { name: "Raw", exact: true }).click();
  const raw = page.getByTestId("workflow-yaml-preview");
  await expect(raw.locator("pre")).toContainText("legacy-run");
  await expect(raw).toContainText("Compiled configuration snapshot · original YAML unavailable");
  await expect(page.getByTestId("workflow-start")).toHaveCount(0);

  effectiveConfig = { status: "unreadable" };
  await page.reload();
  await page.getByRole("button", { name: "Raw", exact: true }).click();
  await expect(raw).toContainText("The bound configuration snapshot is unavailable.");
  await expect(raw).not.toContainText("after clarification");
  await expect(raw.locator("pre")).toHaveCount(0);
});

test("reviews, approves, and follows a proposal without layout collisions", async ({ page, request }) => {
  let terminalProjection: Record<string, unknown> | null = null;
  let terminalProjectionHits = 0;
  await page.route("**/api/projects/*/workflow-requests/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.endsWith(`/workflow-requests/${requestId}`) || !terminalProjection) {
      await route.continue();
      return;
    }
    terminalProjectionHits += 1;
    await route.fulfill({ json: terminalProjection });
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/?page=workflows&project=default`);

  const proposalPage = page.getByTestId("workflow-proposal-page");
  await expect(proposalPage).toBeVisible();
  await expect(page.getByRole("heading", { name: "Workflow", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: /Needs decision/ })).toHaveCount(0);
  if (await page.getByLabel("Select workflow").count()) await expect(page.getByLabel("Select workflow")).toHaveValue(requestId);
  await expect(page.getByTestId("workflow-graph-canvas")).toBeVisible();
  await expect(page.getByRole("button", { name: "Review & Approve", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Raw", exact: true }).click();
  await expect(page.getByRole("button", { name: "Details", exact: true })).toHaveCount(0);
  await expect(page.getByTestId("workflow-yaml-preview").locator("pre")).toContainText("kind: IssueFlow");
  await expectNoViewportOverflow(page);
  await expectVisibleControlsInViewport(page);

  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoViewportOverflow(page);
  await expectVisibleControlsInViewport(page);
  const submitted = page.waitForResponse((response) => (
    response.url().endsWith("/actions/workflow-submit")
    && response.request().method() === "POST"
  ));
  await page.getByTestId("workflow-start").click();
  expect((await submitted).status()).toBe(202);
  await expect(page.getByTestId("workflow-proposal-feedback")).toContainText("accepted");
  await page.getByRole("button", { name: "Run Controls", exact: true }).click();
  await expect(page.getByTestId("workflow-open-run")).toBeVisible();

  const detailResponse = await request.get(
    `/api/projects/default/workflow-requests/${requestId}`,
  );
  expect(detailResponse.status()).toBe(200);
  const body = await detailResponse.json() as Record<string, unknown>;
  const lifecycle = body.lifecycle as Record<string, unknown>;
  const links = body.links as Record<string, unknown>;
  terminalProjection = {
    ...body,
    lifecycle: {
      ...lifecycle,
      run_id: requestId,
      run_started: true,
      submitted: true,
      terminal: "run.goal.completed",
    },
    links: {
      ...links,
      completion_receipt_ref: "artifacts/goal-completion-receipt.v1.json",
      goal_dossier_ref: "artifacts/goal-dossier.v1.json",
    },
  };
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect.poll(() => terminalProjectionHits).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Raw", exact: true }).click();
  await page.getByRole("button", { name: "Run Controls", exact: true }).click();
  await expect(page.getByTestId("workflow-readiness")).toContainText("Run completed");
  await expectNoViewportOverflow(page);

  await page.getByTestId("workflow-open-run").click();
  await expect(page).toHaveURL(/page=runs/);
  await expect(page).toHaveURL(new RegExp(`run_id=${encodeURIComponent(requestId)}`));
});
