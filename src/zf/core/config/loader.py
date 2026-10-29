"""Config loader — parse zf.yaml into ZfConfig with validation."""

from __future__ import annotations

import glob
import hashlib
import os
import re
import string
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import yaml


_ROLE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")
# Permission modes describe ZaoFu's logical role policy. Trusted provider
# processes use the shared full-access execution baseline; the kernel enforces
# write policy, artifact boundaries, and controlled actions independently.
_VALID_PERMISSION_MODES = ("bypass", "allowlist", "default", "restricted")
_VALID_TRANSPORTS = ("tmux", "stream-json")
_VALID_RUN_MANAGER_RESIDENT_SESSION_MODES = ("shared", "dedicated")
_VALID_ROLE_KINDS = ("auto", "writer", "reader")
_VALID_WORKDIR_MODES = ("dry-run", "worktree")
_VALID_SKILL_MATERIALIZE_MODES = ("copy", "symlink")
_VALID_SKILL_SOURCE_MODES = ("readonly",)
_VALID_CANDIDATE_STRATEGIES = ("cherry-pick",)
_VALID_REMOTE_POLICIES = ("local", "optional", "required", "local_only")
_VALID_SHIP_CANDIDATE_STRATEGIES = ("merge",)
_VALID_SHIP_TASK_STRATEGIES = ("cherry-pick",)
_VALID_STAR_TOPOLOGIES = ("fanout_reader", "fanout_writer_scoped")
_VALID_ATTEMPT_DOMAINS = ("plan", "task", "candidate", "gap", "recovery")
_VALID_RESULT_SEMANTICS = ("artifact_production", "subject_gate")
_VALID_FANOUT_ASSIGNMENT_STRATEGIES = ("static_index", "affinity_stage_slots")
_VALID_AFFINITY_STAGE_SLOTS = ("impl", "review", "verify")
_VALID_AUTOPILOT_MODES = ("proposal_only",)
_VALID_AUTOPILOT_ACTIONS = ("triage",)
_VALID_OPENCLAW_BINDING_MODES = ("remote_gateway",)
_VALID_OPENCLAW_WORKSPACE_POLICIES = ("isolated",)
_VALID_OPENCLAW_TOOL_PROFILES = ("safe", "readonly", "reviewer", "coding")
_VALID_WORKFLOW_ROUTE_KINDS = (
    "issue",
    "prd",
    "refactor",
    "feat",
    "workflow",
)
_VALID_WORKFLOW_ROUTE_ALIAS_TARGETS = (
    "issue",
    "prd",
    "refactor",
    "workflow",
)
_VALID_WORKFLOW_TIERS = ("micro", "light", "standard", "full")
_DESIGN_ROLE_NAMES = frozenset({"arch", "critic"})
_DESIGN_STAGE_NAMES = frozenset({"design", "design_critique"})
_LANE_RUNTIME_REWORK_EVENTS = frozenset(
    {
        "dev.failed",
    }
)
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_VALID_AGGREGATE_MODES = (
    "wait_for_all",
    "quorum",
    "any_failed_fail",
    "candidate_integration",
)
# 1206 Phase A: session.tmux_layout accepted values.
_VALID_TMUX_LAYOUTS = ("window_per_role", "pane_grid")
_VALID_AUTORESEARCH_TRIGGER_MODES = ("off", "manual", "supervised", "continuous")
_VALID_AUTORESEARCH_REPAIR_MODES = ("proposal_only", "bounded_repair")
_VALID_REPAIR_BACKENDS = ("codex", "claude-code")
_VALID_FEISHU_INBOUND_MODES = ("bridge",)
_VALID_FEISHU_PROJECTION_BACKENDS = ("lark-cli",)
_VALID_EVOLUTION_MODES = ("evaluate_only", "auto_low_risk")
_DEFAULT_EVOLUTION_AUTO_ASSET_KINDS = (
    "memory_entry",
    "runbook",
    "regression_fixture",
)
_VALID_EVOLUTION_AUTO_ASSET_KINDS = (
    *_DEFAULT_EVOLUTION_AUTO_ASSET_KINDS,
    "skill_prompt",
)
_VALID_EXECUTION_ROUTE_TRIGGERS = (
    "provider_unavailable",
    "provider_rate_limited",
    "provider_capability_mismatch",
    "provider_context_exhausted",
)
_VALID_SEVERITIES = ("low", "medium", "high", "critical")
_VALID_PROVIDER_TELEMETRY_MODES = ("off", "managed", "host_managed")
_ENV_SUB_RE = re.compile(
    r"\$\{(?P<name>[A-Z_][A-Z0-9_]*)(?::-(?P<default>[^}]*))?\}"
)

from zf.core.config.schema import (  # noqa: E402
    GoalConfig,
    ZfConfig,
    ProjectConfig,
    SessionConfig,
    OrchestratorConfig,
    LoopConfig,
    ConstraintsConfig,
    ExecutionConfig,
    ProviderSessionConfig,
    ExecutionProfileConfig,
    ExecutionProfileLimitsConfig,
    RoleConfig,
    RoleAutoscaleConfig,
    RoleLifecycleConfig,
    WakeExtensionConfig,
    WakeExtensionsConfig,
    WorkflowConfig,
    WorkflowOrchestrationConfig,
    WorkflowOrchestrationFlowPolicyConfig,
    WorkflowDagConfig,
    WorkflowKindRouteConfig,
    WorkflowWorkUnitsConfig,
    WorkflowSplitQualityConfig,
    WorkflowAdmissionReplanConfig,
    WorkflowRunAdmissionConfig,
    WorkflowRunLimitsConfig,
    WorkflowTaskAttemptConfig,
    WorkflowCompletionAuditConfig,
    WorkflowResumePacketConfig,
    WorkflowIntegrationConfig,
    WorkflowStrictTriggersConfig,
    WorkflowFastPathConfig,
    WorkflowReplanEvalConfig,
    QualityGateConfig,
    SecurityConfig,
    EventSigningConfig,
    SafetyConfig,
    CostConfig,
    ObservabilityConfig,
    OperationsMetricsConfig,
    ObservabilityAlertConfig,
    OtlpExporterConfig,
    ProviderTelemetryConfig,
    RuntimeLogsConfig,
    VerificationConfig,
    ContractDConfig,
    SemanticDConfig,
    ScopeVerificationConfig,
    RuntimeRuleDConfig,
    EventSchemaValidationConfig,
    RuntimeConfig,
    RuntimeAutoresearchResidentConfig,
    RuntimeEvolutionConfig,
    ExecutionRouteConfig,
    RuntimeExecutionRoutingConfig,
    WorkdirConfig,
    GitIsolationConfig,
    RuntimeSkillsConfig,
    RuntimeRunManagerConfig,
    RuntimeRunManagerReflectConfig,
    RuntimeRunManagerResidentAgentConfig,
    RuntimeRunManagerSourceRepairConfig,
    RuntimeFeishuInboundConfig,
    RuntimeFeishuProjectionConfig,
    RuntimeWebTerminalConfig,
    ProvidersConfig,
    OpenClawProviderConfig,
    OpenClawRemoteBindingConfig,
    IntegrationsConfig,
    ChannelAgentProfileConfig,
    ChannelConfig,
    DEFAULT_CHANNEL_SEMANTIC_SOURCE_CHARS,
    MAX_CHANNEL_SEMANTIC_SOURCE_CHARS,
    FeishuIdentityConfig,
    FeishuIdentityUserConfig,
    FeishuProjectGroupConfig,
    FeishuRouteConfig,
    OpenClawFeishuBridgeBindingConfig,
    OpenClawFeishuBridgeConfig,
    OpenClawFeishuBridgeFeishuConfig,
    OpenClawFeishuBridgeInboundConfig,
    OpenClawFeishuBridgeOpenClawConfig,
    OpenClawFeishuBridgeOutboundConfig,
    OpenClawFeishuBridgeZaofuConfig,
    AutopilotConfig,
    AutopilotScheduleConfig,
    AutoresearchConfig,
    AutoresearchTriggerPolicyConfig,
    SkillSourceConfig,
    WorkflowInlineOverrides,
    WorkflowStageBackedgeConfig,
    WorkflowStageConfig,
    WorkflowPortConfig,
    WorkflowStageCriteriaConfig,
    WorkflowStageOutputConfig,
    WorkflowStageRetryPolicyConfig,
    FanoutAggregateConfig,
    FanoutAssignmentConfig,
    FanoutChildConfig,
    WorkflowAffinityLaneConfig,
    WorkflowAffinityLaneProfileConfig,
    WorkflowAffinityQueueConfig,
)


class ConfigError(Exception):
    pass


def _load_dotenv(path: Path) -> dict[str, str]:
    """Load a small .env file without mutating process environment.

    Shell env wins over .env in _config_env_map(). The file is only a
    variable source for zf.yaml interpolation, not a second control plane.
    """
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


def _config_env_map(config_path: Path) -> dict[str, str]:
    env = _load_dotenv(config_path.parent / ".env")
    env.update(os.environ)
    _apply_feishu_env_aliases(env)
    return env


def _apply_feishu_env_aliases(env: dict[str, str]) -> None:
    aliases = {
        "ZF_LEADER_FEISHU_OPENID": ("FEISHU_OPENID", "FEISHU_USER_OPENID"),
        "ZF_PM_FEISHU_OPENID": ("FEISHU_PM_OPENID",),
        "FEISHU_RUNM": ("FEISHU_RUN_MANAGER_APP_ID", "FEISHU_ARCHITECT_APP_ID"),
        "FEISHU_RUNM_SECRET": (
            "FEISHU_RUN_MANAGER_APP_SECRET",
            "FEISHU_ARCHITECT_APP_SECRET",
        ),
        "FEISHU_KANBAN": ("FEISHU_KANBAN_APP_ID", "FEISHU_PRODUCT_MANAGER_APP_ID"),
        "FEISHU_KANBAN_SECRET": (
            "FEISHU_KANBAN_APP_SECRET",
            "FEISHU_PRODUCT_MANAGER_APP_SECRET",
        ),
    }
    for canonical, candidates in aliases.items():
        if env.get(canonical):
            continue
        for candidate in candidates:
            value = env.get(candidate)
            if value:
                env[canonical] = value
                break


