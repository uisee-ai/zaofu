import { expect, test } from "@playwright/test";
const entry = (source: string) => ({ source_id: source, artifact_id: "task.json", occurrence_id: source,
  json_path: "$.tasks[7]", ref: "artifacts/task.json", sha256: "a".repeat(64), required: true,
  read_status: "missing", selected_bytes: 100, source_bytes: 100000, snapshot_status: "pinned",
  read_command: "zf artifact read --attempt same-attempt --source task --artifact task.json --json-path '$.tasks[7]'",
  cost: { produced_body_bytes: 0, repeated_exact_range_bytes: 0, read_count: 0 } });
const body = (source: string, next: string | null = null) => ({ attempt_id: "same-attempt", task_id: "T7",
  read_purpose: "implementation", total: 2, items: [entry(source)], next_cursor: next });

test("task inputs paginate, explain pinned ranges, filter, and isolate project changes", async ({ page }) => {
  await page.route("**/api/projects/*/attempts/*/inputs?*", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.includes("/second/")) return route.fulfill({ json: body("SECOND-PROJECT") });
    if (url.searchParams.get("query")) return route.fulfill({ json: { ...body(""), total: 0, items: [] } });
    return route.fulfill({ json: url.searchParams.get("cursor") ? body("SECOND-PAGE") : body("FIRST-PAGE", "next") });
  });
  await page.goto("/tests/task-inputs.html");
  await expect(page.getByTestId("task-input-panel")).toContainText("FIRST-PAGE");
  await page.locator("summary").click();
  await expect(page.locator("details")).toContainText("100,000 B");
  await expect(page.locator("pre")).toContainText("--json-path '$.tasks[7]'");
  await expect(page.locator("details")).toContainText("必读回执未完整");
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator("summary")).toContainText("SECOND-PAGE");
  await expect(page.getByText("FIRST-PAGE", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "返回首页" }).click();
  await expect(page.locator("summary")).toContainText("FIRST-PAGE");
  await page.getByLabel("输入定位").fill("missing");
  await page.getByRole("button", { name: "定位", exact: true }).click();
  await expect(page.getByTestId("task-input-panel")).toContainText("没有匹配的授权输入");
  await page.getByRole("button", { name: "切换项目" }).click();
  await expect(page.locator("summary")).toContainText("SECOND-PROJECT");
});

test("loading and access failures never claim an empty success", async ({ page }) => {
  let release: () => void = () => {};
  const waiting = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/projects/*/attempts/*/inputs?*", async route => {
    await waiting;
    await route.fulfill({ status: 403, json: { detail: "denied" } });
  });
  await page.goto("/tests/task-inputs.html");
  await expect(page.getByRole("status")).toContainText("加载输入目录");
  release();
  await expect(page.getByRole("alert")).toContainText("403");
  await expect(page.locator("details")).toHaveCount(0);
});

test("unavailable required policy is shown as unknown", async ({ page }) => {
  await page.route("**/api/projects/*/attempts/*/inputs?*", route => route.fulfill({ json: {
    ...body("UNKNOWN"), items: [{ ...entry("UNKNOWN"), required: null, read_status: "unknown" }],
  } }));
  await page.goto("/tests/task-inputs.html");
  await expect(page.locator("summary")).toContainText("必读政策未知");
  await page.locator("summary").click();
  await expect(page.locator("details")).toContainText("必读政策不可用");
});

test("relations and stale lifecycle explain bounded recovery without silently rebinding", async ({ page }) => {
  await page.route("**/api/projects/*/attempts/*/inputs?*", route => {
    const expanded = new URL(route.request().url()).searchParams.get("navigate") === "true";
    return route.fulfill({ json: { ...body("PINNED"),
      lifecycle: { status: "superseded", requires_rebind: true },
      recovery_command: "zf artifact recover --attempt same-attempt",
      items: [{ ...entry("PINNED"), locator_id: "base" }, ...(expanded ? [{ ...entry("PINNED"),
        locator_id: "relation", navigation_complete: false,
        navigation: { kind: "relation", id: "AC-7", relation: "acceptance", field_pointer: "/acceptance_ids/0" } }] : [])],
    } });
  });
  await page.goto("/tests/task-inputs.html");
  await expect(page.getByTestId("task-input-panel")).toContainText("已被新版本替代");
  await expect(page.getByRole("alert")).toContainText("需要受控重新绑定");
  await page.getByText("展开关系与章节").click();
  const relation = page.locator("details").filter({ has: page.locator("summary", { hasText: "AC-7" }) });
  await relation.locator("summary").click();
  await expect(relation).toContainText("/acceptance_ids/0");
  await expect(relation).toContainText("导航达到提取上限");
  await page.getByTestId("input-recovery-entry").locator("summary").click();
  await expect(page.getByTestId("input-recovery-entry")).toContainText("artifact recover --attempt same-attempt");
  await expect(page.getByTestId("input-recovery-entry")).toContainText("不授予执行权限");
});
