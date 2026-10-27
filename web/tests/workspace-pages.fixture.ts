import type { Page } from "@playwright/test";
import { installUiFixture } from "./ui-alignment.fixture";

const stamp = "2026-10-26T12:30:00Z";
export const pageFeatures = [
  { id: "F-UI", title: "Workspace navigation and acceptance", status: "in_progress", priority: 1, source: "feature" },
  { id: "F-DOCS", title: "Reviewable delivery documentation", status: "done", priority: 2, source: "feature" },
];
export const pageAgents = [
  { instance_id: "developer-1", parent_role: "developer", role_type: "worker", backend: "codex", state: "running", lifecycle_state: "running", attention_state: "working", task_id: "UI-102", context_usage_ratio: .42, cost: { input_tokens: 12000, output_tokens: 4000, usd: .18 }, allowed_actions: ["reply", "drain"] },
  { instance_id: "reviewer-1", parent_role: "reviewer", role_type: "worker", backend: "claude", state: "blocked", lifecycle_state: "waiting", attention_state: "needs_input", task_id: "UI-104", context_usage_ratio: .25, cost: { input_tokens: 4000, output_tokens: 2000, usd: .09 } },
];
const workflow = { request_id: "REQ-UI", status: "proposed", kind: "prd", revision: 1, objective: "Align the workspace with clear acceptance criteria", proposal_digest: "fixture-proposal", proposal_ref: { ref: "fixture/proposal.json", digest: "fixture-proposal" } };
const channel = { channel_id: "ui-channel", name: "Workspace design", status: "open", leader_member_id: "designer", discussion: { mode: "single_responder", default_responder_id: "designer" }, members: [{ member_id: "designer", display_name: "Designer", member_type: "agent", status: "active", permissions: ["message"] }], messages: [{ message_id: "m-1", thread_id: "main", role: "user", text: "Keep the existing navigation and improve the page hierarchy.", ts: stamp }], provider_runs: [{ run_id: "run-design", message_id: "m-1", thread_id: "main", target_member_id: "designer", status: "completed", provider: "codex", updated_at: stamp, parts: [{ id: "answer", kind: "text", state: "completed", content: "The overview should put actionable work first.\n\nKeep runtime evidence accessible and preserve the existing controls." }] }], reply_requests: [], workflow_requests: [], syntheses: [] };
const automations = ["Daily brief", "Weekly review"].map((title, index) => ({
  automation_id: index ? "weekly-review" : "daily-brief", title, status: "ready", window: index ? "7 days" : "24 hours", next_run: "2026-10-27 09:00", trigger: "scheduled", last_run: { run_id: `automation-run-${index}` },
  run_counts_summary: { terminal_total: 4, completed: 4, failed: 0, events_total: 20 },
  recent_runs: [{ run_id: `automation-run-${index}`, status: "completed", started_at: stamp }], run_counts_by_day: [], proposals: [{ title: "Review blocked acceptance", status: "proposed" }],
  outputs: [{ type: "report", title: `${title} report`, summary: "The implementation is progressing; one task needs acceptance evidence.", insights: [{ title: "Acceptance evidence needed", summary: "UI-104 is waiting for an owner decision.", severity: "warning" }] }],
}));

