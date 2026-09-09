import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const css = [
  "../src/styles/00-tokens-base.css",
  "../src/styles/24-issue-triage.css",
].map((path) => readFileSync(resolve(here, path), "utf8")).join("\n");

function shell(body: string): string {
  return `<html><head><style>${css}</style></head><body><section class="issue-triage-shell">${body}</section></body></html>`;
}

test("Issue labels fold after three into a clickable Labels popover", async ({ page }) => {
  await page.setContent(shell(`
    <span class="issue-label-row foldable" aria-label="GitHub labels">
      <button class="badge issue-label-badge issue-triage-tooltip" data-tooltip="Show only issues labelled “p1”">p1</button>
      <button class="badge issue-label-badge issue-triage-tooltip" data-tooltip="Show only issues labelled “p2”">p2</button>
      <button class="badge issue-label-badge issue-triage-tooltip" data-tooltip="Show only issues labelled “bug”">bug</button>
      <span class="issue-label-overflow-menu">
        <button aria-haspopup="menu" aria-label="Show 2 more labels" class="badge issue-label-more">+2</button>
        <span class="issue-label-overflow-popover" role="menu" aria-label="Labels">
          <strong>Labels</strong>
          <span class="issue-label-overflow-options">
            <button aria-label="Filter issues by label p1" class="badge issue-label-badge" role="menuitem">p1</button>
            <button aria-label="Filter issues by label p2" class="badge issue-label-badge" role="menuitem">p2</button>
            <button aria-label="Filter issues by label bug" class="badge issue-label-badge" role="menuitem">bug</button>
            <button aria-label="Filter issues by label performance" class="badge issue-label-badge" role="menuitem">performance</button>
            <button aria-label="Filter issues by label unknown" class="badge issue-label-badge" role="menuitem">unknown</button>
          </span>
        </span>
      </span>
    </span>
  `));
  const trigger = page.getByRole("button", { name: "Show 2 more labels" });
  const popover = page.getByRole("menu", { name: "Labels" });
  await expect(popover).toBeHidden();
  await expect(trigger).toBeVisible();
  await trigger.hover();
  await expect(popover).toBeVisible();
  await expect(popover.getByRole("menuitem", { name: "Filter issues by label p1" })).toBeVisible();
  await expect(popover.getByRole("menuitem", { name: "Filter issues by label performance" })).toBeVisible();
  await expect(popover.getByRole("menuitem", { name: "Filter issues by label unknown" })).toBeVisible();
  await popover.getByRole("menuitem", { name: "Filter issues by label performance" }).hover();
  await expect(popover).toBeVisible();
  await page.getByRole("button", { name: "p1" }).hover();
  expect(await page.getByRole("button", { name: "p1" }).evaluate((node) => getComputedStyle(node, "::after").content)).toContain("Show only issues");
});

test("toolbar hints and permanent cancellation warning are visually explicit", async ({ page }) => {
  await page.setContent(shell(`
    <header class="issue-triage-header"><div class="button-row issue-triage-toolbar-actions">
      <button class="icon-button issue-triage-tooltip" data-tooltip="Fetch the latest Issue metadata and comments from GitHub">Refresh</button>
      <button class="icon-button primary issue-triage-tooltip" data-tooltip="Manually queue the selected or specified GitHub Issue for read-only Triage">Start Triage</button>
      <a class="issue-triage-repository-action"><span class="issue-triage-repository-label">View Repo</span><span class="issue-triage-repository-stat">2</span></a>
      <a class="icon-button">New issue</a>
    </div></header>
    <div class="issue-run-permanent-warning">
      <strong>Permanent cancellation</strong>
      <p>This cannot be resumed. Cancellation does not roll back files already written to the local worktree.</p>
      <label><input type="checkbox" />I understand this Run is permanently cancelled and local files are not reverted.</label>
      <button class="icon-button danger" disabled>Cancel permanently</button>
    </div>
  `));
  const refresh = page.getByRole("button", { name: "Refresh" });
  await refresh.hover();
  expect(await refresh.evaluate((node) => getComputedStyle(node, "::after").opacity)).toBe("0");
  await page.waitForTimeout(550);
  expect(await refresh.evaluate((node) => getComputedStyle(node, "::after").opacity)).toBe("1");
  expect(await refresh.evaluate((node) => getComputedStyle(node, "::after").content)).toContain("latest Issue metadata");
  expect(await refresh.evaluate((node) => getComputedStyle(node, "::after").whiteSpace)).toBe("normal");
  expect(await refresh.evaluate((node) => getComputedStyle(node, "::after").right)).toBe("0px");
  await page.getByRole("button", { name: "Start Triage" }).hover();
  await page.waitForTimeout(550);
  expect(await page.getByRole("button", { name: "Start Triage" }).evaluate((node) => getComputedStyle(node, "::after").content)).toContain("read-only Triage");
  const toolbarFontSize = await page.getByRole("button", { name: "Start Triage" }).evaluate((node) => getComputedStyle(node).fontSize);
  for (const label of ["Refresh", "View Repo", "New issue"]) {
    expect(await page.getByText(label, { exact: true }).evaluate((node) => getComputedStyle(node).fontSize)).toBe(toolbarFontSize);
  }
  expect(await page.locator(".issue-triage-repository-stat").evaluate((node) => getComputedStyle(node).fontSize)).toBe(toolbarFontSize);
  const warning = page.locator(".issue-run-permanent-warning");
  await expect(warning).toContainText("cannot be resumed");
  await expect(warning).toContainText("does not roll back files");
  await expect(page.getByRole("button", { name: "Cancel permanently" })).toBeDisabled();
  expect(Number.parseFloat(await warning.evaluate((node) => getComputedStyle(node).borderTopWidth))).toBeGreaterThanOrEqual(2);
});

