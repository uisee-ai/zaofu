import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";

// This spec needs the isolated fixture owned by the host pytest below.
test.skip(process.env.ZF_TESTKIT_BROWSER_RUN !== "1", "explicit TestKit Docker tier");

test("controlled drag converges Store API SSE and DOM without reloading", async ({ page, context }) => {
  const taskId = "TASK-TESTKIT-BROWSER";
  const regressionTaskId = "TASK-TESTKIT-BROWSER-REGRESSION";
  const pageErrors: string[] = [];
  const serverErrors: string[] = [];
  const consoleErrors: string[] = [];
  const unexpectedOrigins: string[] = [];
  const redirects: string[] = [];
  const evidence: Record<string, unknown> = { task_id: taskId };
  let streamAttempts = 0;
  const injectedDisconnects = Math.max(
    0,
    Number.parseInt(process.env.ZF_TESTKIT_SSE_DISCONNECTS ?? "1", 10) || 0,
  );
  evidence.injected_disconnects = injectedDisconnects;
  const allowedOrigin = new URL(process.env.ZF_WEB_BASE_URL!).origin;
  page.on("pageerror", error => pageErrors.push(String(error)));
  page.on("console", message => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("response", response => {
    if (response.status() >= 500) serverErrors.push(`${response.status()} ${response.url()}`);
    if (response.status() >= 300 && response.status() < 400) redirects.push(`${response.status()} ${response.url()}`);
  });
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.origin !== allowedOrigin) unexpectedOrigins.push(request.url());
  });
  await context.addInitScript((token: string) => {
    localStorage.setItem("zf.webActionToken", token);
    const messages: { id: string; data: string }[] = [];
    Object.assign(window, { testkitSse: messages });
    // Observe native delivery without replacing the stream or its payload.
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      constructor(url: string | URL, options?: EventSourceInit) {
        super(url, options);
        this.addEventListener("message", event => {
          messages.push({ id: event.lastEventId, data: event.data });
        });
      }
    };
  }, process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST!);

  try {
    // Exercise the browser's real EventSource reconnect path.  The first N
    // streams are interrupted by the harness; subsequent connections use the
    // actual server and must converge from the last cursor.
    await page.route("**/stream**", async route => {
      streamAttempts += 1;
      if (streamAttempts <= injectedDisconnects) {
        await route.abort("connectionreset");
      } else {
        await route.continue();
      }
    });
    // Before browser authentication, the same mutation must be rejected.
    const denied = await page.request.post("/api/actions/update-task", {
      data: { task_id: taskId, status: "done" },
    });
    expect([401, 403]).toContain(denied.status());
    evidence.unauthorized_status = denied.status();
    const deniedCreate = await page.request.post("/api/actions/create-task", {
      data: { title: "must not be created" },
    });
    expect([401, 403]).toContain(deniedCreate.status());
    evidence.unauthorized_create_status = deniedCreate.status();
    const authorizedCreate = await page.request.post("/api/actions/create-task", {
      data: { task_id: "TASK-TESTKIT-CREATED", title: "Created through controlled action" },
      headers: { "x-zf-web-token": process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST! },
    });
    expect(authorizedCreate.status()).toBe(201);
    const authorizedCreateBody = await authorizedCreate.json();
    expect(authorizedCreateBody.ok).toBe(true);
    expect(authorizedCreateBody.task_id).toBe("TASK-TESTKIT-CREATED");
    evidence.authorized_create_status = authorizedCreate.status();
    evidence.authorized_create = authorizedCreateBody;

    // Exercise the native regression capture/replay endpoints through the
    // browser context (the Runs-page mock tests cover DOM wiring separately).
    const regressionHeaders = {
      "x-zf-web-token": process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST!,
      "x-idempotency-key": "testkit-browser-regression-capture-once",
    };
    const deniedRegression = await page.request.post("/api/actions/capture-regression-case", {
      data: { task_id: regressionTaskId, feature_id: "F-TESTKIT-BROWSER", assertions: ["rework==0", "scope_violation==0"] },
    });
    expect([401, 403]).toContain(deniedRegression.status());
    const capturedRegression = await page.request.post("/api/actions/capture-regression-case", {
      data: {
        task_id: regressionTaskId,
        feature_id: "F-TESTKIT-BROWSER",
        case_id: "REG-TESTKIT-BROWSER",
        assertions: ["rework==0", "scope_violation==0"],
      },
      headers: regressionHeaders,
    });
    expect(capturedRegression.status()).toBe(201);
    const capturedRegressionBody = await capturedRegression.json();
    expect(capturedRegressionBody.ok).toBe(true);
    expect(capturedRegressionBody.result.case_id).toBe("REG-TESTKIT-BROWSER");
    const regressionCases = await page.request.get("/api/projects/default/regression-cases");
    expect(regressionCases.status()).toBe(200);
    const regressionCasesBody = await regressionCases.json();
    expect(regressionCasesBody.cases).toEqual(expect.arrayContaining([
      expect.objectContaining({ case_id: "REG-TESTKIT-BROWSER", source_task_id: regressionTaskId }),
    ]));
    const replayedRegression = await page.request.post("/api/actions/replay-regression-case", {
      data: { case_id: "REG-TESTKIT-BROWSER" },
      headers: {
        "x-zf-web-token": process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST!,
        "x-idempotency-key": "testkit-browser-regression-replay-once",
      },
    });
    expect(replayedRegression.status()).toBe(200);
    const replayedRegressionBody = await replayedRegression.json();
    expect(replayedRegressionBody.ok).toBe(true);
    expect(replayedRegressionBody.result.passed).toBe(true);
    evidence.regression = {
      denied_status: deniedRegression.status(),
      capture: capturedRegressionBody,
      cases: regressionCasesBody,
      replay: replayedRegressionBody,
    };
    // The native Runs surface must hydrate the captured case from the real
    // backend, rather than from a browser route fixture.
    await page.goto("/?page=delivery-trace&feature=F-TESTKIT-BROWSER", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "Runs", exact: true })).toBeVisible();
    await expect(page.getByTestId(`rg-task-${regressionTaskId}`)).toBeVisible();
    await page.getByTestId(`rg-task-${regressionTaskId}`).click();
    await expect(page.getByTestId("lifecycle-drawer")).toHaveAttribute(
      "aria-label", `Task lifecycle ${regressionTaskId}`,
    );
    await expect(page.getByTestId("regression-cases")).toContainText("REG-TESTKIT-BROWSER");
    evidence.regression_dom = {
      task_visible: true,
      case_visible: true,
    };
    await page.getByRole("button", { name: "close lifecycle drawer" }).click();
    const archiveHeaders = {
      "x-zf-web-token": process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST!,
      "x-idempotency-key": "testkit-browser-archive-once",
    };
    const archived = await page.request.post("/api/actions/archive-task", {
      data: { task_id: "TASK-TESTKIT-CREATED", status: "done" },
      headers: archiveHeaders,
    });
    expect(archived.status()).toBe(200);
    expect((await archived.json()).status).toBe("completed");
    const archiveReplay = await page.request.post("/api/actions/archive-task", {
      data: { task_id: "TASK-TESTKIT-CREATED", status: "done" },
      headers: archiveHeaders,
    });
    expect(archiveReplay.status()).toBe(200);
    const archiveReplayBody = await archiveReplay.json();
    expect(archiveReplayBody.idempotency.status).toBe("replayed");
    const archiveRows = await page.request.get("/api/archives/tasks");
    expect(archiveRows.status()).toBe(200);
    expect((await archiveRows.json()).some(
      (item: { id: string; status: string }) =>
        item.id === "TASK-TESTKIT-CREATED" && item.status === "done",
    )).toBe(true);
    evidence.archive = { response: await archived.json(), replay: archiveReplayBody };
    await page.goto("/?page=board", { waitUntil: "domcontentloaded" });
    const card = page.locator(`.task-card[data-task-id="${taskId}"]`);
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute("data-task-status", "ready");
    const createdCard = page.locator('.task-card[data-task-id="TASK-TESTKIT-CREATED"]');
    await expect(createdCard).toBeVisible();
    await expect(createdCard).toHaveAttribute("data-task-status", "done");
    const statusMatrix = [
      { id: "TASK-TESTKIT-TESTING", column: "testing", api: "review" },
      { id: "TASK-TESTKIT-BLOCKED", column: "blocked", api: "blocked" },
      { id: "TASK-TESTKIT-DONE", column: "done", api: "done" },
      { id: "TASK-TESTKIT-CANCELLED", column: "done", api: "cancelled" },
    ] as const;
    for (const item of statusMatrix) {
      const statusCard = page.locator(`.task-card[data-task-id="${item.id}"]`);
      await expect(statusCard).toBeVisible();
      await expect(statusCard).toHaveAttribute("data-task-status", item.column);
      const statusResponse = await page.request.get(`/api/tasks/${item.id}`);
      expect(statusResponse.status()).toBe(200);
      const statusBody = await statusResponse.json();
      expect(statusBody.task.status).toBe(item.api);
    }
    evidence.status_matrix = statusMatrix;
    const before = await page.request.get(`/api/tasks/${taskId}`);
    expect(before.status()).toBe(200);
    evidence.before = await before.json();
    expect((evidence.before as { task: { status: string } }).task.status).toBe("backlog");
    await page.screenshot({ path: "/zf-run/before.png" });

    const observer = await context.newPage();
    await observer.goto("/?page=board", { waitUntil: "domcontentloaded" });
    const observedCard = observer.locator(`.task-card[data-task-id="${taskId}"]`);
    await expect(observedCard).toHaveAttribute("data-task-status", "ready");

    const target = page.locator('.board-column[data-column-id="in_progress"]');
    const sourceBox = await card.boundingBox();
    const targetBox = await target.boundingBox();
    expect(sourceBox).not.toBeNull();
    expect(targetBox).not.toBeNull();
    const actionResponse = page.waitForResponse(response =>
      response.request().method() === "POST" && response.url().includes("/actions/update-task"));
    await page.mouse.move(sourceBox!.x + sourceBox!.width / 2, sourceBox!.y + 24);
    await page.mouse.down();
    await page.mouse.move(sourceBox!.x + sourceBox!.width / 2 + 24, sourceBox!.y + 36, { steps: 5 });
    await page.mouse.move(targetBox!.x + targetBox!.width / 2, sourceBox!.y + 24, { steps: 20 });
    await page.mouse.up();
    const response = await actionResponse;
    expect(response.ok()).toBe(true);
    evidence.action = await response.json();
    expect((evidence.action as { ok: boolean }).ok).toBe(true);

    await expect(card).toHaveAttribute("data-task-status", "in_progress");
    // This page never sent the action: optimistic updates cannot explain it.
    await expect(observedCard).toHaveAttribute("data-task-status", "in_progress");
    await expect.poll(async () => {
      const detail = await page.request.get(`/api/tasks/${taskId}`);
      expect(detail.status()).toBe(200);
      evidence.after = await detail.json();
      return (evidence.after as { task: { status: string } }).task.status;
    }).toBe("in_progress");
    await expect.poll(async () => page.evaluate((id: string) => {
      const messages = (window as unknown as { testkitSse: { data: string }[] }).testkitSse;
      return messages.some(({ data }) => {
        const event = JSON.parse(data);
        return event.type === "task.updated" && event.task_id === id;
      });
    }, taskId)).toBe(true);
    evidence.sse = await page.evaluate(() =>
      (window as unknown as { testkitSse: unknown[] }).testkitSse);
    evidence.stream_attempts = streamAttempts;
    expect(streamAttempts).toBeGreaterThanOrEqual(injectedDisconnects + 1);
    await expect.poll(async () => observer.evaluate((id: string) => {
      const messages = (window as unknown as { testkitSse: { data: string }[] }).testkitSse;
      return messages.some(({ data }) => JSON.parse(data).id === id);
    }, (evidence.action as { event_id: string }).event_id)).toBe(true);
    evidence.observer_sse = await observer.evaluate(() =>
      (window as unknown as { testkitSse: unknown[] }).testkitSse);
    evidence.observer_dom_status = await observedCard.getAttribute("data-task-status");
    await observer.close();
    evidence.dom_status = await card.getAttribute("data-task-status");
    evidence.network = { allowed_origin: allowedOrigin, unexpected_origins: unexpectedOrigins, redirects };
    await page.screenshot({ path: "/zf-run/after.png" });
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(card).toHaveAttribute("data-task-status", "in_progress");
    evidence.reload_status = await card.getAttribute("data-task-status");
    expect(pageErrors).toEqual([]);
    expect(serverErrors).toEqual([]);
    expect(unexpectedOrigins).toEqual([]);
    expect(redirects).toEqual([]);
  } finally {
    writeFileSync("/zf-run/browser-truth.json", JSON.stringify({
      ...evidence, pageErrors, serverErrors, consoleErrors,
    }, null, 2));
  }
});

