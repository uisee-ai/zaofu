"""schemaProfile — 随发行 event-schema 契约库(doc 90 §3.2,A2)。

治 cj-min 160 行手抄 event_schemas:通用契约引用而非复制。

- `/vN` 不可变:改契约 = 发新版,老项目不被静默改语义;
- merge 优先级:profile → spec.schema_overrides → 项目
  `workflow.dag.event_schemas`(最高,逃生门);
- override 分级:additive(只增 required)= INFO;breaking(删/放宽
  required)= baseline WARN、strict/release 由 loader 升 ConfigError。
"""

from __future__ import annotations

from typing import Any


class SchemaProfileError(ValueError):
    """profile 解析/引用失败——loader 包装为 ConfigError。"""


def _req(*fields: str) -> dict[str, Any]:
    return {"required": list(fields)}


_LANE_STAGE_HANDOFF_EVENTS: dict[str, dict[str, Any]] = {
    "lane.stage.completed": _req(
        "pipeline_id", "trace_id", "task_id", "lane_id", "stage_slot",
        "next_stage_slot", "attempt_id", "fanout_id", "child_id", "run_id",
        "role_instance", "task_map_ref", "handoff_ref", "source_commit",
        "evidence_refs",
    ),
    "lane.stage.failed": _req(
        "pipeline_id", "task_id", "lane_id", "stage_slot", "attempt_id",
        "failure_target", "status", "reason",
    ),
}


# refactor-flow/v1 — 提炼自 cj-min zf-lane-pipeline.yaml(2026-06-11)
# 手抄的 22 事件 required 集。/v1 不可变。
_REFACTOR_FLOW_V1: dict[str, dict[str, Any]] = {
    "refactor.scan.requested": _req(
        "pdd_id", "target_ref", "source_branch", "source_commit",
        "source_tree_status", "objective", "prompt_ref",
    ),
    "refactor.scan.completed": _req(
        "fanout_id", "child_id", "status", "summary", "findings",
        "coverage_matrix", "evidence_refs", "git_refs",
    ),
    "refactor.scan.failed": _req("fanout_id", "child_id", "status", "reason"),
    "zaofu.refactor.review.ready": {
        **_req(
            "fanout_id", "stage_id", "status", "artifact_refs",
            "artifact_digests", "review_artifact_ref", "coverage_matrix_ref",
            "findings_ref", "uncovered_ref",
        ),
        # 1404:kernel 铸造此事件时无法自行派生的字段 —— synth/child 必须
        # 在完成 payload 里给,kernel 透传。缺失在聚合期指名道姓 WARN。
        "field_sources": {
            "review_artifact_ref": "synth",
            "coverage_matrix_ref": "synth",
            "findings_ref": "synth",
            "uncovered_ref": "synth",
        },
    },
    "refactor.plan.child.completed": _req(
        "fanout_id", "child_id", "status", "summary", "evidence_refs",
    ),
    "refactor.plan.child.failed": _req(
        "fanout_id", "child_id", "status", "reason",
    ),
    "zaofu.refactor.plan.blocked": _req("fanout_id", "stage_id", "status"),
    "zaofu.refactor.plan.ready": {
        **_req(
            "fanout_id", "stage_id", "status", "artifact_gate", "artifact_refs",
            "artifact_digests", "plan_artifact_ref", "scan_quality_audit_ref",
            "task_map_ref",
        ),
        # 1404:v3 sim 实测缺口正是 scan_quality_audit_ref(synth 没给,
        # kernel 铸造时静默缺失,只剩 schema 门事后兜底)。
        "field_sources": {
            "plan_artifact_ref": "synth",
            "scan_quality_audit_ref": "synth",
            "task_map_ref": "synth",
        },
    },
    "task_map.ready": _req(
        "pdd_id", "trace_id", "task_map_ref", "source_commit",
        "candidate_base_commit",
    ),
    "candidate.ready": _req(
        "fanout_id", "pdd_id", "candidate_ref", "candidate_base_commit",
        "candidate_head_commit", "diff_ref", "completed_task_ids",
    ),
    "review.child.completed": _req(
        "fanout_id", "child_id", "status", "summary", "evidence_refs",
        "git_refs",
    ),
    "review.child.failed": _req("fanout_id", "child_id", "status", "reason"),
    "review.approved": _req("fanout_id", "stage_id", "status", "target_ref"),
    "review.rejected": _req("fanout_id", "stage_id", "status", "target_ref"),
    "verify.child.completed": _req(
        "fanout_id", "child_id", "status", "summary", "evidence_refs",
        "git_refs",
    ),
    "verify.child.failed": _req("fanout_id", "child_id", "status", "reason"),
    "test.passed": _req("fanout_id", "stage_id", "status", "target_ref"),
    "test.failed": _req("fanout_id", "stage_id", "status", "target_ref"),
    "judge.child.completed": _req(
        "fanout_id", "child_id", "status", "summary", "evidence_refs",
        "git_refs",
    ),
    "judge.child.failed": _req("fanout_id", "child_id", "status", "reason"),
    "judge.passed": _req("fanout_id", "stage_id", "status", "target_ref"),
    "judge.failed": _req("fanout_id", "stage_id", "status", "target_ref"),
}

