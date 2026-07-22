"""kind envelope — K8s/Argo 风格外部语法的前置层(doc 90 §3.4/§3.4.1)。

A5(本切片):camelCase 外部字段 → canonical snake_case 的确定性归一化。
**canonical loader 只认 snake_case**;归一化后仍走既有
`_reject_unknown_keys` / `LanePipelineSpecError` fail-closed 纪律——
禁止为支持新语法而放宽。未知 camelCase 键在本层即 ConfigError 语义
(抛 KindEnvelopeError,loader 包装),不允许静默丢弃。

B1(后续切片)在此模块补 `---` 多文档 kind 路由(LanePipeline/ZfConfig/
SchemaProfile → 单一 ZfConfig)。
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


class KindEnvelopeError(ValueError):
    """envelope 层失败——调用方包装为 ConfigError。"""


def _has_upper(key: str) -> bool:
    return any(c.isupper() for c in key)


def _normalize_mapping(
    raw: dict,
    *,
    rename: dict[str, str],
    nested: dict[str, Any] | None = None,
    context: str,
) -> dict:
    """按显式映射表归一化一层:已知 camelCase 改名;snake 直通;
    含大写但不在映射表 = 未知外部键 → fail-closed。"""
    out: dict[str, Any] = {}
    nested = nested or {}
    for key, value in raw.items():
        k = str(key)
        target = rename.get(k, k)
        if _has_upper(k) and k not in rename:
            raise KindEnvelopeError(
                f"{context}: unknown camelCase key {k!r} — the envelope "
                f"surface only accepts mapped fields (doc 90 §3.4.1); "
                f"canonical snake_case is the authority"
            )
        handler = nested.get(k) or nested.get(target)
        out[target] = handler(value) if callable(handler) else value
    return out


def _norm_task_source(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return _normalize_mapping(
        value,
        rename={"taskMapRef": "task_map_ref"},
        context="spec.taskSource",
    )


def _norm_template(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return _normalize_mapping(
        value,
        rename={
            "stuckThresholdSeconds": "stuck_threshold_seconds",
            "spawnReadyTimeoutSeconds": "spawn_ready_timeout_seconds",
            "permissionMode": "permission_mode",
            "skillsByStage": "skills_by_stage",
            "roleKindByStage": "role_kind_by_stage",
            "allowedTools": "allowed_tools",
            "budgetUsd": "budget_usd",
            "contextWarningThreshold": "context_warning_threshold",
            "contextCompactThreshold": "context_compact_threshold",
            "contextHardCap": "context_hard_cap",
            "drainHoldSeconds": "drain_hold_seconds",
        },
        context="spec.laneRoleTemplate",
    )


def _norm_stage(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    out = _normalize_mapping(
        value,
        rename={
            "rolePattern": "role_pattern",
            "deadlineSeconds": "deadline_seconds",
            "onFailure": "on_failure",
            "rework": "on_failure",  # operator 面别名(doc 90 §3.4.1)
        },
        context="spec.stages[]",
    )
    on_failure = out.get("on_failure")
    if isinstance(on_failure, dict):
        out["on_failure"] = _normalize_mapping(
            on_failure,
            rename={
                "to": "rework_to",
                "reworkTo": "rework_to",
                "feedbackArtifact": "feedback_artifact",
            },
            context="spec.stages[].rework",
        )
    return out


def _norm_barriers(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return _normalize_mapping(
        value,
        rename={
            "stageTransition": "stage_transition",
        },
        context="spec.barriers",
    )


def normalize_lane_pipeline_external(raw: dict) -> dict:
    """LanePipeline 的 envelope 外部 spec → canonical pipeline dict。

    输出可直接进 ``parse_lane_pipeline``(canonical 纪律不放宽)。
    ``reworkDefaults`` 被提升为顶层 max_rework_attempts / trace_budget。
    """
    if not isinstance(raw, dict):
        raise KindEnvelopeError("LanePipeline spec must be a mapping")
    out = _normalize_mapping(
        raw,
        rename={
            "taskSource": "task_source",
            "affinityKey": "affinity_key",
            "lanes": "lane_count",
            "laneCount": "lane_count",
            "reworkDefaults": "_rework_defaults",
            "requireArtifactDigests": "require_artifact_digests",
            "laneRoleTemplate": "lane_role_template",
            "schemaProfile": "schema_profile",
            "schemaOverrides": "schema_overrides",
            "instructionRefs": "instruction_refs",
            "maxReworkAttempts": "max_rework_attempts",
            "traceBudget": "trace_budget",
        },
        nested={
            "taskSource": _norm_task_source,
            "task_source": _norm_task_source,
            "laneRoleTemplate": _norm_template,
            "lane_role_template": _norm_template,
            "stages": lambda v: (
                [_norm_stage(s) for s in v] if isinstance(v, list) else v
            ),
            "barriers": _norm_barriers,
        },
        context="LanePipeline.spec",
    )
    defaults = out.pop("_rework_defaults", None)
    if isinstance(defaults, dict):
        norm = _normalize_mapping(
            defaults,
            rename={
                "maxAttempts": "max_rework_attempts",
                "traceBudget": "trace_budget",
            },
            context="spec.reworkDefaults",
        )
        out.setdefault("max_rework_attempts", norm.get("max_rework_attempts"))
        out.setdefault("trace_budget", norm.get("trace_budget"))
        out = {k: v for k, v in out.items() if v is not None}
    return out


# ------------------------------------------------------------------ B1


_API_VERSION = "zaofu.dev/v1"
_ENVELOPE_KEYS = frozenset({"apiVersion", "kind", "metadata", "spec"})
_KNOWN_KINDS = frozenset({
    "ZfConfig", "LanePipeline", "SchemaProfile", "Workflow",
    "RefactorFlow", "IssueFlow", "PrdFlow", "ConfigProfile", "RoleSet",
})


def assemble_envelope_stream(
    documents: list[Any],
    *,
    profile_source_files: list[dict[str, str]] | None = None,
) -> tuple[dict, dict[str, dict]]:
    """`---` 多文档 kind 流 → (canonical zf.yaml raw dict, extra_profiles)。

    - 单文档且无 kind 键 = 隐式 ZfConfig(legacy,原样返回——零迁移);
    - kind: ZfConfig 恰好一个,spec 即 zf.yaml 主体;
    - kind: LanePipeline → 归一化后 append 进 workflow.pipelines;
      metadata.name 缺省为 pipeline id;
    - kind: SchemaProfile → 注册为项目本地 profile(metadata.name 为键,
      spec.events 为 22 事件式 required 集),供 schema_profile 引用;
    - 未知 kind / apiVersion / envelope 顶层键 → fail-closed。
    """
    docs = [d for d in documents if d is not None]
    if not docs:
        # legacy 语义保持:空文件 → 空 body,由 canonical loader 以
        # "Missing required section: project" 报错(零迁移)。
        return {}, {}
    if len(docs) == 1 and "kind" not in docs[0]:
        if not isinstance(docs[0], dict):
            return docs[0], {}
        return _apply_config_uses(dict(docs[0]), {}, {}), {}

    body: dict | None = None
    pipelines: list[dict] = []
    profiles: dict[str, dict] = {}
    config_profiles: dict[str, dict] = {}
    role_sets: dict[str, dict] = {}
    kind_stages: list[dict] = []
    flow_documents: list[tuple[str, dict]] = []
    for i, doc in enumerate(docs):
        if not isinstance(doc, dict):
            raise KindEnvelopeError(f"document[{i}] must be a mapping")
        if "kind" not in doc:
            raise KindEnvelopeError(
                f"document[{i}]: multi-document streams require an "
                f"envelope (apiVersion/kind/spec) on every document"
            )
        unknown = sorted(str(k) for k in doc if str(k) not in _ENVELOPE_KEYS)
        if unknown:
            raise KindEnvelopeError(
                f"document[{i}]: unknown envelope key(s) {unknown}"
            )
        api = str(doc.get("apiVersion") or "")
        if api != _API_VERSION:
            raise KindEnvelopeError(
                f"document[{i}]: unsupported apiVersion {api!r} "
                f"(expected {_API_VERSION!r})"
            )
        kind = str(doc.get("kind") or "")
        if kind not in _KNOWN_KINDS:
            raise KindEnvelopeError(
                f"document[{i}]: unknown kind {kind!r}; known kinds: "
                f"{sorted(_KNOWN_KINDS)}"
            )
        metadata = doc.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise KindEnvelopeError(f"document[{i}]: metadata must be a mapping")
        spec = doc.get("spec")
        if not isinstance(spec, dict):
            raise KindEnvelopeError(f"document[{i}]: spec must be a mapping")
        if kind == "ZfConfig":
            if body is not None:
                raise KindEnvelopeError(
                    "exactly one kind: ZfConfig document is allowed"
                )
            body = dict(spec)
        elif kind == "LanePipeline":
            canonical = normalize_lane_pipeline_external(dict(spec))
            canonical.setdefault("kind", "lane_pipeline")
            canonical.setdefault(
                "id", str(metadata.get("name") or "").strip(),
            )
            pipelines.append(canonical)
        elif kind == "Workflow":
            from zf.core.workflow.workflow_kind import (
                WorkflowKindError,
                translate_workflow_kind,
            )
            name = str(metadata.get("name") or f"workflow[{i}]")
            try:
                kind_stages.extend(
                    translate_workflow_kind(dict(spec), context=name),
                )
            except WorkflowKindError as exc:
                raise KindEnvelopeError(str(exc))
        elif kind == "RefactorFlow":
            flow_documents.append((kind, dict(spec)))
        elif kind == "IssueFlow":
            flow_documents.append((kind, dict(spec)))
        elif kind == "PrdFlow":
            flow_documents.append((kind, dict(spec)))
        elif kind == "SchemaProfile":
            name = str(metadata.get("name") or "").strip()
            if not name:
                raise KindEnvelopeError(
                    f"document[{i}]: SchemaProfile requires metadata.name"
                )
            events = spec.get("events")
            if not isinstance(events, dict):
                raise KindEnvelopeError(
                    f"document[{i}]: SchemaProfile spec.events must be a "
                    f"mapping of event -> {{required: [...]}}"
                )
            normalized_events: dict[str, dict[str, Any]] = {}
            for event_type, rule in events.items():
                if not isinstance(rule, dict):
                    raise KindEnvelopeError(
                        f"document[{i}]: SchemaProfile event "
                        f"{event_type!r} must be a mapping"
                    )
                normalized_events[str(event_type)] = deepcopy(rule)
            profiles[name] = {
                "extends": str(spec.get("extends") or "").strip(),
                "events": normalized_events,
            }
        elif kind == "ConfigProfile":
            name = str(metadata.get("name") or "").strip()
            if not name:
                raise KindEnvelopeError(
                    f"document[{i}]: ConfigProfile requires metadata.name"
                )
            config_profiles[name] = dict(spec)
        elif kind == "RoleSet":
            name = str(metadata.get("name") or "").strip()
            if not name:
                raise KindEnvelopeError(
                    f"document[{i}]: RoleSet requires metadata.name"
                )
            role_sets[name] = dict(spec)
    if body is None:
        raise KindEnvelopeError(
            "a kind: ZfConfig document is required (project body)"
        )
    body = _apply_config_uses(
        body,
        config_profiles,
        role_sets,
        profile_source_files=profile_source_files,
    )
    flow_defaults = body.pop("flow_defaults", {})
    flow_expansions: list[dict] = []
    for kind, spec in flow_documents:
        from zf.core.config.workflow_profiles import (
            WorkflowProfileError,
            expand_issue_flow,
            expand_prd_flow,
            expand_workflow_profile,
        )
        flow_spec = _apply_flow_defaults(kind, spec, flow_defaults)
        try:
            if kind == "RefactorFlow":
                flow_expansions.append(expand_workflow_profile(flow_spec))
            elif kind == "IssueFlow":
                flow_expansions.append(expand_issue_flow(flow_spec))
            elif kind == "PrdFlow":
                flow_expansions.append(expand_prd_flow(flow_spec))
        except WorkflowProfileError as exc:
            raise KindEnvelopeError(str(exc))
    if flow_expansions:
        from zf.core.config.workflow_profiles import merge_expansion_into_body
        if len(flow_expansions) > 1:
            flow_expansions = [
                _scope_multi_kind_flow_expansion(expansion)
                for expansion in flow_expansions
            ]
        for expansion in flow_expansions:
            merge_expansion_into_body(body, expansion)
    if pipelines or kind_stages:
        workflow = body.setdefault("workflow", {})
        if not isinstance(workflow, dict):
            raise KindEnvelopeError("ZfConfig spec.workflow must be a mapping")
        if pipelines:
            existing = workflow.setdefault("pipelines", [])
            if not isinstance(existing, list):
                raise KindEnvelopeError(
                    "ZfConfig spec.workflow.pipelines must be a list"
                )
            existing.extend(pipelines)
        if kind_stages:
            existing_stages = workflow.setdefault("stages", [])
            if not isinstance(existing_stages, list):
                raise KindEnvelopeError(
                    "ZfConfig spec.workflow.stages must be a list"
                )
            existing_stages.extend(kind_stages)
    return body, profiles


def _scope_multi_kind_flow_expansion(expansion: dict) -> dict:
    """Namespace one Flow expansion inside a multi-kind canonical config.

    Event names remain canonical so the existing kernel contracts stay shared;
    stages carry ``flow_kind`` and runtime dispatch applies the kind guard.
    Roles, pipeline IDs, stage IDs and lane role patterns are namespaced here so
    a single tmux/runtime can materialize all kinds without role collisions.
    """

    out = deepcopy(expansion)
    metadata = out.get("metadata") if isinstance(out.get("metadata"), dict) else {}
    kind = str(metadata.get("flow_kind") or "").strip().lower()
    if kind not in {"issue", "prd", "refactor"}:
        raise KindEnvelopeError(
            "multi-kind Flow expansion requires metadata.flow_kind "
            "(issue|prd|refactor)"
        )

    def scoped(value: object) -> str:
        name = str(value or "").strip()
        if not name:
            return ""
        tokens = {token for token in name.replace("_", "-").split("-") if token}
        return name if kind in tokens else f"{kind}-{name}"

    role_map: dict[str, str] = {}
    for role in out.get("roles", []) or []:
        if not isinstance(role, dict):
            continue
        old_name = str(role.get("name") or "").strip()
        new_name = scoped(old_name)
        if old_name:
            role_map[old_name] = new_name
        role["name"] = new_name
        if str(role.get("instance_id") or "").strip() in {"", old_name}:
            role["instance_id"] = new_name

    first_stage_id = ""
    for stage in out.get("stages", []) or []:
        if not isinstance(stage, dict):
            continue
        stage["id"] = scoped(stage.get("id"))
        stage["flow_kind"] = kind
        if not first_stage_id:
            first_stage_id = str(stage.get("id") or "")
        stage["roles"] = [
            role_map.get(str(role), scoped(role))
            for role in stage.get("roles", []) or []
        ]
        fanout = stage.get("fanout")
        if isinstance(fanout, dict):
            assignment = fanout.get("assignment")
            if isinstance(assignment, dict):
                assignment["role_pool"] = [
                    role_map.get(str(role), scoped(role))
                    for role in assignment.get("role_pool", []) or []
                ]
            for child in fanout.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                for key in ("role", "role_instance"):
                    if str(child.get(key) or "").strip():
                        child[key] = role_map.get(str(child[key]), scoped(child[key]))
        aggregate = stage.get("aggregate")
        if isinstance(aggregate, dict) and str(aggregate.get("synth_role") or ""):
            aggregate["synth_role"] = role_map.get(
                str(aggregate["synth_role"]), scoped(aggregate["synth_role"]),
            )

    for pipeline in out.get("pipelines", []) or []:
        if not isinstance(pipeline, dict):
            continue
        pipeline["id"] = scoped(pipeline.get("id"))
        pipeline["flow_kind"] = kind
        for stage in pipeline.get("stages", []) or []:
            if not isinstance(stage, dict):
                continue
            pattern = str(stage.get("role_pattern") or "").strip()
            if pattern:
                stage["role_pattern"] = scoped(pattern)
        final = pipeline.get("final")
        if isinstance(final, dict) and str(final.get("role") or "").strip():
            final["role"] = role_map.get(str(final["role"]), scoped(final["role"]))

    out["flow_kind"] = kind
    out["entry_stage_id"] = first_stage_id
    out["multi_kind"] = True
    return out


def _apply_flow_defaults(
    kind: str,
    spec: dict,
    flow_defaults: object,
) -> dict:
    if flow_defaults in (None, ""):
        return dict(spec)
    if not isinstance(flow_defaults, dict):
        raise KindEnvelopeError("flow_defaults must be a mapping")
    kind_key = {
        "IssueFlow": "issue",
        "PrdFlow": "prd",
        "RefactorFlow": "refactor",
    }.get(kind, kind)
    raw_defaults = flow_defaults.get(kind_key) or flow_defaults.get(kind)
    if raw_defaults in (None, ""):
        return dict(spec)
    if not isinstance(raw_defaults, dict):
        raise KindEnvelopeError(f"flow_defaults.{kind_key} must be a mapping")
    unknown = sorted(
        str(key)
        for key in raw_defaults
        if str(key) not in {
            "roleSkillBundles", "role_skill_bundles",
            "roleDefaults", "role_defaults",
        }
    )
    if unknown:
        raise KindEnvelopeError(
            f"flow_defaults.{kind_key}: unknown key(s) {unknown}; "
            "only roleSkillBundles and roleDefaults are supported"
        )
    out = dict(spec)
    default_bundles = (
        raw_defaults.get("roleSkillBundles")
        or raw_defaults.get("role_skill_bundles")
        or {}
    )
    if default_bundles:
        out["roleSkillBundles"] = _merge_role_skill_bundle_defaults(
            default_bundles,
            out.get("roleSkillBundles") or out.get("role_skill_bundles") or {},
            context=f"flow_defaults.{kind_key}.roleSkillBundles",
        )
        out.pop("role_skill_bundles", None)
    default_role_values = (
        raw_defaults.get("roleDefaults")
        or raw_defaults.get("role_defaults")
        or {}
    )
    explicit_role_values = (
        out.get("roleDefaults")
        or out.get("role_defaults")
        or {}
    )
    if default_role_values or explicit_role_values:
        if not isinstance(default_role_values, dict):
            raise KindEnvelopeError(
                f"flow_defaults.{kind_key}.roleDefaults must be a mapping"
            )
        if not isinstance(explicit_role_values, dict):
            raise KindEnvelopeError("Flow spec roleDefaults must be a mapping")
        merged_role_values = _norm_template(default_role_values)
        merged_role_values.update(_norm_template(explicit_role_values))
        out["roleDefaults"] = merged_role_values
        out.pop("role_defaults", None)
    return out


def _merge_role_skill_bundle_defaults(
    defaults: object,
    explicit: object,
    *,
    context: str,
) -> dict[str, list[str]]:
    if not isinstance(defaults, dict):
        raise KindEnvelopeError(f"{context} must be a mapping")
    if not isinstance(explicit, dict):
        raise KindEnvelopeError("Flow spec roleSkillBundles must be a mapping")
    out = {
        str(key): _string_list(value, context=f"{context}.{key}")
        for key, value in defaults.items()
    }
    for key, value in explicit.items():
        bundle_name = str(key)
        explicit_values = _string_list(
            value,
            context=f"Flow spec roleSkillBundles.{bundle_name}",
        )
        if not explicit_values:
            out[bundle_name] = []
            continue
        merged = list(out.get(bundle_name, []))
        for item in explicit_values:
            if item not in merged:
                merged.append(item)
        out[bundle_name] = merged
    return out


def _string_list(value: object, *, context: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise KindEnvelopeError(f"{context} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _apply_config_uses(
    body: dict,
    config_profiles: dict[str, dict],
    role_sets: dict[str, dict],
    *,
    profile_source_files: list[dict[str, str]] | None = None,
) -> dict:
    uses = body.get("uses") or []
    if not uses:
        clean = dict(body)
        clean.pop("profile_sources", None)
        if profile_source_files:
            clean["_config_profile_sources"] = [
                _copy_value(item) for item in profile_source_files
            ]
        return clean
    if isinstance(uses, str):
        uses = [uses]
    if not isinstance(uses, list):
        raise KindEnvelopeError("ZfConfig spec.uses must be a list of profile names")

    base: dict[str, Any] = {}
    sources: list[dict[str, str]] = []
    for raw_ref in uses:
        ref = str(raw_ref or "").strip()
        if not ref:
            continue
        resolved_body, resolved_sources = _resolve_config_use(
            ref,
            config_profiles,
            role_sets,
            stack=(),
            context="ZfConfig.spec.uses",
        )
        _deep_merge(base, resolved_body, path=f"use[{ref}]", override=False)
        sources.extend(resolved_sources)

    project_body = dict(body)
    project_body.pop("uses", None)
    project_body.pop("profile_sources", None)
    _deep_merge(base, project_body, path="ZfConfig.spec", override=True)
    if profile_source_files:
        sources.extend(_copy_value(item) for item in profile_source_files)
    if sources:
        base["_config_profile_sources"] = sources
    return base


def _resolve_config_use(
    ref: str,
    config_profiles: dict[str, dict],
    role_sets: dict[str, dict],
    *,
    stack: tuple[str, ...],
    context: str,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if ref in config_profiles:
        if ref in stack:
            cycle = " -> ".join((*stack, ref))
            raise KindEnvelopeError(
                f"{context}: ConfigProfile uses cycle detected: {cycle}"
            )
        profile_spec = config_profiles[ref]
        base: dict[str, Any] = {}
        sources: list[dict[str, str]] = []
        for nested_ref in _uses_list(
            profile_spec.get("uses"),
            context=f"ConfigProfile[{ref}].uses",
        ):
            nested_body, nested_sources = _resolve_config_use(
                nested_ref,
                config_profiles,
                role_sets,
                stack=(*stack, ref),
                context=f"ConfigProfile[{ref}].uses",
            )
            _deep_merge(
                base,
                nested_body,
                path=f"ConfigProfile[{ref}].uses[{nested_ref}]",
                override=False,
            )
            sources.extend(nested_sources)
        own_spec = dict(profile_spec)
        own_spec.pop("uses", None)
        _deep_merge(
            base,
            own_spec,
            path=f"ConfigProfile[{ref}]",
            override=False,
        )
        sources.append(_source_ref("ConfigProfile", ref, profile_spec))
        return base, sources
    if ref in role_sets:
        roles = _roles_from_role_set(ref, role_sets[ref])
        return (
            {"roles": roles},
            [_source_ref("RoleSet", ref, role_sets[ref])],
        )
    raise KindEnvelopeError(
        f"{context} references unknown profile {ref!r}; "
        f"available ConfigProfile={sorted(config_profiles)}, "
        f"RoleSet={sorted(role_sets)}"
    )


def _uses_list(value: object, *, context: str) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise KindEnvelopeError(f"{context} must be a list of profile names")
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _source_ref(kind: str, name: str, spec: dict) -> dict[str, str]:
    payload = json.dumps(spec, ensure_ascii=False, sort_keys=True).encode()
    return {
        "kind": kind,
        "name": name,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _deep_merge(
    target: dict,
    incoming: dict,
    *,
    path: str,
    override: bool,
) -> None:
    if not isinstance(incoming, dict):
        raise KindEnvelopeError(f"{path}: profile body must be a mapping")
    for key, value in incoming.items():
        k = str(key)
        if k.startswith("_"):
            continue
        current = target.get(k)
        current_path = f"{path}.{k}"
        if current is None:
            target[k] = _copy_value(value)
            continue
        if isinstance(current, dict) and isinstance(value, dict):
            _deep_merge(current, value, path=current_path, override=override)
            continue
        if isinstance(current, list) and isinstance(value, list):
            target[k] = _merge_named_list(
                current,
                value,
                path=current_path,
                override=override,
            )
            continue
        if override:
            target[k] = _copy_value(value)
            continue
        if current != value:
            raise KindEnvelopeError(
                f"{current_path}: conflicting profile value without explicit "
                "project override"
            )


def _merge_named_list(
    left: list,
    right: list,
    *,
    path: str,
    override: bool,
) -> list:
    out = [_copy_value(item) for item in left]
    index: dict[str, int] = {}
    for pos, item in enumerate(out):
        if isinstance(item, dict):
            item_id = _item_identity(item)
            if item_id:
                index[item_id] = pos
    for item in right:
        if not isinstance(item, dict):
            if item not in out:
                out.append(_copy_value(item))
            continue
        item_id = _item_identity(item)
        if not item_id:
            out.append(_copy_value(item))
            continue
        if item_id in index:
            if override:
                out[index[item_id]] = _copy_value(item)
                continue
            raise KindEnvelopeError(
                f"{path}: duplicate topology item {item_id!r} from profiles"
            )
        index[item_id] = len(out)
        out.append(_copy_value(item))
    return out


def _item_identity(item: dict) -> str:
    for key in ("name", "instance_id", "id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    trigger = str(item.get("trigger") or "").strip()
    if trigger:
        return f"trigger:{trigger}"
    return ""


def _copy_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _roles_from_role_set(name: str, spec: dict) -> list[dict[str, Any]]:
    backend = str(spec.get("backend") or "").strip()
    model = str(spec.get("model") or "").strip()
    lane_count = int(spec.get("lane_count") or spec.get("lanes") or 0)
    if lane_count < 1:
        raise KindEnvelopeError(f"RoleSet {name!r}: lanes/lane_count must be >= 1")
    stages = spec.get("stages") or {}
    stage_items: list[tuple[str, dict[str, Any]]] = []
    if isinstance(stages, dict):
        stage_items = [
            (str(stage_id), dict(raw or {}))
            for stage_id, raw in stages.items()
            if str(stage_id).strip()
        ]
    elif isinstance(stages, list):
        for raw in stages:
            if not isinstance(raw, dict):
                raise KindEnvelopeError(
                    f"RoleSet {name!r}: stages[] must be mappings"
                )
            stage_id = str(raw.get("id") or "").strip()
            if not stage_id:
                raise KindEnvelopeError(
                    f"RoleSet {name!r}: stages[].id is required"
                )
            stage_items.append((stage_id, dict(raw)))
    else:
        raise KindEnvelopeError(f"RoleSet {name!r}: stages must be a mapping/list")
    if not stage_items:
        raise KindEnvelopeError(f"RoleSet {name!r}: stages must not be empty")

    roles: list[dict[str, Any]] = []
    for order, (stage_id, raw) in enumerate(stage_items):
        pattern = str(
            raw.get("role_pattern") or raw.get("rolePattern")
            or f"{stage_id}-lane-{{lane}}"
        )
        role_kind = str(raw.get("role_kind") or raw.get("roleKind") or "").strip()
        if not role_kind:
            role_kind = "writer" if order == 0 else "reader"
        skills = raw.get("skills") or []
        for lane in range(lane_count):
            role_name = pattern.format(lane=lane)
            role = {
                "name": role_name,
                "instance_id": role_name,
                "backend": str(raw.get("backend") or backend or "claude-code"),
                "role_kind": role_kind,
                "stages": [stage_id],
                "publishes": [
                    f"{stage_id}.child.completed",
                    f"{stage_id}.child.failed",
                ],
            }
            role_model = str(raw.get("model") or model or "").strip()
            if role_model:
                role["model"] = role_model
            if isinstance(skills, list):
                role["skills"] = [str(s) for s in skills if str(s).strip()]
            roles.append(role)
    return roles
