import type {
  OnboardingStatus,
  LoopViewProjection,
  ActionResponse,
  AgentSessionHistoryPage,
  AgentSessionRawOutput,
  AgentProfileCatalog,
  AgentSummary,
  CausationChain,
  ChannelDetail,
  ChannelHistorySearchResult,
  ChannelsPage,
  CostSummary,
  CandidateDetail,
  DeliveryTrace,
  DeliveryFeaturesPage,
  DiagnosticsDetail,
  DiagnosticsLogsPage,
  OperationsObservability,
  EventsPage,
  FanoutDetail,
  IntegrationQueueProjection,
  TaskPipelineProjection,
  LoopProjection,
  LoopActionResponse,
  LoopLearningPromotionResponse,
  MeasureLoopProjection,
  OperatorInputResponse,
  OperatorInboxProjection,
  OperatorOutputPage,
  OperatorSession,
  PlanPreview,
  OverviewPulse,
  RecentEvent,
  RepairActionProjection,
  RoleSummary,
  GoalDossier,
  RunDetail,
  RuntimeResourceProjection,
  RuntimeSummary,
  SearchResult,
  SkillsSummary,
  Snapshot,
  TaskDetail,
  TaskDiff,
  TaskTimeline,
  TraceDetail,
  TraceEventDetail,
  TraceIndexPage,
  TraceSpanPage,
  WorkflowGraph,
  WorkflowRequestDetail,
  WorkflowRequestsPage,
  WorkdirSummary,
  WorkspaceProjectsPage,
  WebChatArtifact,
  WebChatChildActivity,
  WebChatRuntime,
  WebChatSearchResult,
} from "./types";
import { cachedGetJson, clearGetCache } from "./queryClient";

async function requestJson<T>(
  path: string,
  options: { bypassCache?: boolean; signal?: AbortSignal } = {},
): Promise<T> {
  return cachedGetJson<T>(path, options);
}

function projectPrefix(projectId?: string): string {
  return projectId ? `/api/projects/${encodeURIComponent(projectId)}` : "/api";
}

export function invalidateProjectReadCache(projectId: string): void {
  clearGetCache(projectPrefix(projectId));
}

function normalizeWebActionToken(value: string): string {
  let base = value.trim();
  if (
    base.length >= 2
    && ((base.startsWith('"') && base.endsWith('"'))
      || (base.startsWith("'") && base.endsWith("'"))
      || (base.startsWith("`") && base.endsWith("`")))
  ) {
    base = base.slice(1, -1).trim();
  }
  // Header values must be latin1 (ByteString). A token pasted from the masked
  // password field (• = U+2022) or carrying stray unicode would otherwise
  // crash fetch ("character ... greater than 255"). Keep only printable ASCII.
  let out = "";
  for (const ch of base) {
    const code = ch.charCodeAt(0);
    if (code >= 0x21 && code <= 0x7e) out += ch;
  }
  return out;
}

function webActionToken(): string {
  return normalizeWebActionToken(window.localStorage.getItem("zf.webActionToken") ?? "");
}

function webActionAuthHeaders(): Record<string, string> {
  const token = webActionToken();
  return token ? {
    "X-ZF-Web-Token": token,
    Authorization: `Bearer ${token}`,
  } : {};
}

export async function uploadChannelAttachment(projectId: string, channelId: string, threadId: string, file: File): Promise<Record<string, unknown>> {
  const query = new URLSearchParams({ name: file.name, thread_id: threadId });
  const response = await fetch(`${projectPrefix(projectId)}/channels/${encodeURIComponent(channelId)}/attachments?${query}`, {
    method: "POST", headers: { ...webActionAuthHeaders(), "Content-Type": file.type || "application/octet-stream" }, body: file,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(String(result.detail || result.reason || "附件上传失败，请重新选择文件"));
  return result.attachment;
}

export function getWorkspaceProjects(): Promise<WorkspaceProjectsPage> {
  return requestJson<WorkspaceProjectsPage>("/api/workspace/projects");
}

export async function getOnboarding(): Promise<OnboardingStatus> {
  // Gate check must reflect current server state — never the GET cache
  // (a stale completed/skipped read would wrongly show or hide the wizard).
  const response = await fetch("/api/workspace/onboarding", {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`onboarding fetch failed: ${response.status}`);
  return response.json();
}

export interface BootstrapInspect {
  schema_version: string;
  root: string;
  confidence: string;
  stack: string;
  layout: string;
  recommended_flow: string;
  has_config?: boolean;
  candidates: Array<{
    kind: "setup" | "gate" | "doc_fact" | "flow";
    label: string;
    note: string;
    value?: string;
    values?: string[];
    facts?: Record<string, string>;
  }>;
  error?: string;
}

export async function inspectBootstrap(root: string, backend = "claude"): Promise<BootstrapInspect> {
  const response = await fetch(`/api/workspace/bootstrap/inspect?root=${encodeURIComponent(root)}&backend=${encodeURIComponent(backend)}`, {
    headers: { Accept: "application/json", ...webActionAuthHeaders() },
  });
  const data = (await response.json()) as BootstrapInspect & { reason?: string };
  if (!response.ok) throw new Error(data.reason || `inspect failed: ${response.status}`);
  return data;
}

export async function updateOnboarding(payload: {
  action: "step" | "complete" | "skip" | "reset";
  step?: number;
  backend?: string;
  primary_backend?: string;
  mixed_enabled?: boolean;
  notifications?: string;
}): Promise<Record<string, unknown>> {
  const response = await fetch("/api/workspace/onboarding", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...webActionAuthHeaders(),
    },
    body: JSON.stringify(payload),
  });
  const data = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    throw new Error(String(data.reason || `onboarding update failed: ${response.status}`));
  }
  return data;
}

