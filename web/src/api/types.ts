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

export interface TaskAttention {
  required: boolean;
  severity: "none" | "warning" | "error" | string;
  code: string;
  label: string;
  since: string;
  source_ref: string;
}

export interface TaskCardProjection {
  schema_version: "task-card.v1" | string;
  task_id: string;
  title: string;
  lifecycle: {
    canonical_status: string;
    display_status: string;
    effective_terminal: boolean;
    outcome: "success" | "cancelled" | "superseded" | "archived" | "" | string;
    reconciled: boolean;
    canonical_drift: boolean;
  };
  owner: { actor_id: string; role: string };
  current_stage: string | null;
  progress: Record<string, unknown> | null;
  attention: TaskAttention;
  activity: { kind: string; at: string };
}

export interface Task {
  id: string;
  title: string;
  status: string;
  display_status?: string;
  projection_reconciled?: boolean;
  canonical_drift?: boolean;
  effective_terminal?: boolean;
  attention?: TaskAttention;
  task_card?: TaskCardProjection;
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
  terminal_outcome?: "success" | "cancelled" | "superseded" | "archived" | "";
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
    workflow_run_id?: string;
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

export interface WorkflowRequestsPage {
  ok: boolean;
  count: number;
  items: Array<Record<string, unknown>>;
}

export interface WorkflowRequestDetail {
  ok: boolean;
  status: string;
  result: Record<string, unknown>;
  requirement: Record<string, unknown>;
  proposal: Record<string, unknown>;
  artifacts: Record<string, unknown>;
  lifecycle: Record<string, unknown>;
  operation: Record<string, unknown>;
  links: Record<string, unknown>;
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
  task_id_opaque?: boolean;
  goal_claim_ids?: string[];
  goal_claim_ids_total?: number;
  goal_claim_ids_included?: number;
  goal_claim_ids_omitted?: number;
  goal_claim_ids_truncated?: boolean;
  title: string;
  planned: {
    owner_role?: string;
    owner_instance?: string;
    wave?: number;
    blocked_by?: string[];
    blocked_by_total?: number;
    blocked_by_included?: number;
    blocked_by_omitted?: number;
    blocked_by_truncated?: boolean;
    scope?: string[];
  };
  actual: {
    status: string;
    assigned_to: string;
    evidence_events: string[];
    evidence_events_total?: number;
    evidence_events_included?: number;
    evidence_events_omitted?: number;
    evidence_events_truncated?: boolean;
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

export type GoalCoverageNodeKind =
  | "goal"
  | "goal_claim"
  | "task"
  | "verification_result"
  | "goal_closure"
  | "gap";

export interface GoalCoverageNode {
  node_id: string;
  kind: GoalCoverageNodeKind;
  title: string;
  status?: string;
  goal_id?: string;
  goal_id_opaque?: boolean;
  goal_claim_id?: string;
  task_id?: string;
  task_id_opaque?: boolean;
  owner?: string;
  mandatory?: boolean;
  source_ref?: string;
  result_ref?: string;
  gap_ref?: string;
  plan_coverage?: "covered" | "uncovered";
  execution?: "pending" | "running" | "done" | "failed" | "blocked";
  task_verification?: "unverified" | "passed" | "rejected" | "blocked" | "stale";
  closure?: "unknown" | "open" | "closed" | "waived" | "blocked";
  task_ids?: string[];
  task_ids_total?: number;
  task_ids_included?: number;
  task_ids_omitted?: number;
  task_ids_truncated?: boolean;
  task_details?: {
    total: number;
    included: number;
    missing_count: number;
    task_ids_returned?: number;
    task_ids_total?: number;
    task_ids_included?: number;
    task_ids_omitted?: number;
    task_ids_truncated?: boolean;
    missing_task_ids?: string[];
    missing_task_ids_total?: number;
    missing_task_ids_included?: number;
    missing_task_ids_omitted?: number;
    missing_task_ids_truncated?: boolean;
  };
  goal_claim_ids?: string[];
  goal_claim_ids_total?: number;
  goal_claim_ids_included?: number;
  goal_claim_ids_omitted?: number;
  goal_claim_ids_truncated?: boolean;
  supporting_result_refs?: string[];
  supporting_result_refs_total?: number;
  supporting_result_refs_included?: number;
  supporting_result_refs_omitted?: number;
  supporting_result_refs_truncated?: boolean;
  evidence_refs?: string[];
  evidence_refs_total?: number;
  evidence_refs_included?: number;
  evidence_refs_omitted?: number;
  evidence_refs_truncated?: boolean;
  gap_refs?: string[];
  gap_refs_total?: number;
  gap_refs_included?: number;
  gap_refs_omitted?: number;
  gap_refs_truncated?: boolean;
  current?: boolean;
  stale_reasons?: string[];
  stale_reasons_total?: number;
  stale_reasons_included?: number;
  stale_reasons_omitted?: number;
  stale_reasons_truncated?: boolean;
  contract_revision?: string;
}

export interface GoalCoverageGraph {
  schema_version: "goal-coverage-graph.v1" | string;
  coverage_mode: "explicit" | "legacy_derived" | "unmapped" | string;
  identity: {
    project_id?: string;
    workflow_run_id?: string;
    goal_id?: string;
    task_map_generation?: string;
    task_map_ref?: string;
    task_map_digest?: string;
    goal_claim_set_ref?: string;
    goal_claim_set_digest?: string;
    target_commit?: string;
  };
  currentness: {
    is_current_generation: boolean;
    superseded_by?: string;
    stale_reasons?: string[];
  };
  summary: {
    mandatory_claims: number;
    planned_claims: number;
    claims_with_current_results: number;
    closed_claims: number;
    open_gaps: number;
  };
  nodes: GoalCoverageNode[];
  edges: Array<{ from: string; to: string; kind: string }>;
  nodes_truncated?: boolean;
  edges_truncated?: boolean;
  diagnostics_truncated?: boolean;
  diagnostics: Array<{
    code?: string;
    kind?: string;
    message?: string;
    goal_claim_id?: string;
    task_id?: string;
    result_ref?: string;
    stale_reasons?: string[];
  }>;
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

// delivery-view.v2 only exposes refs that the server resolved to a real Trace
// projection. Delivery must never derive these from its synthetic trace_id.
export interface DeliveryCanonicalTraceRef {
  trace_id: string;
  task_id?: string;
  task_ids?: string[];
  run_group_id?: string;
  span_id?: string;
  source_event_ids?: string[];
  source_event_ids_truncated?: boolean;
  event_count?: number;
  trace_id_opaque?: boolean;
  membership?: "trace-v2-source-event" | string;
  task_ids_truncated?: boolean;
  last_seq?: number;
}

export interface DeliveryRefreshScopeProjection {
  task_ids: string[];
  task_ids_total: number;
  task_ids_included: number;
  task_ids_omitted: number;
  task_ids_truncated: boolean;
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

export interface ProviderTelemetryCapability {
  provider: string;
  route: string;
  requested: string;
  detected: string;
  effective: string;
  join_kind: string;
  w3c_inbound: boolean;
  signals: Record<string, boolean>;
  collector_delivery: string;
  failure_class?: string;
  observed_at?: string;
}

export interface OperationsObservability {
  schema_version: string;
  project_id: string;
  scope: { kind: string; note: string };
  owner_delivery?: { available: boolean; routes: Array<{ target: string; route: string; status: string; failure_count: number; reason: string; last_at: string }> };
  provider_telemetry: {
    requested_mode: string;
    updated_at: string;
    capabilities: ProviderTelemetryCapability[];
    bindings: Array<Record<string, unknown>>;
  };
  runtime_logs: {
    count: number;
    levels: Record<string, number>;
    latest_at: string;
  };
  otlp_exporter: {
    enabled: boolean;
    health: string;
    backlog_events: number;
    last_success_at: string;
    last_failure_at: string;
    last_failure_class: string;
    pending: {
      event_count: number;
      span_count: number;
      attempt: number;
      created_at: string;
    };
    counters: Record<string, number>;
  };
  alerts: {
    enabled: boolean;
    last_scan_at: string;
    emitted_total: number;
    suppressed_total: number;
    last_sse_gap_sequence: number;
    last_exporter_health: string;
  };
  metrics: {
    enabled: boolean;
    updated_at: string;
    counter_series: number;
    histogram_series: number;
  };
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
  via_event_id_omitted?: boolean;
  causation_id?: string | null;
  causation_id_omitted?: boolean;
  seq_first?: number | null;
  seq_last?: number | null;
  occurrences: number;
  task_ids: string[];
  task_ids_opaque?: number;
  task_ids_total?: number;
  task_ids_included?: number;
  task_ids_omitted?: number;
  task_ids_truncated?: boolean;
}

export interface DeliveryRunChain {
  schema_version: string;
  status: "completed" | "in_progress" | "not_started" | "no_stage_order" | string;
  trigger?: { event_id: string; type: string; ts?: string | null; actor?: string | null } | null;
  stages: DeliveryRunChainStage[];
  stage_count?: number;
  stages_total?: number;
  stages_included?: number;
  stages_omitted?: number;
  stages_truncated?: boolean;
  task_ids_total?: number;
  task_ids_included?: number;
  task_ids_omitted?: number;
  task_ids_truncated?: boolean;
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
  gate_results_total?: number;
  gate_results_included?: number;
  gate_results_omitted?: number;
  gate_results_truncated?: boolean;
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
  state_history_total?: number;
  state_history_included?: number;
  state_history_omitted?: number;
  state_history_truncated?: boolean;
  tries_total?: number;
  tries_included?: number;
  tries_omitted?: number;
  tries_truncated?: boolean;
  gate_results_total?: number;
  gate_results_included?: number;
  gate_results_omitted?: number;
  gate_results_truncated?: boolean;
}

export interface DeliveryTaskLifecycle {
  schema_version: string;
  tasks: Record<string, DeliveryTaskLifecycleEntry>;
  task_count?: number;
  tasks_included?: number;
  tasks_omitted?: number;
  tasks_truncated?: boolean;
  task_statuses?: Record<string, string>;
  task_status_count?: number;
  task_statuses_included?: number;
  task_statuses_omitted?: number;
  task_statuses_truncated?: boolean;
  state_history_total?: number;
  state_history_included?: number;
  state_history_truncated?: boolean;
  tries_total?: number;
  tries_included?: number;
  tries_truncated?: boolean;
  gate_results_total?: number;
  gate_results_included?: number;
  gate_results_truncated?: boolean;
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
  generated_at?: string;
  feature_id: string;
  trace_id: string;
  view?: "overview" | "runs" | "graph" | "work";
  work_scope?: {
    goal_id: string;
    goal_id_opaque?: boolean;
    matched: boolean;
    claim_count: number;
    task_count: number;
  };
  refresh_scope?: DeliveryRefreshScopeProjection;
  canonical_trace_refs?: Array<string | DeliveryCanonicalTraceRef>;
  canonical_trace_refs_truncated?: boolean;
  run_summary?: {
    total?: number;
    completed?: number;
    running?: number;
    failed?: number;
    latest_label?: string;
  };
  attention_summary?: {
    total_count: number;
    truncated: boolean;
    by_kind: Array<{ kind: string; count: number }>;
    by_kind_truncated: boolean;
  };
  attention?: Array<{
    label: string;
    meta?: string;
    tone?: "ok" | "warn" | "err" | "info" | "muted";
    count?: number;
  }>;
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
    summary_only?: boolean;
    nodes_only?: boolean;
    nodes_total?: number;
    nodes_included?: number;
    nodes_omitted?: number;
    nodes_truncated?: boolean;
    edges_total?: number;
    edges_included?: number;
    edges_omitted?: number;
    edges_truncated?: boolean;
  };
  drift_report: { status: string; summary: Record<string, number>; items: DeliveryTraceDriftItem[] };
  drift?: { status: string; summary: Record<string, number>; items: DeliveryTraceDriftItem[] };
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
  goal_coverage_graph?: GoalCoverageGraph;
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
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
  estimated_usd?: number;
  provider_reported_usd?: number;
  billed_usd?: number;
  unpriced_entries?: number;
  partial_entries?: number;
  entries: number;
}

export interface CostSummary {
  total_usd: number;
  per_role: Record<string, CostRole>;
  precision?: {
    schema_version: string;
    display_total_usd: number;
    estimated_usd: number;
    provider_reported_usd: number;
    billed_usd: number;
    unpriced_entries: number;
    partial_entries: number;
    statuses: Record<string, number>;
    catalogs: Array<{ catalog_version: string; digest: string }>;
  };
  reconciliation?: {
    provider: string;
    accounting_mode: string;
    status: string;
    billed_usd?: string | null;
    estimated_usd?: string | null;
    variance_usd?: string | null;
    attribution_precision?: string;
  } | null;
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
    permission_profile?: string;
    permission_profiles?: string[];
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
  trace_id_opaque?: boolean;
  first_seq: number;
  last_seq: number;
  first_ts: string;
  last_ts: string;
  duration_seconds?: number | null;
  event_count: number;
  task_ids: string[];
  task_ids_truncated?: boolean;
  actors: string[];
  actors_truncated?: boolean;
  backends?: string[];
  backends_truncated?: boolean;
  status?: string;
  source?: "event_trace" | "task_event_fallback" | string;
  source_event_ids?: string[];
  inferred_ids?: string[];
  last_type: string;
}

export interface TraceIndexPage {
  schema_version: "trace-list.v2" | string;
  items: TraceSummary[];
  limit: number;
  has_more: boolean;
  next_cursor: string | null;
  as_of_seq: number;
  is_derived_projection: boolean;
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

export interface TaskPipelineTaskProjection {
  task_id: string;
  title: string;
  task_status: string;
  pipeline_stage: string;
  workflow_run_id: string;
  task_map_generation: string;
  generation_id: string;
  blocked_by: string[];
  blockers: string[];
  current_operation_id: string;
  current_stage: string;
  current_worker: string;
  current_worker_source: string;
  active_operation_count: number;
  active_attempt_count: number;
  active_session_count: number;
}

export interface TaskPipelineOperationProjection {
  operation_id: string;
  workflow_run_id: string;
  task_id: string;
  stage: string;
  operation_generation: number;
  task_map_generation: string;
  workspace_generation: number;
  status: string;
  semantic_verdict: string;
  role_instance: string;
  current_worker: string;
  current_worker_source: string;
  placement_epoch: number;
  session_binding_key: string;
  active_attempt_id: string;
  attempt_ids: string[];
  reason: string;
}

export interface TaskPipelineSessionProjection {
  binding_key: string;
  workflow_run_id: string;
  task_id: string;
  stage: string;
  rework_affinity_id: string;
  session_id: string;
  status: string;
  current_role_instance: string;
  current_placement_epoch: number;
  workspace_generation: number;
  placement_history: Array<Record<string, unknown>>;
  sealed_at: string;
  archived_at: string;
}

export interface TaskPipelineProjection {
  schema_version: string;
  is_derived_projection: boolean;
  enabled: boolean;
  mode: string;
  profile_id: string;
  profile_digest: string;
  summary: {
    generation_count: number;
    task_count: number;
    task_statuses: Record<string, number>;
    operation_count: number;
    operation_statuses: Record<string, number>;
    active_worker_count: number;
    session_statuses: Record<string, number>;
    terminal_convergence: string;
  };
  generations: Array<Record<string, unknown>>;
  tasks: TaskPipelineTaskProjection[];
  operations: TaskPipelineOperationProjection[];
  attempts: Array<Record<string, unknown>>;
  sessions: TaskPipelineSessionProjection[];
  queues: Record<string, string[]>;
  occupancy: Record<string, unknown>;
  capacity: Record<string, unknown>;
  backpressure: Record<string, unknown>;
  dispatchable: Record<string, Array<Record<string, string>>>;
  closure: {
    status: string;
    terminal_expected: boolean;
    converged: boolean;
    active_operation_ids: string[];
    active_attempt_ids: string[];
    active_session_binding_keys: string[];
    missing_candidate_freeze_generation_ids: string[];
    residuals: string[];
  };
  projection_digest: string;
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

export type ChannelDiscussionAttentionState =
  | "running"
  | "needs_input"
  | "ready"
  | "blocked"
  | "done";

export interface ChannelDiscussionActiveAgent {
  member_id: string;
  request_id: string;
  status: string;
  started_at: string;
}

export interface ChannelDiscussionAttention {
  next_round_rejection_reason?: string;
  missing_frozen_members?: string[];
  deferred_question_count?: number;
  schema_version: "channel.discussion-attention.v1" | string;
  is_derived_projection: boolean;
  thread_id: string;
  state: ChannelDiscussionAttentionState;
  reason: string;
  next_action: string;
  execution_state?: "running" | "ready" | "blocked" | "done";
  attention_kind?: "none" | "question" | "review" | "consolidating";
  blocking_scope?: "none" | "phase" | "workflow";
  blocks_transition?: string;
  kernel_phase: string;
  last_outcome: string;
  participant_count: number;
  active_agent_count: number;
  active_agents?: ChannelDiscussionActiveAgent[];
  oldest_active_started_at?: string;
  active_reply_count: number;
  queued_reply_count: number;
  running_reply_count: number;
  completed_reply_count: number;
  failed_reply_count: number;
  context_rejections?: Array<{ context_pack_id: string; target_member_id: string; reason: string }>;
  open_question_count: number;
  owner_question_count: number;
  total_question_count: number;
  resolved_question_count: number;
  question_consolidation_status?: "open" | "consolidating" | "blocked";
  last_activity_at: string;
  can_drain_replies: boolean;
  can_synthesize: boolean;
  can_restart: boolean;
  can_review_questions: boolean;
  can_review_result: boolean;
  can_view_activity: boolean;
}

export interface ChannelSummary {
  channel_id: string;
  name?: string;
  status?: string;
  type?: string;
  scope?: Record<string, unknown>;
  members?: Array<Record<string, unknown>>;
  messages?: Array<Record<string, unknown>>;
  message_count?: number;
  threads?: Record<string, unknown>;
  read_state?: Record<string, unknown>;
  attention?: Array<Record<string, unknown>>;
  syntheses?: Array<Record<string, unknown>>;
  synthesis_requests?: Array<Record<string, unknown>>;
  cross_reviews?: Array<Record<string, unknown>>;
  workflow_requests?: Array<Record<string, unknown>>;
  mentions_detected?: Array<Record<string, unknown>>;
  routes?: Array<Record<string, unknown>>;
  discussions?: Record<string, Record<string, unknown>>;
  discussion_attention?: Record<string, ChannelDiscussionAttention>;
  open_questions?: Array<Record<string, unknown>> | Record<string, Record<string, unknown>>;
  question_frontiers?: Record<string, Array<Record<string, unknown>>>;
  question_dedup_requests?: Array<Record<string, unknown>>;
  owner_questionnaires?: Record<string, Array<Record<string, unknown>>>;
  question_graph_digests?: Record<string, string>;
  consensus?: Record<string, Record<string, unknown>>;
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
  unread_count?: number;
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
  invocation?: {
    schema_version: string;
    scope: Record<string, unknown>;
    summary: {
      considered_count: number;
      loaded_count: number;
      auto_injected_count: number;
      missing_count: number;
      invoked_count: number;
      unobserved_count: number;
    };
    dispatches: Array<Record<string, unknown>>;
    skills: Array<Record<string, unknown>>;
  };
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
  run_scope?: {
    kind: "current_run" | "latest_fanout" | "task_history_legacy" | string;
    workflow_run_id?: string;
    fanout_id?: string;
  };
  history_available?: boolean;
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
  schema_version: "trace-detail.v2" | string;
  trace_id: string;
  trace_id_opaque?: boolean;
  event_count: number;
  first_seq: number | null;
  last_seq: number | null;
  first_ts: string | null;
  last_ts: string | null;
  duration_seconds: number | null;
  status: string;
  timeline: TraceTimelineEvent[];
  tasks: string[];
  tasks_truncated?: boolean;
  actors: string[];
  actors_truncated?: boolean;
  execution_route?: TraceExecutionRouteSummary;
  truncated: boolean;
  has_more: boolean;
  next_cursor: string | null;
  as_of_seq: number;
  empty: boolean;
}

export interface TraceLifecycleSpan {
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  name: string;
  kind: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  source: string;
  truth_class: string;
  degraded: boolean;
  degradation_reason: string | null;
  source_event_ids: string[];
  task_id?: string | null;
  actor?: string | null;
  backend?: string | null;
  provenance?: Record<string, unknown>;
}

export interface TraceSpanCoverage {
  status: "available" | "partial" | "empty" | "degraded" | string;
  reason: string;
  collector: string;
  projection: string;
  ledger: string;
  source: string;
  observed_allowlisted_event_count: number;
  eligible_event_count: number;
  paired_span_count: number;
  degraded_span_count: number;
  unpaired_start_count: number;
  unpaired_terminal_count: number;
  malformed_event_count: number;
  untrusted_event_count: number;
}

export interface TraceSpanPage {
  schema_version: "trace-spans.v1" | string;
  trace_id: string;
  items: TraceLifecycleSpan[];
  focused_item?: TraceLifecycleSpan | null;
  span_count: number;
  limit: number;
  has_more: boolean;
  next_cursor: string | null;
  as_of_seq: number;
  coverage: TraceSpanCoverage;
  diagnostics: Array<{
    kind: string;
    message: string;
    code?: string;
    event_type?: string;
    event_id?: string;
  }>;
  diagnostics_truncated: boolean;
  empty: boolean;
}

export interface TraceExecutionRouteStage {
  stage: string;
  label: string;
  status: string;
  parallel: boolean;
  actors: string[];
  first_seq: number;
  last_seq: number;
  first_ts: string;
  last_ts: string;
  event_count: number;
  event_types: string[];
  task_ids: string[];
  failed_count: number;
  values_truncated: boolean;
}

export interface TraceExecutionRouteSummary extends ExecutionRouteSummary {
  scope: { task_id: string; trace_id: string };
  linear: TraceExecutionRouteStage[];
  trace_event_count: number;
  source_event_count: number;
  metadata_truncated: boolean;
}

export interface TraceTimelineEvent extends EventRecord {
  status?: string | null;
  summary?: string;
  span_id?: string | null;
  parent_span_id?: string | null;
  has_raw?: boolean;
  metadata_truncated?: boolean;
  truncated_fields?: string[];
  payload_slim?: boolean;
}

export interface TraceEventDetail {
  schema_version: "event-detail.v1" | string;
  event_id: string;
  event: EventRecord;
  source: string | Record<string, unknown>;
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

export interface GoalDossier {
  schema_version: "goal-dossier.v1" | string;
  is_derived_projection: boolean;
  run_id: string;
  project_id?: string;
  goal_id: string;
  generated_at: string;
  source_fingerprint: string;
  freshness: {
    status?: string;
    source_event_count?: number;
    last_event_id?: string;
    last_event_at?: string;
    diagnostics?: Array<Record<string, unknown>>;
  };
  goal: Record<string, unknown>;
  terminal?: Record<string, unknown>;
  roadmap: Record<string, unknown>;
  state: {
    task_counts?: Record<string, number>;
    tasks?: Array<Record<string, unknown>>;
    current_overlay?: Record<string, unknown>;
    [key: string]: unknown;
  };
  claim_to_evidence?: {
    status?: string;
    summary?: Record<string, number>;
    rows?: Array<Record<string, unknown>>;
    diagnostics?: Array<Record<string, unknown>>;
  };
  evidence_index: Array<Record<string, unknown>>;
  gaps: Array<Record<string, unknown>>;
  incident_history: Array<Record<string, unknown>>;
  closure: Record<string, unknown>;
  operations: Array<Record<string, unknown>>;
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
  projection_state?: string;
  projection_lag?: number | null;
  tail_behind?: boolean;
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
  tail_behind?: boolean;
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

export interface WebChatTurn {
  schema_version: "webchat-turn.v1" | string;
  turn_id: string;
  project_id: string;
  conversation_id: string;
  thread_id: string;
  task_id?: string;
  backend: string;
  permission_profile: string;
  state: string;
  current_attempt_id?: string;
  stream_cursor?: number;
  pending_interaction_id?: string;
  terminal_reason?: string;
  created_at: string;
  updated_at: string;
}

export interface WebChatInteraction {
  interaction_id: string;
  turn_id: string;
  attempt_id?: string;
  kind: "ask_user" | "tool_approval" | "action_approval" | "artifact_adoption" | string;
  revision: number;
  request_digest: string;
  request: Record<string, unknown>;
  response?: Record<string, unknown>;
  status: string;
  currentness?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WebChatQueueItem {
  queue_id: string;
  idempotency_key: string;
  project_id: string;
  conversation_id: string;
  thread_id: string;
  message: string;
  request?: Record<string, unknown>;
  state: string;
  source_turn_id?: string;
  claimed_turn_id?: string;
  created_at: string;
  updated_at: string;
}

export interface WebChatControlReceipt {
  receipt_id: string;
  idempotency_key: string;
  operation: "steer" | "interrupt" | "cancel" | string;
  turn_id: string;
  attempt_id?: string;
  status: string;
  fallback?: string;
  payload?: Record<string, unknown>;
  created_at: string;
}

export interface WebChatArtifact {
  artifact_id: string;
  version_id: string;
  logical_name: string;
  digest: string;
  media_type: string;
  size_bytes: number;
  storage_uri: string;
  visibility: "private" | "published" | "adopted" | "expired" | string;
  producer: Record<string, unknown>;
  lineage: Record<string, unknown>;
  adopted_target?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WebChatChildActivity {
  activity_id: string;
  kind: "ephemeral_child" | "channel_member" | "delivery_worker" | string;
  parent_turn_id?: string;
  task_id?: string;
  channel_id?: string;
  conversation_id?: string;
  thread_id?: string;
  role?: string;
  provider?: string;
  model?: string;
  permission_profile?: string;
  status: string;
  current_stage?: string;
  heartbeat_at?: string;
  updated_at?: string;
  artifact_refs?: string[];
  capabilities?: string[];
  controls?: Array<{
    operation: "queue" | "steer" | "interrupt" | "cancel" | string;
    route: "controlled_action" | "webchat_turn" | "webchat_queue" | string;
    action?: string;
    target_id?: string;
    payload?: Record<string, unknown>;
    requires_message?: boolean;
  }>;
  why_not_done?: string;
  terminal_evidence?: Array<Record<string, unknown>>;
  usage?: Record<string, unknown>;
  cost?: Record<string, unknown>;
}

export interface WebChatToolCatalog {
  schema_version: string;
  digest: string;
  surface: string;
  credentials_exposed: boolean;
  items: Array<{
    name: string;
    tool_class: string;
    description: string;
    effective_policy: "allow" | "deny" | "ask" | string;
  }>;
}

export interface WebChatRuntime {
  schema_version: "webchat-runtime.v1" | string;
  project_id: string;
  conversation_id: string;
  thread_id: string;
  rollout?: {
    mode: "durable" | "legacy" | string;
    store_schema: string;
    rollback_env: string;
  };
  capabilities: {
    actions: string[];
    controls: string[];
    native_steer: boolean;
    steer_fallback: string;
    completion_authority: boolean;
  };
  turns: WebChatTurn[];
  interactions: WebChatInteraction[];
  queue: WebChatQueueItem[];
  control_receipts?: WebChatControlReceipt[];
  threads: Array<Record<string, unknown>>;
  messages?: Array<{
    message_id: string;
    project_id: string;
    conversation_id: string;
    thread_id: string;
    parent_message_id?: string;
    role: "user" | "assistant" | "system" | string;
    content?: string;
    created_at: string;
    inherited?: boolean;
    inherited_from_thread_id?: string;
    refs?: Record<string, unknown>;
  }>;
  artifacts: WebChatArtifact[];
  tool_catalog: WebChatToolCatalog;
  agent_profile?: {
    profile_id: string;
    revision: number;
    profile_digest: string;
    profile: Record<string, unknown>;
  };
  shadow?: Record<string, unknown>;
}

export interface WebChatSearchResult {
  message_id: string;
  conversation_id: string;
  thread_id: string;
  parent_message_id?: string;
  role: string;
  preview: string;
  created_at: string;
}

export interface AgentProfileCatalog {
  schema_version: string;
  providers: string[];
  roles: string[];
  permission_profiles: string[];
  skills: string[];
  tools: string[];
  contains_secrets: boolean;
  current_profile?: Record<string, unknown>;
  current_revision?: number;
  current_digest?: string;
}

export interface ChannelsPage {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  seq?: number;
  projected_seq?: number;
  projection_state?: string;
  projection_lag?: number | null;
  tail_behind?: boolean;
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
  member_invitation_proposals?: Array<Record<string, unknown>>;
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
  consensus?: Record<string, Record<string, unknown>>;
  open_questions?: Array<Record<string, unknown>> | Record<string, Record<string, unknown>>;
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
  result_receipts?: Array<Record<string, unknown>>;
  discussion?: Record<string, unknown>;
  owner_actor_ref?: string;
  leader_member_id?: string;
  leader_revision?: number;
  origin_binding?: Record<string, unknown>;
  pending_reply_count?: number;
  linked_events?: EventRecord[];
  history_cleared_at?: string;
  history_clear_event_id?: string;
  history_clear_reason?: string;
  has_more?: boolean;
  next_before?: string;
  page?: {
    limit?: number;
    returned?: number;
    before?: string;
    has_more?: boolean;
    next_before?: string;
  };
  diagnostics_summary?: Record<string, number>;
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

export interface LongRunTruthMilestone {
  status: "proven" | "unproven" | string;
  event_id?: string;
  event_type?: string;
  at?: string;
  evidence?: string;
}

export interface LongRunTruthProjection {
  schema_version: string;
  status: "ready" | "degraded" | "empty" | string;
  issues: string[];
  current: {
    run_id: string;
    run_status: string;
    task_map_generation: string;
    candidate_ref: string;
    candidate_digest: string;
    candidate_status: string;
  };
  counts: {
    raw_events: number;
    authority_events: number;
    unique_operations: number;
    authority_operations: number;
    current_operations: number;
    pending_operations: number;
    superseded_operations: number;
  };
  gate: {
    status: string;
    kind: string;
    owner: string;
    reason: string;
    resume_condition: string;
    event_id: string;
    task_id: string;
  };
  no_progress: {
    status: string;
    threshold: number;
    items: Array<Record<string, unknown>>;
  };
  milestones: {
    verified: LongRunTruthMilestone;
    landed: LongRunTruthMilestone;
    reachable: LongRunTruthMilestone;
    owner_notified: LongRunTruthMilestone;
  };
  debug?: Record<string, unknown>;
}

export interface Snapshot {
  read_projection?: { state: "ready" | "building" | "refreshing" | "error"; generated_at_epoch?: number; source_cursor?: { source_seq?: number }; error?: string };
  seq: number;
  generated_at: string;
  project: {
    project_id?: string;
    name?: string;
    description?: string;
    root: string;
    state_dir: string;
  };
  tasks: Task[];
  archive_tasks: Task[];
  features: Feature[];
  delivery_features?: Feature[];
  metrics_snapshot?: MetricsSnapshotProjection;
  long_run_truth?: LongRunTruthProjection;
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
  unread?: boolean;
  read_version?: string;
  resolution?: string;
  verification_required?: boolean;
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
  workflow_run_id?: string;
  fanout_id?: string;
  source_event_id?: string;
  proposal_ref?: string;
  eval_ref?: string;
  candidate_task_map_ref?: string;
  fingerprint?: string;
  attention_id?: string;
  message_id?: string;
  run_id?: string;
  question_id?: string;
  channel_id?: string;
  thread_id?: string;
  asked_by?: string;
  question_kind?: string;
  priority?: string;
  request_event_id?: string;
  request_id?: string;
  revision?: number;
  subject_type?: string;
  conversation_id?: string;
  question_count?: number;
  goal_id?: string;
  terminal_status?: string;
  task_counts?: Record<string, number>;
  claim_counts?: Record<string, number>;
  gap_count?: number;
  next_action?: string;
  deep_link?: string;
  deep_link_label?: string;
  category?: "action_required" | "automation_diagnostic" | "runtime_attention" | "notification" | "resolved" | string;
  actionability?: "human_required" | "automation_owned" | "informational" | "resolved" | string;
  source_role?: string;
  source_actor?: string;
  owner_route?: string;
  content_profile?: string;
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
    unread?: number;
    pending: number;
    action_required_pending?: number;
    noise_pending?: number;
    notification_pending?: number;
    plan_approvals: number;
    attention: number;
    human_decisions?: number;
    decisions?: number;
    decisions_pending?: number;
    channel_decisions?: number;
    kanban_questions?: number;
    run_deliveries?: number;
    suppressed_acknowledged?: number;
  };
  items: OperatorInboxItem[];
  pending: OperatorInboxItem[];
  views?: Record<string, { count: number; ids: string[] }>;
  policy: Record<string, unknown>;
}

export interface PlanPreview {
  action_payload?: Record<string, unknown>;
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
  review_dossier_markdown?: string;
  review_packet?: {
    schema_version: string;
    status: string;
    sections: Array<{
      id: string;
      title: string;
      status: string;
      counts?: Record<string, number>;
      artifact_names?: string[];
      refs?: string[];
      notes?: string[];
    }>;
    diagnostics?: Array<{ code: string; severity: string; message: string; evidence?: string[] }>;
    source_ids?: Record<string, string[]>;
    plan_ids?: Record<string, string[]>;
    owner_decision_items?: Array<Record<string, unknown>>;
    missing_sections?: string[];
    policy?: Record<string, unknown>;
  };
  content_kind?: "full_plan" | "digest_only" | "unavailable";
  content_profile?: "derived_plan_full" | "summary_only" | "derived_plan" | "unknown" | string;
  content_error?: string;
  digest_markdown?: string;
  digest_source?: "bound_task_map" | "stored_digest" | "unavailable";
  source_event_id?: string;
  artifacts?: Array<{ name: string; ref: string; sha256: string }>;
  artifacts_error?: string;
  authority_refs?: string[];
  requirement_artifacts?: Array<{ name: string; ref: string; sha256: string }>;
  task_map_markdown?: string;
  task_map_summary?: {
    ok: boolean;
    task_count: number;
    tasks: Array<{
      task_id?: string;
      title?: string;
      owner_role?: string;
      root_owner_class?: string;
      wave?: string;
      blocked_by?: string;
      allowed_paths?: string[];
      exclusive_files?: string[];
      source_key?: string;
      source_ref?: string;
      source_excerpt?: string;
      acceptance_criteria?: Array<Record<string, unknown>>;
      owner_decision_ids?: string[];
      external_prerequisite_ids?: string[];
      explicit_non_goals?: string[];
      validation?: Record<string, unknown>;
      verification?: string;
    }>;
    reason?: string;
    truncated?: boolean;
  };
  owner_decision_items?: Array<{
    decision_id?: string;
    question?: string;
    options?: Array<{ option_id?: string; label?: string }>;
  }>;
  decision_count?: number;
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
  applied_action?: string;
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
  receipt?: Record<string, unknown>;
  client_message_id?: string;
  message_id?: string;
  duplicate?: boolean;
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
  read_projection?: Snapshot["read_projection"];
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
  attempt_id?: string;
  record_kind?: "attempt" | "signal";
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
  signals?: Array<{ task_id: string; type: string; ts: string; seq: number; reason: string }>;
}

export interface LoopViewLoop {
  id: string;
  label: string;
  shape: string[];
  closure_edge: [string, string] | string[];
  counts: Record<string, number>;
  arc: { state: "flow" | "active" | "broken" | "idle"; label: string };
  health: string;
  members?: Array<{ kind: string; id: string; note: string }>;
  node_stats?: Record<string, Record<string, number>>;
  acct?: { open: number; recovered: number; exhausted: number };
}

export interface LoopViewProjection {
  schema_version: string;
  generated_at: string;
  project_id: string;
  scope: {
    kind: "business_delivery" | "project_recovery" | string;
    business_delivery_active: boolean;
    reason: string;
  };
  run: {
    run_id?: string;
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
  timeline?: {
    tasks: LoopViewTask[];
    attempt_count: number;
    failed_attempt_count: number;
    signal_count: number;
    source: string;
    coverage: string;
    baseline?: { event_id: string; seq: number; task_map_ref: string; task_map_digest: string; task_ids: string[] } | null;
  };
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
  primary_backend: string;
  mixed_enabled: boolean;
  mixed_available: boolean;
  notifications: string;
  backends: OnboardingBackend[];
  preflight: Array<{ name: string; ok: boolean; detail: string }>;
}
