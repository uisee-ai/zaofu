import { expect, test } from "@playwright/test";
import { installUiFixture } from "./ui-alignment.fixture";

test("model picker remains visible at the screenshot viewport", async ({ page }) => {
  await page.setViewportSize({ width: 896, height: 605 });
  await installUiFixture(page, { snapshot: { runtime: {
    mode: "live", live: true, actions: { mutation_enabled: true, allowed: ["chat-orchestrator"] },
    web_session: { mode: "local_trusted", actions_enabled: true },
    agent_surface: { configured_backend: "codex-headless", permission_profile: "read_only", allowed_actions: ["chat-orchestrator"],
      backends: [{ id: "codex-headless", title: "Codex", available: true, source: "headless" }] },
  } } });
  await page.route("**/webchat/model-catalog**", (route) => route.fulfill({ json: { backend: "codex-headless",
    session_defaults: { model: "m", effort: "xhigh", source: "provider_config" },
    models: [{ model: "m", label: "GPT-6.1 Terra", default: true, default_effort: "low", efforts: ["low", "high", "xhigh"] }],
  } }));
  await page.goto("/?project=ui-review&page=board");
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const panel = page.getByRole("dialog", { name: "Kanban Agent" });
  const trigger = panel.getByRole("button", { name: "Model and thinking effort" });
  await expect(trigger).toContainText("Extra High");
  await trigger.click();
  const menu = panel.getByRole("dialog", { name: "Model and thinking effort" });
  const slider = menu.getByRole("slider", { name: "Thinking effort" });
  await expect(slider).toHaveAttribute("aria-valuetext", "Extra High");
  await expect(menu.locator(".kanban-effort-track")).toHaveCSS("background-image", /linear-gradient/);
  const sliderBox = await slider.boundingBox();
  expect(sliderBox).not.toBeNull();
  await slider.click({ position: { x: 14 + (sliderBox!.width - 28) * 2 / 3, y: sliderBox!.height / 2 } });
  await expect(slider).toHaveAttribute("aria-valuetext", "High");
  for (const element of [trigger, menu, panel.getByRole("button", { name: "Send message", exact: true })]) {
    const box = await element.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(897);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(606);
  }
});

test("invalid action token explains authorization and lets the user unlock again", async ({ page }) => {
  await installUiFixture(page, { locked: true, snapshot: { runtime: {
    mode: "live", live: true,
    actions: { mutation_enabled: true, allowed: ["chat-orchestrator"], requires_token: true },
    web_session: { mode: "token_required", unlocked: false, actions_enabled: false },
    agent_surface: { configured_backend: "codex-headless", permission_profile: "read_only", allowed_actions: ["chat-orchestrator"],
      backends: [{ id: "codex-headless", title: "Codex", available: true, source: "headless" }] },
  } } });
  await page.addInitScript(() => localStorage.setItem("zf.webActionToken", "expired-token"));
  await page.route("**/webchat/model-catalog**", (route) => route.fulfill({ status: 403, json: { status: "unauthorized" } }));
  await page.goto("/?project=ui-review&page=board");
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const panel = page.getByRole("dialog", { name: "Kanban Agent" });
  await panel.getByRole("button", { name: "Model and thinking effort" }).click();
  await expect(panel.getByText("Dashboard authorization was rejected. Unlock again to load Codex models.")).toBeVisible();
  await panel.getByRole("button", { name: "Re-enter action token" }).click();
  await expect(panel.getByPlaceholder("action token", { exact: true })).toBeVisible();
});

