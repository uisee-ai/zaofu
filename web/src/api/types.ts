// P5/W2 (docs/impl/22-zaofu-canonical-dag.md): TaskContract is the kanban
// contract synthesized by orchestrator at stage ④ backlog. The 6
// required_backlog_refs (spec_ref / plan_ref / tdd_ref / critic_event_id /
// critic_gate_ref / evidence_contract) are surfaced separately so the UI
// can render BacklogRefsBadge (P5/W1).
export interface TaskContract {
  behavior?: string;
  scope?: string[];
  verification?: string;
  verification_tiers?: string[];
  acceptance?: string;
  exclusions?: string[];
  owner_role?: string;
  owner_instance?: string;
  handoff_artifacts?: string[];
  // 6 required_backlog_refs (P0-P4):
  spec_ref?: string;
  plan_ref?: string;
  tdd_ref?: string;
  critic_event_id?: string;
  critic_gate_ref?: string;
  evidence_contract?: Record<string, unknown>;
  // Catch-all for fields not yet typed (forward-compat):
  [key: string]: unknown;
}

// P5/W1: the canonical 6 keys (kept in sync with TaskContract.spec_ref ...).
// `as const` so consumers can derive a stricter union if useful.
export const BACKLOG_REF_KEYS = [
  "spec_ref",
  "plan_ref",
  "tdd_ref",
  "critic_event_id",
  "critic_gate_ref",
  "evidence_contract",
] as const;

export type BacklogRefKey = (typeof BACKLOG_REF_KEYS)[number];

export interface Task {
  id: string;
  title: string;
  status: string;
  kanban_column?: string;
  workflow_phase?: string;
  impl_exit_gate_state?: string;
  verify_state?: string;
  judge_state?: string;
  verify_lanes?: Array<{
    lane: string;
    state: string;
    event_type: string;
  }>;
  workflow_badges?: Array<{
    kind: string;
    label: string;
    tone: string;
    state?: string;
  }>;
  workflow_projection?: {
    workflow_phase: string;
    impl_exit_gate_state: string;
    verify_state: string;
    judge_state: string;
    verify_lanes: Array<{
      lane: string;
      state: string;
      event_type: string;
    }>;
    terminal_required_event: string;
    rework_target: string;
    rework_reason: string;
    badges: Array<{
      kind: string;
      label: string;
      tone: string;
      state?: string;
    }>;
  };
  terminal?: boolean;
  terminal_outcome?: "success" | "cancelled" | "";
  source?: string;
  priority?: number;
  assigned_to: string;
  retry_count: number;
  blocked_reason: string;
  phase: string | null;
  created_at: string;
  blocked_by?: string[];
  ready?: boolean;
  skills_required?: string[];
  links?: {
    trace?: string;
    candidate?: string;
    fanout?: string;
    fanout_child?: string;
    fanout_run?: string;
  };
  fanout?: {
    fanout_id?: string;
    child_id?: string;
    run_id?: string;
    workdir?: string;
    source_branch?: string;
    task_map_ref?: string;
    source_index_ref?: string;
    lane_id?: string;
    affinity_tag?: string;
    assignment_strategy?: string;
    progress?: {
      done: number;
      total: number;
      failed: number;
      pending: number;
      percent: number;
    };
  };
  git?: Record<string, unknown>;
  latest_event?: EventRecord | null;
  evidence_badges?: Array<{
    kind: string;
    label: string;
    tone: string;
  }>;
  route_summary?: ExecutionRouteSummary;
  // P5/W2: optional structured contract. Server returns asdict(task.contract).
  contract?: TaskContract;
}

export interface EventRecord {
  seq?: number;
  id?: string;
  ts?: string;
  type: string;
  actor?: string | null;
  task_id?: string | null;
  payload?: Record<string, unknown>;
  causation_id?: string | null;
  correlation_id?: string | null;
}

export interface WorkflowGraph {
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
  overlays: {
    fanouts: EventRecord[];
    runs: EventRecord[];
  };
  counts: Record<string, number>;
}

export interface Feature {
  id: string;
  title: string;
  status: string;
  priority: number;
  source?: string;
  confidence?: number;
  trace_ref?: string;
  fanout_ref?: string;
  degraded?: boolean;
  reason?: string;
}

export interface DeliveryFeaturesPage {
  delivery_features: Feature[];
  features: Feature[];
}

// doc 68 S3 / doc 65 — delivery-trace.v1 (read-only projection).
export interface DeliveryTraceNode {
  task_id: string;
  title: string;
  planned: {
    owner_role?: string;
    owner_instance?: string;
    wave?: number;
    blocked_by?: string[];
    scope?: string[];
  };
  actual: {
    status: string;
    assigned_to: string;
    evidence_events: string[];
    fanout_ids?: string[];
    affinity?: {
      planned_owner: string; planned_role: string; actual_owner: string;
      instances_history: string[]; drifted: boolean; drift_kind: string;
    };
    started_at?: string;
    completed_at?: string;
    duration_seconds?: number | null;
    trace_id?: string;
    changed_files?: string[];
    agent_summary?: { launched: number; executed: number; expected: number };
    health?: { heartbeat_age_seconds: number | null; stuck: boolean };
    on_critical_path?: boolean;
  };
  drift: unknown[];
  superseded?: boolean;
}

export interface DeliveryClosedLoopNode {
  node_id: string;
  kind: string;
  title?: string;
  status?: string;
  project_id?: string;
  feature_id?: string;
  trace_id?: string;
  run_id?: string;
  task_id?: string;
  cycle_id?: string;
  agent_id?: string;
  role_id?: string;
  topology?: string;
  evidence_event_ids?: string[];
  artifact_refs?: string[];
  source_event_ids?: string[];
  fanout_ids?: string[];
  deep_links?: Record<string, string>;
  [key: string]: unknown;
}

export interface DeliveryClosedLoopEdge {
  from: string;
  to: string;
  kind: string;
  status?: string;
}

export interface DeliveryClosedLoop {
  schema_version: string;
  trace_id: string;
  project_id?: string;
  feature_id?: string;
  node_count: number;
  edge_count: number;
  nodes: DeliveryClosedLoopNode[];
  edges: DeliveryClosedLoopEdge[];
  readiness?: Record<string, unknown>;
  source_event_ids?: string[];
  diagnostics?: Array<{ kind: string; message: string }>;
}

export interface DeliveryTraceDriftItem {
  kind: string;
  severity: string;
  task_id: string;
  message: string;
}

export interface DeliveryTracePhaseRun {
  task_id: string;
  fanout_id: string;
  topology: string;
  status: string;
  launched: number;
  expected: number;
  executed: number;
}

export interface DeliveryTracePhase {
  phase_id: string;
  order: number;
  status: string;
  task_count: number;
  done_count: number;
  completion_rate: number;
  pass_rate: number | null;
  eval: { verdict: string } & Record<string, unknown>;
  rework_count: number;
  paused_count: number;
  agent_runs: DeliveryTracePhaseRun[];
  task_ids: string[];
  lifecycle_events?: Array<{ task_id?: string; kind?: string; ts?: string }>;
}

export interface DeliveryTraceCycleEvent {
  seq: number;
  event_id: string;
  event_type: string;
  task_id?: string;
  ts?: string;
  status?: string;
}

export interface DeliveryTraceCycle {
  cycle_id: string;
  kind: string;
  status: string;
  phase?: string;
  order?: number;
  gate?: string;
  trigger?: string;
  topology?: string;
  task_ids?: string[];
  task_count?: number;
  done_count?: number;
  completion_rate?: number | null;
  pass_rate?: number | null;
  rework_count?: number;
  paused_count?: number;
  started_at?: string;
  ended_at?: string;
  evidence_refs?: string[];
  events?: DeliveryTraceCycleEvent[];
  [key: string]: unknown;
}

export interface DeliveryTraceAutoresearchCycle extends DeliveryTraceCycle {
  kind: "autoresearch" | string;
  trigger?: string;
  policy?: string;
  deposition?: string;
  sandbox?: string;
  score_delta?: number | null;
  baseline_score?: number | null;
  candidate_score?: number | null;
}

