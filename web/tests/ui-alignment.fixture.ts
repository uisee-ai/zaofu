import type { Page } from "@playwright/test";
export const uiTasks = [
  { id: "UI-101", title: "Design a calmer workspace", status: "ready", assigned_to: "designer", priority: 1 },
  { id: "UI-102", title: "Build the shared task views", status: "in_progress", assigned_to: "dev-1", priority: 2 },
  { id: "UI-103", title: "Verify keyboard navigation", status: "testing", assigned_to: "verify-1", priority: 0 },
  { id: "UI-104", title: "Resolve missing acceptance evidence", status: "blocked", assigned_to: "dev-2", priority: 1, blocked_reason: "Waiting for the owner's acceptance criteria." },
  { id: "UI-105", title: "Document General settings", status: "done", assigned_to: "writer", priority: 3 },
  { id: "UI-106", title: "Design the inbox reading pane", status: "ready", assigned_to: "designer", priority: 2 },
].map((task, i) => ({ retry_count: 0, blocked_reason: "", phase: "implementation", created_at: `2026-10-26T0${i + 1}:15:00Z`, skills_required: ["ui-design"], ...task }));
export async function installUiFixture(page: Page, options: { manyInbox?: boolean; locked?: boolean; snapshot?: Record<string, unknown> } = {}) {
  const writes: Array<{ path: string; body: Record<string, unknown> }> = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    class MockSource {
      onopen: (() => void) | null = null; onerror = null; onmessage = null;
      constructor() { setTimeout(() => this.onopen?.(), 10); }
      addEventListener() {} removeEventListener() {} close() {}
    }
    Object.defineProperty(window, "EventSource", { value: MockSource });
  });
  const project = { project_id: "ui-review", name: "ZaoFu Studio", root: "/workspace/studio", state_dir: "/workspace/studio/.runtime", config_path: "/workspace/studio/zf.yaml", can_open_board: true };
  const runtime = { mode: "live", live: true, actions: { mutation_enabled: true, allowed: ["update-task", "plan-approve", "plan-reject", "run-delivery-ack"], requires_token: options.locked || false }, web_session: { mode: options.locked ? "token_required" : "local_trusted", unlocked: !options.locked, actions_enabled: !options.locked } };
  const items = Array.from({ length: options.manyInbox ? 24 : 3 }, (_, i) => ({ id: `plan-${i + 1}`, kind: "plan_approval", category: "action_required", actionability: "human_required", source_role: "orchestrator", title: i === 0 ? "Review the workspace alignment plan" : i === 1 ? "Confirm task view acceptance" : `Review iteration ${i + 1}`, summary: "Align the workspace around clear priorities and a consistent reading rhythm.\nPreserve the existing approval and task state boundaries.", plan_id: `plan-${i + 1}`, status: "pending", created_ts: "2026-10-26T12:30:00Z", task_count: 5, preview: { available: true }, actions: [] as Array<{ action: string; label: string }> }));
  const delivery = { id: "delivery-1", kind: "run_delivery", category: "notification", actionability: "informational", title: "Workspace verification completed", summary: "All five acceptance checks passed.", status: "pending", created_ts: "2026-10-26T12:30:00Z", actions: [{ action: "run-delivery-ack", label: "Ack" }] };
  await page.route(/^http:\/\/[^/]+\/api\//, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET") {
      writes.push({ path, body: request.postDataJSON() || {} });
      return route.fulfill({ json: { ok: true, status: "accepted", action: path.split("/").pop(), reason: "Fixture action accepted" } });
    }
    let json: unknown = {};
    if (path.endsWith("/workspace/projects")) json = { items: [project], projects: [project], active_project_id: project.project_id, server_default_project_id: project.project_id };
    else if (path.endsWith("/workspace/onboarding")) json = { completed: true, skipped: false, show_welcome: false, backends: [], preflight: [] };
    else if (path.endsWith("/web-session")) json = runtime.web_session;
    else if (/\/snapshot(?:\/(?:light|board|observability))?$/.test(path)) json = { seq: 1, generated_at: "2026-10-26T12:30:00Z", project, tasks: uiTasks, archive_tasks: [], agents: [], features: [], traces: [], fanouts: [], candidates: [], runs: [], active_runs: [], roles: [], workdirs: [], workers: [], skills: { pool: [], bindings: [] }, cost: { total: { usd: 0, input_tokens: 0, output_tokens: 0 }, by_role: {} }, runtime, ...options.snapshot };
    else if (path.endsWith("/health/summary")) json = { runtime_state: "running", live: true, seq: 1, task_counts: {}, active: 1, queued: 2, blocked: 1, projection: { state: "ready", lag: 0 } };
    else if (path.endsWith("/operator/inbox")) json = { summary: { total: items.length + 1, pending: items.length + 1, action_required_pending: items.length, notification_pending: 1, plan_approvals: items.length, attention: 0 }, items: [...items, delivery], pending: [...items, delivery], policy: {} };
    else if (/\/plans\/[^/]+\/preview$/.test(path)) json = { ok: true, plan_id: path.split("/").at(-2), status: "pending", content_kind: "digest_only", content_profile: "summary_only", markdown: "# Workspace alignment\n\nPreserve canonical task state and explicit approval.\n\n## Acceptance\n\nBoard, List and Table share the filtered task set.", refs: {}, task_count: 5, task_map_summary: { task_count: 5 }, artifacts: [] };
    else if (/\/tasks\/[^/]+\/diff$/.test(path)) json = { task_id: path.split("/").at(-2), files: [], diff: "", base: "", head: "" };
    else if (/\/tasks\/[^/]+\/timeline$/.test(path)) json = { task_id: path.split("/").at(-2), items: [], attempts: [], events: [], summary: {} };
    else if (/\/tasks\/[^/]+$/.test(path)) json = { task: uiTasks.find((task) => task.id === path.split("/").at(-1)), events: [], contract: {}, briefing: { text: "" }, artifacts: [] };
    else if (path.includes("/events") || path.includes("/views/recent")) json = { items: [], current_seq: 1, next_cursor: null };
    else if (path.includes("pending-proposals")) json = { items: [] };
    else if (path.endsWith("/sessions")) json = { sessions: [], items: [] };
    return route.fulfill({ json });
  });
  return { writes, errors };
}
