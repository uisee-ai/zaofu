import { readFile } from "node:fs/promises";
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

test("reference comparison uses paired trial data and distinguishes project dispatches", async ({ page }, testInfo) => {
  const report = JSON.parse(await readFile(new URL("../src/data/skill-loading-reference.json", import.meta.url), "utf-8"));
  const card = {
    workflow_run_id: "workflow-B", attempt_id: "verify-2", dispatch_id: "d2", revision: 1,
    score: { percent: null, passed: 0, denominator: 0, unknown: 1 }, criteria: [],
    skills: [{ name: report.skill, digest: report.skill_digest, auto_inject: true, load_on_demand: false,
      read_event_ids: [], invocation_event_ids: [] }],
    provider: "codex", configured_model: "project-assigned-model", recommendation: {action: "复核证据"},
  };
  await page.route("**/api/projects/*/tasks/same-task/skill-evaluations?*", route =>
    route.fulfill({json: {cards: route.request().url().includes("/first/") ? [card] : []}}));
  await page.goto("/tests/task-inputs.html?evaluations");
  const panel = page.getByTestId("task-skill-evaluations");
  await panel.getByText("Skill 加载策略对照（参考试验）").click();
  const comparison = panel.getByRole("region", {name: "Skill 加载策略参考试验"});
  await expect(comparison.getByRole("heading", {name: "保留默认必读"})).toBeVisible();
  await expect(comparison).toContainText(report.model);
  await expect(comparison).toContainText("project-assigned-model · 默认必读 · Skill 摘要一致");
  for (const split of ["test","selection"]) {
    await comparison.getByRole("combobox").selectOption(split);
    const score = report.splits[split];
    await expect(comparison).toContainText(`完整三组配对 ${score.matched_pairs}/${score.expected_pairs}`);
    const row = comparison.getByRole("row", {name: /^按需加载/});
    await expect(row.getByRole("cell").nth(0)).toHaveText(`${score.arms.candidate.verdict_correct}/${score.matched_pairs}`);
    await expect(row.getByRole("cell").nth(1)).toHaveText(`${score.arms.candidate.correct}/${score.matched_pairs}`);
    if (split === "test") {
      await comparison.getByText("查看按需组的 2 次提交偏差").click();
      await expect(comparison).toContainText("median-red · 第 2 次");
      await expect(comparison.locator("code").last()).toContainText("missing_proof");
      await comparison.getByText("查看按需组的 2 次提交偏差").click();
      await comparison.screenshot({path: testInfo.outputPath("skill-loading-comparison.png")});
    }
  }
  const jsonUrl = await comparison.getByRole("link", {name: "下载逐例 JSON"}).getAttribute("href");
  const payload = await (await page.request.get(jsonUrl!)).json();
  expect(payload.observations.length).toBe(42);
  expect(payload.common_fingerprint).toBe(report.common_fingerprint);
  await page.getByRole("button", {name: "切换项目"}).click();
  await panel.getByText("Skill 加载策略对照（参考试验）").click();
  await expect(panel).toContainText("尚无此 Skill 的记录");
  await expect(panel).not.toContainText("project-assigned-model");
});