# refactor-flow/v2 — v1 + kernel-owned lane stage handoff 事件。v1 保持
# 不变,需要 per-lane stage transition 的项目显式升级到 v2。
_REFACTOR_FLOW_V2: dict[str, dict[str, Any]] = {
    **_REFACTOR_FLOW_V1,
    **_LANE_STAGE_HANDOFF_EVENTS,
}

# canonical-dag/v1 — 通用 stage 语法事件契约(G1,2026-06-11)。
# 面向任何走 canonical-dag 词汇(review/test/judge/candidate 家族)的项目:
# 引用即得 schema 强制,不需要项目自带 160 行手抄。required 取通用基线
# (stage 终态:fanout_id/stage_id/status;child 终态:fanout_id/child_id/
# status)——项目可经 additive override 收紧,不可放宽(分级同 A2)。
_CANONICAL_DAG_V1: dict[str, dict[str, Any]] = {
    "task_map.ready": _req("task_map_ref"),
    "candidate.ready": _req(
        "fanout_id", "candidate_ref", "candidate_base_commit",
    ),
    "candidate.conflict": _req("fanout_id", "status"),
    "integration.failed": _req("fanout_id", "status"),
    "review.approved": _req("fanout_id", "stage_id", "status"),
    "review.rejected": _req("fanout_id", "stage_id", "status"),
    "review.child.completed": _req("fanout_id", "child_id", "status"),
    "review.child.failed": _req("fanout_id", "child_id", "status"),
    "test.passed": _req("fanout_id", "stage_id", "status"),
    "test.failed": _req("fanout_id", "stage_id", "status"),
    "verify.child.completed": _req("fanout_id", "child_id", "status"),
    "verify.child.failed": _req("fanout_id", "child_id", "status"),
    "judge.passed": _req("fanout_id", "stage_id", "status"),
    "judge.failed": _req("fanout_id", "stage_id", "status"),
    "judge.child.completed": _req("fanout_id", "child_id", "status"),
    "judge.child.failed": _req("fanout_id", "child_id", "status"),
}

_CANONICAL_DAG_V2: dict[str, dict[str, Any]] = {
    **_CANONICAL_DAG_V1,
    **_LANE_STAGE_HANDOFF_EVENTS,
}

SCHEMA_PROFILES: dict[str, dict[str, dict[str, Any]]] = {
    "refactor-flow/v1": _REFACTOR_FLOW_V1,
    "refactor-flow/v2": _REFACTOR_FLOW_V2,
    "canonical-dag/v1": _CANONICAL_DAG_V1,
    "canonical-dag/v2": _CANONICAL_DAG_V2,
}


