"""schemaProfile — 随发行 event-schema 契约库(doc 90 §3.2,A2)。

治 cj-min 160 行手抄 event_schemas:通用契约引用而非复制。

- `/vN` 不可变:改契约 = 发新版,老项目不被静默改语义;
- merge 优先级:profile → spec.schema_overrides → 项目
  `workflow.dag.event_schemas`(最高,逃生门);
- override 分级:additive(只增 required)= INFO;breaking(删/放宽
  required)= baseline WARN、strict/release 由 loader 升 ConfigError。
"""

from __future__ import annotations

from copy import deepcopy
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

# canonical-dag/v3 — v2 + 读者子报告证据档位(LB-4,2026-07-08)。
# r3 light 实弹:verify/judge 子报告带判决但 evidence_refs 空、矩阵 0 行,
# briefing 条款(advisory)被无视 → 档位入 schema(FIX-14 non_empty)。
# 只加在 agent 报告面的 child 完成事件上;kernel 铸造的 stage 聚合与 v2
# 的 lane handoff 契约原样保留。briefing 教育由 FIX-14
# `_schema_education_report_fields` 按此规则自动镜像,不需另配。
_READER_CHILD_EVIDENCE: dict[str, Any] = {
    "required": [
        "fanout_id", "child_id", "status", "summary", "evidence_refs",
        "report",
    ],
    "non_empty": ["summary", "evidence_refs"],
    "nested": {
        "report": {
            "required": ["requirement_coverage_matrix"],
            "non_empty": ["requirement_coverage_matrix"],
        },
    },
}

_CANONICAL_DAG_V3: dict[str, dict[str, Any]] = {
    **_CANONICAL_DAG_V2,
    "verify.child.completed": _READER_CHILD_EVIDENCE,
    "review.child.completed": _READER_CHILD_EVIDENCE,
    "judge.child.completed": _READER_CHILD_EVIDENCE,
}

_VERIFICATION_RESULT_V1_RULE: dict[str, Any] = {
    "required": [
        "fanout_id", "child_id", "status", "summary", "evidence_refs",
        "report", "workflow_run_id", "task_id", "contract_revision",
        "task_map_generation", "base_commit", "task_ref",
        "contract_snapshot_ref", "contract_snapshot_digest",
        "target_snapshot_ref", "target_commit",
        "target_snapshot_digest", "verification_result",
    ],
    "non_empty": [
        "summary", "workflow_run_id", "task_id", "contract_revision",
        "task_map_generation", "base_commit", "task_ref",
        "contract_snapshot_ref", "contract_snapshot_digest",
        "target_snapshot_ref", "target_commit",
        "target_snapshot_digest",
    ],
    "nested": {
        "verification_result": {
            "required": [
                "schema_version", "execution_status", "verdict",
                "failure_class", "workflow_run_id", "task_id",
                "contract_revision", "task_map_generation", "base_commit",
                "task_ref", "contract_snapshot_ref",
                "contract_snapshot_digest", "target_snapshot_ref",
                "target_commit", "target_snapshot_digest",
                "verification_owner", "verification_tier",
                "requirement_results",
            ],
            "non_empty": [
                "schema_version", "execution_status", "verdict", "failure_class",
                "workflow_run_id", "task_id", "contract_revision",
                "task_map_generation", "base_commit", "task_ref",
                "contract_snapshot_ref", "contract_snapshot_digest",
                "target_snapshot_ref", "target_commit", "target_snapshot_digest",
                "verification_owner", "verification_tier",
            ],
            "enum": {
                "execution_status": ["completed", "failed"],
                "verdict": ["passed", "rejected", "blocked", "abstained"],
                "failure_class": [
                    "none", "product_failure", "dependency_blocked",
                    "verifier_execution_failure", "verifier_contract_failure",
                ],
                "verification_owner": [
                    "impl_self_check", "task_verify", "candidate_verify", "human",
                ],
                "verification_tier": [
                    "fast", "task_non_smoke", "integration", "real_e2e", "release",
                ],
            },
            "list_item": {
                "requirement_results": {
                    "required": [
                        "acceptance_id", "status", "verification_owner",
                        "verification_tier", "evidence_refs", "findings",
                        "reproduction_commands",
                    ],
                    "non_empty": [
                        "acceptance_id", "status", "verification_owner",
                        "verification_tier",
                    ],
                    "enum": {
                        "status": [
                            "passed", "failed", "blocked", "waived",
                            "not_applicable",
                        ],
                        "verification_owner": [
                            "impl_self_check", "task_verify", "candidate_verify", "human",
                        ],
                        "verification_tier": [
                            "fast", "task_non_smoke", "integration", "real_e2e", "release",
                        ],
                    },
                },
            },
            "when": {
                "if": {"execution_status": "completed"},
                "then": {"non_empty": ["requirement_results"]},
            },
        },
    },
}

