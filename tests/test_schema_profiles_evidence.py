"""canonical-dag/v3 读者子报告证据档位(LB-4,2026-07-08)。

r3 light 实弹:verify/judge 子报告带判决但 evidence_refs 空、矩阵 0 行,
briefing 条款(advisory)被无视。本组测试钉住根修的四段链:

1. 出厂 profile 携带 non_empty/nested 档位且 resolve 不剥档;
2. merge 三层合并继承档位;显式缩水档位 = breaking;
3. registry 按档位拒空(empty_required);
4. EventWriter blocking 档把违约完成事件替换为 discriminator.failed;
   loader 解析 report_evidence_gate 开关。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema_profiles import (
    classify_override,
    merge_event_schemas,
    resolve_schema_profile,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.verification.event_schema import EventSchemaRegistry


def _full_child_payload(**overrides) -> dict:
    payload = {
        "fanout_id": "fanout-x",
        "child_id": "verify-a",
        "status": "completed",
        "summary": "8 tests pass, gates 3/3.",
        "evidence_refs": ["artifacts/verify/report.json"],
        "report": {
            "requirement_coverage_matrix": [
                {"requirement_id": "AC-1", "status": "covered"},
            ],
        },
    }
    payload.update(overrides)
    return payload


def test_v3_resolves_with_evidence_tier_intact():
    resolved = resolve_schema_profile("canonical-dag/v3")
    rule = resolved["verify.child.completed"]
    assert "evidence_refs" in rule["required"]
    assert "report" in rule["required"]
    assert set(rule["non_empty"]) == {"summary", "evidence_refs"}
    assert rule["nested"]["report"]["non_empty"] == [
        "requirement_coverage_matrix",
    ]
    # v2 沿袭的 lane handoff 契约仍在(v3 是 v2 超集)。
    assert "lane.stage.completed" in resolved


def test_v4_preserves_typed_result_rules_without_mutating_v3():
    v3 = resolve_schema_profile("canonical-dag/v3")
    v4 = resolve_schema_profile("canonical-dag/v4")
    typed = v4["verify.child.completed"]["nested"]["verification_result"]
    assert "target_snapshot_ref" in typed["required"]
    assert typed["enum"]["execution_status"] == ["completed", "failed"]
    assert typed["list_item"]["requirement_results"]["enum"]["status"] == [
        "passed", "failed", "blocked", "waived", "not_applicable",
    ]
    assert typed["when"]["if"] == {"execution_status": "completed"}
    assert "verification_result" not in v3["verify.child.completed"]["required"]


def test_v6_goal_closure_result_binds_current_target_and_claims() -> None:
    canonical = resolve_schema_profile("canonical-dag/v6")
    refactor = resolve_schema_profile("refactor-flow/v3")
    assert canonical["judge.child.completed"] == refactor["judge.child.completed"]
    registry = EventSchemaRegistry.from_dict(canonical)
    result = {
        "schema_version": "goal-closure-result.v1",
        "workflow_run_id": "run-1",
        "goal_id": "GOAL-1",
        "flow_kind": "prd",
        "task_map_generation": "generation-1",
        "target_commit": "a" * 40,
        "objective_ref": "docs/prd.md",
        "goal_claim_set_ref": "artifacts/claims.json",
        "goal_claim_set_digest": "b" * 64,
        "planning_result_ref": "artifacts/task-map.json",
        "candidate_ref": "candidate/GOAL-1",
        "closure_fact_ref": "artifacts/closure.json",
        "closure_fact_digest": "c" * 64,
        "input_result_refs": ["artifacts/verify.json"],
        "goal_coverage": [{
            "goal_claim_id": "GOAL-AC-1",
            "status": "closed",
            "supporting_result_refs": ["artifacts/verify.json"],
        }],
        "open_gap_refs": [],
        "verdict": "passed",
        "recommended_action": "complete",
        "summary": "closed",
    }
    payload = {
        "fanout_id": "fanout-judge",
        "child_id": "judge-prd",
        "status": "completed",
        "summary": "closed",
        "workflow_run_id": "run-1",
        "operation_id": "op-judge",
        "request_hash": "request-hash",
        "task_map_generation": "generation-1",
        "target_commit": "a" * 40,
        "contract_snapshot_ref": "artifacts/contract.json",
        "contract_snapshot_digest": "d" * 64,
        "target_snapshot_ref": "artifacts/target.json",
        "target_snapshot_digest": "e" * 64,
        "goal_closure_result": result,
    }
    assert registry.validate(ZfEvent(
        type="judge.child.completed",
        payload=payload,
    )) == []

    invalid = dict(payload)
    invalid.pop("target_snapshot_digest")
    violations = registry.validate(ZfEvent(
        type="judge.child.completed",
        payload=invalid,
    ))
    assert any(
        item.field_path == "payload.target_snapshot_digest"
        and item.code == "missing_required"
        for item in violations
    )


def test_v7_run_goal_completed_requires_verification_and_delivery_identity() -> None:
    canonical = resolve_schema_profile("canonical-dag/v7")
    refactor = resolve_schema_profile("refactor-flow/v4")
    assert canonical["run.goal.completed"] == refactor["run.goal.completed"]
    registry = EventSchemaRegistry.from_dict(canonical)
    payload = {
        "run_id": "run-1",
        "goal_id": "GOAL-1",
        "claim_id": "claim-1",
        "task_map_generation": "task-map-1",
        "target_commit": "a" * 40,
        "verified_target_commit": "a" * 40,
        "verification_event_id": "verify-event-1",
        "verification_admitted_call_result_ref": {
            "ref": "artifacts/verify.json",
            "sha256": "b" * 64,
        },
        "candidate_event_id": "candidate-event-1",
        "candidate_ref": "candidate/GOAL-1",
        "goal_claim_set_ref": "artifacts/claims.json",
        "goal_claim_set_digest": "c" * 64,
        "admitted_call_result_ref": {
            "ref": "artifacts/closure.json",
            "sha256": "d" * 64,
        },
        "delivery_policy": "report_only",
        "delivery_status": "not_required",
        "delivery_event_id": "",
    }
    assert registry.validate(ZfEvent(type="run.goal.completed", payload=payload)) == []

    invalid = dict(payload)
    invalid["verification_event_id"] = ""
    violations = registry.validate(ZfEvent(type="run.goal.completed", payload=invalid))
    assert any(
        item.field_path == "payload.verification_event_id"
        and item.code == "empty_required"
        for item in violations
    )

    unsettled = dict(payload)
    unsettled["delivery_status"] = "settled"
    violations = registry.validate(ZfEvent(type="run.goal.completed", payload=unsettled))
    assert any(
        item.field_path == "payload.delivery_event_id"
        and item.code == "empty_required"
        for item in violations
    )


def test_v3_registry_rejects_empty_evidence_and_matrix():
    registry = EventSchemaRegistry.from_dict(
        resolve_schema_profile("canonical-dag/v3"),
    )
    event = ZfEvent(
        type="verify.child.completed",
        actor="verify-a",
        payload=_full_child_payload(
            evidence_refs=[],
            report={"requirement_coverage_matrix": []},
        ),
    )
    codes = {(v.field_path, v.code) for v in registry.validate(event)}
    assert ("payload.evidence_refs", "empty_required") in codes
    assert (
        "payload.report.requirement_coverage_matrix", "empty_required",
    ) in codes


def test_v3_lane_stage_failed_requires_failure_target_present_not_nonempty():
    """canonical-dag/v3 requires failure_target/reason PRESENT (not non-empty)
    on lane.stage.failed. The kernel forge (orchestrator_fanout) must include
    them even when empty — a failed stage with no rework_to, or a reasonless
    failure — else the blocking gate replaces the whole failure event with
    discriminator.failed and rework routing never sees it (2026-07-08 E2E)."""
    registry = EventSchemaRegistry.from_dict(
        resolve_schema_profile("canonical-dag/v3"),
    )
    base = {
        "pipeline_id": "p", "task_id": "T-1", "lane_id": "lane0",
        "stage_slot": "impl", "attempt_id": "a1", "status": "failed",
    }
    missing = ZfEvent(type="lane.stage.failed", actor="zf-cli", payload=dict(base))
    codes = {(v.field_path, v.code) for v in registry.validate(missing)}
    assert ("payload.failure_target", "missing_required") in codes
    assert ("payload.reason", "missing_required") in codes

    present_empty = ZfEvent(
        type="lane.stage.failed",
        actor="zf-cli",
        payload={**base, "failure_target": "", "reason": ""},
    )
    codes2 = {(v.field_path, v.code) for v in registry.validate(present_empty)}
    assert ("payload.failure_target", "missing_required") not in codes2
    assert ("payload.reason", "missing_required") not in codes2


def test_v3_registry_passes_full_report():
    registry = EventSchemaRegistry.from_dict(
        resolve_schema_profile("canonical-dag/v3"),
    )
    event = ZfEvent(
        type="verify.child.completed",
        actor="verify-a",
        payload=_full_child_payload(),
    )
    assert registry.validate(event) == []


def test_required_only_local_override_inherits_non_empty():
    effective, sources, diags = merge_event_schemas(
        profile_name="canonical-dag/v3",
        spec_overrides=None,
        local_schemas={
            "verify.child.completed": {
                "required": [
                    "fanout_id", "child_id", "status", "summary",
                    "evidence_refs", "report", "git_refs",
                ],
            },
        },
        harness_profile="baseline",
    )
    rule = effective["verify.child.completed"]
    assert "git_refs" in rule["required"]
    # 只写 required 的 additive override 不再静默剥掉证据档位。
    assert set(rule.get("non_empty") or []) == {"summary", "evidence_refs"}
    assert rule["nested"]["report"]["non_empty"] == [
        "requirement_coverage_matrix",
    ]
    assert sources["verify.child.completed"] == "local"
    assert all(d["severity"] != "ERROR" for d in diags)


def test_explicit_non_empty_shrink_is_breaking():
    base = resolve_schema_profile("canonical-dag/v3")["verify.child.completed"]
    assert classify_override(
        base,
        {"required": list(base["required"]), "non_empty": []},
    ) == "breaking"
    _effective, _sources, diags = merge_event_schemas(
        profile_name="canonical-dag/v3",
        spec_overrides=None,
        local_schemas={
            "verify.child.completed": {
                "required": list(base["required"]),
                "non_empty": [],
            },
        },
        harness_profile="strict",
    )
    assert any(d["severity"] == "ERROR" for d in diags)


def test_writer_blocking_replaces_empty_evidence_completion(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(
        log,
        schema_registry=EventSchemaRegistry.from_dict(
            resolve_schema_profile("canonical-dag/v3"),
        ),
        schema_mode="blocking",
    )
    result = writer.append(ZfEvent(
        type="verify.child.completed",
        actor="verify-a",
        payload=_full_child_payload(evidence_refs=[]),
    ))
    assert result.type == "discriminator.failed"
    types = [e.type for e in log.read_all()]
    assert "verify.child.completed" not in types
    assert result.payload["blocked_event_type"] == "verify.child.completed"

    ok = writer.append(ZfEvent(
        type="verify.child.completed",
        actor="verify-a",
        payload=_full_child_payload(),
    ))
    assert ok.type == "verify.child.completed"


def _loader_yaml(tmp_path: Path, verification: dict) -> Path:
    data = {
        "project": {"name": "test", "state_dir": str(tmp_path / ".zf")},
        "roles": [{"name": "dev", "backend": "mock"}],
        "verification": verification,
    }
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_loader_parses_report_evidence_gate(tmp_path: Path):
    cfg = load_config(_loader_yaml(
        tmp_path, {"report_evidence_gate": "fail_closed"},
    ))
    assert cfg.verification.report_evidence_gate == "fail_closed"


def test_loader_defaults_report_evidence_gate_signal(tmp_path: Path):
    cfg = load_config(_loader_yaml(tmp_path, {"event_schema": {}}))
    assert cfg.verification.report_evidence_gate == "signal"


def test_loader_rejects_unknown_report_evidence_gate(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_config(_loader_yaml(
            tmp_path, {"report_evidence_gate": "maybe"},
        ))


def test_reader_child_briefing_template_is_v3_compliant():
    """LB-4 模板缺口回归:canonical-dag/v3 给 verify/judge.child.completed 加了
    顶层 non_empty[summary, evidence_refs];briefing 成功模板经两级机械教育
    (report.* + 顶层)后必须自洽通过 blocking 校验——否则合规 agent 照抄模板
    也吃 discriminator.failed,执法在 happy path 制造返工。用真 orchestrator
    方法组装,避免测试与实现漂移。"""
    from zf.core.config.schema import (
        ProjectConfig, WorkflowConfig, WorkflowDagConfig, ZfConfig,
    )
    from zf.core.events.model import ZfEvent
    from zf.runtime.orchestrator import Orchestrator

    schemas = resolve_schema_profile("canonical-dag/v3")
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        workflow=WorkflowConfig(dag=WorkflowDagConfig(event_schemas=schemas)),
    )
    orch = Orchestrator.__new__(Orchestrator)
    orch.config = cfg
    registry = EventSchemaRegistry.from_dict(schemas)

    for child_event in ("verify.child.completed", "judge.child.completed",
                        "review.child.completed"):
        sp = {
            "fanout_id": "f", "stage_id": "s", "child_id": "c", "run_id": "r",
            "role_instance": "ri", "status": "completed",
            "report": {"child_id": "c", "status": "passed",
                       "summary": "x", "findings": [], "recommendation": "approve"},
        }
        sp["report"].update(
            orch._schema_education_report_fields(child_event, existing=sp["report"])
        )
        sp.update(orch._schema_education_toplevel_fields(child_event, existing=sp))
        event = ZfEvent(type=child_event, actor="ri", payload=sp)
        assert registry.validate(event) == [], child_event
        assert sp["evidence_refs"], child_event
        assert sp["summary"], child_event
        assert sp["report"]["requirement_coverage_matrix"], child_event


def test_strict_refs_derives_enforcement_switches(tmp_path: Path):
    """policy 字段执法化(2026-07-08 ⑤ 续):evidencePolicy: strict_refs 从
    advisory 旋钮升为执法开关驱动源——未显式配置时派生 blocking +
    fail_closed;显式 verification.*(含 profile 合并)优先,是逃生门。"""
    def _cfg(extra: dict) -> object:
        data = {
            "project": {"name": "t", "state_dir": str(tmp_path / ".zf")},
            "roles": [{"name": "dev", "backend": "mock"}],
            "workflow": {"_flow_metadata": {"evidence_policy": "strict_refs"}},
            **extra,
        }
        path = tmp_path / "zf.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return load_config(path)

    derived = _cfg({})
    assert derived.verification.event_schema.mode == "blocking"
    assert derived.verification.report_evidence_gate == "fail_closed"

    explicit = _cfg({"verification": {
        "event_schema": {"mode": "warning"},
        "report_evidence_gate": "signal",
    }})
    assert explicit.verification.event_schema.mode == "warning"
    assert explicit.verification.report_evidence_gate == "signal"


def test_non_strict_evidence_policy_leaves_defaults(tmp_path: Path):
    data = {
        "project": {"name": "t", "state_dir": str(tmp_path / ".zf")},
        "roles": [{"name": "dev", "backend": "mock"}],
        "workflow": {"_flow_metadata": {"evidence_policy": "report_only"}},
    }
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.verification.event_schema.mode == "disabled"
    assert cfg.verification.report_evidence_gate == "signal"


def test_union_required_keys_covers_config_driven_failure_events():
    """R6 (2026-07-10): a stage's failure_event is config-driven — the fanout
    aggregate published lane.stage.failed with a fanout-shaped payload and the
    blocking discriminator swallowed the failure. Kernel emitters forge the
    union of required keys across profiles before publishing."""
    from zf.core.config.schema_profiles import union_required_keys

    keys = union_required_keys("lane.stage.failed")
    for expected in (
        "pipeline_id", "task_id", "lane_id", "stage_slot",
        "attempt_id", "failure_target", "status", "reason",
    ):
        assert expected in keys
    assert union_required_keys("no.such.event") == ()