export type ProjectAdmissionAction =
  | "open"
  | "register"
  | "initialize_state"
  | "initialize_project"
  | "blocked";

export interface ProjectAdmissionDiagnostic {
  severity: "STOP" | "WARN" | "INFO" | string;
  kind: string;
  message: string;
}

export interface WorkspaceProjectPathInspection {
  schema_version: "workspace.project-admission.v1";
  ok: boolean;
  status: "valid" | "missing" | "invalid";
  root: string;
  root_resolved: string;
  root_exists: boolean;
  config_path: string;
  has_config: boolean;
  config_loadable: boolean;
  state_dir_resolved: string;
  state_dir_exists: boolean;
  state_ready: boolean;
  diagnostics: ProjectAdmissionDiagnostic[];
  project_profile: {
    schema: string;
    layout: string;
    confidence: string;
    languages: string[];
    surfaces: string[];
    gate_cmds: string[];
    units: Array<{
      root: string;
      language: string;
      frameworks: string[];
      surface: string;
      build_cmd: string;
      test_cmd: string;
      gate_cmds: string[];
      has_tests: boolean;
    }>;
  };
  admission: {
    action: ProjectAdmissionAction;
    label: string;
    reason: string;
    project_id: string;
  };
}

export async function validateWorkspaceProjectPath(
  root: string,
  workspace = "default",
): Promise<WorkspaceProjectPathInspection> {
  const response = await fetch("/api/workspace/projects/validate-path", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...webActionAuthHeaders(),
    },
    body: JSON.stringify({ root, workspace }),
  });
  const data = (await response.json()) as WorkspaceProjectPathInspection & {
    reason?: string;
  };
  if (!response.ok) {
    throw new Error(data.reason || `project inspection returned ${response.status}`);
  }
  return data;
}

export async function registerWorkspaceProject(payload: {
  root: string;
  workspace?: string;
}): Promise<Record<string, unknown>> {
  const response = await fetch("/api/workspace/projects/register", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...webActionAuthHeaders(),
    },
    body: JSON.stringify(payload),
  });
  const data = (await response.json()) as Record<string, unknown>;
  if (!response.ok && response.status >= 500) {
    throw new Error(String(data.reason || `workspace register returned ${response.status}`));
  }
  return data;
}

export interface PresetInfo {
  name: string;
  description: string;
  roleCount: number;
  kind: string;
  backend: string;
}

export async function listPresets(): Promise<PresetInfo[]> {
  const response = await fetch("/api/presets", { headers: { Accept: "application/json" } });
  const data = (await response.json()) as { presets?: PresetInfo[] };
  return Array.isArray(data.presets) ? data.presets : [];
}

export async function recommendProfile(
  root: string,
  intent: string,
  options?: { stack?: string; surface?: string; scale?: string; backend?: string },
): Promise<Record<string, unknown>> {
  const params = new URLSearchParams({ path: root, intent });
  if (options?.stack) params.set("stack", options.stack);
  if (options?.surface) params.set("surface", options.surface);
  if (options?.scale) params.set("scale", options.scale);
  if (options?.backend) params.set("backend", options.backend);
  const response = await fetch(`/api/profile/recommend?${params.toString()}`, {
    headers: { Accept: "application/json" },
  });
  return (await response.json()) as Record<string, unknown>;
}

export async function initWorkspaceProject(payload: {
  root: string;
  workspace?: string;
  preset?: string;
  kind?: "multi" | "issue" | "prd" | "refactor";
  name?: string;
  project_name?: string;
  source_ref?: string;
  source_root?: string;
  target_root?: string;
  backend?: string;
  verify_backend?: string;
  mixed_enabled?: boolean;
  lanes?: number;
  strictness?: string;
  parity_scope?: string | string[];
  state_dir?: string;
  force?: boolean;
  apply_profile?: boolean;
  stack?: string;
  surface?: string;
  scale?: string;
  scaffold?: boolean;
  intent?: string;
  description?: string;
  notes?: string;
}): Promise<Record<string, unknown>> {
  const response = await fetch("/api/workspace/projects/init", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...webActionAuthHeaders(),
    },
    body: JSON.stringify(payload),
  });
  const data = (await response.json()) as Record<string, unknown>;
  if (!response.ok && response.status >= 500) {
    throw new Error(String(data.reason || `workspace init returned ${response.status}`));
  }
  return data;
}

export async function createWorkflowIntake(
  projectId: string,
  payload: {
    kind: string;
    objective?: string;
    source_root?: string;
    target_root?: string;
    backend?: string;
    lanes?: number;
    request_id?: string;
  },
): Promise<Record<string, unknown>> {
  const response = await fetch(`${projectPrefix(projectId)}/workflow-intake`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...webActionAuthHeaders(),
    },
    body: JSON.stringify(payload),
  });
  const data = (await response.json()) as Record<string, unknown>;
  if (!response.ok && response.status >= 500) {
    throw new Error(String(data.reason || `workflow intake returned ${response.status}`));
  }
  return data;
}