export interface TaskMapHistoryEntry {
  artifact_id: string;
  version: number;
  status: string;
  ref: string;
  supersedes: string;
  reason: string;
  event_id: string;
  superseded: boolean;
  is_current: boolean;
}

export interface DeliveryWorkflowChildRun {
  child_id: string;
  stage_id?: string;
  role?: string;
  task_id?: string;
  status: string;
  worker_id?: string;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number | null;
  error?: { type?: string; message?: string } | Record<string, unknown>;
  links?: Record<string, string>;
  source_event_ids?: string[];
}

export interface DeliveryWorkflowStageRun {
  stage_id: string;
  node_id: string;
  label: string;
  kind: string;
  operator_kind: string;
  status: string;
  attempt: number;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number | null;
  queue_wait_ms?: number | null;
  upstream_stage_ids: string[];
  downstream_stage_ids: string[];
  trigger_events: string[];
  output_events: string[];
  source_event_ids: string[];
  fanout_id?: string;
  fanout_child_runs?: DeliveryWorkflowChildRun[];
  task_ids: string[];
  artifact_refs: string[];
  metrics: Record<string, number | string | null>;
  verdict?: { status?: string; reason?: string; evidence_event_id?: string };
  metadata?: Record<string, unknown>;
}

export interface DeliveryWorkflowTrace {
  schema_version: string;
  workflow_id: string;
  project_id?: string;
  feature_id?: string;
  task_map_ref?: string;
  config_ref?: string;
  graph: {
    schema_version: string;
    nodes: Array<Record<string, unknown>>;
    edges: Array<Record<string, unknown>>;
  };
  stage_runs: DeliveryWorkflowStageRun[];
  fanout_runs: Array<Record<string, unknown>>;
  active_stage_ids: string[];
  metrics: Record<string, unknown>;
  diagnostics: Array<{ kind: string; message: string }>;
  source_event_ids: string[];
}

export interface DeliveryTaskFlowTask {
  task_id: string;
  title: string;
  status: string;
  assigned_to?: string;
  phase?: string;
  owner_role?: string;
  owner_instance?: string;
  blocked_by?: string[];
  evidence_event_ids?: string[];
  latest_event?: { event_id?: string; event_type?: string; ts?: string };
  source_event_ids?: string[];
}

export interface DeliveryTaskFlowStage {
  stage_id: string;
  label: string;
  status: string;
  tasks_total: number;
  tasks_done: number;
  tasks_running: number;
  tasks_failed: number;
  tasks_blocked?: number;
  active_task_ids: string[];
  task_ids: string[];
  tasks: DeliveryTaskFlowTask[];
  run_group_ids: string[];
  gate_summary?: Record<string, unknown>;
  source_event_ids?: string[];
  diagnostics?: Array<{ kind: string; message: string }>;
}

export interface DeliveryTaskFlow {
  schema_version: string;
  stage_order: string[];
  active_stage_ids: string[];
  stages: DeliveryTaskFlowStage[];
  metrics: Record<string, number | string | null>;
  diagnostics: Array<{ kind: string; message: string }>;
}

export interface DeliveryRunGroup {
  schema_version: string;
  group_id: string;
  stage_id: string;
  node_id?: string;
  label: string;
  kind: string;
  operator_kind?: string;
  status: string;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number | null;
  task_ids: string[];
  children: Array<Record<string, unknown>>;
  steps: Array<Record<string, unknown>>;
  metrics: Record<string, number | string | null>;
  verdict?: Record<string, unknown>;
  artifact_refs?: string[];
  source_event_ids?: string[];
}

export interface DeliveryRunTraceSpan {
  trace_id: string;
  span_id: string;
  parent_span_id?: string;
  task_id?: string;
  run_id?: string;
  fanout_id?: string;
  role?: string;
  instance_id?: string;
  backend?: string;
  status: string;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number | null;
  tokens_input?: number;
  tokens_output?: number;
  tools_count?: number;
  error?: Record<string, unknown>;
  evidence_refs?: string[];
  raw_event_refs?: string[];
  kind?: string;
  name?: string;
  cost_usd?: number;
  tool_calls?: Array<Record<string, unknown>>;
  degraded?: boolean;
}

