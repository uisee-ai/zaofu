import { expect, test } from "@playwright/test";

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

test("standalone JSON renders on Kanban Agent and Channel Group timelines", async ({ page }) => {
  await page.goto("/?fixture=agent-session");

  for (const testId of ["fx-kanban-compact", "fx-channel-compact"]) {
    const surface = page.getByTestId(testId);
    const block = surface.locator(".agent-code-block").filter({
      hasText: '"surface": "shared"',
    });
    await expect(block).toHaveCount(1);
    await expect(block.locator(".agent-code-header > span")).toHaveText("json");
    await expect(block.locator("pre > code")).toHaveText(expectedJson);
  }
});

test("historical compact JSON fences remain readable without creating an approval", async ({ page }) => {
  await page.goto("/?fixture=agent-session");
  for (const testId of ["fx-kanban-compact", "fx-channel-compact"]) {
    const surface = page.getByTestId(testId);
    const block = surface.locator(".agent-code-block").filter({hasText: "legacy-reviewer"});
    await expect(block).toHaveCount(1);
    await expect(block.locator(".agent-code-header small")).not.toHaveText("1 lines");
    const code = await block.locator("pre > code").textContent();
    expect(code?.split("\n").length).toBeGreaterThan(5);
    expect(JSON.parse(code || "").action_proposal.payload.role_markdown).toBe("第一段\n\n第二段");
    await expect(surface.getByText("历史角色注册草稿，尚未执行。")).toBeVisible();
    await expect(surface.getByRole("button", {name: "Approve", exact: true})).toHaveCount(0);
  }
});
