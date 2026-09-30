import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

test("issue 59 template preserves peers and aligns multiline support and locations", async ({ page }) => {
  await page.goto("/tests/fixtures/portable-issue-workspace/index.html");
  await page.getByRole("button", { name: "生成 Draft", exact: true }).click();
  const body = readFileSync(new URL("./fixtures/portable-issue-workspace/issue-59-template.md", import.meta.url), "utf8");
  await page.getByLabel("Draft Markdown").fill(body);
  await page.getByRole("button", { name: "查看预览", exact: true }).click();
  const preview = page.getByLabel("Issue Markdown preview");
  await expect(preview.getByRole("heading", { name: "Environment", exact: true })).toBeVisible();
  await expect(preview.getByRole("heading", { name: "四、Other analysis", exact: true })).toBeVisible();
  await expect(preview).not.toContainText(/\bE\d+\b/);
  await expect(preview.locator("pre")).toHaveCount(0);
  const environment = preview.getByRole("heading", { name: "Environment", exact: true }).locator("+ ul > li");
  await expect(environment).toHaveCount(6);
  const triage = preview.getByRole("heading", { name: "Triage", exact: true }).locator("+ ul > li");
  expect((await triage.allTextContents()).map(text => text.split(":")[0])).toEqual([
    "Classification", "Classification status", "Severity", "Severity status", "Severity reason",
    "Reproduction status", "Component", "Impact scope", "Evidence collection",
    "Evidence collection status", "Assessment confidence", "Assessment",
  ]);
  const claims = preview.getByRole("heading", { name: "一、Claims", exact: true }).locator("+ ol > li");
  await expect(claims).toHaveCount(3);
  const conflicts = preview.getByRole("heading", { name: "二、Conflicts", exact: true }).locator("+ ol > li");
  const hypotheses = preview.getByRole("heading", { name: "三、Hypotheses", exact: true }).locator("+ ol > li");
  await expect(hypotheses).toHaveCount(2);
  for (const item of [claims.first(), conflicts.first(), hypotheses.first()]) {
    const support = item.locator("p").filter({ has: page.locator('[data-streamdown="strong"]', { hasText: /^Support:$/ }) });
    await expect(support.locator('[data-streamdown="strong"] + br')).toHaveCount(1);
    const positions = await support.evaluate(el => {
      const label = el.querySelector('[data-streamdown="strong"]')!.getBoundingClientRect();
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      const entries: { x: number; y: number }[] = [];
      while (walker.nextNode()) {
        const node = walker.currentNode;
        const match = /^[\s]*([ab]\.)/.exec(node.textContent || "");
        if (!match) continue;
        const range = document.createRange();
        const start = match[0].indexOf(match[1]);
        range.setStart(node, start); range.setEnd(node, start + 2);
        const rect = range.getBoundingClientRect(); entries.push({ x: rect.x, y: rect.y });
      }
      return { labelY: label.y, entries };
    });
    expect(positions.entries[0].y).toBeGreaterThan(positions.labelY);
    if (positions.entries.length > 1) {
      expect(Math.abs(positions.entries[0].x - positions.entries[1].x)).toBeLessThan(1);
      expect(positions.entries[1].y).toBeGreaterThan(positions.entries[0].y);
    }
  }
  const evidence = preview.getByRole("heading", { name: "Evidence summary", exact: true }).locator("+ ol > li");
  await expect(evidence).toHaveCount(5);
  await expect(evidence.nth(2).locator("p").first().locator('[data-streamdown="strong"] + br')).toHaveCount(1);
  const locations = await evidence.nth(2).locator("p").first().evaluate(el => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const result: { x: number; y: number }[] = [];
    while (walker.nextNode()) {
      const node = walker.currentNode; const text = node.textContent || "";
      const match = /[ab]\. (?:command|stderr)/.exec(text);
      if (!match) continue;
      const range = document.createRange(); range.setStart(node, match.index); range.setEnd(node, match.index + 2);
      const rect = range.getBoundingClientRect(); result.push({ x: rect.x, y: rect.y });
    }
    return result;
  });
  expect(locations).toHaveLength(2);
  expect(Math.abs(locations[0].x - locations[1].x)).toBeLessThan(1);
  expect(locations[1].y).toBeGreaterThan(locations[0].y);
});