test("critical SPA routes stay origin-bound and do not follow redirects", async ({ page }) => {
  const allowedOrigin = new URL(process.env.ZF_WEB_BASE_URL!).origin;
  const unexpectedOrigins: string[] = [];
  const redirects: string[] = [];
  const pageErrors: string[] = [];
  const routeResults: Array<{ path: string; status: number | null }> = [];
  page.on("pageerror", error => pageErrors.push(String(error)));
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.origin !== allowedOrigin) unexpectedOrigins.push(request.url());
  });
  page.on("response", response => {
    // 304 is cache revalidation, not a redirect and is expected on repeated
    // route navigations in a real browser context.
    if (response.status() >= 300 && response.status() < 400 && response.status() !== 304) {
      redirects.push(`${response.status()} ${response.url()}`);
    }
  });

  for (const path of [
    "/?page=board",
    "/?page=delivery-trace",
    "/?page=observability&obs_tab=events",
    "/?page=loop",
    "/?page=channels",
  ]) {
    const response = await page.goto(path, { waitUntil: "domcontentloaded" });
    routeResults.push({ path, status: response?.status() ?? null });
    await expect(page.locator("main.workspace")).toBeVisible();
  }

  expect(unexpectedOrigins).toEqual([]);
  expect(redirects).toEqual([]);
  writeFileSync("/zf-run/network-boundary.json", JSON.stringify({
    allowed_origin: allowedOrigin,
    routes: routeResults,
    unexpected_origins: unexpectedOrigins,
    redirects,
    page_errors: pageErrors,
  }, null, 2));
});
