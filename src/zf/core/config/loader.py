"""Config loader — parse zf.yaml into ZfConfig with validation."""

from __future__ import annotations

import glob
import hashlib
import os
import re
from pathlib import Path

import yaml


_ROLE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")
# 1231-T2: expanded to cover Codex's sandbox × approval spectrum.
# `default` → -a never -s workspace-write (codex only)
# `restricted` → -a untrusted -s read-only (codex only; equivalent to
#    legacy `allowlist` for codex — kept as distinct name for clarity)
# `allowlist` → legacy claude tool-allowlist, also maps to restricted
#    semantics when backend=codex.
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
_VALID_FANOUT_ASSIGNMENT_STRATEGIES = ("static_index", "affinity_stage_slots")
_VALID_AFFINITY_STAGE_SLOTS = ("impl", "review", "verify")
_VALID_AUTOPILOT_MODES = ("proposal_only",)
_VALID_AUTOPILOT_ACTIONS = ("triage",)
_VALID_OPENCLAW_BINDING_MODES = ("remote_gateway",)
_VALID_OPENCLAW_WORKSPACE_POLICIES = ("isolated",)
_VALID_OPENCLAW_TOOL_PROFILES = ("safe", "readonly", "reviewer", "coding")
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
_VALID_SEVERITIES = ("low", "medium", "high", "critical")
_ENV_SUB_RE = re.compile(
    r"\$\{(?P<name>[A-Z_][A-Z0-9_]*)(?::-(?P<default>[^}]*))?\}"
)