test("default displays the actual Codex model and effort without overriding them", async ({ page }) => {
  const { writes } = await installUiFixture(page, { snapshot: { runtime: {
    mode: "live", live: true, actions: { mutation_enabled: true, allowed: ["chat-orchestrator"] },
    web_session: { mode: "local_trusted", actions_enabled: true },
    agent_surface: { configured_backend: "codex-headless", permission_profile: "read_only", allowed_actions: ["chat-orchestrator"],
      backends: [{ id: "codex-headless", title: "Codex", available: true, source: "headless" }] },
  } } });
  await page.route("**/webchat/model-catalog**", (route) => route.fulfill({ json: { backend: "codex-headless",
    session_defaults: { model: "current", effort: "xhigh", source: "provider_config" }, models: [
      { model: "current", label: "Current Model", default: false, default_effort: "low", efforts: ["low", "high", "xhigh"] },
      { model: "recommended", label: "Recommended", default: true, default_effort: "low", efforts: ["low", "high"] },
    ],
  } }));
  await page.goto("/?project=ui-review&page=board");
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const panel = page.getByRole("dialog", { name: "Kanban Agent" });
  const trigger = panel.getByRole("button", { name: "Model and thinking effort" });
  await expect(trigger).toContainText("Current Model");
  await expect(trigger).toContainText("Extra High");
  await trigger.click();
  await expect(panel.getByRole("slider", { name: "Thinking effort" })).toHaveAttribute("aria-valuetext", "Extra High");
  await page.keyboard.press("Escape");
  await panel.locator("textarea.headless-input").fill("inspect defaults");
  await panel.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => writes.find((item) => item.path.endsWith("/actions/chat-orchestrator"))?.body).toBeTruthy();
  const body = writes.find((item) => item.path.endsWith("/actions/chat-orchestrator"))?.body as { payload?: Record<string, unknown> };
  expect(body.payload).not.toHaveProperty("model");
  expect(body.payload).not.toHaveProperty("thinking_level");
});

test("selected model and effort are sent and remain selected across turns and reload", async ({ page }) => {
  const { writes } = await installUiFixture(page, { snapshot: { runtime: {
    mode: "live", live: true,
    actions: { mutation_enabled: true, allowed: ["chat-orchestrator"] },
    web_session: { mode: "local_trusted", actions_enabled: true },
    agent_surface: { configured_backend: "codex-headless", permission_profile: "read_only", allowed_actions: ["chat-orchestrator"],
      backends: [{ id: "codex-headless", title: "Codex", available: true, source: "headless" }] },
  } } });
  await page.route("**/webchat/model-catalog**", (route) => route.fulfill({ json: { backend: "codex-headless", models: [
    { model: "default-model", label: "Default", default: true, default_effort: "low", efforts: ["low", "high"] },
    { model: "other-model", label: "Other", default: false, default_effort: "medium", efforts: ["medium", "high"] },
  ] } }));
  await page.route("**/webchat/runtime**", (route) => route.fulfill({ json: {
    schema_version: "webchat-runtime.v1", project_id: "ui-review", conversation_id: "kanban:ui-review", thread_id: "main",
    capabilities: { actions: [], controls: [], native_steer: false, steer_fallback: "queue", completion_authority: false },
    turns: [], interactions: [], queue: [], threads: [], artifacts: [], messages: [],
    tool_catalog: { schema_version: "webchat-tool-catalog.v1", digest: "fixture", surface: "kanban_agent", credentials_exposed: false, items: [] },
  } }));
  await page.goto("/?project=ui-review&page=board");
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const panel = page.getByRole("dialog", { name: "Kanban Agent" });
  const trigger = panel.getByRole("button", { name: "Model and thinking effort" });
  await trigger.click();
  const menu = panel.getByRole("dialog", { name: "Model and thinking effort" });
  await menu.getByRole("button", { name: "Choose model" }).click();
  await menu.getByRole("button", { name: "Other", exact: true }).click();
  const effort = menu.getByRole("slider", { name: "Thinking effort" });
  await effort.focus();
  await page.keyboard.press("End");
  await expect(effort).toHaveAttribute("aria-valuetext", "High");
  await expect(trigger).toContainText("Other");
  await expect(trigger).toContainText("High");
  await page.keyboard.press("Escape");
  await panel.locator("textarea.headless-input").fill("inspect the project");
  await panel.getByRole("button", { name: "Send message" }).click();
  await expect.poll(() => writes.find((item) => item.path.endsWith("/actions/chat-orchestrator"))?.body).toMatchObject({
    payload: { model: "other-model", thinking_level: "high" },
  });
  await expect(trigger).toContainText("Other");
  await expect(trigger).toContainText("High");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(trigger).toBeVisible();
  await trigger.click();
  await expect(effort).toBeVisible();
  expect(await panel.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  await page.reload();
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.getByRole("button", { name: "Model and thinking effort" })).toContainText("Other");
  await expect(page.getByRole("button", { name: "Model and thinking effort" })).toContainText("High");
});

