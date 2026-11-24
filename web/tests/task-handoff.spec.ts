import { installUiFixture } from "./ui-alignment.fixture";
import { expect, test } from "@playwright/test";

test("current task handoff links its workspace, target, inputs and partial failures", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  let inputRequests = 0;
  let stale = false;
  const task = { id: "TASK-HANDOFF", title: "核对任务接力", status: "in_progress", assigned_to: "verify-2",
    phase: "implement", active_dispatch_id: "dispatch-current", blocked_by: [], skills_required: [] };
  const response = () => ({ task, events: [], contract: {}, evidence: {}, links: {}, briefing: {}, diagnostics: {},
    execution_route: { current_stage: "verify", current_stage_label: "verify", linear: [], dag: { nodes: [], edges: [] },
      swimlanes: [], source_events: [], empty: true },
    task_run_panel: { status: "in_progress", current_stage: "verify", route_summary: { current_stage: "verify" } },
    handoff_summary: { schema_version: "handoff-summary.v1", task_id: task.id, objective: task.title,
      current_state: "in_progress", current_stage: "verify", owner: { role: "verify", instance_id: "verify-2" },
      completed: [{ kind: "admitted_result", summary: "verify · generation 1 · failed" }],
      next_required_event: "task.pipeline.verify.completed", next_required_action: "等待当前阶段提交并接纳结果",
      pipeline: { status: "current", operation_status: "running", current_stage: "verify", pipeline_stage: "verify_active", workflow_run_id: "run-current",
        attempt_id: "attempt-current", dispatch_id: "dispatch-current", target_ref: "target-current",
        workspace: stale ? { status: "unavailable", reason: "task workspace metadata does not match the current dispatch" }
          : { status: "bound", path: "/custom-state/execution/tasks/TASK-HANDOFF/g1/project" } },
      input_recovery: { status: stale ? "rebind_required" : "bound", required_visible: 3, historical_missing: 1,
        current_memory: "unknown", checkpoint: { status: "captured" } } },
  });
  await installUiFixture(page, { locked: true, snapshot: { tasks: [task] } });
  await page.route("**/api/projects/ui-review/attempts/*/inputs?*", route => {
    inputRequests++;
    expect(route.request().url()).toContain("/attempt-current/");
    return route.fulfill({ status: 403, json: { detail: "denied" } });
  });
  await page.route("**/tasks/TASK-HANDOFF", route => route.fulfill({json: response()}));
  await page.goto("/?project=ui-review&page=task&task=TASK-HANDOFF");
  await expect(page.locator(".reader-status")).toContainText("验证中");
  await expect(page.getByRole("complementary", { name: "任务属性" })).toContainText("verify-2");
  await expect(page.getByTestId("handoff-binding-details")).toHaveCount(0);
  await page.getByRole("link", { name: "查看任务事件" }).click();
  const inspect = page.getByText(/查看任务运行详情 ·/);
  await inspect.click();
  await expect(page.locator(".handoff-line")).toContainText("admitted");
  await expect(page.locator(".handoff-line")).toContainText("generation 1 · failed");
  await expect(page.locator(".handoff-line")).not.toContainText("done");

  const binding = page.getByTestId("handoff-binding-details");
  await binding.locator("summary").click();
  await expect(binding).toContainText("target-current");
  await expect(binding).toContainText("/custom-state/execution/tasks/TASK-HANDOFF/g1/project");
  await expect(binding).toContainText("3 项；历史读取缺口 1 项");
  await expect(binding).toContainText("未知；历史读取不能证明当前仍保留内容");
  await expect(binding).toContainText("保存与读取记录不授予执行权限");
  await expect(binding).toContainText("已有绑定快照");
  await expect(binding).toContainText("快照不包含忽略文件、Git 历史和运行时状态");
  await page.screenshot({ path: test.info().outputPath("handoff-current.png"), fullPage: true });
  expect(inputRequests).toBe(0);
  await page.getByRole("button", { name: "查看输入与成果" }).click();
  await expect(page.getByTestId("task-input-panel").getByRole("alert")).toContainText("403");
  const readsAfterOpening = inputRequests;
  expect(readsAfterOpening).toBeGreaterThan(0);
  stale = true;
  await inspect.click();
  await expect(binding).toHaveCount(0);
  await inspect.click();
  await binding.locator("summary").click();
  await expect(binding).toContainText("绑定已过期，等待受控重新绑定");
  await expect(binding).toContainText("task workspace metadata does not match");
  await expect(binding).not.toContainText("/custom-state/execution/tasks/TASK-HANDOFF/g1/project");
  await page.getByRole("button", { name: "查看交接历史" }).click();
  await expect(page.getByTestId("task-activity-view")).toBeVisible();
  await expect(page.locator(".task-run-summary-panel")).toContainText("等待当前阶段提交并接纳结果");
  expect(inputRequests).toBe(readsAfterOpening);
  expect(errors).toEqual([]);
});