export async function touchWorkspaceProject(projectId: string): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/workspace/projects/${encodeURIComponent(projectId)}/touch`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      ...webActionAuthHeaders(),
    },
  });
  const data = (await response.json()) as Record<string, unknown>;
  if (!response.ok && response.status >= 500) {
    throw new Error(String(data.reason || `workspace touch returned ${response.status}`));
  }
  return data;
}

export async function removeWorkspaceProject(projectId: string): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/workspace/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
    headers: {
      Accept: "application/json",
      ...webActionAuthHeaders(),
    },
  });
  const data = (await response.json()) as Record<string, unknown>;
  if (!response.ok && response.status >= 500) {
    throw new Error(String(data.reason || `workspace remove returned ${response.status}`));
  }
  return data;
}

// Scoped, fast automation projection. The Automations page must NOT read
// snapshot.automations: that field is only populated in the full snapshot,
// which the page never loads (it loads the light slice), and the full
// snapshot replays the whole event log (can take 45s+ on a busy project).
export function getProjectAutomations(projectId?: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`${projectPrefix(projectId)}/automations`);
}

// Scoped trace roll-up: the Event Traces page fetches this (fast read-model
// slim) instead of pulling the full snapshot, which replays the whole log.
export function getProjectTraces(
  projectId?: string,
  params: { limit?: number; cursor?: string } = {},
): Promise<TraceIndexPage> {
  const search = new URLSearchParams({
    contract: "v2",
    limit: String(params.limit ?? 50),
  });
  if (params.cursor) search.set("cursor", params.cursor);
  return requestJson<TraceIndexPage>(`${projectPrefix(projectId)}/traces?${search.toString()}`);
}

export function getRunContractProjection(projectId?: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`${projectPrefix(projectId)}/run-contract`);
}

export function getFailureCandidatesProjection(projectId?: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`${projectPrefix(projectId)}/failure-candidates`);
}

export function getRealE2eMatrixProjection(projectId?: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`${projectPrefix(projectId)}/real-e2e-matrix`);
}

export function getSnapshot(projectId?: string): Promise<Snapshot> {
  return requestJson<Snapshot>(`${projectPrefix(projectId)}/snapshot`);
}

export function getProjectCost(projectId?: string): Promise<CostSummary> {
  return requestJson<CostSummary>(`${projectPrefix(projectId)}/cost`);
}

export interface ProjectHealthSummary {
  schema_version: string;
  runtime_state: string;
  live: boolean;
  seq: number;
  last_event_age_s: number | null;
  task_counts: Record<string, number>;
  active: number;
  queued: number;
  blocked: number;
  projection: { state?: string; lag?: number | null; tail_behind?: boolean };
}

export function getProjectHealth(projectId?: string): Promise<ProjectHealthSummary> {
  return requestJson<ProjectHealthSummary>(`${projectPrefix(projectId)}/health/summary`);
}

export function getSnapshotLight(projectId?: string): Promise<Snapshot> {
  return requestJson<Snapshot>(`${projectPrefix(projectId)}/snapshot/light`);
}

export function getDeliveryFeatures(projectId?: string): Promise<DeliveryFeaturesPage> {
  return requestJson<DeliveryFeaturesPage>(`${projectPrefix(projectId)}/delivery-features`);
}

// delivery-trace.v2 — read-only, view-scoped Delivery projection.
export function getDeliveryTrace(
  featureId: string,
  projectId?: string,
  sinceEventId?: string,
  options: {
    bypassCache?: boolean;
    goalId?: string;
    signal?: AbortSignal;
    view?: "overview" | "runs" | "graph" | "work";
  } = {},
): Promise<DeliveryTrace> {
  const { goalId, view, ...requestOptions } = options;
  const params = new URLSearchParams({ contract: "v2" });
  if (view) params.set("view", view);
  if (goalId) params.set("goal_id", goalId);
  if (sinceEventId) params.set("since_event_id", sinceEventId);
  const suffix = `?${params.toString()}`;
  return requestJson<DeliveryTrace>(
    `${projectPrefix(projectId)}/delivery-traces/${encodeURIComponent(featureId)}${suffix}`,
    requestOptions,
  ).then((data) => ({
    ...data,
    trace_id: data.trace_id ?? "",
    synthetic: data.synthetic ?? false,
    diagnostics: data.diagnostics ?? [],
    drift_report: data.drift_report ?? data.drift ?? { status: "unknown", summary: {}, items: [] },
    execution_graph: data.execution_graph ?? {
      task_count: data.task_map?.task_count ?? 0,
      done_count: 0,
      in_progress_count: 0,
      blocked_count: 0,
      waiting_count: data.task_map?.task_count ?? 0,
      nodes: [],
      edges: [],
      waves: [],
    },
  }));
}

export function getLoops(projectId?: string): Promise<LoopProjection> {
  return requestJson<LoopProjection>(`${projectPrefix(projectId)}/loops`);
}

export function getLoopView(
  projectId?: string,
  options: { signal?: AbortSignal } = {},
): Promise<LoopViewProjection> {
  return requestJson<LoopViewProjection>(`${projectPrefix(projectId)}/loop-view`, options);
}

export function getMeasureLoops(
  projectId?: string,
  featureId?: string,
  lens?: string,
): Promise<MeasureLoopProjection> {
  const params = new URLSearchParams();
  if (featureId) params.set("feature_id", featureId);
  if (lens) params.set("lens", lens);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson<MeasureLoopProjection>(`${projectPrefix(projectId)}/measure/loops${suffix}`);
}

export async function postLoopAction(
  loopId: string,
  payload: {
    candidate_id: string;
    suggested_action?: string;
    idempotency_key?: string;
  },
  projectId?: string,
): Promise<LoopActionResponse> {
  const response = await fetch(
    `${projectPrefix(projectId)}/loops/${encodeURIComponent(loopId)}/actions`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...webActionAuthHeaders(),
        ...(payload.idempotency_key ? { "X-Idempotency-Key": payload.idempotency_key } : {}),
      },
      body: JSON.stringify(payload),
    },
  );
  const data = (await response.json()) as LoopActionResponse;
  if (!response.ok) {
    throw new Error(String(data.reason || data.status || `loop action returned ${response.status}`));
  }
  return data;
}

export async function postLoopLearningPromotion(
  loopId: string,
  learningId: string,
  payload: {
    target?: string;
    idempotency_key?: string;
  },
  projectId?: string,
): Promise<LoopLearningPromotionResponse> {
  const response = await fetch(
    `${projectPrefix(projectId)}/loops/${encodeURIComponent(loopId)}/learning/${encodeURIComponent(learningId)}/promotions`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...webActionAuthHeaders(),
        ...(payload.idempotency_key ? { "X-Idempotency-Key": payload.idempotency_key } : {}),
      },
      body: JSON.stringify(payload),
    },
  );
  const data = (await response.json()) as LoopLearningPromotionResponse;
  if (!response.ok) {
    throw new Error(String(data.reason || data.status || `loop learning promotion returned ${response.status}`));
  }
  return data;
}

// causation-chain.v1 — walk an event's causation links back to the source
// (used by Run Graph ⛓ causal replay; read-only).
export function getDeliveryCausationChain(
  featureId: string,
  eventId: string,
  projectId?: string,
): Promise<CausationChain> {
  return requestJson<CausationChain>(
    `${projectPrefix(projectId)}/delivery-traces/${encodeURIComponent(featureId)}/causation/${encodeURIComponent(eventId)}`,
  );
}

// doc 82 §8.2/§9 — diagnostics log rows (project-scoped, read-only)
export function getDiagnosticsLogs(
  projectId?: string,
  params?: { limit?: number; level?: string; taskId?: string; role?: string; traceId?: string },
): Promise<DiagnosticsLogsPage> {
  const search = new URLSearchParams();
  if (params?.limit) search.set("limit", String(params.limit));
  if (params?.level) search.set("level", params.level);
  if (params?.taskId) search.set("task_id", params.taskId);
  if (params?.role) search.set("role", params.role);
  if (params?.traceId) search.set("trace_id", params.traceId);
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return requestJson<DiagnosticsLogsPage>(
    `/api/projects/${encodeURIComponent(projectId || "default")}/diagnostics/logs${suffix}`,
  );
}

export function getOperationsObservability(projectId?: string): Promise<OperationsObservability> {
  return requestJson<OperationsObservability>(
    `/api/projects/${encodeURIComponent(projectId || "default")}/observability/operations`,
  );
}

// overview-pulse.v1 — RUN PULSE / TASK FLOW bands (project-scoped, read-only)
export function fetchOverviewPulse(projectId: string): Promise<OverviewPulse> {
  return requestJson<OverviewPulse>(
    `/api/projects/${encodeURIComponent(projectId || "default")}/overview-pulse`,
  );
}

export function getRecentEvents(limit = 60, projectId?: string): Promise<RecentEvent[]> {
  return getRecentEventsPage(limit, projectId).then((page) => page.items);
}

export function getRecentEventsPage(
  limit = 60,
  projectId?: string,
  requireFresh = false,
): Promise<EventsPage> {
  if (projectId) {
    const params = new URLSearchParams({ limit: String(limit) });
    if (requireFresh) params.set("require_fresh", "true");
    return requestJson<EventsPage>(
      `${projectPrefix(projectId)}/events?${params.toString()}`,
      { bypassCache: requireFresh },
    );
  }
  return requestJson<RecentEvent[]>(`/api/views/recent?limit=${limit}`).then((items) => ({
    items,
    next_cursor: null,
    current_seq: items.reduce((maxSeq, event) => Math.max(maxSeq, Number(event.seq || 0)), 0),
    limit,
  }));
}

export function getTaskDetail(taskId: string, projectId?: string): Promise<TaskDetail> {
  return requestJson<TaskDetail>(`${projectPrefix(projectId)}/tasks/${encodeURIComponent(taskId)}`);
}

export function getTaskTimeline(taskId: string, projectId?: string): Promise<TaskTimeline> {
  const params = new URLSearchParams({ limit: "200" });
  return requestJson<TaskTimeline>(
    `${projectPrefix(projectId)}/tasks/${encodeURIComponent(taskId)}/timeline?${params.toString()}`,
  );
}

export function getTaskDiff(taskId: string, projectId?: string): Promise<TaskDiff> {
  return requestJson<TaskDiff>(`${projectPrefix(projectId)}/tasks/${encodeURIComponent(taskId)}/diff`);
}

export function getEventsPage(
  params: URLSearchParams,
  projectId?: string,
  requireFresh = false,
): Promise<EventsPage> {
  const query = new URLSearchParams(params);
  if (requireFresh) query.set("require_fresh", "true");
  return requestJson<EventsPage>(
    `${projectPrefix(projectId)}/events?${query.toString()}`,
    { bypassCache: requireFresh },
  );
}

export interface PendingKanbanProposal {
  proposal_event_id: string;
  proposal_event_ids?: string[];
  proposal_id: string;
  proposal_digest: string;
  revision: number;
  expires_at?: string;
  source?: string;
  ts: string;
  action: string;
  requested_action: string;
  reason: string;
  valid: boolean;
  validation_error: string;
  title: string;
  payload: Record<string, unknown>;
  turn_id: string;
  conversation_id: string;
  thread_key: string;
}

export function getKanbanPendingProposals(
  projectId?: string,
): Promise<{ items: PendingKanbanProposal[] }> {
  // Approval entries are a safety boundary. A short GET cache can otherwise
  // hide a proposal immediately after a Plan answer and leave the operator
  // with no way to approve or reject it in the current session.
  return requestJson<{ items: PendingKanbanProposal[] }>(
    `${projectPrefix(projectId)}/kanban-agent/pending-proposals`,
    { bypassCache: true },
  );
}

export function getAgentSessionHistory(
  projectId: string,
  params: {
    surface?: string;
    threadId: string;
    conversationId?: string;
    backend?: string;
    taskId?: string;
    beforeSeq?: number | null;
    limit?: number;
    requireFresh?: boolean;
  },
): Promise<AgentSessionHistoryPage> {
  const search = new URLSearchParams({
    surface: params.surface ?? "kanban_agent",
    thread_id: params.threadId,
    limit: String(params.limit ?? 160),
  });
  if (params.conversationId) search.set("conversation_id", params.conversationId);
  if (params.backend) search.set("backend", params.backend);
  if (params.taskId) search.set("task_id", params.taskId);
  if (params.beforeSeq) search.set("before_seq", String(params.beforeSeq));
  if (params.requireFresh) search.set("require_fresh", "true");
  return requestJson<AgentSessionHistoryPage>(
    `${projectPrefix(projectId)}/agent-session/history?${search.toString()}`,
    { bypassCache: Boolean(params.requireFresh) },
  );
}

export function getAgentSessionRawOutput(
  projectId: string | undefined,
  rawRef: string,
  params: { offset?: number; limit?: number } = {},
): Promise<AgentSessionRawOutput> {
  const search = new URLSearchParams({ ref: rawRef });
  if (params.offset) search.set("offset", String(params.offset));
  if (params.limit) search.set("limit", String(params.limit));
  return requestJson<AgentSessionRawOutput>(
    `${projectPrefix(projectId || "default")}/agent-session/raw-output?${search.toString()}`,
  );
}

async function webChatMutation<T>(
  projectId: string,
  path: string,
  payload?: Record<string, unknown>,
  method: "POST" | "DELETE" = "POST",
): Promise<T> {
  const response = await fetch(`${projectPrefix(projectId)}/webchat${path}`, {
    method,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...webActionAuthHeaders(),
    },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const data = await response.json() as T & { detail?: string; reason?: string };
  if (!response.ok) {
    throw new Error(String(data.detail || data.reason || `WebChat request failed: ${response.status}`));
  }
  clearGetCache(`${projectPrefix(projectId)}/webchat`);
  return data;
}

export function getWebChatRuntime(
  projectId: string,
  params: { conversationId: string; threadId: string; surface?: string },
): Promise<WebChatRuntime> {
  const search = new URLSearchParams({
    conversation_id: params.conversationId,
    thread_id: params.threadId,
    surface: params.surface ?? "kanban_agent",
  });
  return requestJson<WebChatRuntime>(
    `${projectPrefix(projectId)}/webchat/runtime?${search.toString()}`,
    { bypassCache: true },
  );
}

export function enqueueWebChatMessage(
  projectId: string,
  payload: Record<string, unknown>,
): Promise<{ ok: boolean; created: boolean; item: WebChatRuntime["queue"][number] }> {
  return webChatMutation(projectId, "/queue", payload);
}

export function cancelWebChatQueue(projectId: string, queueId: string): Promise<Record<string, unknown>> {
  return webChatMutation(projectId, `/queue/${encodeURIComponent(queueId)}`, undefined, "DELETE");
}

export function controlWebChatTurn(
  projectId: string,
  turnId: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return webChatMutation(projectId, `/turns/${encodeURIComponent(turnId)}/control`, payload);
}

export function answerWebChatInteraction(
  projectId: string,
  interactionId: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return webChatMutation(projectId, `/interactions/${encodeURIComponent(interactionId)}/answer`, payload);
}

export function searchWebChatMessages(
  projectId: string,
  query: string,
  conversationId: string,
  threadId = "",
): Promise<{ items: WebChatSearchResult[] }> {
  const search = new URLSearchParams({ q: query, conversation_id: conversationId });
  if (threadId) search.set("thread_id", threadId);
  return requestJson(`${projectPrefix(projectId)}/webchat/conversations/search?${search.toString()}`, { bypassCache: true });
}

export function getWebChatMessages(
  projectId: string,
  conversationId: string,
  threadId: string,
): Promise<{ items: Array<WebChatSearchResult & { content: string }> }> {
  return requestJson(
    `${projectPrefix(projectId)}/webchat/conversations/${encodeURIComponent(conversationId)}`
      + `/threads/${encodeURIComponent(threadId)}/messages?limit=500`,
    { bypassCache: true },
  );
}

export function correctWebChatMessage(
  projectId: string,
  messageId: string,
  content: string,
): Promise<Record<string, unknown>> {
  return webChatMutation(projectId, `/messages/${encodeURIComponent(messageId)}/correct`, { content });
}

export function forkWebChatMessage(
  projectId: string,
  messageId: string,
  title: string,
): Promise<Record<string, unknown>> {
  return webChatMutation(projectId, `/messages/${encodeURIComponent(messageId)}/fork`, { title });
}

export function compactWebChatThread(
  projectId: string,
  conversationId: string,
  threadId: string,
  summary: string,
  coveredMessageIds: string[],
): Promise<Record<string, unknown>> {
  return webChatMutation(
    projectId,
    `/conversations/${encodeURIComponent(conversationId)}/threads/${encodeURIComponent(threadId)}/compact`,
    { summary, covered_message_ids: coveredMessageIds },
  );
}

export function webChatExportUrl(
  projectId: string,
  conversationId: string,
  threadId: string,
  format: "markdown" | "json" | "txt" = "markdown",
): string {
  return `${projectPrefix(projectId)}/webchat/conversations/${encodeURIComponent(conversationId)}`
    + `/threads/${encodeURIComponent(threadId)}/export?format=${format}`;
}

export function uploadWebChatArtifact(
  projectId: string,
  payload: Record<string, unknown>,
): Promise<{ ok: boolean; created: boolean; artifact: WebChatArtifact }> {
  return webChatMutation(projectId, "/artifacts", payload);
}

export function getWebChatArtifact(
  projectId: string,
  versionId: string,
): Promise<{ artifact: WebChatArtifact; preview: string; preview_truncated: boolean }> {
  return requestJson(
    `${projectPrefix(projectId)}/webchat/artifacts/${encodeURIComponent(versionId)}`,
    { bypassCache: true },
  );
}

export function webChatArtifactContentUrl(projectId: string, versionId: string): string {
  return `${projectPrefix(projectId)}/webchat/artifacts/${encodeURIComponent(versionId)}/content`;
}

export function compareWebChatArtifacts(
  projectId: string,
  leftVersionId: string,
  rightVersionId: string,
): Promise<{ diff: string }> {
  return requestJson(
    `${projectPrefix(projectId)}/webchat/artifacts/compare/`
      + `${encodeURIComponent(leftVersionId)}/${encodeURIComponent(rightVersionId)}`,
    { bypassCache: true },
  );
}

export function publishWebChatArtifact(
  projectId: string,
  versionId: string,
  digest: string,
): Promise<{ ok: boolean; artifact: WebChatArtifact }> {
  return webChatMutation(projectId, `/artifacts/${encodeURIComponent(versionId)}/publish`, {
    digest,
    principal: "kanban-agent",
  });
}

export function adoptWebChatArtifact(
  projectId: string,
  versionId: string,
  digest: string,
  target: Record<string, unknown>,
): Promise<{ ok: boolean; artifact: WebChatArtifact; controlled: boolean }> {
  return webChatMutation(projectId, `/artifacts/${encodeURIComponent(versionId)}/adopt`, { digest, target });
}

export function getWebChatTaskAdoptionTarget(
  projectId: string,
  taskId: string,
): Promise<Record<string, unknown>> {
  return requestJson(
    `${projectPrefix(projectId)}/webchat/adoption-targets/tasks/${encodeURIComponent(taskId)}`,
    { bypassCache: true },
  );
}

export function getWebChatChildActivities(
  projectId: string,
  parentTurnId = "",
): Promise<{ items: WebChatChildActivity[] }> {
  const search = new URLSearchParams();
  if (parentTurnId) search.set("parent_turn_id", parentTurnId);
  return requestJson(
    `${projectPrefix(projectId)}/webchat/child-activities${search.size ? `?${search.toString()}` : ""}`,
    { bypassCache: true },
  );
}

export function getAgentProfileCatalog(projectId: string): Promise<AgentProfileCatalog> {
  return requestJson(`${projectPrefix(projectId)}/webchat/profiles/catalog`, { bypassCache: true });
}

export function proposeAgentProfile(
  projectId: string,
  profileId: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return webChatMutation(projectId, `/profiles/${encodeURIComponent(profileId)}/proposals`, payload);
}

export function applyAgentProfile(
  projectId: string,
  proposalId: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return webChatMutation(projectId, `/profiles/proposals/${encodeURIComponent(proposalId)}/apply`, payload);
}

export function getTraceDetail(
  traceId: string,
  projectId?: string,
  params: { limit?: number; cursor?: string } = {},
): Promise<TraceDetail> {
  const search = new URLSearchParams({
    contract: "v2",
    limit: String(params.limit ?? 80),
  });
  if (params.cursor) search.set("cursor", params.cursor);
  return requestJson<TraceDetail>(
    `${projectPrefix(projectId)}/traces/${encodeURIComponent(traceId)}?${search.toString()}`,
  );
}

export function getTraceSpans(
  traceId: string,
  projectId?: string,
  params: { limit?: number; cursor?: string; spanId?: string } = {},
): Promise<TraceSpanPage> {
  const search = new URLSearchParams({
    contract: "v1",
    limit: String(params.limit ?? 100),
  });
  if (params.cursor) search.set("cursor", params.cursor);
  if (params.spanId) search.set("focus_span_id", params.spanId);
  return requestJson<TraceSpanPage>(
    `${projectPrefix(projectId)}/traces/${encodeURIComponent(traceId)}/spans?${search.toString()}`,
  );
}

export function getTraceEventRaw(eventId: string, projectId?: string): Promise<TraceEventDetail> {
  return requestJson<TraceEventDetail>(
    `${projectPrefix(projectId)}/events/${encodeURIComponent(eventId)}`,
  );
}

export function getCandidateDetail(pddId: string, projectId?: string): Promise<CandidateDetail> {
  return requestJson<CandidateDetail>(`${projectPrefix(projectId)}/candidates/${encodeURIComponent(pddId)}`);
}

export function getFanoutDetail(fanoutId: string, projectId?: string): Promise<FanoutDetail> {
  return requestJson<FanoutDetail>(`${projectPrefix(projectId)}/fanouts/${encodeURIComponent(fanoutId)}`);
}

export function getRunDetail(runId: string, projectId?: string): Promise<RunDetail> {
  return requestJson<RunDetail>(`${projectPrefix(projectId)}/runs/${encodeURIComponent(runId)}`);
}

export function getRunDossier(runId: string, projectId?: string): Promise<GoalDossier> {
  return requestJson<GoalDossier>(
    `${projectPrefix(projectId)}/runs/${encodeURIComponent(runId)}/dossier`,
  );
}

export function getWorkdirs(projectId?: string): Promise<WorkdirSummary[]> {
  return requestJson<WorkdirSummary[]>(`${projectPrefix(projectId)}/workdirs`);
}

export function getIntegrationQueue(projectId?: string): Promise<IntegrationQueueProjection> {
  return requestJson<IntegrationQueueProjection>(`${projectPrefix(projectId)}/integration-queue`);
}

export function getTaskPipeline(projectId?: string): Promise<TaskPipelineProjection> {
  return requestJson<TaskPipelineProjection>(`${projectPrefix(projectId)}/task-pipeline`);
}

export function getRepairActions(projectId?: string): Promise<RepairActionProjection> {
  return requestJson<RepairActionProjection>(`${projectPrefix(projectId)}/repair-actions`);
}

export function getRuntimeResources(projectId?: string): Promise<RuntimeResourceProjection> {
  return requestJson<RuntimeResourceProjection>(`${projectPrefix(projectId)}/runtime/resources`);
}

export function getRoles(projectId?: string): Promise<RoleSummary[]> {
  return requestJson<RoleSummary[]>(`${projectPrefix(projectId)}/roles`);
}

export function getAgents(projectId?: string): Promise<AgentSummary[]> {
  return requestJson<AgentSummary[]>(`${projectPrefix(projectId)}/agents`);
}

export function getAgentCockpit(projectId?: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`${projectPrefix(projectId)}/agent-cockpit`);
}

export function getAgentLive(projectId?: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`${projectPrefix(projectId)}/agent-live`);
}

export function getRuntime(projectId?: string): Promise<RuntimeSummary> {
  return requestJson<RuntimeSummary>(`${projectPrefix(projectId)}/runtime`);
}

export function getChannels(projectId?: string, requireFresh = false): Promise<ChannelsPage> {
  const suffix = requireFresh ? "?require_fresh=true" : "";
  return requestJson<ChannelsPage>(
    `${projectPrefix(projectId)}/channels${suffix}`,
    { bypassCache: requireFresh },
  ).then((data) => ({
    ...data,
    channels: data.channels ?? [],
  }));
}

export function getChannelDetail(channelId: string, projectId?: string): Promise<ChannelDetail> {
  return requestJson<ChannelDetail>(`${projectPrefix(projectId)}/channels/${encodeURIComponent(channelId)}`);
}

export function getChannelConversation(
  channelId: string,
  projectId?: string,
  options: { before?: string; limit?: number; requireFresh?: boolean } = {},
): Promise<ChannelDetail> {
  const params = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.before) params.set("before", options.before);
  const path = `${projectPrefix(projectId)}/channels/${encodeURIComponent(channelId)}/conversation?${params.toString()}`;
  return requestJson<ChannelDetail>(path, { bypassCache: Boolean(options.requireFresh) });
}

export function searchChannelHistory(
  channelId: string,
  q: string,
  projectId?: string,
  options: { limit?: number; threadId?: string; memberId?: string; mention?: string } = {},
): Promise<ChannelHistorySearchResult> {
  const params = new URLSearchParams({
    q,
    limit: String(options.limit ?? 30),
  });
  if (options.threadId) params.set("thread_id", options.threadId);
  if (options.memberId) params.set("member_id", options.memberId);
  if (options.mention) params.set("mention", options.mention);
  return requestJson<ChannelHistorySearchResult>(
    `${projectPrefix(projectId)}/channels/${encodeURIComponent(channelId)}/history/search?${params.toString()}`,
  );
}

export async function postChannelMessage(
  channelId: string,
  payload: Record<string, unknown>,
  projectId?: string,
): Promise<ActionResponse> {
  if (projectId) {
    return postAction("channel-post-message", { ...payload, channel_id: channelId }, projectId);
  }
  const idempotencyKey = payload.idempotency_key
    ? String(payload.idempotency_key)
    : `web-channel-post-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const response = await fetch(`/api/channels/${encodeURIComponent(channelId)}/messages`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...webActionAuthHeaders(),
      "X-Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(payload),
  });
  const data = (await response.json()) as ActionResponse;
  if (!response.ok && response.status >= 500) {
    throw new Error(data.reason || `channel message returned ${response.status}`);
  }
  clearGetCache("/api");
  return data;
}

