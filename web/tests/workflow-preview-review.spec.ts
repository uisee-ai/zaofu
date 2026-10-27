import { expect, test } from "@playwright/test";
const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST || "";
test.skip(!token, "Requires an isolated API with the deterministic preview review provider.");
test("YAML reply stays inside Raw and retains text when the panel is reopened", async ({ page, request }) => {
  const id = `yaml-reply-${Date.now()}`;
  const result = await request.post("/api/projects/default/actions/workflow-request", {
    headers: { "x-zf-web-token": token }, data: { project_id: "default", actor: "playwright", idempotency_key: id,
      payload: { request_id: id, kind: "issue", objective: "Review checkout expiry", backend: "mock", allow_missing_env: true,
        acceptance: ["Active sessions remain valid."], constraints: ["Preserve API."] } },
  });
  expect(result.ok(), await result.text()).toBeTruthy();
  await page.addInitScript(value => localStorage.setItem("zf.webActionToken", value), token);
  await page.goto(`/?page=workflows&project=default&request=${id}`);
  await expect(page.getByRole("button", { name: "Agent", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Raw", exact: true }).click();
  const raw = page.getByRole("complementary", { name: "Raw YAML", exact: true });
  await expect(raw.getByRole("button", { name: "Details", exact: true })).toHaveCount(0);
  await raw.getByRole("button", { name: "Agent", exact: true }).click();
  const reply = raw.getByRole("dialog", { name: "Workflow Agent" });
  await expect(reply.getByRole("textbox")).toHaveCount(1);
  await reply.getByRole("textbox").fill("Explain lanes; do not modify yet.");
  await page.keyboard.press("Escape");
  await expect(reply).toBeHidden();
  await page.getByRole("button", { name: "Close workflow panel" }).click();
  await expect(page.getByRole("dialog", { name: "Workflow Agent" })).toHaveCount(0);
  await page.getByRole("button", { name: "Raw", exact: true }).click();
  await raw.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(reply.getByRole("textbox")).toHaveValue("Explain lanes; do not modify yet.");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(raw.locator(".workflow-raw-yaml pre")).toBeInViewport();
  await expect(reply.getByRole("textbox")).toBeInViewport();
  expect((await reply.boundingBox())!.height).toBeLessThan(260);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await raw.screenshot({ path: test.info().outputPath("yaml-reply-mobile.png") });
});
