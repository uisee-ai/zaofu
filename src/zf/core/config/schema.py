"""ZfConfig — typed config schema for zf.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field, fields


@dataclass
class ProjectConfig:
    name: str = ""
    workspace: str = "."
    state_dir: str = ".zf"
    # project.scripts.setup:项目自声明的 worktree 就绪脚本,workdir 铸造
    # provision 后在 worktree 内执行。内容属项目语义(pnpm install /
    # uv sync ...),kernel 只负责执行、超时、幂等与失败上浮。
    setup_script: str = ""


@dataclass
class SessionConfig:
    tmux_session: str = "zf"
    # 1206 Phase A: layout strategy. "window_per_role" keeps the legacy
    # one-window-per-role behavior; "pane_grid" collapses all roles
    # into panes of a single window for single-glance observability.
    tmux_layout: str = "window_per_role"


@dataclass
class LoopConfig:
    max_iterations: int = 20
    idle_exit_after: int = 3


@dataclass
class OrchestratorConfig:
    backend: str = "python"
    # P-Y1: empty == use backend CLI default (claude / codex etc).
    # Legacy "placeholder" still accepted by backend.py adapters for
    # backward compat with old yaml configs.
    model: str = ""
    loop: LoopConfig = field(default_factory=LoopConfig)
    # B9/B11 — stream-json transport reliability (Run 3 post-mortem)
    transport_timeout_s: float = 120.0      # per-call SDK timeout
    max_turns: int = 30                      # Claude CLI --max-turns; default cap is too low
    rate_limit_cooldown_s: float = 60.0      # cool-down after agent.api_blocked
    # Min seconds between Layer-2 wakes. A burst of trigger events within this
    # window coalesces into one leading wake + one trailing flush instead of N
    # back-to-back briefings (the orchestrator rebuilds full state from disk
    # each wake, so coalescing loses no decision context). 0 disables.
    wake_min_interval_s: float = 5.0


@dataclass
class ConstraintsConfig:
    allowed_paths: list[str] = field(default_factory=list)
    blocked_paths: list[str] = field(default_factory=list)
    max_steps: int = 0


@dataclass
class ExecutionConfig:
    command: str = ""


@dataclass
class RoleAutoscaleConfig:
    enabled: bool = False
    min_replicas: int = 1
    max_replicas: int = 1
    target_ready_tasks_per_worker: int = 1
    scale_up_pending_seconds: float = 0.0
    scale_down_idle_seconds: float = 900.0
    cooldown_seconds: float = 180.0
    drain_before_stop: bool = True

    def __post_init__(self) -> None:
        if self.min_replicas < 1:
            raise ValueError("RoleAutoscaleConfig.min_replicas must be >= 1")
        if self.max_replicas < self.min_replicas:
            raise ValueError(
                "RoleAutoscaleConfig.max_replicas must be >= min_replicas"
            )
        if self.max_replicas > 6:
            raise ValueError("RoleAutoscaleConfig.max_replicas must be <= 6")
        if self.target_ready_tasks_per_worker < 1:
            raise ValueError(
                "RoleAutoscaleConfig.target_ready_tasks_per_worker must be >= 1"
            )
        if self.scale_up_pending_seconds < 0:
            raise ValueError(
                "RoleAutoscaleConfig.scale_up_pending_seconds must be >= 0"
            )
        if self.scale_down_idle_seconds < 0:
            raise ValueError(
                "RoleAutoscaleConfig.scale_down_idle_seconds must be >= 0"
            )
        if self.cooldown_seconds < 0:
            raise ValueError("RoleAutoscaleConfig.cooldown_seconds must be >= 0")


@dataclass
class RoleConfig:
    name: str = ""
    backend: str = "python"
    role_kind: str = "auto"  # auto | writer | reader
    # B-MIXEDBACKEND-01 (2026-04-23): optional per-replica backend
    # override. When set, its length must equal `replicas` and each entry
    # is the backend for the corresponding instance (dev-1 gets backends[0],
    # dev-2 gets backends[1], ...). When empty, every replica shares
    # `backend`. Mutually exclusive with `backend` in yaml (loader
    # enforces); `backend` here keeps the pre-expansion "typed parent"
    # value for backward-compat tooling that inspects RoleConfig.backend.
    backends: list[str] = field(default_factory=list)
    # P-Y1: empty == use backend CLI default. Don't pin a model in zf.yaml
    # unless the project specifically needs to — backends evolve faster
    # than configs and a stale pinned model is a silent regression.
    model: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    # B/C sprint: removed refresh_policy field — 0 runtime references
    # (Orchestrator._refresh_policy is a separate object with its own
    # max_turns/max_failures defaults, unrelated to RoleConfig).
    permission_mode: str = "bypass"  # "bypass" | "allowlist"
    transport: str = "tmux"  # "tmux" | "stream-json"
    stuck_threshold_seconds: float = 300.0  # G-LIFE-3: seconds of unchanged pane output → worker.stuck
    # 2026-05-15 (r5 discovery): pane cold-boot may exceed the default 30s,
    # causing _wait_role_ready to time out and the watchdog to enter a
    # respawn loop. This knob (seconds) overrides the 120s default in
    # _wait_role_ready. 0 (or unset) → use the default.
    spawn_ready_timeout_seconds: float = 0.0
    # G-INST-1: multi-instance support. `name` is the role *type* (dev/review/...);
    # `instance_id` is the per-worker unique id. Defaults to `name` when
    # replicas == 1 so single-instance configs stay backward-compatible.
    instance_id: str = ""
    replicas: int = 1
    # G-RECYCLE-2 / doc 59: context window tracking. Warning/checkpoint,
    # provider-native compact, and hard-cap recycle are separate thresholds.
    context_window_tokens: int = 200_000   # Claude fallback; codex self-reports
    context_warning_threshold: float | None = None
    context_compact_threshold: float | None = None
    context_hard_cap: float | None = None
    # Legacy aliases. Kept for old zf.yaml files and tests; __post_init__
    # normalizes them onto the context_* fields.
    recycle_threshold: float | None = None
    recycle_hard_cap: float | None = None
    # LH-0.T1: max reworks per task before dispatch is refused and
    # task.rework.capped fires. Counted by review.rejected / test.failed
    # / judge.failed / gate.failed / discriminator.failed events.
    max_rework_attempts: int = 3
    # LH-0.T3: orphan timeouts. After this many seconds without a
    # stage-progress event (dev.build.done, review.approved, test.passed,
    # judge.passed) while the task is in_progress, emit task.orphan_warning;
    # double that → task.orphaned + human.escalate + requeue.
    orphan_warning_seconds: float = 900.0
    orphan_escalate_seconds: float = 1800.0
    # LH-0.T4: after context ratio crosses context_hard_cap, keep
    # refusing new dispatch to this instance for at most this long
    # before force-recycling (even if busy). Prevents infinite
    # pending_recycle when a worker is always busy on long tasks.
    drain_hold_seconds: float = 180.0
    # G-COST-BLOCK-1: per-role hard cost cap. None = no cap.
    budget_usd: float | None = None
    # Agent View / autoscale: zf.yaml remains the control plane. Runtime
    # instances may be added up to max_replicas when this block is enabled.
    autoscale: RoleAutoscaleConfig = field(default_factory=RoleAutoscaleConfig)
    constraints: ConstraintsConfig = field(default_factory=ConstraintsConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    stages: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    publishes: list[str] = field(default_factory=list)
    # V4:单句提示列表,briefing 注入,
    # **不参与任何门判定**——规则必须有机器门,guardrails 只是提示
    # (doc 90 rev2.1 边界)。
    guardrails: list[str] = field(default_factory=list)
    # P-Y2: optional Claude-only extensions. Codex backends ignore these
    # (loader emits a one-line warning on load — see _build_role).
    #   plugins — directories passed via `claude --plugin-dir <path>`
    #             (repeatable). Each path is loaded for the spawn session.
    #   skills  — names of skills the role should leverage. Not a CLI
    #             flag; rendered into the role's instructions so Claude
    #             auto-resolves via /skill-name on demand.
    #   agent   — single Claude agent name passed via `claude --agent`.
    plugins: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    agent: str = ""

    def __post_init__(self) -> None:
        if self.replicas < 1:
            raise ValueError(
                f"RoleConfig(name={self.name!r}).replicas must be >= 1, got {self.replicas}"
            )
        if self.context_window_tokens <= 0:
            raise ValueError(
                f"RoleConfig(name={self.name!r}).context_window_tokens must be > 0, "
                f"got {self.context_window_tokens}"
            )
        legacy_threshold = self.recycle_threshold
        legacy_hard_cap = self.recycle_hard_cap
        warning = (
            self.context_warning_threshold
            if self.context_warning_threshold is not None
            else legacy_threshold
            if legacy_threshold is not None
            else 0.6
        )
        compact = (
            self.context_compact_threshold
            if self.context_compact_threshold is not None
            else legacy_threshold
            if legacy_threshold is not None
            else 0.7
        )
        hard_cap = (
            self.context_hard_cap
            if self.context_hard_cap is not None
            else legacy_hard_cap
            if legacy_hard_cap is not None
            else 0.9
        )
        self.context_warning_threshold = float(warning)
        self.context_compact_threshold = float(compact)
        self.context_hard_cap = float(hard_cap)
        self.recycle_threshold = self.context_warning_threshold
        self.recycle_hard_cap = self.context_hard_cap
        if not (0.0 < self.context_warning_threshold < 1.0):
            raise ValueError(
                f"RoleConfig(name={self.name!r}).context_warning_threshold "
                f"must be in (0, 1), got {self.context_warning_threshold}"
            )
        if not (
            self.context_warning_threshold
            <= self.context_compact_threshold
            <= self.context_hard_cap
            < 1.0
        ):
            raise ValueError(
                f"RoleConfig(name={self.name!r}): context thresholds must satisfy "
                "0 < context_warning_threshold <= context_compact_threshold "
                "<= context_hard_cap < 1.0, got "
                f"warning={self.context_warning_threshold} "
                f"compact={self.context_compact_threshold} "
                f"hard_cap={self.context_hard_cap}"
            )
        if not self.instance_id:
            self.instance_id = self.name
        # B-MIXEDBACKEND-01: validate backends list shape when provided.
        # Empty list (the default) means "all replicas share `backend`".
        # When provided, length must equal replicas and items non-empty.
        if self.backends:
            if len(self.backends) != self.replicas:
                raise ValueError(
                    f"RoleConfig(name={self.name!r}): len(backends)="
                    f"{len(self.backends)} must equal replicas={self.replicas}"
                )
            for i, b in enumerate(self.backends):
                if not b:
                    raise ValueError(
                        f"RoleConfig(name={self.name!r}): backends[{i}] "
                        f"is empty — every replica must declare a backend"
                    )
        if self.autoscale.enabled and self.replicas > self.autoscale.max_replicas:
            raise ValueError(
                f"RoleConfig(name={self.name!r}): replicas={self.replicas} "
                f"must be <= autoscale.max_replicas={self.autoscale.max_replicas}"
            )


@dataclass
class QualityGateConfig:
    enabled: bool = True
    required_checks: list[str] = field(default_factory=list)
    # V4:门失败时的修复文案,随 gate.failed
    # payload 进 briefing —— prose 的唯一合法住所(doc 90 rev2.1)。
    on_fail: str = ""


@dataclass
class WakeExtensionConfig:
    """P3 (2026-04-20): opt-in wake pattern extension for one class of
    events (hooks or agent telemetry). Disabled by default to preserve
    prior behavior.

    rate_limit_per_minute: cap on how often events of this class wake
    the orchestrator. 0 = unlimited. Protects against pathological
    scenarios (rapid tool bursts, hook floods) from amplifying kernel
    workload N× per worker turn.
    """
    enabled: bool = False
    include: list[str] = field(default_factory=list)
    rate_limit_per_minute: int = 0


@dataclass
class WakeExtensionsConfig:
    """P3 (2026-04-20): configurable wake_patterns extensions.

    Default = all off. When `hooks.enabled=true`, `hooks.include` event
    types are added to wake_patterns so hook events can drive Layer 1
    decisions (e.g. circuit breaker reacts to Codex PreToolUse deny).
    Same shape for agent.* telemetry events.
    """
    hooks: "WakeExtensionConfig" = field(default_factory=lambda: WakeExtensionConfig())
    agent: "WakeExtensionConfig" = field(default_factory=lambda: WakeExtensionConfig())


@dataclass
class FanoutAggregateConfig:
    mode: str = "wait_for_all"
    success_event: str = ""
    failure_event: str = ""
    child_success_event: str = "workflow.child.completed"
    child_failure_event: str = "workflow.child.failed"
    synth_role: str = ""
    max_retries: int = 0
    # EVAL-WAVE-REVIEW-001 (doc 43 §2.6): wave_review aggregation
    # strategies for multi-reviewer adversarial review. Empty string
    # ('') falls back to ``mode``-based behavior (backward compat).
    #
    # Supported strategies:
    # - "all_approve_or_one_rejects" — all reviewers must approve;
    #   ANY reject → failure_event (strongest adversarial guarantee)
    # - "majority_approve" — >50% approve → success_event
    # - "any_approve_and_no_reject" — at least 1 approve AND no
    #   rejects → success_event (lenient; permits abstain via
    #   review.suspended)
    review_strategy: str = ""
    pending_event: str = ""   # emitted when quorum not yet reached
    quorum: int = 0           # minimum reviewers responses before aggregating
    # B3 (R25 ISSUE-005): dedicated synth wait budget. 0 → inherit the
    # stage timeout (legacy behavior, 40min-class waits).
    synth_timeout_seconds: int = 0


@dataclass
class FanoutChildConfig:
    role_instance: str = ""
    role: str = ""
    scope: str = ""
    task_id: str = ""
    payload: dict = field(default_factory=dict)


@dataclass
class FanoutAssignmentConfig:
    strategy: str = "static_index"
    role_pool: list[str] = field(default_factory=list)
    lane_profile: str = ""
    stage_slot: str = ""


@dataclass
class WorkflowAffinityLaneConfig:
    id: str = ""
    impl: str = ""
    review: str = ""
    verify: str = ""


@dataclass
class WorkflowAffinityQueueConfig:
    order: str = "priority_fifo"


@dataclass
class WorkflowAffinityLaneProfileConfig:
    affinity_key: str = "affinity_tag"
    queue: WorkflowAffinityQueueConfig = field(
        default_factory=WorkflowAffinityQueueConfig,
    )
    lanes: list[WorkflowAffinityLaneConfig] = field(default_factory=list)


@dataclass
class WorkflowStageOutputConfig:
    required_keys: list[str] = field(default_factory=list)
    required_artifacts: list[str] = field(default_factory=list)
    artifact_kinds: list[str] = field(default_factory=list)


@dataclass
class WorkflowStageRetryPolicyConfig:
    max_attempts: int = 0
    backoff_seconds: int = 0
    on_failure: str = "rework"  # rework | retry | suspend | escalate

    def __post_init__(self) -> None:
        if self.max_attempts < 0:
            raise ValueError("workflow stage retry.max_attempts must be >= 0")
        if self.backoff_seconds < 0:
            raise ValueError("workflow stage retry.backoff_seconds must be >= 0")
        if self.on_failure not in {"rework", "retry", "suspend", "escalate"}:
            raise ValueError(
                "workflow stage retry.on_failure must be one of "
                "rework, retry, suspend, escalate"
            )


@dataclass
class WorkflowStageCriteriaConfig:
    instructions: list[str] = field(default_factory=list)
    success_criteria: list[dict] = field(default_factory=list)
    output: WorkflowStageOutputConfig = field(default_factory=WorkflowStageOutputConfig)
    retry: WorkflowStageRetryPolicyConfig = field(default_factory=WorkflowStageRetryPolicyConfig)


@dataclass
class WorkflowStageBackedgeConfig:
    event: str = ""
    restart_stage: str = ""
    restart_role: str = ""
    target_affinity: str = ""
    max_attempts: int = 0
    feedback_artifact: str = ""
    emit: str = ""

    def __post_init__(self) -> None:
        if self.max_attempts < 0:
            raise ValueError("workflow stage backedge.max_attempts must be >= 0")


@dataclass
class WorkflowStageConfig:
    id: str = ""
    trigger: str = ""
    topology: str = ""
    roles: list[str] = field(default_factory=list)
    target_ref: str = ""
    task_map: str = ""
    assignment: FanoutAssignmentConfig = field(default_factory=FanoutAssignmentConfig)
    children: list[FanoutChildConfig] = field(default_factory=list)
    aggregate: FanoutAggregateConfig = field(default_factory=FanoutAggregateConfig)
    timeout_seconds: int = 0
    criteria: WorkflowStageCriteriaConfig = field(default_factory=WorkflowStageCriteriaConfig)
    on_reject: WorkflowStageBackedgeConfig = field(default_factory=WorkflowStageBackedgeConfig)
    on_fail: WorkflowStageBackedgeConfig = field(default_factory=WorkflowStageBackedgeConfig)
    gate_profile: list[str] = field(default_factory=list)
    # FIX-15②(bizsim r4):判审段 delta 门——同一审计对象 commit 已有本段
    # 驳回记录时拒绝重开审(r4 judge 两次必败审的实锚)。opt-in,verify
    # 类基建重试段勿开。
    retrigger_requires_delta: bool = False
    # Opt-in: when a writer-fanout stage is driven directly by task_map.ready
    # (the refactor scan flow) instead of the product-delivery handshake,
    # synthesize the task_map's tasks as canonical kanban tasks before the
    # admission gate. Default False keeps the fail-closed canonical gating.
    synthesize_canonical_tasks: bool = False


@dataclass
class WorkflowDagConfig:
    """P2/K4 (docs/impl/22-zaofu-canonical-dag.md): parse ``workflow.dag``
    sub-section so kernel can enforce stage_order + required_backlog_refs
    + dev_requires_orchestrator_backlog.

    These fields were declared in yaml but ``loader.py`` silently ignored
    them until P2. Defaults preserve old behavior (everything disabled = no
    enforcement) so old yamls without ``workflow.dag`` keep working.
    """

    enabled: bool = False
    graph_static_gate_action: bool = False
    graph_review_test_judge_reconcile: bool = False
    default_gate_level: str = "permissive"  # strict | permissive
    dev_requires_orchestrator_backlog: bool = False
    design_to_backlog_owner: str = ""  # e.g. "orchestrator"
    design_events: dict[str, str] = field(default_factory=dict)
    required_backlog_refs: list[str] = field(default_factory=list)
    stage_order: list[str] = field(default_factory=list)
    # TR-EVENT-SCHEMA-LOCK-001 step 1/3 (doc 42 §11.3 A from
    # cangjie-mono eval, 2026-05-18): per-event-type payload schema
    # rules. Empty dict → loose mode (no schema validation), preserves
    # backward compat. See src/zf/core/verification/event_schema.py for
    # the rule format. Parsed dumb-as-dict here; EventSchemaRegistry
    # interprets the structure.
    event_schemas: dict[str, dict] = field(default_factory=dict)
    # doc 90 增补:顶层 profile 引用(不依赖 lane_pipeline)。merge 优先级
    # profile → 本地 event_schemas(逃生门最高),分级同 A2。
    schema_profile: str = ""
    # v4 smoke 发现(2026-06-12):入口事件是外部接口(doc 90 §2.4),
    # flowProfile/lane 物化在展开期登记,graph producer 判定豁免 ——
    # 原则化而非点名(G2 精神)。
    external_triggers: list = field(default_factory=list)



@dataclass
class WorkflowSplitQualityConfig:
    mode: str = "warning"  # warning | blocking
    max_scope_files: int = 12
    require_validation_surface: bool = True

    def __post_init__(self) -> None:
        if self.mode not in {"warning", "blocking"}:
            raise ValueError(
                f"workflow.work_units.split_quality.mode must be warning/blocking, got {self.mode!r}"
            )
        if self.max_scope_files < 0:
            raise ValueError("workflow.work_units.split_quality.max_scope_files must be >= 0")


@dataclass
class WorkflowWorkUnitsConfig:
    enabled: bool = False
    split_quality: WorkflowSplitQualityConfig = field(
        default_factory=WorkflowSplitQualityConfig,
    )


@dataclass
class WorkflowCompletionAuditConfig:
    enabled: bool = False
    provider_completed_state: str = "completed_unverified"
    routes: dict[str, str] = field(default_factory=dict)


@dataclass
class WorkflowResumePacketConfig:
    enabled: bool = False
    max_tokens: int = 1200
    generate_on: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("workflow.resume_packet.max_tokens must be > 0")


@dataclass
class WorkflowIntegrationConfig:
    enabled: bool = False
    boundaries: list[str] = field(default_factory=list)


@dataclass
class WorkflowStrictTriggersConfig:
    rework_attempts_gte: int = 0
    context_usage_gte: float = 0.0
    file_globs: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.rework_attempts_gte < 0:
            raise ValueError("workflow.strict_triggers.rework_attempts_gte must be >= 0")
        if self.context_usage_gte < 0:
            raise ValueError("workflow.strict_triggers.context_usage_gte must be >= 0")


@dataclass
class WorkflowFastPathConfig:
    """Optional small-task path for low-risk implementation work.

    The kernel still treats zf.yaml as the control plane. This block only
    declares when the orchestrator may bypass heavy design/judge stages; review
    and verification evidence remain required unless the YAML says otherwise.
    """

    enabled: bool = False
    max_scope_files: int = 2
    skip_stages: list[str] = field(
        default_factory=lambda: ["design", "design_critique", "judge"],
    )
    allow_docs_only: bool = True
    blocked_file_globs: list[str] = field(default_factory=list)
    blocked_keywords: list[str] = field(default_factory=list)
    verification_required: bool = True

    def __post_init__(self) -> None:
        if self.max_scope_files < 0:
            raise ValueError("workflow.fast_path.max_scope_files must be >= 0")
        allowed = {"design", "design_critique", "judge"}
        unknown = [stage for stage in self.skip_stages if stage not in allowed]
        if unknown:
            raise ValueError(
                "workflow.fast_path.skip_stages contains unsupported stages: "
                + ", ".join(unknown)
            )


@dataclass
class WorkflowReplanEvalConfig:
    enabled: bool = False
    profile: str = "baseline"  # baseline | strict | release
    require_source_coverage: bool = True
    strict_requires_independent_review: bool = True
    release_requires_e2e: bool = True
    release_requires_security: bool = True
    release_requires_human_approval: bool = True

    def __post_init__(self) -> None:
        if self.profile not in {"baseline", "strict", "release"}:
            raise ValueError(
                "workflow.replan_eval.profile must be baseline, strict, or release; "
                f"got {self.profile!r}"
            )


@dataclass
class WorkflowAdmissionReplanConfig:
    """Plan-level replan → 自动回 synth 重拆。

    R27/R29 实证缺口:task_map.ready 过不了 admission(缺 assembly / 无 root
    owner / W1 路径重叠)时,orchestrator 只记 no_action → **永久 stall**。
    本开关把那根「admission 拦截 → rework 回 synth」(doc 93 §1 图里画了、
    B14 只接了人审 plan.rejected 那半)补上:enabled=True 时,admission 类
    fanout.cancelled 触发候选级 replan —— 重发 ``resynth_trigger`` 事件并带
    admission reason 作 rework_feedback,plan synth 据此重拆出合格 task_map;
    attempt cap(沿用候选级 max_attempts)后 escalate + B12 quarantine。

    R35 补充:同一条 resynth bridge 也覆盖 repeated candidate verify
    被分类为 ``contract_freeze_gap`` / 其他 plan-level failure 的情况。
    字段名保留 ``admission_replan`` 以兼容已有 yaml。

    默认 ``enabled=False`` = 现状(no_action),零迁移。``resynth_trigger`` 是
    重跑 plan synth 那个 stage 的真实 trigger 事件(如 refactor 链的
    ``zaofu.refactor.review.ready``);留空则即便 enabled 也不重发(防误配)。
    """

    enabled: bool = False
    resynth_trigger: str = ""


@dataclass
class WorkflowConfig:
    # B/C sprint: removed mode/gate_level/strategy/stages — 0 runtime
    # references. Old yaml that sets them still loads (loader ignores
    # unknown keys), only the in-memory config stops carrying them.
    gan_rounds: int = 1  # G-GAN-2: default 1 = no loop. >=2 enables architect↔critic GAN loop.
    harness_profile: str = "baseline"  # baseline | strict | release

    # B14 (doc 93 §8): 人工 plan 审核门开关。False(缺省) = admission
    # 通过后 kernel 自动铸 plan.approved(payload auto:true)直接执行,
    # 行为与无门时代逐事件等价(零迁移);True = hold,发
    # plan.approval.requested 等 operator 批。fanout 触发语义恒挂
    # plan.approved —— 开关只决定这枚事件由谁铸。
    plan_approval_enabled: bool = False

    # 131-P2-3(Temporal 借鉴条款):thinking backend 闲置宽限,自派发起
    # max(idle_threshold, attempt_lease_grace_s) 内不判 idle。F15 实证值
    # 900s 出厂;深读型 reader 多的项目可调大。
    attempt_lease_grace_s: float = 900.0

    # P0-2 (2026-04-20): YAML-declared event→action bindings. Each entry
    # has shape:
    #   - event: <event_type>
    #     actions:
    #       - type: emit | log | noop
    #         params: { ... }
    # These are appended to the built-in reactor registry at startup,
    # so custom YAML roles can publish new event types and have Layer 1
    # react without requiring Python changes.
    event_actions: list[dict] = field(default_factory=list)

    # P1-1 (2026-04-20): per-failure-event rework target. Keys are
    # event type strings (review.rejected, test.failed, critic.plan.
    # rejected, etc); values are role names. Falls back to "dev" for
    # unspecified events. Task.contract.rework_to (per-task) overrides
    # this (per-project default) when set.
    #
    # Example:
    #   rework_routing:
    #     review.rejected: dev
    #     test.failed: dev
    #     critic.plan.rejected: arch     # critic driven design rework
    rework_routing: dict[str, str] = field(default_factory=dict)
    # R28 (doc 93 §1/§5): admission/W1 机械拒 → 自动回 synth 重拆。默认
    # 关闭 = no_action 现状(零迁移);见 WorkflowAdmissionReplanConfig。
    admission_replan: WorkflowAdmissionReplanConfig = field(
        default_factory=WorkflowAdmissionReplanConfig,
    )
    stages: list[WorkflowStageConfig] = field(default_factory=list)
    # doc 88 P0 (2026-06-11-0327): high-level lane_pipeline specs, parsed by
    # core/workflow/lane_pipeline.py. Inspect-only at P0 — runtime does not
    # consume them yet.
    pipelines: list = field(default_factory=list)
    # doc 90 A1: lane_role_template 展开产生的 role 来源元数据
    # (loader 落盘,inspect 直读——重放会把已合并产物误判为 override)。
    pipelines_role_meta: list = field(default_factory=list)
    # doc 90 A2: effective event_schemas 的来源(event→profile|override|local)。
    pipelines_schema_sources: dict = field(default_factory=dict)
    affinity_lanes: dict[str, WorkflowAffinityLaneProfileConfig] = field(
        default_factory=dict,
    )

    # P3 (2026-04-20): optional wake pattern extensions for long-
    # horizon / chaos-test scenarios. See WakeExtensionsConfig docstring.
    wake_extensions: WakeExtensionsConfig = field(
        default_factory=WakeExtensionsConfig,
    )

    # P2/K4 (docs/impl/22): parse workflow.dag sub-section so kernel can
    # enforce stage_order + required_backlog_refs + dev_requires_orchestrator_backlog.
    # Defaults to empty/disabled (no enforcement) so old yamls keep working.
    dag: WorkflowDagConfig = field(default_factory=WorkflowDagConfig)

    # ZF-LH-INLINE-001 (doc 26 §3.3, doc 39 backfill): user-message
    # inline override keywords. Default disabled (enabled=False) so
    # old yamls keep working. See WorkflowInlineOverrides docstring.
    inline_overrides: WorkflowInlineOverrides = field(
        default_factory=lambda: WorkflowInlineOverrides(),
    )
    work_units: WorkflowWorkUnitsConfig = field(
        default_factory=WorkflowWorkUnitsConfig,
    )
    completion_audit: WorkflowCompletionAuditConfig = field(
        default_factory=WorkflowCompletionAuditConfig,
    )
    resume_packet: WorkflowResumePacketConfig = field(
        default_factory=WorkflowResumePacketConfig,
    )
    integration: WorkflowIntegrationConfig = field(
        default_factory=WorkflowIntegrationConfig,
    )
    strict_triggers: WorkflowStrictTriggersConfig = field(
        default_factory=WorkflowStrictTriggersConfig,
    )
    fast_path: WorkflowFastPathConfig = field(
        default_factory=WorkflowFastPathConfig,
    )
    replan_eval: WorkflowReplanEvalConfig = field(
        default_factory=WorkflowReplanEvalConfig,
    )
    # FlowProfile / controller emitted metadata. This is not runtime truth and
    # never drives scheduling directly; it lets inspect/render audit whether
    # declared high-level policies have deterministic consumers.
    flow_metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.harness_profile not in {"baseline", "strict", "release"}:
            raise ValueError(
                "workflow.harness_profile must be baseline, strict, or release; "
                f"got {self.harness_profile!r}"
            )


@dataclass
class WorkflowInlineOverrides:
    """ZF-LH-INLINE-001 (doc 26 §3.3): operator can include emergency
    override keywords inside a user.message to skip a pipeline stage
    for the next dispatched task.

    YAML shape::

        workflow:
          inline_overrides:
            enabled: true
            patterns:
              skip_critic:  ["skip critic", "别走 critic"]
              skip_test:    ["skip test", "别测了"]
              skip_judge:   ["skip judge", "别走 judge"]
              skip_all:     ["skip workflow", "直接 ship"]
            audit_event: "workflow.inline_override"

    Discipline (enforced by the scanner, not just docs):

    - Only matches when the source event ``actor`` is ``user`` or
      ``zf-cli``. Agent-emitted events that quote a keyword don't
      trigger — closes the obvious "make worker quote 'skip judge' to
      bypass review" attack.
    - Every match emits a ``workflow.inline_override`` audit event
      with ``human_initiated: true`` so the override is auditable.
    - ``enabled=False`` short-circuits the scanner entirely — no
      patterns are matched, no audit events fire.
    """

    enabled: bool = False
    patterns: dict[str, list[str]] = field(default_factory=dict)
    audit_event: str = "workflow.inline_override"


@dataclass
class SemanticDConfig:
    """LH-2: SemanticDiscriminator toggle. Default off to keep existing
    pipelines unchanged; flip to true when the project wants scope +
    exclusion fidelity checks run as part of the discriminator array."""
    enabled: bool = False


@dataclass
class ScopeVerificationConfig:
    """Scope ratchet enforcement mode.

    ``fail_closed`` keeps scope violations observational by default for
    legacy projects. Strict harness presets can flip it so out-of-scope writes
    block the dev -> review handoff and route rework.
    """

    fail_closed: bool = False


@dataclass
class RuntimeRuleDConfig:
    """Runtime discriminator toggle for rules promoted from docs/review."""

    enabled: bool = False


@dataclass
class ContractDConfig:
    """Contract discriminator toggle.

    Default stays permissive for legacy tasks. Presets that require
    Sprint Contract discipline can set ``verification.contract.required``.
    """

    required: bool = False
    quality_required: bool = False
    rework_delta_required: bool = False
    dispatch_token_required: bool = False


@dataclass
class EventSchemaValidationConfig:
    """TR-EVENT-SCHEMA-LOCK-001 step 2/3: runtime mode for event payload
    schema validation. Defaults to ``disabled`` so legacy projects keep
    working with no behavioural change.

    Modes:
      - ``disabled``: validator is a no-op (default; backward compat)
      - ``warning``:  validate every appended event; on violation, write
                      the original event AS USUAL plus an extra
                      ``event.schema.violated`` warning event. Does NOT
                      block the workflow — operators can migrate workers
                      to emit full payloads without losing events.
      - ``blocking``: validate every appended event; on violation, REPLACE
                      the original with ``discriminator.failed`` so the
                      stage transition is blocked and rework_routing
                      kicks in.

    Set in zf.yaml:

    ::

        verification:
          event_schema:
            mode: warning
    """

    mode: str = "disabled"  # disabled | warning | blocking


@dataclass
class VerificationConfig:
    contract: ContractDConfig = field(default_factory=ContractDConfig)
    semantic: SemanticDConfig = field(default_factory=SemanticDConfig)
    scope: ScopeVerificationConfig = field(default_factory=ScopeVerificationConfig)
    architecture: RuntimeRuleDConfig = field(default_factory=RuntimeRuleDConfig)
    promoted: RuntimeRuleDConfig = field(default_factory=RuntimeRuleDConfig)
    # TR-EVENT-SCHEMA-LOCK-001 step 2/3:
    event_schema: EventSchemaValidationConfig = field(
        default_factory=EventSchemaValidationConfig,
    )
    # LH-B1: staging knob for the stale-runtime-snapshot terminal gate.
    #   enforced (default) — mismatch emits task.completion.stale_rejected
    #                        AND blocks the terminal transition (current behavior)
    #   shadow             — mismatch emits stale_rejected (observable) but does
    #                        NOT block; lets operators watch before enforcing
    #   off                — mismatch neither emits nor blocks
    # Default "enforced" preserves the pre-LH-B1 fail-closed behavior.
    snapshot_gate: str = "enforced"


@dataclass
class WorkdirConfig:
    enabled: bool = False
    root: str = ".zf/workdirs"
    mode: str = "dry-run"  # dry-run | worktree
    # Gitignored runtime env dirs (e.g. `.venv`, `node_modules`) present in the
    # main checkout but absent in fresh worktrees. Each existing path is
    # symlinked from project_root into every worktree so gate/verify commands
    # (run_tests.sh, tsc, npm) run natively. Missing sources are skipped.
    provision_paths: list[str] = field(default_factory=list)


@dataclass
class GitIsolationConfig:
    writer_branch_prefix: str = "worker"
    task_ref_prefix: str = "task"
    candidate_branch_prefix: str = "candidate"
    candidate_base_ref: str = "main"
    candidate_strategy: str = "cherry-pick"
    # local | optional | required | local_only.
    # This governs whether dev workers may/must publish task commits to a
    # remote. The default is local-first: a local commit is the evidence
    # contract, and remote publication is an operator/task override.
    remote_policy: str = "local"
    ship_target_branch: str = "main"
    ship_candidate_strategy: str = "merge"
    ship_task_strategy: str = "cherry-pick"
    ship_final_command: str = ""
    # 2026-05-15 r-next backlog B-3: orchestrator auto-ship candidate when
    # candidate.integration.completed (quality passed) fires. Default off
    # so existing flows that rely on operator-triggered ship aren't surprised;
    # cangjie + autoresearch-driven runs set this true to close the
    # candidate→main loop without manual `git merge`.
    auto_ship_on_candidate_complete: bool = False
    # cj-min refactor: auto-ship the candidate after the TERMINAL gate
    # (judge.passed), not at candidate.integration.completed — which in the
    # fanout-writer topology fires BEFORE the candidate-level review→verify→judge
    # pass (R18 order: integration.completed ... review.approved, test.passed,
    # judge.passed). Shipping at integration would merge un-judged code. Default
    # off; the cj-min run sets this true with ship_target_branch=cj-min.
    auto_ship_on_judge_passed: bool = False


@dataclass
class RuntimeSkillsConfig:
    pool: str = ".zf/skills"
    materialize: str = "copy"  # copy | symlink
    lock_file: str = ".zf/skills.lock.json"
    strict: bool = False


@dataclass
class RuntimeRunManagerResidentAgentConfig:
    enabled: bool = False
    transport: str = "tmux"
    instance_id: str = "run-manager"
    prompt_on_start: bool = True
    # shared: resident pane joins session.tmux_session.
    # dedicated: resident pane uses tmux_session or derives
    # "<session.tmux_session>-run-manager".
    session_mode: str = "shared"
    tmux_session: str = ""


@dataclass
class RuntimeRunManagerReflectConfig:
    enabled: bool = False
    backend: str = ""
    timeout_seconds: int = 180


@dataclass
class RuntimeRunManagerSourceRepairConfig:
    enabled: bool = False
    backend: str = ""
    mode: str = "isolated_worktree"
    branch_prefix: str = "self-repair/run-manager"
    apply_policy: str = "proposal_only"
    restart_policy: str = "never_during_active_run"
    restart_boundary: str = "terminal_or_operator_approved_checkpoint"
    replay_before_restart: bool = True
    allow_paths: list[str] = field(default_factory=lambda: [
        "src/zf/**",
        "tests/**",
        "docs/**",
    ])
    deny_paths: list[str] = field(default_factory=lambda: [
        ".env",
        "**/events.jsonl",
        "**/kanban.json",
        "**/session.yaml",
    ])


@dataclass
class RuntimeRunManagerConfig:
    backend: str = ""
    reflect: RuntimeRunManagerReflectConfig = field(
        default_factory=RuntimeRunManagerReflectConfig,
    )
    resident_agent: RuntimeRunManagerResidentAgentConfig = field(
        default_factory=RuntimeRunManagerResidentAgentConfig,
    )
    source_repair: RuntimeRunManagerSourceRepairConfig = field(
        default_factory=RuntimeRunManagerSourceRepairConfig,
    )


@dataclass
class RuntimeAutoresearchResidentConfig:
    enabled: bool = False
    interval_seconds: float = 10.0
    max_actions_per_tick: int = 1
    worktree_root: str = "/tmp/zaofu-autoresearch-resident/worktrees"
    output_root: str = ""
    self_repair_consumer: bool = False
    self_repair_spawn: bool = False
    self_repair_backend: str = ""


@dataclass
class RuntimeFeishuInboundConfig:
    enabled: bool = False
    # Only the long-connection bridge is productized as a zf start sidecar.
    mode: str = "bridge"
    debounce_ms: int = 600
    require_routing: bool = True
    # Non-empty = per-sender allowlist: inbound messages from other senders
    # are dropped with a `feishu.inbound.sender_blocked` audit event.
    allowed_senders: list[str] = field(default_factory=list)


@dataclass
class RuntimeConfig:
    workdirs: WorkdirConfig = field(default_factory=WorkdirConfig)
    git: GitIsolationConfig = field(default_factory=GitIsolationConfig)
    skills: RuntimeSkillsConfig = field(default_factory=RuntimeSkillsConfig)
    run_manager: RuntimeRunManagerConfig = field(
        default_factory=RuntimeRunManagerConfig,
    )
    autoresearch_resident: RuntimeAutoresearchResidentConfig = field(
        default_factory=RuntimeAutoresearchResidentConfig,
    )
    feishu_inbound: RuntimeFeishuInboundConfig = field(
        default_factory=RuntimeFeishuInboundConfig,
    )


@dataclass
class OpenClawRemoteBindingConfig:
    id: str = ""
    mode: str = "remote_gateway"
    base_url: str = ""
    token_env: str = ""
    default_workspace_policy: str = "isolated"
    tool_profile: str = "safe"
    timeout_seconds: float = 120.0
    provision_agent: bool = False


@dataclass
class OpenClawProviderConfig:
    default_binding: str = ""
    bindings: dict[str, OpenClawRemoteBindingConfig] = field(default_factory=dict)


@dataclass
class ProvidersConfig:
    openclaw: OpenClawProviderConfig = field(default_factory=OpenClawProviderConfig)


@dataclass
class OpenClawFeishuBridgeZaofuConfig:
    channel_id: str = ""
    thread_id: str = "main"


@dataclass
class OpenClawFeishuBridgeOpenClawConfig:
    provider_binding_id: str = ""
    account_id: str = "default"
    agent_id: str = "zaofu-bridge"


@dataclass
class OpenClawFeishuBridgeFeishuConfig:
    chat_id: str = ""
    target: str = ""


@dataclass
class OpenClawFeishuBridgeOutboundConfig:
    enabled: bool = True
    include_event_types: list[str] = field(
        default_factory=lambda: ["channel.message.posted"]
    )
    exclude_roles: list[str] = field(default_factory=lambda: ["system"])
    reply_to_inbound_source: bool = True


@dataclass
class OpenClawFeishuBridgeInboundConfig:
    enabled: bool = False
    require_prefix: str = "/zf"
    require_mention: bool = True
    accept_plain_text: bool = False
    allowed_chat_ids: list[str] = field(default_factory=list)
    payload_dir: str = ""
    server_token_env: str = "ZF_OPENCLAW_FEISHU_INBOUND_TOKEN"


@dataclass
class OpenClawFeishuBridgeBindingConfig:
    id: str = ""
    zaofu: OpenClawFeishuBridgeZaofuConfig = field(
        default_factory=OpenClawFeishuBridgeZaofuConfig
    )
    openclaw: OpenClawFeishuBridgeOpenClawConfig = field(
        default_factory=OpenClawFeishuBridgeOpenClawConfig
    )
    feishu: OpenClawFeishuBridgeFeishuConfig = field(
        default_factory=OpenClawFeishuBridgeFeishuConfig
    )
    mode: str = "interactive"
    outbound: OpenClawFeishuBridgeOutboundConfig = field(
        default_factory=OpenClawFeishuBridgeOutboundConfig
    )
    inbound: OpenClawFeishuBridgeInboundConfig = field(
        default_factory=OpenClawFeishuBridgeInboundConfig
    )


@dataclass
class OpenClawFeishuBridgeConfig:
    enabled: bool = False
    default_binding: str = ""
    bindings: dict[str, OpenClawFeishuBridgeBindingConfig] = field(
        default_factory=dict
    )


@dataclass
class FeishuIdentityUserConfig:
    """One Feishu principal → ZaoFu operator + auth level mapping entry."""

    operator: str = ""
    level: str = "viewer"  # viewer | operator | approver


@dataclass
class FeishuIdentityConfig:
    """Trust model for inbound Feishu callbacks (backlog feishu-B).

    Lives in the control plane (zf.yaml) alongside the kernel trust model:
    granting a Feishu user APPROVER is a privilege grant, so it belongs here,
    not in a scattered runtime file. Disabled by default → fail-closed: with no
    mapping every principal is an unmapped VIEWER and all mutations are denied.
    """

    enabled: bool = False
    verification_token_env: str = "ZF_FEISHU_VERIFICATION_TOKEN"
    replay_window_seconds: int = 300
    users: dict[str, FeishuIdentityUserConfig] = field(default_factory=dict)
    # feishu-A2 signed action tokens: bind a card button to its exact
    # (action, target, chat, expiry, nonce) so it can't be forged, replayed,
    # or repurposed even by an authorized principal. Separate HMAC key from the
    # Feishu verification token. require_signed_actions stays False for compat
    # (in-flight unsigned cards still work); flip True once cards have rotated.
    action_token_secret_env: str = "ZF_FEISHU_ACTION_TOKEN_SECRET"
    action_token_ttl_seconds: int = 86400
    require_signed_actions: bool = False


@dataclass
class FeishuRouteConfig:
    """Bind one Feishu chat to a ZaoFu target (doc 98 §4).

    target=channel → route to channel_id, default_member replies unless an
    @mention overrides; target=kanban_agent → Kanban Agent bridge;
    target=run_manager → resident Run Manager Agent bridge; target=worker →
    bridge an EXISTING worker session (no new tmux).
    """

    target: str = "channel"  # channel | kanban_agent | run_manager | worker | agent
    channel_id: str = ""
    default_member: str = ""
    worker_session_id: str = ""
    # target=agent (P0-2 lightweight direct-bind, doc 98 §10): a Feishu chat binds
    # directly to a coding agent + codebase, no channel/member to create.
    backend: str = ""   # claude-code | codex | ...
    cwd: str = ""       # codebase the agent works on


@dataclass
class IntegrationsConfig:
    openclaw_feishu_bridge: OpenClawFeishuBridgeConfig = field(
        default_factory=OpenClawFeishuBridgeConfig
    )
    feishu_identity: FeishuIdentityConfig = field(
        default_factory=FeishuIdentityConfig
    )
    # doc 98 §4: chat_id → route. Unmapped chat = no route (caller drops).
    feishu_routing: dict[str, FeishuRouteConfig] = field(default_factory=dict)


@dataclass
class AutopilotScheduleConfig:
    id: str = ""
    interval: str = "24h"
    action: str = "triage"


@dataclass
class AutopilotConfig:
    enabled: bool = False
    mode: str = "proposal_only"
    stale_after_hours: float = 24.0
    failed_event_window_hours: float = 72.0
    schedules: list[AutopilotScheduleConfig] = field(default_factory=list)


@dataclass
class AutoresearchTriggerPolicyConfig:
    enabled: bool = True
    mode: str = "supervised"  # off | manual | supervised | continuous
    repair_mode: str = "proposal_only"  # proposal_only | bounded_repair
    self_repair_backend: str = ""
    eligible_failure_classes: list[str] = field(default_factory=list)
    severity_min: str = "high"
    cooldown_minutes: int = 30
    max_triggers_per_hour: int = 2
    max_daily_runs: int = 5


@dataclass
class AutoresearchConfig:
    trigger_policy: AutoresearchTriggerPolicyConfig = field(
        default_factory=AutoresearchTriggerPolicyConfig
    )


@dataclass
class SkillSourceConfig:
    name: str = ""
    path: str = ""
    mode: str = "readonly"


@dataclass
class EventSigningConfig:
    """P2.1 — opt-in HMAC signing for events.jsonl.

    enabled=True requires the env var named in `secret_env` to hold a
    non-empty secret. start.py reads it and constructs an EventSigner,
    which EventLog uses to sign every appended event. Verification on
    read is automatic when EventLog has a signer.

    ``allow_unsigned_fallback`` controls what happens when signing is
    enabled but the secret is missing. Default False is fail-closed:
    misconfiguration aborts start/emit/hook-recv rather than silently
    downgrading to unsigned events. Set True only for legacy projects
    that still need the old warn-and-continue behavior; the factory
    prints a warning and returns an unsigned EventLog in that explicit mode.
    """
    enabled: bool = False
    secret_env: str = "ZF_EVENT_SECRET"
    allow_unsigned_fallback: bool = False


@dataclass
class SecurityConfig:
    event_signing: EventSigningConfig = field(default_factory=EventSigningConfig)


@dataclass
class SafetyConfig:
    tool_closure_enabled: bool = True


@dataclass
class GoalConfig:
    """133/G 批 goal 回路(灰度,默认全关 = 现行为零回归)。"""

    enabled: bool = False
    max_rescans: int = 5
    idle_progress_ticks: int = 3
    # U2:rework cap 指纹计数(同 findings 指纹才 +1;含驳回有效性前置)
    rework_fingerprint: bool = False
    quiescent_after_escalate: bool = True
    # 批B:lane 微环(拒收→活会话续改,不换代不重派)
    micro_loop: bool = False


@dataclass
class ZfConfig:
    version: str = "1.0"
    preset: str = ""
    project: ProjectConfig = field(default_factory=ProjectConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    constraints: ConstraintsConfig = field(default_factory=ConstraintsConfig)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    roles: list[RoleConfig] = field(default_factory=list)
    stage_labels: dict[str, str] = field(default_factory=dict)
    quality_gates: dict[str, QualityGateConfig] = field(default_factory=dict)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    # LH-2: rule-based SemanticDiscriminator opt-in.
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    integrations: IntegrationsConfig = field(default_factory=IntegrationsConfig)
    autopilot: AutopilotConfig = field(default_factory=AutopilotConfig)
    autoresearch: AutoresearchConfig = field(default_factory=AutoresearchConfig)
    skill_sources: list[SkillSourceConfig] = field(default_factory=list)
    # G-COST-BLOCK-1: global hard cost cap. None = no cap.
    global_budget_usd: float | None = None
    # Global kill switch for dispatch-time hard budget enforcement.
    # Cost tracking still records usage when disabled.
    budget_enforcement_enabled: bool = True
    # P0-8(审计 D9):tracker 读失败时的档位。默认 False 维持历史
    # fail-open(读失败按 $0 放行);True 时读失败按超额熔断——
    # "瞄具黑屏就停火",不再盲开。
    budget_fail_closed: bool = False
    goal: GoalConfig = field(default_factory=GoalConfig)

    def __post_init__(self) -> None:
        # G-INST-2: expand replicas into independent RoleConfig instances.
        # Each expanded instance has replicas=1 and a unique instance_id
        # like "dev-1" / "dev-2" / "dev-3". Idempotent: running this again
        # on already-expanded roles (replicas all == 1) is a no-op.
        #
        # P0-REPLICA-FIELDS-01: rebuild kwargs via dataclasses.fields()
        # so every RoleConfig field is auto-inherited. The previous
        # hand-written whitelist silently dropped 8 runtime-consumed
        # fields (plugins/skills/agent/max_rework_attempts/orphan_*/
        # drain_hold_seconds), causing replicas to behave unlike the
        # YAML they came from. Override only the 4 fields that must
        # change per replica; shallow-copy list fields so replicas
        # don't share mutable references.
        _OVERRIDABLE = {"backend", "backends", "instance_id", "replicas"}
        expanded: list[RoleConfig] = []
        for role in self.roles:
            if role.replicas <= 1:
                expanded.append(role)
                continue
            for i in range(1, role.replicas + 1):
                # B-MIXEDBACKEND-01: per-replica backend override when
                # role.backends is populated; otherwise the whole pool
                # shares role.backend (legacy behavior).
                per_replica_backend = (
                    role.backends[i - 1] if role.backends else role.backend
                )
                overrides: dict[str, object] = {
                    "backend": per_replica_backend,
                    "backends": [],
                    "instance_id": f"{role.name}-{i}",
                    "replicas": 1,
                }
                init_kwargs: dict[str, object] = {}
                for f in fields(role):
                    if f.name in _OVERRIDABLE:
                        init_kwargs[f.name] = overrides[f.name]
                    else:
                        val = getattr(role, f.name)
                        init_kwargs[f.name] = (
                            list(val) if isinstance(val, list) else val
                        )
                expanded.append(RoleConfig(**init_kwargs))
        self.roles = expanded
