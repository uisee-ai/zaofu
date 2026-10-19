import { expect, test } from "@playwright/test";

const project = process.env.ZF_CHANNEL_181_PROJECT;
test.skip(!project, "Requires an isolated channel_181_relay.py --real --serve run");

test("five real provider replies survive Web projection and refresh", async ({ page, request }) => {
  const channel = "ch-relay-181";
  const response = await request.get(`/api/projects/${project}/channels/${channel}/conversation?limit=100`);
  expect(response.ok()).toBeTruthy();
  const data = await response.json();
  const roles = ["product_pm", "architect", "critic", "ui_ux", "backend"];
  expect(data.reply_requests.filter((r: { status: string }) => r.status === "completed")).toHaveLength(5);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`/?project=${project}&page=channels&channel=${channel}`);
  for (let pass = 0; pass < 2; pass++) {
    for (const role of roles) {
      const block = page.locator(`.agent-run-block[data-member-id="${role}"]`);
      await expect(block).toHaveCount(1);
      for (const marker of ["REQ-181-A", "REQ-181-B", "DISSENT-181", "30", "24"]) {
        await expect(block).toContainText(marker);
      }
      await expect(block).not.toContainText("zf:channel-contract");
      await expect(block).not.toContainText('"channel_contribution"');
    }
    if (pass === 0) await page.reload();
  }
  expect(errors).toEqual([]);
});
