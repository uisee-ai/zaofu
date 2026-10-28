import { writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST || "";
const expectedModel = process.env.ZF_MODEL_E2E_MODEL || "";
const expectedEffort = process.env.ZF_MODEL_E2E_EFFORT || "";
const secondEffort = process.env.ZF_MODEL_E2E_SECOND_EFFORT || "";
const artifactPath = process.env.ZF_MODEL_E2E_ARTIFACT || "";

test("Web selection reaches the real Codex turn", async ({ page, request }) => {
  test.setTimeout(480_000);
  expect(token && expectedModel && expectedEffort && secondEffort && artifactPath, "use the isolated real-provider runner").toBeTruthy();
  const projectsResponse = await request.get("/api/workspace/projects");
  expect(projectsResponse.ok()).toBeTruthy();
  const projects = await projectsResponse.json();
  const projectId = String(projects.server_default_project_id || "");
  expect(projectId).toBeTruthy();
  const catalogResponse = await request.get(`/api/projects/${encodeURIComponent(projectId)}/webchat/model-catalog`, {
    headers: { "X-ZF-Web-Token": token },
  });
  expect(catalogResponse.ok()).toBeTruthy();
  const catalog = await catalogResponse.json();
  const selected = catalog.models.find((item: { model: string }) => item.model === expectedModel);
  expect(selected?.efforts).toContain(expectedEffort);
  expect(selected?.efforts).toContain(secondEffort);
  expect(secondEffort).not.toBe(expectedEffort);

  await page.addInitScript(({ actionToken }) => {
    localStorage.setItem("zf.webActionToken", actionToken);
    localStorage.setItem("zf.operatorBackend", "codex-headless");
  }, { actionToken: token });
  await page.goto(`/?project=${encodeURIComponent(projectId)}&page=board`);
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const panel = page.getByRole("dialog", { name: "Kanban Agent" });
  await expect(panel.getByRole("button", { name: /Agent backend: Codex/ })).toBeVisible();
  const trigger = panel.getByRole("button", { name: "Model and thinking effort" });
  const defaultModel = catalog.models.find((item: { model: string }) => item.model === catalog.session_defaults?.model);
  expect(defaultModel, "provider should expose its configured model").toBeTruthy();
  const defaultEffort = String(catalog.session_defaults?.effort || "");
  expect(defaultEffort, "provider should expose its configured effort").toBeTruthy();
  const defaultLabel = defaultEffort === "xhigh" ? "Extra High" : defaultEffort === "low" ? "Light" : defaultEffort;
  await expect(trigger).toContainText(defaultModel.label);
  await expect(trigger).toContainText(new RegExp(defaultLabel, "i"));
  await trigger.click();
  const menu = panel.getByRole("dialog", { name: "Model and thinking effort" });
  await menu.getByRole("button", { name: "Choose model" }).click();
  await menu.getByRole("button", { name: selected.label, exact: true }).click();
  const slider = menu.getByRole("slider", { name: "Thinking effort" });
  await slider.focus();
  await page.keyboard.press("End");
  const index = selected.efforts.indexOf(expectedEffort) + 1;
  for (let i = selected.efforts.length; i > index; i -= 1) await page.keyboard.press("ArrowLeft");
  const label = expectedEffort === "xhigh" ? "Extra High" : expectedEffort === "low" ? "Light" : expectedEffort;
  await expect(slider).toHaveAttribute("aria-valuetext", new RegExp(`^${label}$`, "i"));
  await page.keyboard.press("Escape");
  await panel.locator("textarea.headless-input").fill("Reply exactly: ZF_MODEL_E2E_OK");
  const pendingRequest = page.waitForRequest((item) => (
    item.method() === "POST" && item.url().endsWith("/actions/chat-orchestrator")
  ));
  await panel.getByRole("button", { name: "Send message", exact: true }).click();
  const sent = (await pendingRequest).postDataJSON() as { payload: Record<string, unknown> };
  expect(sent.payload).toMatchObject({ backend: "codex-headless", model: expectedModel, thinking_level: expectedEffort });
  const turnId = String(sent.payload.turn_id || "");
  const threadKey = String(sent.payload.thread_key || "");
  expect(turnId && threadKey).toBeTruthy();

  await expect.poll(async () => {
    const response = await request.get(`/api/projects/${encodeURIComponent(projectId)}/events?limit=500`);
    if (!response.ok()) return "";
    const data = await response.json();
    const events = Array.isArray(data.items) ? data.items : [];
    return events.find((event: { type: string; payload?: { turn_id?: string } }) => (
      ["kanban.agent.turn.completed", "kanban.agent.turn.failed"].includes(event.type) && event.payload?.turn_id === turnId
    ))?.type || "";
  }, { timeout: 150_000, intervals: [250, 500, 1000] }).toBe("kanban.agent.turn.completed");
  await expect(panel.locator(".agent-text-part").filter({ hasText: "ZF_MODEL_E2E_OK" }).last()).toBeVisible({ timeout: 30_000 });
  await expect(trigger).toContainText(selected.label);
  await page.reload();
  if (!(await panel.isVisible().catch(() => false))) await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(trigger).toContainText(selected.label);
  await trigger.click();
  const secondSlider = menu.getByRole("slider", { name: "Thinking effort" });
  await secondSlider.focus();
  await page.keyboard.press("End");
  const secondIndex = selected.efforts.indexOf(secondEffort) + 1;
  for (let i = selected.efforts.length; i > secondIndex; i -= 1) await page.keyboard.press("ArrowLeft");
  const secondLabel = secondEffort === "xhigh" ? "Extra High" : secondEffort === "low" ? "Light" : secondEffort;
  await expect(secondSlider).toHaveAttribute("aria-valuetext", new RegExp(`^${secondLabel}$`, "i"));
  await page.keyboard.press("Escape");
  await panel.locator("textarea.headless-input").fill("Reply exactly: ZF_MODEL_E2E_SECOND_OK");
  const secondRequest = page.waitForRequest((item) => item.method() === "POST" && item.url().endsWith("/actions/chat-orchestrator"));
  await panel.getByRole("button", { name: "Send message", exact: true }).click();
  const second = (await secondRequest).postDataJSON() as { payload: Record<string, unknown> };
  expect(second.payload).toMatchObject({ backend: "codex-headless", model: expectedModel, thinking_level: secondEffort, thread_key: threadKey });
  const secondTurnId = String(second.payload.turn_id || "");
  expect(secondTurnId).toBeTruthy();
  await expect.poll(async () => {
    const response = await request.get(`/api/projects/${encodeURIComponent(projectId)}/events?limit=500`);
    if (!response.ok()) return "";
    const data = await response.json();
    return (data.items || []).find((event: { type: string; payload?: { turn_id?: string } }) => (
      ["kanban.agent.turn.completed", "kanban.agent.turn.failed"].includes(event.type) && event.payload?.turn_id === secondTurnId
    ))?.type || "";
  }, { timeout: 150_000, intervals: [250, 500, 1000] }).toBe("kanban.agent.turn.completed");
  await expect(panel.locator(".agent-text-part").filter({ hasText: "ZF_MODEL_E2E_SECOND_OK" }).last()).toBeVisible({ timeout: 30_000 });
  await trigger.click();
  await menu.getByRole("button", { name: "Reset model and effort" }).click();
  await expect(trigger).toContainText(defaultModel.label);
  await expect(trigger).toContainText(new RegExp(defaultLabel, "i"));
  await page.keyboard.press("Escape");
  await panel.locator("textarea.headless-input").fill("Reply exactly: ZF_MODEL_E2E_DEFAULT_OK");
  const defaultRequest = page.waitForRequest((item) => item.method() === "POST" && item.url().endsWith("/actions/chat-orchestrator"));
  await panel.getByRole("button", { name: "Send message", exact: true }).click();
  const reset = (await defaultRequest).postDataJSON() as { payload: Record<string, unknown> };
  expect(reset.payload).not.toHaveProperty("model");
  expect(reset.payload).not.toHaveProperty("thinking_level");
  const defaultTurnId = String(reset.payload.turn_id || "");
  expect(defaultTurnId).toBeTruthy();
  await expect.poll(async () => {
    const response = await request.get(`/api/projects/${encodeURIComponent(projectId)}/events?limit=500`);
    if (!response.ok()) return "";
    const data = await response.json();
    return (data.items || []).find((event: { type: string; payload?: { turn_id?: string } }) => (
      ["kanban.agent.turn.completed", "kanban.agent.turn.failed"].includes(event.type) && event.payload?.turn_id === defaultTurnId
    ))?.type || "";
  }, { timeout: 150_000, intervals: [250, 500, 1000] }).toBe("kanban.agent.turn.completed");
  await expect(panel.locator(".agent-text-part").filter({ hasText: "ZF_MODEL_E2E_DEFAULT_OK" }).last()).toBeVisible({ timeout: 30_000 });
  writeFileSync(artifactPath, JSON.stringify({ projectId, turnId, secondTurnId, defaultTurnId, threadKey, model: expectedModel,
    effort: expectedEffort, secondEffort, defaultModel: catalog.session_defaults.model, defaultEffort }));
});