def resolve_schema_profile(
    name: str,
    extra_profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """B1: extra_profiles = kind: SchemaProfile 注册的项目本地库,
    与内建同名时内建优先(发行契约不可被项目影子化)。"""
    source = None
    if name in SCHEMA_PROFILES:
        source = SCHEMA_PROFILES[name]
    elif extra_profiles and name in extra_profiles:
        source = extra_profiles[name]
    if source is None:
        known = sorted(set(SCHEMA_PROFILES) | set(extra_profiles or {}))
        raise SchemaProfileError(
            f"unknown schema profile {name!r}; known profiles: {known}"
        )
    # 深拷贝防原地篡改(/vN 不可变);field_sources(1404)一并透传
    out: dict[str, dict[str, Any]] = {}
    for event, rule in source.items():
        entry: dict[str, Any] = {"required": list(rule.get("required", []))}
        if isinstance(rule.get("field_sources"), dict):
            entry["field_sources"] = dict(rule["field_sources"])
        out[event] = entry
    return out


def classify_override(
    base_rule: dict[str, Any] | None,
    override_rule: dict[str, Any],
) -> str:
    """additive(只增 required)→ 'additive';删/放宽 → 'breaking';
    base 无此事件 → 'additive'(新增事件规则不破坏 profile 契约)。"""
    if not base_rule:
        return "additive"
    base_req = set(base_rule.get("required", []) or [])
    over_req = set(override_rule.get("required", []) or [])
    if base_req - over_req:
        return "breaking"  # profile 的 required 字段被删/放宽
    return "additive"


def merge_event_schemas(
    *,
    profile_name: str,
    spec_overrides: dict[str, Any] | None,
    local_schemas: dict[str, Any] | None,
    harness_profile: str,
    extra_profiles: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    """合并三层并分级。

    返回 (effective_schemas, sources(event→profile|override|local),
    diagnostics)。breaking override 在 strict/release 下产出
    severity=ERROR 的诊断——loader 据此抛 ConfigError;baseline 为 WARN。
    """
    effective = (
        resolve_schema_profile(profile_name, extra_profiles)
        if profile_name else {}
    )
    sources: dict[str, str] = {e: "profile" for e in effective}
    diagnostics: list[dict[str, Any]] = []

    def _apply(layer: dict[str, Any] | None, label: str) -> None:
        for event, rule in (layer or {}).items():
            if not isinstance(rule, dict):
                continue
            klass = classify_override(effective.get(event), rule)
            if klass == "breaking":
                severity = (
                    "ERROR" if harness_profile in ("strict", "release")
                    else "WARN"
                )
                diagnostics.append({
                    "kind": "schema_profile_breaking_override",
                    "severity": severity,
                    "event": event,
                    "layer": label,
                    "message": (
                        f"{label} override on {event!r} removes/relaxes "
                        f"profile-required fields "
                        f"(profile={profile_name}); breaking overrides are "
                        f"{severity} under harness_profile="
                        f"{harness_profile}"
                    ),
                })
            else:
                diagnostics.append({
                    "kind": "schema_profile_additive_override",
                    "severity": "INFO",
                    "event": event,
                    "layer": label,
                    "message": f"{label} additive override on {event!r}",
                })
            effective[event] = {
                "required": list(rule.get("required", []) or []),
            }
            if isinstance(rule.get("field_sources"), dict):
                effective[event]["field_sources"] = dict(rule["field_sources"])
            sources[event] = label
    _apply(spec_overrides, "override")
    _apply(local_schemas, "local")
    return effective, sources, diagnostics


def synth_owed_gaps(
    event_type: str,
    payload: dict[str, Any],
    event_schemas: dict[str, Any] | None,
) -> list[str]:
    """1404:kernel 铸造 stage 事件前的 payload 来源契约检查。

    返回 profile 标注为 source==synth、required、且 payload 缺失的
    字段列表(指名道姓)。空列表 = 契约满足。kernel 自有字段
    (fanout_id/status/artifact_gate 等)不在此列——它们由铸造方注入。
    """
    rule = (event_schemas or {}).get(event_type)
    if not isinstance(rule, dict):
        return []
    sources = rule.get("field_sources")
    if not isinstance(sources, dict):
        return []
    required = set(rule.get("required", []) or [])
    return sorted(
        field
        for field, owner in sources.items()
        if owner == "synth" and field in required
        and not str(payload.get(field) or "").strip()
    )
