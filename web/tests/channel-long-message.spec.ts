import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("long public text remains accessible and downloadable after refresh", async ({ page }) => {
  await page.goto("/?fixture=agent-session&long=1");
  const message = page.getByTestId("fx-long-message");
  await expect(message.getByRole("button", { name: "下一页" })).toBeVisible();
  await message.getByRole("button", { name: "下一页" }).click();
  await expect(message.locator("pre")).toHaveText("😀\nTAIL-181-😀");
  await expect(message.locator("pre")).toContainText("TAIL-181-😀");
  const download = page.waitForEvent("download");
  await message.getByRole("button", { name: "下载完整正文" }).click();
  const saved = await (await download).path();
  expect(await readFile(saved!, "utf-8")).toBe("完整原文\n".repeat(39_999) + "边界汉字😀\nTAIL-181-😀");
  await page.reload();
  await message.getByRole("button", { name: "下一页" }).click();
  await expect(message.locator("pre")).toContainText("TAIL-181-😀");
});