test("multiple stage cards open floating four-row menus without moving the page", async ({ page }) => {
  await page.setContent(shell(`
    <div class="issue-state-stages">
      <section class="issue-state-stage stage-triage open">
        <button class="issue-state-stage-select"><span>Triage</span><strong>5</strong></button>
        <button class="issue-state-stage-toggle"><span>5 states</span><span>⌄</span></button>
        <div class="issue-state-stage-options">
          <button class="issue-triage-tooltip" data-tooltip="Queued for Triage"><span>Triage queued</span><strong>1</strong></button>
          <button><span>Triaging</span><strong>1</strong></button>
          <button><span>Triage paused</span><strong>1</strong></button>
          <button><span>Triage recovering</span><strong>1</strong></button>
          <button><span>Needs another row</span><strong>1</strong></button>
        </div>
      </section>
      <section class="issue-state-stage stage-fix open">
        <button class="issue-state-stage-select"><span>Fix</span><strong>2</strong></button>
        <button class="issue-state-stage-toggle"><span>5 states</span><span>⌄</span></button>
        <div class="issue-state-stage-options"><button><span>Fixing</span><strong>2</strong></button></div>
      </section>
      <section class="issue-state-stage stage-delivery"><button class="issue-state-stage-select"><span>Delivery</span><strong>1</strong></button><button class="issue-state-stage-toggle"><span>9 states</span></button></section>
    </div>
    <div class="issue-triage-filters" style="margin-top:16px"><form><input /></form></div>
  `));
  const strip = page.locator(".issue-state-stages");
  const cards = strip.locator(".issue-state-stage");
  expect(await strip.evaluate((node) => getComputedStyle(node).display)).toBe("grid");
  expect((await cards.nth(1).boundingBox())?.x).toBeGreaterThan((await cards.nth(0).boundingBox())?.x ?? 0);
  expect(Math.abs((await cards.nth(0).boundingBox())!.height - (await cards.nth(1).boundingBox())!.height)).toBeLessThanOrEqual(1);
  const menus = page.locator(".issue-state-stage-options");
  await expect(menus).toHaveCount(2);
  expect(await menus.nth(0).evaluate((node) => getComputedStyle(node).position)).toBe("absolute");
  expect(await menus.nth(0).evaluate((node) => getComputedStyle(node).overflowY)).toBe("auto");
  expect((await menus.nth(0).boundingBox())!.height).toBeLessThan(5 * 36);
  expect((await page.locator(".issue-triage-filters").boundingBox())!.y)
    .toBeLessThan((await menus.nth(0).boundingBox())!.y + (await menus.nth(0).boundingBox())!.height);
  const stageColor = await cards.nth(0).evaluate((node) => getComputedStyle(node).color);
  expect(await cards.nth(0).locator(".issue-state-stage-select").evaluate((node) => getComputedStyle(node).color)).toBe(stageColor);
  expect(await menus.nth(0).locator("button").first().evaluate((node) => getComputedStyle(node).color))
    .toBe(await page.locator("body").evaluate((node) => getComputedStyle(node).color));
  const stageState = menus.nth(0).locator("button").first();
  await stageState.hover();
  await page.waitForTimeout(600);
  expect(await stageState.evaluate((node) => getComputedStyle(node, "::after").opacity)).toBe("0");
  await page.waitForTimeout(450);
  expect(await stageState.evaluate((node) => getComputedStyle(node, "::after").opacity)).toBe("1");
  await page.setContent(shell(`
    <div class="issue-state-stage-options"></div>
    <div class="issue-author-popover floating"></div>
    <div class="issue-floating-tooltip"></div>
  `));
  const stageLayer = Number(await page.locator(".issue-state-stage-options").evaluate((node) => getComputedStyle(node).zIndex));
  const authorLayer = Number(await page.locator(".issue-author-popover").evaluate((node) => getComputedStyle(node).zIndex));
  const tooltipLayer = Number(await page.locator(".issue-floating-tooltip").evaluate((node) => getComputedStyle(node).zIndex));
  expect(authorLayer).toBeGreaterThan(stageLayer);
  expect(tooltipLayer).toBeGreaterThan(stageLayer);
});