# Explicit opt-in only. v3 remains immutable and keeps accepting legacy report
# payloads; v4 binds verifier output to one immutable task/target snapshot.
_CANONICAL_DAG_V4: dict[str, dict[str, Any]] = {
    **_CANONICAL_DAG_V3,
    "verify.child.completed": _VERIFICATION_RESULT_V1_RULE,
    "verify.child.failed": _VERIFICATION_RESULT_V1_RULE,
    "review.child.completed": _VERIFICATION_RESULT_V1_RULE,
    "review.child.failed": _VERIFICATION_RESULT_V1_RULE,
    "judge.child.completed": _VERIFICATION_RESULT_V1_RULE,
    "judge.child.failed": _VERIFICATION_RESULT_V1_RULE,
}

_DURABLE_CALL_RESULT_EVENTS: dict[str, dict[str, Any]] = {
    "workflow.call.result.reported": _req(
        "schema_version", "workflow_run_id", "operation_id", "request_hash",
        "result_digest", "mode", "adapter_id", "adapter_version",
        "envelope_ref", "control_result_ref", "source_event_id",
    ),
    "workflow.call.result.repair.requested": _req(
        "schema_version", "workflow_run_id", "operation_id", "request_hash",
        "envelope_ref", "control_result_ref", "correction_ref", "issues",
        "repair_round", "repair_cap", "semantic_attempt_incremented",
    ),
    "workflow.call.result.admitted": _req(
        "schema_version", "workflow_run_id", "operation_id", "request_hash",
        "admission_status", "mode", "envelope_ref", "control_result_ref",
        "control_result_schema", "source_event_id",
    ),
    "workflow.call.result.invalid": _req(
        "schema_version", "workflow_run_id", "operation_id", "request_hash",
        "envelope_ref", "control_result_ref", "issues", "repair_round",
        "repair_cap", "reason", "semantic_attempt_incremented",
    ),
    "workflow.operation.requested": _req(
        "schema_version", "canonicalization_version", "workflow_run_id",
        "operation_id", "operation_type", "request_hash", "request_ref",
    ),
    "workflow.operation.started": _req(
        "schema_version", "workflow_run_id", "operation_id", "request_hash",
    ),
    "workflow.operation.settled": _req(
        "schema_version", "workflow_run_id", "operation_id", "request_hash",
        "admitted_call_result_ref", "reason",
    ),
    "workflow.operation.failed": _req(
        "schema_version", "workflow_run_id", "operation_id", "request_hash",
        "reason",
    ),
    "workflow.operation.blocked": _req(
        "schema_version", "workflow_run_id", "operation_id", "request_hash",
        "reason",
    ),
}

# Explicit opt-in. v4 remains immutable; v5 adds only mechanical call-result
# and workflow-operation contracts. Product verdict semantics stay in typed
# sidecars and parent agents rather than this profile.
_CANONICAL_DAG_V5: dict[str, dict[str, Any]] = {
    **_CANONICAL_DAG_V4,
    **_DURABLE_CALL_RESULT_EVENTS,
}

_GOAL_CLOSURE_CHILD_RULE: dict[str, Any] = {
    "required": [
        "fanout_id", "child_id", "status", "summary",
        "workflow_run_id", "operation_id", "request_hash",
        "task_map_generation", "target_commit",
        "contract_snapshot_ref", "contract_snapshot_digest",
        "target_snapshot_ref", "target_snapshot_digest",
        "goal_closure_result",
    ],
    "non_empty": [
        "summary", "workflow_run_id", "operation_id", "request_hash",
        "task_map_generation", "target_commit",
        "contract_snapshot_ref", "contract_snapshot_digest",
        "target_snapshot_ref", "target_snapshot_digest",
    ],
    "nested": {
        "goal_closure_result": {
            "required": [
                "schema_version", "workflow_run_id", "goal_id", "flow_kind",
                "task_map_generation", "target_commit", "objective_ref",
                "goal_claim_set_ref", "goal_claim_set_digest",
                "planning_result_ref", "candidate_ref", "closure_fact_ref",
                "closure_fact_digest", "input_result_refs", "goal_coverage",
                "open_gap_refs", "verdict", "recommended_action", "summary",
            ],
            "non_empty": [
                "schema_version", "workflow_run_id", "goal_id", "flow_kind",
                "task_map_generation", "target_commit", "objective_ref",
                "goal_claim_set_ref", "goal_claim_set_digest",
                "planning_result_ref", "candidate_ref", "closure_fact_ref",
                "closure_fact_digest", "input_result_refs", "goal_coverage",
                "verdict", "recommended_action", "summary",
            ],
            "enum": {
                "flow_kind": ["issue", "prd", "refactor"],
                "verdict": ["passed", "rejected", "blocked"],
                "recommended_action": [
                    "complete", "gap_plan", "replan", "candidate_verify",
                    "human", "hold",
                ],
            },
            "list_item": {
                "goal_coverage": {
                    "required": [
                        "goal_claim_id", "status", "supporting_result_refs",
                    ],
                    "non_empty": ["goal_claim_id", "status"],
                    "enum": {
                        "status": ["closed", "open", "blocked", "waived"],
                    },
                },
            },
        },
    },
}