test("model catalog failure is visible and retry revalidates effort selection", async ({ page }) => {
  await installUiFixture(page, { snapshot: { runtime: {
    mode: "live", live: true,
    actions: { mutation_enabled: true, allowed: ["chat-orchestrator"] },
    web_session: { mode: "local_trusted", actions_enabled: true },
    agent_surface: { configured_backend: "codex-headless", permission_profile: "read_only", allowed_actions: ["chat-orchestrator"],
      backends: [{ id: "codex-headless", title: "Codex", available: true, source: "headless" }] },
  } } });
  let requests = 0;
  await page.route("**/webchat/model-catalog**", (route) => {
    requests += 1;
    return requests === 1 ? route.fulfill({ status: 503, json: { detail: "unavailable" } }) : route.fulfill({ json: {
      backend: "codex-headless", models: [
        { model: "one", label: "One", default: true, default_effort: "low", efforts: ["low", "high"] },
        { model: "two", label: "Two", default: false, default_effort: "medium", efforts: ["medium"] },
      ],
    } });
  });
  await page.goto("/?project=ui-review&page=board");
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const panel = page.getByRole("dialog", { name: "Kanban Agent" });
  await panel.getByRole("button", { name: "Model and thinking effort" }).click();
  await expect(panel.getByText("Model catalog unavailable.")).toBeVisible();
  await panel.getByRole("button", { name: "Retry", exact: true }).click();
  const menu = panel.getByRole("dialog", { name: "Model and thinking effort" });
  await menu.getByRole("button", { name: "Choose model" }).click();
  await menu.getByRole("button", { name: "One", exact: true }).click();
  const effort = menu.getByRole("slider", { name: "Thinking effort" });
  await effort.focus();
  await page.keyboard.press("End");
  await expect(effort).toHaveAttribute("aria-valuetext", "High");
  await menu.getByRole("button", { name: "Choose model" }).click();
  await menu.getByRole("button", { name: "Two", exact: true }).click();
  await expect(effort).toHaveAttribute("aria-valuetext", "Medium");
  await expect(effort).toHaveAttribute("max", "1");
});

test("effort can be selected without changing the session model", async ({ page }) => {
  const { writes } = await installUiFixture(page, { snapshot: { runtime: {
    mode: "live", live: true, actions: { mutation_enabled: true, allowed: ["chat-orchestrator"] },
    web_session: { mode: "local_trusted", actions_enabled: true },
    agent_surface: { configured_backend: "codex-headless", permission_profile: "read_only", allowed_actions: ["chat-orchestrator"],
      backends: [{ id: "codex-headless", title: "Codex", available: true, source: "headless" }] },
  } } });
  await page.route("**/webchat/model-catalog**", (route) => route.fulfill({ json: { backend: "codex-headless", models: [
    { model: "one", label: "One", default: true, default_effort: "low", efforts: ["low", "high"] },
    { model: "two", label: "Two", default: false, default_effort: "high", efforts: ["high", "xhigh"] },
  ] } }));
  await page.goto("/?project=ui-review&page=board");
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const panel = page.getByRole("dialog", { name: "Kanban Agent" });
  await panel.getByRole("button", { name: "Model and thinking effort" }).click();
  const slider = panel.getByRole("slider", { name: "Thinking effort" });
  await expect(slider).toBeEnabled();
  await slider.focus();
  await page.keyboard.press("End");
  await expect(slider).toHaveAttribute("aria-valuetext", "High");
  await page.keyboard.press("Escape");
  await panel.locator("textarea.headless-input").fill("check the project");
  await panel.getByRole("button", { name: "Send message" }).click();
  await expect.poll(() => writes.find((item) => item.path.endsWith("/actions/chat-orchestrator"))?.body).toMatchObject({ payload: { thinking_level: "high" } });
  const body = writes.find((item) => item.path.endsWith("/actions/chat-orchestrator"))?.body as { payload?: Record<string, unknown> };
  expect(body.payload).not.toHaveProperty("model");
});
