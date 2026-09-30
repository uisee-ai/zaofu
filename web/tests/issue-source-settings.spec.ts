import { test, expect } from "@playwright/test";

test("source setting requires inspection, saves and survives refresh", async ({ page }) => {
  await page.goto("/tests/fixtures/issue-source-settings/index.html");
  await expect(page.getByText("/workspace/fixture", { exact: true })).toBeVisible();
  const publication = page.getByRole("region", { name: "Issue publication repositories", exact: true });
  await expect(publication).toContainText("example/issue-reports");
  await expect(page.getByText("Source remote (origin)", { exact: true })).toBeVisible();
  await page.getByLabel("Source mode").selectOption("explicit_target");
  await page.getByLabel("Source directory").fill("/workspace/product");
  await expect(publication).toContainText("example/issue-reports");
  const save = page.getByRole("button", { name: "Save source directory" });
  await expect(save).toBeDisabled();
  await page.getByRole("button", { name: "Check directory" }).click();
  await expect(page.getByText("https://github.com/example/product.git", { exact: true })).toBeVisible();
  await expect(page.getByText("Yes, including untracked files", { exact: true })).toBeVisible();
  await expect(page.getByText("abc123", { exact: true })).toBeVisible();
  await save.click();
  await expect(page.getByRole("status")).toContainText("Saved for new Issue sessions");
  await expect(publication).toContainText("example/issue-reports");
  await page.reload();
  await expect(page.getByLabel("Source directory")).toHaveValue("/workspace/product");
  await expect(publication).toContainText("example/issue-reports");
  await page.getByLabel("Source mode").selectOption("current_project");
  await page.getByRole("button", { name: "Check directory" }).click();
  await save.click();
  await page.reload();
  await expect(page.getByLabel("Source mode")).toHaveValue("current_project");
});

test("missing publication target never falls back to source origin", async ({ page }) => {
  await page.goto("/tests/fixtures/issue-source-settings/index.html");
  await page.evaluate(() => localStorage.setItem("no-publication-target", "true"));
  await page.reload();
  await expect(page.getByRole("region", { name: "Issue publication repositories", exact: true })).toContainText("No publication target loaded. No fallback to the source origin.");
  await expect(page.getByText("https://github.com/example/product.git", { exact: true })).toBeVisible();
});

test("invalid paths and concurrent config edits cannot silently save", async ({ page }) => {
  await page.goto("/tests/fixtures/issue-source-settings/index.html");
  await page.getByLabel("Source mode").selectOption("explicit_target");
  await page.getByLabel("Source directory").fill("/missing");
  await page.getByRole("button", { name: "Check directory" }).click();
  await expect(page.getByRole("alert")).toContainText("源码目录不存在");
  await expect(page.getByRole("button", { name: "Save source directory" })).toBeDisabled();
  await page.getByLabel("Source directory").fill("/workspace/product");
  await page.getByRole("button", { name: "Check directory" }).click();
  await page.evaluate(() => localStorage.setItem("simulate-config-conflict", "true"));
  await page.getByRole("button", { name: "Save source directory" }).click();
  await expect(page.getByRole("alert")).toContainText("zf.yaml 已变化");
  expect(await page.evaluate(() => localStorage.getItem("issue-source"))).toBeNull();
});
