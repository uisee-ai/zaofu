import { expect, test } from "@playwright/test";

test("task score, unknowns, evidence and revision follow the selected project", async ({ page }, testInfo) => {
  const first = {
    workflow_run_id: "workflow-A", task_outcome: "blocked", operation_id: "op", attempt_id: "a-1",
    dispatch_id: "d1", revision: 1, digest: "first-digest", change_reason: "terminal_observed", predecessor: null,
    critical_status: "pending", score: { percent: null, passed: 0, denominator: 0, unknown: 5, not_applicable: 0, coverage: 0 },
    recommendation: { action: "复核冻结证据" }, criteria: [], skills: [],
  };
  const second = { ...first, revision: 2, digest: "second-digest", predecessor: first.digest,
    change_reason: "independent_review_updated", critical_status: "failed", task_outcome: "passed",
    review_evidence: { H1: "evt-host" },
    independent_review: { status: "reviewed", requested_model: "local:1", rule: "task-verify-independent.v3",
      trigger: "sample_released", input_digest: "input-digest", decision: { critical_false_release: true,
        limitation: "单次观察", owner: "evidence_producer", items: [{id: "AC-1", finding: "violated", judgment: "contradicts", reason: "归档输出与要求冲突", citations: ["H1"]}] } },
    score: { percent: 50, passed: 1, denominator: 2, unknown: 3, not_applicable: 0, coverage: 0.4 },
    criteria: [{ id: "frozen_target_and_contract", result: "fail", reason: "目标提交冲突", critical: true, evidence_refs: ["evt-target"] }],
    skills: [{ name: "review", read_status: "observed_header", invocation: "unknown",
      read_event_ids: ["evt-read"], invocation_event_ids: [] }],
  };
  await page.route("**/api/projects/*/tasks/same-task/skill-evaluations?*", route => {
    const url = new URL(route.request().url());
    const cards = url.pathname.includes("/second/") ? [] : url.searchParams.get("history") === "true" ? [first, second] : [second];
    return route.fulfill({ json: { cards } });
  });
  await page.goto("/tests/task-inputs.html?evaluations");
  const panel = page.getByTestId("task-skill-evaluations");
  await panel.getByText(/修订 2.*50%.*1\/2.*关键项失败/).click();
  await expect(panel).toContainText("未知 3 · 不适用 0 · 可判定覆盖率 40%");
  await expect(panel.getByText("evt-target")).toBeVisible();
  await expect(panel.getByText("目标提交冲突")).toBeVisible();
  await panel.getByText("独立语义复核 · 已复核").click();
  await expect(panel.getByRole("alert")).toHaveText("独立评审发现关键误放行");
  await expect(panel).toContainText("AC-1 · violated · contradicts · 归档输出与要求冲突 · evt-host");
  await panel.getByText(/Skill 调用与读取/).click();
  await expect(panel.getByText("evt-read")).toBeVisible();
  await testInfo.attach("scorecard", { body: await panel.screenshot(), contentType: "image/png" });
  await panel.getByLabel("显示历史修订").check();
  await expect(panel.getByText(/修订 1.*待评分.*0\/0.*关键项待证/)).toBeVisible();
  await page.getByRole("button", { name: "切换项目" }).click();
  await expect(panel).toContainText("尚无已发布的评分卡");
  await expect(panel).not.toContainText("evt-target");
});