test("ordered analysis keeps claims as peers and supporting conclusions readable", async ({ page }) => {
  await page.goto("/tests/fixtures/portable-issue-workspace/index.html");
  await page.getByRole("button", { name: "生成 Draft", exact: true }).click();
  await page.getByLabel("Draft Markdown").fill(`## Analysis

### 一、Claims

1. **Claim:** Missing dependency

   (1) **Confidence:** high

   (2) **Status:** reproduced

   (3) **Support:** a. E1：wheel declares only PyYAML

   b. E2：CLI import fails

2. **Claim:** Help affected

   (1) **Confidence:** 未提供

   (2) **Status:** observed

   (3) **Support:** a. E2：parser import blocks help

### 二、Conflicts

1. **Conflict:** Source identity

   (1) **Support:** a. E1：wheel declares only PyYAML

   (2) **Resolution:** Do not bind build commit`);
  await page.getByRole("button", { name: "查看预览", exact: true }).click();
  const first = page.locator("li").filter({ hasText: "Missing dependency" });
  const second = page.locator("li").filter({ hasText: "Help affected" });
  await expect(first).toHaveCount(1);
  await expect(second).toHaveCount(1);
  expect(await first.evaluate((el) => el.parentElement?.children.length)).toBe(2);
  expect(await second.evaluate((el) => el.previousElementSibling?.textContent)).toContain("Missing dependency");
  await expect(first).toContainText("E1：wheel declares only PyYAML");
  await expect(first.locator("pre")).toHaveCount(0);
});

