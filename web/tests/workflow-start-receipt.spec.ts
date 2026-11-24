import { expect, test } from "@playwright/test";

test("an accepted request awaiting the consumer does not claim the workflow started", async ({ page }) => {
  await page.goto("/?fixture=agent-session");
  const receipt = page.getByTestId("fx-workflow-request");
  await expect(receipt).toContainText("Workflow start requested");
  await expect(receipt).not.toContainText("Workflow started");
  await page.reload();
  await expect(receipt).toContainText("Workflow start requested");
  await expect(receipt.getByRole("button", { name: "Start workflow" })).toHaveCount(0);
});

for (const proposedBackend of [undefined, "claude-headless"]) {
  test(`pending proposal preserves its backend (${proposedBackend ?? "unspecified"})`, async ({ page }) => {
    await page.addInitScript((token) => {
      localStorage.setItem("zf.webActionToken", token);
      localStorage.setItem("zf.operatorBackend", "codex-headless");
    }, process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "test-token");
    await page.route("**/kanban-agent/pending-proposals", (route) => route.fulfill({
      json: {
        schema_version: "kanban-agent.pending-proposals.v1",
        items: [{
          proposal_event_id: "evt-backend-proposal",
          proposal_id: "backend-proposal",
          action: "workflow-start",
          valid: true,
          title: "Start workflow",
          payload: { task_id: "TASK-PROPOSED", backend: proposedBackend },
        }],
      },
    }));
    let submitted: Record<string, unknown> | undefined;
    await page.route("**/actions/workflow-start", async (route) => {
      submitted = route.request().postDataJSON().payload;
      await route.fulfill({ json: { ok: true, status: "accepted", run_id: "workflow-test" } });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Open zaofu", exact: true }).click();
    await page.getByLabel("Pending proposals").getByRole("button", { name: "Start workflow", exact: true }).click();
    await expect.poll(() => submitted).toBeDefined();
    expect(submitted?.backend).toBe(proposedBackend);
    expect(submitted?.task_id).toBe("TASK-PROPOSED");
    expect(submitted?.proposal_event_id).toBe("evt-backend-proposal");
  });
}
