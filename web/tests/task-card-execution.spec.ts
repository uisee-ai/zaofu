import { expect, test } from "@playwright/test";
import { installWorkspacePages } from "./workspace-pages.fixture";
import { installUiFixture, uiTasks } from "./ui-alignment.fixture";
import type { TaskCardProjection } from "../src/api/types";

test("board cards identify active attempts, meaningful progress, waiting conditions and the bound run", async ({ page }, info) => {
  const milestone = { label: "管理员修改后刷新可见", at: "2026-11-27T10:30:00Z" };
  function card(id: string, status: string, execution?: TaskCardProjection["execution"]) {
    return {
      ...uiTasks[0], id, title: id, status, assigned_to: "", skills_required: ["planned-skill"],
      task_card: execution && { lifecycle: { display_status: status, effective_terminal: status === "done", outcome: status === "done" ? "success" : "" },
        owner: { actor_id: "", role: "" }, activity: { at: milestone.at }, execution },
    };
  }
  const tasks = [
    card("Queued", "ready", { stage: "verify", state: "verify_ready" }),
    card("Warranty", "in_progress", { stage: "verify", state: "running", last_output: { ...milestone, event_id: "result-217" },
      milestone: { label: "下一检查已派发", at: "2026-11-27T10:45:00Z" }, workflow_run_id: "run-217", request_id: "request-217",
      active_instances: [{ instance_id: "verify-2", role: "verify", attempt_id: "try-2", operation_id: "op-2" }, { instance_id: "reader-4", role: "reader", attempt_id: "try-3" }] }),
    card("Account", "blocked", { stage: "verify", state: "external_gate_waiting", waiting_for: "等待只读账号开通", milestone }),
    card("Stopped", "done", { milestone, workflow_run_id: "run-217" }),
    card("Ordinary", "ready"),
  ];
  const { writes, errors } = await installUiFixture(page, { snapshot: { tasks } });
  await page.setViewportSize({ width: 1520, height: 920 });
  const taskRequests: string[] = [];
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path.includes("/tasks/")) taskRequests.push(path);
  });
  const details = { ...tasks[1], active_dispatch_id: "dispatch-217" };
  await page.route("**/tasks/Warranty", route => route.fulfill({ json: {
    task: details, execution: details.task_card?.execution, contract: {}, events: [], properties: { milestone },
    handoff_summary: { pipeline: { status: "current", current_stage: "verify", pipeline_stage: "verify_active",
      operation_status: "running", dispatch_id: "dispatch-217", owner: { instance_id: "verify-2" }, attempt_id: "try-2" } },
    deliverables: [{ name: "warranty-evidence.md", sha256: "a".repeat(64) }],
  } }));
  const find = (id: string) => page.locator(`.task-card[data-task-id="${id}"]`);
  await page.goto("/?project=ui-review&page=board&view=board");
  const active = find("Warranty");
  await expect(active).toContainText("verify-2");
  await expect(active.locator(".card-worker")).toHaveCount(2);
  await expect(active.getByTitle("verify · verify-2 · try-2")).toBeVisible();
  await expect(active).toContainText("reader-4");
  await expect(active).not.toContainText("try-1");
  await expect(find("Queued")).toContainText(/verify ready|等待验证/);
  await expect(find("Queued").locator(".card-worker")).toHaveCount(0);
  await expect(find("Account")).toContainText("等待只读账号开通");
  await expect(find("Stopped").locator(".card-worker")).toHaveCount(0);
  await expect(find("Ordinary").locator(".card-execution-stage, .card-worker, .card-run-link")).toHaveCount(0);
  await expect(find("Ordinary").locator(".task-assignee-avatar")).toHaveAttribute("title", /(?:Required skill|所需技能) · planned-skill/);
  const stamp = active.getByTestId("task-card-milestone").locator("time");
  await expect(stamp).toHaveAttribute("datetime", milestone.at);
  const stampText = await stamp.innerText();
  tasks[1].task_card!.activity.at = "2026-11-27T11:30:00Z";
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("fixture:sse", { detail: {
    id: "heartbeat-217", seq: 22, task_id: "Warranty", type: "worker.heartbeat", payload: {},
  } })));
  // The next complete snapshot contains the later activity timestamp too.
  await page.reload();
  await expect(stamp).toHaveText(stampText);
  await expect(stamp).toHaveAttribute("datetime", milestone.at);
  await expect(active.getByTestId("task-card-milestone")).toContainText(milestone.label);
  await expect(active.getByTestId("task-card-milestone")).toContainText(/Latest output|最近产出/);
  await active.locator(".card-attempts summary").click();
  await expect(active.locator(".card-attempts code")).toHaveText(["try-2", "try-3"]);
  expect(taskRequests).toEqual([]);
  await page.screenshot({ path: info.outputPath("board-execution.png"), fullPage: true });

  await active.getByRole("button").click();
  const reader = page.getByRole("region", { name: "任务详情" });
  await expect(reader).toContainText("try-2");
  const memberLink = reader.getByRole("link", {name: "try-2"});
  await expect(memberLink).toHaveAttribute("href", /request=request-217&expected_run=run-217&graph=workflow&node=operation%3Aop-2/);
  await expect(reader.getByRole("link", {name: "任务分工、产物与交接"})).toHaveAttribute("href", /graph=modules&node=task%3AWarranty/);
  await expect(reader).toContainText("try-3");
  await expect(reader.getByRole("link", { name: "warranty-evidence.md" })).toHaveAttribute("href", /\/tasks\/Warranty\/deliverables\/a{64}$/);
  await reader.getByRole("button", { name: "返回看板" }).click();
  const run = active.locator(".card-run-link");
  await expect(run).toHaveAttribute("href", /project=ui-review.*page=runs.*run_id=run-217/);
  const runRequest = page.waitForRequest(request => new URL(request.url()).pathname === "/api/projects/ui-review/runs/run-217");
  await run.click();
  await runRequest;
  expect(await page.evaluate(() => new URLSearchParams(location.search).get("run_id"))).toBe("run-217");
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});

test("the normal dashboard keeps the run pin while opening an attempt link", async ({ page }) => {
  await installWorkspacePages(page);
  await page.route("**/workflow-requests/REQ-UI/graph*", route => {
    if (new URL(route.request().url()).pathname.endsWith("/structure")) return route.fulfill({ status: 404 });
    return route.fulfill({ json: {
      scope: { request_id: "REQ-UI", revision: 1, run_id: "current-run", head_seq: 1, source_seq: 1,
        proposal_digest: "fixture", historical: false },
      nodes: [{ id: "operation:op-1", kind: "operation", label: "Current verifier", status: "running", views: ["workflow"], data: {} }],
      edges: [], diagnostics: [], capabilities: {}, plans: [], versions: [], timeline: [],
      analysis: { intervals: [], critical_path: { status: "unknown", node_ids: [], method: "" } },
    } });
  });
  const url = "/?project=ui-review&page=workflows&request=REQ-UI&graph=workflow&node=operation%3Aop-1&expected_run=";
  await page.goto(url + "previous-run");
  await expect(page.getByRole("status").filter({ hasText: "此任务链接绑定运行 previous-run" })).toBeVisible();
  await expect(page.getByTestId("workflow-graph-canvas")).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get("expected_run")).toBe("previous-run");
  await page.goto(url + "current-run");
  await expect(page.getByTestId("workflow-graph-canvas")).toBeVisible();
  expect(new URL(page.url()).searchParams.get("expected_run")).toBe("current-run");
});
