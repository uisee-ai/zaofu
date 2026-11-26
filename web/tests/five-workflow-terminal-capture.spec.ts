import { mkdir, writeFile } from "node:fs/promises";

import { test, type APIRequestContext, type Page } from "@playwright/test";

const evidenceDir = process.env.ZF_PLAYWRIGHT_EVIDENCE_DIR ?? "";
const projectId = process.env.ZF_FIVE_E2E_PROJECT_ID ?? "";
const taskId = process.env.ZF_FIVE_E2E_TASK_ID ?? "";
const runId = process.env.ZF_FIVE_E2E_RUN_ID ?? "";
const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const captureName = (process.env.ZF_PLAYWRIGHT_CAPTURE_NAME ?? "terminal-failure")
  .replace(/[^A-Za-z0-9._-]+/g, "-");

type EventItem = {
  id?: string;
  type?: string;
  task_id?: string | null;
  correlation_id?: string | null;
  payload?: Record<string, unknown>;
};

test.describe.configure({ mode: "serial", timeout: 90_000 });

async function jsonOrError(request: APIRequestContext, path: string): Promise<unknown> {
  try {
    const response = await request.get(path);
    return {
      status: response.status(),
      body: await response.json().catch(() => ({})),
    };
  } catch (error) {
    return { status: 0, error: String(error) };
  }
}

function belongsToCase(event: EventItem): boolean {
  const payload = event.payload ?? {};
  const eventTask = String(
    event.task_id
      ?? payload.task_id
      ?? payload.parent_task_id
      ?? payload.root_task_id
      ?? "",
  );
  const eventRuns = [
    event.correlation_id,
    payload.workflow_run_id,
    payload.run_id,
    payload.request_id,
  ].map((value) => String(value ?? ""));
  return Boolean((taskId && eventTask === taskId) || (runId && eventRuns.includes(runId)));
}

async function openProject(page: Page): Promise<void> {
  await page.addInitScript(({ actionToken, selectedProject }) => {
    if (actionToken) window.localStorage.setItem("zf.webActionToken", actionToken);
    window.localStorage.setItem("zf.operatorBackend", "codex-headless");
    window.localStorage.setItem("zf.themeMode", "light");
    if (selectedProject) window.localStorage.setItem("zf.selectedProjectId", selectedProject);
  }, { actionToken: token, selectedProject: projectId });
  const query = projectId ? `?project=${encodeURIComponent(projectId)}` : "";
  await page.goto(`/${query}`, { waitUntil: "domcontentloaded" });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.waitForTimeout(2_000);
}

test("captures a read-only terminal failure snapshot", async ({ page, request }) => {
  if (!evidenceDir) throw new Error("ZF_PLAYWRIGHT_EVIDENCE_DIR is required");
  await mkdir(evidenceDir, { recursive: true });
  await openProject(page);

  await page.screenshot({
    fullPage: true,
    path: `${evidenceDir}/${captureName}.png`,
  });
  const agentDialog = page.getByRole("dialog", { name: "zaofu" });
  if (await agentDialog.isVisible().catch(() => false)) {
    await agentDialog.screenshot({
      path: `${evidenceDir}/${captureName}-kanban-agent.png`,
    });
  }

  const snapshot = await jsonOrError(request, "/api/snapshot");
  await writeFile(
    `${evidenceDir}/${captureName}-snapshot.json`,
    `${JSON.stringify(snapshot, null, 2)}\n`,
    "utf8",
  );
  if (projectId) {
    const response = await jsonOrError(
      request,
      `/api/projects/${encodeURIComponent(projectId)}/events?limit=1000`,
    );
    const body = response && typeof response === "object" && "body" in response
      ? (response as { body?: { items?: EventItem[] } }).body
      : undefined;
    const items = Array.isArray(body?.items) ? body.items.filter(belongsToCase) : [];
    await writeFile(
      `${evidenceDir}/${captureName}-events.json`,
      `${JSON.stringify({ response, related_items: items }, null, 2)}\n`,
      "utf8",
    );
  }
});