export function getWorkflowGraph(projectId?: string): Promise<WorkflowGraph> {
  return requestJson<WorkflowGraph>(`${projectPrefix(projectId)}/workflow/graph`);
}

export function getWorkflowRequests(projectId?: string): Promise<WorkflowRequestsPage> {
  return requestJson<WorkflowRequestsPage>(
    `${projectPrefix(projectId)}/workflow-requests`,
  );
}

export function getWorkflowRequestDetail(
  requestId: string,
  projectId?: string,
): Promise<WorkflowRequestDetail> {
  return requestJson<WorkflowRequestDetail>(
    `${projectPrefix(projectId)}/workflow-requests/${encodeURIComponent(requestId)}`,
  );
}

export async function clarifyWorkflowRequest(
  projectId: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return mutateWorkflowRequest(projectId, "workflow-clarify", payload);
}

export async function prepareWorkflowProposal(
  projectId: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return mutateWorkflowRequest(projectId, "workflow-submit", {
    ...payload,
    apply: false,
  });
}

async function mutateWorkflowRequest(
  projectId: string,
  action: "workflow-clarify" | "workflow-submit",
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const response = await fetch(`${projectPrefix(projectId)}/${action}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...webActionAuthHeaders(),
    },
    body: JSON.stringify(payload),
  });
  const data = (await response.json()) as Record<string, unknown>;
  if (!response.ok && response.status >= 500) {
    throw new Error(String(data.reason || `${action} returned ${response.status}`));
  }
  invalidateProjectReadCache(projectId);
  return data;
}

export async function getWebSession(): Promise<NonNullable<RuntimeSummary["web_session"]>> {
  const response = await fetch("/api/web-session", {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`web session fetch returned ${response.status}`);
  return response.json();
}

export interface RegressionCase {
  case_id: string;
  source_task_id: string;
  feature_id?: string;
  assertions?: string[];
  command?: string;
}

export function getRegressionCases(
  projectId?: string,
  featureId?: string,
): Promise<{ cases: RegressionCase[] }> {
  const q = featureId ? `?feature_id=${encodeURIComponent(featureId)}` : "";
  return requestJson<{ cases: RegressionCase[] }>(
    `${projectPrefix(projectId)}/regression-cases${q}`,
  );
}

export async function unlockWebSession(passcode: string): Promise<{
  ok: boolean;
  status: string;
  reason?: string;
  session?: RuntimeSummary["web_session"];
}> {
  const response = await fetch("/api/web-session/unlock", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ passcode }),
  });
  const data = (await response.json()) as {
    ok: boolean;
    status: string;
    reason?: string;
    session?: RuntimeSummary["web_session"];
  };
  if (!response.ok && response.status >= 500) {
    throw new Error(data.reason || `web session unlock returned ${response.status}`);
  }
  return data;
}

export async function lockWebSession(): Promise<{
  ok: boolean;
  status: string;
  session?: RuntimeSummary["web_session"];
}> {
  const response = await fetch("/api/web-session/lock", {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  return (await response.json()) as {
    ok: boolean;
    status: string;
    session?: RuntimeSummary["web_session"];
  };
}

export function getOperatorSession(projectId?: string): Promise<OperatorSession> {
  return requestJson<OperatorSession>(`${projectPrefix(projectId)}/operator/session`);
}

export function getOperatorOutput(cursor = 0, limit = 200, projectId?: string): Promise<OperatorOutputPage> {
  return requestJson<OperatorOutputPage>(
    `${projectPrefix(projectId)}/operator/output?cursor=${cursor}&limit=${limit}`,
  );
}

export function getOperatorInbox(projectId?: string): Promise<OperatorInboxProjection> {
  return requestJson<OperatorInboxProjection>(`${projectPrefix(projectId)}/operator/inbox`);
}

export function getPlanPreview(planId: string, projectId?: string): Promise<PlanPreview> {
  return requestJson<PlanPreview>(
    `${projectPrefix(projectId)}/plans/${encodeURIComponent(planId)}/preview`,
  );
}

export function getSkills(): Promise<SkillsSummary> {
  return requestJson<SkillsSummary>("/api/skills");
}

export function getDiagnostics(traceId: string): Promise<DiagnosticsDetail> {
  return requestJson<DiagnosticsDetail>(`/api/diagnostics/${encodeURIComponent(traceId)}`);
}

export function search(q: string, limit = 50, projectId?: string): Promise<SearchResult> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return requestJson<SearchResult>(`${projectPrefix(projectId)}/search?${params.toString()}`);
}

export async function postAction(
  action: string,
  payload: Record<string, unknown>,
  projectId?: string,
): Promise<ActionResponse> {
  const idempotencyKey = payload.idempotency_key
    ? String(payload.idempotency_key)
    : `web-${action}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const body = projectId
    ? {
      project_id: projectId,
      idempotency_key: idempotencyKey,
      actor: "web",
      payload,
    }
    : payload;
  const response = await fetch(`${projectPrefix(projectId)}/actions/${encodeURIComponent(action)}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...webActionAuthHeaders(),
      "X-Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(body),
  });
  const data = (await response.json()) as ActionResponse;
  if (!response.ok && response.status >= 500 && action !== "chat-orchestrator") {
    throw new Error(data.reason || `${action} returned ${response.status}`);
  }
  clearGetCache(projectPrefix(projectId));
  return data;
}

export async function postOperatorInput(text: string, projectId?: string): Promise<OperatorInputResponse> {
  const response = await fetch(`${projectPrefix(projectId)}/operator/input`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...webActionAuthHeaders(),
    },
    body: JSON.stringify({ text }),
  });
  const data = (await response.json()) as OperatorInputResponse;
  if (!response.ok && response.status >= 500) {
    throw new Error(data.reason || `operator input returned ${response.status}`);
  }
  clearGetCache(projectPrefix(projectId));
  return data;
}

export async function startOperator(
  payload: Record<string, unknown>,
  projectId?: string,
): Promise<OperatorInputResponse> {
  const response = await fetch(`${projectPrefix(projectId)}/operator/start`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...webActionAuthHeaders(),
    },
    body: JSON.stringify(payload),
  });
  const data = (await response.json()) as OperatorInputResponse;
  if (!response.ok && response.status >= 500) {
    throw new Error(data.reason || `operator start returned ${response.status}`);
  }
  clearGetCache(projectPrefix(projectId));
  return data;
}

export async function stopOperator(reason = "web stop requested", projectId?: string): Promise<OperatorInputResponse> {
  const response = await fetch(`${projectPrefix(projectId)}/operator/stop`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...webActionAuthHeaders(),
    },
    body: JSON.stringify({ reason }),
  });
  const data = (await response.json()) as OperatorInputResponse;
  if (!response.ok && response.status >= 500) {
    throw new Error(data.reason || `operator stop returned ${response.status}`);
  }
  clearGetCache(projectPrefix(projectId));
  return data;
}