_GOAL_CLOSURE_RESULT_RULE = _GOAL_CLOSURE_CHILD_RULE["nested"][
    "goal_closure_result"
]

_GOAL_CLOSURE_EVENTS: dict[str, dict[str, Any]] = {
    "goal.claim_set.pinned": _req(
        "workflow_run_id", "goal_id", "task_map_generation",
        "task_map_ref", "goal_claim_set_ref", "goal_claim_set_digest",
    ),
    "flow.goal.closed": _req(
        "workflow_run_id", "goal_id", "task_map_generation",
        "candidate_head_commit", "closure_fact_ref",
        "closure_fact_digest", "closure_identity",
    ),
    "module.parity.closed": _req(
        "workflow_run_id", "goal_id", "task_map_generation",
        "candidate_head_commit", "closure_fact_ref",
        "closure_fact_digest", "closure_identity",
    ),
    "goal.closure.synthesized": {
        **_req(
            "fanout_id", "stage_id", "status", "workflow_run_id", "goal_id",
            "task_map_generation", "candidate_head_commit", "closure_identity",
            "closure_fact_ref", "closure_fact_digest", "goal_claim_set_ref",
            "goal_claim_set_digest", "operation_id", "request_hash",
            "contract_snapshot_ref", "contract_snapshot_digest",
            "target_snapshot_ref", "target_snapshot_digest",
            "admitted_call_result_ref", "control_result_ref", "goal_closure_result",
        ),
        "nested": {"goal_closure_result": _GOAL_CLOSURE_RESULT_RULE},
    },
    "goal.closure.synthesis.failed": _req(
        "fanout_id", "stage_id", "status",
    ),
    "run.goal.completion.claimed": _req(
        "run_id", "goal_id", "claim_id", "task_map_generation",
        "target_commit", "goal_claim_set_ref", "goal_claim_set_digest",
        "admitted_call_result_ref",
    ),
    "run.goal.completion.blocked": _req(
        "run_id", "claim_id", "blockers", "blocker_fingerprint",
    ),
    "run.goal.completion.rejected": _req(
        "run_id", "claim_id", "invalid_reasons",
    ),
    "run.goal.completed": _req("run_id", "claim_id", "target_commit"),
    "run.delivery.requested": _req(
        "run_id", "claim_id", "delivery_operation_id", "candidate_ref",
    ),
    "run.delivery.settled": _req(
        "run_id", "claim_id", "delivery_operation_id", "candidate_ref",
    ),
    "run.delivery.failed": _req(
        "run_id", "claim_id", "delivery_operation_id", "candidate_ref",
        "reason",
    ),
    "run.delivery.blocked": _req(
        "run_id", "claim_id", "delivery_operation_id", "candidate_ref",
        "reason",
    ),
    "goal.closure.rejected": _req(
        "workflow_run_id", "goal_id", "task_map_generation", "target_commit",
        "admitted_call_result_ref", "recommended_action", "open_gap_refs",
    ),
    "goal.closure.blocked": _req(
        "workflow_run_id", "goal_id", "task_map_generation", "target_commit",
        "admitted_call_result_ref", "recommended_action", "reason",
    ),
    "goal.closure.compat.projected": _req(
        "workflow_run_id", "goal_id", "source_event_id", "compat_event_id",
        "compat_event_type", "admitted_call_result_ref",
    ),
}

# Explicit opt-in. v5 remains immutable; v6 replaces only the final Judge
# child contract and adds the mechanical Goal-closure lifecycle.
_CANONICAL_DAG_V6: dict[str, dict[str, Any]] = {
    **_CANONICAL_DAG_V5,
    **_GOAL_CLOSURE_EVENTS,
    "judge.child.completed": _GOAL_CLOSURE_CHILD_RULE,
    "judge.child.failed": _req("fanout_id", "child_id", "status", "reason"),
}