def _expand_env_vars(text: str, env: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        default = match.group("default")
        value = env.get(name)
        if value is None or value == "":
            if default is not None:
                return default
            raise ConfigError(f"Missing environment variable {name!r} in zf.yaml")
        return value

    return _ENV_SUB_RE.sub(replace, text)


def _bool_value(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return bool(value)


def _string_list(value: object, *, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if not isinstance(value, list):
        raise ConfigError("expected a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


# 2026-06-10 review P1-6: enum *values* were already fail-closed, but key
# *names* were not — `harnes_profile:` silently reverted to baseline and
# `zf validate` stayed green. Reject unknown keys at the three levels an
# operator typo bites hardest (top-level / workflow / role).
_KNOWN_TOP_LEVEL_KEYS = frozenset({
    "version", "preset", "project", "session", "orchestrator", "constraints",
    "workflow", "roles", "stage_labels", "quality_gates", "security",
    "safety", "verification", "runtime", "providers", "integrations",
    "autopilot", "autoresearch", "skill_sources", "global_budget_usd",
    "budget_enforcement", "budget_enforcement_enabled",
    # P0-8 存量遗漏(与 attempt_lease_grace_s 同族白名单坑)+ 133/G 批
    "budget_fail_closed", "goal", "channel", "cost", "observability",
})
_KNOWN_COST_KEYS = frozenset({
    "pricing_catalog_url",
    "pricing_refresh_ttl_seconds",
    "pricing_refresh_timeout_seconds",
    "backend_accounting_modes",
})
_KNOWN_CHANNEL_KEYS = frozenset({"agent_profiles", "semantic_source_max_chars", "codex_context_window_tokens", "provider_timeout_seconds"})
_KNOWN_CHANNEL_AGENT_PROFILE_KEYS = frozenset({
    "revision",
    "persona",
    "display_name",
    "channel_role",
    "provider",
    "backend",
    "model",
    "role_context_ref",
    "skill_refs",
    "visibility_ceiling",
    "permission_ceiling",
    "lifecycle",
})
_KNOWN_WORKFLOW_KEYS = frozenset({
    "attempt_lease_grace_s",  # 131-P2-3 lease 宽限(r6 首用)
    "harness_profile", "affinity_lanes", "stages", "rework_routing",
    "gan_rounds", "event_actions", "wake_extensions", "dag",
    "inline_overrides", "work_units", "completion_audit", "resume_packet",
    "integration", "strict_triggers", "fast_path", "replan_eval",
    "pipelines", "admission_replan", "plan_approval", "_flow_metadata",
    "run_admission",
    "task_attempt",
    "_flow_metadata_by_kind", "_generic_workflows",
    "kind_routes",
    "execution_profiles",
    "run_limits",
    "allow_unverified_candidate",  # ⑤c 合并候选树门显式豁免(2026-07-08)
    "candidate_quality_source",
    "impl_self_check_required",
    "orchestration",
})
_KNOWN_ROLE_KEYS = frozenset({
    "name", "backend", "backends", "role_kind", "flow_kind", "model",
    "model_reasoning_effort", "allowed_tools",
    "permission_mode", "transport", "stuck_threshold_seconds", "instance_id",
    "replicas", "context_window_tokens", "context_warning_threshold",
    "context_compact_threshold", "context_hard_cap", "recycle_threshold",
    "recycle_hard_cap", "max_rework_attempts", "orphan_warning_seconds",
    "orphan_escalate_seconds", "drain_hold_seconds",
    "spawn_ready_timeout_seconds", "budget_usd", "autoscale", "constraints",
    "execution", "stages", "triggers", "publishes", "guardrails", "plugins", "skills",
    "agent", "provider_session", "lifecycle",
})
_KNOWN_PROVIDER_SESSION_KEYS = frozenset({
    "effort", "agent", "max_parallel_agents", "native_delegation",
})
_KNOWN_ROLE_LIFECYCLE_KEYS = frozenset({
    "mode", "idle_seconds", "cooldown_seconds",
    "preserve_session", "preserve_workdir", "reader_context_scope",
})


def _reject_unknown_keys(
    data: dict, known: frozenset[str], context: str,
) -> None:
    # 下划线前缀键 = YAML anchor 定义区约定(如 `_role_defaults: &defaults`),
    # loader 不消费其内容,仅供 `<<: *defaults` 复用——显式豁免,其余未知键
    # 仍 fail-closed(doc 90 实证:9 个 role 的通用字段 anchor 化)。
    unknown = sorted(
        str(k) for k in data
        if str(k) not in known and not str(k).startswith("_")
    )
    if not unknown:
        return
    import difflib
    hints = []
    for key in unknown:
        close = difflib.get_close_matches(key, known, n=1)
        hints.append(f"{key!r}" + (f" (did you mean {close[0]!r}?)" if close else ""))
    raise ConfigError(
        f"Unknown key(s) in {context}: {', '.join(hints)}. "
        f"Typo'd keys silently fall back to defaults, so they are rejected."
    )


def _parse_project_setup_script(project_data: dict) -> str:
    """project.scripts.setup:项目自声明的 worktree 就绪脚本,可选。"""
    if "setup_script" in project_data:
        raise ConfigError(
            "project.setup_script is not a valid config key; "
            "use project.scripts.setup"
        )
    scripts = project_data.get("scripts") or {}
    if not isinstance(scripts, dict):
        raise ConfigError("project.scripts must be a mapping")
    _reject_unknown_keys(scripts, frozenset({"setup"}), "project.scripts")
    setup = scripts.get("setup", "")
    if setup and not isinstance(setup, str):
        raise ConfigError("project.scripts.setup must be a string")
    return str(setup or "").strip()


def _parse_project_description(project_data: dict) -> str:
    description = project_data.get("description", "")
    if description and not isinstance(description, str):
        raise ConfigError("project.description must be a string")
    return str(description or "").strip()


def _build_constraints(data: dict | None) -> ConstraintsConfig:
    if not data:
        return ConstraintsConfig()
    return ConstraintsConfig(
        allowed_paths=data.get("allowed_paths", []),
        blocked_paths=data.get("blocked_paths", []),
        max_steps=data.get("max_steps", 0),
    )


def _build_role_autoscale(data: object, *, role_name: str) -> RoleAutoscaleConfig:
    if data in (None, ""):
        return RoleAutoscaleConfig()
    if not isinstance(data, dict):
        raise ConfigError(f"role {role_name!r}: autoscale must be a mapping")
    try:
        return RoleAutoscaleConfig(
            enabled=bool(data.get("enabled", False)),
            min_replicas=int(data.get("min_replicas", 1)),
            max_replicas=int(data.get("max_replicas", 1)),
            target_ready_tasks_per_worker=int(
                data.get("target_ready_tasks_per_worker", 1)
            ),
            scale_up_pending_seconds=float(
                data.get("scale_up_pending_seconds", 0.0)
            ),
            scale_down_idle_seconds=float(
                data.get("scale_down_idle_seconds", 900.0)
            ),
            cooldown_seconds=float(data.get("cooldown_seconds", 180.0)),
            drain_before_stop=bool(data.get("drain_before_stop", True)),
        )
    except ValueError as exc:
        raise ConfigError(f"role {role_name!r}: invalid autoscale: {exc}") from exc


def _build_provider_session(
    data: object,
    *,
    role_name: str,
) -> ProviderSessionConfig | None:
    if data in (None, ""):
        return None
    if not isinstance(data, dict):
        raise ConfigError(f"role {role_name!r}: provider_session must be a mapping")
    _reject_unknown_keys(
        data,
        _KNOWN_PROVIDER_SESSION_KEYS,
        f"role {role_name!r}.provider_session",
    )
    if not data:
        return None
    effort = data.get("effort", "")
    agent = data.get("agent", "")
    if effort is not None and not isinstance(effort, str):
        raise ConfigError(
            f"role {role_name!r}: provider_session.effort must be a string"
        )
    if agent is not None and not isinstance(agent, str):
        raise ConfigError(
            f"role {role_name!r}: provider_session.agent must be a string"
        )
    parallel = data.get("max_parallel_agents")
    if isinstance(parallel, bool) or (
        parallel is not None and not isinstance(parallel, int)
    ):
        raise ConfigError(
            f"role {role_name!r}: provider_session.max_parallel_agents "
            "must be an integer"
        )
    try:
        return ProviderSessionConfig(
            effort=str(effort or "").strip(),
            agent=str(agent or "").strip(),
            max_parallel_agents=parallel,
            native_delegation=data.get("native_delegation"),
        )
    except ValueError as exc:
        raise ConfigError(
            f"role {role_name!r}: invalid provider_session: {exc}"
        ) from exc


def _build_role_lifecycle(
    data: object,
    *,
    role_name: str,
) -> RoleLifecycleConfig:
    if data in (None, ""):
        return RoleLifecycleConfig()
    if not isinstance(data, dict):
        raise ConfigError(f"role {role_name!r}: lifecycle must be a mapping")
    _reject_unknown_keys(
        data,
        _KNOWN_ROLE_LIFECYCLE_KEYS,
        f"role {role_name!r}.lifecycle",
    )
    try:
        return RoleLifecycleConfig(
            reader_context_scope=data.get("reader_context_scope", "workflow"),
            mode=str(data.get("mode", "eager") or "eager").strip(),
            idle_seconds=float(data.get("idle_seconds", 900.0)),
            cooldown_seconds=float(data.get("cooldown_seconds", 180.0)),
            preserve_session=_bool_value(
                data.get("preserve_session"),
                default=True,
            ),
            preserve_workdir=_bool_value(
                data.get("preserve_workdir"),
                default=True,
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"role {role_name!r}: invalid lifecycle: {exc}"
        ) from exc


def _build_session(data: dict | None) -> SessionConfig:
    """Parse ``session:`` block. Defaults keep existing yamls unchanged.

    1206 Phase A: validate ``tmux_layout`` against the allowed set.
    """
    data = data or {}
    # Default pane_grid (2026-07-09); still overridable per config/profile with
    # an explicit session.tmux_layout (e.g. window_per_role for legacy).
    layout = data.get("tmux_layout", "pane_grid")
    if layout not in _VALID_TMUX_LAYOUTS:
        raise ConfigError(
            f"Invalid session.tmux_layout {layout!r}: "
            f"must be one of {_VALID_TMUX_LAYOUTS}"
        )
    return SessionConfig(
        tmux_session=data.get("tmux_session", "zf"),
        tmux_layout=layout,
    )


def _build_wake_extensions(data: dict | None) -> WakeExtensionsConfig:
    """P3 (2026-04-20): parse workflow.wake_extensions from yaml."""
    if not data:
        return WakeExtensionsConfig()

    def _one(section: dict | None) -> WakeExtensionConfig:
        if not section:
            return WakeExtensionConfig()
        return WakeExtensionConfig(
            enabled=bool(section.get("enabled", False)),
            include=list(section.get("include", []) or []),
            rate_limit_per_minute=int(section.get("rate_limit_per_minute", 0) or 0),
        )

    return WakeExtensionsConfig(
        hooks=_one(data.get("hooks")),
        agent=_one(data.get("agent")),
    )


def _build_inline_overrides(data: dict | None) -> "WorkflowInlineOverrides":
    """ZF-LH-INLINE-001 (doc 26 §3.3): parse
    ``workflow.inline_overrides`` from yaml. Defaults to disabled so
    old yamls keep working unchanged."""
    from zf.core.config.schema import WorkflowInlineOverrides

    if not isinstance(data, dict):
        return WorkflowInlineOverrides()
    raw_patterns = data.get("patterns") or {}
    patterns: dict[str, list[str]] = {}
    if isinstance(raw_patterns, dict):
        for key, value in raw_patterns.items():
            if isinstance(value, list):
                patterns[str(key)] = [
                    str(item) for item in value if isinstance(item, str)
                ]
    return WorkflowInlineOverrides(
        enabled=bool(data.get("enabled", False)),
        patterns=patterns,
        audit_event=str(
            data.get("audit_event") or "workflow.inline_override"
        ),
    )


def _parse_plan_approval_enabled(raw: object, *, default: bool = False) -> bool:
    """B14/B-93-02: ``plan_approval: true`` 或 ``{enabled: true}``。

    doc93 §8:baseline 缺省 false / strict|release 缺省 true。``default`` 由
    调用方按 harness_profile 传入,只在 yaml **未显式声明** plan_approval 时
    生效;显式值(bool 或 {enabled}）始终覆盖 profile 默认。
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, dict):
        return bool(raw.get("enabled", default))
    return default


def _build_workflow_dag(data: dict | None) -> WorkflowDagConfig:
    """P2/K4 (docs/impl/22): parse workflow.dag from yaml.

    Defaults to a disabled DagConfig so old yamls without ``workflow.dag``
    keep working with no enforcement. Setting ``workflow.dag.enabled: true``
    + ``dev_requires_orchestrator_backlog: true`` activates the
    required_backlog_refs preflight (see P2/K4 in contract_validation.py).
    """
    if not isinstance(data, dict):
        return WorkflowDagConfig()
    return WorkflowDagConfig(
        enabled=bool(data.get("enabled", False)),
        graph_static_gate_action=bool(data.get("graph_static_gate_action", False)),
        graph_review_test_judge_reconcile=bool(
            data.get("graph_review_test_judge_reconcile", False),
        ),
        default_gate_level=str(data.get("default_gate_level", "permissive")),
        dev_requires_orchestrator_backlog=bool(
            data.get("dev_requires_orchestrator_backlog", False),
        ),
        design_to_backlog_owner=str(data.get("design_to_backlog_owner", "")),
        design_events=dict(data.get("design_events", {}) or {}),
        required_backlog_refs=list(data.get("required_backlog_refs", []) or []),
        stage_order=list(data.get("stage_order", []) or []),
        # TR-EVENT-SCHEMA-LOCK-001 step 1/3: parse event_schemas dumb-as-dict
        # — EventSchemaRegistry interprets the shape at validation time.
        event_schemas=dict(data.get("event_schemas", {}) or {}),
        event_schemas_by_kind=dict(
            data.get("event_schemas_by_kind", {}) or {},
        ),
        schema_profile=str(data.get("schema_profile", "") or ""),
        external_triggers=[
            str(t) for t in data.get("external_triggers", []) or []
        ],
    )


def _build_admission_replan(data) -> WorkflowAdmissionReplanConfig:
    """R28: parse ``workflow.admission_replan`` (default off = no_action 现状)."""
    if not isinstance(data, dict):
        return WorkflowAdmissionReplanConfig()
    return WorkflowAdmissionReplanConfig(
        enabled=bool(data.get("enabled", False)),
        resynth_trigger=str(data.get("resynth_trigger", "") or "").strip(),
    )


_ORCHESTRATION_MODES = frozenset({"exception_advisor", "semantic_control"})
_ORCHESTRATION_CHECKPOINTS = frozenset({
    "pre_impl",
    "plan_candidate",
    "stage_barrier",
    "semantic_failure",
    "goal_revision",
    "pre_closeout",
    "owner_delivery",
})
_ORCHESTRATION_CHECKPOINT_POLICIES = frozenset({"shadow", "blocking"})
_ORCHESTRATION_FLOW_KINDS = frozenset({
    "issue",
    "prd",
    "refactor",
    "workflow",
    "research",
})
_ORCHESTRATION_BLOCKING_PILOT_PATHS = frozenset({
    "workflow.orchestration.flow_policies.issue",
    "workflow.orchestration.flow_policies.prd",
    "workflow.orchestration.flow_policies.refactor",
})


def _build_orchestration_flow_policy(
    data: object,
    *,
    path: str,
) -> WorkflowOrchestrationFlowPolicyConfig:
    if data in (None, ""):
        return WorkflowOrchestrationFlowPolicyConfig()
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping")
    known = {
        "mode",
        "checkpoints",
        "checkpoint_policies",
        "pilot_id",
        "shadow_sample_percent",
        "autonomous_actions",
        "review_blocked_tasks",
    }
    unknown = sorted(str(key) for key in data if str(key) not in known)
    if unknown:
        raise ConfigError(
            f"{path} contains unknown key(s): " + ", ".join(unknown)
        )
    mode = str(data.get("mode") or "exception_advisor").strip().lower()
    if mode not in _ORCHESTRATION_MODES:
        raise ConfigError(
            f"{path}.mode must be exception_advisor or semantic_control; "
            f"got {mode!r}"
        )
    checkpoints_raw = data.get("checkpoints", []) or []
    if not isinstance(checkpoints_raw, list):
        raise ConfigError(f"{path}.checkpoints must be a list")
    checkpoints = [
        str(value or "").strip().lower() for value in checkpoints_raw
    ]
    if any(not value for value in checkpoints):
        raise ConfigError(f"{path}.checkpoints cannot contain empty values")
    invalid_checkpoints = sorted(set(checkpoints) - _ORCHESTRATION_CHECKPOINTS)
    if invalid_checkpoints:
        raise ConfigError(
            f"{path}.checkpoints contains unsupported value(s): "
            + ", ".join(invalid_checkpoints)
        )
    if len(checkpoints) != len(set(checkpoints)):
        raise ConfigError(f"{path}.checkpoints cannot contain duplicates")
    policies_raw = data.get("checkpoint_policies", {}) or {}
    if not isinstance(policies_raw, dict):
        raise ConfigError(f"{path}.checkpoint_policies must be a mapping")
    policies = {
        str(key or "").strip().lower(): str(value or "").strip().lower()
        for key, value in policies_raw.items()
    }
    invalid_policy_checkpoints = sorted(
        set(policies) - _ORCHESTRATION_CHECKPOINTS
    )
    if invalid_policy_checkpoints:
        raise ConfigError(
            f"{path}.checkpoint_policies contains unsupported checkpoint(s): "
            + ", ".join(invalid_policy_checkpoints)
        )
    invalid_policies = sorted({
        value
        for value in policies.values()
        if value not in _ORCHESTRATION_CHECKPOINT_POLICIES
    })
    if invalid_policies:
        raise ConfigError(
            f"{path} checkpoint policy must be shadow or blocking; got "
            + ", ".join(invalid_policies)
        )
    undeclared = sorted(set(policies) - set(checkpoints))
    if undeclared:
        raise ConfigError(
            f"{path} checkpoint policy requires the checkpoint to be declared: "
            + ", ".join(undeclared)
        )
    if mode == "exception_advisor" and checkpoints:
        raise ConfigError(f"{path}.checkpoints require mode=semantic_control")
    if mode == "semantic_control" and not checkpoints:
        raise ConfigError(
            f"{path} mode=semantic_control requires at least one checkpoint"
        )
    pilot_id = str(data.get("pilot_id") or "").strip()
    try:
        shadow_sample_percent = int(data.get("shadow_sample_percent", 100))
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"{path}.shadow_sample_percent must be an integer"
        ) from exc
    if not 0 <= shadow_sample_percent <= 100:
        raise ConfigError(
            f"{path}.shadow_sample_percent must be between 0 and 100"
        )
    blocking = sorted(
        checkpoint
        for checkpoint in checkpoints
        if policies.get(checkpoint, "blocking") == "blocking"
    )
    if blocking and not pilot_id:
        raise ConfigError(
            f"{path} blocking checkpoints require an explicit pilot_id"
        )
    if blocking and path not in _ORCHESTRATION_BLOCKING_PILOT_PATHS:
        raise ConfigError(
            f"{path} blocking rollout is restricted to an explicit full "
            "Product Flow pilot"
        )
    if set(blocking) - {"plan_candidate", "pre_impl", "semantic_failure", "pre_closeout"}:
        raise ConfigError(
            f"{path} rollout permits plan_candidate, pre_impl, semantic_failure and pre_closeout blocking; "
            f"got {', '.join(blocking)}"
        )
    if pilot_id and not blocking:
        raise ConfigError(
            f"{path}.pilot_id requires one blocking checkpoint"
        )
    actions = data.get("autonomous_actions")
    if actions is not None:
        if (not isinstance(actions, list)
                or any(not isinstance(item, str) for item in actions)
                or set(actions) - {"delegate", "continue", "rework"}
                or len(actions) != len(set(actions))):
            raise ConfigError(f"{path}.autonomous_actions must list unique delegate/continue/rework actions")
        if mode != "semantic_control":
            raise ConfigError(f"{path}.autonomous_actions requires semantic_control")
    review_blocked = data.get("review_blocked_tasks", False)
    if not isinstance(review_blocked, bool):
        raise ConfigError(f"{path}.review_blocked_tasks must be boolean")
    if review_blocked and (actions is None or "semantic_failure" not in checkpoints):
        raise ConfigError(f"{path}.review_blocked_tasks requires autonomous_actions and semantic_failure")
    return WorkflowOrchestrationFlowPolicyConfig(
        mode=mode,
        checkpoints=checkpoints,
        checkpoint_policies=policies,
        pilot_id=pilot_id,
        shadow_sample_percent=shadow_sample_percent,
        autonomous_actions=actions,
        review_blocked_tasks=review_blocked,
    )


def _validate_orchestration_blocking_pilot_tiers(config: ZfConfig) -> None:
    """Keep semantic blocking out of micro/light and non-Product routes."""
    orchestration = config.workflow.orchestration
    for flow_kind, policy in orchestration.flow_policies.items():
        blocking = [
            checkpoint
            for checkpoint in policy.checkpoints
            if policy.checkpoint_policies.get(checkpoint, "blocking")
            == "blocking"
        ]
        if not blocking:
            continue
        route = config.workflow.kind_routes.get(flow_kind)
        tier = str(getattr(route, "default_tier", "") or "").strip().lower()
        if tier not in {"standard", "full"}:
            raise ConfigError(
                "workflow.orchestration.flow_policies."
                f"{flow_kind} blocking rollout requires a standard or full "
                "kind route; micro/light/unspecified routes stay advisory"
            )


def _build_workflow_orchestration(
    data: object,
) -> WorkflowOrchestrationConfig:
    if data in (None, ""):
        return WorkflowOrchestrationConfig()
    if not isinstance(data, dict):
        raise ConfigError("workflow.orchestration must be a mapping")
    known = {
        "mode",
        "checkpoints",
        "checkpoint_policies",
        "pilot_id",
        "shadow_sample_percent",
        "autonomous_actions",
        "review_blocked_tasks",
        "flow_policies",
        "oa_profile",
        "normal_frontier_owner",
        "max_plan_revisions",
        "no_progress_limit",
    }
    unknown = sorted(str(key) for key in data if str(key) not in known)
    if unknown:
        raise ConfigError(
            "workflow.orchestration contains unknown key(s): "
            + ", ".join(unknown)
        )
    root = _build_orchestration_flow_policy(
        {
            key: data[key]
            for key in (
                "mode",
                "checkpoints",
                "checkpoint_policies",
                "pilot_id",
                "shadow_sample_percent",
                "autonomous_actions",
                "review_blocked_tasks",
            )
            if key in data
        },
        path="workflow.orchestration",
    )
    flow_policies_raw = data.get("flow_policies", {}) or {}
    if not isinstance(flow_policies_raw, dict):
        raise ConfigError("workflow.orchestration.flow_policies must be a mapping")
    invalid_flow_kinds = sorted(
        str(key) for key in flow_policies_raw
        if str(key).strip().lower() not in _ORCHESTRATION_FLOW_KINDS
    )
    if invalid_flow_kinds:
        raise ConfigError(
            "workflow.orchestration.flow_policies contains unsupported flow "
            "kind(s): " + ", ".join(invalid_flow_kinds)
        )
    flow_policies = {
        str(key).strip().lower(): _build_orchestration_flow_policy(
            value,
            path=(
                "workflow.orchestration.flow_policies."
                + str(key).strip().lower()
            ),
        )
        for key, value in flow_policies_raw.items()
    }
    try:
        max_plan_revisions = int(data.get("max_plan_revisions", 2))
        no_progress_limit = int(data.get("no_progress_limit", 2))
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            "workflow.orchestration revision limits must be integers"
        ) from exc
    if max_plan_revisions < 1 or no_progress_limit < 1:
        raise ConfigError(
            "workflow.orchestration revision limits must be >= 1"
        )
    oa_profile = str(data.get("oa_profile") or "").strip()
    normal_frontier_owner = str(
        data.get("normal_frontier_owner") or "run_manager"
    ).strip().lower()
    if normal_frontier_owner not in {"run_manager", "wrc"}:
        raise ConfigError(
            "workflow.orchestration.normal_frontier_owner must be "
            "run_manager or wrc"
        )
    return WorkflowOrchestrationConfig(
        mode=root.mode,
        checkpoints=root.checkpoints,
        checkpoint_policies=root.checkpoint_policies,
        pilot_id=root.pilot_id,
        shadow_sample_percent=root.shadow_sample_percent,
        autonomous_actions=root.autonomous_actions,
        review_blocked_tasks=root.review_blocked_tasks,
        flow_policies=flow_policies,
        oa_profile=oa_profile,
        normal_frontier_owner=normal_frontier_owner,
        max_plan_revisions=max_plan_revisions,
        no_progress_limit=no_progress_limit,
    )


def _build_run_admission(data: object) -> WorkflowRunAdmissionConfig:
    """Parse the versioned Project Run admission policy."""

    if data in (None, ""):
        return WorkflowRunAdmissionConfig()
    if not isinstance(data, dict):
        raise ConfigError("workflow.run_admission must be a mapping")
    known = {"version", "mode", "max_active_runs"}
    unknown = sorted(str(key) for key in data if str(key) not in known)
    if unknown:
        raise ConfigError(
            "workflow.run_admission contains unknown key(s): "
            + ", ".join(unknown)
        )
    mode = str(data.get("mode") or "serial").strip().lower()
    default_limit = 1 if mode == "serial" else 2
    try:
        return WorkflowRunAdmissionConfig(
            version=str(data.get("version") or "v1").strip().lower(),
            mode=mode,
            max_active_runs=int(
                data.get("max_active_runs")
                if data.get("max_active_runs") is not None
                else default_limit
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid workflow.run_admission: {exc}") from exc


def _build_task_attempt(data: object) -> WorkflowTaskAttemptConfig:
    """Parse scheduler-owned TaskAttempt rollout policy."""

    if data in (None, ""):
        return WorkflowTaskAttemptConfig()
    if not isinstance(data, dict):
        raise ConfigError("workflow.task_attempt must be a mapping")
    known = {"version", "mode", "max_attempts"}
    unknown = sorted(str(key) for key in data if str(key) not in known)
    if unknown:
        raise ConfigError(
            "workflow.task_attempt contains unknown key(s): "
            + ", ".join(unknown)
        )
    try:
        return WorkflowTaskAttemptConfig(
            version=str(data.get("version") or "v1").strip().lower(),
            mode=str(data.get("mode") or "shadow").strip().lower(),
            max_attempts=int(
                data.get("max_attempts")
                if data.get("max_attempts") is not None
                else 3
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid workflow.task_attempt: {exc}") from exc


def _build_workflow_kind_routes(
    data: object,
    *,
    stage_ids: set[str],
) -> dict[str, WorkflowKindRouteConfig]:
    """doc133: parse deterministic request kind -> workflow stage routes."""
    if data in (None, ""):
        return {}
    if not isinstance(data, dict):
        raise ConfigError("workflow.kind_routes must be a mapping")
    routes: dict[str, WorkflowKindRouteConfig] = {}
    for raw_kind, raw_route in data.items():
        kind = str(raw_kind or "").strip().lower()
        if kind not in _VALID_WORKFLOW_ROUTE_KINDS:
            raise ConfigError(
                "workflow.kind_routes keys must be one of "
                + ", ".join(_VALID_WORKFLOW_ROUTE_KINDS)
                + f"; got {kind!r}"
            )
        if not isinstance(raw_route, dict):
            raise ConfigError(f"workflow.kind_routes.{kind} must be a mapping")
        alias = str(raw_route.get("alias") or "").strip().lower()
        pattern_id = str(raw_route.get("pattern_id") or "").strip()
        if alias:
            if alias not in _VALID_WORKFLOW_ROUTE_ALIAS_TARGETS:
                raise ConfigError(
                    f"workflow.kind_routes.{kind}.alias must be one of "
                    + ", ".join(_VALID_WORKFLOW_ROUTE_ALIAS_TARGETS)
                )
            if pattern_id:
                raise ConfigError(
                    f"workflow.kind_routes.{kind} cannot set both alias and pattern_id"
                )
        tiers_raw = raw_route.get("tier_routes")
        if tiers_raw is None:
            tiers_raw = raw_route.get("tiers")
        if tiers_raw in (None, ""):
            tiers_raw = {}
        if not isinstance(tiers_raw, dict):
            raise ConfigError(
                f"workflow.kind_routes.{kind}.tier_routes must be a mapping"
            )
        tier_routes = {
            str(tier or "").strip().lower(): str(target or "").strip()
            for tier, target in tiers_raw.items()
            if str(tier or "").strip() or str(target or "").strip()
        }
        default_tier = str(raw_route.get("default_tier") or "").strip().lower()
        try:
            route = WorkflowKindRouteConfig(
                pattern_id=pattern_id,
                alias=alias,
                default_tier=default_tier,
                tier_routes=tier_routes,
            )
        except ValueError as exc:
            raise ConfigError(f"Invalid workflow.kind_routes.{kind}: {exc}") from exc
        if not route.alias:
            default_target = (
                route.tier_routes.get(route.default_tier, "")
                if route.default_tier
                else ""
            )
            if not route.pattern_id and not default_target:
                raise ConfigError(
                    f"workflow.kind_routes.{kind} must declare pattern_id or "
                    "a tier_routes entry for default_tier"
                )
        for field_name, target in [
            ("pattern_id", route.pattern_id),
            *[(f"tier_routes.{tier}", target) for tier, target in route.tier_routes.items()],
        ]:
            if target and target not in stage_ids:
                raise ConfigError(
                    f"workflow.kind_routes.{kind}.{field_name} references "
                    f"unknown workflow stage {target!r}"
                )
        routes[kind] = route
    for kind, route in routes.items():
        if route.alias and route.alias not in routes:
            raise ConfigError(
                f"workflow.kind_routes.{kind}.alias references missing "
                f"route {route.alias!r}"
            )
    return routes


def _build_flow_metadata_by_kind(data: object) -> dict[str, dict]:
    if data in (None, ""):
        return {}
    if not isinstance(data, dict):
        raise ConfigError("workflow._flow_metadata_by_kind must be a mapping")
    result: dict[str, dict] = {}
    for raw_kind, raw_metadata in data.items():
        kind = str(raw_kind or "").strip().lower()
        if kind not in {"issue", "prd", "refactor", "workflow"}:
            raise ConfigError(
                "workflow._flow_metadata_by_kind keys must be issue, prd, "
                f"refactor, or workflow; got {kind!r}"
            )
        if not isinstance(raw_metadata, dict):
            raise ConfigError(
                f"workflow._flow_metadata_by_kind.{kind} must be a mapping"
            )
        result[kind] = dict(raw_metadata)
    return result


def _build_workflow_work_units(data: dict | None) -> WorkflowWorkUnitsConfig:
    if not isinstance(data, dict):
        return WorkflowWorkUnitsConfig()
    split_data = data.get("split_quality") or {}
    if not isinstance(split_data, dict):
        split_data = {}
    try:
        split = WorkflowSplitQualityConfig(
            mode=str(split_data.get("mode", "warning") or "warning"),
            max_scope_files=int(split_data.get("max_scope_files", 12) or 0),
            max_acceptance_criteria=int(
                split_data.get("max_acceptance_criteria", 0) or 0
            ),
            require_validation_surface=bool(
                split_data.get("require_validation_surface", True)
            ),
        )
        return WorkflowWorkUnitsConfig(
            enabled=bool(data.get("enabled", False)),
            split_quality=split,
        )
    except ValueError as exc:
        raise ConfigError(f"Invalid workflow.work_units: {exc}") from exc


def _build_completion_audit(data: dict | None) -> WorkflowCompletionAuditConfig:
    if not isinstance(data, dict):
        return WorkflowCompletionAuditConfig()
    routes = data.get("routes") or {}
    return WorkflowCompletionAuditConfig(
        enabled=bool(data.get("enabled", False)),
        provider_completed_state=str(
            data.get("provider_completed_state", "completed_unverified")
            or "completed_unverified"
        ),
        routes={str(k): str(v) for k, v in routes.items()} if isinstance(routes, dict) else {},
    )


def _build_resume_packet(data: dict | None) -> WorkflowResumePacketConfig:
    if not isinstance(data, dict):
        return WorkflowResumePacketConfig()
    try:
        return WorkflowResumePacketConfig(
            enabled=bool(data.get("enabled", False)),
            max_tokens=int(data.get("max_tokens", 1200) or 1200),
            generate_on=list(data.get("generate_on", []) or []),
        )
    except ValueError as exc:
        raise ConfigError(f"Invalid workflow.resume_packet: {exc}") from exc


def _build_integration(data: dict | None) -> WorkflowIntegrationConfig:
    if not isinstance(data, dict):
        return WorkflowIntegrationConfig()
    return WorkflowIntegrationConfig(
        enabled=bool(data.get("enabled", False)),
        boundaries=list(data.get("boundaries", []) or []),
    )


def _build_strict_triggers(data: dict | None) -> WorkflowStrictTriggersConfig:
    if not isinstance(data, dict):
        return WorkflowStrictTriggersConfig()
    try:
        return WorkflowStrictTriggersConfig(
            rework_attempts_gte=int(data.get("rework_attempts_gte", 0) or 0),
            context_usage_gte=float(data.get("context_usage_gte", 0.0) or 0.0),
            file_globs=list(data.get("file_globs", []) or []),
            labels=list(data.get("labels", []) or []),
        )
    except ValueError as exc:
        raise ConfigError(f"Invalid workflow.strict_triggers: {exc}") from exc


def _build_fast_path(data: dict | None) -> WorkflowFastPathConfig:
    if not isinstance(data, dict):
        return WorkflowFastPathConfig()
    try:
        return WorkflowFastPathConfig(
            enabled=bool(data.get("enabled", False)),
            max_scope_files=int(data.get("max_scope_files", 2) or 0),
            skip_stages=list(
                data.get(
                    "skip_stages",
                    ["design", "design_critique", "judge"],
                )
                or []
            ),
            allow_docs_only=bool(data.get("allow_docs_only", True)),
            blocked_file_globs=list(data.get("blocked_file_globs", []) or []),
            blocked_keywords=list(data.get("blocked_keywords", []) or []),
            verification_required=bool(data.get("verification_required", True)),
        )
    except ValueError as exc:
        raise ConfigError(f"Invalid workflow.fast_path: {exc}") from exc


def _build_replan_eval(
    data: dict | None,
    *,
    harness_profile: str,
) -> WorkflowReplanEvalConfig:
    if not isinstance(data, dict):
        return WorkflowReplanEvalConfig(profile=harness_profile)
    try:
        return WorkflowReplanEvalConfig(
            enabled=bool(data.get("enabled", False)),
            profile=str(data.get("profile") or harness_profile or "baseline"),
            require_source_coverage=bool(data.get("require_source_coverage", True)),
            strict_requires_independent_review=bool(
                data.get("strict_requires_independent_review", True)
            ),
            release_requires_e2e=bool(data.get("release_requires_e2e", True)),
            release_requires_security=bool(
                data.get("release_requires_security", True)
            ),
            release_requires_human_approval=bool(
                data.get("release_requires_human_approval", True)
            ),
        )
    except ValueError as exc:
        raise ConfigError(f"Invalid workflow.replan_eval: {exc}") from exc


def _build_affinity_lanes(data: object) -> dict[str, WorkflowAffinityLaneProfileConfig]:
    if data in (None, ""):
        return {}
    if not isinstance(data, dict):
        raise ConfigError("workflow.affinity_lanes must be a mapping")
    profiles: dict[str, WorkflowAffinityLaneProfileConfig] = {}
    for profile_id, raw_profile in data.items():
        name = str(profile_id).strip()
        if not name:
            raise ConfigError("workflow.affinity_lanes contains an empty profile id")
        if not isinstance(raw_profile, dict):
            raise ConfigError(f"workflow.affinity_lanes[{name!r}] must be a mapping")
        queue_raw = raw_profile.get("queue") or {}
        if queue_raw and not isinstance(queue_raw, dict):
            raise ConfigError(f"workflow.affinity_lanes[{name!r}].queue must be a mapping")
        queue = WorkflowAffinityQueueConfig(
            order=str((queue_raw or {}).get("order") or "priority_fifo"),
        )
        lanes_raw = raw_profile.get("lanes") or []
        if not isinstance(lanes_raw, list):
            raise ConfigError(f"workflow.affinity_lanes[{name!r}].lanes must be a list")
        lanes: list[WorkflowAffinityLaneConfig] = []
        seen: set[str] = set()
        for lane_index, raw_lane in enumerate(lanes_raw):
            if not isinstance(raw_lane, dict):
                raise ConfigError(
                    f"workflow.affinity_lanes[{name!r}].lanes[{lane_index}] must be a mapping"
                )
            lane_id = str(raw_lane.get("id") or "").strip()
            if not lane_id:
                raise ConfigError(
                    f"workflow.affinity_lanes[{name!r}].lanes[{lane_index}].id is required"
                )
            if lane_id in seen:
                raise ConfigError(
                    f"workflow.affinity_lanes[{name!r}] duplicates lane id {lane_id!r}"
                )
            seen.add(lane_id)
            lanes.append(WorkflowAffinityLaneConfig(
                id=lane_id,
                impl=str(raw_lane.get("impl") or "").strip(),
                review=str(raw_lane.get("review") or "").strip(),
                verify=str(raw_lane.get("verify") or "").strip(),
            ))
        profiles[name] = WorkflowAffinityLaneProfileConfig(
            affinity_key=str(raw_profile.get("affinity_key") or "affinity_tag"),
            queue=queue,
            lanes=lanes,
        )
    return profiles


def _build_fanout_assignment(data: object, stage_index: int) -> FanoutAssignmentConfig:
    if data in (None, ""):
        return FanoutAssignmentConfig()
    if not isinstance(data, dict):
        raise ConfigError(f"workflow.stages[{stage_index}].fanout.assignment must be a mapping")
    strategy = str(data.get("strategy") or "static_index").strip() or "static_index"
    if strategy not in _VALID_FANOUT_ASSIGNMENT_STRATEGIES:
        raise ConfigError(
            f"workflow.stages[{stage_index}].fanout.assignment.strategy {strategy!r} "
            f"must be one of {_VALID_FANOUT_ASSIGNMENT_STRATEGIES}"
        )
    stage_slot = str(data.get("stage_slot") or "").strip()
    if strategy == "affinity_stage_slots":
        if stage_slot not in _VALID_AFFINITY_STAGE_SLOTS:
            raise ConfigError(
                f"workflow.stages[{stage_index}].fanout.assignment.stage_slot "
                f"must be one of {_VALID_AFFINITY_STAGE_SLOTS}"
            )
        lane_profile = str(data.get("lane_profile") or "").strip()
        if not lane_profile:
            raise ConfigError(
                f"workflow.stages[{stage_index}].fanout.assignment.lane_profile is required"
            )
    else:
        lane_profile = str(data.get("lane_profile") or "").strip()
    return FanoutAssignmentConfig(
        strategy=strategy,
        role_pool=[
            str(role).strip()
            for role in data.get("role_pool", []) or []
            if str(role).strip()
        ],
        lane_profile=lane_profile,
        stage_slot=stage_slot,
    )


def _affinity_stage_slot_roles(
    *,
    stage_index: int,
    assignment: FanoutAssignmentConfig,
    affinity_lanes: dict[str, WorkflowAffinityLaneProfileConfig],
) -> list[str]:
    if assignment.strategy != "affinity_stage_slots":
        return []
    profile = affinity_lanes.get(assignment.lane_profile)
    if profile is None:
        raise ConfigError(
            f"workflow.stages[{stage_index}].fanout.assignment.lane_profile "
            f"{assignment.lane_profile!r} is not declared in workflow.affinity_lanes"
        )
    roles: list[str] = []
    for lane in profile.lanes:
        target = getattr(lane, assignment.stage_slot, "")
        if not target:
            raise ConfigError(
                f"workflow.affinity_lanes[{assignment.lane_profile!r}].lanes"
                f"[{lane.id!r}].{assignment.stage_slot} is required"
            )
        roles.append(target)
    return roles


def _build_workflow_ports(
    data: object,
    *,
    stage_index: int,
    field_name: str,
) -> list[WorkflowPortConfig]:
    if data in (None, ""):
        return []
    if not isinstance(data, list):
        raise ConfigError(
            f"workflow.stages[{stage_index}].{field_name} must be a list"
        )
    ports: list[WorkflowPortConfig] = []
    names: set[str] = set()
    for port_index, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise ConfigError(
                f"workflow.stages[{stage_index}].{field_name}"
                f"[{port_index}] must be a mapping"
            )
        name = str(raw.get("name") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        if not name or not kind:
            raise ConfigError(
                f"workflow.stages[{stage_index}].{field_name}"
                f"[{port_index}] requires name and kind"
            )
        if name in names:
            raise ConfigError(
                f"workflow.stages[{stage_index}].{field_name} has duplicate "
                f"port {name!r}"
            )
        names.add(name)
        ports.append(WorkflowPortConfig(
            name=name,
            kind=kind,
            source=str(raw.get("source") or "").strip(),
            required=bool(raw.get("required", True)),
        ))
    return ports


def _build_workflow_string_list(
    data: object,
    *,
    stage_index: int,
    field_name: str,
) -> list[str]:
    if data in (None, ""):
        return []
    if not isinstance(data, list):
        raise ConfigError(
            f"workflow.stages[{stage_index}].{field_name} must be a list"
        )
    values = [str(item).strip() for item in data]
    if any(not item for item in values):
        raise ConfigError(
            f"workflow.stages[{stage_index}].{field_name} cannot contain "
            "empty values"
        )
    if len(values) != len(set(values)):
        raise ConfigError(
            f"workflow.stages[{stage_index}].{field_name} contains duplicates"
        )
    return values


def _build_generic_workflows(data: object) -> list[dict]:
    # Internal YAML `_generic_workflows` maps to WorkflowConfig
    # `"generic_workflows"`; it is compiler output, not a second user DSL.
    if data in (None, ""):
        return []
    if not isinstance(data, list):
        raise ConfigError("workflow._generic_workflows must be a list")
    contracts: list[dict] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ConfigError(
                f"workflow._generic_workflows[{index}] must be a mapping"
            )
        contracts.append(dict(item))
    if len(contracts) > 1:
        raise ConfigError(
            "workflow._generic_workflows supports one effective contract"
        )
    return contracts


def _build_workflow_stages(
    data: object,
    roles: list[RoleConfig],
    affinity_lanes: dict[str, WorkflowAffinityLaneProfileConfig] | None = None,
) -> list[WorkflowStageConfig]:
    if data in (None, ""):
        return []
    if not isinstance(data, list):
        raise ConfigError("workflow.stages must be a list")
    affinity_lanes = affinity_lanes or {}
    stages: list[WorkflowStageConfig] = []
    for i, raw_stage in enumerate(data):
        if not isinstance(raw_stage, dict):
            raise ConfigError(f"workflow.stages[{i}] must be a mapping")
        stage_id = str(raw_stage.get("id") or "")
        trigger = str(raw_stage.get("trigger") or "")
        flow_kind = str(raw_stage.get("flow_kind") or "").strip().lower()
        topology = str(raw_stage.get("topology") or "")
        attempt_domain = str(
            raw_stage.get("attempt_domain") or ""
        ).strip().lower()
        result_semantics = str(
            raw_stage.get("result_semantics") or ""
        ).strip().lower()
        if not stage_id:
            raise ConfigError(f"workflow.stages[{i}].id is required")
        if not trigger:
            raise ConfigError(f"workflow.stages[{i}].trigger is required")
        if flow_kind and flow_kind not in {
            "issue",
            "prd",
            "refactor",
            "workflow",
        }:
            raise ConfigError(
                f"workflow.stages[{i}].flow_kind must be issue, prd, "
                f"refactor, or workflow; got {flow_kind!r}"
            )
        if topology not in _VALID_STAR_TOPOLOGIES:
            raise ConfigError(
                f"workflow.stages[{i}].topology {topology!r} must be one of "
                f"{_VALID_STAR_TOPOLOGIES}"
            )
        if attempt_domain and attempt_domain not in _VALID_ATTEMPT_DOMAINS:
            raise ConfigError(
                f"workflow.stages[{i}].attempt_domain {attempt_domain!r} "
                f"must be one of {_VALID_ATTEMPT_DOMAINS}"
            )
        if result_semantics and result_semantics not in _VALID_RESULT_SEMANTICS:
            raise ConfigError(
                f"workflow.stages[{i}].result_semantics {result_semantics!r} "
                f"must be one of {_VALID_RESULT_SEMANTICS}"
            )
        fanout = raw_stage.get("fanout") or {}
        if fanout and not isinstance(fanout, dict):
            raise ConfigError(f"workflow.stages[{i}].fanout must be a mapping")
        aggregate = _build_fanout_aggregate(raw_stage.get("aggregate") or {})
        role_targets = [str(role) for role in raw_stage.get("roles", []) or []]
        assignment = _build_fanout_assignment((fanout or {}).get("assignment"), i)
        role_targets.extend(assignment.role_pool)
        role_targets.extend(_affinity_stage_slot_roles(
            stage_index=i,
            assignment=assignment,
            affinity_lanes=affinity_lanes,
        ))
        children = _build_fanout_children((fanout or {}).get("children", []))
        for child in children:
            target = child.role_instance or child.role
            if target:
                role_targets.append(target)
        role_targets = list(dict.fromkeys(role_targets))
        if not role_targets:
            raise ConfigError(f"workflow.stages[{i}] must declare roles or fanout.children")
        _validate_stage_roles(
            stage_index=i,
            topology=topology,
            role_targets=role_targets,
            roles=roles,
        )
        if aggregate.synth_role:
            _validate_stage_synth_role(
                stage_index=i,
                synth_role=aggregate.synth_role,
                roles=roles,
            )
        source = raw_stage.get("source") or {}
        if source and not isinstance(source, dict):
            raise ConfigError(f"workflow.stages[{i}].source must be a mapping")
        task_map = str(
            raw_stage.get("task_map")
            or raw_stage.get("task_map_path")
            or (source.get("task_map") if isinstance(source, dict) else "")
            or (fanout or {}).get("task_map")
            or ""
        )
        if topology == "fanout_writer_scoped":
            scoped_children = [
                child for child in children
                if child.scope or child.task_id or child.payload.get("scope")
            ]
            if not task_map and not scoped_children:
                raise ConfigError(
                    f"workflow.stages[{i}] fanout_writer_scoped requires "
                    "task_map or scoped fanout.children"
                )
            if aggregate.mode == "quorum":
                raise ConfigError(
                    f"workflow.stages[{i}] fanout_writer_scoped cannot use quorum"
                )
        if aggregate.mode not in _VALID_AGGREGATE_MODES:
            raise ConfigError(
                f"workflow.stages[{i}].aggregate.mode {aggregate.mode!r} "
                f"must be one of {_VALID_AGGREGATE_MODES}"
            )
        target = raw_stage.get("target") or {}
        if target and not isinstance(target, dict):
            raise ConfigError(f"workflow.stages[{i}].target must be a mapping")
        target_ref = str(
            raw_stage.get("target_ref")
            or (target.get("ref") if isinstance(target, dict) else "")
            or ""
        )
        stages.append(WorkflowStageConfig(
            id=stage_id,
            trigger=trigger,
            flow_kind=flow_kind,
            topology=topology,
            operation=str(raw_stage.get("operation") or ""),
            attempt_domain=attempt_domain,
            result_semantics=result_semantics,
            input_ports=_build_workflow_ports(
                raw_stage.get("input_ports"),
                stage_index=i,
                field_name="input_ports",
            ),
            output_ports=_build_workflow_ports(
                raw_stage.get("output_ports"),
                stage_index=i,
                field_name="output_ports",
            ),
            dependencies=_build_workflow_string_list(
                raw_stage.get("dependencies"),
                stage_index=i,
                field_name="dependencies",
            ),
            dependency_events=_build_workflow_string_list(
                raw_stage.get("dependency_events"),
                stage_index=i,
                field_name="dependency_events",
            ),
            dependency_failure_events=_build_workflow_string_list(
                raw_stage.get("dependency_failure_events"),
                stage_index=i,
                field_name="dependency_failure_events",
            ),
            dependency_barrier_id=str(
                raw_stage.get("dependency_barrier_id") or ""
            ),
            dependency_barrier_digest=str(
                raw_stage.get("dependency_barrier_digest") or ""
            ),
            roles=role_targets,
            target_ref=target_ref,
            task_map=task_map,
            assignment=assignment,
            children=children,
            aggregate=aggregate,
            timeout_seconds=int(
                raw_stage.get("timeout_seconds")
                or (raw_stage.get("aggregate") or {}).get("timeout_seconds", 0)
                or 0
            ),
            criteria=_build_stage_criteria(raw_stage.get("criteria") or {}),
            on_reject=_build_stage_backedge(
                raw_stage.get("on_reject"),
                stage_index=i,
                field_name="on_reject",
            ),
            on_fail=_build_stage_backedge(
                raw_stage.get("on_fail"),
                stage_index=i,
                field_name="on_fail",
            ),
            gate_profile=[
                str(value)
                for value in raw_stage.get("gate_profile", []) or []
                if str(value).strip()
            ],
            synthesize_canonical_tasks=bool(
                raw_stage.get("synthesize_canonical_tasks")
                or (source.get("synthesize_canonical_tasks")
                    if isinstance(source, dict) else False)
            ),
            retrigger_requires_delta=bool(
                raw_stage.get("retrigger_requires_delta") or False
            ),
        ))
    _validate_stage_backedge_semantics(stages)
    return stages


CANDIDATE_LEVEL_FAILURE_EVENTS = frozenset({
    "verify.failed",
    "test.failed",
    "judge.failed",
    "integration.failed",
    "candidate.conflict",
    "plan.rejected",
})


def _stage_index_by_id(stages: list[WorkflowStageConfig]) -> dict[str, int]:
    return {stage.id: idx for idx, stage in enumerate(stages) if stage.id}


def _same_lane_affinity_backedge_events(
    stages: list[WorkflowStageConfig],
) -> set[str]:
    stages_by_id = {stage.id: stage for stage in stages if stage.id}
    events: set[str] = set()
    for stage in stages:
        for backedge in (stage.on_reject, stage.on_fail):
            if not backedge.event:
                continue
            if str(backedge.target_affinity or "") != "same_lane":
                continue
            target_stage = stages_by_id.get(backedge.restart_stage)
            if (
                target_stage is not None
                and target_stage.assignment.strategy == "affinity_stage_slots"
            ):
                events.add(backedge.event)
    return events


def _validate_stage_backedge_semantics(
    stages: list[WorkflowStageConfig],
) -> None:
    stages_by_id = {stage.id: stage for stage in stages if stage.id}
    indexes = _stage_index_by_id(stages)
    for stage in stages:
        stage_index = indexes.get(stage.id, 0)
        for field_name, backedge in (
            ("on_reject", stage.on_reject),
            ("on_fail", stage.on_fail),
        ):
            if not backedge.event:
                continue
            if str(backedge.target_affinity or "") != "same_lane":
                continue
            target_stage = stages_by_id.get(backedge.restart_stage)
            if (
                target_stage is None
                or target_stage.assignment.strategy != "affinity_stage_slots"
            ):
                continue
            if backedge.event in CANDIDATE_LEVEL_FAILURE_EVENTS:
                raise ConfigError(
                    f"workflow.stages[{stage_index}].{field_name}.event "
                    f"{backedge.event!r} is candidate-level and cannot use "
                    "target_affinity: same_lane; route it through candidate "
                    "rework/replan instead"
                )


def _build_stage_backedge(
    data: object,
    *,
    stage_index: int,
    field_name: str,
) -> WorkflowStageBackedgeConfig:
    if data in (None, ""):
        return WorkflowStageBackedgeConfig()
    if not isinstance(data, dict):
        raise ConfigError(f"workflow.stages[{stage_index}].{field_name} must be a mapping")
    event = str(data.get("event") or "").strip()
    restart_stage = str(data.get("restart_stage") or "").strip()
    restart_role = str(
        data.get("restart_role")
        or data.get("role")
        or data.get("target_role")
        or ""
    ).strip()
    if not event:
        raise ConfigError(
            f"workflow.stages[{stage_index}].{field_name}.event is required"
        )
    if not restart_stage and not restart_role:
        raise ConfigError(
            f"workflow.stages[{stage_index}].{field_name} must declare "
            "restart_stage or restart_role"
        )
    try:
        return WorkflowStageBackedgeConfig(
            event=event,
            restart_stage=restart_stage,
            restart_role=restart_role,
            target_affinity=str(data.get("target_affinity") or "").strip(),
            max_attempts=int(data.get("max_attempts") or 0),
            feedback_artifact=str(data.get("feedback_artifact") or "").strip(),
            emit=str(data.get("emit") or "").strip(),
        )
    except ValueError as exc:
        raise ConfigError(
            f"Invalid workflow.stages[{stage_index}].{field_name}: {exc}"
        ) from exc


def _derive_stage_backedge_rework_routing(
    stages: list[WorkflowStageConfig],
) -> dict[str, str]:
    stage_primary_roles = {
        stage.id: stage.roles[0]
        for stage in stages
        if stage.id and stage.roles
    }
    routing: dict[str, str] = {}
    stages_by_id = {stage.id: stage for stage in stages if stage.id}
    for stage in stages:
        for backedge in (stage.on_reject, stage.on_fail):
            if not backedge.event:
                continue
            if str(backedge.target_affinity or "") == "same_lane":
                target_stage = stages_by_id.get(backedge.restart_stage)
                if (
                    target_stage is not None
                    and target_stage.assignment.strategy == "affinity_stage_slots"
                ):
                    continue
            target = (
                backedge.restart_role
                or stage_primary_roles.get(backedge.restart_stage, "")
                or backedge.restart_stage
            )
            if target:
                routing[backedge.event] = target
    return routing


def _has_affinity_stage_slots(stages: list[WorkflowStageConfig]) -> bool:
    return any(
        stage.assignment.strategy == "affinity_stage_slots"
        for stage in stages
    )


def _role_by_rework_target(roles: list[RoleConfig]) -> dict[str, RoleConfig]:
    out: dict[str, RoleConfig] = {}
    for role in roles:
        if role.name:
            out.setdefault(role.name, role)
        if role.instance_id:
            out.setdefault(role.instance_id, role)
    return out


def _is_design_rework_target(
    target: str,
    roles_by_target: dict[str, RoleConfig],
) -> bool:
    if not target:
        return False
    if target in _DESIGN_ROLE_NAMES:
        return True
    role = roles_by_target.get(target)
    if role is None:
        return False
    role_refs = {role.name, role.instance_id}
    if role_refs & _DESIGN_ROLE_NAMES:
        return True
    return bool(set(role.stages) & _DESIGN_STAGE_NAMES)


def _validate_rework_routing(
    raw_routing: object,
    stages: list[WorkflowStageConfig],
    roles: list[RoleConfig],
) -> dict:
    if raw_routing in (None, ""):
        return {}
    if not isinstance(raw_routing, dict):
        raise ConfigError("workflow.rework_routing must be a mapping")
    same_lane_events = _same_lane_affinity_backedge_events(stages)
    has_lane_pipeline = _has_affinity_stage_slots(stages)
    roles_by_target = _role_by_rework_target(roles)
    routing = dict(raw_routing)
    for event in routing:
        event_name = str(event or "").strip()
        target = str(routing[event] or "").strip()
        if "," in event_name:
            raise ConfigError(
                "workflow.rework_routing keys must name exactly one event; "
                f"split combined key {event_name!r} into separate entries"
            )
        if event_name in same_lane_events:
            raise ConfigError(
                f"workflow.rework_routing.{event_name} duplicates an "
                "affinity same-lane stage backedge; remove the top-level "
                "fixed route to avoid cross-lane rework"
            )
        if (
            has_lane_pipeline
            and event_name in _LANE_RUNTIME_REWORK_EVENTS
            and _is_design_rework_target(target, roles_by_target)
        ):
            raise ConfigError(
                f"workflow.rework_routing.{event_name} cannot route lane "
                f"runtime event to design role {target!r}; use same-lane "
                "stage backedge, orchestrator, or a plan synth role"
            )
    return routing


def _build_stage_criteria(data: object) -> WorkflowStageCriteriaConfig:
    if data in (None, ""):
        return WorkflowStageCriteriaConfig()
    if not isinstance(data, dict):
        raise ConfigError("workflow stage criteria must be a mapping")
    output_raw = data.get("output") or {}
    if output_raw and not isinstance(output_raw, dict):
        raise ConfigError("workflow stage criteria.output must be a mapping")
    retry_raw = data.get("retry") or {}
    if retry_raw and not isinstance(retry_raw, dict):
        raise ConfigError("workflow stage criteria.retry must be a mapping")
    success_raw = data.get("success_criteria") or []
    if isinstance(success_raw, dict):
        success_raw = [success_raw]
    if not isinstance(success_raw, list):
        raise ConfigError("workflow stage criteria.success_criteria must be a list")
    instructions_raw = data.get("instructions") or []
    if isinstance(instructions_raw, str):
        instructions_raw = [instructions_raw]
    if not isinstance(instructions_raw, list):
        raise ConfigError("workflow stage criteria.instructions must be a list")
    try:
        return WorkflowStageCriteriaConfig(
            instructions=[
                str(item).strip()
                for item in instructions_raw
                if str(item).strip()
            ],
            success_criteria=[
                item if isinstance(item, dict) else {
                    "kind": "command_passed",
                    "command": str(item),
                }
                for item in success_raw
            ],
            output=WorkflowStageOutputConfig(
                required_keys=[
                    str(value)
                    for value in output_raw.get("required_keys", []) or []
                    if str(value).strip()
                ],
                required_artifacts=[
                    str(value)
                    for value in output_raw.get("required_artifacts", []) or []
                    if str(value).strip()
                ],
                artifact_kinds=[
                    str(value)
                    for value in output_raw.get("artifact_kinds", []) or []
                    if str(value).strip()
                ],
            ),
            retry=WorkflowStageRetryPolicyConfig(
                max_attempts=int(retry_raw.get("max_attempts") or 0),
                backoff_seconds=int(retry_raw.get("backoff_seconds") or 0),
                on_failure=str(retry_raw.get("on_failure") or "rework"),
            ),
        )
    except ValueError as exc:
        raise ConfigError(f"Invalid workflow stage criteria: {exc}") from exc


def _validate_stage_criteria_config_refs(
    *,
    config_path: Path,
    stages: list[WorkflowStageConfig],
) -> None:
    """Fail fast when a fixed project-local gate config is missing.

    Runtime gates remain fail-closed, but a literal relative ``config_ref`` in
    zf.yaml should be visible at cold start. Otherwise a long run can reach the
    final reader aggregate and fail only because the reducer cannot load its
    own gate configuration.
    """
    project_root = config_path.parent
    for stage_index, stage in enumerate(stages):
        for criterion_index, criterion in enumerate(stage.criteria.success_criteria):
            if not isinstance(criterion, dict):
                continue
            kind = str(criterion.get("kind") or criterion.get("type") or "").strip()
            if kind not in {"artifact_matrix_gate", "candidate_artifact_matrix_gate"}:
                continue
            ref = str(
                criterion.get("config_ref")
                or criterion.get("gate_config_ref")
                or ""
            ).strip()
            if not ref or _dynamic_or_external_ref(ref):
                continue
            if (project_root / ref).exists():
                continue
            raise ConfigError(
                "workflow.stages"
                f"[{stage_index}].criteria.success_criteria"
                f"[{criterion_index}].config_ref {ref!r} does not exist "
                f"under {project_root}"
            )


def _dynamic_or_external_ref(ref: str) -> bool:
    if "${" in ref or "$" in ref:
        return True
    path = Path(ref)
    return path.is_absolute() or ref.startswith(("~", ".."))


def _build_fanout_aggregate(data: object) -> FanoutAggregateConfig:
    if data and not isinstance(data, dict):
        raise ConfigError("workflow stage aggregate must be a mapping")
    raw = data if isinstance(data, dict) else {}
    retry = raw.get("retry") or {}
    if retry and not isinstance(retry, dict):
        raise ConfigError("workflow stage aggregate.retry must be a mapping")
    return FanoutAggregateConfig(
        mode=str(raw.get("mode") or "wait_for_all"),
        success_event=str(raw.get("success_event") or ""),
        failure_event=str(raw.get("failure_event") or ""),
        child_success_event=str(
            raw.get("child_success_event")
            or raw.get("child_result_success_event")
            or "workflow.child.completed"
        ),
        child_failure_event=str(
            raw.get("child_failure_event")
            or raw.get("child_result_failure_event")
            or "workflow.child.failed"
        ),
        synth_role=str(raw.get("synth_role") or ""),
        max_retries=int(raw.get("max_retries") or retry.get("max_attempts", 0) or 0),
        # EVAL-WAVE-REVIEW-001 (doc 43 §2.6): wave_review strategy +
        # pending event + quorum override.
        review_strategy=str(raw.get("review_strategy") or ""),
        pending_event=str(raw.get("pending_event") or ""),
        quorum=int(raw.get("quorum") or 0),
        # B3 (R25 ISSUE-005): dedicated synth wait budget.
        synth_timeout_seconds=int(raw.get("synth_timeout_seconds") or 0),
    )


def _build_fanout_children(data: object) -> list[FanoutChildConfig]:
    if data in (None, ""):
        return []
    if not isinstance(data, list):
        raise ConfigError("workflow stage fanout.children must be a list")
    children: list[FanoutChildConfig] = []
    for raw in data:
        if not isinstance(raw, dict):
            raise ConfigError("workflow stage fanout.children entries must be mappings")
        children.append(FanoutChildConfig(
            role_instance=str(raw.get("role_instance") or ""),
            role=str(raw.get("role") or ""),
            scope=str(raw.get("scope") or ""),
            task_id=str(raw.get("task_id") or ""),
            payload=dict(raw.get("payload") or {}),
        ))
    return children


def _validate_stage_roles(
    *,
    stage_index: int,
    topology: str,
    role_targets: list[str],
    roles: list[RoleConfig],
) -> None:
    for target in role_targets:
        matches = [
            role for role in roles
            if role.name == target or role.instance_id == target
        ]
        if not matches:
            raise ConfigError(
                f"workflow.stages[{stage_index}] references missing role {target!r}"
            )
        role_kinds = {_resolve_role_kind(role) for role in matches}
        if topology == "fanout_reader" and role_kinds != {"reader"}:
            raise ConfigError(
                f"workflow.stages[{stage_index}] fanout_reader requires reader roles; "
                f"{target!r} resolved to {sorted(role_kinds)}"
            )
        if topology == "fanout_writer_scoped" and role_kinds != {"writer"}:
            raise ConfigError(
                f"workflow.stages[{stage_index}] fanout_writer_scoped requires writer roles; "
                f"{target!r} resolved to {sorted(role_kinds)}"
            )


def _validate_stage_synth_role(
    *,
    stage_index: int,
    synth_role: str,
    roles: list[RoleConfig],
) -> None:
    matches = [
        role for role in roles
        if role.name == synth_role or role.instance_id == synth_role
    ]
    if not matches:
        raise ConfigError(
            f"workflow.stages[{stage_index}] references missing synth_role "
            f"{synth_role!r}"
        )
    role_kinds = {_resolve_role_kind(role) for role in matches}
    if role_kinds != {"reader"}:
        raise ConfigError(
            f"workflow.stages[{stage_index}].aggregate.synth_role requires "
            f"a reader role; {synth_role!r} resolved to {sorted(role_kinds)}"
        )


def _resolve_role_kind(role: RoleConfig) -> str:
    if role.role_kind != "auto":
        return role.role_kind
    if role.name in {"review", "test", "judge", "verify", "critic"}:
        return "reader"
    return "writer"


def _build_role(data: dict) -> RoleConfig:
    name = data.get("name", "")
    _reject_unknown_keys(
        data, _KNOWN_ROLE_KEYS, f"role {name!r}" if name else "role",
    )
    if not _ROLE_NAME_RE.match(name):
        raise ConfigError(
            f"Invalid role name {name!r}: must match {_ROLE_NAME_RE.pattern} "
            f"(letters, digits, underscore, hyphen; first char a letter; max 32)"
        )
    permission_mode_explicit = "permission_mode" in data
    permission_mode = data.get("permission_mode", "bypass")
    if permission_mode not in _VALID_PERMISSION_MODES:
        raise ConfigError(
            f"Invalid permission_mode {permission_mode!r} for role {name!r}: "
            f"must be one of {_VALID_PERMISSION_MODES}"
        )
    # A sprint: nudge users toward least-privilege when they implicitly
    # accept the bypass default. Won't fire if user explicitly wrote
    # `permission_mode: bypass` (acknowledged choice).
    backend = data.get("backend", "python")
    role_kind = data.get("role_kind", "auto")
    if role_kind not in _VALID_ROLE_KINDS:
        raise ConfigError(
            f"Invalid role_kind {role_kind!r} for role {name!r}: "
            f"must be one of {_VALID_ROLE_KINDS}"
        )
    flow_kind = str(data.get("flow_kind") or "").strip().lower()
    if flow_kind not in {"", "issue", "prd", "refactor", "workflow"}:
        raise ConfigError(
            f"Invalid flow_kind {flow_kind!r} for role {name!r}: "
            "must be one of '', issue, prd, refactor, workflow"
        )
    # B-MIXEDBACKEND-01 (2026-04-23): per-replica backends list. Mutually
    # exclusive with singular `backend` when both are set explicitly.
    backends_raw = data.get("backends")
    if backends_raw is not None:
        if not isinstance(backends_raw, list) or not all(
            isinstance(b, str) and b for b in backends_raw
        ):
            raise ConfigError(
                f"role {name!r}: `backends` must be a list of non-empty strings"
            )
        if "backend" in data:
            raise ConfigError(
                f"role {name!r}: specify either `backend` (singular, all "
                f"replicas same) or `backends` (list, per-replica), not both"
            )
        backends_list = list(backends_raw)
        # When only `backends` is set, derive `backend` from the first entry
        # so legacy readers (e.g. start.py role menu) still see a scalar.
        backend = backends_list[0]
    else:
        backends_list = []
    if (
        not permission_mode_explicit
        and permission_mode == "bypass"
        and (backend in ("claude-code", "codex") or any(
            b in ("claude-code", "codex") for b in backends_list
        ))
    ):
        import sys
        print(
            f"Warning: role {name!r} has implicit permission_mode: bypass; "
            f"provider execution already uses the trusted full-access baseline. "
            f"Add `permission_mode: bypass` to acknowledge the role's logical "
            f"policy, or switch to `permission_mode: allowlist` + "
            f"`allowed_tools: [...]` for a least privilege ZaoFu contract.",
            file=sys.stderr,
        )
    transport = data.get("transport", "tmux")
    if transport not in _VALID_TRANSPORTS:
        raise ConfigError(
            f"Invalid transport {transport!r} for role {name!r}: "
            f"must be one of {_VALID_TRANSPORTS}"
        )
    if transport == "stream-json" and any(
        value != "claude-code" for value in (backends_list or [backend])
    ):
        raise ConfigError(
            f"role {name!r}: stream-json requires backend=claude-code; "
            "use transport=tmux for Codex or other backends"
        )
    execution_data = data.get("execution")
    if execution_data is not None and not isinstance(execution_data, dict):
        raise ConfigError(f"role {name!r}: execution must be a mapping")
    execution_data = execution_data or {}
    _reject_unknown_keys(
        execution_data,
        frozenset({"command", "default_profile", "profile_allowlist"}),
        f"role {name!r}.execution",
    )
    try:
        execution = ExecutionConfig(
            command=str(execution_data.get("command", "") or ""),
            default_profile=str(
                execution_data.get("default_profile", "direct-v1")
                or "direct-v1"
            ),
            profile_allowlist=_string_list(
                execution_data.get("profile_allowlist"),
                default=["direct-v1"],
            ),
        )
    except ConfigError as exc:
        raise ConfigError(f"role {name!r}.execution: {exc}") from exc
    replicas = int(data.get("replicas", 1))
    if replicas < 1:
        raise ConfigError(
            f"Invalid replicas {replicas!r} for role {name!r}: must be >= 1"
        )
    autoscale = _build_role_autoscale(data.get("autoscale"), role_name=name)
    if autoscale.enabled and replicas < autoscale.min_replicas:
        raise ConfigError(
            f"role {name!r}: replicas={replicas} must be >= "
            f"autoscale.min_replicas={autoscale.min_replicas}"
        )
    if autoscale.enabled and replicas > autoscale.max_replicas:
        raise ConfigError(
            f"role {name!r}: replicas={replicas} must be <= "
            f"autoscale.max_replicas={autoscale.max_replicas}"
        )
    # B-MIXEDBACKEND-01: cross-validate `backends` length against `replicas`.
    # RoleConfig.__post_init__ re-checks this, but raising here yields a
    # clearer ConfigError at yaml-load time.
    if backends_list and len(backends_list) != replicas:
        raise ConfigError(
            f"role {name!r}: len(backends)={len(backends_list)} must equal "
            f"replicas={replicas} (one backend per replica)"
        )
    plugins = list(data.get("plugins", []) or [])
    skills = list(data.get("skills", []) or [])
    agent = str(data.get("agent", "") or "")
    provider_session = _build_provider_session(
        data.get("provider_session"),
        role_name=name,
    )
    lifecycle = _build_role_lifecycle(
        data.get("lifecycle"),
        role_name=name,
    )
    if name == "orchestrator" and lifecycle.mode == "on_demand":
        raise ConfigError(
            "role 'orchestrator': lifecycle.mode=on_demand is invalid; "
            "control-plane roles must remain resident"
        )
    if (
        provider_session is not None
        and provider_session.agent
        and agent
        and provider_session.agent != agent
    ):
        raise ConfigError(
            f"role {name!r}: role.agent and provider_session.agent conflict; "
            "declare one agent identity"
        )
    # P-Y3: codex backend doesn't support plugins / agent (it has no
    # equivalent CLI flag). skills *can* still be referenced in the
    # role's instructions, but plugin/agent fields are silently dropped
    # by CodexAdapter — surface a warning at load time so configs aren't
    # quietly mismatched. We don't fail-fast: experimentation should not
    # be blocked by this.
    # B-MIXEDBACKEND-01: also fire the warning when *any* replica is codex
    # (mixed pools carry codex replicas even if singular `backend` says claude).
    any_codex = backend == "codex" or any(
        b == "codex" for b in backends_list
    )
    if any_codex and (plugins or agent):
        import sys
        unsupported = []
        if plugins:
            unsupported.append(f"plugins ({len(plugins)})")
        if agent:
            unsupported.append("agent")
        print(
            f"Warning: role {name!r} backend=codex does not support "
            f"{', '.join(unsupported)}; fields will be ignored. "
            f"Use backend=claude-code if you need them.",
            file=sys.stderr,
        )

    return RoleConfig(
        name=name,
        backend=backend,
        role_kind=role_kind,
        flow_kind=flow_kind,
        backends=backends_list,
        model=data.get("model", ""),
        model_reasoning_effort=data.get("model_reasoning_effort", ""),
        allowed_tools=data.get("allowed_tools", []),
        permission_mode=permission_mode,
        transport=transport,
        stuck_threshold_seconds=float(
            data.get("stuck_threshold_seconds", 300.0)
        ),
        instance_id=data.get("instance_id", ""),
        replicas=replicas,
        context_window_tokens=int(data.get("context_window_tokens", 200_000)),
        context_warning_threshold=(
            float(data["context_warning_threshold"])
            if data.get("context_warning_threshold") is not None
            else None
        ),
        context_compact_threshold=(
            float(data["context_compact_threshold"])
            if data.get("context_compact_threshold") is not None
            else None
        ),
        context_hard_cap=(
            float(data["context_hard_cap"])
            if data.get("context_hard_cap") is not None
            else None
        ),
        recycle_threshold=(
            float(data["recycle_threshold"])
            if data.get("recycle_threshold") is not None
            else None
        ),
        recycle_hard_cap=(
            float(data["recycle_hard_cap"])
            if data.get("recycle_hard_cap") is not None
            else None
        ),
        max_rework_attempts=int(data.get("max_rework_attempts", 3)),
        orphan_warning_seconds=float(data.get("orphan_warning_seconds", 900.0)),
        orphan_escalate_seconds=float(data.get("orphan_escalate_seconds", 1800.0)),
        drain_hold_seconds=float(data.get("drain_hold_seconds", 180.0)),
        spawn_ready_timeout_seconds=float(
            data.get("spawn_ready_timeout_seconds", 0.0)
        ),
        budget_usd=(
            float(data["budget_usd"]) if data.get("budget_usd") is not None else None
        ),
        provider_session=provider_session,
        lifecycle=lifecycle,
        autoscale=autoscale,
        constraints=_build_constraints(data.get("constraints")),
        execution=execution,
        stages=data.get("stages", []),
        triggers=data.get("triggers", []),
        publishes=data.get("publishes", []),
        guardrails=[str(g) for g in data.get("guardrails", []) or []],
        plugins=plugins,
        skills=skills,
        agent=agent,
    )


def _build_execution_profiles(
    data: object,
) -> dict[str, ExecutionProfileConfig]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError("workflow.execution_profiles must be a mapping")
    profiles: dict[str, ExecutionProfileConfig] = {}
    profile_name_re = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
    profile_keys = frozenset({
        "schema_version",
        "strategy",
        "continuation",
        "collaboration",
        "access",
        "capability_policy",
        "limits",
    })
    limit_keys = frozenset({
        "max_children",
        "max_depth",
        "timeout_seconds",
        "max_usage_samples",
        "token_budget",
        "cost_budget_usd",
    })
    for raw_name, raw_profile in data.items():
        name = str(raw_name or "").strip()
        if not profile_name_re.fullmatch(name):
            raise ConfigError(
                "workflow.execution_profiles keys must start with a lowercase "
                "letter and contain only lowercase letters, digits, _ or -"
            )
        if not isinstance(raw_profile, dict):
            raise ConfigError(
                f"workflow.execution_profiles.{name} must be a mapping"
            )
        _reject_unknown_keys(
            raw_profile,
            profile_keys,
            f"workflow.execution_profiles.{name}",
        )
        raw_limits = raw_profile.get("limits") or {}
        if not isinstance(raw_limits, dict):
            raise ConfigError(
                f"workflow.execution_profiles.{name}.limits must be a mapping"
            )
        _reject_unknown_keys(
            raw_limits,
            limit_keys,
            f"workflow.execution_profiles.{name}.limits",
        )
        try:
            limits = ExecutionProfileLimitsConfig(
                max_children=int(raw_limits.get("max_children", 0) or 0),
                max_depth=int(raw_limits.get("max_depth", 0) or 0),
                timeout_seconds=float(
                    raw_limits.get("timeout_seconds", 0.0) or 0.0
                ),
                max_usage_samples=int(
                    raw_limits.get("max_usage_samples", 0) or 0
                ),
                token_budget=int(raw_limits.get("token_budget", 0) or 0),
                cost_budget_usd=float(
                    raw_limits.get("cost_budget_usd", 0.0) or 0.0
                ),
            )
            profile = ExecutionProfileConfig(
                schema_version=str(
                    raw_profile.get(
                        "schema_version",
                        "execution-profile.v1",
                    )
                    or "execution-profile.v1"
                ),
                strategy=str(raw_profile.get("strategy", "direct") or "direct"),
                continuation=str(
                    raw_profile.get("continuation", "turn") or "turn"
                ),
                collaboration=str(
                    raw_profile.get("collaboration", "single") or "single"
                ),
                access=str(
                    raw_profile.get("access", "read_only") or "read_only"
                ),
                capability_policy=str(
                    raw_profile.get("capability_policy", "require")
                    or "require"
                ),
                limits=limits,
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"Invalid workflow.execution_profiles.{name}: {exc}"
            ) from exc
        if name == "direct-v1" and profile != ExecutionProfileConfig():
            raise ConfigError(
                "workflow.execution_profiles.direct-v1 is reserved for the "
                "canonical direct/turn/single profile"
            )
        profiles[name] = profile
    return profiles


def _build_workflow_run_limits(data: object) -> WorkflowRunLimitsConfig:
    if data is None:
        return WorkflowRunLimitsConfig()
    if not isinstance(data, dict):
        raise ConfigError("workflow.run_limits must be a mapping")
    _reject_unknown_keys(
        data,
        frozenset({"timeout_seconds", "token_budget", "cost_budget_usd"}),
        "workflow.run_limits",
    )
    try:
        return WorkflowRunLimitsConfig(
            timeout_seconds=float(data.get("timeout_seconds", 0.0) or 0.0),
            token_budget=int(data.get("token_budget", 0) or 0),
            cost_budget_usd=float(data.get("cost_budget_usd", 0.0) or 0.0),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid workflow.run_limits: {exc}") from exc


def _validate_role_execution_profiles(
    roles: list[RoleConfig],
    profiles: dict[str, ExecutionProfileConfig],
) -> None:
    available = {"direct-v1", *profiles}
    for role in roles:
        execution = role.execution
        default_profile = str(execution.default_profile or "").strip()
        allowlist = [
            str(item or "").strip()
            for item in execution.profile_allowlist
            if str(item or "").strip()
        ]
        if not default_profile:
            raise ConfigError(
                f"role {role.name!r}.execution.default_profile is required"
            )
        if default_profile not in available:
            raise ConfigError(
                f"role {role.name!r}.execution.default_profile references "
                f"unknown profile {default_profile!r}"
            )
        unknown = sorted(set(allowlist) - available)
        if unknown:
            raise ConfigError(
                f"role {role.name!r}.execution.profile_allowlist references "
                f"unknown profile(s): {', '.join(unknown)}"
            )
        if default_profile not in allowlist:
            raise ConfigError(
                f"role {role.name!r}.execution.default_profile must be in "
                "profile_allowlist"
            )


def _build_workflow_pipelines(data: object) -> list:
    """doc 88 P0: parse workflow.pipelines via the lane_pipeline module.

    Spec errors (unknown keys / bad kind / missing fields) are wrapped as
    ConfigError so `zf validate` and load_config agree (validate=loader
    单一权威).
    """
    if not data:
        return []
    from zf.core.workflow.lane_pipeline import (
        LanePipelineSpecError,
        parse_workflow_pipelines,
    )
    try:
        return parse_workflow_pipelines(data)
    except LanePipelineSpecError as exc:
        raise ConfigError(str(exc))


def _build_quality_gates(data: dict | None) -> dict[str, QualityGateConfig]:
    if not data:
        return {}
    gates = {}
    for name, gate_data in data.items():
        if not isinstance(gate_data, dict):
            gate_data = {}
        gates[name] = QualityGateConfig(
            enabled=gate_data.get("enabled", True),
            required_checks=gate_data.get("required_checks", []),
            on_fail=str(gate_data.get("on_fail", "") or ""),
        )
    return gates


def _build_skill_sources(data: list | None) -> list[SkillSourceConfig]:
    if not data:
        return []
    if not isinstance(data, list):
        raise ConfigError("skill_sources must be a list")
    sources: list[SkillSourceConfig] = []
    seen: set[str] = set()
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ConfigError(f"skill_sources[{i}] must be a mapping")
        name = str(item.get("name", "") or "")
        if not _ROLE_NAME_RE.match(name):
            raise ConfigError(
                f"Invalid skill_sources[{i}].name {name!r}: must match "
                f"{_ROLE_NAME_RE.pattern}"
            )
        if name in seen:
            raise ConfigError(f"Duplicate skill source name {name!r}")
        seen.add(name)
        path = str(item.get("path", "") or "")
        if not path:
            raise ConfigError(f"skill_sources[{i}].path is required")
        mode = str(item.get("mode", "readonly") or "readonly")
        if mode not in _VALID_SKILL_SOURCE_MODES:
            raise ConfigError(
                f"Invalid skill_sources[{i}].mode {mode!r}: "
                f"must be one of {_VALID_SKILL_SOURCE_MODES}"
            )
        sources.append(SkillSourceConfig(name=name, path=path, mode=mode))
    return sources


def _profile_source_refs_from_documents(documents: list[object]) -> list[object]:
    """Extract ZfConfig.spec.profile_sources before envelope assembly.

    This is a load-time source list only.  The field is stripped before the
    canonical ZfConfig is built, so runtime consumers never read profile files.
    """
    refs: list[object] = []
    docs = [doc for doc in documents if doc is not None]
    if not docs:
        return refs
    if len(docs) == 1 and isinstance(docs[0], dict) and "kind" not in docs[0]:
        raw = docs[0].get("profile_sources") or []
        return raw if isinstance(raw, list) else [raw]
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if str(doc.get("kind") or "") != "ZfConfig":
            continue
        spec = doc.get("spec") or {}
        if not isinstance(spec, dict):
            continue
        raw = spec.get("profile_sources") or []
        return raw if isinstance(raw, list) else [raw]
    return refs


def _profile_source_item_path(item: object, *, index: int) -> str:
    if isinstance(item, str):
        ref = item.strip()
    elif isinstance(item, dict):
        unknown = sorted(str(k) for k in item if str(k) not in {"path"})
        if unknown:
            raise ConfigError(
                f"profile_sources[{index}] unknown key(s) {unknown}; "
                "only 'path' is supported"
            )
        ref = str(item.get("path") or "").strip()
    else:
        raise ConfigError(f"profile_sources[{index}] must be a string or mapping")
    if not ref:
        raise ConfigError(f"profile_sources[{index}] path is required")
    return ref


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_profile_source_documents(
    config_path: Path,
    refs: list[object],
    *,
    env: dict[str, str],
) -> tuple[list[object], list[dict[str, str]]]:
    if not refs:
        return [], []
    base = config_path.parent
    documents: list[object] = []
    sources: list[dict[str, str]] = []
    seen: set[Path] = set()
    for index, item in enumerate(refs):
        ref = _profile_source_item_path(item, index=index)
        pattern = ref if Path(ref).is_absolute() else str(base / ref)
        matches = [Path(p).resolve() for p in sorted(glob.glob(pattern))]
        if not matches:
            raise ConfigError(
                f"profile_sources[{index}] {ref!r} did not match any files"
            )
        for source_path in matches:
            if source_path in seen:
                continue
            seen.add(source_path)
            if not source_path.is_file():
                raise ConfigError(
                    f"profile source {source_path} is not a regular file"
                )
            text = _expand_env_vars(
                source_path.read_text(encoding="utf-8"),
                env,
            )
            try:
                loaded = list(yaml.load_all(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)))
            except yaml.YAMLError as exc:
                raise ConfigError(
                    f"profile source {source_path} YAML parse error: {exc}"
                )
            documents.extend(loaded)
            sources.append({
                "kind": "ProfileSource",
                "name": ref,
                "path": str(source_path),
                "sha256": _sha256_file(source_path),
            })
    return documents, sources


def load_config(path: Path) -> ZfConfig:
    import sys
    from zf.core.events.known_types import validate_role_event_names

    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    env = _config_env_map(path)
    text = _expand_env_vars(path.read_text(encoding="utf-8"), env)
    try:
        documents = list(yaml.load_all(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)))
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML parse error: {e}")
    profile_documents, profile_source_files = _load_profile_source_documents(
        path,
        _profile_source_refs_from_documents(documents),
        env=env,
    )
    if profile_documents:
        non_empty_documents = [doc for doc in documents if doc is not None]
        first_document = non_empty_documents[0] if non_empty_documents else None
        if (
            len(non_empty_documents) == 1
            and isinstance(first_document, dict)
            and "kind" not in first_document
        ):
            documents = [{
                "apiVersion": "zaofu.dev/v1",
                "kind": "ZfConfig",
                "metadata": {"name": "legacy"},
                "spec": first_document,
            }]
        documents = profile_documents + documents
    # doc 90 B1: kind envelope 前置层。单文档无 kind = 隐式 ZfConfig
    # (legacy 零迁移);多文档/kind 流路由进同一 raw dict —— envelope
    # 是语法糖,不是第二控制面。
    from zf.core.config.kind_envelope import (
        KindEnvelopeError,
        assemble_envelope_stream,
    )
    try:
        raw, _envelope_profiles = assemble_envelope_stream(
            documents,
            profile_source_files=profile_source_files,
        )
    except KindEnvelopeError as e:
        raise ConfigError(str(e))
    if raw is None:
        raw = {}
    # V3:版本化 preset(name/vN)在 load 期作为 policy 基线 merge,
    # 项目字段最高;裸名 preset 保持 init 标记语义(忽略,零迁移)。
    preset_ref = str(raw.get("preset") or "") if isinstance(raw, dict) else ""
    if "/" in preset_ref:
        from zf.core.config.presets import (
            PresetError,
            merge_preset_base,
            resolve_versioned_preset,
        )
        try:
            raw = merge_preset_base(raw, resolve_versioned_preset(preset_ref))
        except PresetError as exc:
            raise ConfigError(str(exc))

    # P0-VALIDATE-LOADER-01: fail-fast schema-level checks. Previously
    # validate_config() did these as a shallow second pass; centralising
    # them here keeps `validate ≥ loader` (backlog T1 invariant) and
    # turns AttributeError on non-dict roots into a readable ConfigError.
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a YAML mapping")
    _reject_unknown_keys(raw, _KNOWN_TOP_LEVEL_KEYS, "top-level config")
    # feishu.yaml 适配器配置:若同目录存在 feishu.yaml,把其 feishu_* 合并进
    # integrations —— 文件分离、逻辑一份(同一 ZfConfig、同一校验),不另起
    # loader/真相。向后兼容:zf.yaml 内联 integrations.feishu_* 仍可用。
    raw = _merge_feishu_yaml(raw, path)
    if "project" not in raw:
        raise ConfigError("Missing required section: project")
    project_data = raw["project"]
    if not isinstance(project_data, dict):
        raise ConfigError("project must be a mapping")
    if not project_data.get("name"):
        raise ConfigError("project.name is required")
    roles_raw = raw.get("roles", []) or []
    if not isinstance(roles_raw, list):
        raise ConfigError("roles must be a list")
    for i, r in enumerate(roles_raw):
        if not isinstance(r, dict):
            raise ConfigError(f"roles[{i}]: must be a mapping")

    session_data = raw.get("session", {}) or {}
    orch_data = raw.get("orchestrator", {}) or {}
    loop_data = orch_data.get("loop", {}) or {}
    workflow_data = raw.get("workflow", {}) or {}
    if isinstance(workflow_data, dict):
        _reject_unknown_keys(workflow_data, _KNOWN_WORKFLOW_KEYS, "workflow")
    harness_profile = str(
        workflow_data.get("harness_profile", "baseline") or "baseline"
    )
    if harness_profile not in {"baseline", "strict", "release"}:
        raise ConfigError(
            "Invalid workflow.harness_profile "
            f"{harness_profile!r}: must be baseline, strict, or release"
        )
    roles = [_build_role(r) for r in roles_raw]
    # doc 90 A1(顺序关键):lane_role_template 生成必须先于
    # _build_workflow_stages 的 role 引用校验——真实 hermes 文件的手写
    # stages 引用 dev-lane-* 生成 role,后置生成会被校验误判 missing。
    pipelines = _build_workflow_pipelines(workflow_data.get("pipelines"))
    pipelines_role_meta: list = []
    if pipelines:
        from zf.core.workflow.lane_role_template import (
            LaneRoleTemplateError,
            generate_lane_roles,
        )
        try:
            for pipeline_spec in pipelines:
                roles, _metas = generate_lane_roles(pipeline_spec, roles)
                pipelines_role_meta.extend(_metas)
        except LaneRoleTemplateError as exc:
            raise ConfigError(str(exc))
        # doc 88 P1 切片 1(G3):pipelines-only 配置物化为 canonical
        # stages(与 kind: Workflow 同构);手写 stages 已覆盖同一
        # trigger → 跳过 + WARN(doc 90 §7 双表示漂移提示,hermes
        # v1/v2 现状零回归)。affinity profile 缺位时一并物化。
        from zf.core.workflow.lane_pipeline_materialize import (
            lane_profile_name,
            materialize_affinity_profile,
            materialize_lane_pipeline_stages,
        )
        stage_dicts = workflow_data.get("stages")
        if not isinstance(stage_dicts, list):
            stage_dicts = []
            workflow_data["stages"] = stage_dicts
        hand_triggers = {
            str(s.get("trigger") or "")
            for s in stage_dicts if isinstance(s, dict)
        }
        flow_metadata_raw = workflow_data.get("_flow_metadata")
        task_pipeline_raw = (
            flow_metadata_raw.get("task_pipeline")
            if isinstance(flow_metadata_raw, dict)
            else None
        )
        task_pipeline_blocking = bool(
            isinstance(task_pipeline_raw, dict)
            and str(task_pipeline_raw.get("mode") or "") == "blocking"
        )
        rendered_pipeline_stages = bool(
            isinstance(flow_metadata_raw, dict)
            and flow_metadata_raw.get("rendered_pipeline_stages")
        )
        affinity_data = workflow_data.get("affinity_lanes")
        if not isinstance(affinity_data, dict):
            affinity_data = {}
            workflow_data["affinity_lanes"] = affinity_data
        merged_names = {
            str(getattr(r, "name", "") or "") for r in roles
        }
        for pipeline_spec in pipelines:
            needed = set()
            for st in pipeline_spec.stages:
                pattern = st.role_pattern or f"{st.stage_id}-lane-{{lane}}"
                needed |= {
                    pattern.format(lane=i)
                    for i in range(max(pipeline_spec.lane_count, 0))
                }
            if pipeline_spec.final_role:
                needed.add(pipeline_spec.final_role)
            if not needed <= merged_names:
                # 角色不齐(通常缺 lane_role_template)→ 维持 inspect-only
                # 行为,不物化;缺口由 compile_lane_pipeline 的 role STOP
                # 诊断负责,这里不重复告警。
                continue
            if pipeline_spec.trigger in hand_triggers:
                if not rendered_pipeline_stages:
                    print(
                        f"Warning: lane_pipeline "
                        f"{pipeline_spec.pipeline_id!r} and hand-written "
                        f"stages cover the same trigger "
                        f"{pipeline_spec.trigger!r}; dual representation "
                        "can drift, so remove the hand-written stages",
                        file=sys.stderr,
                    )
                continue
            stage_dicts.extend(
                materialize_lane_pipeline_stages(
                    pipeline_spec,
                    task_pipeline_blocking=task_pipeline_blocking,
                ),
            )
            dag_data = workflow_data.setdefault("dag", {})
            if isinstance(dag_data, dict):
                ext = dag_data.setdefault("external_triggers", [])
                if isinstance(ext, list) and pipeline_spec.trigger not in ext:
                    ext.append(pipeline_spec.trigger)
            # Same-lane rework is represented by materialized on_fail
            # backedges. Do not also emit lane-0 global fallback routes; they
            # are lossy and can reintroduce cross-lane rework.
            profile_id = lane_profile_name(pipeline_spec)
            if profile_id not in affinity_data:
                affinity_data[profile_id] = materialize_affinity_profile(
                    pipeline_spec,
                )
    budget_enforcement_raw = raw.get("budget_enforcement")
    budget_enforcement_enabled = raw.get("budget_enforcement_enabled")
    if budget_enforcement_enabled is None and isinstance(
        budget_enforcement_raw, dict,
    ):
        budget_enforcement_enabled = budget_enforcement_raw.get("enabled")

    affinity_lanes = _build_affinity_lanes(workflow_data.get("affinity_lanes"))
    workflow_stages = _build_workflow_stages(
        workflow_data.get("stages"),
        roles,
        affinity_lanes,
    )
    workflow_kind_routes = _build_workflow_kind_routes(
        workflow_data.get("kind_routes"),
        stage_ids={stage.id for stage in workflow_stages if stage.id},
    )
    execution_profiles = _build_execution_profiles(
        workflow_data.get("execution_profiles"),
    )
    _validate_role_execution_profiles(roles, execution_profiles)
    _validate_stage_criteria_config_refs(
        config_path=path,
        stages=workflow_stages,
    )
    rework_routing = _derive_stage_backedge_rework_routing(workflow_stages)
    rework_routing.update(
        _validate_rework_routing(
            workflow_data.get("rework_routing", {}) or {},
            workflow_stages,
            roles,
        )
    )

    cfg = ZfConfig(
        version=str(raw.get("version", "1.0")),
        preset=raw.get("preset", ""),
        project=ProjectConfig(
            name=project_data["name"],
            description=_parse_project_description(project_data),
            workspace=project_data.get("workspace", "."),
            state_dir=project_data.get("state_dir", ".zf"),
            setup_script=_parse_project_setup_script(project_data),
        ),
        session=_build_session(session_data),
        orchestrator=OrchestratorConfig(
            backend=orch_data.get("backend", "python"),
            model=orch_data.get("model", ""),
            loop=LoopConfig(
                max_iterations=loop_data.get("max_iterations", 20),
                idle_exit_after=loop_data.get("idle_exit_after", 3),
            ),
            transport_timeout_s=float(orch_data.get("transport_timeout_s", 120.0)),
            max_turns=int(orch_data.get("max_turns", 30)),
            rate_limit_cooldown_s=float(orch_data.get("rate_limit_cooldown_s", 60.0)),
            wake_min_interval_s=float(orch_data.get("wake_min_interval_s", 5.0)),
        ),
        constraints=_build_constraints(raw.get("constraints")),
        workflow=WorkflowConfig(
            gan_rounds=workflow_data.get("gan_rounds", 1),
            harness_profile=harness_profile,
            # B14 (doc 93 §8): plan_approval 接受 bool 或 {enabled: bool}
            # B-93-02 (doc93 §8): plan_approval 未显式声明时按 harness_profile
            # 派生默认 —— strict/release 缺省人审 hold,baseline 缺省直行。
            plan_approval_enabled=_parse_plan_approval_enabled(
                workflow_data.get("plan_approval"),
                default=harness_profile in ("strict", "release"),
            ),
            # ⑤c:合并候选树门的显式豁免出口(观测型运行)。
            allow_unverified_candidate=bool(
                workflow_data.get("allow_unverified_candidate", False)
            ),
            candidate_quality_source=str(
                workflow_data.get("candidate_quality_source", "auto")
                or "auto"
            ),
            impl_self_check_required=bool(
                workflow_data.get("impl_self_check_required", False)
            ),
            orchestration=_build_workflow_orchestration(
                workflow_data.get("orchestration")
            ),
            event_actions=workflow_data.get("event_actions", []) or [],
            # 131-P2-3:lease 宽限可配置(F15 实证出厂 900s)。
            attempt_lease_grace_s=float(
                workflow_data.get("attempt_lease_grace_s", 900.0) or 900.0
            ),
            rework_routing=rework_routing,
            kind_routes=workflow_kind_routes,
            execution_profiles=execution_profiles,
            run_limits=_build_workflow_run_limits(
                workflow_data.get("run_limits")
            ),
            # R28 (doc 93 §1/§5): admission/W1 机械拒 → 自动回 synth。缺省关。
            admission_replan=_build_admission_replan(
                workflow_data.get("admission_replan")
            ),
            run_admission=_build_run_admission(
                workflow_data.get("run_admission")
            ),
            task_attempt=_build_task_attempt(
                workflow_data.get("task_attempt")
            ),
            stages=workflow_stages,
            generic_workflows=_build_generic_workflows(
                workflow_data.get("_generic_workflows")
            ),
            affinity_lanes=affinity_lanes,
            wake_extensions=_build_wake_extensions(
                workflow_data.get("wake_extensions")
            ),
            # P2/K4 (docs/impl/22): parse workflow.dag sub-section so
            # kernel can enforce required_backlog_refs + dev_requires_
            # orchestrator_backlog + stage_order. Absent in old yamls →
            # defaults give a no-enforcement DagConfig (backward compat).
            dag=_build_workflow_dag(workflow_data.get("dag")),
            # ZF-LH-INLINE-001 (doc 26 §3.3): parse
            # workflow.inline_overrides — operator emergency-skip
            # keywords inside user.message. Absent → default disabled.
            inline_overrides=_build_inline_overrides(
                workflow_data.get("inline_overrides")
            ),
            work_units=_build_workflow_work_units(
                workflow_data.get("work_units")
            ),
            completion_audit=_build_completion_audit(
                workflow_data.get("completion_audit")
            ),
            resume_packet=_build_resume_packet(
                workflow_data.get("resume_packet")
            ),
            integration=_build_integration(workflow_data.get("integration")),
            strict_triggers=_build_strict_triggers(
                workflow_data.get("strict_triggers")
            ),
            fast_path=_build_fast_path(workflow_data.get("fast_path")),
            replan_eval=_build_replan_eval(
                workflow_data.get("replan_eval"),
                harness_profile=harness_profile,
            ),
            flow_metadata=workflow_data.get("_flow_metadata", {}) or {},
            flow_metadata_by_kind=_build_flow_metadata_by_kind(
                workflow_data.get("_flow_metadata_by_kind")
            ),
            pipelines=pipelines,
            pipelines_role_meta=pipelines_role_meta,
        ),
        roles=roles,
        stage_labels=raw.get("stage_labels", {}) or {},
        quality_gates=_build_quality_gates(raw.get("quality_gates")),
        security=_build_security(raw.get("security")),
        safety=_build_safety(raw.get("safety")),
        verification=_build_verification(
            raw.get("verification"),
            contract_hardening_default=bool(
                (raw.get("autopilot") or {}).get("enabled", False)
            ),
        ),
        runtime=_build_runtime(
            raw.get("runtime"),
            roles=roles,
            execution_profiles=execution_profiles,
        ),
        providers=_build_providers(raw.get("providers")),
        integrations=_build_integrations(raw.get("integrations")),
        channel=_build_channel(raw.get("channel")),
        autopilot=_build_autopilot(raw.get("autopilot")),
        autoresearch=_build_autoresearch(raw.get("autoresearch")),
        skill_sources=_build_skill_sources(raw.get("skill_sources")),
        cost=_build_cost(raw.get("cost")),
        observability=_build_observability(raw.get("observability")),
        global_budget_usd=(
            float(raw["global_budget_usd"])
            if raw.get("global_budget_usd") is not None else None
        ),
        budget_enforcement_enabled=_bool_value(
            budget_enforcement_enabled,
            default=True,
        ),
        budget_fail_closed=_bool_value(
            raw.get("budget_fail_closed"),
            default=False,
        ),
        goal=_build_goal(raw.get("goal")),
    )
    # ⑤ 续(2026-07-08):policy 字段执法化——`evidencePolicy: strict_refs`
    # 从 advisory 旋钮升为执法开关的**驱动源**(单一控制点),终结与
    # verification.* 的平行重复声明:未显式配置时派生
    # event_schema.mode=blocking + report_evidence_gate=fail_closed;
    # 显式配置(项目 yaml 或 uses profile 合并进 raw)优先,是逃生门。
    flow_metadata_values = [
        cfg.workflow.flow_metadata,
        *(cfg.workflow.flow_metadata_by_kind or {}).values(),
    ]
    if any(
        str((metadata or {}).get("evidence_policy") or "").strip()
        == "strict_refs"
        for metadata in flow_metadata_values
    ):
        verification_raw = raw.get("verification")
        if not isinstance(verification_raw, dict):
            verification_raw = {}
        event_schema_raw = verification_raw.get("event_schema")
        if not isinstance(event_schema_raw, dict):
            event_schema_raw = {}
        if "mode" not in event_schema_raw:
            cfg.verification.event_schema.mode = "blocking"
        if "report_evidence_gate" not in verification_raw:
            cfg.verification.report_evidence_gate = "fail_closed"
    # Keep the project-authored escape-hatch schemas separate from effective
    # profile output. Multi-kind pipelines must each merge against this same
    # declared base; feeding one kind's effective rules into the next kind
    # silently relaxes/overwrites contracts for shared event names.
    declared_event_schemas = dict(cfg.workflow.dag.event_schemas)
    # doc 90 增补:dag 顶层 schema_profile(不依赖 lane_pipeline 的引用位)。
    if cfg.workflow.dag.schema_profile:
        from zf.core.config.schema_profiles import (
            SchemaProfileError as _SPErr,
            merge_event_schemas as _merge,
        )
        try:
            effective, sources, schema_diags = _merge(
                profile_name=cfg.workflow.dag.schema_profile,
                spec_overrides=None,
                local_schemas=cfg.workflow.dag.event_schemas,
                harness_profile=cfg.workflow.harness_profile,
                extra_profiles=_envelope_profiles,
            )
        except _SPErr as exc:
            raise ConfigError(str(exc))
        errors = [d for d in schema_diags if d["severity"] == "ERROR"]
        if errors:
            raise ConfigError("; ".join(d["message"] for d in errors))
        for d in schema_diags:
            if d["severity"] == "WARN":
                print(f"Warning: {d['message']}", file=sys.stderr)
        cfg.workflow.dag.event_schemas = effective
        cfg.workflow.pipelines_schema_sources = sources
    if cfg.workflow.pipelines:
        # doc 90 A2: schemaProfile → effective event_schemas。merge 优先级
        # profile → spec.schema_overrides → 项目 dag.event_schemas(最高,
        # 逃生门);breaking override 在 strict/release 下 ConfigError。
        from zf.core.config.schema_profiles import (
            SchemaProfileError,
            merge_event_schemas,
        )
        pipeline_kinds = {
            str(getattr(pipeline, "flow_kind", "") or "").strip().lower()
            for pipeline in cfg.workflow.pipelines
            if str(getattr(pipeline, "flow_kind", "") or "").strip()
        }
        multi_kind = len(pipeline_kinds) > 1
        scoped_schemas: dict[str, dict[str, dict]] = {}
        for pipeline_spec in cfg.workflow.pipelines:
            profile_name = getattr(pipeline_spec, "schema_profile", "")
            if not profile_name:
                continue
            flow_kind = str(
                getattr(pipeline_spec, "flow_kind", "") or ""
            ).strip().lower()
            try:
                effective, sources, schema_diags = merge_event_schemas(
                    profile_name=profile_name,
                    spec_overrides=getattr(
                        pipeline_spec, "schema_overrides", {},
                    ),
                    local_schemas=(
                        declared_event_schemas
                        if multi_kind
                        else cfg.workflow.dag.event_schemas
                    ),
                    harness_profile=cfg.workflow.harness_profile,
                    extra_profiles=_envelope_profiles,
                )
            except SchemaProfileError as exc:
                raise ConfigError(str(exc))
            errors = [d for d in schema_diags if d["severity"] == "ERROR"]
            if errors:
                raise ConfigError(
                    "; ".join(d["message"] for d in errors)
                )
            for d in schema_diags:
                if d["severity"] == "WARN":
                    print(f"Warning: {d['message']}", file=sys.stderr)
            if multi_kind and flow_kind:
                scoped_schemas[flow_kind] = effective
            else:
                cfg.workflow.dag.event_schemas = effective
                cfg.workflow.pipelines_schema_sources = sources
        if scoped_schemas:
            cfg.workflow.dag.event_schemas_by_kind = scoped_schemas
    if cfg.workflow.generic_workflows:
        from zf.core.workflow.bounded_agent import validate_bounded_agent_config
        try:
            validate_bounded_agent_config(cfg)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        from zf.core.config.schema_profiles import (
            SchemaProfileError,
            merge_event_schemas,
        )

        try:
            effective, sources, schema_diags = merge_event_schemas(
                profile_name="generic-workflow/v1",
                spec_overrides=None,
                local_schemas=declared_event_schemas,
                harness_profile=cfg.workflow.harness_profile,
                extra_profiles=_envelope_profiles,
            )
        except SchemaProfileError as exc:
            raise ConfigError(str(exc))
        errors = [d for d in schema_diags if d["severity"] == "ERROR"]
        if errors:
            raise ConfigError("; ".join(d["message"] for d in errors))
        for diagnostic in schema_diags:
            if diagnostic["severity"] == "WARN":
                print(f"Warning: {diagnostic['message']}", file=sys.stderr)
        if cfg.workflow.pipelines:
            cfg.workflow.dag.event_schemas_by_kind = {
                **cfg.workflow.dag.event_schemas_by_kind,
                "workflow": effective,
            }
        else:
            cfg.workflow.dag.event_schemas = effective
        cfg.workflow.pipelines_schema_sources.update(sources)
    # W2(2026-06-11):runtime 路径默认从 project.state_dir 派生。
    # schema 默认值硬编码 .zf(v3 sim 实测撞 PathGuard 的根因家族);
    # 默认值即派生,显式非默认配置保留。"显式写 .zf/* 但 state_dir
    # 不同"的配置本会被 PathGuard 拒——派生改写使其落回合法区。
    state_dir_name = str(cfg.project.state_dir or ".zf")
    if state_dir_name != ".zf":
        _derived = {
            ("workdirs", "root"): (".zf/workdirs", f"{state_dir_name}/workdirs"),
            ("skills", "pool"): (".zf/skills", f"{state_dir_name}/skills"),
            ("skills", "lock_file"): (
                ".zf/skills.lock.json", f"{state_dir_name}/skills.lock.json",
            ),
        }
        for (section, field_name), (default, derived) in _derived.items():
            holder = getattr(cfg.runtime, section, None)
            if holder is not None and getattr(holder, field_name, "") == default:
                setattr(holder, field_name, derived)

    # V1-②(doc 90 §9.11):特化 role 的 publishes 从 stage 成员关系派生。
    # 仅填空(role.publishes 为空且出现在某 stage.roles)——显式 publishes
    # 永远最高;lane role 由 A1 生成器派生,此处覆盖手写特化 role
    # (spec 里少写一组事件名 = spec/status 泄漏少一处)。
    stage_child_events: dict[str, list[str]] = {}
    for stage in workflow_stages:
        events = [
            e for e in (
                getattr(stage.aggregate, "child_success_event", ""),
                getattr(stage.aggregate, "child_failure_event", ""),
            ) if e
        ]
        if not events:
            continue
        for role_name in getattr(stage, "roles", []) or []:
            stage_child_events.setdefault(str(role_name), events)
    if stage_child_events:
        for role in roles:
            if not getattr(role, "publishes", None) and role.name in stage_child_events:
                role.publishes = list(stage_child_events[role.name])

    # E sprint: warn on triggers that look like typos (not in known events
    # and not published by any role). publishes are user-extensible —
    # they declare new event names and are NOT validated.
    for warn in validate_role_event_names(cfg.roles):
        print(f"Warning: {warn}", file=sys.stderr)
    _validate_orchestration_blocking_pilot_tiers(cfg)
    cfg.config_sources = list(raw.get("_config_profile_sources", []) or [])
    return cfg


# P1-3 (2026-07-09): fail-closed key sets for the security-relevant config
# sections. A typo'd sub-key (e.g. `event_signing.enable` missing the 'd')
# previously fell back to its default, silently disabling a gate while
# `zf validate` stayed green (the ⑤ "opt-in default-off, invisibly off"
# amplifier). Reject unknown keys so the typo surfaces instead of degrading a
# security/verification gate. Value validation (enum checks) already existed;
# this adds key-name validation, which was the gap.
_KNOWN_SECURITY_KEYS = frozenset({"event_signing"})
_KNOWN_EVENT_SIGNING_KEYS = frozenset(
    {"enabled", "secret_env", "allow_unsigned_fallback"}
)
_KNOWN_SAFETY_KEYS = frozenset({"tool_closure"})
_KNOWN_TOOL_CLOSURE_KEYS = frozenset({"enabled"})
_KNOWN_VERIFICATION_KEYS = frozenset({
    "contract", "semantic", "scope", "architecture", "promoted",
    "event_schema", "snapshot_gate", "report_evidence_gate",
})
_KNOWN_CONTRACT_KEYS = frozenset({
    "required", "quality_required", "rework_delta_required",
    "dispatch_token_required",
})
_KNOWN_ENABLED_ONLY_KEYS = frozenset({"enabled"})
_KNOWN_SCOPE_KEYS = frozenset({"fail_closed"})
_KNOWN_EVENT_SCHEMA_KEYS = frozenset({"mode"})


def _build_security(data: dict | None) -> SecurityConfig:
    if not data:
        return SecurityConfig()
    if not isinstance(data, dict):
        raise ConfigError("security must be a mapping")
    _reject_unknown_keys(data, _KNOWN_SECURITY_KEYS, "security")
    es_data = data.get("event_signing") or {}
    if not isinstance(es_data, dict):
        raise ConfigError("security.event_signing must be a mapping")
    _reject_unknown_keys(
        es_data, _KNOWN_EVENT_SIGNING_KEYS, "security.event_signing"
    )
    return SecurityConfig(
        event_signing=EventSigningConfig(
            enabled=bool(es_data.get("enabled", False)),
            secret_env=str(es_data.get("secret_env", "ZF_EVENT_SECRET")),
            allow_unsigned_fallback=bool(
                es_data.get("allow_unsigned_fallback", False)
            ),
        ),
    )


def _build_safety(data: dict | None) -> SafetyConfig:
    if not data:
        return SafetyConfig()
    if not isinstance(data, dict):
        raise ConfigError("safety must be a mapping")
    _reject_unknown_keys(data, _KNOWN_SAFETY_KEYS, "safety")
    tool_closure = data.get("tool_closure") or {}
    if not isinstance(tool_closure, dict):
        raise ConfigError("safety.tool_closure must be a mapping")
    _reject_unknown_keys(
        tool_closure, _KNOWN_TOOL_CLOSURE_KEYS, "safety.tool_closure"
    )
    return SafetyConfig(
        tool_closure_enabled=bool(tool_closure.get("enabled", True)),
    )


def _build_verification(
    data: dict | None,
    *,
    contract_hardening_default: bool = False,
) -> VerificationConfig:
    if not data:
        return VerificationConfig(
            contract=ContractDConfig(
                quality_required=contract_hardening_default,
                rework_delta_required=contract_hardening_default,
                dispatch_token_required=contract_hardening_default,
            ),
        )
    if not isinstance(data, dict):
        raise ConfigError("verification must be a mapping")
    _reject_unknown_keys(data, _KNOWN_VERIFICATION_KEYS, "verification")
    contract = data.get("contract") or {}
    semantic = data.get("semantic") or {}
    scope = data.get("scope") or {}
    architecture = data.get("architecture") or {}
    promoted = data.get("promoted") or {}
    event_schema = data.get("event_schema") or {}
    if not isinstance(contract, dict):
        raise ConfigError("verification.contract must be a mapping")
    if not isinstance(semantic, dict):
        raise ConfigError("verification.semantic must be a mapping")
    if not isinstance(scope, dict):
        raise ConfigError("verification.scope must be a mapping")
    if not isinstance(architecture, dict):
        raise ConfigError("verification.architecture must be a mapping")
    if not isinstance(promoted, dict):
        raise ConfigError("verification.promoted must be a mapping")
    if not isinstance(event_schema, dict):
        raise ConfigError("verification.event_schema must be a mapping")
    _reject_unknown_keys(contract, _KNOWN_CONTRACT_KEYS, "verification.contract")
    _reject_unknown_keys(
        semantic, _KNOWN_ENABLED_ONLY_KEYS, "verification.semantic"
    )
    _reject_unknown_keys(scope, _KNOWN_SCOPE_KEYS, "verification.scope")
    _reject_unknown_keys(
        architecture, _KNOWN_ENABLED_ONLY_KEYS, "verification.architecture"
    )
    _reject_unknown_keys(
        promoted, _KNOWN_ENABLED_ONLY_KEYS, "verification.promoted"
    )
    _reject_unknown_keys(
        event_schema, _KNOWN_EVENT_SCHEMA_KEYS, "verification.event_schema"
    )
    # TR-EVENT-SCHEMA-LOCK-001 step 2/3 (doc 42 §11.3 A): event_schema.mode
    # is one of {disabled, warning, blocking}. Unknown values raise — surface
    # operator typos rather than silently degrading.
    event_schema_mode = str(event_schema.get("mode", "disabled"))
    if event_schema_mode not in {"disabled", "warning", "blocking"}:
        raise ConfigError(
            f"verification.event_schema.mode must be one of "
            f"disabled / warning / blocking; got {event_schema_mode!r}"
        )
    # LH-B1: stale-runtime-snapshot gate staging. Unknown values raise to
    # surface operator typos rather than silently defaulting.
    snapshot_gate = str(data.get("snapshot_gate", "enforced"))
    if snapshot_gate not in {"off", "shadow", "enforced"}:
        raise ConfigError(
            f"verification.snapshot_gate must be one of "
            f"off / shadow / enforced; got {snapshot_gate!r}"
        )
    # U20 → LB-4: 审角色报告证据门的 fail-closed 开关。
    report_evidence_gate = str(data.get("report_evidence_gate", "signal"))
    if report_evidence_gate not in {"signal", "fail_closed"}:
        raise ConfigError(
            f"verification.report_evidence_gate must be one of "
            f"signal / fail_closed; got {report_evidence_gate!r}"
        )
    return VerificationConfig(
        contract=ContractDConfig(
            required=bool(contract.get("required", False)),
            quality_required=bool(
                contract.get("quality_required", contract_hardening_default),
            ),
            rework_delta_required=bool(
                contract.get("rework_delta_required", contract_hardening_default),
            ),
            dispatch_token_required=bool(
                contract.get("dispatch_token_required", contract_hardening_default),
            ),
        ),
        semantic=SemanticDConfig(
            enabled=bool(semantic.get("enabled", False)),
        ),
        scope=ScopeVerificationConfig(
            fail_closed=bool(scope.get("fail_closed", False)),
        ),
        architecture=RuntimeRuleDConfig(
            enabled=bool(architecture.get("enabled", False)),
        ),
        promoted=RuntimeRuleDConfig(
            enabled=bool(promoted.get("enabled", False)),
        ),
        event_schema=EventSchemaValidationConfig(
            mode=event_schema_mode,
        ),
        snapshot_gate=snapshot_gate,
        report_evidence_gate=report_evidence_gate,
    )


def _build_execution_routes(
    data: dict,
    *,
    roles: list[RoleConfig],
    execution_profiles: dict[str, ExecutionProfileConfig],
) -> list[ExecutionRouteConfig]:
    raw_routes = data.get("routes") or []
    if not isinstance(raw_routes, list):
        raise ConfigError("runtime.execution_routing.routes must be a list")
    known_keys = frozenset({
        "id",
        "roles",
        "flow_kinds",
        "backend",
        "model",
        "model_reasoning_effort",
        "execution_profile",
        "provider_session",
        "automatic_triggers",
    })
    route_id_re = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
    available_profiles = {"direct-v1", *execution_profiles}
    role_refs = {
        ref
        for role in roles
        for ref in (str(role.name or "").strip(), str(role.instance_id or "").strip())
        if ref
    }
    seen: set[str] = set()
    routes: list[ExecutionRouteConfig] = []
    for index, raw_route in enumerate(raw_routes):
        owner = f"runtime.execution_routing.routes[{index}]"
        if not isinstance(raw_route, dict):
            raise ConfigError(f"{owner} must be a mapping")
        _reject_unknown_keys(raw_route, known_keys, owner)
        route_id = str(raw_route.get("id") or "").strip()
        if not route_id_re.fullmatch(route_id):
            raise ConfigError(
                f"{owner}.id must start with a lowercase letter and contain "
                "only lowercase letters, digits, _ or -"
            )
        if route_id in seen:
            raise ConfigError(
                f"runtime.execution_routing.routes duplicates id {route_id!r}"
            )
        seen.add(route_id)
        route_roles = _string_list(raw_route.get("roles"))
        if not route_roles:
            raise ConfigError(f"{owner}.roles must contain at least one role")
        unknown_roles = sorted(set(route_roles) - role_refs)
        if unknown_roles:
            raise ConfigError(
                f"{owner}.roles references unknown role(s): "
                + ", ".join(unknown_roles)
            )
        flow_kinds = _string_list(raw_route.get("flow_kinds"))
        unknown_flow_kinds = sorted(
            set(flow_kinds) - {"issue", "prd", "refactor", "workflow"}
        )
        if unknown_flow_kinds:
            raise ConfigError(
                f"{owner}.flow_kinds contains unsupported value(s): "
                + ", ".join(unknown_flow_kinds)
            )
        backend = str(raw_route.get("backend") or "").strip()
        if backend not in _VALID_REPAIR_BACKENDS:
            raise ConfigError(
                f"{owner}.backend must be one of {_VALID_REPAIR_BACKENDS}"
            )
        execution_profile = str(
            raw_route.get("execution_profile") or ""
        ).strip()
        if execution_profile and execution_profile not in available_profiles:
            raise ConfigError(
                f"{owner}.execution_profile references unknown profile "
                f"{execution_profile!r}"
            )
        triggers = _string_list(raw_route.get("automatic_triggers"))
        if not triggers:
            raise ConfigError(
                f"{owner}.automatic_triggers must contain at least one trigger"
            )
        unknown_triggers = sorted(
            set(triggers) - set(_VALID_EXECUTION_ROUTE_TRIGGERS)
        )
        if unknown_triggers:
            raise ConfigError(
                f"{owner}.automatic_triggers contains unsupported value(s): "
                + ", ".join(unknown_triggers)
            )
        provider_session = _build_provider_session(
            raw_route.get("provider_session"),
            role_name=f"execution route {route_id}",
        )
        try:
            route = ExecutionRouteConfig(
                id=route_id,
                roles=route_roles,
                flow_kinds=flow_kinds,
                backend=backend,
                model=str(raw_route.get("model") or "").strip(),
                model_reasoning_effort=str(
                    raw_route.get("model_reasoning_effort") or ""
                ).strip(),
                execution_profile=execution_profile,
                provider_session=provider_session,
                automatic_triggers=triggers,
            )
        except ValueError as exc:
            raise ConfigError(f"Invalid {owner}: {exc}") from exc
        matching_roles = [
            role
            for role in roles
            if role.name in route_roles or role.instance_id in route_roles
        ]
        for role in matching_roles:
            if execution_profile and execution_profile not in set(
                role.execution.profile_allowlist
            ):
                raise ConfigError(
                    f"{owner}.execution_profile {execution_profile!r} is not "
                    f"allowed by role {role.instance_id!r}"
                )
            effective_execution = role.execution
            if execution_profile:
                effective_execution = replace(
                    effective_execution,
                    default_profile=execution_profile,
                )
            effective_role = replace(
                role,
                backend=backend,
                model=route.model,
                model_reasoning_effort=route.model_reasoning_effort,
                provider_session=provider_session,
                execution=effective_execution,
            )
            try:
                from zf.runtime.backend import (
                    get_adapter,
                    validate_provider_session_config,
                )

                validate_provider_session_config(
                    effective_role,
                    capabilities=get_adapter(backend).capabilities,
                )
            except ValueError as exc:
                raise ConfigError(f"Invalid {owner}: {exc}") from exc
        routes.append(route)
    return routes


def _build_runtime(
    data: dict | None,
    *,
    roles: list[RoleConfig] | None = None,
    execution_profiles: dict[str, ExecutionProfileConfig] | None = None,
) -> RuntimeConfig:
    if not data:
        return RuntimeConfig()
    if not isinstance(data, dict):
        raise ConfigError("runtime must be a mapping")
    workdirs_raw = data.get("workdirs") or {}
    git_raw = data.get("git") or {}
    skills_raw = data.get("skills") or {}
    run_manager_raw = data.get("run_manager") or {}
    autoresearch_resident_raw = data.get("autoresearch_resident") or {}
    evolution_raw = data.get("evolution") or {}
    execution_routing_raw = data.get("execution_routing") or {}
    feishu_inbound_raw = data.get("feishu_inbound") or {}
    feishu_projection_raw = data.get("feishu_projection") or {}
    web_terminal_raw = data.get("web_terminal") or {}
    if not isinstance(workdirs_raw, dict):
        raise ConfigError("runtime.workdirs must be a mapping")
    if not isinstance(git_raw, dict):
        raise ConfigError("runtime.git must be a mapping")
    if not isinstance(skills_raw, dict):
        raise ConfigError("runtime.skills must be a mapping")
    if not isinstance(run_manager_raw, dict):
        raise ConfigError("runtime.run_manager must be a mapping")
    if not isinstance(autoresearch_resident_raw, dict):
        raise ConfigError("runtime.autoresearch_resident must be a mapping")
    if not isinstance(evolution_raw, dict):
        raise ConfigError("runtime.evolution must be a mapping")
    if not isinstance(execution_routing_raw, dict):
        raise ConfigError("runtime.execution_routing must be a mapping")
    _reject_unknown_keys(
        execution_routing_raw,
        {
            "enabled",
            "max_switches_per_task",
            "semantic_triage_attempt",
            "routes",
        },
        "runtime.execution_routing",
    )
    if not isinstance(feishu_inbound_raw, dict):
        raise ConfigError("runtime.feishu_inbound must be a mapping")
    if not isinstance(feishu_projection_raw, dict):
        raise ConfigError("runtime.feishu_projection must be a mapping")
    if not isinstance(web_terminal_raw, dict):
        raise ConfigError("runtime.web_terminal must be a mapping")
    _reject_unknown_keys(
        web_terminal_raw,
        {
            "enabled",
            "backend",
            "herdr_binary",
            "minimum_herdr_version",
            "allowed_origins",
            "max_sessions",
            "max_attachments_per_session",
            "max_cols",
            "max_rows",
            "max_input_bytes",
            "max_frame_bytes",
            "bridge_queue_frames",
            "bridge_queue_bytes",
            "ticket_ttl_seconds",
            "provider_start_timeout_seconds",
            "allow_takeover",
        },
        "runtime.web_terminal",
    )
    web_terminal_backend = str(
        web_terminal_raw.get("backend", "herdr") or "herdr"
    ).strip()
    if web_terminal_backend != "herdr":
        raise ConfigError("runtime.web_terminal.backend must be 'herdr'")
    herdr_binary = str(
        web_terminal_raw.get("herdr_binary", "herdr") or "herdr"
    ).strip()
    if (
        not herdr_binary
        or "\x00" in herdr_binary
        or any(char.isspace() for char in herdr_binary)
        or ("/" in herdr_binary and not Path(herdr_binary).is_absolute())
    ):
        raise ConfigError(
            "runtime.web_terminal.herdr_binary must be a bare command or absolute path"
        )
    minimum_herdr_version = str(
        web_terminal_raw.get("minimum_herdr_version", "0.8.0") or "0.8.0"
    ).strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", minimum_herdr_version):
        raise ConfigError(
            "runtime.web_terminal.minimum_herdr_version must be semver X.Y.Z"
        )
    allowed_terminal_origins = _string_list(
        web_terminal_raw.get("allowed_origins"),
        default=[],
    )
    invalid_terminal_origins: list[str] = []
    for origin in allowed_terminal_origins:
        try:
            parsed_origin = urlsplit(origin)
            _ = parsed_origin.port
            valid_origin = (
                parsed_origin.scheme in {"http", "https"}
                and bool(parsed_origin.hostname)
                and parsed_origin.username is None
                and parsed_origin.password is None
                and not parsed_origin.path
                and not parsed_origin.query
                and not parsed_origin.fragment
                and origin == f"{parsed_origin.scheme}://{parsed_origin.netloc}"
            )
        except ValueError:
            valid_origin = False
        if not valid_origin:
            invalid_terminal_origins.append(origin)
    if invalid_terminal_origins:
        raise ConfigError(
            "runtime.web_terminal.allowed_origins entries must be HTTP(S) origins"
        )
    terminal_numeric_defaults = {
        "max_sessions": 8,
        "max_attachments_per_session": 8,
        "max_cols": 400,
        "max_rows": 200,
        "max_input_bytes": 64 * 1024,
        "max_frame_bytes": 4 * 1024 * 1024,
        "bridge_queue_frames": 64,
        "bridge_queue_bytes": 8 * 1024 * 1024,
        "ticket_ttl_seconds": 30,
        "provider_start_timeout_seconds": 60,
    }
    terminal_numeric_raw = {
        key: web_terminal_raw.get(key, default)
        for key, default in terminal_numeric_defaults.items()
    }
    if any(
        isinstance(value, (bool, float)) for value in terminal_numeric_raw.values()
    ):
        raise ConfigError("runtime.web_terminal limits must be integers")
    try:
        terminal_numeric = {
            key: int(value) for key, value in terminal_numeric_raw.items()
        }
    except (TypeError, ValueError) as exc:
        raise ConfigError("runtime.web_terminal limits must be integers") from exc
    if not 1 <= terminal_numeric["max_sessions"] <= 64:
        raise ConfigError("runtime.web_terminal.max_sessions must be between 1 and 64")
    if not 1 <= terminal_numeric["max_attachments_per_session"] <= 64:
        raise ConfigError(
            "runtime.web_terminal.max_attachments_per_session must be between 1 and 64"
        )
    if not 20 <= terminal_numeric["max_cols"] <= 1000:
        raise ConfigError("runtime.web_terminal.max_cols must be between 20 and 1000")
    if not 5 <= terminal_numeric["max_rows"] <= 500:
        raise ConfigError("runtime.web_terminal.max_rows must be between 5 and 500")
    if not 1024 <= terminal_numeric["max_input_bytes"] <= 1024 * 1024:
        raise ConfigError(
            "runtime.web_terminal.max_input_bytes must be between 1024 and 1048576"
        )
    if not 64 * 1024 <= terminal_numeric["max_frame_bytes"] <= 32 * 1024 * 1024:
        raise ConfigError(
            "runtime.web_terminal.max_frame_bytes must be between 65536 and 33554432"
        )
    if not 4 <= terminal_numeric["bridge_queue_frames"] <= 1024:
        raise ConfigError(
            "runtime.web_terminal.bridge_queue_frames must be between 4 and 1024"
        )
    if terminal_numeric["bridge_queue_bytes"] < terminal_numeric["max_frame_bytes"]:
        raise ConfigError(
            "runtime.web_terminal.bridge_queue_bytes must be >= max_frame_bytes"
        )
    if terminal_numeric["bridge_queue_bytes"] > 256 * 1024 * 1024:
        raise ConfigError(
            "runtime.web_terminal.bridge_queue_bytes must be <= 268435456"
        )
    aggregate_terminal_queue_budget = (
        terminal_numeric["max_sessions"]
        * terminal_numeric["max_attachments_per_session"]
        * terminal_numeric["bridge_queue_bytes"]
    )
    if aggregate_terminal_queue_budget > 512 * 1024 * 1024:
        raise ConfigError(
            "runtime.web_terminal aggregate bridge queue budget must be <= 536870912"
        )
    if not 5 <= terminal_numeric["ticket_ttl_seconds"] <= 300:
        raise ConfigError(
            "runtime.web_terminal.ticket_ttl_seconds must be between 5 and 300"
        )
    if not 4 <= terminal_numeric["provider_start_timeout_seconds"] <= 300:
        raise ConfigError(
            "runtime.web_terminal.provider_start_timeout_seconds must be between 4 and 300"
        )
    execution_routes = _build_execution_routes(
        execution_routing_raw,
        roles=roles or [],
        execution_profiles=execution_profiles or {},
    )
    execution_routing_enabled = _bool_value(
        execution_routing_raw.get("enabled"),
        default=False,
    )
    try:
        execution_routing_max_switches = int(
            execution_routing_raw.get("max_switches_per_task", 1)
            if execution_routing_raw.get("max_switches_per_task") is not None
            else 1
        )
        execution_routing_triage_attempt = int(
            execution_routing_raw.get("semantic_triage_attempt", 3)
            if execution_routing_raw.get("semantic_triage_attempt") is not None
            else 3
        )
        RuntimeExecutionRoutingConfig(
            enabled=execution_routing_enabled,
            max_switches_per_task=execution_routing_max_switches,
            semantic_triage_attempt=execution_routing_triage_attempt,
            routes=execution_routes,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid runtime.execution_routing: {exc}") from exc
    if execution_routing_enabled and not execution_routes:
        raise ConfigError(
            "runtime.execution_routing.routes is required when enabled is true"
        )
    if execution_routing_enabled and execution_routing_max_switches < 1:
        raise ConfigError(
            "runtime.execution_routing.max_switches_per_task must be >= 1 "
            "when enabled is true"
        )
    resident_raw = run_manager_raw.get("resident_agent") or {}
    if not isinstance(resident_raw, dict):
        raise ConfigError("runtime.run_manager.resident_agent must be a mapping")
    reflect_raw = run_manager_raw.get("reflect") or {}
    if not isinstance(reflect_raw, dict):
        raise ConfigError("runtime.run_manager.reflect must be a mapping")
    source_repair_raw = run_manager_raw.get("source_repair") or {}
    if not isinstance(source_repair_raw, dict):
        raise ConfigError("runtime.run_manager.source_repair must be a mapping")
    mode = str(workdirs_raw.get("mode", "dry-run"))
    if mode not in _VALID_WORKDIR_MODES:
        raise ConfigError(
            f"Invalid runtime.workdirs.mode {mode!r}: "
            f"must be one of {_VALID_WORKDIR_MODES}"
        )
    skill_materialize = str(skills_raw.get("materialize", "copy"))
    if skill_materialize not in _VALID_SKILL_MATERIALIZE_MODES:
        raise ConfigError(
            f"Invalid runtime.skills.materialize {skill_materialize!r}: "
            f"must be one of {_VALID_SKILL_MATERIALIZE_MODES}"
        )
    run_manager_backend = str(run_manager_raw.get("backend", "") or "").strip()
    if run_manager_backend and run_manager_backend not in _VALID_REPAIR_BACKENDS:
        raise ConfigError(
            f"Invalid runtime.run_manager.backend {run_manager_backend!r}: "
            f"must be one of {_VALID_REPAIR_BACKENDS}"
        )
    reflect_backend = str(reflect_raw.get("backend", "") or "").strip()
    if reflect_backend and reflect_backend not in _VALID_REPAIR_BACKENDS:
        raise ConfigError(
            f"Invalid runtime.run_manager.reflect.backend {reflect_backend!r}: "
            f"must be one of {_VALID_REPAIR_BACKENDS}"
        )
    source_repair_backend = str(
        source_repair_raw.get("backend", "") or ""
    ).strip()
    if source_repair_backend and source_repair_backend not in _VALID_REPAIR_BACKENDS:
        raise ConfigError(
            "Invalid runtime.run_manager.source_repair.backend "
            f"{source_repair_backend!r}: must be one of {_VALID_REPAIR_BACKENDS}"
        )
    source_repair_mode = str(
        source_repair_raw.get("mode", "isolated_worktree") or "isolated_worktree"
    ).strip()
    if source_repair_mode != "isolated_worktree":
        raise ConfigError(
            "runtime.run_manager.source_repair.mode currently only supports "
            "'isolated_worktree'"
        )
    source_repair_apply_policy = str(
        source_repair_raw.get("apply_policy", "proposal_only") or "proposal_only"
    ).strip()
    if source_repair_apply_policy not in {
        "proposal_only",
        "verified_checkpoint_apply",
    }:
        raise ConfigError(
            "runtime.run_manager.source_repair.apply_policy must be one of "
            "'proposal_only' or 'verified_checkpoint_apply'"
        )
    source_repair_restart_policy = str(
        source_repair_raw.get("restart_policy", "never_during_active_run")
        or "never_during_active_run"
    ).strip()
    if source_repair_restart_policy not in {
        "never_during_active_run",
        "operator_approved",
        "next_run",
    }:
        raise ConfigError(
            "Invalid runtime.run_manager.source_repair.restart_policy "
            f"{source_repair_restart_policy!r}"
        )
    source_repair_restart_boundary = str(
        source_repair_raw.get(
            "restart_boundary",
            "terminal_or_operator_approved_checkpoint",
        )
        or "terminal_or_operator_approved_checkpoint"
    ).strip()
    reflect_timeout_seconds = int(reflect_raw.get("timeout_seconds", 180) or 180)
    if reflect_timeout_seconds <= 0:
        raise ConfigError("runtime.run_manager.reflect.timeout_seconds must be > 0")
    resident_transport = str(
        resident_raw.get("transport", "tmux") or "tmux"
    ).strip()
    if resident_transport not in _VALID_TRANSPORTS:
        raise ConfigError(
            "Invalid runtime.run_manager.resident_agent.transport "
            f"{resident_transport!r}: must be one of {_VALID_TRANSPORTS}"
        )
    if resident_transport != "tmux":
        raise ConfigError(
            "runtime.run_manager.resident_agent.transport currently only "
            "supports 'tmux'"
        )
    resident_instance_id = str(
        resident_raw.get("instance_id", "run-manager") or "run-manager"
    ).strip()
    if not resident_instance_id:
        raise ConfigError(
            "runtime.run_manager.resident_agent.instance_id must be non-empty"
        )
    resident_session_mode = str(
        resident_raw.get("session_mode", "shared") or "shared"
    ).strip()
    if resident_session_mode not in _VALID_RUN_MANAGER_RESIDENT_SESSION_MODES:
        raise ConfigError(
            "Invalid runtime.run_manager.resident_agent.session_mode "
            f"{resident_session_mode!r}: must be one of "
            f"{_VALID_RUN_MANAGER_RESIDENT_SESSION_MODES}"
        )
    resident_tmux_session = str(
        resident_raw.get("tmux_session", "") or ""
    ).strip()
    resident_enabled = _bool_value(resident_raw.get("enabled"), default=False)
    if resident_enabled and not run_manager_backend:
        raise ConfigError(
            "runtime.run_manager.backend is required when "
            "runtime.run_manager.resident_agent.enabled is true"
        )
    try:
        autoresearch_resident_interval = float(
            autoresearch_resident_raw.get("interval_seconds", 10.0) or 10.0
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            "runtime.autoresearch_resident.interval_seconds must be numeric"
        ) from exc
    if autoresearch_resident_interval <= 0:
        raise ConfigError(
            "runtime.autoresearch_resident.interval_seconds must be > 0"
        )
    try:
        autoresearch_resident_max_actions = int(
            autoresearch_resident_raw.get("max_actions_per_tick", 1) or 1
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            "runtime.autoresearch_resident.max_actions_per_tick must be an integer"
        ) from exc
    if autoresearch_resident_max_actions <= 0:
        raise ConfigError(
            "runtime.autoresearch_resident.max_actions_per_tick must be > 0"
        )
    autoresearch_resident_backend = str(
        autoresearch_resident_raw.get("self_repair_backend", "") or ""
    ).strip()
    if (
        autoresearch_resident_backend
        and autoresearch_resident_backend not in _VALID_REPAIR_BACKENDS
    ):
        raise ConfigError(
            "Invalid runtime.autoresearch_resident.self_repair_backend "
            f"{autoresearch_resident_backend!r}: must be one of {_VALID_REPAIR_BACKENDS}"
        )
    evolution_mode = str(
        evolution_raw.get("mode", "evaluate_only") or "evaluate_only"
    ).strip()
    if evolution_mode not in _VALID_EVOLUTION_MODES:
        raise ConfigError(
            f"Invalid runtime.evolution.mode {evolution_mode!r}: "
            f"must be one of {_VALID_EVOLUTION_MODES}"
        )
    evolution_backend = str(evolution_raw.get("backend", "") or "").strip()
    if evolution_backend and evolution_backend not in _VALID_REPAIR_BACKENDS:
        raise ConfigError(
            f"Invalid runtime.evolution.backend {evolution_backend!r}: "
            f"must be one of {_VALID_REPAIR_BACKENDS}"
        )
    evolution_enabled = _bool_value(evolution_raw.get("enabled"), default=False)
    if evolution_enabled and not evolution_backend:
        raise ConfigError(
            "runtime.evolution.backend is required when runtime.evolution.enabled is true"
        )
    autoresearch_resident_enabled = _bool_value(
        autoresearch_resident_raw.get("enabled"),
        default=False,
    )
    evolution_sealed_root = str(
        evolution_raw.get("sealed_root", "") or ""
    ).strip()
    if evolution_enabled and not autoresearch_resident_enabled:
        raise ConfigError(
            "runtime.autoresearch_resident.enabled must be true when "
            "runtime.evolution.enabled is true"
        )
    if evolution_enabled and not evolution_sealed_root:
        raise ConfigError(
            "runtime.evolution.sealed_root is required when "
            "runtime.evolution.enabled is true"
        )
    try:
        evolution_trial_repetitions = int(
            evolution_raw.get("trial_repetitions", 2) or 2
        )
        evolution_trial_timeout = int(
            evolution_raw.get("trial_timeout_seconds", 300) or 300
        )
        evolution_lease_seconds = int(
            evolution_raw.get("lease_seconds", 600) or 600
        )
        evolution_max_attempts = int(
            evolution_raw.get("max_trial_attempts", 2) or 2
        )
        evolution_max_actions = int(
            evolution_raw.get("max_actions_per_tick", 4) or 4
        )
        evolution_max_cost = float(
            evolution_raw.get("max_cost_usd", 2.0) or 2.0
        )
        evolution_max_tokens = int(
            evolution_raw.get("max_tokens", 50_000) or 50_000
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError("runtime.evolution numeric values must be valid numbers") from exc
    if min(
        evolution_trial_repetitions,
        evolution_trial_timeout,
        evolution_lease_seconds,
        evolution_max_attempts,
        evolution_max_actions,
        evolution_max_tokens,
    ) <= 0 or evolution_max_cost <= 0:
        raise ConfigError("runtime.evolution numeric values must be > 0")
    evolution_auto_kinds = _string_list(
        evolution_raw.get("auto_asset_kinds"),
        default=list(_DEFAULT_EVOLUTION_AUTO_ASSET_KINDS),
    )
    unsupported_evolution_kinds = sorted(
        set(evolution_auto_kinds) - set(_VALID_EVOLUTION_AUTO_ASSET_KINDS)
    )
    if unsupported_evolution_kinds:
        raise ConfigError(
            "runtime.evolution.auto_asset_kinds may only contain policy-authorized kinds; "
            f"unsupported: {unsupported_evolution_kinds}"
        )
    evolution_token_env = str(
        evolution_raw.get("access_token_env", "ZF_EVOLUTION_EVALUATOR_TOKEN")
        or "ZF_EVOLUTION_EVALUATOR_TOKEN"
    ).strip()
    if not _ENV_NAME_RE.fullmatch(evolution_token_env):
        raise ConfigError("runtime.evolution.access_token_env must be an env variable name")
    feishu_inbound_mode = str(
        feishu_inbound_raw.get("mode", "bridge") or "bridge"
    ).strip()
    if feishu_inbound_mode not in _VALID_FEISHU_INBOUND_MODES:
        raise ConfigError(
            f"Invalid runtime.feishu_inbound.mode {feishu_inbound_mode!r}: "
            f"must be one of {_VALID_FEISHU_INBOUND_MODES}"
        )
    try:
        feishu_inbound_debounce_ms = int(
            feishu_inbound_raw.get("debounce_ms", 600)
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            "runtime.feishu_inbound.debounce_ms must be an integer"
        ) from exc
    if feishu_inbound_debounce_ms < 0:
        raise ConfigError("runtime.feishu_inbound.debounce_ms must be >= 0")
    feishu_allowed_senders_raw = feishu_inbound_raw.get("allowed_senders") or []
    if not isinstance(feishu_allowed_senders_raw, list):
        raise ConfigError("runtime.feishu_inbound.allowed_senders must be a list")
    feishu_allowed_senders = [
        str(value).strip()
        for value in feishu_allowed_senders_raw
        if str(value or "").strip()
    ]
    feishu_projection_backend = str(
        feishu_projection_raw.get("backend", "lark-cli") or "lark-cli"
    ).strip()
    if feishu_projection_backend not in _VALID_FEISHU_PROJECTION_BACKENDS:
        raise ConfigError(
            f"Invalid runtime.feishu_projection.backend {feishu_projection_backend!r}: "
            f"must be one of {_VALID_FEISHU_PROJECTION_BACKENDS}"
        )
    try:
        feishu_projection_poll_interval = float(
            feishu_projection_raw.get("poll_interval_seconds", 2.0)
        )
        feishu_projection_reconcile_interval = float(
            feishu_projection_raw.get("reconcile_interval_seconds", 3600.0)
        )
        feishu_projection_archive_days = int(
            feishu_projection_raw.get("include_archive_days", 30)
        )
        feishu_projection_max_actions = int(
            feishu_projection_raw.get("max_actions_per_tick", 20)
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            "runtime.feishu_projection numeric values must be valid numbers"
        ) from exc
    if feishu_projection_poll_interval <= 0:
        raise ConfigError(
            "runtime.feishu_projection.poll_interval_seconds must be > 0"
        )
    if feishu_projection_reconcile_interval <= 0:
        raise ConfigError(
            "runtime.feishu_projection.reconcile_interval_seconds must be > 0"
        )
    if feishu_projection_archive_days < 0:
        raise ConfigError(
            "runtime.feishu_projection.include_archive_days must be >= 0"
        )
    if feishu_projection_max_actions <= 0:
        raise ConfigError(
            "runtime.feishu_projection.max_actions_per_tick must be > 0"
        )
    candidate_strategy = str(git_raw.get("candidate_strategy", "cherry-pick"))
    if candidate_strategy not in _VALID_CANDIDATE_STRATEGIES:
        raise ConfigError(
            f"Invalid runtime.git.candidate_strategy {candidate_strategy!r}: "
            f"must be one of {_VALID_CANDIDATE_STRATEGIES}"
        )
    remote_policy = str(git_raw.get("remote_policy", "local"))
    if remote_policy not in _VALID_REMOTE_POLICIES:
        raise ConfigError(
            f"Invalid runtime.git.remote_policy {remote_policy!r}: "
            f"must be one of {_VALID_REMOTE_POLICIES}"
        )
    ship_candidate_strategy = str(git_raw.get("ship_candidate_strategy", "merge"))
    if ship_candidate_strategy not in _VALID_SHIP_CANDIDATE_STRATEGIES:
        raise ConfigError(
            "Invalid runtime.git.ship_candidate_strategy "
            f"{ship_candidate_strategy!r}: must be one of "
            f"{_VALID_SHIP_CANDIDATE_STRATEGIES}"
        )
    ship_task_strategy = str(git_raw.get("ship_task_strategy", "cherry-pick"))
    if ship_task_strategy not in _VALID_SHIP_TASK_STRATEGIES:
        raise ConfigError(
            f"Invalid runtime.git.ship_task_strategy {ship_task_strategy!r}: "
            f"must be one of {_VALID_SHIP_TASK_STRATEGIES}"
        )
    return RuntimeConfig(
        workdirs=WorkdirConfig(
            enabled=bool(workdirs_raw.get("enabled", False)),
            root=str(workdirs_raw.get("root", ".zf/workdirs")),
            mode=mode,
            provision_paths=[
                str(p).strip()
                for p in (workdirs_raw.get("provision_paths") or [])
                if str(p).strip()
            ],
        ),
        git=GitIsolationConfig(
            writer_branch_prefix=str(
                git_raw.get("writer_branch_prefix", "worker")
            ),
            task_ref_prefix=str(git_raw.get("task_ref_prefix", "task")),
            candidate_branch_prefix=str(
                git_raw.get("candidate_branch_prefix", "candidate")
            ),
            candidate_base_ref=str(git_raw.get("candidate_base_ref", "main")),
            candidate_strategy=candidate_strategy,
            remote_policy=remote_policy,
            ship_target_branch=str(
                git_raw.get("ship_target_branch", git_raw.get("ship_target", "main"))
            ),
            ship_candidate_strategy=ship_candidate_strategy,
            ship_task_strategy=ship_task_strategy,
            ship_final_command=str(git_raw.get("ship_final_command", "")),
            auto_ship_on_candidate_complete=bool(
                git_raw.get("auto_ship_on_candidate_complete", False)
            ),
            auto_ship_on_judge_passed=bool(
                git_raw.get("auto_ship_on_judge_passed", False)
            ),
        ),
        skills=RuntimeSkillsConfig(
            pool=str(skills_raw.get("pool", ".zf/skills")),
            materialize=skill_materialize,
            lock_file=str(skills_raw.get("lock_file", ".zf/skills.lock.json")),
            strict=bool(skills_raw.get("strict", False)),
        ),
        run_manager=RuntimeRunManagerConfig(
            backend=run_manager_backend,
            reflect=RuntimeRunManagerReflectConfig(
                enabled=_bool_value(reflect_raw.get("enabled"), default=False),
                backend=reflect_backend,
                timeout_seconds=reflect_timeout_seconds,
            ),
            resident_agent=RuntimeRunManagerResidentAgentConfig(
                enabled=resident_enabled,
                transport=resident_transport,
                instance_id=resident_instance_id,
                model=str(resident_raw.get("model", "") or "").strip(),
                model_reasoning_effort=str(
                    resident_raw.get("model_reasoning_effort", "") or ""
                ).strip(),
                prompt_on_start=_bool_value(
                    resident_raw.get("prompt_on_start"),
                    default=True,
                ),
                session_mode=resident_session_mode,
                tmux_session=resident_tmux_session,
            ),
            source_repair=RuntimeRunManagerSourceRepairConfig(
                enabled=_bool_value(
                    source_repair_raw.get("enabled"),
                    default=False,
                ),
                backend=source_repair_backend,
                mode=source_repair_mode,
                branch_prefix=str(
                    source_repair_raw.get(
                        "branch_prefix",
                        "self-repair/run-manager",
                    )
                    or "self-repair/run-manager"
                ),
                apply_policy=source_repair_apply_policy,
                restart_policy=source_repair_restart_policy,
                restart_boundary=source_repair_restart_boundary,
                replay_before_restart=_bool_value(
                    source_repair_raw.get("replay_before_restart"),
                    default=True,
                ),
                allow_paths=_string_list(
                    source_repair_raw.get("allow_paths"),
                    default=["src/zf/**", "tests/**", "docs/**"],
                ),
                deny_paths=_string_list(
                    source_repair_raw.get("deny_paths"),
                    default=[
                        ".env",
                        "**/events.jsonl",
                        "**/kanban.json",
                        "**/session.yaml",
                    ],
                ),
            ),
        ),
        autoresearch_resident=RuntimeAutoresearchResidentConfig(
            enabled=_bool_value(
                autoresearch_resident_raw.get("enabled"),
                default=False,
            ),
            interval_seconds=autoresearch_resident_interval,
            max_actions_per_tick=autoresearch_resident_max_actions,
            worktree_root=str(
                autoresearch_resident_raw.get(
                    "worktree_root",
                    "/tmp/zaofu-autoresearch-resident/worktrees",
                )
            ),
            output_root=str(autoresearch_resident_raw.get("output_root", "") or ""),
            self_repair_consumer=_bool_value(
                autoresearch_resident_raw.get("self_repair_consumer"),
                default=False,
            ),
            self_repair_spawn=_bool_value(
                autoresearch_resident_raw.get("self_repair_spawn"),
                default=False,
            ),
            self_repair_backend=autoresearch_resident_backend,
        ),
        evolution=RuntimeEvolutionConfig(
            enabled=evolution_enabled,
            mode=evolution_mode,
            backend=evolution_backend,
            model=str(evolution_raw.get("model", "") or "").strip(),
            model_reasoning_effort=str(
                evolution_raw.get("model_reasoning_effort", "") or ""
            ).strip(),
            trial_repetitions=evolution_trial_repetitions,
            trial_timeout_seconds=evolution_trial_timeout,
            lease_seconds=evolution_lease_seconds,
            max_trial_attempts=evolution_max_attempts,
            max_actions_per_tick=evolution_max_actions,
            max_cost_usd=evolution_max_cost,
            max_tokens=evolution_max_tokens,
            sealed_root=evolution_sealed_root,
            access_token_env=evolution_token_env,
            auto_asset_kinds=evolution_auto_kinds,
        ),
        execution_routing=RuntimeExecutionRoutingConfig(
            enabled=execution_routing_enabled,
            max_switches_per_task=execution_routing_max_switches,
            semantic_triage_attempt=execution_routing_triage_attempt,
            routes=execution_routes,
        ),
        feishu_inbound=RuntimeFeishuInboundConfig(
            enabled=_bool_value(feishu_inbound_raw.get("enabled"), default=False),
            mode=feishu_inbound_mode,
            debounce_ms=feishu_inbound_debounce_ms,
            require_routing=_bool_value(
                feishu_inbound_raw.get("require_routing"),
                default=True,
            ),
            allowed_senders=feishu_allowed_senders,
        ),
        feishu_projection=RuntimeFeishuProjectionConfig(
            enabled=_bool_value(
                feishu_projection_raw.get("enabled"),
                default=False,
            ),
            backend=feishu_projection_backend,
            auto_create_target=_bool_value(
                feishu_projection_raw.get("auto_create_target"),
                default=False,
            ),
            base_name=str(feishu_projection_raw.get("base_name") or "").strip(),
            table_name=str(
                feishu_projection_raw.get("table_name") or "Kanban"
            ).strip(),
            time_zone=str(
                feishu_projection_raw.get("time_zone") or "Asia/Shanghai"
            ).strip(),
            poll_interval_seconds=feishu_projection_poll_interval,
            reconcile_interval_seconds=feishu_projection_reconcile_interval,
            include_archive_days=feishu_projection_archive_days,
            max_actions_per_tick=feishu_projection_max_actions,
        ),
        web_terminal=RuntimeWebTerminalConfig(
            enabled=_bool_value(web_terminal_raw.get("enabled"), default=True),
            backend=web_terminal_backend,
            herdr_binary=herdr_binary,
            minimum_herdr_version=minimum_herdr_version,
            allowed_origins=allowed_terminal_origins,
            max_sessions=terminal_numeric["max_sessions"],
            max_attachments_per_session=terminal_numeric[
                "max_attachments_per_session"
            ],
            max_cols=terminal_numeric["max_cols"],
            max_rows=terminal_numeric["max_rows"],
            max_input_bytes=terminal_numeric["max_input_bytes"],
            max_frame_bytes=terminal_numeric["max_frame_bytes"],
            bridge_queue_frames=terminal_numeric["bridge_queue_frames"],
            bridge_queue_bytes=terminal_numeric["bridge_queue_bytes"],
            ticket_ttl_seconds=terminal_numeric["ticket_ttl_seconds"],
            provider_start_timeout_seconds=terminal_numeric[
                "provider_start_timeout_seconds"
            ],
            allow_takeover=_bool_value(
                web_terminal_raw.get("allow_takeover"), default=True
            ),
        ),
    )


def _build_providers(data: dict | None) -> ProvidersConfig:
    if not data:
        return ProvidersConfig()
    if not isinstance(data, dict):
        raise ConfigError("providers must be a mapping")
    return ProvidersConfig(
        openclaw=_build_openclaw_provider(data.get("openclaw")),
    )


def build_openclaw_provider_config(data: object) -> OpenClawProviderConfig:
    """Parse OpenClaw provider bindings from project or workspace metadata."""
    return _build_openclaw_provider(data)


def _build_openclaw_provider(data: object) -> OpenClawProviderConfig:
    if data in (None, ""):
        return OpenClawProviderConfig()
    if not isinstance(data, dict):
        raise ConfigError("providers.openclaw must be a mapping")
    default_binding = str(data.get("default_binding") or "").strip()
    raw_bindings = data.get("bindings")
    bindings_source: dict[str, object] = {}
    if raw_bindings is not None:
        if not isinstance(raw_bindings, dict):
            raise ConfigError("providers.openclaw.bindings must be a mapping")
        bindings_source.update(raw_bindings)
    for key, value in data.items():
        if key in {"bindings", "default_binding"}:
            continue
        if isinstance(value, dict):
            bindings_source[str(key)] = value
    bindings: dict[str, OpenClawRemoteBindingConfig] = {}
    for binding_id, raw_binding in bindings_source.items():
        binding_key = str(binding_id).strip()
        if not binding_key:
            raise ConfigError("providers.openclaw binding id is required")
        if not re.match(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$", binding_key):
            raise ConfigError(
                f"providers.openclaw binding id {binding_key!r} must start "
                "with a letter and contain only letters, digits, dot, "
                "underscore, or hyphen"
            )
        if not isinstance(raw_binding, dict):
            raise ConfigError(f"providers.openclaw.{binding_key} must be a mapping")
        bindings[binding_key] = _build_openclaw_binding(binding_key, raw_binding)
    if default_binding and default_binding not in bindings:
        raise ConfigError(
            f"providers.openclaw.default_binding {default_binding!r} "
            "does not reference a declared binding"
        )
    if not default_binding and "default" in bindings:
        default_binding = "default"
    return OpenClawProviderConfig(
        default_binding=default_binding,
        bindings=bindings,
    )


def _build_openclaw_binding(
    binding_id: str,
    data: dict[str, object],
) -> OpenClawRemoteBindingConfig:
    mode = str(data.get("mode") or "remote_gateway").strip()
    if mode not in _VALID_OPENCLAW_BINDING_MODES:
        raise ConfigError(
            f"providers.openclaw.{binding_id}.mode must be one of "
            f"{_VALID_OPENCLAW_BINDING_MODES}"
        )
    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ConfigError(f"providers.openclaw.{binding_id}.base_url is required")
    if not base_url.startswith(("http://", "https://")):
        raise ConfigError(
            f"providers.openclaw.{binding_id}.base_url must start with http:// or https://"
        )
    token_env = str(data.get("token_env") or "").strip()
    if token_env and not _ENV_NAME_RE.match(token_env):
        raise ConfigError(
            f"providers.openclaw.{binding_id}.token_env must be an environment "
            "variable name like OPENCLAW_GATEWAY_TOKEN"
        )
    workspace_policy = str(
        data.get("default_workspace_policy")
        or data.get("workspace_policy")
        or "isolated"
    ).strip()
    if workspace_policy not in _VALID_OPENCLAW_WORKSPACE_POLICIES:
        raise ConfigError(
            f"providers.openclaw.{binding_id}.default_workspace_policy must be "
            f"one of {_VALID_OPENCLAW_WORKSPACE_POLICIES}"
        )
    tool_profile = str(data.get("tool_profile") or "safe").strip()
    if tool_profile not in _VALID_OPENCLAW_TOOL_PROFILES:
        raise ConfigError(
            f"providers.openclaw.{binding_id}.tool_profile must be one of "
            f"{_VALID_OPENCLAW_TOOL_PROFILES}"
        )
    try:
        timeout_seconds = float(data.get("timeout_seconds") or 120.0)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"providers.openclaw.{binding_id}.timeout_seconds must be a number"
        ) from exc
    if timeout_seconds <= 0:
        raise ConfigError(
            f"providers.openclaw.{binding_id}.timeout_seconds must be > 0"
        )
    return OpenClawRemoteBindingConfig(
        id=binding_id,
        mode=mode,
        base_url=base_url,
        token_env=token_env,
        default_workspace_policy=workspace_policy,
        tool_profile=tool_profile,
        timeout_seconds=timeout_seconds,
        provision_agent=bool(data.get("provision_agent", False)),
    )


_FEISHU_YAML_KEYS = (
    "feishu_routing",
    "feishu_identity",
    "feishu_project_group",
)


def _merge_feishu_yaml(raw: dict, zf_yaml_path: Path) -> dict:
    """Merge a sibling ``feishu.yaml`` into ``raw["integrations"]`` so the Feishu
    adapter config can live in its own file while still compiling into the single
    ZfConfig (one validation, one truth). feishu.yaml may put the ``feishu_*`` keys
    at top level or under an ``integrations:`` block. A key present in BOTH zf.yaml
    and feishu.yaml is a ConfigError (no silent override / drift)."""
    if not isinstance(raw, dict):
        return raw
    feishu_path = zf_yaml_path.parent / "feishu.yaml"
    if not feishu_path.exists():
        return raw
    text = _expand_env_vars(
        feishu_path.read_text(encoding="utf-8"), _config_env_map(feishu_path))
    try:
        fdata = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"feishu.yaml parse error: {exc}")
    if not isinstance(fdata, dict):
        raise ConfigError("feishu.yaml must be a YAML mapping")
    nested = fdata.get("integrations")
    src = nested if isinstance(nested, dict) else fdata
    integrations = raw.get("integrations")
    integrations = dict(integrations) if isinstance(integrations, dict) else {}
    for key in _FEISHU_YAML_KEYS:
        if key not in src:
            continue
        if key in integrations:
            raise ConfigError(
                f"integrations.{key} is configured in both zf.yaml and feishu.yaml; "
                "keep it in exactly one place")
        integrations[key] = src[key]
    merged = dict(raw)
    merged["integrations"] = integrations
    return merged


def _build_integrations(data: object) -> IntegrationsConfig:
    if data in (None, ""):
        return IntegrationsConfig()
    if not isinstance(data, dict):
        raise ConfigError("integrations must be a mapping")
    return IntegrationsConfig(
        openclaw_feishu_bridge=_build_openclaw_feishu_bridge(
            data.get("openclaw_feishu_bridge")
        ),
        feishu_identity=_build_feishu_identity(data.get("feishu_identity")),
        feishu_project_group=_build_feishu_project_group(
            data.get("feishu_project_group")
        ),
        feishu_routing=_build_feishu_routing(data.get("feishu_routing")),
    )


_CHANNEL_PROFILE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_CHANNEL_VISIBILITY_CEILINGS = {
    "minimal",
    "planner",
    "reviewer",
    "owner_report",
    "full_audit",
}


def _build_channel(data: object) -> ChannelConfig:
    if data in (None, ""):
        return ChannelConfig()
    if not isinstance(data, dict):
        raise ConfigError("channel must be a mapping")
    _reject_unknown_keys(data, _KNOWN_CHANNEL_KEYS, "channel")
    raw_profiles = data.get("agent_profiles") or {}
    if not isinstance(raw_profiles, dict):
        raise ConfigError("channel.agent_profiles must be a mapping")

    profiles: dict[str, ChannelAgentProfileConfig] = {}
    for raw_profile_id, raw_profile in raw_profiles.items():
        profile_id = str(raw_profile_id or "").strip()
        context = f"channel.agent_profiles[{profile_id!r}]"
        if not _CHANNEL_PROFILE_ID_RE.match(profile_id):
            raise ConfigError(
                f"{context} id must start with a letter and contain only "
                "letters, digits, dot, underscore, or hyphen"
            )
        if not isinstance(raw_profile, dict):
            raise ConfigError(f"{context} must be a mapping")
        _reject_unknown_keys(
            raw_profile,
            _KNOWN_CHANNEL_AGENT_PROFILE_KEYS,
            context,
        )
        skill_refs = raw_profile.get("skill_refs") or []
        if not isinstance(skill_refs, list) or not all(
            isinstance(item, str) and item.strip() for item in skill_refs
        ):
            raise ConfigError(f"{context}.skill_refs must be a string list")
        visibility = str(
            raw_profile.get("visibility_ceiling") or "minimal"
        ).strip()
        if visibility not in _CHANNEL_VISIBILITY_CEILINGS:
            raise ConfigError(
                f"{context}.visibility_ceiling must be one of "
                f"{sorted(_CHANNEL_VISIBILITY_CEILINGS)}"
            )
        permission = str(
            raw_profile.get("permission_ceiling") or "read_only"
        ).strip().lower().replace("-", "_").replace(" ", "_")
        if permission not in _FEISHU_PERMISSION_PROFILES:
            raise ConfigError(
                f"{context}.permission_ceiling must be one of "
                f"{sorted(_FEISHU_PERMISSION_PROFILES)}"
            )
        try:
            profiles[profile_id] = ChannelAgentProfileConfig(
                revision=int(raw_profile.get("revision", 1)),
                persona=str(raw_profile.get("persona") or ""),
                display_name=str(raw_profile.get("display_name") or ""),
                channel_role=str(raw_profile.get("channel_role") or ""),
                provider=str(raw_profile.get("provider") or ""),
                backend=str(raw_profile.get("backend") or ""),
                model=str(raw_profile.get("model") or ""),
                role_context_ref=str(
                    raw_profile.get("role_context_ref") or ""
                ),
                skill_refs=[str(item).strip() for item in skill_refs],
                visibility_ceiling=visibility,
                permission_ceiling=permission,
                lifecycle=str(
                    raw_profile.get("lifecycle") or "persistent"
                ).strip(),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{context}: {exc}") from exc
    budget = data.get("semantic_source_max_chars", DEFAULT_CHANNEL_SEMANTIC_SOURCE_CHARS)
    if type(budget) is not int or not 1 <= budget <= MAX_CHANNEL_SEMANTIC_SOURCE_CHARS:
        raise ConfigError(
            "channel.semantic_source_max_chars must be an integer between 1 and "
            f"{MAX_CHANNEL_SEMANTIC_SOURCE_CHARS}"
        )
    context_window = data.get("codex_context_window_tokens", 0)
    if type(context_window) is not int or not 0 <= context_window <= 1_000_000:
        raise ConfigError("channel.codex_context_window_tokens must be an integer between 0 and 1000000")
    timeout = data.get("provider_timeout_seconds", 0)
    if type(timeout) is not int or not 0 <= timeout <= 7200:
        raise ConfigError("channel.provider_timeout_seconds must be an integer between 0 and 7200")
    return ChannelConfig(agent_profiles=profiles, semantic_source_max_chars=budget,
                         codex_context_window_tokens=context_window, provider_timeout_seconds=timeout)


_FEISHU_ROUTE_TARGETS = {
    "channel",
    "kanban_agent",
    "run_manager",
    "worker",
    "agent",
}
_FEISHU_PERMISSION_PROFILES = {
    "read_only",
    "operator",
    "artifact_writer",
    "project_writer",
    "workspace_writer",
    "isolated_writer",
    "dangerous_full",
}
_FEISHU_PROJECT_GROUP_PURPOSES = {
    "run_manager",
    "kanban_agent",
    "default",
}
_FEISHU_PROJECT_GROUP_KINDS = {"collaboration"}
_KNOWN_FEISHU_PROJECT_GROUP_KEYS = frozenset({
    "enabled",
    "auto_provision",
    "binding_id",
    "group_kind",
    "name_template",
    "owner_open_id_env",
    "provisioner_purpose",
    "bot_purposes",
    "primary_responder",
    "channel_id",
})


def _build_feishu_project_group(data: object) -> FeishuProjectGroupConfig:
    """Validate project Feishu collaboration-group desired topology.

    Provisioning remains disabled by default.  Enabling it is an explicit
    control-plane opt-in; the resolved chat and verified members are stored in
    the project runtime binding sidecar, never in this config object.
    """
    if data in (None, ""):
        return FeishuProjectGroupConfig()
    if not isinstance(data, dict):
        raise ConfigError("integrations.feishu_project_group must be a mapping")
    _reject_unknown_keys(
        data,
        _KNOWN_FEISHU_PROJECT_GROUP_KEYS,
        "integrations.feishu_project_group",
    )
    enabled = _bool_value(data.get("enabled"), False)
    auto_provision = _bool_value(data.get("auto_provision"), False)
    if auto_provision and not enabled:
        raise ConfigError(
            "integrations.feishu_project_group.auto_provision requires enabled=true"
        )
    binding_id = str(data.get("binding_id") or "project-collaboration").strip()
    if not _CHANNEL_PROFILE_ID_RE.match(binding_id):
        raise ConfigError(
            "integrations.feishu_project_group.binding_id must start with a "
            "letter and contain only letters, digits, dot, underscore, or hyphen"
        )
    group_kind = str(data.get("group_kind") or "collaboration").strip()
    if group_kind not in _FEISHU_PROJECT_GROUP_KINDS:
        raise ConfigError(
            "integrations.feishu_project_group.group_kind must be one of "
            f"{sorted(_FEISHU_PROJECT_GROUP_KINDS)}"
        )
    name_template = str(
        data.get("name_template") or "ZaoFu - {project_name}"
    ).strip()
    if not name_template:
        raise ConfigError("integrations.feishu_project_group.name_template is required")
    try:
        fields = list(string.Formatter().parse(name_template))
        if any(
            field_name is not None
            and (field_name != "project_name" or format_spec or conversion)
            for _literal, field_name, format_spec, conversion in fields
        ):
            raise ValueError("unsupported template field")
        rendered_name = name_template.format(project_name="project")
    except (KeyError, ValueError) as exc:
        raise ConfigError(
            "integrations.feishu_project_group.name_template only supports "
            "the {project_name} placeholder"
        ) from exc
    if not rendered_name.strip() or len(rendered_name) > 60:
        raise ConfigError(
            "integrations.feishu_project_group.name_template must render a "
            "non-empty group name of at most 60 characters"
        )
    owner_open_id_env = str(
        data.get("owner_open_id_env") or "ZF_FEISHU_PROVISIONER_OWNER_OPEN_ID"
    ).strip()
    if not _ENV_NAME_RE.match(owner_open_id_env):
        raise ConfigError(
            "integrations.feishu_project_group.owner_open_id_env must be an "
            "uppercase environment variable name"
        )
    raw_purposes = data.get("bot_purposes")
    if raw_purposes in (None, ""):
        raw_purposes = ["kanban_agent", "run_manager"]
    if not isinstance(raw_purposes, list):
        raise ConfigError("integrations.feishu_project_group.bot_purposes must be a list")
    bot_purposes = [str(item).strip() for item in raw_purposes if str(item).strip()]
    if not bot_purposes or len(bot_purposes) != len(set(bot_purposes)):
        raise ConfigError(
            "integrations.feishu_project_group.bot_purposes must be a non-empty "
            "unique list"
        )
    invalid = sorted(set(bot_purposes) - _FEISHU_PROJECT_GROUP_PURPOSES)
    if invalid:
        raise ConfigError(
            "integrations.feishu_project_group.bot_purposes contains unsupported "
            f"purpose(s): {', '.join(invalid)}"
        )
    provisioner_purpose = str(
        data.get("provisioner_purpose") or "run_manager"
    ).strip()
    primary_responder = str(
        data.get("primary_responder") or "kanban_agent"
    ).strip()
    for field_name, value in (
        ("provisioner_purpose", provisioner_purpose),
        ("primary_responder", primary_responder),
    ):
        if value not in bot_purposes:
            raise ConfigError(
                f"integrations.feishu_project_group.{field_name} must be one of "
                "bot_purposes"
            )
    channel_id = str(data.get("channel_id") or "zaofu").strip()
    if not channel_id:
        raise ConfigError("integrations.feishu_project_group.channel_id is required")
    return FeishuProjectGroupConfig(
        enabled=enabled,
        auto_provision=auto_provision,
        binding_id=binding_id,
        group_kind=group_kind,
        name_template=name_template,
        owner_open_id_env=owner_open_id_env,
        provisioner_purpose=provisioner_purpose,
        bot_purposes=bot_purposes,
        primary_responder=primary_responder,
        channel_id=channel_id,
    )


def _build_feishu_routing(data: object) -> dict[str, FeishuRouteConfig]:
    if data in (None, ""):
        return {}
    if not isinstance(data, dict):
        raise ConfigError("integrations.feishu_routing must be a mapping")
    routes: dict[str, FeishuRouteConfig] = {}
    for chat_id, entry in data.items():
        if not isinstance(entry, dict):
            raise ConfigError(
                f"integrations.feishu_routing[{chat_id}] must be a mapping"
            )
        target = str(entry.get("target") or "channel")
        if target not in _FEISHU_ROUTE_TARGETS:
            raise ConfigError(
                f"integrations.feishu_routing[{chat_id}].target must be one of "
                f"{sorted(_FEISHU_ROUTE_TARGETS)}"
            )
        worker_session_id = str(entry.get("worker_session_id") or "")
        if target == "worker" and not worker_session_id:
            raise ConfigError(
                f"integrations.feishu_routing[{chat_id}] target=worker requires "
                "worker_session_id (bridge an existing worker, no new tmux)"
            )
        backend = str(entry.get("backend") or "")
        if target == "agent" and not backend:
            raise ConfigError(
                f"integrations.feishu_routing[{chat_id}] target=agent requires "
                "backend (claude-code | codex | ...)"
            )
        permission_profile = (
            str(entry.get("permission_profile") or "read_only")
            .strip()
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
        )
        if permission_profile not in _FEISHU_PERMISSION_PROFILES:
            raise ConfigError(
                f"integrations.feishu_routing[{chat_id}].permission_profile "
                f"must be one of {sorted(_FEISHU_PERMISSION_PROFILES)}"
            )
        dangerous_ack = _bool_value(entry.get("dangerous_ack"), False)
        allowed_senders_raw = entry.get("allowed_senders") or []
        if not isinstance(allowed_senders_raw, list):
            raise ConfigError(
                f"integrations.feishu_routing[{chat_id}].allowed_senders must be a list"
            )
        allowed_senders = [
            str(sender).strip()
            for sender in allowed_senders_raw
            if str(sender).strip()
        ]
        if permission_profile == "dangerous_full" and (
            not dangerous_ack or not allowed_senders
        ):
            raise ConfigError(
                f"integrations.feishu_routing[{chat_id}] dangerous_full requires "
                "dangerous_ack=true and a non-empty allowed_senders list"
            )
        routes[str(chat_id)] = FeishuRouteConfig(
            target=target,
            channel_id=str(entry.get("channel_id") or ""),
            default_member=str(entry.get("default_member") or ""),
            worker_session_id=worker_session_id,
            backend=backend,
            cwd=str(entry.get("cwd") or ""),
            permission_profile=permission_profile,
            dangerous_ack=dangerous_ack,
            allowed_senders=allowed_senders,
        )
    return routes


def _build_feishu_identity(data: object) -> FeishuIdentityConfig:
    if data in (None, ""):
        return FeishuIdentityConfig()
    if not isinstance(data, dict):
        raise ConfigError("integrations.feishu_identity must be a mapping")
    raw_users = data.get("users") or {}
    if not isinstance(raw_users, dict):
        raise ConfigError("integrations.feishu_identity.users must be a mapping")
    users: dict[str, FeishuIdentityUserConfig] = {}
    for principal, entry in raw_users.items():
        if not isinstance(entry, dict):
            raise ConfigError(
                f"integrations.feishu_identity.users[{principal}] must be a mapping"
            )
        users[str(principal)] = FeishuIdentityUserConfig(
            operator=str(entry.get("operator") or ""),
            level=str(entry.get("level") or "viewer"),
        )
    return FeishuIdentityConfig(
        enabled=bool(data.get("enabled", False)),
        verification_token_env=str(
            data.get("verification_token_env") or "ZF_FEISHU_VERIFICATION_TOKEN"
        ),
        replay_window_seconds=int(data.get("replay_window_seconds", 300) or 300),
        users=users,
        action_token_secret_env=str(
            data.get("action_token_secret_env") or "ZF_FEISHU_ACTION_TOKEN_SECRET"
        ),
        action_token_ttl_seconds=int(
            data.get("action_token_ttl_seconds", 86400) or 86400
        ),
        require_signed_actions=bool(data.get("require_signed_actions", False)),
    )


def _build_openclaw_feishu_bridge(data: object) -> OpenClawFeishuBridgeConfig:
    if data in (None, ""):
        return OpenClawFeishuBridgeConfig()
    if not isinstance(data, dict):
        raise ConfigError("integrations.openclaw_feishu_bridge must be a mapping")
    raw_bindings = data.get("bindings") or {}
    if not isinstance(raw_bindings, dict):
        raise ConfigError("integrations.openclaw_feishu_bridge.bindings must be a mapping")
    bindings: dict[str, OpenClawFeishuBridgeBindingConfig] = {}
    for binding_id, raw_binding in raw_bindings.items():
        binding_key = str(binding_id).strip()
        if not binding_key:
            raise ConfigError("integrations.openclaw_feishu_bridge binding id is required")
        if not re.match(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$", binding_key):
            raise ConfigError(
                f"integrations.openclaw_feishu_bridge binding id {binding_key!r} "
                "must start with a letter and contain only letters, digits, dot, "
                "underscore, or hyphen"
            )
        if not isinstance(raw_binding, dict):
            raise ConfigError(
                f"integrations.openclaw_feishu_bridge.bindings.{binding_key} "
                "must be a mapping"
            )
        bindings[binding_key] = _build_openclaw_feishu_bridge_binding(
            binding_key,
            raw_binding,
        )
    default_binding = str(data.get("default_binding") or "").strip()
    if default_binding and default_binding not in bindings:
        raise ConfigError(
            "integrations.openclaw_feishu_bridge.default_binding "
            f"{default_binding!r} does not reference a declared binding"
        )
    if not default_binding and len(bindings) == 1:
        default_binding = next(iter(bindings))
    return OpenClawFeishuBridgeConfig(
        enabled=_bool_value(data.get("enabled"), default=False),
        default_binding=default_binding,
        bindings=bindings,
    )


def _build_openclaw_feishu_bridge_binding(
    binding_id: str,
    data: dict[str, object],
) -> OpenClawFeishuBridgeBindingConfig:
    zaofu_raw = data.get("zaofu") or {}
    openclaw_raw = data.get("openclaw") or {}
    feishu_raw = data.get("feishu") or {}
    outbound_raw = data.get("outbound") or {}
    inbound_raw = data.get("inbound") or {}
    for field_name, value in (
        ("zaofu", zaofu_raw),
        ("openclaw", openclaw_raw),
        ("feishu", feishu_raw),
        ("outbound", outbound_raw),
        ("inbound", inbound_raw),
    ):
        if not isinstance(value, dict):
            raise ConfigError(
                f"integrations.openclaw_feishu_bridge.bindings.{binding_id}.{field_name} "
                "must be a mapping"
            )
    channel_id = str(zaofu_raw.get("channel_id") or "").strip()
    target = str(feishu_raw.get("target") or "").strip()
    chat_id = str(feishu_raw.get("chat_id") or "").strip()
    if not target and chat_id:
        target = f"chat:{chat_id}"
    return OpenClawFeishuBridgeBindingConfig(
        id=binding_id,
        zaofu=OpenClawFeishuBridgeZaofuConfig(
            channel_id=channel_id,
            thread_id=str(zaofu_raw.get("thread_id") or "main").strip() or "main",
        ),
        openclaw=OpenClawFeishuBridgeOpenClawConfig(
            provider_binding_id=str(
                openclaw_raw.get("provider_binding_id") or ""
            ).strip(),
            account_id=str(openclaw_raw.get("account_id") or "default").strip()
            or "default",
            agent_id=str(openclaw_raw.get("agent_id") or "zaofu-bridge").strip()
            or "zaofu-bridge",
        ),
        feishu=OpenClawFeishuBridgeFeishuConfig(chat_id=chat_id, target=target),
        mode=str(data.get("mode") or "interactive").strip() or "interactive",
        outbound=OpenClawFeishuBridgeOutboundConfig(
            enabled=_bool_value(outbound_raw.get("enabled"), default=True),
            include_event_types=_string_list(
                outbound_raw.get("include_event_types"),
                default=["channel.message.posted"],
            ),
            exclude_roles=_string_list(
                outbound_raw.get("exclude_roles"),
                default=["system"],
            ),
            reply_to_inbound_source=_bool_value(
                outbound_raw.get("reply_to_inbound_source"),
                default=True,
            ),
        ),
        inbound=OpenClawFeishuBridgeInboundConfig(
            enabled=_bool_value(inbound_raw.get("enabled"), default=False),
            require_prefix=str(inbound_raw.get("require_prefix") or "/zf").strip()
            or "/zf",
            require_mention=_bool_value(inbound_raw.get("require_mention"), default=True),
            accept_plain_text=_bool_value(
                inbound_raw.get("accept_plain_text"),
                default=False,
            ),
            allowed_chat_ids=_string_list(inbound_raw.get("allowed_chat_ids")),
            payload_dir=str(inbound_raw.get("payload_dir") or "").strip(),
            server_token_env=str(
                inbound_raw.get("server_token_env")
                or "ZF_OPENCLAW_FEISHU_INBOUND_TOKEN"
            ).strip()
            or "ZF_OPENCLAW_FEISHU_INBOUND_TOKEN",
        ),
    )


_KNOWN_GOAL_KEYS = frozenset({
    "enabled", "max_rescans", "idle_progress_ticks",
    "rework_fingerprint", "quiescent_after_escalate", "micro_loop",
})
_KNOWN_OBSERVABILITY_KEYS = frozenset({
    "provider_telemetry", "metrics", "runtime_logs", "otlp_exporter", "alerts",
})
_KNOWN_PROVIDER_TELEMETRY_KEYS = frozenset({
    "mode", "profile_id", "endpoint_env", "enable_traces",
})
_KNOWN_OPERATIONS_METRICS_KEYS = frozenset({"enabled", "access_token_env"})
_KNOWN_RUNTIME_LOGS_KEYS = frozenset({"enabled"})
_KNOWN_OTLP_EXPORTER_KEYS = frozenset({
    "enabled", "endpoint_env", "headers_env", "interval_seconds",
    "request_timeout_seconds", "batch_size", "retry_initial_seconds",
    "retry_max_seconds", "healthy_sample_rate",
})
_KNOWN_OBSERVABILITY_ALERT_KEYS = frozenset({"enabled", "cooldown_seconds"})


def _build_observability(data: object) -> ObservabilityConfig:
    if data in (None, ""):
        return ObservabilityConfig()
    if not isinstance(data, dict):
        raise ConfigError("observability must be a mapping")
    _reject_unknown_keys(data, _KNOWN_OBSERVABILITY_KEYS, "observability")
    telemetry_raw = data.get("provider_telemetry") or {}
    metrics_raw = data.get("metrics") or {}
    runtime_logs_raw = data.get("runtime_logs") or {}
    exporter_raw = data.get("otlp_exporter") or {}
    alerts_raw = data.get("alerts") or {}
    if not isinstance(telemetry_raw, dict):
        raise ConfigError("observability.provider_telemetry must be a mapping")
    if not isinstance(metrics_raw, dict):
        raise ConfigError("observability.metrics must be a mapping")
    if not isinstance(runtime_logs_raw, dict):
        raise ConfigError("observability.runtime_logs must be a mapping")
    if not isinstance(exporter_raw, dict):
        raise ConfigError("observability.otlp_exporter must be a mapping")
    if not isinstance(alerts_raw, dict):
        raise ConfigError("observability.alerts must be a mapping")
    _reject_unknown_keys(
        telemetry_raw,
        _KNOWN_PROVIDER_TELEMETRY_KEYS,
        "observability.provider_telemetry",
    )
    _reject_unknown_keys(
        metrics_raw,
        _KNOWN_OPERATIONS_METRICS_KEYS,
        "observability.metrics",
    )
    _reject_unknown_keys(
        runtime_logs_raw,
        _KNOWN_RUNTIME_LOGS_KEYS,
        "observability.runtime_logs",
    )
    _reject_unknown_keys(
        exporter_raw,
        _KNOWN_OTLP_EXPORTER_KEYS,
        "observability.otlp_exporter",
    )
    _reject_unknown_keys(
        alerts_raw,
        _KNOWN_OBSERVABILITY_ALERT_KEYS,
        "observability.alerts",
    )
    mode = str(telemetry_raw.get("mode", "off") or "off").strip().lower()
    if mode not in _VALID_PROVIDER_TELEMETRY_MODES:
        raise ConfigError(
            "observability.provider_telemetry.mode must be one of "
            f"{_VALID_PROVIDER_TELEMETRY_MODES}"
        )
    endpoint_env = str(telemetry_raw.get("endpoint_env") or "").strip()
    if endpoint_env and not _ENV_NAME_RE.fullmatch(endpoint_env):
        raise ConfigError(
            "observability.provider_telemetry.endpoint_env must be an "
            "environment variable name"
        )
    if mode == "managed" and not endpoint_env:
        raise ConfigError(
            "observability.provider_telemetry.endpoint_env is required when "
            "mode is managed"
        )
    metrics_enabled = _bool_value(metrics_raw.get("enabled"), default=False)
    access_token_env = str(metrics_raw.get("access_token_env") or "").strip()
    if access_token_env and not _ENV_NAME_RE.fullmatch(access_token_env):
        raise ConfigError(
            "observability.metrics.access_token_env must be an environment "
            "variable name"
        )
    if metrics_enabled and not access_token_env:
        raise ConfigError(
            "observability.metrics.access_token_env is required when metrics "
            "are enabled"
        )
    exporter_enabled = _bool_value(exporter_raw.get("enabled"), default=False)
    exporter_endpoint_env = str(exporter_raw.get("endpoint_env") or "").strip()
    exporter_headers_env = str(exporter_raw.get("headers_env") or "").strip()
    for key, value in (
        ("endpoint_env", exporter_endpoint_env),
        ("headers_env", exporter_headers_env),
    ):
        if value and not _ENV_NAME_RE.fullmatch(value):
            raise ConfigError(
                f"observability.otlp_exporter.{key} must be an environment "
                "variable name"
            )
    if exporter_enabled and not exporter_endpoint_env:
        raise ConfigError(
            "observability.otlp_exporter.endpoint_env is required when "
            "enabled"
        )
    try:
        exporter_interval = float(exporter_raw.get("interval_seconds", 15.0))
        exporter_timeout = float(
            exporter_raw.get("request_timeout_seconds", 3.0)
        )
        exporter_batch_size = int(exporter_raw.get("batch_size", 64))
        retry_initial = float(exporter_raw.get("retry_initial_seconds", 5.0))
        retry_max = float(exporter_raw.get("retry_max_seconds", 300.0))
        healthy_sample_rate = float(
            exporter_raw.get("healthy_sample_rate", 0.1)
        )
        alert_cooldown = float(alerts_raw.get("cooldown_seconds", 300.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError("observability exporter and alert values must be numeric") from exc
    if not 1.0 <= exporter_interval <= 3_600.0:
        raise ConfigError("observability.otlp_exporter.interval_seconds must be 1..3600")
    if not 0.1 <= exporter_timeout <= 60.0:
        raise ConfigError(
            "observability.otlp_exporter.request_timeout_seconds must be 0.1..60"
        )
    if not 1 <= exporter_batch_size <= 512:
        raise ConfigError("observability.otlp_exporter.batch_size must be 1..512")
    if not 1.0 <= retry_initial <= retry_max <= 3_600.0:
        raise ConfigError(
            "observability.otlp_exporter retry seconds must satisfy "
            "1 <= initial <= max <= 3600"
        )
    if not 0.0 <= healthy_sample_rate <= 1.0:
        raise ConfigError(
            "observability.otlp_exporter.healthy_sample_rate must be 0..1"
        )
    if not 30.0 <= alert_cooldown <= 86_400.0:
        raise ConfigError(
            "observability.alerts.cooldown_seconds must be 30..86400"
        )
    return ObservabilityConfig(
        provider_telemetry=ProviderTelemetryConfig(
            mode=mode,
            profile_id=str(
                telemetry_raw.get("profile_id") or "zaofu-managed-v1"
            ).strip(),
            endpoint_env=endpoint_env,
            enable_traces=_bool_value(
                telemetry_raw.get("enable_traces"), default=False
            ),
        ),
        metrics=OperationsMetricsConfig(
            enabled=metrics_enabled,
            access_token_env=access_token_env,
        ),
        runtime_logs=RuntimeLogsConfig(
            enabled=_bool_value(runtime_logs_raw.get("enabled"), default=True),
        ),
        otlp_exporter=OtlpExporterConfig(
            enabled=exporter_enabled,
            endpoint_env=exporter_endpoint_env,
            headers_env=exporter_headers_env,
            interval_seconds=exporter_interval,
            request_timeout_seconds=exporter_timeout,
            batch_size=exporter_batch_size,
            retry_initial_seconds=retry_initial,
            retry_max_seconds=retry_max,
            healthy_sample_rate=healthy_sample_rate,
        ),
        alerts=ObservabilityAlertConfig(
            enabled=_bool_value(alerts_raw.get("enabled"), default=False),
            cooldown_seconds=alert_cooldown,
        ),
    )


def _build_cost(data: object) -> CostConfig:
    if data is None:
        return CostConfig()
    if not isinstance(data, dict):
        raise ConfigError("cost must be a mapping")
    _reject_unknown_keys(data, _KNOWN_COST_KEYS, "cost")
    modes = data.get("backend_accounting_modes") or {}
    if not isinstance(modes, dict):
        raise ConfigError("cost.backend_accounting_modes must be a mapping")
    normalized_modes: dict[str, str] = {}
    valid_modes = {"api", "subscription", "enterprise", "unknown"}
    for backend, mode in modes.items():
        backend_name = str(backend or "").strip()
        mode_name = str(mode or "").strip().lower()
        if not backend_name or mode_name not in valid_modes:
            raise ConfigError(
                "cost.backend_accounting_modes values must be api, "
                "subscription, enterprise, or unknown"
            )
        normalized_modes[backend_name] = mode_name
    try:
        ttl = int(data.get("pricing_refresh_ttl_seconds", 86_400))
        timeout = float(data.get("pricing_refresh_timeout_seconds", 10.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError("cost pricing refresh values must be numeric") from exc
    if ttl < 60:
        raise ConfigError("cost.pricing_refresh_ttl_seconds must be >= 60")
    if timeout <= 0:
        raise ConfigError("cost.pricing_refresh_timeout_seconds must be > 0")
    return CostConfig(
        pricing_catalog_url=str(data.get("pricing_catalog_url") or "").strip(),
        pricing_refresh_ttl_seconds=ttl,
        pricing_refresh_timeout_seconds=timeout,
        backend_accounting_modes=normalized_modes,
    )


def _build_goal(data: dict | None) -> GoalConfig:
    if not data:
        return GoalConfig()
    if not isinstance(data, dict):
        raise ConfigError("goal must be a mapping")
    _reject_unknown_keys(data, _KNOWN_GOAL_KEYS, "goal")
    max_rescans = int(data.get("max_rescans", 5))
    idle_ticks = int(data.get("idle_progress_ticks", 3))
    if max_rescans < 0:
        raise ConfigError("goal.max_rescans must be >= 0")
    if idle_ticks < 1:
        raise ConfigError("goal.idle_progress_ticks must be >= 1")
    return GoalConfig(
        enabled=_bool_value(data.get("enabled"), default=False),
        max_rescans=max_rescans,
        idle_progress_ticks=idle_ticks,
        rework_fingerprint=_bool_value(
            data.get("rework_fingerprint"), default=False,
        ),
        quiescent_after_escalate=_bool_value(
            data.get("quiescent_after_escalate"), default=True,
        ),
        micro_loop=_bool_value(data.get("micro_loop"), default=False),
    )


def _build_autopilot(data: dict | None) -> AutopilotConfig:
    if not data:
        return AutopilotConfig()
    if not isinstance(data, dict):
        raise ConfigError("autopilot must be a mapping")
    mode = str(data.get("mode", "proposal_only") or "proposal_only")
    if mode not in _VALID_AUTOPILOT_MODES:
        raise ConfigError(
            f"Invalid autopilot.mode {mode!r}: must be one of {_VALID_AUTOPILOT_MODES}"
        )
    stale_after_hours = float(data.get("stale_after_hours", 24.0) or 24.0)
    failed_event_window_hours = float(
        data.get("failed_event_window_hours", 72.0) or 72.0
    )
    if stale_after_hours <= 0:
        raise ConfigError("autopilot.stale_after_hours must be > 0")
    if failed_event_window_hours <= 0:
        raise ConfigError("autopilot.failed_event_window_hours must be > 0")

    schedules_raw = data.get("schedules", []) or []
    if not isinstance(schedules_raw, list):
        raise ConfigError("autopilot.schedules must be a list")
    schedules: list[AutopilotScheduleConfig] = []
    seen: set[str] = set()
    for i, raw_schedule in enumerate(schedules_raw):
        if not isinstance(raw_schedule, dict):
            raise ConfigError(f"autopilot.schedules[{i}] must be a mapping")
        schedule_id = str(raw_schedule.get("id") or "").strip()
        interval = str(raw_schedule.get("interval") or "").strip()
        action = str(raw_schedule.get("action") or "triage").strip()
        if not schedule_id:
            raise ConfigError(f"autopilot.schedules[{i}].id is required")
        if schedule_id in seen:
            raise ConfigError(f"Duplicate autopilot schedule id {schedule_id!r}")
        seen.add(schedule_id)
        if not interval:
            raise ConfigError(f"autopilot.schedules[{i}].interval is required")
        if action not in _VALID_AUTOPILOT_ACTIONS:
            raise ConfigError(
                f"Invalid autopilot.schedules[{i}].action {action!r}: "
                f"must be one of {_VALID_AUTOPILOT_ACTIONS}"
            )
        schedules.append(AutopilotScheduleConfig(
            id=schedule_id,
            interval=interval,
            action=action,
        ))

    return AutopilotConfig(
        enabled=bool(data.get("enabled", False)),
        mode=mode,
        stale_after_hours=stale_after_hours,
        failed_event_window_hours=failed_event_window_hours,
        schedules=schedules,
    )


def _build_autoresearch(data: dict | None) -> AutoresearchConfig:
    if not data:
        return AutoresearchConfig()
    if not isinstance(data, dict):
        raise ConfigError("autoresearch must be a mapping")
    policy_raw = data.get("trigger_policy") or {}
    if not isinstance(policy_raw, dict):
        raise ConfigError("autoresearch.trigger_policy must be a mapping")

    mode = str(policy_raw.get("mode", "supervised") or "supervised")
    if mode not in _VALID_AUTORESEARCH_TRIGGER_MODES:
        raise ConfigError(
            "Invalid autoresearch.trigger_policy.mode "
            f"{mode!r}: must be one of {_VALID_AUTORESEARCH_TRIGGER_MODES}"
        )
    severity_min = str(policy_raw.get("severity_min", "high") or "high").lower()
    if severity_min not in _VALID_SEVERITIES:
        raise ConfigError(
            "Invalid autoresearch.trigger_policy.severity_min "
            f"{severity_min!r}: must be one of {_VALID_SEVERITIES}"
        )
    repair_mode = str(
        policy_raw.get("repair_mode", "proposal_only") or "proposal_only"
    )
    if repair_mode not in _VALID_AUTORESEARCH_REPAIR_MODES:
        raise ConfigError(
            "Invalid autoresearch.trigger_policy.repair_mode "
            f"{repair_mode!r}: must be one of {_VALID_AUTORESEARCH_REPAIR_MODES}"
        )
    self_repair_backend = str(policy_raw.get("self_repair_backend", "") or "").strip()
    if self_repair_backend and self_repair_backend not in _VALID_REPAIR_BACKENDS:
        raise ConfigError(
            "Invalid autoresearch.trigger_policy.self_repair_backend "
            f"{self_repair_backend!r}: must be one of {_VALID_REPAIR_BACKENDS}"
        )
    eligible_failure_classes = _string_list(
        policy_raw.get("eligible_failure_classes"),
        default=[],
    )
    try:
        cooldown_minutes = int(policy_raw.get("cooldown_minutes", 30))
        max_triggers_per_hour = int(policy_raw.get("max_triggers_per_hour", 2))
        max_daily_runs = int(policy_raw.get("max_daily_runs", 5))
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"Invalid autoresearch.trigger_policy numeric value: {exc}"
        ) from exc
    if cooldown_minutes < 0:
        raise ConfigError("autoresearch.trigger_policy.cooldown_minutes must be >= 0")
    if max_triggers_per_hour < 0:
        raise ConfigError(
            "autoresearch.trigger_policy.max_triggers_per_hour must be >= 0"
        )
    if max_daily_runs < 0:
        raise ConfigError("autoresearch.trigger_policy.max_daily_runs must be >= 0")

    return AutoresearchConfig(
        trigger_policy=AutoresearchTriggerPolicyConfig(
            enabled=_bool_value(policy_raw.get("enabled"), default=True),
            mode=mode,
            repair_mode=repair_mode,
            self_repair_backend=self_repair_backend,
            eligible_failure_classes=eligible_failure_classes,
            severity_min=severity_min,
            cooldown_minutes=cooldown_minutes,
            max_triggers_per_hour=max_triggers_per_hour,
            max_daily_runs=max_daily_runs,
        )
    )


def validate_config(path: Path) -> list[str]:
    """P0-VALIDATE-LOADER-01: route through the real loader.

    Pre-fix this function did a shallow check (project/name + role
    names) that accepted YAMLs which load_config() would reject —
    e.g. invalid tmux_layout, mismatched replicas/backends, or
    backend/backends conflicts. Users saw `zf validate` go green
    and then `zf start` blew up. We now invoke load_config() and
    convert construction errors into a one-element error list, so
    validate is never more permissive than runtime.
    """
    if not path.exists():
        return [f"Config file not found: {path}"]

    try:
        config = load_config(path)
    except ConfigError as e:
        return [str(e)]
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"]
    except (ValueError, TypeError) as e:
        # RoleConfig.__post_init__ raises ValueError (replicas >= 1,
        # recycle ratios, backends length); missing-key fall-throughs
        # in builders surface as TypeError. Both are user-facing
        # schema violations.
        return [f"Schema error: {e}"]
    if config.safety.tool_closure_enabled:
        from zf.core.config.tool_closure import validate_tool_closure

        return validate_tool_closure(config)
    return []
