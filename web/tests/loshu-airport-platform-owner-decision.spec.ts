import { mkdirSync, writeFileSync } from "node:fs";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const projectId = process.env.ZF_LOSHU_PROJECT_ID ?? "";
const taskId = process.env.ZF_LOSHU_TASK_ID ?? "";
const evidenceDir = process.env.ZF_PLAYWRIGHT_EVIDENCE_DIR ?? "";
const checkpointId = "plan-owner-41768741461ae52d";
const workflowRunId = "workflow-99d69cb187b322f4";

type EventItem = {
  seq: number;
  type: string;
  task_id?: string | null;
  payload?: Record<string, unknown>;
};

type EventPage = {
  current_seq?: number;
  items?: EventItem[];
};

test.describe.configure({ mode: "serial", timeout: 900_000 });

async function apiJson<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(path);
  const body = await response.json().catch(() => ({}));
  expect(response.ok(), `${path}: ${response.status()} ${JSON.stringify(body)}`).toBeTruthy();
  return body as T;
}

async function eventCursor(request: APIRequestContext): Promise<number> {
  const page = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(projectId)}/events?limit=1`,
  );
  return Number(page.current_seq ?? 0);
}

async function eventsAfter(
  request: APIRequestContext,
  cursor: number,
): Promise<EventItem[]> {
  const page = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(projectId)}/events?cursor=${cursor}&limit=1000`,
  );
  return page.items ?? [];
}

async function capture(page: Page, name: string): Promise<void> {
  mkdirSync(evidenceDir, { recursive: true });
  await page.screenshot({ fullPage: true, path: `${evidenceDir}/${name}.png` });
}

test("approves AC14 option A and resumes the same PRD run through Kanban Agent", async ({
  page,
  request,
}) => {
  expect(token).not.toBe("");
  expect(projectId).not.toBe("");
  expect(taskId).not.toBe("");
  const cursor = await eventCursor(request);

  await page.addInitScript(({ actionToken }) => {
    window.localStorage.clear();
    window.localStorage.setItem("zf.operatorBackend", "codex-headless");
    window.localStorage.setItem("zf.webActionToken", actionToken);
    window.localStorage.setItem("zf.themeMode", "light");
  }, { actionToken: token });
  await page.goto(`/?project=${encodeURIComponent(projectId)}`);
  await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Open zaofu" }).click();
  const dialog = page.getByRole("dialog", { name: "zaofu" });
  await expect(dialog).toBeVisible();

  const input = dialog.locator("textarea.headless-input");
  await input.fill([
    `针对 Task ${taskId}、checkpoint ${checkpointId} 的阻塞 owner decision，owner 选择推荐选项 A：`,
    "AC14 在 30/60/120 FPS、相同模拟时长下，路线进度最大偏差不得超过路线总长 0.5%。",
    `请仅生成 replan-approve 受控 action proposal，保持同一 workflow run ${workflowRunId}，`,
    "批准 critic 的 rework，并要求 revised plan 落实全部 fix_items、保留 3 个逻辑 implementation owner，同时并发受 profile 上限控制。",
    "proposal payload 必须绑定 approval_ref/checkpoint_id=plan-owner-41768741461ae52d、",
    "fanout_id=fanout-prd-plan-evt-879d33db、source_event_id=evt-e5f95ee7651d、",
    "proposal_ref=artifacts/plan-synth/owner-checkpoints/96d39172c6f417d55cf1e7c7c4612884d21dbcd89b755868ea40d1e1e218a654.json、",
    "eval_ref=artifacts/call-results/control/plan-synthesis-result.v1/07cdb358138bba2bfdbe5f3ebfb9b89a80c983c33af12f0304488e2fa6bb857e.json，",
    "reason 中记录选项 A 与 0.5% 容差。不要创建 Task、不要启动新 Run、不要直接修改 runtime truth。",
  ].join(""));
  await dialog.getByRole("button", { name: "Send message" }).click();

  const proposal = page.locator(
    '[data-testid="agent-card-approve"][data-proposal-action="replan-approve"]:not(.is-completed)',
  ).filter({ hasText: checkpointId }).last();
  await expect(proposal).toBeVisible({ timeout: 600_000 });
  await expect(proposal).toContainText(/0\.5%|0\.5 percent/i);
  await capture(page, "08-owner-decision-proposal");

  const approveButton = proposal.getByTestId("agent-proposal-approve");
  await expect(approveButton).toBeEnabled({ timeout: 30_000 });
  await approveButton.click();

  let approved: EventItem | undefined;
  let resolved: EventItem | undefined;
  await expect.poll(async () => {
    const events = await eventsAfter(request, cursor);
    approved = events.find((event) => (
      event.type === "replan.owner_decision.approved"
      && event.payload?.checkpoint_id === checkpointId
    ));
    resolved = events.find((event) => (
      event.type === "plan.synth.owner_decision.resolved"
      && event.payload?.checkpoint_id === checkpointId
    ));
    return Boolean(approved && resolved);
  }, { timeout: 300_000, intervals: [500, 1000, 2000] }).toBeTruthy();

  expect(JSON.stringify(approved?.payload ?? {})).toMatch(/0\.5%|0\.5 percent/i);
  await capture(page, "09-owner-decision-approved");
  mkdirSync(evidenceDir, { recursive: true });
  writeFileSync(
    `${evidenceDir}/owner-decision.json`,
    `${JSON.stringify({
      schema_version: "loshu-airport-platform-owner-decision.v1",
      project_id: projectId,
      task_id: taskId,
      workflow_run_id: workflowRunId,
      checkpoint_id: checkpointId,
      selected_option: "A",
      max_route_progress_deviation: "0.5% of route length",
      approved_event: approved,
      resolved_event: resolved,
      captured_at: new Date().toISOString(),
    }, null, 2)}\n`,
  );
});
