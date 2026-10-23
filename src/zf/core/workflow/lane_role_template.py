"""laneRoleTemplate — 由 lane_pipeline spec 生成 lane role(doc 90 §3.1)。

治 cj-min 510 行 role 克隆:用户写 1 个 template,compiler 在 load 期展开
为普通 RoleConfig(runtime 无感知)。核心纪律:

- **topology truth 由生成层锁定**:name/instance_id/role_kind/triggers/
  publishes 不许被手写同名 role 覆盖——否则"template 一份 truth、roles
  又一份 truth"。覆盖尝试 = ConfigError(load 期硬失败,validate 同拒)。
- **非拓扑字段白名单覆盖**:backend/model/skills/tools/权限/预算/context
  阈值等允许手写 role 精调。
- 与 `replicas` 池命名(name-1/name-2)不混用:同名冲突即 ConfigError。

纯函数、确定性:loader 与 inspect 共用同一展开,inspect 据 meta 展示
来源(generated / generated+override)。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


class LaneRoleTemplateError(ValueError):
    """生成/合并失败——loader 包装为 ConfigError。"""


# 手写同名 role 允许覆盖的非拓扑字段(doc 90 §3.1 白名单)。
OVERRIDABLE_ROLE_FIELDS = (
    "backend", "model", "model_reasoning_effort", "backends",
    "transport",
    "skills", "allowed_tools", "plugins",
    "permission_mode", "budget_usd",
    "provider_session", "lifecycle",
    "context_window_tokens", "context_warning_threshold",
    "context_compact_threshold", "context_hard_cap",
    "recycle_threshold", "recycle_hard_cap",
    "stuck_threshold_seconds", "spawn_ready_timeout_seconds",
    "max_rework_attempts", "orphan_warning_seconds",
    "orphan_escalate_seconds", "drain_hold_seconds",
    "constraints", "execution", "agent",
)
# 生成层锁定的 topology truth。
LOCKED_TOPOLOGY_FIELDS = ("role_kind", "flow_kind", "triggers", "publishes")

_KNOWN_TEMPLATE_KEYS = frozenset({
    "backend", "model", "model_reasoning_effort", "permission_mode",
    "transport",
    "context_window_tokens",
    "stuck_threshold_seconds",
    "spawn_ready_timeout_seconds", "budget_usd",
    "skills_by_stage", "allowed_tools", "plugins",
    "role_kind_by_stage", "backend_by_stage",
    "provider_session", "provider_session_by_stage",
    "lifecycle", "lifecycle_by_stage",
    "execution",
    # 真实 hermes 文件暴露的两个声明位(topology 仍归生成层,声明式扩展,
    # 不开手写 role 覆盖口):
    "publishes_extra_by_stage",  # e.g. impl 额外发 dev.blocked
    "role_stages_by_stage",      # role.stages 标签 ≠ pipeline stage_id 时
})


@dataclass(frozen=True)
class LaneRoleTemplateSpec:
    backend: str = "claude-code"
    transport: str = "tmux"
    model: str = ""
    model_reasoning_effort: str = ""
    context_window_tokens: int = 200_000
    permission_mode: str = "bypass"
    stuck_threshold_seconds: float = 300.0
    spawn_ready_timeout_seconds: float = 0.0
    budget_usd: float | None = None
    allowed_tools: tuple[str, ...] = ()
    plugins: tuple[str, ...] = ()
    skills_by_stage: dict[str, tuple[str, ...]] = field(default_factory=dict)
    role_kind_by_stage: dict[str, str] = field(default_factory=dict)
    backend_by_stage: dict[str, str] = field(default_factory=dict)
    provider_session: Any | None = None
    provider_session_by_stage: dict[str, Any] = field(default_factory=dict)
    lifecycle: Any | None = None
    lifecycle_by_stage: dict[str, Any] = field(default_factory=dict)
    execution: Any | None = None
    publishes_extra_by_stage: dict[str, tuple[str, ...]] = field(
        default_factory=dict,
    )
    role_stages_by_stage: dict[str, tuple[str, ...]] = field(
        default_factory=dict,
    )


def parse_lane_role_template(raw: Any, *, context: str) -> LaneRoleTemplateSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise LaneRoleTemplateError(
            f"{context}.lane_role_template must be a mapping"
        )
    unknown = sorted(str(k) for k in raw if str(k) not in _KNOWN_TEMPLATE_KEYS)
    if unknown:
        raise LaneRoleTemplateError(
            f"{context}.lane_role_template: unknown key(s) {unknown}"
        )
    skills_raw = raw.get("skills_by_stage") or {}
    if not isinstance(skills_raw, dict):
        raise LaneRoleTemplateError(
            f"{context}.lane_role_template.skills_by_stage must be a mapping"
        )
    kinds_raw = raw.get("role_kind_by_stage") or {}
    if not isinstance(kinds_raw, dict):
        raise LaneRoleTemplateError(
            f"{context}.lane_role_template.role_kind_by_stage must be a mapping"
        )
    backends_raw = raw.get("backend_by_stage") or {}
    if not isinstance(backends_raw, dict):
        raise LaneRoleTemplateError(
            f"{context}.lane_role_template.backend_by_stage must be a mapping"
        )
    provider_sessions_raw = raw.get("provider_session_by_stage") or {}
    if not isinstance(provider_sessions_raw, dict):
        raise LaneRoleTemplateError(
            f"{context}.lane_role_template.provider_session_by_stage "
            "must be a mapping"
        )
    lifecycles_raw = raw.get("lifecycle_by_stage") or {}
    if not isinstance(lifecycles_raw, dict):
        raise LaneRoleTemplateError(
            f"{context}.lane_role_template.lifecycle_by_stage must be a mapping"
        )
    transport = str(raw.get("transport") or "tmux")
    if transport not in {"tmux", "stream-json"}:
        raise LaneRoleTemplateError(
            f"{context}.lane_role_template.transport must be tmux or stream-json"
        )
    return LaneRoleTemplateSpec(
        backend=str(raw.get("backend") or "claude-code"),
        transport=transport,
        model=str(raw.get("model") or ""),
        model_reasoning_effort=str(
            raw.get("model_reasoning_effort") or ""
        ),
        context_window_tokens=int(
            raw.get("context_window_tokens") or 200_000
        ),
        permission_mode=str(raw.get("permission_mode") or "bypass"),
        stuck_threshold_seconds=float(raw.get("stuck_threshold_seconds") or 300.0),
        spawn_ready_timeout_seconds=float(
            raw.get("spawn_ready_timeout_seconds") or 0.0
        ),
        budget_usd=(
            float(raw["budget_usd"]) if raw.get("budget_usd") is not None else None
        ),
        allowed_tools=tuple(str(x) for x in raw.get("allowed_tools") or []),
        plugins=tuple(str(x) for x in raw.get("plugins") or []),
        skills_by_stage={
            str(stage): tuple(str(s) for s in skills or [])
            for stage, skills in skills_raw.items()
        },
        role_kind_by_stage={
            str(stage): str(kind) for stage, kind in kinds_raw.items()
        },
        backend_by_stage={
            str(stage): str(backend)
            for stage, backend in backends_raw.items()
            if str(backend).strip()
        },
        provider_session=_parse_provider_session(
            raw.get("provider_session"),
            context=f"{context}.lane_role_template.provider_session",
        ),
        provider_session_by_stage={
            str(stage): _parse_provider_session(
                value,
                context=(
                    f"{context}.lane_role_template."
                    f"provider_session_by_stage.{stage}"
                ),
            )
            for stage, value in provider_sessions_raw.items()
        },
        lifecycle=_parse_lifecycle(
            raw.get("lifecycle"),
            context=f"{context}.lane_role_template.lifecycle",
        ),
        lifecycle_by_stage={
            str(stage): _parse_lifecycle(
                value,
                context=f"{context}.lane_role_template.lifecycle_by_stage.{stage}",
            )
            for stage, value in lifecycles_raw.items()
        },
        execution=_parse_execution(
            raw.get("execution"),
            context=f"{context}.lane_role_template.execution",
        ),
        publishes_extra_by_stage={
            str(stage): tuple(str(e) for e in events or [])
            for stage, events in (raw.get("publishes_extra_by_stage") or {}).items()
        },
        role_stages_by_stage={
            str(stage): tuple(str(s) for s in labels or [])
            for stage, labels in (raw.get("role_stages_by_stage") or {}).items()
        },
    )


@dataclass(frozen=True)
class GeneratedRoleMeta:
    pipeline_id: str
    name: str
    stage_id: str
    lane: int
    source: str  # "generated" | "generated+override"
    overridden_fields: tuple[str, ...] = ()


def generate_lane_roles(
    spec: Any,  # LanePipelineSpec(lazy import 防环)
    handwritten_roles: list[Any],
) -> tuple[list[Any], list[GeneratedRoleMeta]]:
    """展开 template 为 RoleConfig 列表,并与手写 role 合并。

    返回 (最终 roles 列表[手写非冲突 + 生成/合并], meta)。
    手写同名 role 被合并进生成 role(白名单字段覆盖);其余手写 role
    原样保留。冲突(topology 覆盖 / replicas 池)抛 LaneRoleTemplateError。
    """
    from zf.core.config.schema import RoleConfig

    template: LaneRoleTemplateSpec | None = getattr(
        spec, "lane_role_template", None,
    )
    if template is None:
        return list(handwritten_roles), []

    by_name: dict[str, Any] = {}
    for role in handwritten_roles:
        by_name[str(getattr(role, "name", "") or "")] = role

    generated: list[Any] = []
    metas: list[GeneratedRoleMeta] = []
    consumed: set[str] = set()
    stage_ids = [s.stage_id for s in spec.stages]
    _reject_unknown_stage_overrides(template, stage_ids, spec.pipeline_id)
    for stage_idx, stage in enumerate(spec.stages):
        default_kind = "writer" if stage_idx == 0 else "reader"
        role_kind = template.role_kind_by_stage.get(stage.stage_id, default_kind)
        pattern = stage.role_pattern or f"{stage.stage_id}-lane-{{lane}}"
        publishes = [
            e for e in (stage.success_event, stage.failure_event) if e
        ]
        for extra in template.publishes_extra_by_stage.get(stage.stage_id, ()):
            if extra not in publishes:
                publishes.append(extra)
        role_stages = list(
            template.role_stages_by_stage.get(stage.stage_id, ())
        ) or [stage.stage_id]
        skills = list(template.skills_by_stage.get(stage.stage_id, ()))
        for lane in range(max(spec.lane_count, 0)):
            try:
                name = pattern.format(lane=lane)
            except (KeyError, IndexError, ValueError) as exc:
                raise LaneRoleTemplateError(
                    f"{spec.pipeline_id}.{stage.stage_id}: role_pattern "
                    f"{pattern!r} failed to expand: {exc}"
                )
            role_kwargs: dict[str, Any] = {
                "name": name,
                "instance_id": name,
                "backend": template.backend_by_stage.get(
                    stage.stage_id,
                    template.backend,
                ),
                "transport": template.transport,
                "model": template.model,
                "model_reasoning_effort": template.model_reasoning_effort,
                "context_window_tokens": template.context_window_tokens,
                "role_kind": role_kind,
                "flow_kind": str(getattr(spec, "flow_kind", "") or ""),
                "permission_mode": template.permission_mode,
                "stuck_threshold_seconds": template.stuck_threshold_seconds,
                "spawn_ready_timeout_seconds": (
                    template.spawn_ready_timeout_seconds
                ),
                "budget_usd": template.budget_usd,
                "allowed_tools": list(template.allowed_tools),
                "plugins": list(template.plugins),
                "skills": list(skills),
                "stages": list(role_stages),
                "publishes": list(publishes),
            }
            provider_session = template.provider_session_by_stage.get(
                stage.stage_id,
                template.provider_session,
            )
            lifecycle = template.lifecycle_by_stage.get(
                stage.stage_id,
                template.lifecycle,
            )
            if provider_session is not None:
                role_kwargs["provider_session"] = provider_session
            if lifecycle is not None:
                role_kwargs["lifecycle"] = lifecycle
            if template.execution is not None:
                role_kwargs["execution"] = template.execution
            base = RoleConfig(
                **role_kwargs,
            )
            manual = by_name.get(name)
            if manual is None:
                generated.append(base)
                metas.append(GeneratedRoleMeta(
                    pipeline_id=spec.pipeline_id,
                    name=name, stage_id=stage.stage_id, lane=lane,
                    source="generated",
                ))
                continue
            consumed.add(name)
            _reject_pool_conflict(manual, name, spec.pipeline_id)
            _reject_topology_override(manual, base, name, spec.pipeline_id)
            overridden: list[str] = []
            merged = base
            for field_name in OVERRIDABLE_ROLE_FIELDS:
                manual_value = getattr(manual, field_name, None)
                if _is_explicit(manual_value, field_name):
                    merged = replace(merged, **{field_name: manual_value})
                    overridden.append(field_name)
            _reject_provider_agent_conflict(merged, name, spec.pipeline_id)
            generated.append(merged)
            metas.append(GeneratedRoleMeta(
                pipeline_id=spec.pipeline_id,
                name=name, stage_id=stage.stage_id, lane=lane,
                source="generated+override",
                overridden_fields=tuple(sorted(overridden)),
            ))
    if spec.final_role and spec.final_role not in by_name and not any(
        getattr(r, "name", "") == spec.final_role for r in generated
    ):
        # final judge 不属 lane 模板;缺失由 compile_lane_pipeline 的
        # lane_pipeline_final_role_missing STOP 负责,不在此生成。
        pass
    passthrough = [
        role for role in handwritten_roles
        if str(getattr(role, "name", "") or "") not in consumed
    ]
    _ = stage_ids
    return passthrough + generated, metas


def _is_explicit(value: Any, field_name: str) -> bool:
    """手写字段是否构成显式覆盖(非默认空值)。

    保守判定:非空容器 / 非 None 标量 / 非空字符串才覆盖。permission_mode
    的 'bypass' 默认值无法与显式 bypass 区分——视为覆盖无害(同值)。
    """
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict)) and not value:
        return False
    if isinstance(value, str) and not value:
        return False
    if field_name == "stuck_threshold_seconds" and value == 300.0:
        return False
    if field_name == "spawn_ready_timeout_seconds" and value == 0.0:
        return False
    if field_name == "context_window_tokens" and value == 200_000:
        return False
    if field_name == "transport" and value == "tmux":
        return False
    if field_name == "max_rework_attempts" and value == 3:
        return False
    if field_name == "orphan_warning_seconds" and value == 900.0:
        return False
    if field_name == "orphan_escalate_seconds" and value == 1800.0:
        return False
    if field_name == "drain_hold_seconds" and value == 180.0:
        return False
    if field_name == "lifecycle":
        from zf.core.config.schema import RoleLifecycleConfig

        return value != RoleLifecycleConfig()
    if field_name == "execution":
        from zf.core.config.schema import ExecutionConfig

        return value != ExecutionConfig()
    return True


def _parse_provider_session(raw: Any, *, context: str) -> Any | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise LaneRoleTemplateError(f"{context} must be a mapping")
    allowed = {"effort", "agent", "max_parallel_agents", "native_delegation"}
    unknown = sorted(str(key) for key in raw if str(key) not in allowed)
    if unknown:
        raise LaneRoleTemplateError(f"{context}: unknown key(s) {unknown}")
    if not raw:
        return None
    effort = raw.get("effort", "")
    agent = raw.get("agent", "")
    parallel = raw.get("max_parallel_agents")
    if not isinstance(effort, str):
        raise LaneRoleTemplateError(f"{context}.effort must be a string")
    if not isinstance(agent, str):
        raise LaneRoleTemplateError(f"{context}.agent must be a string")
    if parallel is not None and (
        isinstance(parallel, bool) or not isinstance(parallel, int)
    ):
        raise LaneRoleTemplateError(
            f"{context}.max_parallel_agents must be an integer"
        )
    from zf.core.config.schema import ProviderSessionConfig

    try:
        return ProviderSessionConfig(
            effort=effort.strip(),
            agent=agent.strip(),
            max_parallel_agents=parallel,
            native_delegation=raw.get("native_delegation"),
        )
    except ValueError as exc:
        raise LaneRoleTemplateError(f"{context}: {exc}") from exc


def _parse_lifecycle(raw: Any, *, context: str) -> Any | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise LaneRoleTemplateError(f"{context} must be a mapping")
    allowed = {
        "mode",
        "idle_seconds",
        "cooldown_seconds",
        "preserve_session",
        "preserve_workdir",
        "reader_context_scope",
    }
    unknown = sorted(str(key) for key in raw if str(key) not in allowed)
    if unknown:
        raise LaneRoleTemplateError(f"{context}: unknown key(s) {unknown}")
    from zf.core.config.schema import RoleLifecycleConfig

    try:
        return RoleLifecycleConfig(
            reader_context_scope=raw.get("reader_context_scope", "workflow"),
            mode=str(raw.get("mode") or "eager"),
            idle_seconds=float(raw.get("idle_seconds", 900.0)),
            cooldown_seconds=float(raw.get("cooldown_seconds", 180.0)),
            preserve_session=_strict_bool(
                raw.get("preserve_session", True),
                context=f"{context}.preserve_session",
            ),
            preserve_workdir=_strict_bool(
                raw.get("preserve_workdir", True),
                context=f"{context}.preserve_workdir",
            ),
        )
    except (TypeError, ValueError) as exc:
        raise LaneRoleTemplateError(f"{context}: {exc}") from exc


def _parse_execution(raw: Any, *, context: str) -> Any | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise LaneRoleTemplateError(f"{context} must be a mapping")
    allowed = {"command", "default_profile", "profile_allowlist"}
    unknown = sorted(str(key) for key in raw if str(key) not in allowed)
    if unknown:
        raise LaneRoleTemplateError(f"{context}: unknown key(s) {unknown}")
    allowlist = raw.get("profile_allowlist", ["direct-v1"])
    if not isinstance(allowlist, list) or not all(
        isinstance(item, str) and item.strip() for item in allowlist
    ):
        raise LaneRoleTemplateError(
            f"{context}.profile_allowlist must be a list of non-empty strings"
        )
    from zf.core.config.schema import ExecutionConfig

    return ExecutionConfig(
        command=str(raw.get("command") or ""),
        default_profile=str(raw.get("default_profile") or "direct-v1"),
        profile_allowlist=[item.strip() for item in allowlist],
    )


def _strict_bool(value: Any, *, context: str) -> bool:
    if not isinstance(value, bool):
        raise LaneRoleTemplateError(f"{context} must be a boolean")
    return value


def _reject_unknown_stage_overrides(
    template: LaneRoleTemplateSpec,
    stage_ids: list[str],
    pipeline_id: str,
) -> None:
    known = set(stage_ids)
    for field_name, values in (
        ("provider_session_by_stage", template.provider_session_by_stage),
        ("lifecycle_by_stage", template.lifecycle_by_stage),
    ):
        unknown = sorted(set(values) - known)
        if unknown:
            raise LaneRoleTemplateError(
                f"{pipeline_id}.lane_role_template.{field_name}: "
                f"unknown stage(s) {unknown}"
            )


def _reject_provider_agent_conflict(
    role: Any,
    name: str,
    pipeline_id: str,
) -> None:
    provider_session = getattr(role, "provider_session", None)
    provider_agent = str(getattr(provider_session, "agent", "") or "")
    role_agent = str(getattr(role, "agent", "") or "")
    if provider_agent and role_agent and provider_agent != role_agent:
        raise LaneRoleTemplateError(
            f"{pipeline_id}: role {name!r} has conflicting role.agent and "
            "provider_session.agent"
        )


def _reject_pool_conflict(manual: Any, name: str, pipeline_id: str) -> None:
    replicas = int(getattr(manual, "replicas", 1) or 1)
    autoscale = getattr(manual, "autoscale", None)
    if replicas > 1 or bool(getattr(autoscale, "enabled", False)):
        raise LaneRoleTemplateError(
            f"{pipeline_id}: role {name!r} uses replicas/autoscale pool "
            "naming; lane template roles are lane-bound and cannot use pools"
        )


def _reject_topology_override(
    manual: Any, base: Any, name: str, pipeline_id: str,
) -> None:
    locked: list[str] = []
    manual_kind = str(getattr(manual, "role_kind", "") or "")
    if manual_kind not in ("", "auto") and manual_kind != base.role_kind:
        locked.append("role_kind")
    if list(getattr(manual, "triggers", []) or []):
        locked.append("triggers")
    manual_pub = list(getattr(manual, "publishes", []) or [])
    if manual_pub and manual_pub != list(base.publishes):
        locked.append("publishes")
    manual_instance = str(getattr(manual, "instance_id", "") or "")
    if manual_instance and manual_instance != name:
        locked.append("instance_id")
    manual_stages = list(getattr(manual, "stages", []) or [])
    if manual_stages and manual_stages != list(base.stages):
        locked.append("stages")
    if locked:
        raise LaneRoleTemplateError(
            f"{pipeline_id}: role {name!r} attempts to override locked "
            f"topology field(s) {locked}; topology truth is owned by the "
            "lane template. Overridable fields: "
            f"{', '.join(OVERRIDABLE_ROLE_FIELDS[:6])}..."
        )