test("issue list and detail are equal independently scrolling panes without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 800 });
  await page.setContent(shell(`
    <div class="issue-triage-content with-detail" style="height: 480px">
      <div class="issue-triage-list"><div style="height: 900px">Issues</div></div>
      <aside class="issue-triage-detail">
        <dl class="issue-triage-meta"><div class="issue-triage-detail-triage"><dt>Triage</dt><dd><button class="issue-detail-id-link">TRIAGE-0123456789ABCDEF</button></dd></div></dl>
        <section class="issue-candidate-delivery"><div class="issue-candidate-pr-status"><a href="#">PR #26 · pr open</a><span>GitHub review: pending (0)</span><button class="icon-button issue-triage-tooltip" data-tooltip="Read the current PR and review state from GitHub">Refresh PR</button></div></section>
        <div style="height: 900px">Detail</div>
      </aside>
    </div>
  `));
  const content = page.locator(".issue-triage-content");
  const list = page.locator(".issue-triage-list");
  const detail = page.locator(".issue-triage-detail");
  const listBox = await list.boundingBox();
  const detailBox = await detail.boundingBox();
  expect(Math.abs((listBox?.width ?? 0) - (detailBox?.width ?? 0))).toBeLessThanOrEqual(1);
  expect(await list.evaluate((node) => getComputedStyle(node).overflowY)).toBe("auto");
  expect(await detail.evaluate((node) => getComputedStyle(node).overflowY)).toBe("auto");
  expect(await content.evaluate((node) => node.scrollWidth)).toBe(await content.evaluate((node) => node.clientWidth));
  expect(await page.locator(".issue-detail-id-link").evaluate((node) => getComputedStyle(node).color))
    .toBe(await detail.evaluate((node) => getComputedStyle(node).color));
  const prRow = page.locator(".issue-candidate-pr-status");
  const prLink = prRow.locator("a");
  expect((await prLink.boundingBox())?.width).toBeLessThan((await prRow.boundingBox())?.width ?? 0);
  const refresh = page.getByRole("button", { name: "Refresh PR" });
  await refresh.hover();
  expect(await refresh.evaluate((node) => getComputedStyle(node, "::after").whiteSpace)).toBe("normal");
  expect(await refresh.evaluate((node) => getComputedStyle(node, "::after").overflowWrap)).toBe("anywhere");
  expect(await refresh.evaluate((node) => getComputedStyle(node, "::after").boxSizing)).toBe("border-box");
});

test("the full Triage page scrolls while both issue panes retain local scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 700 });
  await page.setContent(shell(`
    <section class="issue-triage-page">
      <div style="height:300px;flex:0 0 auto">Controls</div>
      <div class="issue-triage-content with-detail">
        <div class="issue-triage-list"><div style="height:900px">Issues</div></div>
        <aside class="issue-triage-detail"><div style="height:900px">Detail</div></aside>
      </div>
    </section>
  `));
  await page.locator(".issue-triage-shell").evaluate((node) => {
    (node as HTMLElement).style.height = "500px";
  });
  const triagePage = page.locator(".issue-triage-page");
  expect(await triagePage.evaluate((node) => getComputedStyle(node).overflowY)).toBe("auto");
  expect(await triagePage.evaluate((node) => node.scrollHeight)).toBeGreaterThan(await triagePage.evaluate((node) => node.clientHeight));
  expect(await page.locator(".issue-triage-list").evaluate((node) => getComputedStyle(node).overflowY)).toBe("auto");
  expect(await page.locator(".issue-triage-detail").evaluate((node) => getComputedStyle(node).overflowY)).toBe("auto");
});
