import { expect, test, type Locator } from "@playwright/test";

const expectedJson = [
  "{",
  '  "status": "healthy",',
  '  "details": {',
  '    "surface": "shared",',
  '    "items": [',
  "      1,",
  "      2",
  "    ]",
  "  }",
  "}",
].join("\n");

// Highlighting may use separate block spans without newline text nodes.
// Verify visible line positions and copied bytes, rather than that DOM detail.
async function renderedLineCount(code: Locator) {
  return code.evaluate(element => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const positions = new Set<number>();
    while (walker.nextNode()) {
      if (!walker.currentNode.textContent?.trim()) continue;
      const range = document.createRange(); range.selectNodeContents(walker.currentNode);
      for (const rect of range.getClientRects()) if (rect.width && rect.height) positions.add(Math.round(rect.y));
    }
    return positions.size;
  });
}

test("standalone JSON renders on Kanban Agent and Channel Group timelines", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/?fixture=agent-session");

  for (const testId of ["fx-kanban-compact", "fx-channel-compact"]) {
    const surface = page.getByTestId(testId);
    const block = surface.locator(".agent-code-block").filter({
      hasText: '"surface": "shared"',
    });
    await expect(block).toHaveCount(1);
    await expect(block.locator(".agent-code-header > span")).toHaveText("json");
    await block.scrollIntoViewIfNeeded();
    await expect.poll(() => renderedLineCount(block.locator("pre > code"))).toBeGreaterThanOrEqual(10);
    await block.getByTitle("Copy code", { exact: true }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(expectedJson);
  }
});

test("historical compact JSON fences remain readable without creating an approval", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/?fixture=agent-session");
  for (const testId of ["fx-kanban-compact", "fx-channel-compact"]) {
    const surface = page.getByTestId(testId);
    const block = surface.locator(".agent-code-block").filter({hasText: "legacy-reviewer"});
    await expect(block).toHaveCount(1);
    await expect(block.locator(".agent-code-header small")).not.toHaveText("1 lines");
    await block.scrollIntoViewIfNeeded();
    await expect.poll(() => renderedLineCount(block.locator("pre > code"))).toBeGreaterThan(5);
    await block.getByTitle("Copy code", { exact: true }).click();
    const code = await page.evaluate(() => navigator.clipboard.readText());
    expect(code?.split("\n").length).toBeGreaterThan(5);
    expect(JSON.parse(code || "").action_proposal.payload.role_markdown).toBe("第一段\n\n第二段");
    await expect(surface.getByText("历史角色注册草稿，尚未执行。")).toBeVisible();
    await expect(surface.getByRole("button", {name: "Approve", exact: true})).toHaveCount(0);
  }
});
