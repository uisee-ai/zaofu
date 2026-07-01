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
    "RefactorFlow",
})


def assemble_envelope_stream(
    documents: list[Any],
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
        return docs[0], {}

    body: dict | None = None
    pipelines: list[dict] = []
    profiles: dict[str, dict] = {}
    kind_stages: list[dict] = []
    flow_expansions: list[dict] = []
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
            from zf.core.config.workflow_profiles import (
                WorkflowProfileError,
                expand_workflow_profile,
            )
            try:
                flow_expansions.append(expand_workflow_profile(dict(spec)))
            except WorkflowProfileError as exc:
                raise KindEnvelopeError(str(exc))
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
            profiles[name] = {
                str(e): {"required": list((r or {}).get("required", []))}
                for e, r in events.items()
            }
    if body is None:
        raise KindEnvelopeError(
            "a kind: ZfConfig document is required (project body)"
        )
    if flow_expansions:
        from zf.core.config.workflow_profiles import (
            merge_expansion_into_body,
        )
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