export async function installWorkspacePages(page: Page, options: { locked?: boolean; empty?: boolean } = {}) {
  const result = await installUiFixture(page, { locked: options.locked, snapshot: {
    project: { project_id: "ui-review", name: "ZaoFu Studio", root: "/workspace/studio", state_dir: "/workspace/studio/.runtime", description: "A shared workspace for designing, implementing and verifying product delivery. ".repeat(5) },
    features: pageFeatures, delivery_features: pageFeatures, agents: pageAgents, agent_cockpit: { summary: {} }, agent_live: {},
    active_runs: [{ run_id: "run-1", status: "running" }],
    ...(options.empty ? { tasks: [], features: [], agents: [], active_runs: [] } : {}),
  } });
  await page.route(/^http:\/\/[^/]+\/api\//, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") {
      // Context lookup uses POST but does not execute or revise a workflow.
      if (path.endsWith("/workflow-preview-review") && route.request().postDataJSON()?.operation === "context") {
        return route.fulfill({ json: { ok: true, fields: { "requirement.objective": { value: workflow.objective, editable: true, reason: "" } } } });
      }
      return route.fallback();
    }
    let json: unknown;
    if (path.endsWith("/overview-pulse")) json = { schema_version: "overview-pulse.v1", run_pulse: { last_event_age_seconds: 7, events_per_bucket: [1, 2], bucket_seconds: 60, respawn_failed_streak: 0, loop: { status: "running", age_seconds: 4 }, sessions: { active: 2, stale: 0 } }, task_flow: null, attention: null, why_not: null };
    else if (path.endsWith("/cost")) json = { total: { usd: 0, input_tokens: 0, output_tokens: 0, entries: 0 }, per_role: {}, recent: [] };
    else if (path.endsWith("/agents")) json = options.empty ? [] : pageAgents;
    else if (path.endsWith("/agent-live") || path.endsWith("/agent-cockpit")) json = {};
    else if (path.endsWith("/automations")) json = { project_id: "ui-review", items: automations };
    else if (path.endsWith("/delivery-features")) json = { features: pageFeatures, delivery_features: pageFeatures };
    else if (path.includes("/delivery-traces/")) json = {
      schema_version: "delivery-trace.v1", feature_id: "F-UI", trace_id: "", status: "in_progress", synthetic: false,
      task_map: { status: "ready", task_count: 6, wave_count: 1 }, execution_graph: { task_count: 6, done_count: 1, in_progress_count: 2, blocked_count: 1, waiting_count: 2, nodes: [], edges: [], waves: [] },
      drift_report: { status: "ok", summary: {}, items: [] }, ship: { status: "blocked", required_tasks: 6, done_tasks: 1, missing_evidence: [{ task_id: "UI-104", status: "blocked" }] },
      attention: [{ label: "Acceptance evidence needed", meta: "UI-104 · owner review", tone: "warn" }], run_summary: { total: 4, running: 1, completed: 3, failed: 0, latest_label: "Implementation and verification in progress" }, run_groups: [], cycles: [], phases: [],
    };
    else if (path.endsWith("/workflow-requests")) json = { ok: true, count: 1, items: [workflow] };
    else if (path.endsWith("/workflow-requests/REQ-UI/graph")) json = { scope: { request_id: "REQ-UI", revision: 1, run_id: "", head_seq: 1, source_seq: 1, proposal_digest: "fixture-proposal", historical: false }, nodes: [{ id: "req", kind: "requirement", label: "Workspace design", status: "proposed", views: ["lifecycle"], data: {} }], edges: [], diagnostics: [], capabilities: {}, plans: [], versions: [], timeline: [], analysis: { intervals: [], critical_path: { status: "unknown", node_ids: [], duration_ms: null, method: "" } } };
    else if (path.endsWith("/workflow-requests/REQ-UI")) json = { ok: true, status: "proposed", result: workflow, requirement: { objective: workflow.objective, acceptance: ["Keep navigation order", "Verify desktop and mobile layouts"], constraints: ["Preserve controlled actions"] }, proposal: { flow_family: "PrdFlow", approval_status: "approvable", stage_graph: { nodes: [{ id: "design", role: "designer", label: "Design" }, { id: "implement", role: "developer", label: "Implement", dependencies: ["design"] }] }, blockers: [], preflight: { diagnostics: [] } }, artifacts: {}, lifecycle: { submitted: false }, operation: {}, links: {} };
    else if (path.endsWith("/traces")) json = { schema_version: "trace-list.v2", items: [{ trace_id: "trace-ui", first_seq: 1, last_seq: 2, first_ts: stamp, last_ts: stamp, duration_seconds: 120, event_count: 2, task_ids: ["UI-102"], actors: ["developer"], backends: ["codex"], status: "completed", last_type: "task.done", source: "event_trace" }], limit: 50, has_more: false, next_cursor: null, as_of_seq: 2 };
    else if (path.endsWith("/loop-view")) json = { schema_version: "loop-view.v1", generated_at: stamp, project_id: "ui-review", scope: { kind: "business_delivery", business_delivery_active: true, reason: "delivery evidence present" }, run: { run_id: "run-1", scope: "current", event_count: 2, semantic_event_count: 2, first_ts: stamp, last_ts: stamp, latched: false, promise: { source: "workflow_completion contract", satisfied: 1, latched: false, chain: [{ event: "plan.approved", satisfied: true, seq: 1 }, { event: "run.completed", satisfied: false }] } }, stages: [{ id: "plan", rounds: 1, last_status: "plan.approved", last_ts: stamp, warn: false }, { id: "implementation", rounds: 1, last_status: "dev.started", last_ts: stamp, warn: false }], backflows: [], subscriber_chains: [], tasks: [], loops: {}, faults: [], companions: {}, pump: { total: 4, lag_warnings: 0 }, health_counters: {}, source_projection_refs: [] };
    else if (path.endsWith("/channels")) json = { channels: [channel] };
    else if (path.includes("/channels/ui-channel")) json = channel;
    else if (path.endsWith("/events") || path.endsWith("/views/recent")) json = { items: options.empty ? [] : [{ type: "task.started", task_id: "UI-102", actor: "developer", ts: stamp, seq: 1 }], current_seq: 1, next_cursor: null };
    else return route.fallback();
    return route.fulfill({ json });
  });
  return result;
}