test("draft edits survive polling and reload; preview is local; publication uses saved body", async ({ page }) => {
  await page.goto("/tests/fixtures/portable-issue-workspace/index.html");
  await page.getByRole("button", { name: "生成 Draft", exact: true }).click();
  await page.getByLabel("Draft Markdown").fill("## Edited body\n\nUser changes");
  await page.getByRole("button", { name: "查看预览", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Edited body" })).toBeVisible();
  await expect(page.locator("#calls")).not.toContainText('"action":"self-issue-preview"');
  await expect(page.getByText("已保存 Draft v2", { exact: true })).toBeVisible({ timeout: 13000 });
  await page.reload();
  await expect(page.getByLabel("Draft Markdown")).toHaveValue("## Edited body\n\nUser changes");
  await expect(page.getByRole("button", { name: "发布", exact: true })).toBeInViewport();
  await page.getByRole("button", { name: "发布", exact: true }).click();
  await expect(page.getByText("Issue 已发布，可通过右上角 × 关闭会话。")).toBeVisible();
  await page.getByLabel("最小化 Issue Agent session").click();
  await expect(page.getByRole("dialog", { name: "Issue Agent session" })).toBeHidden();
  await page.getByRole("button", { name: "恢复 Issue 会话" }).click();
  await expect(page.getByText("Issue 已发布，可通过右上角 × 关闭会话。")).toBeVisible();
  await page.getByLabel("关闭 Issue Agent session").click();
  await expect(page.getByText("closed", { exact: true })).toBeVisible();
});

test("selected field comments, pause, resume and original evidence remain visible after Draft", async ({ page }) => {
  await page.goto("/tests/fixtures/portable-issue-workspace/index.html");
  await page.getByRole("button", { name: "生成 Draft", exact: true }).click();
  await page.getByRole("checkbox", { name: "actual_behavior", exact: true }).check();
  await page.getByLabel("actual_behavior 补充说明").fill("Check network again");
  await page.getByRole("button", { name: "再次分析", exact: true }).click();
  await expect(page.locator("#calls")).toContainText("Check network again");
  await expect(page.getByLabel("Draft Markdown")).toBeVisible();
  await page.getByRole("button", { name: "中断分析", exact: true }).click();
  await expect(page.getByText("用户中断：未完成的分析不作为新报告产出。", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "从最近安全检查点恢复", exact: true }).click();
  await expect(page.getByText("原结果", { exact: true })).toBeVisible();
  await expect(page.getByText("再次分析结果", { exact: true })).toBeVisible();
  await page.getByText("活动详情（1）", { exact: true }).click();
  await page.getByText("完整工具结果", { exact: true }).click();
  await expect(page.getByText(/END OF FULL RESULT/)).toBeVisible();
  await page.getByRole("button", { name: "生成 Draft", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Draft v2/ })).toBeVisible();
});

test("unsynced IndexedDB backup survives reload and failed save blocks publication", async ({ page }) => {
  await page.goto("/tests/fixtures/portable-issue-workspace/index.html");
  await page.evaluate(() => localStorage.setItem("portable-fail-save", "1"));
  await page.getByRole("button", { name: "生成 Draft", exact: true }).click();
  await page.getByLabel("Draft Markdown").fill("## Unsynced recovery\nLocal content");
  await expect(page.getByText("已保存在本机，等待同步", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "发布", exact: true }).click();
  await expect(page.getByText("服务端同步：模拟网络不可用", { exact: true })).toBeVisible();
  await expect(page.locator("#calls")).not.toContainText('"action":"self-issue-publish"');
  await expect(page.locator("#calls")).not.toContainText('"action":"self-issue-preview"');
  await page.getByLabel("最小化 Issue Agent session").click();
  await expect(page.getByRole("dialog", { name: "Issue Agent session" })).toBeHidden();
  await page.getByRole("button", { name: "恢复 Issue 会话" }).click();
  await expect(page.getByLabel("Draft Markdown")).toHaveValue("## Unsynced recovery\nLocal content");
  await page.reload();
  await expect(page.getByLabel("Draft Markdown")).toHaveValue("## Unsynced recovery\nLocal content");
  await expect(page.getByText("服务端同步：模拟网络不可用", { exact: true })).toBeVisible();
  await page.evaluate(() => localStorage.removeItem("portable-fail-save"));
  await page.getByRole("button", { name: "重试同步", exact: true }).click();
  await expect(page.getByText("已保存 Draft v2", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Draft Markdown")).toHaveValue("## Unsynced recovery\nLocal content");
});

test("changed remote revision shows both copies and requires explicit recovery", async ({ page }) => {
  await page.goto("/tests/fixtures/portable-issue-workspace/index.html");
  await page.evaluate(() => localStorage.setItem("portable-fail-save", "1"));
  await page.getByRole("button", { name: "生成 Draft", exact: true }).click();
  await page.getByLabel("Draft Markdown").fill("My local version");
  await expect(page.getByText("服务端同步：模拟网络不可用", { exact: true })).toBeVisible();
  await page.evaluate(() => {
    const server = JSON.parse(localStorage.getItem("portable-fixture")!);
    server.editor = { ...server.editor, revision: 2, body: "Remote tab version" };
    localStorage.setItem("portable-fixture", JSON.stringify(server));
  });
  await page.reload();
  const conflict = page.getByRole("region", { name: "Draft 保存冲突" });
  await expect(conflict).toBeVisible();
  await expect(conflict).toContainText("My local version");
  await expect(conflict).toContainText("Remote tab version");
  await page.evaluate(() => localStorage.removeItem("portable-fail-save"));
  await page.getByRole("button", { name: "恢复本机版本 1 并同步", exact: true }).click();
  await expect(page.getByLabel("Draft Markdown")).toHaveValue("My local version");
  await expect(page.getByText("已保存 Draft v3", { exact: true })).toBeVisible();
});

test("session read stops waiting when the server never responds", async ({ page }) => {
  await page.goto("/tests/fixtures/portable-issue-workspace/index.html");
  await page.clock.install();
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let reached!: () => void;
  const requested = new Promise<void>((resolve) => { reached = resolve; });
  await page.route("**/api/projects/fixture/actions/self-issue-session-get", async (route) => {
    reached();
    await held;
    await route.abort().catch(() => undefined);
  });
  try {
    await page.evaluate(async () => {
      const modulePath = "/src/api/client.ts";
      const { postAction } = await import(modulePath);
      void postAction("self-issue-session-get", { session_id: "test" }, "fixture")
        .catch((error: Error) => { document.body.dataset.timeoutResult = error.message; });
    });
    await requested;
    await page.clock.fastForward(10_001);
    await expect(page.locator("body")).toHaveAttribute("data-timeout-result", /self-issue-session-get timed out/);
  } finally {
    release();
  }
});
