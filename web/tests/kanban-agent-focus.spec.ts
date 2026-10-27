import { expect, test } from "@playwright/test";
import { installUiFixture } from "./ui-alignment.fixture";

test("Kanban Agent keeps the composer border unchanged while typing", async ({ page }) => {
  test.setTimeout(90_000);
  const { writes, errors } = await installUiFixture(page);
  await page.route("**/webchat/runtime**", route => route.fulfill({ json: {
    schema_version: "webchat-runtime.v1", project_id: "ui-review", conversation_id: "kanban:ui-review", thread_id: "main",
    capabilities: { actions: [], controls: [], native_steer: false, steer_fallback: "queue", completion_authority: true },
    turns: [], interactions: [], queue: [], threads: [], artifacts: [], messages: [],
    tool_catalog: { schema_version: "agent-tool-catalog.v1", digest: "fixture", surface: "kanban_agent", credentials_exposed: false, items: [] },
  } }));
  for (const [width, theme] of [[1440, "light"], [390, "dark"]] as const) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/?project=ui-review&page=board");
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await page.getByRole("button", { name: "Open Kanban Agent" }).click();
    const composer = page.locator(".headless-composer");
    const input = composer.locator("textarea.headless-input");
    await expect(input).toBeVisible({ timeout: 30_000 });
    await expect(input).toBeEnabled();
    await input.blur();
    const previousStyle = await composer.evaluate(element => ({
      borderColor: getComputedStyle(element).borderColor,
      boxShadow: getComputedStyle(element).boxShadow,
    }));
    await input.focus();
    await expect(input).toBeFocused();
    await input.fill("Test message");
    await expect(input).toHaveValue("Test message");
    await expect(input).toHaveCSS("outline-style", "none");
    await expect(composer).toHaveCSS("border-color", previousStyle.borderColor);
    await expect(composer).toHaveCSS("box-shadow", previousStyle.boxShadow);
  }
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});
