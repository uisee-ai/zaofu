import { expect, test } from "@playwright/test";

const projectId = process.env.ZF_WEB_PROJECT_ID ?? "zaofu-915bc1fe";

test("a scoped Project dashboard renders overview pulse before its snapshot and deferred workspace", async ({ page }) => {
  let releaseWorkspace!: () => void;
  let releaseSnapshot!: () => void;
  const workspaceGate = new Promise<void>((resolve) => { releaseWorkspace = resolve; });
  const snapshotGate = new Promise<void>((resolve) => { releaseSnapshot = resolve; });
  let workspaceObserved = false;
  let snapshotObserved = false;
  const pulseRequests: string[] = [];

  await page.route("**/api/workspace/projects", async (route) => {
    workspaceObserved = true;
    await workspaceGate;
    await route.fulfill({ json: { items: [{ project_id: projectId, name: projectId, root: "/fixture", can_open_board: true }], active_project_id: projectId } });
  });
  await page.route(/\/api\/projects\/[^/]+\/snapshot\/board$/, async (route) => {
    snapshotObserved = true;
    await snapshotGate;
    await route.fulfill({ json: { seq: 1, project: { project_id: projectId, name: projectId, root: "/fixture" }, tasks: [], archive_tasks: [], features: [], agents: [], traces: [], fanouts: [], candidates: [], runs: [], active_runs: [], roles: [], workers: [], workdirs: [], runtime: { live: false, actions: {} }, generated_at: "2026-10-27T00:00:00Z" } });
  });
  await page.route(/\/api\/projects\/[^/]+\/overview-pulse$/, async (route) => {
    pulseRequests.push(route.request().url());
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "overview-pulse.v1",
        is_derived_projection: true,
        run_pulse: {
          last_event_age_seconds: 7,
          events_per_bucket: [1, 2],
          bucket_seconds: 60,
          respawn_failed_streak: 0,
          loop: { status: "running", age_seconds: 4 },
          sessions: { active: 2, stale: 0 },
        },
        task_flow: null,
        attention: null,
        why_not: null,
      }),
    });
  });

  try {
    await page.goto(`/?project=${encodeURIComponent(projectId)}&page=project`, {
      waitUntil: "domcontentloaded",
    });
    await expect.poll(() => snapshotObserved).toBe(true);
    expect(workspaceObserved).toBe(false);
    await expect(page.getByTestId("overview-pulse-band")).toContainText("7s");
    expect(pulseRequests).toHaveLength(1);
    expect(new URL(pulseRequests[0]).pathname).toBe(`/api/projects/${projectId}/overview-pulse`);
    releaseSnapshot();
    await expect.poll(() => workspaceObserved).toBe(true);
  } finally {
    releaseWorkspace();
    releaseSnapshot();
  }
});
