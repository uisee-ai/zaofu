import { expect, test, type Locator, type Page } from "@playwright/test";

type Rect = {
  bottom: number;
  height: number;
  left: number;
  right: number;
  top: number;
  width: number;
};

async function openKanbanAgent(page: Page) {
  const projectId = process.env.ZF_E2E_PROJECT_ID;
  await page.goto(projectId ? `/?project=${encodeURIComponent(projectId)}` : "/");
  const openButton = page.getByRole("button", { name: "Open Kanban Agent" });
  await expect(openButton).toBeVisible({ timeout: 90_000 });
  await openButton.click();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toBeVisible();
}

async function box(locator: Locator): Promise<Rect> {
  const rect = await locator.boundingBox();
  expect(rect).not.toBeNull();
  return {
    bottom: (rect?.y ?? 0) + (rect?.height ?? 0),
    height: rect?.height ?? 0,
    left: rect?.x ?? 0,
    right: (rect?.x ?? 0) + (rect?.width ?? 0),
    top: rect?.y ?? 0,
    width: rect?.width ?? 0,
  };
}

async function expectInsideViewport(page: Page, locator: Locator, label: string) {
  const rect = await box(locator);
  const viewport = page.viewportSize();
  expect(viewport, `${label} viewport`).not.toBeNull();
  expect(rect.width, `${label} width`).toBeGreaterThan(0);
  expect(rect.height, `${label} height`).toBeGreaterThan(0);
  expect(rect.left, `${label} left`).toBeGreaterThanOrEqual(0);
  expect(rect.top, `${label} top`).toBeGreaterThanOrEqual(0);
  expect(rect.right, `${label} right`).toBeLessThanOrEqual((viewport?.width ?? 0) + 1);
  expect(rect.bottom, `${label} bottom`).toBeLessThanOrEqual((viewport?.height ?? 0) + 1);
}

async function expectHorizontallyInsideViewport(page: Page, locator: Locator, label: string) {
  const rect = await box(locator);
  const viewport = page.viewportSize();
  expect(viewport, `${label} viewport`).not.toBeNull();
  expect(rect.width, `${label} width`).toBeGreaterThan(0);
  expect(rect.left, `${label} left`).toBeGreaterThanOrEqual(0);
  expect(rect.right, `${label} right`).toBeLessThanOrEqual((viewport?.width ?? 0) + 1);
}

async function expectThreadComposerSeparated(page: Page) {
  const thread = await box(page.locator(".headless-thread"));
  const composer = await box(page.locator(".headless-composer"));
  expect(thread.bottom, "thread should end before composer").toBeLessThanOrEqual(composer.top + 1);
}

async function createSecondThread(page: Page) {
  await page.getByRole("button", { name: "New Kanban Agent chat" }).click();
  await expect(page.getByRole("tablist", { name: "Agent threads" }).getByRole("button"))
    .toHaveCount(2);
}

async function selectSplitThread(page: Page) {
  const splitSelect = page.getByLabel("Compare with thread");
  await expect(splitSelect).toBeVisible();
  const value = await splitSelect.locator("option").nth(1).getAttribute("value");
  expect(value).toBeTruthy();
  await splitSelect.selectOption(value ?? "");
}

async function stubPendingProposal(page: Page) {
  await page.route("**/kanban-agent/pending-proposals", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "kanban-agent.pending-proposals.v1",
        project_id: "layout-test",
        items: [{
          proposal_event_id: "evt-layout-proposal",
          proposal_id: "evt-layout-proposal",
          action: "idea-to-product",
          requested_action: "idea-to-product",
          reason: "Verify pending proposal layout",
          valid: true,
          validation_error: "",
          title: "Start product workflow",
          payload: {},
          refs: {},
        }],
      }),
    });
  });
}

test("Kanban Agent docked and fullscreen layout stays inside the workbench", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openKanbanAgent(page);

  await expectInsideViewport(page, page.locator(".agent-page-shell.docked"), "docked shell");
  const docked = await box(page.locator(".agent-page-shell.docked"));
  expect(docked.width, "docked conversation width").toBeGreaterThanOrEqual(600);
  expect(docked.width, "docked conversation width").toBeLessThanOrEqual(680);
  await expectInsideViewport(page, page.locator(".orchestrator-panel"), "docked panel");
  await expectInsideViewport(page, page.locator(".headless-composer"), "docked composer");
  await expectThreadComposerSeparated(page);

  await page.getByRole("button", { name: "Fullscreen Kanban Agent" }).click();
  await expect(page.locator(".agent-page-shell.fullscreen")).toBeVisible();
  await expectInsideViewport(page, page.locator(".agent-page-shell.fullscreen"), "fullscreen shell");
  await expectInsideViewport(page, page.locator(".orchestrator-panel.fullscreen"), "fullscreen panel");
  await expectInsideViewport(page, page.locator(".headless-composer"), "fullscreen composer");
  await expectThreadComposerSeparated(page);

  await createSecondThread(page);
  await selectSplitThread(page);
  await expect(page.locator(".agent-session-panes.split")).toBeVisible();
  await expect(page.getByRole("button", { name: "Resize split pane" })).toBeVisible();
  await expectHorizontallyInsideViewport(page, page.locator(".agent-session-panes.split"), "split panes");
});