export interface DeliveryThickGraphNode {
  id: string;
  kind: "task" | "stage" | "gate" | "behavior" | "eval" | "artifact" | "span" | string;
  label: string;
  status?: string;
  task_id?: string;
  task_ids?: string[];
  event_ids?: string[];
  behavior_ids?: string[];
  eval_ids?: string[];
  source_refs?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface DeliveryThickGraphEdge {
  id: string;
  kind: "caused_by" | "validated_by" | "reworked_by" | "failed_by" | "produced" | "consumed" | "contains" | string;
  source: string;
  target: string;
  status?: string;
  event_ids?: string[];
}

export interface DeliveryBehaviorOverlay {
  behavior_id: string;
  loop_id?: string;
  kind: string;
  status: string;
  task_ids?: string[];
  event_ids?: string[];
  summary?: string;
  owner_event_type?: string;
  detector?: string;
}

export interface DeliveryEvalOverlay {
  eval_id: string;
  loop_id?: string;
  kind: string;
  status: string;
  task_ids?: string[];
  event_ids?: string[];
  score?: number | null;
  detail?: unknown;
  evaluator?: string;
  owner_event_type?: string;
}

export interface DeliveryThickTrace {
  schema_version: "delivery-thick-trace.v1" | string;
  generated_at: string;
  project_id?: string;
  target: {
    id: string;
    trace_id?: string;
    status?: string;
    workflow_archetype?: string;
    synthetic?: boolean;
  };
  graph: {
    node_count: number;
    edge_count: number;
    layers: string[];
    nodes: DeliveryThickGraphNode[];
    edges: DeliveryThickGraphEdge[];
  };
  spans: DeliveryRunTraceSpan[];
  span_count: number;
  behaviors: DeliveryBehaviorOverlay[];
  evals: DeliveryEvalOverlay[];
  artifacts: Array<Record<string, unknown>>;
  improvement_candidates: Array<Record<string, unknown>>;
  cursor?: DeliveryTraceCursor;
  diagnostics: Array<{ kind: string; message: string }>;
  otel?: Record<string, unknown>;
  related_loop_ids?: string[];
  related_loop_count?: number;
}

export interface DeliveryAutoresearchGraph {
  schema_version: string;
  graph_id: string;
  comparison_mode: "ab" | "single_candidate" | string;
  status: string;
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, string>>;
  source_event_ids?: string[];
}

export interface DeliveryRunTrace {
  schema_version: string;
  trace_id: string;
  span_count: number;
  timeline_count: number;
  spans: DeliveryRunTraceSpan[];
  timeline: Array<Record<string, unknown>>;
  usage_summary: Record<string, unknown>;
  autoresearch_graphs: DeliveryAutoresearchGraph[];
  diagnostics: Array<{ kind: string; message: string }>;
}

export interface DeliveryTraceCursor {
  schema_version: string;
  last_event_id: string;
  last_seq: number;
  since_event_id?: string;
  since_seq?: number | null;
  new_event_count: number;
  has_more: boolean;
  degraded: boolean;
  reason?: string;
}

export interface DeliveryTraceDelta {
  schema_version: string;
  type: string;
  seq?: number;
  event_id?: string;
  event_type?: string;
  status?: string;
  task_id?: string;
  stage_id?: string;
  fanout_id?: string;
  ts?: string;
  reason?: string;
}

export interface LoopProjectionSummary {
  total: number;
  open: number;
  verifying: number;
  recovered: number;
  exhausted: number;
  behavior_count: number;
  eval_count: number;
  candidate_count: number;
  action_count?: number;
  verification_count?: number;
  learning_count?: number;
  by_kind: Record<string, number>;
}

export interface LoopItem {
  loop_id: string;
  kind: string;
  status: string;
  title?: string;
  summary?: string;
  task_ids?: string[];
  feature_ids?: string[];
  fanout_ids?: string[];
  trace_ids?: string[];
  event_ids?: string[];
  source_event_types?: string[];
  behavior_ids?: string[];
  eval_ids?: string[];
  candidate_ids?: string[];
  started_at?: string;
  updated_at?: string;
  trigger_event_id?: string;
  fix_layer?: string;
  diagnosis_id?: string;
  verification_ids?: string[];
  latest_verification_id?: string;
  latest_verification_status?: string;
}

export interface LoopDiagnosisRecord {
  diagnosis_id: string;
  loop_id?: string;
  candidate_id?: string;
  source_kind?: string;
  fix_layer?: string;
  confidence?: number;
  reason?: string;
  recommended_action?: string;
  evidence_refs?: string[];
  source_event_types?: string[];
  secondary_signals?: string[];
  evidence_packet?: Record<string, unknown>;
}

export interface LoopActionRecord {
  action_id: string;
  loop_id?: string;
  candidate_id?: string;
  suggested_action?: string;
  source_kind?: string;
  status: string;
  request_event_id?: string;
  mapped_event_id?: string;
  mapped_event_type?: string;
  mapped_action?: string;
  downstream_action_id?: string;
  terminal_event_id?: string;
  terminal_event_type?: string;
  outcome?: string;
  reason?: string;
  task_ids?: string[];
  event_ids?: string[];
  evidence_refs?: string[];
  proposal_only?: boolean;
  requested_at?: string;
  updated_at?: string;
  idempotency_key?: string;
  verification_ids?: string[];
  latest_verification_id?: string;
  latest_verification_status?: string;
}

export interface LoopVerificationRecord {
  verification_id: string;
  action_id?: string;
  source_action_id?: string;
  loop_id?: string;
  candidate_id?: string;
  status: string;
  result?: string;
  mode?: string;
  request_event_id?: string;
  completed_event_id?: string;
  terminal_event_id?: string;
  terminal_event_type?: string;
  reason?: string;
  missing_evidence?: string[];
  next_check?: string;
  task_ids?: string[];
  event_ids?: string[];
  evidence_refs?: string[];
  requested_at?: string;
  updated_at?: string;
}

export interface LoopLearningRecord {
  learning_id: string;
  loop_id?: string;
  candidate_id?: string;
  action_id?: string;
  verification_id?: string;
  artifact_kind?: string;
  artifact_ref?: string;
  status?: string;
  fix_layer?: string;
  verification_status?: string;
  promotion_path?: string;
  promotion_status?: string;
  promotion_target?: string;
  promotion_id?: string;
  promotion_ref?: string;
  promotion_event_ids?: string[];
  promotion_reason?: string;
  summary?: string;
  event_ids?: string[];
  evidence_refs?: string[];
  updated_at?: string;
}

export interface LoopProjection {
  schema_version: "loop.v1" | string;
  generated_at: string;
  project_id?: string;
  summary: LoopProjectionSummary;
  loops: LoopItem[];
  behaviors: DeliveryBehaviorOverlay[];
  evals: DeliveryEvalOverlay[];
  diagnoses?: LoopDiagnosisRecord[];
  candidates: Array<Record<string, unknown>>;
  actions?: LoopActionRecord[];
  verifications?: LoopVerificationRecord[];
  learning?: LoopLearningRecord[];
  source_event_ids?: string[];
  diagnostics?: Array<{ kind: string; message: string }>;
}

export interface MeasureLoopLens {
  id: string;
  label: string;
  default_layout?: string;
}

export interface MeasureLoopMetric {
  id: string;
  label: string;
  value: string;
  raw?: string | number | boolean | null;
  tone?: string;
  detail?: string;
  source_event_ids?: string[];
  task_ids?: string[];
  trace_ids?: string[];
  loop_ids?: string[];
  graph_node_ids?: string[];
  source_projection_refs?: string[];
}

export interface MeasureLoopStage {
  id: string;
  label: string;
  value: string;
  detail?: string;
  tone?: string;
  source_event_ids?: string[];
  task_ids?: string[];
  trace_ids?: string[];
  loop_ids?: string[];
  graph_node_ids?: string[];
  source_projection_refs?: string[];
}

export interface MeasureLoopGraph {
  layout_hint?: string;
  node_count?: number;
  edge_count?: number;
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
}

export interface MeasureLoopFeedItem {
  seq?: number;
  event_id?: string;
  event_type?: string;
  task_id?: string;
  status?: string;
  ts?: string;
  trace_id?: string;
}

export interface MeasureLoopProjection {
  schema_version: "measure-loop.v1" | string;
  generated_at: string;
  project_id?: string;
  feature_id?: string;
  active_lens: string;
  lenses: MeasureLoopLens[];
  summary: Record<string, unknown>;
  metrics: MeasureLoopMetric[];
  stages: MeasureLoopStage[];
  graph: MeasureLoopGraph;
  feed: MeasureLoopFeedItem[];
  diagnostics?: Array<{ kind: string; message: string }>;
  source_projection_refs?: string[];
}

export interface LoopActionResponse {
  ok: boolean;
  status: string;
  action_id?: string;
  request_event_id?: string;
  mapped_event_id?: string;
  mapped_event_type?: string;
  terminal_event_id?: string;
  terminal_event_type?: string;
  reason?: string;
  idempotency?: { key?: string; status?: string };
  [key: string]: unknown;
}

export interface LoopLearningPromotionResponse {
  ok: boolean;
  status: string;
  promotion_id?: string;
  target?: string;
  proposal_ref?: string;
  request_event_id?: string;
  terminal_event_id?: string;
  terminal_event_type?: string;
  reason?: string;
  idempotency?: { key?: string; status?: string };
  [key: string]: unknown;
}

// doc 82 P3 — trace-level cockpit summaries (additive, read-only)
export interface DeliveryScoreSummaryEntry {
  cycle_id?: string;
  baseline_score?: number | null;
  candidate_score?: number | null;
  score_delta?: number | null;
  status?: string;
}

export interface DeliveryScoreSummary {
  schema_version: string;
  scored_cycle_count: number;
  latest: DeliveryScoreSummaryEntry;
  best: DeliveryScoreSummaryEntry;
}

export interface DeliveryDepositionSummary {
  schema_version: string;
  counts: Record<string, number>;
  latest_deposition: string;
  replan_gate_status: string;
  replan_eval_decision: string;
  owner_decision_required: boolean;
}

export interface ObservabilityRef {
  kind: string;
  trace_id: string;
  event_count: number;
  last_event_id: string;
  last_event_type: string;
}

// doc 82 §8.2 — diagnostics log rows
export interface DiagnosticsLogRow {
  timestamp: string;
  level: string;
  trace_id: string;
  task_id: string;
  role: string;
  source: string;
  message: string;
  attrs: Record<string, unknown>;
  raw_event_ref: string;
}

export interface DiagnosticsLogsPage {
  schema_version: string;
  project_id: string;
  rows: DiagnosticsLogRow[];
  count: number;
}

// 2026-06-10 delivery slice 1 — per-task flow metrics + workflow archetype.
// All optional/defensive: older backends omit both fields entirely.
export type DeliveryWorkflowArchetype = "feature" | "refactor" | "bugfix";

export interface DeliveryFlowConvergenceRound {
  round: number;
  passed: number;
  failed: number;
}

export interface DeliveryFlowTaskMetrics {
  queue_wait_seconds?: number | null;
  first_response_seconds?: number | null;
  wait_seconds?: number | null;
  active_seconds?: number;
  rework_seconds?: number;
  backedge_count?: number;
  convergence?: DeliveryFlowConvergenceRound[];
}

export interface DeliveryFlowMetrics {
  workflow_archetype?: DeliveryWorkflowArchetype | string;
  tasks?: Record<string, DeliveryFlowTaskMetrics>;
}

// 2026-06-11 S-D — run-chain.v1 (Airflow dag_run-style stage chain).
export interface DeliveryRunChainStage {
  stage: string;
  status: "done" | "active" | "waiting" | string;
  entered_at?: string | null;
  completed_at?: string | null;
  via_event_id?: string | null;
  causation_id?: string | null;
  seq_first?: number | null;
  seq_last?: number | null;
  occurrences: number;
  task_ids: string[];
}

export interface DeliveryRunChain {
  schema_version: string;
  status: "completed" | "in_progress" | "not_started" | "no_stage_order" | string;
  trigger?: { event_id: string; type: string; ts?: string | null; actor?: string | null } | null;
  stages: DeliveryRunChainStage[];
}

// 2026-06-11 S-A — task-lifecycle.v1 (Airflow task-instance state history).
export interface DeliveryTaskLifecycleState {
  state: string;
  entered_at?: string | null;
  dwell_seconds?: number | null;
  via_event_id?: string | null;
  try?: number | null;
}

export interface DeliveryTaskGateResult {
  type: string;
  passed: boolean;
  event_id?: string | null;
  detail?: Record<string, string | number | boolean>;
}

export interface DeliveryTaskTry {
  try: number;
  dispatched_at?: string | null;
  first_response_seconds?: number | null;
  outcome: "in_flight" | "done" | "blocked" | "failed" | string;
  gate_results: DeliveryTaskGateResult[];
  rework_kind?: string | null;
  dispatch_id?: string | null;
  briefing_ref?: string | null;
  snapshot_ref?: string | null;
  seq_first?: number | null;
  seq_last?: number | null;
  tool_calls?: number;
  tokens_in?: number;
  tokens_out?: number;
}

export interface DeliveryTaskLifecycleEntry {
  state_history: DeliveryTaskLifecycleState[];
  tries: DeliveryTaskTry[];
}

export interface DeliveryTaskLifecycle {
  schema_version: string;
  tasks: Record<string, DeliveryTaskLifecycleEntry>;
}

// causation-chain.v1 — event ancestry from a target event back to its source
// (chain order: target → source).
export interface CausationChainEntry {
  id: string;
  type?: string | null;
  ts?: string | null;
  task_id?: string | null;
}

export interface CausationChain {
  schema_version: string;
  chain: CausationChainEntry[];
}

export interface DeliveryTrace {
  schema_version: string;
  feature_id: string;
  trace_id: string;
  status: string;
  synthetic: boolean;
  workflow_archetype?: DeliveryWorkflowArchetype | string;
  flow_metrics?: DeliveryFlowMetrics;
  run_chain?: DeliveryRunChain;
  task_lifecycle?: DeliveryTaskLifecycle;
  phases?: DeliveryTracePhase[];
  phase_count?: number;
  cycles?: DeliveryTraceCycle[];
  autoresearch_cycles?: DeliveryTraceAutoresearchCycle[];
  task_map_history?: TaskMapHistoryEntry[];
  workflow_spine?: {
    schema_version: string;
    node_count: number;
    nodes: Array<Record<string, unknown>>;
    diagnostics: Array<{ kind: string; message: string }>;
  };
  task_map: { status: string; task_count: number; wave_count: number };
  execution_graph: {
    task_count: number;
    done_count: number;
    in_progress_count: number;
    blocked_count: number;
    waiting_count: number;
    nodes: DeliveryTraceNode[];
    edges: { from: string; to: string; kind: string; status: string }[];
    waves: { wave: number; task_ids: string[]; status: string }[];
  };
  drift_report: { status: string; summary: Record<string, number>; items: DeliveryTraceDriftItem[] };
  ship: { status: string; readiness?: string; shipped?: boolean; ship_status?: string; merge_ref?: string; candidate_status?: string; required_tasks: number; done_tasks: number; missing_evidence: { task_id: string; status: string }[]; release_blockers?: { kind: string; severity: string }[] };
  closed_loop?: DeliveryClosedLoop;
  workflow_trace?: DeliveryWorkflowTrace;
  task_flow?: DeliveryTaskFlow;
  run_groups?: DeliveryRunGroup[];
  trace?: DeliveryRunTrace;
  score_summary?: DeliveryScoreSummary;
  deposition_summary?: DeliveryDepositionSummary;
  observability_refs?: ObservabilityRef[];
  cursor?: DeliveryTraceCursor;
  deltas?: DeliveryTraceDelta[];
  thick_trace?: DeliveryThickTrace;
  related_loop_ids?: string[];
  related_loop_count?: number;
  diagnostics: { kind: string; message: string }[];
}

export interface Worker {
  instance_id: string;
  backend: string;
  spawned_at: string;
  state: string;
}

export interface CostRole {
  usd: number;
  input_tokens: number;
  output_tokens: number;
  entries: number;
}

export interface CostSummary {
  total_usd: number;
  per_role: Record<string, CostRole>;
}

export interface RuntimeSummary {
  mode: string;
  live: boolean;
  state_dir?: string;
  generated_at?: string;
  seq?: number;
  providers?: Record<string, number>;
  sessions?: Record<string, unknown>;
  workdirs?: Record<string, unknown>;
  git?: Record<string, unknown>;
  actions?: {
    mutation_enabled: boolean;
    allowed: string[];
    requires_token?: boolean;
  };
  web_session?: {
    mode: "local_trusted" | "remote_passcode" | "token_required" | "read_only" | string;
    unlocked: boolean;
    actions_enabled: boolean;
    expires_at?: string | null;
    requires_token?: boolean;
    token_fallback_enabled?: boolean;
  };
  agent_surface?: {
    id: string;
    session_id: string;
    status: string;
    scope: string;
    task_id?: string;
    context_task_id?: string;
    backend: string;
    configured_backend?: string;
    default_backend?: string;
    backends?: Array<{
      id: string;
      title: string;
      available: boolean;
      source?: string;
      default?: boolean;
      capabilities?: Record<string, unknown>;
    }>;
    descriptor?: Record<string, unknown>;
    profile: string;
    terminal_backed: boolean;
    delivery: string;
    capabilities: string[];
    allowed_actions?: string[];
    forbidden: string[];
    forbidden_capabilities?: string[];
    boundary?: Record<string, unknown>;
    status_model?: Record<string, unknown>;
    evidence_model?: Record<string, unknown>;
    shared_context?: KanbanAgentSharedContext;
    skills_available?: KanbanAgentSkillsSummary;
    last_event_seq: number;
    started_at?: string;
    alive?: boolean;
    output_seq?: number;
    state_dir?: string;
    shared_project_workdir?: string;
    operator_workdir?: string;
    workdir?: string;
    transcript_path?: string;
  };
  resources?: RuntimeResourceProjection;
  last_known_good?: Record<string, unknown>;
}

export interface RuntimeResourceProjection {
  schema_version: string;
  is_derived_projection?: boolean;
  truth_sources?: string[];
  generated_at?: string;
  summary: {
    provider_sessions: number;
    terminal_excerpts: number;
    stale_sessions: number;
    tmux_sessions: number;
  };
  session?: Record<string, unknown>;
  provider_sessions: Array<Record<string, unknown>>;
  terminal_excerpts: Array<Record<string, unknown>>;
  host: {
    host_id: string;
    tmux: {
      configured_session?: string;
      available?: boolean;
      sessions: Array<Record<string, unknown>>;
      role_sessions?: string[];
      configured_active?: boolean;
      probe?: string;
      read_only?: boolean;
    };
  };
  policy?: Record<string, unknown>;
  error?: string;
}

export interface KanbanAgentSharedContext {
  mode?: string;
  project_root?: string;
  shared_project_workdir?: string;
  state_dir?: string;
  zf_yaml?: string;
  operator_workdir?: string;
  context_files?: Record<string, string>;
  truth_files?: Array<{ name: string; path: string }>;
  projections?: string[];
}

export interface KanbanAgentSkillsSummary {
  pool_path?: string;
  pool_count?: number;
  enabled_role_count?: number;
  names?: string[];
  enabled_by_role?: Array<Record<string, unknown>>;
  warnings?: number;
}

export interface TraceSummary {
  trace_id: string;
  first_seq: number;
  last_seq: number;
  first_ts: string;
  last_ts: string;
  duration_seconds?: number | null;
  event_count: number;
  task_ids: string[];
  actors: string[];
  backends?: string[];
  status?: string;
  source_event_ids?: string[];
  inferred_ids?: string[];
  last_type: string;
}

export interface CandidateSummary {
  pdd_id: string;
  candidate_ref: string;
  last_seq: number;
  last_type: string;
  tasks: string[];
  status: string;
  ship_ready: boolean;
}

export interface FanoutSummary {
  fanout_id: string;
  last_seq: number;
  last_type: string;
  children: string[];
  tasks: string[];
  topology?: string;
  stage_id?: string;
  target_ref?: string;
  trace_id?: string;
  pdd_id?: string;
  status?: string;
  progress?: {
    done: number;
    total: number;
    failed: number;
    pending: number;
    percent: number;
    active_total?: number;
    planned_total?: number;
    lane_scope?: string;
  };
  lane_projection?: FanoutLaneProjection;
}

export interface FanoutLaneProjection {
  strategy?: string;
  lane_profile?: string;
  stage_slot?: string;
  planned_lane_count: number;
  active_lane_count: number;
  active_child_count: number;
  planned_roles: string[];
  active_roles: string[];
  planned_lane_ids: string[];
  active_lane_ids: string[];
  active_task_ids: string[];
  scope: string;
  is_scoped: boolean;
  source?: string;
}

export interface IntegrationQueueSummary {
  total: number;
  counts: Record<string, number>;
  queued: number;
  integrating: number;
  needs_review: number;
  integrated: number;
  discarded: number;
  stale_rejected: number;
  issue_count: number;
}

export interface IntegrationQueueEntry {
  id: string;
  status: string;
  task_id?: string;
  fanout_instance_id?: string;
  source_ref?: string;
  base_ref?: string;
  handoff_ref?: string;
  artifact_refs?: string[];
  verification_refs?: string[];
  reason?: string;
  retry_count?: number;
  created_event_id?: string;
  updated_event_id?: string;
  updated_at?: string;
  event_refs?: Array<Record<string, string>>;
  issues?: Array<Record<string, string>>;
  [key: string]: unknown;
}

export interface IntegrationArbiterDecision {
  id: string;
  queue_entry_id: string;
  queue_status: string;
  decision: string;
  status: string;
  idempotency_key: string;
  task_id?: string;
  fanout_instance_id?: string;
  target_event_type?: string;
  reason?: string;
  audit_event_id?: string;
  action_options?: Array<Record<string, unknown>>;
  controlled_action?: Record<string, unknown>;
  dirty_guard?: Record<string, unknown>;
  merge_safety?: Record<string, unknown>;
}

export interface IntegrationArbiterProjection {
  schema_version: string;
  is_derived_projection: boolean;
  summary: Record<string, number | Record<string, number>>;
  input_event_types?: string[];
  dirty_guard?: Record<string, unknown>;
  merge_safety?: Record<string, unknown>;
  decisions: IntegrationArbiterDecision[];
  policy?: Record<string, unknown>;
}

export interface IntegrationQueueProjection {
  schema_version: string;
  is_derived_projection: boolean;
  summary: IntegrationQueueSummary;
  entries: IntegrationQueueEntry[];
  stale_entries: IntegrationQueueEntry[];
  issues: Array<Record<string, string>>;
  arbiter?: IntegrationArbiterProjection;
}

export interface RepairActionSummary {
  total: number;
  counts: Record<string, number>;
  pending: number;
  applied: number;
  rejected: number;
  invalid: number;
  duplicate: number;
  issue_count: number;
}

export interface RepairActionRecord {
  id: string;
  kind: string;
  status: string;
  task_id?: string;
  stage?: string;
  fanout_id?: string;
  fanout_child_id?: string;
  queue_entry_id?: string;
  worker_id?: string;
  role?: string;
  projection?: string;
  attempt?: number;
  idempotency_key?: string;
  reason?: string;
  requested_event_id?: string;
  terminal_event_id?: string;
  updated_at?: string;
  evidence_refs?: string[];
  issues?: Array<Record<string, string>>;
  [key: string]: unknown;
}

export interface RepairActionProjection {
  schema_version: string;
  is_derived_projection: boolean;
  summary: RepairActionSummary;
  actions: RepairActionRecord[];
  issues: Array<Record<string, string>>;
}

export interface ChannelSummary {
  channel_id: string;
  name?: string;
  status?: string;
  type?: string;
  scope?: Record<string, unknown>;
  members?: Array<Record<string, unknown>>;
  messages?: Array<Record<string, unknown>>;
  threads?: Record<string, unknown>;
  read_state?: Record<string, unknown>;
  attention?: Array<Record<string, unknown>>;
  syntheses?: Array<Record<string, unknown>>;
  synthesis_requests?: Array<Record<string, unknown>>;
  workflow_requests?: Array<Record<string, unknown>>;
  mentions_detected?: Array<Record<string, unknown>>;
  routes?: Array<Record<string, unknown>>;
  discussions?: Record<string, Record<string, unknown>>;
  open_questions?: Record<string, Record<string, unknown>>;
  reply_requests?: Array<Record<string, unknown>>;
  provider_runs?: Array<Record<string, unknown>>;
  agent_session_runs?: Array<Record<string, unknown>>;
  typing?: Array<Record<string, unknown>>;
  active_typing?: Array<Record<string, unknown>>;
  attachments?: Array<Record<string, unknown>>;
  artifacts?: Array<Record<string, unknown>>;
  running_replies?: Array<Record<string, unknown>>;
  queued_replies?: Array<Record<string, unknown>>;
  context_packs?: Array<Record<string, unknown>>;
  handoffs?: Array<Record<string, unknown>>;
  state_updates?: Array<Record<string, unknown>>;
  owner_reports?: Array<Record<string, unknown>>;
  automation_reports?: Array<Record<string, unknown>>;
  discussion?: Record<string, unknown>;
  pending_reply_count?: number;
}

export interface ExecutionPatternProjection {
  schema_version: string;
  source: string;
  generated_at?: string;
  patterns: Array<Record<string, unknown>>;
  active_runs?: Array<Record<string, unknown>>;
  counts?: Record<string, number>;
}

export interface RunSummary {
  run_id: string;
  trace_id?: string;
  test_task_id?: string;
  scenario_id?: string;
  target_project_id?: string;
  target_config?: string;
  status?: string;
  health?: string;
  live_state_dir?: string;
  artifact_dir?: string;
  artifact_manifest?: string;
  started_at?: string;
  heartbeat_at?: string;
  ended_at?: string;
  archived_at?: string;
  exit_code?: number | null;
  validation_status?: string;
  summary?: Record<string, unknown>;
}

export interface RoleSummary {
  instance_id: string;
  name: string;
  role_kind: string;
  backend: string;
  model: string;
  transport?: string;
  skills: string[];
  state: string;
  active_task: string;
  session_id: string;
  session_path: string;
  spawned_at: string;
  last_heartbeat: string;
  cost: CostRole;
}

export interface AgentSummary {
  instance_id: string;
  parent_role?: string;
  origin?: string;
  role_type: string;
  role_kind: string;
  agent_kind: string;
  layer: string;
  control_scope: string;
  backend: string;
  model: string;
  transport?: string;
  skills: string[];
  plugins?: string[];
  agent?: string;
  runtime_state: string;
  state: string;
  lifecycle_state?: string;
  attention_state?: string;
  /** Project runtime stopped/archived: states are last-known, not current. */
  stale?: boolean;
  project_runtime_state?: string;
  active_task: string;
  task_id?: string;
  session_id: string;
  session_path: string;
  spawned_at: string;
  last_heartbeat: string;
  cost: CostRole;
  workdir: string;
  project_path: string;
  cwd?: string;
  worktree_path?: string;
  branch_or_ref: string;
  branch: string;
  commit: string;
  dirty: boolean;
  workdir_mode: string;
  last_event_seq?: number;
  last_event_type?: string;
  last_output_summary?: string;
  needs_input_reason?: string;
  provider_stop_reason?: string;
  context_usage_ratio?: number | null;
  allowed_actions?: string[];
  capabilities?: string[];
  forbidden?: string[];
  debug?: {
    transport: string;
    log_path: string;
    briefing_paths: string[];
    attach_hint: string;
    tmux_session: string;
    tmux_target: string;
    state_inference: string;
  };
}

export interface AgentViewProjection {
  mode: string;
  generated_at: string;
  state_dir: string;
  role_groups: Array<{
    role: string;
    count: number;
    static_count: number;
    autoscale_count: number;
    runtime_count: number;
    attention_count: number;
    worker_ids: string[];
  }>;
  attention: Array<{
    instance_id: string;
    parent_role: string;
    attention_state: string;
    lifecycle_state: string;
    task_id: string;
    reason: string;
    last_event_type: string;
    last_event_seq: number;
  }>;
  workers: AgentSummary[];
  selected_instance_id: string;
  write_boundary: Record<string, string>;
}

export interface WorkdirSummary {
  instance_id: string;
  role_name: string;
  role_kind: string;
  backend: string;
  workdir: string;
  project_path: string;
  branch_or_ref: string;
  branch: string;
  commit: string;
  exists: boolean;
  project_exists: boolean;
  mode: string;
  enabled: boolean;
  dirty: boolean;
  status_lines: string[];
  owner: Record<string, unknown>;
  active_task: string;
  error: string;
}

export interface SkillsSummary {
  pool_path: string;
  materialize: string;
  lock_file: string;
  pool: Array<{
    name: string;
    path: string;
    description: string;
    sha256: string;
    enabled_by: string[];
    warnings: string[];
  }>;
  enabled: Array<{
    role: string;
    role_name: string;
    backend: string;
    skills: string[];
  }>;
  loaded: Array<Record<string, unknown>>;
  lock: Array<Record<string, unknown>>;
  manifests: Array<Record<string, unknown>>;
  warnings: Array<Record<string, unknown>>;
}

export interface StageSummary {
  state: string;
  latest: EventRecord | null;
  event_count: number;
}

export interface ExecutionRouteSummary {
  schema_version: string;
  summary: string;
  status: string;
  current_stage: string;
  current_stage_label: string;
  step_count: number;
  parallel: boolean;
  empty: boolean;
}

export interface ExecutionRouteNode {
  id: string;
  stage: string;
  stage_label: string;
  actor: string;
  role: string;
  status: string;
  first_seq: number;
  last_seq: number;
  first_ts: string;
  last_ts: string;
  event_count: number;
  event_types: string[];
  task_ids: string[];
  evidence_event_ids: string[];
  failed_count: number;
}

export interface ExecutionRouteStage {
  stage: string;
  label: string;
  status: string;
  parallel: boolean;
  actors: string[];
  node_ids: string[];
  first_seq: number;
  last_seq: number;
  first_ts: string;
  last_ts: string;
  event_count: number;
  event_types: string[];
  task_ids: string[];
  failed_count: number;
}

export interface ExecutionRouteProjection extends ExecutionRouteSummary {
  scope: {
    task_id: string;
    trace_id: string;
  };
  linear: ExecutionRouteStage[];
  dag: {
    nodes: ExecutionRouteNode[];
    edges: Array<{ from: string; to: string; kind: string }>;
  };
  swimlanes: Array<{
    actor: string;
    items: Array<Record<string, unknown>>;
  }>;
  source_event_count: number;
  source_events: Array<Record<string, unknown>>;
}

export interface TaskRunPanelProjection {
  schema_version: string;
  generated_at?: string;
  task_id: string;
  status: string;
  current_stage: string;
  current_stage_label?: string;
  active_operation?: Record<string, unknown> | null;
  latest_progress?: Record<string, unknown>;
  route_summary?: ExecutionRouteSummary | Record<string, unknown>;
  workdir?: WorkdirSummary | Record<string, unknown>;
  role_instance?: string;
  health?: Record<string, unknown>;
  counts?: Record<string, unknown>;
  source_event_ids?: string[];
  empty?: boolean;
  error?: string;
}

export interface HandoffSummaryProjection {
  schema_version: string;
  generated_at?: string;
  task_id: string;
  objective: string;
  current_state: string;
  current_stage: string;
  owner?: Record<string, unknown>;
  completed?: Array<Record<string, unknown>>;
  missing_evidence?: Array<Record<string, unknown>>;
  blockers?: string[];
  next_required_event?: string;
  next_required_action?: string;
  do_not_repeat?: string[];
  evidence_refs?: Array<Record<string, unknown>>;
  changed_files?: string[];
  resume_packet_ref?: string;
  source_event_ids?: string[];
  empty?: boolean;
  error?: string;
}

export interface TaskDetail {
  task: Task & Record<string, unknown>;
  contract: Record<string, unknown>;
  evidence: Record<string, unknown>;
  artifact_refs?: Record<string, unknown>;
  status_model?: Record<string, unknown>;
  evidence_model?: Record<string, unknown>;
  runs?: Array<Record<string, unknown>>;
  progress_projection?: Record<string, unknown>;
  operations?: Record<string, unknown>;
  execution_route?: ExecutionRouteProjection;
  task_run_panel?: TaskRunPanelProjection;
  handoff_summary?: HandoffSummaryProjection;
  events: EventRecord[];
  trace_id: string | null;
  correlation_id: string | null;
  links: {
    trace: string;
    candidate: string;
    fanout: string;
  };
  role_instance: string;
  workdir: WorkdirSummary | Record<string, never>;
  briefing: {
    path: string;
    text: string;
    truncated: boolean;
  };
  git: Record<string, unknown>;
  verify: StageSummary;
  review: StageSummary;
  diagnostics: DiagnosticsDetail;
}

export interface TaskTimeline {
  schema_version: string;
  task_id: string;
  event_count: number;
  timeline: EventRecord[];
  trace_id: string | null;
  correlation_id: string | null;
  links: {
    trace: string;
  };
  execution_route?: ExecutionRouteProjection;
  query?: Record<string, unknown>;
  empty: boolean;
}

export interface TaskDiff {
  task_id: string;
  base: string;
  head: string;
  range?: string;
  cwd?: string;
  files: string[];
  diff: string;
  truncated: boolean;
  error: string;
}

export interface TraceDetail {
  trace_id: string;
  event_count: number;
  timeline: EventRecord[];
  tasks: string[];
  actors: string[];
  git_refs: Record<string, unknown>;
  diagnostics: DiagnosticsDetail;
  execution_route?: ExecutionRouteProjection;
  empty: boolean;
}

export interface CandidateDetail {
  pdd_id: string;
  candidate_ref: string;
  base_main: string;
  task_refs: string[];
  tasks: string[];
  verify: StageSummary;
  review: StageSummary;
  ship_ready: boolean;
  blockers: string[];
  timeline: EventRecord[];
  empty: boolean;
}

export interface FanoutDetail {
  fanout_id: string;
  trace_id?: string;
  pdd_id?: string;
  topology: string;
  stage_id?: string;
  target_ref?: string;
  status?: string;
  progress?: {
    done: number;
    total: number;
    failed: number;
    pending: number;
    percent: number;
    active_total?: number;
    planned_total?: number;
    lane_scope?: string;
  };
  lane_projection?: FanoutLaneProjection;
  trigger?: Record<string, unknown>;
  aggregate_role: string;
  wait_policy: string;
  children: Array<Record<string, unknown>>;
  aggregate?: Record<string, unknown>;
  aggregate_config?: Record<string, unknown>;
  synth?: Record<string, unknown>;
  manifest?: Record<string, unknown>;
  timeline: EventRecord[];
  empty: boolean;
}

export interface RunDetail {
  run_id: string;
  run: Record<string, unknown>;
  manifest: Record<string, unknown>;
  artifact_dir: string;
  artifacts: Array<Record<string, unknown>>;
  empty?: boolean;
}

export interface DiagnosticsDetail {
  trace_id: string;
  path?: string;
  items: Array<{
    stream: string;
    index: number;
    payload: Record<string, unknown>;
  }>;
  empty: boolean;
}

export interface EventsPage {
  items: EventRecord[];
  next_cursor: number | null;
  current_seq: number;
  limit: number;
}

export interface AgentSessionHistoryPage {
  schema_version: string;
  surface: "kanban_agent" | "channel_group" | string;
  thread_id: string;
  items: EventRecord[];
  limit: number;
  next_before_seq: number | null;
  has_more: boolean;
  current_seq: number;
  projection_state?: string;
  projection_lag?: number | null;
  source?: string;
}

export interface AgentSessionRawOutput {
  schema_version: string;
  raw_ref: string;
  content: string;
  offset: number;
  limit: number;
  byte_count: number;
  line_count: number;
  sha256: string;
  mime: string;
  encoding: string;
  truncated: boolean;
  next_offset: number | null;
  metadata?: Record<string, unknown>;
}

export interface ChannelsPage {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  seq?: number;
  channels: ChannelSummary[];
}

export interface WorkspaceProject {
  project_id: string;
  aliases?: string[];
  name: string;
  root: string;
  config_path: string;
  state_dir_hint: string;
  state_dir_resolved?: string;
  can_open_board?: boolean;
  lifecycle?: {
    has_config: boolean;
    config_loadable: boolean;
    state_dir_exists: boolean;
    initialized: boolean;
    can_open_board: boolean;
    runtime_state: string;
    reason?: string;
    state_dir_resolved?: string;
    missing_truth_files?: string[];
  };
  last_opened_at?: string;
}

export interface WorkspaceProjectsPage {
  schema_version?: string;
  active_project_id: string;
  server_default_project_id?: string;
  active_project_is_server_default?: boolean;
  items: WorkspaceProject[];
  projects: WorkspaceProject[];
  warning?: string;
}

export interface ChannelDetail extends ChannelSummary {
  empty?: boolean;
  schema_version?: string;
  generated_at?: string;
  seq?: number;
  source?: string;
  created_at?: string;
  created_by?: string;
  members: Array<Record<string, unknown>>;
  messages?: Array<Record<string, unknown>>;
  recent_messages?: EventRecord[];
  syntheses?: Array<Record<string, unknown>>;
  synthesis_requests?: Array<Record<string, unknown>>;
  workflow_requests: Array<Record<string, unknown>>;
  mentions_detected?: Array<Record<string, unknown>>;
  routes?: Array<Record<string, unknown>>;
  discussions?: Record<string, Record<string, unknown>>;
  open_questions?: Record<string, Record<string, unknown>>;
  reply_requests?: Array<Record<string, unknown>>;
  provider_runs?: Array<Record<string, unknown>>;
  agent_session_runs?: Array<Record<string, unknown>>;
  typing?: Array<Record<string, unknown>>;
  active_typing?: Array<Record<string, unknown>>;
  attachments?: Array<Record<string, unknown>>;
  artifacts?: Array<Record<string, unknown>>;
  running_replies?: Array<Record<string, unknown>>;
  queued_replies?: Array<Record<string, unknown>>;
  context_packs?: Array<Record<string, unknown>>;
  handoffs?: Array<Record<string, unknown>>;
  state_updates?: Array<Record<string, unknown>>;
  owner_reports?: Array<Record<string, unknown>>;
  automation_reports?: Array<Record<string, unknown>>;
  discussion?: Record<string, unknown>;
  pending_reply_count?: number;
  linked_events?: EventRecord[];
  history_cleared_at?: string;
  history_clear_event_id?: string;
  history_clear_reason?: string;
}

export interface ChannelHistorySearchResult {
  schema_version: string;
  generated_at?: string;
  channel_id: string;
  query: string;
  filters: Record<string, string>;
  history_index?: Record<string, Array<Record<string, unknown>>>;
  result_count?: number;
  results: Array<Record<string, unknown>>;
  items?: Array<Record<string, unknown>>;
}

export interface SearchResult {
  query: string;
  filters: Record<string, string>;
  terms: string[];
  tasks: Task[];
  events: EventRecord[];
  traces: TraceSummary[];
}

// Kernel 12-metric snapshot (MetricsCollector passthrough; fields may grow)
export interface MetricsSnapshotProjection {
  mtts?: number;
  stuck_recovery_rate?: number;
  crash_free_hours?: number;
  resume_fidelity?: number;
  vcr?: number;
  scope_violation_rate?: number;
  discriminator_catch_rate?: number;
  goal_drift?: number;
  throughput_per_hour?: number;
  rework_ratio?: number;
  avg_task_duration_minutes?: number;
  cost_per_task?: number;
  token_per_task?: number;
  budget_breach_rate?: number;
  tasks_done?: number;
  window_hours?: number;
  [key: string]: unknown;
}

export interface TaskFlowStats {
  schema_version: string;
  done_24h: number;
  done_7d: number[];
  throughput_per_hour_24h: number;
  oldest_in_progress_seconds: number | null;
  oldest_blocked_seconds: number | null;
}

export interface RoleEfficiencyRow {
  role: string;
  done: number;
  avg_duration_minutes: number | null;
  rework: number;
  respawn: number;
}

export interface FleetStats {
  task_flow?: TaskFlowStats;
  role_efficiency?: RoleEfficiencyRow[];
}

export interface Snapshot {
  seq: number;
  generated_at: string;
  project: {
    project_id?: string;
    name?: string;
    root: string;
    state_dir: string;
  };
  tasks: Task[];
  archive_tasks: Task[];
  features: Feature[];
  delivery_features?: Feature[];
  metrics_snapshot?: MetricsSnapshotProjection;
  fleet_stats?: FleetStats;
  traces: TraceSummary[];
  fanouts: FanoutSummary[];
  channels?: ChannelSummary[];
  automations?: Record<string, unknown>;
  agent_live?: Record<string, unknown>;
  assignment_routes?: Record<string, unknown>;
  agent_cockpit?: Record<string, unknown>;
  recovery?: Record<string, unknown>;
  pause_lifecycle?: Record<string, unknown>;
  provider_capabilities?: Record<string, unknown>;
  spine_review?: Record<string, unknown>;
  mutation_audit?: Record<string, unknown>;
  worktree_drift?: Record<string, unknown>;
  execution_patterns?: ExecutionPatternProjection;
  candidates: CandidateSummary[];
  runs: RunSummary[];
  active_runs: RunSummary[];
  agents?: AgentSummary[];
  agent_view?: AgentViewProjection;
  roles: RoleSummary[];
  workdirs: WorkdirSummary[];
  skills: SkillsSummary;
  cost: CostSummary;
  workers: Worker[];
  runtime: RuntimeSummary;
}

export type RecentEvent = EventRecord;

export interface OperatorSession {
  session_id: string;
  backend: string;
  status: string;
  delivery?: string;
  profile?: string;
  scope?: string;
  task_id?: string;
  context_task_id?: string;
  started_at?: string;
  stopped_at?: string;
  ended_at?: string;
  alive?: boolean;
  output_seq?: number;
  state_dir?: string;
  shared_project_workdir?: string;
  operator_workdir?: string;
  shared_context?: KanbanAgentSharedContext;
  skills_available?: KanbanAgentSkillsSummary;
  allowed_actions?: string[];
  forbidden_capabilities?: string[];
  boundary?: Record<string, unknown>;
  status_model?: Record<string, unknown>;
  evidence_model?: Record<string, unknown>;
  transcript_path?: string;
  workdir?: string;
  reason?: string;
  exit_code?: number | null;
}

export interface OperatorOutputChunk {
  seq: number;
  ts: string;
  stream: string;
  text: string;
}

export interface OperatorOutputPage {
  session: OperatorSession;
  cursor: number;
  next_cursor: number;
  chunks: OperatorOutputChunk[];
}

export interface OperatorInboxItem {
  id: string;
  kind: string;
  status: string;
  title: string;
  summary?: string;
  created_event_id?: string;
  created_ts?: string;
  resolved_event_id?: string;
  resolved_ts?: string;
  approval_ref?: string;
  plan_id?: string;
  stage_id?: string;
  trace_id?: string;
  pdd_id?: string;
  task_count?: number | null;
  refs?: Record<string, string>;
  preview?: {
    available?: boolean;
    api_path?: string;
    fullscreen?: boolean;
    scroll?: boolean;
  };
  actions?: Array<Record<string, unknown>>;
  reject_reason?: string;
  decision_token?: string;
  checkpoint_id?: string;
  fingerprint?: string;
  attention_id?: string;
  category?: "action_required" | "automation_diagnostic" | "runtime_attention" | "notification" | "resolved" | string;
  actionability?: "human_required" | "automation_owned" | "informational" | "resolved" | string;
  source_role?: string;
  source_actor?: string;
  owner_route?: string;
  group_key?: string;
  dedupe_count?: number;
  first_seen_at?: string;
  last_seen_at?: string;
  latest_event_id?: string;
  policy?: Record<string, unknown>;
}

export interface OperatorInboxProjection {
  schema_version: string;
  is_derived_projection: boolean;
  summary: {
    total: number;
    pending: number;
    action_required_pending?: number;
    noise_pending?: number;
    plan_approvals: number;
    attention: number;
    human_decisions?: number;
    suppressed_acknowledged?: number;
  };
  items: OperatorInboxItem[];
  pending: OperatorInboxItem[];
  views?: Record<string, { count: number; ids: string[] }>;
  policy: Record<string, unknown>;
}

export interface PlanPreview {
  schema_version: string;
  ok: boolean;
  plan_id: string;
  status: string;
  requested_event_id?: string;
  requested_ts?: string;
  resolved_event_id?: string;
  resolved_ts?: string;
  reject_reason?: string;
  stage_id?: string;
  trace_id?: string;
  pdd_id?: string;
  task_count?: number | null;
  refs: Record<string, string>;
  markdown: string;
  task_map_summary?: {
    ok: boolean;
    task_count: number;
    tasks: Array<Record<string, string>>;
    reason?: string;
    truncated?: boolean;
  };
  actions?: Record<string, string>;
  policy?: Record<string, unknown>;
}

export interface OperatorInputResponse {
  ok: boolean;
  status: string;
  reason: string;
  bytes?: number;
  event_id?: string;
  session?: OperatorSession;
}

export interface ActionResponse {
  ok: boolean;
  status: string;
  action: string;
  requested_action?: string;
  reason: string;
  event_id?: string;
  trace_id?: string;
  fanout_id?: string;
  channel_id?: string;
  task_id?: string;
  automation_id?: string;
  run_id?: string;
  project_id?: string;
  started_event_id?: string;
  proposal_count?: number;
  outputs?: Array<Record<string, unknown>>;
  reply_event_id?: string;
  idempotency?: Record<string, unknown>;
  result?: Record<string, unknown>;
  reply?: Record<string, unknown>;
  route?: Record<string, unknown>;
  blockers?: string[];
}

// overview-pulse.v1 — RUN PULSE / TASK FLOW bands (read-only derived
// projection). Every field is optional/nullable: no-data must stay
// distinguishable from 0 at the rendering layer.
export interface RunPulseLoop {
  status?: string | null;
  age_seconds?: number | null;
}

export interface RunPulseSessions {
  active?: number | null;
  total?: number | null;
  stale?: number | null;
  by_state?: Record<string, number>;
  by_backend?: Record<string, number>;
}

export interface RunPulse {
  last_event_age_seconds?: number | null;
  events_per_bucket?: number[];
  bucket_seconds?: number | null;
  respawn_failed_streak?: number | null;
  respawn_cooldown_instances?: string[];
  loop?: RunPulseLoop | null;
  sessions?: RunPulseSessions | null;
}

export interface TaskFlowBlockedPocketItem {
  task_id?: string;
  reason?: string;
  age_seconds?: number | null;
}

export interface TaskFlowPulse {
  columns?: Partial<Record<"todo" | "in_progress" | "verify" | "blocked" | "done" | "other", number | null>>;
  oldest_age_seconds?: Partial<Record<"todo" | "in_progress" | "verify" | "blocked", number | null>>;
  transitions_per_hour?: {
    todo_to_in_progress?: number | null;
    in_progress_to_verify?: number | null;
    verify_to_done?: number | null;
  };
  window_hours?: number | null;
  wip?: { used?: number | null; capacity?: number | null } | null;
  rework_backedge_per_hour?: number | null;
  blocked_side_pocket?: TaskFlowBlockedPocketItem[];
  done_gate?: string | null;
}

export interface OverviewPulseAttention {
  unacked_escalations?: number | null;
  oldest_unacked_escalation_seconds?: number | null;
  remediation_open_by_tier?: Record<string, number>;
  sm_stuck_observed?: number | null;
  safe_halt_active?: boolean | null;
}

export interface OverviewWhyNotNotification {
  kind?: string;
  severity?: string;
  reason?: string;
  [key: string]: unknown;
}

export interface OverviewWhyNot {
  summary?: string | null;
  notifications?: OverviewWhyNotNotification[];
}

export interface OverviewPulse {
  schema_version?: string;
  is_derived_projection?: boolean;
  generated_at?: string;
  run_pulse?: RunPulse | null;
  task_flow?: TaskFlowPulse | null;
  attention?: OverviewPulseAttention | null;
  why_not?: OverviewWhyNot | null;
}

// loop-view.v1 (Loop page v2 single data source; see stage_loop_projection.py)
export interface LoopViewPromiseItem {
  event: string;
  satisfied: boolean;
  seq?: number;
  ts?: string;
}

export interface LoopViewAttempt {
  started_ts: string;
  role: string;
  terminal: { type: string; ts: string; reason?: string; seq?: number } | null;
  open: boolean;
  counted: boolean;
  orphan?: boolean;
}

export interface LoopViewTask {
  id: string;
  stage_id?: string;
  attempts: LoopViewAttempt[];
  fails: number;
  counted: number;
  source: string;
}

export interface LoopViewLoop {
  id: string;
  label: string;
  shape: string[];
  closure_edge: [string, string] | string[];
  counts: Record<string, number>;
  arc: { state: "flow" | "active" | "broken"; label: string };
  health: string;
  members?: Array<{ kind: string; id: string; note: string }>;
  node_stats?: Record<string, Record<string, number>>;
  acct?: { open: number; recovered: number; exhausted: number };
}

export interface LoopViewProjection {
  schema_version: string;
  generated_at: string;
  project_id: string;
  run: {
    event_count: number;
    semantic_event_count: number;
    first_ts: string;
    last_ts: string;
    latched: boolean;
    promise: {
      source: string;
      chain: LoopViewPromiseItem[];
      satisfied: number;
      latched: boolean;
    };
  };
  stages: Array<{
    id: string;
    rounds: number;
    last_status: string;
    last_ts: string;
    warn: boolean;
  }>;
  tasks: LoopViewTask[];
  backflows: Array<{ from_stage: string; to_stage: string; kind: string; count: number }>;
  subscriber_chains: Array<{ topic: string; seq: number; subscriber: string; result: string; result_seq: number }>;
  loops: Record<string, LoopViewLoop>;
  faults: Array<{ kind: string; count: number; owner_loop: string }>;
  companions: Record<string, Record<string, number>>;
  pump: { total: number; lag_warnings: number };
  health_counters: Record<string, number>;
  source_projection_refs: string[];
}

// First-run welcome onboarding (see core/workspace/onboarding.py)
export interface OnboardingBackend {
  id: string;
  detected: boolean;
  path: string;
  note: string;
  always_available: boolean;
}

export interface OnboardingStatus {
  schema_version: string;
  show_welcome: boolean;
  completed: boolean;
  skipped: boolean;
  step: number;
  backend: string;
  notifications: string;
  backends: OnboardingBackend[];
  preflight: Array<{ name: string; ok: boolean; detail: string }>;
}