from zf.core.config.schema import (  # noqa: E402
    ZfConfig,
    ProjectConfig,
    SessionConfig,
    OrchestratorConfig,
    LoopConfig,
    ConstraintsConfig,
    ExecutionConfig,
    RoleConfig,
    RoleAutoscaleConfig,
    WakeExtensionConfig,
    WakeExtensionsConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    WorkflowWorkUnitsConfig,
    WorkflowSplitQualityConfig,
    WorkflowAdmissionReplanConfig,
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
    VerificationConfig,
    ContractDConfig,
    SemanticDConfig,
    ScopeVerificationConfig,
    RuntimeRuleDConfig,
    EventSchemaValidationConfig,
    RuntimeConfig,
    WorkdirConfig,
    GitIsolationConfig,
    RuntimeSkillsConfig,
    RuntimeRunManagerConfig,
    RuntimeRunManagerReflectConfig,
    RuntimeRunManagerResidentAgentConfig,
    RuntimeRunManagerSourceRepairConfig,
    RuntimeFeishuInboundConfig,
    ProvidersConfig,
    OpenClawProviderConfig,
    OpenClawRemoteBindingConfig,
    IntegrationsConfig,
    FeishuIdentityConfig,
    FeishuIdentityUserConfig,
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
    return env


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
})
_KNOWN_WORKFLOW_KEYS = frozenset({
    "attempt_lease_grace_s",  # 131-P2-3 lease 宽限(r6 首用)
    "harness_profile", "affinity_lanes", "stages", "rework_routing",
    "gan_rounds", "event_actions", "wake_extensions", "dag",
    "inline_overrides", "work_units", "completion_audit", "resume_packet",
    "integration", "strict_triggers", "fast_path", "replan_eval",
    "pipelines", "admission_replan", "plan_approval", "_flow_metadata",
})
_KNOWN_ROLE_KEYS = frozenset({
    "name", "backend", "backends", "role_kind", "model", "allowed_tools",
    "permission_mode", "transport", "stuck_threshold_seconds", "instance_id",
    "replicas", "context_window_tokens", "context_warning_threshold",
    "context_compact_threshold", "context_hard_cap", "recycle_threshold",
    "recycle_hard_cap", "max_rework_attempts", "orphan_warning_seconds",
    "orphan_escalate_seconds", "drain_hold_seconds",
    "spawn_ready_timeout_seconds", "budget_usd", "autoscale", "constraints",
    "execution", "stages", "triggers", "publishes", "guardrails", "plugins", "skills",
    "agent",
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


def _build_session(data: dict | None) -> SessionConfig:
    """Parse ``session:`` block. Defaults keep existing yamls unchanged.

    1206 Phase A: validate ``tmux_layout`` against the allowed set.
    """
    data = data or {}
    layout = data.get("tmux_layout", "window_per_role")
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
        topology = str(raw_stage.get("topology") or "")
        if not stage_id:
            raise ConfigError(f"workflow.stages[{i}].id is required")
        if not trigger:
            raise ConfigError(f"workflow.stages[{i}].trigger is required")
        if topology not in _VALID_STAR_TOPOLOGIES:
            raise ConfigError(
                f"workflow.stages[{i}].topology {topology!r} must be one of "
                f"{_VALID_STAR_TOPOLOGIES}"
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
            topology=topology,
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
    try:
        return WorkflowStageCriteriaConfig(
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
            f"Warning: role {name!r} has implicit permission_mode: bypass — "
            f"agent will run with --dangerously-skip-permissions (full "
            f"system access). Add `permission_mode: bypass` to acknowledge, "
            f"or switch to `permission_mode: allowlist` + "
            f"`allowed_tools: [...]` for least privilege.",
            file=sys.stderr,
        )
    transport = data.get("transport", "tmux")
    if transport not in _VALID_TRANSPORTS:
        raise ConfigError(
            f"Invalid transport {transport!r} for role {name!r}: "
            f"must be one of {_VALID_TRANSPORTS}"
        )
    execution_data = data.get("execution")
    execution = ExecutionConfig(command=execution_data.get("command", "")) if execution_data else ExecutionConfig()
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
            f"{', '.join(unsupported)} — fields will be ignored. "
            f"Use backend=claude-code if you need them.",
            file=sys.stderr,
        )

    return RoleConfig(
        name=name,
        backend=backend,
        role_kind=role_kind,
        backends=backends_list,
        model=data.get("model", ""),
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
                loaded = list(yaml.safe_load_all(text))
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
        documents = list(yaml.safe_load_all(text))
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
                print(
                    f"Warning: lane_pipeline "
                    f"{pipeline_spec.pipeline_id!r} and hand-written "
                    f"stages cover the same trigger "
                    f"{pipeline_spec.trigger!r} — dual representation "
                    f"drifts; remove the hand stages once P4 lands "
                    f"(doc 90 §7)",
                    file=sys.stderr,
                )
                continue
            stage_dicts.extend(
                materialize_lane_pipeline_stages(pipeline_spec),
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
            workspace=project_data.get("workspace", "."),
            state_dir=project_data.get("state_dir", ".zf"),
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
            event_actions=workflow_data.get("event_actions", []) or [],
            # 131-P2-3:lease 宽限可配置(F15 实证出厂 900s)。
            attempt_lease_grace_s=float(
                workflow_data.get("attempt_lease_grace_s", 900.0) or 900.0
            ),
            rework_routing=rework_routing,
            # R28 (doc 93 §1/§5): admission/W1 机械拒 → 自动回 synth。缺省关。
            admission_replan=_build_admission_replan(
                workflow_data.get("admission_replan")
            ),
            stages=workflow_stages,
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
        runtime=_build_runtime(raw.get("runtime")),
        providers=_build_providers(raw.get("providers")),
        integrations=_build_integrations(raw.get("integrations")),
        autopilot=_build_autopilot(raw.get("autopilot")),
        autoresearch=_build_autoresearch(raw.get("autoresearch")),
        skill_sources=_build_skill_sources(raw.get("skill_sources")),
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
    )
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
        for pipeline_spec in cfg.workflow.pipelines:
            profile_name = getattr(pipeline_spec, "schema_profile", "")
            if not profile_name:
                continue
            try:
                effective, sources, schema_diags = merge_event_schemas(
                    profile_name=profile_name,
                    spec_overrides=getattr(
                        pipeline_spec, "schema_overrides", {},
                    ),
                    local_schemas=cfg.workflow.dag.event_schemas,
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
            cfg.workflow.dag.event_schemas = effective
            cfg.workflow.pipelines_schema_sources = sources
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
    cfg.config_sources = list(raw.get("_config_profile_sources", []) or [])
    return cfg


def _build_security(data: dict | None) -> SecurityConfig:
    if not data:
        return SecurityConfig()
    es_data = data.get("event_signing") or {}
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
    tool_closure = data.get("tool_closure") or {}
    if not isinstance(tool_closure, dict):
        raise ConfigError("safety.tool_closure must be a mapping")
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
    )


def _build_runtime(data: dict | None) -> RuntimeConfig:
    if not data:
        return RuntimeConfig()
    if not isinstance(data, dict):
        raise ConfigError("runtime must be a mapping")
    workdirs_raw = data.get("workdirs") or {}
    git_raw = data.get("git") or {}
    skills_raw = data.get("skills") or {}
    run_manager_raw = data.get("run_manager") or {}
    feishu_inbound_raw = data.get("feishu_inbound") or {}
    if not isinstance(workdirs_raw, dict):
        raise ConfigError("runtime.workdirs must be a mapping")
    if not isinstance(git_raw, dict):
        raise ConfigError("runtime.git must be a mapping")
    if not isinstance(skills_raw, dict):
        raise ConfigError("runtime.skills must be a mapping")
    if not isinstance(run_manager_raw, dict):
        raise ConfigError("runtime.run_manager must be a mapping")
    if not isinstance(feishu_inbound_raw, dict):
        raise ConfigError("runtime.feishu_inbound must be a mapping")
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
    if source_repair_apply_policy not in {"proposal_only"}:
        raise ConfigError(
            "runtime.run_manager.source_repair.apply_policy currently only "
            "supports 'proposal_only'"
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


_FEISHU_YAML_KEYS = ("feishu_routing", "feishu_identity")


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
        fdata = yaml.safe_load(text) or {}
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
                f"integrations.{key} is configured in BOTH zf.yaml and feishu.yaml "
                "— keep it in exactly one place")
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
        feishu_routing=_build_feishu_routing(data.get("feishu_routing")),
    )


_FEISHU_ROUTE_TARGETS = {
    "channel",
    "kanban_agent",
    "run_manager",
    "worker",
    "agent",
}


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
        routes[str(chat_id)] = FeishuRouteConfig(
            target=target,
            channel_id=str(entry.get("channel_id") or ""),
            default_member=str(entry.get("default_member") or ""),
            worker_session_id=worker_session_id,
            backend=backend,
            cwd=str(entry.get("cwd") or ""),
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
