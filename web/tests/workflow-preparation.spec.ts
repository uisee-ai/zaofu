import { expect, test } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST || "";
test.skip(!token, "Requires the isolated workflow-prepare-server.py fixture.");

for (const action of ["create-task", "idea-to-product"]) {
test(`${action} asks, resumes, previews complete YAML, then starts only after approval`, async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.addInitScript((value) => localStorage.setItem("zf.webActionToken", value), token);
  const workspace = await (await request.get("/api/workspace/projects")).json();
  const project = workspace.server_default_project_id;
  const response = await request.post(`/api/projects/${project}/actions/${action}`, {
    headers: { "x-zf-web-token": token }, data: {
      project_id: project, actor: "web", idempotency_key: `prepare-browser-task-${Date.now()}`,
      payload: action === "idea-to-product" ? { objective: "Keep active sessions valid", backend: "mock" } : { title: "Keep active sessions valid", execution_mode: "workflow", prepare_workflow: true,
        backend: "mock", contract: { behavior: "Keep active sessions valid",
          verification: "python -m pytest -q", verification_tiers: ["runtime"], owner_instance: "fix-lane-0",
          spec_skip_reason: "Isolated preparation browser fixture", acceptance_criteria: ["Active sessions remain valid"] } },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const created = await response.json();
  const rid = created.request_id || created.workflow_preparation?.request_id;
  expect(rid).toBeTruthy();
  await page.goto(`/?page=workflows&project=${encodeURIComponent(project)}&request=${encodeURIComponent(rid)}`);
  await page.getByRole("button", { name: "Answer Questions", exact: true }).click();
  const clarification = page.getByTestId("workflow-clarification");
  await expect(clarification).toBeVisible({ timeout: 25_000 });
  await expect(clarification).toContainText("Which session behavior should be preserved?");
  const before = await (await request.get(`/api/projects/${project}/workflow-requests/${rid}`)).json();
  expect(before.lifecycle.submitted).toBe(false);
  await clarification.getByPlaceholder("Type a concrete answer.").fill("Keep active sessions valid; reject expired sessions.");
  await clarification.getByRole("button", { name: "Save & prepare proposal" }).click();
  await page.getByRole("button", { name: "Close workflow panel" }).click();
  await page.getByRole("button", { name: "Raw", exact: true }).click();
  const yaml = page.getByTestId("workflow-yaml-preview");
  await expect(yaml).toBeVisible({ timeout: 30_000 });
  await expect(yaml.locator("pre")).toContainText("kind: IssueFlow", { timeout: 30_000 });
  await expect(yaml.locator("pre")).toContainText("kind: ZfConfig");
  const detail = await (await request.get(`/api/projects/${project}/workflow-requests/${rid}`)).json();
  expect(detail.result.revision).toBeGreaterThan(before.result.revision);
  expect(detail.requirement.clarification_answers).toHaveLength(1);
  expect(detail.lifecycle.submitted).toBe(false);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBeTruthy();
  await expect(page.getByTestId("workflow-start")).toBeEnabled();
  await page.getByTestId("workflow-start").click();
  await page.getByRole("button", { name: "Run Controls", exact: true }).click();
  await expect(page.getByTestId("workflow-open-run")).toBeVisible({ timeout: 20_000 });
  const submitted = await (await request.get(`/api/projects/${project}/workflow-requests/${rid}`)).json();
  expect(submitted.lifecycle.submitted).toBe(true);
});
}
