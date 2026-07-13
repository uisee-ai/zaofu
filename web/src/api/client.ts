import type {
  OnboardingStatus,
  LoopViewProjection,
  ActionResponse,
  AgentSessionHistoryPage,
  AgentSessionRawOutput,
  AgentSummary,
  CausationChain,
  ChannelDetail,
  ChannelHistorySearchResult,
  ChannelsPage,
  CandidateDetail,
  DeliveryTrace,
  DeliveryThickTrace,
  DeliveryFeaturesPage,
  DiagnosticsDetail,
  DiagnosticsLogsPage,
  EventsPage,
  FanoutDetail,
  IntegrationQueueProjection,
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
  WorkflowGraph,
  WorkdirSummary,
  WorkspaceProjectsPage,
} from "./types";
import { cachedGetJson, clearGetCache } from "./queryClient";

async function requestJson<T>(path: string): Promise<T> {
  return cachedGetJson<T>(path);
}

function projectPrefix(projectId?: string): string {
  return projectId ? `/api/projects/${encodeURIComponent(projectId)}` : "/api";
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
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`inspect failed: ${response.status}`);
  return response.json();
}

export async function updateOnboarding(payload: {
  action: "step" | "complete" | "skip" | "reset";
  step?: number;
  backend?: string;
  notifications?: string;
}): Promise<Record<string, unknown>> {
  const response = await fetch("/api/workspace/onboarding", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`onboarding update failed: ${response.status}`);
  return response.json();
}

export async function validateWorkspaceProjectPath(root: string): Promise<Record<string, unknown>> {
  const response = await fetch("/api/workspace/projects/validate-path", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ root }),
  });
  return (await response.json()) as Record<string, unknown>;
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
  kind?: "issue" | "prd" | "refactor";
  name?: string;
  project_name?: string;
  source_ref?: string;
  source_root?: string;
  target_root?: string;
  backend?: string;
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
export function getProjectTraces(projectId?: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`${projectPrefix(projectId)}/traces`);
}

export function getRunContractProjection(projectId?: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`${projectPrefix(projectId)}/run-contract`);
}

// 131-P0-5: shadow spine read-only explain (runs/stages/health/tasks).
export function getWorkflowSpine(projectId?: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`${projectPrefix(projectId)}/workflow-spine`);
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

// doc 68 S3 — delivery-trace.v1 (read-only feature delivery projection).
export function getDeliveryTrace(
  featureId: string,
  projectId?: string,
  sinceEventId?: string,
): Promise<DeliveryTrace> {
  const params = new URLSearchParams();
  if (sinceEventId) params.set("since_event_id", sinceEventId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson<DeliveryTrace>(
    `${projectPrefix(projectId)}/delivery-traces/${encodeURIComponent(featureId)}${suffix}`,
  );
}

export function getDeliveryThickTrace(
  featureId: string,
  projectId?: string,
  sinceEventId?: string,
): Promise<DeliveryThickTrace> {
  const params = new URLSearchParams();
  if (sinceEventId) params.set("since_event_id", sinceEventId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson<DeliveryThickTrace>(
    `${projectPrefix(projectId)}/delivery-traces/${encodeURIComponent(featureId)}/thick${suffix}`,
  );
}

export function getLoops(projectId?: string): Promise<LoopProjection> {
  return requestJson<LoopProjection>(`${projectPrefix(projectId)}/loops`);
}

export function getLoopView(projectId?: string): Promise<LoopViewProjection> {
  return requestJson<LoopViewProjection>(`${projectPrefix(projectId)}/loop-view`);
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

// overview-pulse.v1 — RUN PULSE / TASK FLOW bands (project-scoped, read-only)
export function fetchOverviewPulse(projectId: string): Promise<OverviewPulse> {
  return requestJson<OverviewPulse>(
    `/api/projects/${encodeURIComponent(projectId || "default")}/overview-pulse`,
  );
}

export function getRecentEvents(limit = 60, projectId?: string): Promise<RecentEvent[]> {
  return getRecentEventsPage(limit, projectId).then((page) => page.items);
}

export function getRecentEventsPage(limit = 60, projectId?: string): Promise<EventsPage> {
  if (projectId) {
    return requestJson<EventsPage>(
      `${projectPrefix(projectId)}/events?limit=${encodeURIComponent(String(limit))}`,
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

export function getEventsPage(params: URLSearchParams, projectId?: string): Promise<EventsPage> {
  return requestJson<EventsPage>(`${projectPrefix(projectId)}/events?${params.toString()}`);
}

export interface PendingKanbanProposal {
  proposal_event_id: string;
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
  return requestJson<{ items: PendingKanbanProposal[] }>(
    `${projectPrefix(projectId)}/kanban-agent/pending-proposals`,
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
  return requestJson<AgentSessionHistoryPage>(
    `${projectPrefix(projectId)}/agent-session/history?${search.toString()}`,
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

export function getTraceDetail(traceId: string, projectId?: string): Promise<TraceDetail> {
  return requestJson<TraceDetail>(`${projectPrefix(projectId)}/traces/${encodeURIComponent(traceId)}`);
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

export function getWorkdirs(projectId?: string): Promise<WorkdirSummary[]> {
  return requestJson<WorkdirSummary[]>(`${projectPrefix(projectId)}/workdirs`);
}

export function getIntegrationQueue(projectId?: string): Promise<IntegrationQueueProjection> {
  return requestJson<IntegrationQueueProjection>(`${projectPrefix(projectId)}/integration-queue`);
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

export function getChannels(projectId?: string): Promise<ChannelsPage> {
  return requestJson<ChannelsPage>(`${projectPrefix(projectId)}/channels`).then((data) => ({
    ...data,
    channels: data.channels ?? [],
  }));
}

export function getChannelDetail(channelId: string, projectId?: string): Promise<ChannelDetail> {
  return requestJson<ChannelDetail>(`${projectPrefix(projectId)}/channels/${encodeURIComponent(channelId)}`);
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