# Refactor keeps its historical event vocabulary but opts into the same Thin
# Judge / durable call-result / completion contracts as canonical-dag/v6.
_REFACTOR_FLOW_V3: dict[str, dict[str, Any]] = {
    **_REFACTOR_FLOW_V2,
    **_DURABLE_CALL_RESULT_EVENTS,
    **_GOAL_CLOSURE_EVENTS,
    "judge.child.completed": _GOAL_CLOSURE_CHILD_RULE,
    "judge.child.failed": _req("fanout_id", "child_id", "status", "reason"),
}

SCHEMA_PROFILES: dict[str, dict[str, dict[str, Any]]] = {
    "refactor-flow/v1": _REFACTOR_FLOW_V1,
    "refactor-flow/v2": _REFACTOR_FLOW_V2,
    "refactor-flow/v3": _REFACTOR_FLOW_V3,
    "canonical-dag/v1": _CANONICAL_DAG_V1,
    "canonical-dag/v2": _CANONICAL_DAG_V2,
    "canonical-dag/v3": _CANONICAL_DAG_V3,
    "canonical-dag/v4": _CANONICAL_DAG_V4,
    "canonical-dag/v5": _CANONICAL_DAG_V5,
    "canonical-dag/v6": _CANONICAL_DAG_V6,
}

_RULE_KEYS = (
    "required", "optional", "non_empty", "enum", "nested", "list_item",
    "when", "field_sources",
)


def _copy_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(rule[key])
        for key in _RULE_KEYS
        if key in rule
    }


def _merge_rule(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = _copy_rule(base)
    for key in _RULE_KEYS:
        if key in override:
            merged[key] = deepcopy(override[key])
    merged.setdefault("required", [])
    return merged


def union_required_keys(event_type: str) -> tuple[str, ...]:
    """Union of `required` keys for an event across ALL built-in profiles.

    For kernel emitters whose event type is config-driven (a stage's
    failure_event may name any contract event): forge these keys present
    (value may be empty) before emitting, else a blocking discriminator
    replaces the failure signal with discriminator.failed and the run
    wedges (2026-07-10 R6: fanout verify-timeout published
    lane.stage.failed without the lane-pipeline keys)."""
    out: list[str] = []
    for table in SCHEMA_PROFILES.values():
        rule = table.get(event_type)
        if not isinstance(rule, dict):
            continue
        for key in rule.get("required", []):
            if key not in out:
                out.append(str(key))
    return tuple(out)


def resolve_schema_profile(
    name: str,
    extra_profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """B1: extra_profiles = kind: SchemaProfile 注册的项目本地库,
    与内建同名时内建优先(发行契约不可被项目影子化)。"""
    return _resolve_schema_profile(name, extra_profiles, stack=())


def _resolve_schema_profile(
    name: str,
    extra_profiles: dict[str, dict[str, Any]] | None,
    *,
    stack: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    if name in stack:
        raise SchemaProfileError(
            "schema profile extends cycle: " + " -> ".join((*stack, name))
        )
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
    extends = ""
    event_source = source
    if isinstance(source.get("events"), dict):
        extends = str(source.get("extends") or "").strip()
        event_source = source["events"]
    out = (
        _resolve_schema_profile(extends, extra_profiles, stack=(*stack, name))
        if extends
        else {}
    )
    for event, rule in event_source.items():
        if not isinstance(rule, dict):
            continue
        out[str(event)] = _merge_rule(out.get(str(event), {}), rule)
    return out


def classify_override(
    base_rule: dict[str, Any] | None,
    override_rule: dict[str, Any],
) -> str:
    """additive(只增 required/收紧档位)→ 'additive';删/放宽 → 'breaking';
    base 无此事件 → 'additive'(新增事件规则不破坏 profile 契约)。

    non_empty / nested 只在 override **显式提供**该键时参与分级(缺席 =
    继承 base,见 merge_event_schemas);显式缩水 profile 档位 = breaking。"""
    if not base_rule:
        return "additive"
    base_req = set(base_rule.get("required", []) or [])
    over_req = set(override_rule.get("required", []) or [])
    if base_req - over_req:
        return "breaking"  # profile 的 required 字段被删/放宽
    if "non_empty" in override_rule:
        base_ne = set(base_rule.get("non_empty", []) or [])
        over_ne = set(override_rule.get("non_empty", []) or [])
        if base_ne - over_ne:
            return "breaking"  # profile 的 non_empty 档位被放宽
    if "nested" in override_rule:
        if (base_rule.get("nested") or {}) and not (override_rule.get("nested") or {}):
            return "breaking"  # profile 的嵌套档位被整体摘除
    for key in ("enum", "list_item", "when"):
        if key in override_rule and base_rule.get(key) and not override_rule.get(key):
            return "breaking"
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
            base_rule = effective.get(event) or {}
            effective[event] = _merge_rule(base_rule, rule)
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