test("Kanban Agent pending proposal card stays separate from the composer", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await stubPendingProposal(page);
  await openKanbanAgent(page);

  const pending = await box(page.getByLabel("Pending proposals"));
  const composer = await box(page.locator(".headless-composer"));
  expect(composer.top - pending.bottom, "proposal card and composer gap").toBeGreaterThanOrEqual(8);
  expect(Math.abs(pending.left - composer.left), "proposal and composer left edges").toBeLessThanOrEqual(1);
  expect(Math.abs(pending.right - composer.right), "proposal and composer right edges").toBeLessThanOrEqual(1);
  await expectInsideViewport(page, page.getByLabel("Pending proposals"), "pending proposal card");
});

test("Kanban Agent session navigation stays fixed and preserves each thread position", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openKanbanAgent(page);
  await createSecondThread(page);

  const navigation = page.getByTestId("agent-thread-navigation");
  const thread = page.locator(".headless-thread");
  const tabs = page.getByRole("tablist", { name: "Agent threads" }).getByRole("button");
  await expect(navigation).toBeVisible();
  await expect(page.getByLabel("Compare with thread")).not.toBeVisible();
  await expect(tabs).toHaveCount(2);
  const initialTitles = await tabs.locator("span:nth-child(2)").allTextContents();
  expect(initialTitles).toEqual(["main", "chat 2"]);
  expect(new Set(initialTitles).size, "session titles should be unique").toBe(initialTitles.length);
  expect(
    await navigation.evaluate((node, scrollNode) => !scrollNode.contains(node), await thread.elementHandle()),
    "session navigation should be outside the transcript scroller",
  ).toBe(true);

  await page.addStyleTag({ content: ".headless-thread .agent-turn-list { min-height: 2400px !important; }" });
  const dockedNavigationTop = (await box(navigation)).top;
  await thread.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await expect.poll(() => thread.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  expect(Math.abs((await box(navigation)).top - dockedNavigationTop)).toBeLessThanOrEqual(1);

  // chat 2 is active after creation. Give each session a different reading
  // position, switch twice, and verify neither selection nor tab order jumps.
  await thread.evaluate((node) => { node.scrollTop = 320; node.dispatchEvent(new Event("scroll")); });
  await tabs.filter({ hasText: "main" }).click();
  await expect(tabs.filter({ hasText: "main" })).toHaveAttribute("aria-selected", "true");
  await expect.poll(() => tabs.locator("span:nth-child(2)").allTextContents()).toEqual(initialTitles);
  await expect.poll(() => thread.evaluate((node) => node.scrollHeight - node.clientHeight)).toBeGreaterThan(900);
  await thread.evaluate((node) => { node.scrollTop = 700; node.dispatchEvent(new Event("scroll")); });
  await tabs.filter({ hasText: "chat 2" }).click();
  await expect(tabs.filter({ hasText: "chat 2" })).toHaveAttribute("aria-selected", "true");
  await expect.poll(async () => Math.round(await thread.evaluate((node) => node.scrollTop))).toBe(320);
  await expect.poll(() => tabs.locator("span:nth-child(2)").allTextContents()).toEqual(initialTitles);

  await page.getByRole("button", { name: "Fullscreen Kanban Agent" }).click();
  await expect(navigation).toBeVisible();
  await expect(page.getByLabel("Compare with thread")).toBeVisible();
  const fullscreenNavigationTop = (await box(navigation)).top;
  await thread.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await expect.poll(() => thread.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  expect(Math.abs((await box(navigation)).top - fullscreenNavigationTop)).toBeLessThanOrEqual(1);
});

test("Kanban Agent mobile fullscreen stacks split panes without overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 });
  await openKanbanAgent(page);
  await page.getByRole("button", { name: "Fullscreen Kanban Agent" }).click();
  await createSecondThread(page);
  await selectSplitThread(page);

  await expectInsideViewport(page, page.locator(".agent-page-shell.fullscreen"), "mobile shell");
  await expectInsideViewport(page, page.locator(".orchestrator-panel.fullscreen"), "mobile panel");
  await expectInsideViewport(page, page.locator(".headless-composer"), "mobile composer");
  await expectThreadComposerSeparated(page);
  await expect(page.getByRole("button", { name: "Resize split pane" })).not.toBeVisible();
});

test("Kanban Agent light mode backend menu keeps readable foreground and background", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 820 });
  await page.addInitScript(() => {
    window.localStorage.setItem("zf.themeMode", "light");
  });
  await openKanbanAgent(page);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

  await page.getByRole("button", { name: /Agent backend/ }).click();
  const menu = page.getByRole("listbox", { name: "Kanban Agent backend options" });
  await expect(menu).toBeVisible();
  await expectInsideViewport(page, menu, "backend menu");
  await expect(menu.getByRole("option", { name: /Claude/ })).toBeVisible();
  await expect(menu.getByRole("option", { name: /Codex/ })).toBeVisible();

  const colors = await menu.locator(".agent-model-menu-item").first().evaluate((node) => {
    const item = window.getComputedStyle(node);
    const capability = window.getComputedStyle(node.querySelector(".agent-model-capability") as Element);
    const menuStyle = window.getComputedStyle(node.parentElement as Element);
    return {
      itemColor: item.color,
      itemBackground: item.backgroundColor,
      menuBackground: menuStyle.backgroundColor,
      capabilityColor: capability.color,
    };
  });
  expect(colors.menuBackground).not.toBe("rgba(0, 0, 0, 0)");
  expect(colors.itemColor).not.toBe(colors.menuBackground);
  expect(colors.capabilityColor).not.toBe(colors.menuBackground);
});
