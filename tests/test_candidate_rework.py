"""Tests for candidate-level rework self-healing planning."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_rework import (
    candidate_quality_failure_message,
    plan_candidate_rework,
)
from zf.runtime.candidate_rework_generation import (
    current_rework_task_map_replay_candidates,
)
from zf.runtime.candidate_integration import candidate_failure_envelope
from zf.runtime.replan_resynth import (
    build_replan_resynth_event,
    plan_missing_replan_resynth_events,
)


def _ev(etype, payload=None, task_id=None, eid="", corr=""):
    return SimpleNamespace(
        type=etype, payload=payload or {}, task_id=task_id, id=eid, correlation_id=corr
    )


def test_rework_task_map_replay_keeps_only_latest_current_scope() -> None:
    events = [
        _ev(
            "task_map.ready",
            {
                "workflow_run_id": "run-1",
                "pdd_id": "PDD-1",
                "task_map_generation": "gen-1",
                "rework_of": "reject-1",
            },
            eid="ready-old",
        ),
        _ev(
            "task_map.ready",
            {
                "workflow_run_id": "run-1",
                "pdd_id": "PDD-1",
                "task_map_generation": "gen-1",
                "rework_of": "reject-1",
            },
            eid="ready-duplicate",
        ),
        _ev(
            "task_map.amended",
            {"supersedes_task_map_generation": "gen-1"},
            eid="amended",
        ),
        _ev(
            "task_map.ready",
            {
                "workflow_run_id": "run-1",
                "pdd_id": "PDD-1",
                "task_map_generation": "gen-2",
                "rework_of": "reject-2",
            },
            eid="ready-current",
        ),
        _ev(
            "task_map.ready",
            {
                "workflow_run_id": "run-1",
                "pdd_id": "PDD-2",
                "task_map_generation": "other-gen",
                "rework_of": "reject-3",
            },
            eid="ready-other-pdd",
        ),
    ]

    selected = current_rework_task_map_replay_candidates(events)

    assert [event.id for event in selected] == [
        "ready-current",
        "ready-other-pdd",
    ]


def test_rework_task_map_replay_deduplicates_same_current_generation() -> None:
    events = [
        _ev(
            "task_map.ready",
            {
                "workflow_run_id": "run-1",
                "pdd_id": "PDD-1",
                "task_map_generation": "gen-current",
                "rework_of": "reject-1",
            },
            eid="ready-first",
        ),
        _ev(
            "task_map.ready",
            {
                "workflow_run_id": "run-1",
                "pdd_id": "PDD-1",
                "task_map_generation": "gen-current",
                "rework_of": "reject-1",
            },
            eid="ready-last",
        ),
    ]

    selected = current_rework_task_map_replay_candidates(events)

    assert [event.id for event in selected] == ["ready-last"]


def test_candidate_rejection_plans_retrigger_with_feedback():
    events = [
        _ev("review.child.failed", {"child_id": "review-contract", "reason": "bad npm metadata", "trace_id": "t1"}, eid="c1"),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1", "trace_id": "t1"}, eid="r1", corr="t1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    p = plans[0]
    assert p.action == "retrigger"
    assert p.pdd_id == "CJMIN-1"          # derived from target_ref tail
    assert p.attempt == 1
    assert p.source_event_id == "r1"
    assert "review-contract: bad npm metadata" in p.feedback


def test_verify_failed_plans_candidate_retrigger_with_feedback():
    events = [
        _ev("verify.child.failed", {
            "child_id": "verify-lane-4-web-tui",
            "reason": "fixture contract mismatch: tool_id missing",
            "trace_id": "t1",
        }, eid="vc1", corr="t1"),
        _ev("verify.failed", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
        }, eid="vf1", corr="t1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    p = plans[0]
    assert p.action == "retrigger"
    assert p.source_event_type == "verify.failed"
    assert p.source_event_id == "vf1"
    assert "contract_fixture_gap" in p.failure_categories
    assert p.rework_summary["source_event_type"] == "verify.failed"


def test_candidate_gate_owner_routes_task_and_downstream_rework():
    candidate = {
        "status": "quality_failed",
        "quality": {
            "status": "failed",
            "gates_failed": ["task_contract:TASK-SCAFFOLD:CMD-PACKAGE"],
            "gate_checks": {
                "task_contract:TASK-SCAFFOLD:CMD-PACKAGE": [{
                    "command": "npm --prefix app test -- --test-name-pattern package",
                    "exit_code": 1,
                    "stdout_tail": "ERR_SERVER_NOT_RUNNING",
                }],
            },
        },
    }
    payload = {
        "pdd_id": "PRD-COMPOSE",
        "trace_id": "trace-compose",
        **candidate_failure_envelope(candidate, failed_children=[]),
    }

    plans = plan_candidate_rework([
        ZfEvent(
            id="integration-compose-failed",
            type="integration.failed",
            correlation_id="trace-compose",
            payload=payload,
        ),
    ])

    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].failed_task_ids == ("TASK-SCAFFOLD",)


def test_unattributed_candidate_cleanliness_failure_replans_without_input_replay():
    """Candidate input task_ids are not failed-task attribution.

    A tracked build-output drift cannot be repaired by rebuilding the same
    candidate or replaying every input slice; planning must assign the missing
    owned path first.
    """
    events = [
        _ev("integration.failed", {
            "pdd_id": "PRD-WEB",
            "trace_id": "trace-web",
            "failure_scope": "candidate",
            "failure_class": "candidate_product_quality_failed",
            "failure_fingerprint": "candidate-cleanliness-1",
            "quality_gates_failed": ["candidate_worktree_clean"],
            "diagnostic_class": "candidate_worktree_dirty",
            "diagnostic_summary": "M web/dist/index.html",
            "task_ids": ["TASK-API", "TASK-WEB"],
            "failed_task_ids": [],
        }, eid="integration-cleanliness", corr="trace-web"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"
    assert plans[0].failed_task_ids == ()
    assert "candidate_worktree_drift" in plans[0].failure_categories


def test_candidate_attempt_ledger_prefers_referenced_event_type():
    events = [
        _ev("integration.failed", {
            "pdd_id": "PRD-SOURCE",
            "trace_id": "trace-source",
            "failure_scope": "candidate",
            "failure_class": "product_test_failed",
            "failed_task_ids": ["TASK-WEB"],
        }, eid="integration-old", corr="trace-source"),
        _ev("orchestrator.replan_requested", {
            "pdd_id": "PRD-SOURCE",
            "rework_of": "integration-old",
            "rework_source": "integration.failed",
        }, eid="replan-old", corr="trace-source"),
        _ev("issue.triage.failed", {
            "pdd_id": "PRD-SOURCE",
            "trace_id": "trace-source",
        }, eid="triage-failed", corr="trace-source"),
        _ev("task_map.ready", {
            "pdd_id": "PRD-SOURCE",
            "rework_of": "triage-failed",
            "rework_source": "integration.failed",
        }, eid="triage-replay", corr="trace-source"),
        _ev("integration.failed", {
            "pdd_id": "PRD-SOURCE",
            "trace_id": "trace-source",
            "failure_scope": "candidate",
            "failure_class": "product_test_failed",
            "failed_task_ids": ["TASK-WEB"],
        }, eid="integration-current", corr="trace-source"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].attempt == 2


def test_candidate_cleanliness_fingerprint_ignores_dirty_path_format():
    config = SimpleNamespace(
        goal=SimpleNamespace(rework_fingerprint=True),
        workflow=SimpleNamespace(rework_routing={}),
    )
    common = {
        "pdd_id": "PRD-CLEAN",
        "trace_id": "trace-clean",
        "failure_scope": "candidate",
        "failure_class": "candidate_product_quality_failed",
        "quality_gates_failed": ["candidate_worktree_clean"],
        "failed_task_ids": [],
    }
    events = [
        _ev("integration.failed", {
            **common,
            "diagnostic_class": "command_failed",
            "diagnostic_summary": "git status --porcelain --untracked-files=all",
        }, eid="clean-old", corr="trace-clean"),
        _ev("orchestrator.replan_requested", {
            "pdd_id": "PRD-CLEAN",
            "rework_of": "clean-old",
            "rework_source": "integration.failed",
        }, eid="clean-replan", corr="trace-clean"),
        _ev("integration.failed", {
            **common,
            "diagnostic_class": "candidate_worktree_dirty",
            "diagnostic_summary": "D web/dist/old.js\n?? web/dist/new.js",
            "failing_command": "git status --porcelain --untracked-files=all",
            "exit_code": 0,
        }, eid="clean-current", corr="trace-clean"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2, config=config)

    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].attempt == 2


def test_admitted_plan_port_dependency_plans_replan_not_dev_retrigger():
    event = ZfEvent(
        id="tf-plan-port",
        type="test.failed",
        actor="zf-cli",
        origin="kernel",
        correlation_id="trace-plan-port",
        payload={
            "pdd_id": "PRD-PLAN-PORT",
            "target_ref": "candidate/PRD-PLAN-PORT",
            "trace_id": "trace-plan-port",
            "failure_class": "dependency_blocked",
            "recovery_action": "replan",
            "rework_scope": "plan_ports",
            "failed_task_ids": ["TASK-1"],
            "semantic_result_refs": [
                "artifacts/call-results/control/result.json",
            ],
        },
    )
    config = SimpleNamespace(
        workflow=SimpleNamespace(
            rework_routing={"test.failed": "dev-lane-0"},
        ),
    )

    plans = plan_candidate_rework([event], config=config)

    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"
    assert plans[0].failed_task_ids == ("TASK-1",)


def test_candidate_lineage_contract_mismatch_replans_without_impl_retry() -> None:
    event = ZfEvent(
        id="integration-lineage",
        type="integration.failed",
        actor="zf-cli",
        origin="kernel",
        correlation_id="trace-lineage",
        payload={
            "pdd_id": "PRD-LINEAGE",
            "target_ref": "candidate/PRD-LINEAGE",
            "trace_id": "trace-lineage",
            "failure_scope": "candidate",
            "failure_class": "candidate_quality_gate_contract_mismatch",
            "diagnostic_class": "candidate_lineage_contract_mismatch",
            "primary_failure_reason": (
                "candidate quality gate requires source-branch commit ancestry "
                "after patch-equivalent candidate integration"
            ),
            "failed_task_ids": ["TASK-RELEASE"],
        },
    )

    plans = plan_candidate_rework([event], max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"
    assert plans[0].failed_task_ids == ("TASK-RELEASE",)


def test_admitted_non_plan_dependency_escalates_without_retry():
    event = ZfEvent(
        id="tf-dependency",
        type="test.failed",
        actor="zf-cli",
        origin="kernel",
        correlation_id="trace-dependency",
        payload={
            "pdd_id": "PRD-DEPENDENCY",
            "target_ref": "candidate/PRD-DEPENDENCY",
            "trace_id": "trace-dependency",
            "failure_class": "dependency_blocked",
            "recovery_action": "escalate",
            "rework_scope": "dependency",
            "semantic_result_refs": [
                "artifacts/call-results/control/result.json",
            ],
        },
    )

    plans = plan_candidate_rework([event])

    assert len(plans) == 1
    assert plans[0].action == "escalate"
    assert plans[0].classification == "dependency_blocked"


def test_upstream_contract_blocker_plans_task_map_replan() -> None:
    events = [
        _ev(
            "dev.blocked",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "blocker_kind": "upstream_contract_gap",
                "blocked_on_task": "SIM-CORE-002",
                "blocked_on_paths": ["app/src/sim/core/simulation.ts"],
                "reason": "meet-and-pass requires off-lane bays in core",
                "evidence_refs": ["git:abc123", "cmd:npm test -- blocker"],
            },
            task_id="SIM-SCHED-003",
            eid="blocked-scheduler",
            corr="trace-sim1",
        ),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    plan = plans[0]
    assert plan.action == "replan"
    assert plan.classification == "design_issue"
    assert plan.failed_task_ids == ("SIM-CORE-002", "SIM-SCHED-003")
    assert plan.source_event_id == "blocked-scheduler"
    assert "off-lane bays" in plan.feedback[0]


def test_contract_blocker_survives_downstream_integration_aggregate() -> None:
    events = [
        _ev(
            "dev.blocked",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "failure_class": "task_contract_unsatisfiable",
                "reason": "the pinned runner and legacy screenshot baseline conflict",
                "evidence_refs": ["event:e2e-passed", "event:pixel-diff"],
            },
            task_id="SIM-CONFIG-001",
            eid="blocked-config",
            corr="trace-sim1",
        ),
        _ev(
            "integration.failed",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "failure_scope": "candidate",
                "failure_class": "candidate_integration_failure",
                "failed_children": ["dev-lane-0-SIM-CONFIG-001"],
            },
            eid="integration-config",
            corr="trace-sim1",
        ),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    plan = plans[0]
    assert plan.action == "replan"
    assert plan.classification == "design_issue"
    assert plan.source_event_id == "integration-config"
    assert plan.failed_task_ids == ("SIM-CONFIG-001",)
    assert "task-contract-blocker SIM-CONFIG-001" in plan.feedback[0]


def test_candidate_dependency_failure_retries_integration_without_task_rework() -> None:
    events = [
        _ev(
            "integration.failed",
            {
                "pdd_id": "CANLAB-1",
                "trace_id": "trace-canlab",
                "fanout_id": "fanout-canlab-impl",
                "failure_scope": "candidate",
                "failure_class": "candidate_dependency_missing",
                "failed_children": [],
                "failed_task_ids": ["CANLAB-WU-01", "CANLAB-WU-02"],
                "completed_task_ids": ["CANLAB-WU-01", "CANLAB-WU-02"],
            },
            eid="integration-canlab-dependency",
            corr="trace-canlab",
        ),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].failed_task_ids == ()


def test_new_contract_blocker_does_not_inherit_unrelated_integration_cap() -> None:
    events = [
        _ev(
            "integration.failed",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "findings": [{"message": "old candidate lineage mismatch"}],
            },
            eid="integration-old-1",
            corr="trace-sim1",
        ),
        _ev(
            "orchestrator.replan_requested",
            {
                "pdd_id": "SIM1-1",
                "rework_of": "integration-old-1",
                "rework_source": "integration.failed",
            },
            eid="replan-old-1",
            corr="trace-sim1",
        ),
        _ev(
            "integration.failed",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "findings": [{"message": "old candidate evidence mismatch"}],
            },
            eid="integration-old-2",
            corr="trace-sim1",
        ),
        _ev(
            "orchestrator.replan_requested",
            {
                "pdd_id": "SIM1-1",
                "rework_of": "integration-old-2",
                "rework_source": "integration.failed",
            },
            eid="replan-old-2",
            corr="trace-sim1",
        ),
        _ev(
            "dev.blocked",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "failure_class": "task_contract_unsatisfiable",
                "reason": "digest command conflicts with the immutable sidecar reader",
                "evidence_refs": ["git:checkpoint-current"],
            },
            task_id="SIM-RELEASE-003",
            eid="blocked-current",
            corr="trace-sim1",
        ),
        _ev(
            "integration.failed",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "failure_class": "candidate_integration_failure",
                "findings": [{
                    "task_id": "SIM-RELEASE-003",
                    "message": "digest command conflicts with the immutable sidecar reader",
                }],
            },
            eid="integration-current",
            corr="trace-sim1",
        ),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].source_event_id == "integration-current"
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"
    assert plans[0].attempt == 1


def test_same_contract_blocker_still_honors_attempt_cap() -> None:
    message = "digest command conflicts with the immutable sidecar reader"
    events = []
    for attempt in (1, 2):
        blocker_id = f"blocked-{attempt}"
        failure_id = f"integration-{attempt}"
        events.extend([
            _ev(
                "dev.blocked",
                {
                    "pdd_id": "SIM1-1",
                    "trace_id": "trace-sim1",
                    "failure_class": "task_contract_unsatisfiable",
                    "reason": message,
                    "evidence_refs": [f"git:checkpoint-{attempt}"],
                },
                task_id="SIM-RELEASE-003",
                eid=blocker_id,
                corr="trace-sim1",
            ),
            _ev(
                "integration.failed",
                {
                    "pdd_id": "SIM1-1",
                    "trace_id": "trace-sim1",
                    "failure_class": "candidate_integration_failure",
                    "findings": [{"task_id": "SIM-RELEASE-003", "message": message}],
                },
                eid=failure_id,
                corr="trace-sim1",
            ),
            _ev(
                "orchestrator.replan_requested",
                {
                    "pdd_id": "SIM1-1",
                    "rework_of": failure_id,
                    "rework_source": "integration.failed",
                },
                eid=f"replan-{attempt}",
                corr="trace-sim1",
            ),
        ])
    events.extend([
        _ev(
            "dev.blocked",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "failure_class": "task_contract_unsatisfiable",
                "reason": message,
                "evidence_refs": ["git:checkpoint-3"],
            },
            task_id="SIM-RELEASE-003",
            eid="blocked-3",
            corr="trace-sim1",
        ),
        _ev(
            "integration.failed",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "failure_class": "candidate_integration_failure",
                "findings": [{"task_id": "SIM-RELEASE-003", "message": message}],
            },
            eid="integration-3",
            corr="trace-sim1",
        ),
    ])

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "escalate"
    assert plans[0].classification == "design_issue"
    assert plans[0].attempt == 3


def test_module_parity_closure_supersedes_task_contract_blocker() -> None:
    events = [
        _ev(
            "dev.blocked",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "failure_class": "task_contract_unsatisfiable",
                "reason": "an earlier candidate failed its screenshot contract",
            },
            task_id="SIM-CONFIG-001",
            eid="blocked-config",
            corr="trace-sim1",
        ),
        _ev(
            "module.parity.closed",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "candidate_ref": "candidate/SIM1-1",
                "target_commit": "c" * 40,
                "open_p0_p1_gap_count": 0,
            },
            eid="parity-closed",
            corr="trace-sim1",
        ),
    ]

    assert plan_candidate_rework(events, max_attempts=2) == []


def test_verify_failed_payload_findings_become_rework_feedback():
    events = [
        _ev("verify.failed", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
            "findings": [
                {
                    "task_id": "CJMIN-GATEWAY-001",
                    "category": "parity_gap",
                    "message": "delivery chunk offsets must be UTF-16 based",
                    "verification_command": "npm test -- gateway",
                },
            ],
        }, eid="vf1", corr="t1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    feedback = plans[0].feedback
    assert feedback == (
        "CJMIN-GATEWAY-001 / parity_gap: "
        "delivery chunk offsets must be UTF-16 based "
        "(verify: npm test -- gateway)",
    )
    assert "parity_gap" in plans[0].failure_categories


def test_verify_failed_parity_gap_preserves_structured_gap_tasks():
    gap_task = {
        "task_id": "CANGJIE-WEB-GAP-001",
        "affinity_tag": "web-tui",
        "owner_role": "dev",
        "allowed_paths": ["web/**", "packages/web-adapter/**"],
        "acceptance_criteria": ["Cangjie WebChat uses Cangjie runtime"],
        "verification": ["npm run test -- web"],
        "source_refs": ["hermes-agent/web"],
    }
    events = [
        _ev("verify.failed", {
            "target_ref": "cand/CANGJIE-1",
            "trace_id": "t1",
            "findings": [
                {
                    "task_id": "CANGJIE-WEB-001",
                    "category": "parity_gap",
                    "message": "Web dashboard still uses a demo bridge",
                    "verification_command": "npm run test -- web",
                    "gap_task": gap_task,
                },
            ],
        }, eid="vf1", corr="t1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    plan = plans[0]
    assert plan.action == "retrigger"
    assert plan.gap_tasks == (gap_task,)
    assert plan.failed_task_ids == ("CANGJIE-WEB-001",)
    assert plan.rework_summary["gap_tasks"] == [gap_task]
    assert "parity_gap" in plan.failure_categories


def test_repeated_verify_contract_gap_replans_to_refresh_contract_freeze():
    events = [
        _ev("verify.failed", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
        }, eid="vf1", corr="t1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "vf1",
            "rework_source": "verify.failed",
            "rework_attempt": 1,
        }, eid="rw1", corr="t1"),
        _ev("verify.child.failed", {
            "child_id": "verify-lane-4-web-tui",
            "reason": (
                "path completion fixture is shape-only; Python reference returns "
                "extra @staged/@url:/@git: items"
            ),
            "trace_id": "t1",
        }, eid="vc2", corr="t1"),
        _ev("verify.failed", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
        }, eid="vf2", corr="t1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    p = plans[0]
    assert p.action == "replan"
    assert p.attempt == 2
    assert p.classification == "contract_freeze_gap"
    assert set(p.failure_categories) >= {"contract_fixture_gap", "parity_gap"}
    assert p.rework_summary["action"] == "replan"


def test_infra_only_verify_failure_does_not_consume_rework_budget():
    events = [
        _ev("fanout.started", {
            "fanout_id": "fanout-verify-real-1",
            "pdd_id": "CJMIN-1",
        }, eid="fs1"),
        _ev("verify.child.failed", {
            "fanout_id": "fanout-verify-real-1",
            "child_id": "verify-lane-4-web-tui",
            "reason": "fixture contract mismatch: tool_id missing",
            "trace_id": "t1",
        }, eid="vc1", corr="t1"),
        _ev("verify.failed", {
            "fanout_id": "fanout-verify-real-1",
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
        }, eid="vf1", corr="t1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "vf1",
            "rework_source": "verify.failed",
            "rework_attempt": 1,
        }, eid="rw1", corr="t1"),
        _ev("fanout.started", {
            "fanout_id": "fanout-verify-infra",
            "pdd_id": "CJMIN-1",
        }, eid="fs2"),
        _ev("fanout.child.failed", {
            "fanout_id": "fanout-verify-infra",
            "child_id": "verify-lane-0-assembly",
            "reason": (
                "refusing to send task to verify-lane-0: pane is not "
                "running an agent process (current_command=bash)"
            ),
            "trace_id": "t2",
        }, eid="ifc1", corr="t2"),
        _ev("verify.failed", {
            "fanout_id": "fanout-verify-infra",
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t2",
        }, eid="vf-infra", corr="t2"),
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CJMIN-1",
            "rework_of": "vf-infra",
            "rework_source": "verify.failed",
            "rework_attempt": 2,
        }, eid="rw-infra", corr="t2"),
        _ev("fanout.started", {
            "fanout_id": "fanout-verify-real-2",
            "pdd_id": "CJMIN-1",
        }, eid="fs3"),
        _ev("verify.child.failed", {
            "fanout_id": "fanout-verify-real-2",
            "child_id": "verify-lane-1-pi-core",
            "reason": "fixture contract mismatch: transcript missing",
            "trace_id": "t3",
        }, eid="vc3", corr="t3"),
        _ev("verify.failed", {
            "fanout_id": "fanout-verify-real-2",
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t3",
        }, eid="vf3", corr="t3"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].source_event_id == "vf3"
    assert plans[0].attempt == 2
    assert plans[0].action == "replan"


def test_legacy_idle_timeout_does_not_plan_candidate_rework():
    events = [
        _ev("fanout.started", {
            "fanout_id": "fanout-candidate-runtime-timeout",
            "pdd_id": "PDD-RUNTIME",
        }, eid="fs-runtime"),
        _ev("fanout.child.failed", {
            "fanout_id": "fanout-candidate-runtime-timeout",
            "child_id": "verify-lane-1",
            "reason": "idle",
            "timeout_seconds": 7200,
            "trace_id": "run-runtime",
        }, eid="fc-runtime", corr="run-runtime"),
        _ev("test.failed", {
            "fanout_id": "fanout-candidate-runtime-timeout",
            "target_ref": "refs/heads/candidate/PDD-RUNTIME",
            "reason": "timeout",
            "timeout_seconds": 7200,
            "trace_id": "run-runtime",
        }, eid="tf-runtime", corr="run-runtime"),
    ]

    assert plan_candidate_rework(events, max_attempts=2) == []


def test_candidate_verify_rejection_uses_current_fanout_and_survives_reemit():
    result = {
        "schema_version": "verification-result.v1",
        "execution_status": "completed",
        "verdict": "rejected",
        "verification_owner": "candidate_verify",
        "task_id": "PRD-AC25",
        "requirement_results": [{
            "acceptance_id": "AC25",
            "status": "failed",
            "findings": [{
                "path": "src/styles/mobility.css",
                "severity": "high",
                "message": "inactive mode still occupies the viewport",
            }],
        }],
        "rework_items": [{
            "rework_item_id": "RW-AC25",
            "required_delta": "hide inactive mode surfaces",
            "done_when": "computed display is none",
        }],
    }
    events = [
        _ev("fanout.started", {
            "fanout_id": "fanout-old",
            "pdd_id": "PRD-AC25",
        }, eid="old-start"),
        _ev("fanout.child.failed", {
            "fanout_id": "fanout-old",
            "child_id": "verify-old",
            "trace_id": "workflow-1",
            "reason": "docker registry unavailable",
        }, eid="old-failed", corr="workflow-1"),
        _ev("fanout.started", {
            "fanout_id": "fanout-current",
            "pdd_id": "PRD-AC25",
        }, eid="current-start"),
        _ev("fanout.child.failed", {
            "fanout_id": "fanout-current",
            "child_id": "verify-current",
            "task_id": "PRD-AC25",
            "parent_task_id": "PRD-AC25",
            "trace_id": "workflow-1",
            "semantic_verdict": "rejected",
            "report": result,
            "reason": "semantic verdict: rejected",
        }, eid="current-failed", corr="workflow-1"),
        _ev("test.failed", {
            "fanout_id": "fanout-current",
            "pdd_id": "PRD-AC25",
            "target_ref": "refs/heads/candidate/PRD-AC25",
            "trace_id": "workflow-1",
        }, eid="test-failed", corr="workflow-1"),
        _ev("candidate.ready", {
            "fanout_id": "fanout-current",
            "pdd_id": "PRD-AC25",
            "candidate_ref": "refs/heads/candidate/PRD-AC25",
            "source": "workflow_resume_batch",
            "rework_of": "aggregate-failed",
            "trace_id": "workflow-1",
        }, eid="bad-reemit", corr="workflow-1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    plan = plans[0]
    assert plan.source_event_id == "test-failed"
    assert plan.failed_task_ids == ()
    assert plan.rework_paths == ("src/styles/mobility.css",)
    assert any("inactive mode still occupies" in line for line in plan.feedback)
    assert all("docker registry unavailable" not in line for line in plan.feedback)


def test_failed_rework_admission_reopens_same_rejection_with_new_checkpoint_seed():
    events = [
        _ev("fanout.started", {
            "fanout_id": "fanout-verify",
            "pdd_id": "PRD-1",
        }, eid="verify-started"),
        _ev("test.failed", {
            "fanout_id": "fanout-verify",
            "pdd_id": "PRD-1",
            "target_ref": "refs/heads/candidate/PRD-1",
            "trace_id": "workflow-1",
            "workflow_run_id": "workflow-1",
            "task_map_generation": "generation-1",
        }, eid="test-failed", corr="workflow-1"),
        _ev("task_map.ready", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
            "workflow_run_id": "workflow-1",
            "task_map_generation": "generation-2",
            "supersedes_task_map_generation": "generation-1",
            "rework_of": "test-failed",
            "rework_source": "test.failed",
            "rework_attempt": 1,
        }, eid="bad-ready", corr="workflow-1"),
        _ev("fanout.cancelled", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
            "trigger_event_id": "bad-ready",
            "failure_scope": "plan_admission",
            "reason": "superseded_task_map",
        }, eid="bad-admission", corr="workflow-1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].source_event_id == "test-failed"
    assert plans[0].attempt == 1
    assert plans[0].continuation_failure_ids == ("bad-admission",)


def test_candidate_rework_checkpoint_bounds_repeated_continuation_failures():
    from types import SimpleNamespace

    from zf.runtime.run_manager import _candidate_rework_checkpoint_id

    base = {
        "pdd_id": "PRD-1",
        "source_event_id": "test-failed",
        "action": "retrigger",
        "attempt": 1,
    }
    first = _candidate_rework_checkpoint_id(SimpleNamespace(
        **base,
        continuation_failure_ids=("admission-1",),
    ))
    second = _candidate_rework_checkpoint_id(SimpleNamespace(
        **base,
        continuation_failure_ids=("admission-1", "admission-2"),
    ))
    third = _candidate_rework_checkpoint_id(SimpleNamespace(
        **base,
        continuation_failure_ids=("admission-1", "admission-2", "admission-3"),
    ))

    assert first != second
    assert second == third


def test_structural_failure_of_latest_continuation_replans_original_rejection():
    events = [
        _ev("test.failed", {
            "fanout_id": "fanout-verify",
            "pdd_id": "PRD-1",
            "target_ref": "refs/heads/candidate/PRD-1",
            "trace_id": "workflow-1",
            "workflow_run_id": "workflow-1",
            "task_map_generation": "generation-1",
        }, eid="test-failed", corr="workflow-1"),
        _ev("task_map.ready", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
            "workflow_run_id": "workflow-1",
            "task_map_generation": "generation-2",
            "rework_of": "test-failed",
            "rework_source": "test.failed",
            "rework_attempt": 1,
        }, eid="first-ready", corr="workflow-1"),
        _ev("task_map.ready", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
            "workflow_run_id": "workflow-1",
            "task_map_generation": "generation-2",
            "rework_of": "test-failed",
            "rework_source": "test.failed",
            "rework_attempt": 1,
        }, eid="bad-ready", corr="workflow-1"),
        _ev("fanout.cancelled", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
            "trigger_event_id": "bad-ready",
            "failure_scope": "plan_admission",
            "reason": (
                "writer fanout task_map has overlapping allowed paths "
                "'src/app/**' and 'src/app/main.ts'"
            ),
        }, eid="bad-admission", corr="workflow-1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].source_event_id == "test-failed"
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"
    assert plans[0].continuation_failure_ids == ("bad-admission",)
    assert any("overlapping allowed paths" in line for line in plans[0].feedback)


def test_successful_marker_after_failed_continuation_handles_rejection():
    events = [
        _ev("test.failed", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
        }, eid="test-failed", corr="workflow-1"),
        _ev("task_map.ready", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
            "rework_of": "test-failed",
        }, eid="bad-ready", corr="workflow-1"),
        _ev("fanout.cancelled", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
            "trigger_event_id": "bad-ready",
            "failure_scope": "plan_admission",
            "reason": "plan artifact package admission failed",
        }, eid="bad-admission", corr="workflow-1"),
        _ev("task_map.ready", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
            "rework_of": "test-failed",
        }, eid="good-ready", corr="workflow-1"),
    ]

    assert plan_candidate_rework(events, max_attempts=2) == []


def test_latest_failed_continuation_outranks_newer_handled_rejection():
    events = [
        _ev("test.failed", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
        }, eid="older-failure", corr="workflow-1"),
        _ev("test.failed", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
        }, eid="newer-failure", corr="workflow-1"),
        _ev("task_map.ready", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
            "rework_of": "newer-failure",
        }, eid="newer-handled", corr="workflow-1"),
        _ev("task_map.ready", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
            "rework_of": "older-failure",
        }, eid="older-bad-ready", corr="workflow-1"),
        _ev("fanout.cancelled", {
            "pdd_id": "PRD-1",
            "trace_id": "workflow-1",
            "trigger_event_id": "older-bad-ready",
            "failure_scope": "plan_admission",
            "reason": (
                "writer fanout task_map has overlapping allowed paths "
                "'src/app/**' and 'src/app/main.ts'"
            ),
        }, eid="older-admission-failed", corr="workflow-1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].source_event_id == "older-failure"
    assert plans[0].action == "replan"


def test_already_handled_rejection_is_skipped():
    events = [
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r1"),
        _ev("task_map.ready", {"pdd_id": "CJMIN-1", "rework_of": "r1", "rework_attempt": 1}),
    ]
    assert plan_candidate_rework(events) == []


def test_escalates_at_max_attempts():
    events = [
        _ev("task_map.ready", {"pdd_id": "CJMIN-1", "rework_of": "rA", "rework_attempt": 1}),
        _ev("task_map.ready", {"pdd_id": "CJMIN-1", "rework_of": "rB", "rework_attempt": 2}),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r3"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "escalate"
    assert plans[0].attempt == 3


def test_duplicate_rework_markers_count_once_for_attempt_budget():
    events = [
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r1",
            "rework_source": "review.rejected",
            "rework_attempt": 1,
        }, eid="rw1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r1",
            "rework_source": "review.rejected",
            "rework_attempt": 1,
        }, eid="rw1-dup"),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r2"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].attempt == 2


def test_same_failed_event_with_repeated_resume_markers_stays_handled():
    events = [
        _ev("verify.failed", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
            "failed_task_ids": ["CJMIN-GATEWAY-001"],
        }, eid="vf1", corr="t1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "vf1",
            "rework_source": "verify.failed",
            "rework_attempt": 1,
            "failed_task_ids": ["CJMIN-GATEWAY-001"],
        }, eid="rw1", corr="t1"),
        _ev("workflow.resume.applied", {
            "checkpoint_id": "ck1",
            "idempotency_key": "ck1",
            "safe_resume_action": "repair_failed_children",
            "source_event_id": "vf1",
        }, eid="wr1", corr="t1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "vf1",
            "rework_source": "verify.failed",
            "rework_attempt": 1,
            "failed_task_ids": ["CJMIN-GATEWAY-001"],
        }, eid="rw1-dup", corr="t1"),
        _ev("workflow.resume.applied", {
            "checkpoint_id": "ck1",
            "idempotency_key": "ck1",
            "safe_resume_action": "repair_failed_children",
            "source_event_id": "vf1",
        }, eid="wr1-dup", corr="t1"),
    ]

    assert plan_candidate_rework(events, max_attempts=2) == []


def test_review_rejected_uses_source_budget_after_runtime_rework_history():
    events = [
        _ev("fanout.child.failed", {
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "reason": "stale_task_map",
            "suggested_action": "use_latest_product_delivery_wave_ready",
        }, eid="stale-1", corr="tr-1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "stale-1",
            "rework_source": "fanout.child.failed",
            "rework_attempt": 1,
        }, eid="rw-stale-1"),
        _ev("integration.failed", {
            "pdd_id": "CJMIN-1",
            "target_ref": "main",
            "trace_id": "tr-1",
        }, eid="int-1", corr="tr-1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "int-1",
            "rework_source": "integration.failed",
            "rework_attempt": 1,
        }, eid="rw-int-1"),
        _ev("review.rejected", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "tr-1",
        }, eid="review-1", corr="tr-1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].source_event_id == "review-1"
    assert plans[0].attempt == 1


def test_escalation_marker_handles_rejection_idempotently():
    events = [
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r1",
            "rework_source": "review.rejected",
            "rework_attempt": 1,
        }, eid="rw1"),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r2"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r2",
            "rework_source": "review.rejected",
            "rework_attempt": 2,
        }, eid="rw2"),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r3"),
        _ev("human.escalate", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r3",
            "rework_source": "review.rejected",
            "rework_attempt": 3,
        }, eid="esc1"),
    ]

    assert plan_candidate_rework(events, max_attempts=2) == []


def test_integration_failure_plans_bounded_rework():
    # integration.failed is candidate-level (task_id=None): the kernel sweep must
    # own its recovery (bounded, same pdd) instead of an agent re-route that
    # mints an incrementing pdd each cycle.
    events = [
        _ev("integration.failed", {"target_ref": "cand/CJMIN-1", "pdd_id": "CJMIN-1"}, eid="i1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].pdd_id == "CJMIN-1"


def test_new_task_map_generation_drops_prior_trace_failure_attribution():
    events = [
        _ev(
            "dev.blocked",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "blocker_kind": "upstream_contract_gap",
                "blocked_on_task": "SIM-CORE-002",
                "reason": "old schema gap in core",
            },
            task_id="SIM-SCHED-003",
            eid="old-blocker",
            corr="trace-sim1",
        ),
        _ev(
            "task_map.ready",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "rework_of": "old-blocker",
                "rework_source": "dev.blocked",
                "rework_attempt": 1,
            },
            eid="new-generation",
            corr="trace-sim1",
        ),
        _ev(
            "integration.failed",
            {
                "pdd_id": "SIM1-1",
                "trace_id": "trace-sim1",
                "status": "quality_failed",
                "findings": [{
                    "category": "candidate_quality",
                    "message": "candidate package.json is missing",
                }],
            },
            eid="integration-failure",
            corr="trace-sim1",
        ),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].source_event_id == "integration-failure"
    assert plans[0].failed_task_ids == ()
    assert plans[0].failure_categories == ()
    assert len(plans[0].feedback) == 1
    assert "candidate package.json is missing" in plans[0].feedback[0]
    assert "old schema gap" not in plans[0].feedback[0]


def test_integration_failure_resolves_pdd_from_fanout_started():
    # Real writer fanout failures report target_ref="main"; that is the branch,
    # not the candidate PDD. Recovery must follow fanout_id back to the started
    # manifest identity so task_map.ready is re-emitted for the original PDD.
    events = [
        _ev("fanout.started", {
            "fanout_id": "fanout-impl-1",
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "target_ref": "main",
        }, eid="fs1", corr="tr-1"),
        _ev("integration.failed", {
            "fanout_id": "fanout-impl-1",
            "target_ref": "main",
            "trace_id": "tr-1",
        }, eid="i1", corr="tr-1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].pdd_id == "CJMIN-1"


def test_wrong_pdd_rework_does_not_mark_integration_failure_handled():
    # A previous bad recovery emitted task_map.ready{pdd_id=main,rework_of=i1}.
    # That must not suppress the corrected rework for the fanout's real PDD.
    events = [
        _ev("fanout.started", {
            "fanout_id": "fanout-impl-1",
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "target_ref": "main",
        }, eid="fs1", corr="tr-1"),
        _ev("integration.failed", {
            "fanout_id": "fanout-impl-1",
            "target_ref": "main",
            "trace_id": "tr-1",
        }, eid="i1", corr="tr-1"),
        _ev("task_map.ready", {
            "pdd_id": "main",
            "rework_of": "i1",
            "rework_attempt": 1,
        }, eid="bad-rework", corr="tr-1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].pdd_id == "CJMIN-1"


def test_stale_task_map_child_failure_plans_retrigger():
    events = [
        _ev("fanout.child.failed", {
            "fanout_id": "fanout-impl-1",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "reason": "stale_task_map",
            "suggested_action": "use_latest_product_delivery_wave_ready",
        }, eid="f1", corr="tr-1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].pdd_id == "CJMIN-1"
    assert plans[0].source_event_type == "fanout.child.failed"
    assert "dev-lane-0-CJMIN-PI-CORE-001: stale_task_map" in plans[0].feedback


def test_latest_handled_rejection_suppresses_older_same_pdd():
    events = [
        _ev("fanout.child.failed", {
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "reason": "stale_task_map",
        }, eid="f1", corr="tr-1"),
        _ev("fanout.child.failed", {
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "reason": "stale_task_map",
        }, eid="f2", corr="tr-1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "f2",
            "rework_attempt": 1,
        }, eid="rw1", corr="tr-1"),
    ]

    assert plan_candidate_rework(events, max_attempts=2) == []


def test_stale_task_map_uses_runtime_recovery_attempt_budget():
    events = [
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r1",
            "rework_source": "review.rejected",
            "rework_attempt": 1,
        }, eid="rw1"),
        _ev("test.failed", {"target_ref": "cand/CJMIN-1"}, eid="t1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "t1",
            "rework_source": "test.failed",
            "rework_attempt": 2,
        }, eid="rw2"),
        _ev("fanout.child.failed", {
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "reason": "stale_task_map",
            "suggested_action": "use_latest_product_delivery_wave_ready",
        }, eid="f1", corr="tr-1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].attempt == 1


def test_task_level_rejection_ignored():
    # rejection WITH a task_id is the per-task path, not candidate-level
    events = [_ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, task_id="T-1", eid="r1")]
    assert plan_candidate_rework(events) == []


def test_candidate_assembly_rejection_with_task_id_is_not_task_local():
    events = [_ev(
        "integration.failed",
        {
            "pdd_id": "CJMIN-1",
            "candidate_ref": "cand/CJMIN-1",
            "verification_owner": "assembly",
            "recovery_action": "replan",
            "status": "failed",
        },
        task_id="CJMIN-ASSEMBLY-001",
        eid="assembly-failed",
    )]

    plans = plan_candidate_rework(events)

    assert len(plans) == 1
    assert plans[0].source_event_id == "assembly-failed"
    assert plans[0].action in {"retrigger", "replan"}


def test_old_candidate_generation_is_audit_only():
    events = [
        _ev(
            "integration.failed",
            {
                "pdd_id": "CJMIN-1",
                "candidate_ref": "cand/CJMIN-1",
                "workflow_run_id": "run-1",
                "task_map_generation": "generation-1",
                "status": "failed",
            },
            task_id="CJMIN-ASSEMBLY-001",
            eid="old-failure",
        ),
        _ev(
            "task_map.ready",
            {
                "pdd_id": "CJMIN-1",
                "workflow_run_id": "run-1",
                "task_map_generation": "generation-2",
                "task_map_ref": "artifacts/task-map-v2.json",
            },
            eid="new-generation",
        ),
    ]

    assert plan_candidate_rework(events) == []


def test_late_failure_from_old_candidate_generation_is_audit_only():
    events = [
        _ev(
            "task_map.ready",
            {
                "pdd_id": "CJMIN-1",
                "workflow_run_id": "run-1",
                "task_map_generation": "generation-2",
                "task_map_ref": "artifacts/task-map-v2.json",
            },
            eid="new-generation",
        ),
        _ev(
            "test.failed",
            {
                "pdd_id": "CJMIN-1",
                "workflow_run_id": "run-1",
                "task_map_generation": "generation-1",
                "status": "failed",
            },
            eid="late-old-failure",
        ),
    ]

    assert plan_candidate_rework(events) == []


def test_multiple_rejections_same_pdd_plan_once():
    # two review.rejected for the same candidate must yield ONE rework,
    # not double-trigger the writer fanout.
    events = [
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r1"),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r2"),
    ]
    plans = plan_candidate_rework(events)
    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].source_event_id == "r2"  # latest rejection


# --- doc 78 W2: plan-level failures plan a replan, not a re-implement ---

def test_plan_rejection_resolves_candidate_from_referenced_task_map():
    events = [
        _ev("task_map.ready", {
            "pdd_id": "TASK-REF-1",
            "trace_id": "run-ref-1",
            "task_map_ref": "artifacts/refactor/task_map.json",
        }, eid="plan-candidate-1", corr="run-ref-1"),
        _ev("plan.rejected", {
            "plan_id": "plan-candidate-1",
            "reason": "task_map_goal_claim_ids_not_in_pinned_claim_set",
            "reason_codes": ["task_map_goal_claim_ids_not_in_pinned_claim_set"],
            "orchestration_delta": {
                "directives": [{
                    "directive_id": "revise-goal-claim-binding",
                    "required_actions": [
                        "Replace dangling goal claim IDs with pinned claim IDs."
                    ],
                }],
            },
        }, eid="plan-rejected-1", corr="run-ref-1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].pdd_id == "TASK-REF-1"
    assert plans[0].source_event_id == "plan-rejected-1"
    assert "plan-rejection-code: task_map_goal_claim_ids_not_in_pinned_claim_set" in plans[0].feedback
    assert (
        "revise-goal-claim-binding: Replace dangling goal claim IDs with pinned claim IDs."
        in plans[0].feedback
    )


def test_integration_conflict_plans_replan_not_retrigger():
    # A cherry-pick conflict is a plan-level (decomposition) error: re-implementing
    # the same task_map repeats it. The plan must re-decompose, not re-implement.
    events = [
        _ev("integration.failed", {
            "target_ref": "cand/CJMIN-1", "pdd_id": "CJMIN-1",
            "status": "conflict", "conflict_files": ["packages/state/db.ts"],
        }, eid="i1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"


def test_design_marker_review_rejection_plans_replan():
    events = [
        _ev("review.rejected", {
            "target_ref": "cand/CJMIN-1",
            "reason": "the architecture/contract decomposition is wrong",
        }, eid="r1"),
    ]
    plans = plan_candidate_rework(events)
    assert plans[0].action == "replan"


def test_candidate_conflict_plans_replan():
    # The actual cherry-pick conflict event (candidate.conflict, task_id=None)
    # must be recovered by the sweep and routed to replan, not ignored.
    events = [
        _ev("candidate.conflict", {
            "target_ref": "cand/CJMIN-1", "pdd_id": "CJMIN-1",
            "status": "conflict", "conflict_files": ["packages/state/db.ts"],
        }, eid="cc1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"


def test_integration_authoritative_verification_scope_gap_plans_replan():
    events = [
        _ev("integration.failed", {
            "target_ref": "main",
            "pdd_id": "CJMIN-R37",
            "trace_id": "tr-r37",
            "findings": [
                {
                    "child_id": "dev-lane-3-CJMIN-PROVIDERS-FUNCTION-CALLING-001",
                    "reason": "authoritative_verification_unrunnable_inside_allowed_scope",
                    "summary": (
                        "Focused provider checks pass, but authoritative "
                        "verification requires workspace/package files outside "
                        "this slice's allowed_paths."
                    ),
                }
            ],
        }, eid="int-scope", corr="tr-r37"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"
    assert plans[0].source_event_type == "integration.failed"


def test_replan_escalates_at_max_attempts():
    # The dangerous loop: replan events advance the attempt counter, so a
    # decomposition that cannot be fixed must ESCALATE at the cap, not emit yet
    # another replan forever. Guards the if/elif precedence (escalate before
    # the replan classification branch).
    events = [
        _ev("orchestrator.replan_requested", {"pdd_id": "CJMIN-1", "rework_of": "x1", "rework_attempt": 1}),
        _ev("orchestrator.replan_requested", {"pdd_id": "CJMIN-1", "rework_of": "x2", "rework_attempt": 2}),
        _ev("candidate.conflict", {"target_ref": "cand/CJMIN-1", "pdd_id": "CJMIN-1", "status": "conflict"}, eid="cc3"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "escalate"
    assert plans[0].attempt == 3


def test_replan_requested_marks_rejection_handled():
    # Once a replan has been requested for a rejection it must not re-fire each
    # tick; orchestrator.replan_requested{rework_of} is a handling marker and
    # advances the attempt counter (so the cap still bounds it).
    events = [
        _ev("integration.failed", {
            "target_ref": "cand/CJMIN-1", "pdd_id": "CJMIN-1", "status": "conflict",
        }, eid="i1"),
        _ev("orchestrator.replan_requested", {"pdd_id": "CJMIN-1", "rework_of": "i1", "rework_attempt": 1}),
    ]
    assert plan_candidate_rework(events) == []


# --- R28 (doc 93 §1/§5): admission/W1 机械拒 → 自动回 synth -------------------

def _replan_config(enabled=True, trigger="zaofu.refactor.review.ready"):
    return SimpleNamespace(
        workflow=SimpleNamespace(
            admission_replan=SimpleNamespace(
                enabled=enabled, resynth_trigger=trigger
            )
        )
    )


def test_multi_flow_replan_uses_the_prd_plan_trigger() -> None:
    config = SimpleNamespace(
        workflow=SimpleNamespace(
            admission_replan=SimpleNamespace(
                enabled=True,
                resynth_trigger="zaofu.refactor.review.ready",
            ),
            stages=[
                SimpleNamespace(
                    id="prd-plan",
                    flow_kind="prd",
                    topology="fanout_reader",
                    trigger="prd.scan.completed",
                    success_event="",
                    aggregate=SimpleNamespace(success_event="task_map.ready"),
                ),
                SimpleNamespace(
                    id="refactor-plan",
                    flow_kind="refactor",
                    topology="fanout_reader",
                    trigger="zaofu.refactor.review.ready",
                    success_event="",
                    aggregate=SimpleNamespace(
                        success_event="zaofu.refactor.plan.ready",
                    ),
                ),
            ],
        ),
    )
    plan = SimpleNamespace(
        pdd_id="PRD-1",
        trace_id="prd-run",
        target_ref="main",
        source_event_id="blocked-1",
        attempt=1,
        source_event_type="dev.blocked",
        feedback=("browser wiring is outside the old slice",),
        failure_categories=(),
        rework_summary={},
        classification="design_issue",
        flow_kind="prd",
    )

    event = build_replan_resynth_event(plan=plan, events=[], config=config)

    assert event is not None
    assert event.type == "prd.scan.completed"
    assert event.payload["flow_kind"] == "prd"


def test_replan_resynth_rebinds_stale_package_to_current_generation() -> None:
    config = SimpleNamespace(
        workflow=SimpleNamespace(
            admission_replan=SimpleNamespace(
                enabled=True,
                resynth_trigger="prd.scan.completed",
            ),
            stages=[],
        ),
    )
    events = [
        _ev(
            "prd.scan.completed",
            {
                "workflow_run_id": "prd-run",
                "trace_id": "prd-run",
                "pdd_id": "PRD-1",
                "task_map_generation": "generation-old",
                "plan_artifact_package_id": "planpkg-old",
                "plan_artifact_package_ref": "artifacts/plan-packages/old.json",
                "plan_artifact_package_digest": "digest-old",
                "goal_claim_set_ref": "artifacts/claims-old.json",
                "goal_claim_set_digest": "claims-old-digest",
            },
            eid="scan-old",
            corr="prd-run",
        ),
        _ev(
            "goal.claim_set.pinned",
            {
                "workflow_run_id": "prd-run",
                "task_map_generation": "generation-current",
                "goal_claim_set_ref": "artifacts/claims-current.json",
                "goal_claim_set_digest": "claims-current-digest",
            },
            eid="claims-current",
            corr="prd-run",
        ),
        _ev(
            "plan.artifact_package.admitted",
            {
                "workflow_run_id": "prd-run",
                "package_slot": "execution_plan",
                "task_map_generation": "generation-current",
                "package_id": "planpkg-current",
                "package_ref": "artifacts/plan-packages/current.json",
                "package_digest": "digest-current",
            },
            eid="package-current",
            corr="prd-run",
        ),
    ]
    plan = SimpleNamespace(
        pdd_id="PRD-1",
        trace_id="prd-run",
        workflow_run_id="prd-run",
        task_map_generation="generation-current",
        plan_artifact_package_id="planpkg-old",
        plan_artifact_package_ref="artifacts/plan-packages/old.json",
        plan_artifact_package_digest="digest-old",
        target_ref="refs/heads/candidate/PRD-1",
        source_event_id="plan-rejected",
        attempt=1,
        source_event_type="plan.rejected",
        feedback=("regenerate plan matrices",),
        failure_categories=(),
        rework_summary={},
        classification="ambiguous",
        flow_kind="prd",
    )

    event = build_replan_resynth_event(plan=plan, events=events, config=config)

    assert event is not None
    assert event.payload["task_map_generation"] == "generation-current"
    assert event.payload["plan_artifact_package_id"] == "planpkg-current"
    assert event.payload["plan_artifact_package_ref"] == (
        "artifacts/plan-packages/current.json"
    )
    assert event.payload["plan_artifact_package_digest"] == "digest-current"
    assert event.payload["goal_claim_set_ref"] == "artifacts/claims-current.json"
    assert event.payload["goal_claim_set_digest"] == "claims-current-digest"


def test_replan_resynth_rebinds_rejected_generation_to_current_package() -> None:
    config = SimpleNamespace(
        workflow=SimpleNamespace(
            admission_replan=SimpleNamespace(
                enabled=True,
                resynth_trigger="prd.scan.completed",
            ),
            stages=[],
        ),
    )
    events = [
        _ev(
            "prd.scan.completed",
            {
                "workflow_run_id": "prd-run",
                "trace_id": "prd-run",
                "pdd_id": "PRD-1",
            },
            eid="scan-old",
            corr="prd-run",
        ),
        _ev(
            "plan.artifact_package.admitted",
            {
                "workflow_run_id": "prd-run",
                "package_slot": "execution_plan",
                "task_map_generation": "generation-rejected",
                "package_id": "planpkg-rejected",
                "package_ref": "artifacts/plan-packages/rejected.json",
                "package_digest": "digest-rejected",
            },
            eid="package-rejected",
            corr="prd-run",
        ),
        _ev(
            "plan.artifact_package.admitted",
            {
                "workflow_run_id": "prd-run",
                "package_slot": "execution_plan",
                "task_map_generation": "generation-current",
                "package_id": "planpkg-current",
                "package_ref": "artifacts/plan-packages/current.json",
                "package_digest": "digest-current",
            },
            eid="package-current",
            corr="prd-run",
        ),
    ]
    plan = SimpleNamespace(
        pdd_id="PRD-1",
        trace_id="prd-run",
        workflow_run_id="prd-run",
        task_map_generation="generation-rejected",
        plan_artifact_package_id="planpkg-rejected",
        plan_artifact_package_ref="artifacts/plan-packages/rejected.json",
        plan_artifact_package_digest="digest-rejected",
        target_ref="refs/heads/candidate/PRD-1",
        source_event_id="plan-rejected",
        attempt=1,
        source_event_type="plan.rejected",
        feedback=("restore mandatory claims",),
        failure_categories=(),
        rework_summary={},
        classification="ambiguous",
        flow_kind="prd",
    )

    event = build_replan_resynth_event(plan=plan, events=events, config=config)

    assert event is not None
    assert event.payload["task_map_generation"] == "generation-current"
    assert event.payload["plan_artifact_package_id"] == "planpkg-current"
    assert event.payload["plan_artifact_package_ref"] == (
        "artifacts/plan-packages/current.json"
    )
    assert event.payload["plan_artifact_package_digest"] == "digest-current"


def test_replan_resynth_prioritizes_latest_plan_rejection_owner_delta() -> None:
    config = SimpleNamespace(
        workflow=SimpleNamespace(
            admission_replan=SimpleNamespace(
                enabled=True,
                resynth_trigger="prd.scan.completed",
            ),
            stages=[],
        ),
    )
    delta = {
        "directives": [{
            "directive_id": "final-two-task-closure",
            "required_actions": [
                "Keep exactly the AC8 receipt and candidate audit tasks.",
                "Preserve the three mandatory inherited Goal claims.",
            ],
        }],
    }
    events = [
        _ev(
            "prd.scan.completed",
            {
                "workflow_run_id": "prd-run",
                "trace_id": "prd-run",
                "pdd_id": "PRD-1",
                "rework_feedback": ["old eight-task feedback"],
            },
            eid="scan-old",
            corr="prd-run",
        ),
        _ev(
            "plan.rejected",
            {
                "plan_id": "plan-old",
                "reason": "Owner-authorized final continuation.",
                "reason_codes": ["mandatory_goal_claim_continuity"],
                "orchestration_delta": delta,
                "operator_override": True,
                "owner_authorization": "continue_until_complete",
            },
            eid="plan-rejected-final",
            corr="prd-run",
        ),
    ]
    plan = SimpleNamespace(
        pdd_id="PRD-1",
        trace_id="prd-run",
        workflow_run_id="prd-run",
        target_ref="refs/heads/candidate/PRD-1",
        source_event_id="plan-rejected-final",
        attempt=1,
        source_event_type="plan.rejected",
        feedback=("old eight-task feedback",),
        failure_categories=(),
        rework_summary={},
        classification="ambiguous",
        flow_kind="prd",
    )

    event = build_replan_resynth_event(plan=plan, events=events, config=config)

    assert event is not None
    assert event.payload["rework_feedback"][0] == (
        "plan-rejection: Owner-authorized final continuation."
    )
    assert event.payload["rework_feedback"][-1] == "old eight-task feedback"
    assert event.payload["required_actions"] == [
        "Keep exactly the AC8 receipt and candidate audit tasks.",
        "Preserve the three mandatory inherited Goal claims.",
    ]
    assert event.payload["orchestration_delta"] == delta
    assert event.payload["operator_override"] is True
    assert event.payload["owner_authorization"] == "continue_until_complete"
    assert "plan_artifact_package_id" not in event.payload


def test_single_flow_replan_keeps_configured_resynth_trigger() -> None:
    config = _replan_config()
    plan = SimpleNamespace(
        pdd_id="REFACTOR-1",
        trace_id="refactor-run",
        target_ref="main",
        source_event_id="blocked-1",
        attempt=1,
        source_event_type="dev.blocked",
        feedback=(),
        failure_categories=(),
        rework_summary={},
        classification="design_issue",
        flow_kind="refactor",
    )

    event = build_replan_resynth_event(plan=plan, events=[], config=config)

    assert event is not None
    assert event.type == "zaofu.refactor.review.ready"


def test_replan_followthrough_repairs_a_cross_flow_trigger() -> None:
    config = SimpleNamespace(
        workflow=SimpleNamespace(
            admission_replan=SimpleNamespace(
                enabled=True,
                resynth_trigger="zaofu.refactor.review.ready",
            ),
            stages=[
                SimpleNamespace(
                    flow_kind="prd",
                    topology="fanout_reader",
                    trigger="prd.scan.completed",
                    success_event="",
                    aggregate=SimpleNamespace(success_event="task_map.ready"),
                ),
            ],
        ),
    )
    marker = _ev(
        "orchestrator.replan_requested",
        {
            "workflow_run_id": "prd-run",
            "flow_kind": "prd",
            "pdd_id": "PRD-1",
            "target_ref": "main",
            "rework_of": "blocked-1",
            "rework_attempt": 1,
            "rework_source": "dev.blocked",
            "classification": "design_issue",
        },
        eid="replan-1",
        corr="prd-run",
    )
    wrong_trigger = _ev(
        "zaofu.refactor.review.ready",
        {
            "workflow_run_id": "prd-run",
            "flow_kind": "prd",
            "pdd_id": "PRD-1",
            "rework_of": "blocked-1",
        },
        eid="wrong-1",
        corr="prd-run",
    )

    repairs = plan_missing_replan_resynth_events(
        [marker, wrong_trigger],
        config=config,
    )

    assert [event.type for event in repairs] == ["prd.scan.completed"]
    assert repairs[0].payload["rework_of"] == "blocked-1"


def test_replan_followthrough_carries_causal_human_resolution() -> None:
    config = SimpleNamespace(
        workflow=SimpleNamespace(
            admission_replan=SimpleNamespace(
                enabled=True,
                resynth_trigger="prd.scan.completed",
            ),
            stages=[],
        ),
    )
    resolution = SimpleNamespace(
        type="human.resolved",
        id="human-resolution-1",
        actor="operator",
        task_id="PRD-1",
        correlation_id="prd-run",
        causation_id="escalation-1",
        payload={
            "schema_version": "human-resolution.v1",
            "resolved_event_id": "escalation-1",
            "source_failure_event_id": "plan-rejected-1",
            "action": "start_new_generation",
            "response": "Keep AC24 command-free and the release audit runtime-only.",
            "contract_evidence_refs": ["git:" + "a" * 40],
        },
    )
    marker = SimpleNamespace(
        type="orchestrator.replan_requested",
        id="replan-1",
        actor="operator",
        task_id="PRD-1",
        correlation_id="prd-run",
        causation_id="human-resolution-1",
        payload={
            "workflow_run_id": "prd-run",
            "flow_kind": "prd",
            "pdd_id": "PRD-1",
            "source_commit": "a" * 40,
            "candidate_base_commit": "a" * 40,
            "rework_of": "plan-rejected-1",
            "rework_source": "plan.rejected",
            "rework_attempt": 2,
            "previous_plan_candidate_refs": [{
                "ref": "artifacts/plan/task-map.json",
                "sha256": "b" * 64,
            }],
            "required_actions": ["Preserve the exact owner resolution."],
        },
    )

    repairs = plan_missing_replan_resynth_events(
        [resolution, marker],
        config=config,
    )

    assert len(repairs) == 1
    assert repairs[0].payload["human_resolution"] == {
        "schema_version": "human-resolution.v1",
        "source_event_id": "human-resolution-1",
        "source_ref": "events.jsonl#human-resolution-1",
        "actor": "operator",
        "resolved_event_id": "escalation-1",
        "source_failure_event_id": "plan-rejected-1",
        "action": "start_new_generation",
        "response": "Keep AC24 command-free and the release audit runtime-only.",
        "contract_evidence_refs": ["git:" + "a" * 40],
    }
    assert repairs[0].payload["previous_plan_candidate_refs"] == [{
        "ref": "artifacts/plan/task-map.json",
        "sha256": "b" * 64,
    }]
    assert repairs[0].payload["required_actions"] == [
        "Preserve the exact owner resolution.",
    ]
    assert repairs[0].payload["source_commit"] == "a" * 40
    assert repairs[0].payload["candidate_base_commit"] == "a" * 40


def test_replan_followthrough_does_not_repeat_after_task_map_progress() -> None:
    config = SimpleNamespace(
        workflow=SimpleNamespace(
            admission_replan=SimpleNamespace(
                enabled=True,
                resynth_trigger="zaofu.refactor.review.ready",
            ),
            stages=[
                SimpleNamespace(
                    flow_kind="prd",
                    topology="fanout_reader",
                    trigger="prd.scan.completed",
                    success_event="",
                    aggregate=SimpleNamespace(success_event="task_map.ready"),
                ),
            ],
        ),
    )
    marker = _ev(
        "orchestrator.replan_requested",
        {
            "workflow_run_id": "prd-run",
            "flow_kind": "prd",
            "pdd_id": "PRD-1",
            "rework_of": "blocked-1",
        },
        eid="replan-1",
        corr="prd-run",
    )
    ready = _ev(
        "task_map.ready",
        {
            "workflow_run_id": "prd-run",
            "flow_kind": "prd",
            "pdd_id": "PRD-1",
        },
        eid="ready-1",
        corr="prd-run",
    )

    assert plan_missing_replan_resynth_events(
        [marker, ready],
        config=config,
    ) == []


_ADMISSION_REASON = (
    "lane_pipeline admission rejected task_map: assembly task "
    "'CJMIN-ASSEMBLY-001' is not present in the task_map; no task owns root"
)


def test_admission_cancel_plans_replan_when_enabled():
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1",
            "trace_id": "t1",
            "reason": _ADMISSION_REASON,
        }, eid="cx1", corr="t1"),
    ]
    plans = plan_candidate_rework(events, config=_replan_config())
    assert len(plans) == 1
    p = plans[0]
    assert p.action == "replan"          # 回 synth 重拆,绝不 retrigger 同张被拒 map
    assert p.pdd_id == "CJMIN-1"
    assert p.source_event_id == "cx1"
    assert p.source_event_type == "fanout.cancelled"
    assert any("admission:" in f for f in p.feedback)


def test_admission_cancel_ignored_when_disabled_default():
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1", "trace_id": "t1", "reason": _ADMISSION_REASON,
        }, eid="cx1", corr="t1"),
    ]
    # 缺省(无 config / 开关关)= 现状 no_action,不产 plan(零回归)
    assert plan_candidate_rework(events) == []
    assert plan_candidate_rework(events, config=_replan_config(enabled=False)) == []


def test_canonical_plan_admission_cancel_never_consumes_candidate_budget():
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1",
            "trace_id": "t1",
            "reason": _ADMISSION_REASON,
            "failure_scope": "plan_admission",
            "plan_admission_incident_id": "plan-admission-1",
            "canonical_failure_event_id": "plan-failure-1",
        }, eid="cx1", corr="t1"),
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1",
            "trace_id": "t1",
            "reason": _ADMISSION_REASON,
            "failure_scope": "plan_admission",
            "plan_admission_incident_id": "plan-admission-1",
            "canonical_failure_event_id": "plan-failure-1",
        }, eid="cx2", corr="t1"),
    ]

    assert plan_candidate_rework(events, config=_replan_config()) == []


def test_non_contract_cancel_not_replanned():
    # stale/dedup 类取消不在白名单 —— 即便开关开也不当重拆(否则误把 task 状态
    # 类失败送去 synth)。
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1", "trace_id": "t1", "reason": "stale_task_map",
        }, eid="cx1", corr="t1"),
    ]
    assert plan_candidate_rework(events, config=_replan_config()) == []


def test_w1_overlap_cancel_plans_replan():
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1", "trace_id": "t1",
            "reason": "writer fanout task_map has overlapping allowed paths "
                      "'docs/cj-min/**' and 'docs/cj-min/state-config.md'",
        }, eid="cx1", corr="t1"),
    ]
    plans = plan_candidate_rework(events, config=_replan_config())
    assert len(plans) == 1 and plans[0].action == "replan"


def test_admission_replan_handled_by_marker():
    # orchestrator.replan_requested(rework_of=被拒 cancel)= handled 标记,
    # sweep 不再每 tick 重发。
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1", "trace_id": "t1", "reason": _ADMISSION_REASON,
        }, eid="cx1", corr="t1"),
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CJMIN-1", "rework_of": "cx1",
            "rework_source": "fanout.cancelled", "rework_attempt": 1,
        }, eid="rq1", corr="t1"),
    ]
    assert plan_candidate_rework(events, config=_replan_config()) == []


def test_admission_replan_escalates_at_cap():
    events = [
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CJMIN-1", "rework_of": "a",
            "rework_source": "fanout.cancelled", "rework_attempt": 1,
        }, eid="rq1"),
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CJMIN-1", "rework_of": "b",
            "rework_source": "fanout.cancelled", "rework_attempt": 2,
        }, eid="rq2"),
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1", "trace_id": "t1", "reason": _ADMISSION_REASON,
        }, eid="cx3", corr="t1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2, config=_replan_config())
    assert len(plans) == 1 and plans[0].action == "escalate"


def test_clean_module_parity_closure_supersedes_stale_w1_cancel():
    events = [
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CANGJIE-R3",
            "rework_of": "cx1",
            "rework_source": "fanout.cancelled",
            "rework_attempt": 1,
        }, eid="rq1", corr="trace-r3"),
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CANGJIE-R3",
            "rework_of": "cx2",
            "rework_source": "fanout.cancelled",
            "rework_attempt": 2,
        }, eid="rq2", corr="trace-r3"),
        _ev("fanout.cancelled", {
            "pdd_id": "CANGJIE-R3",
            "trace_id": "trace-r3",
            "candidate_ref": "cand/CANGJIE-R3",
            "reason": (
                "writer fanout task_map has overlapping allowed paths "
                "'packages/core/**' and 'packages/core/src/agent-loop.ts'"
            ),
        }, eid="cx3", corr="trace-r3"),
        _ev("module.parity.scan.completed", {
            "pdd_id": "CANGJIE-R3",
            "trace_id": "trace-r3",
            "candidate_ref": "cand/CANGJIE-R3",
            "open_p0_p1_gap_count": 0,
        }, eid="parity-clean", corr="trace-r3"),
    ]

    assert plan_candidate_rework(
        events,
        max_attempts=2,
        config=_replan_config(),
    ) == []


def test_clean_module_parity_closure_does_not_supersede_other_pdd_cancel():
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CANGJIE-R3",
            "trace_id": "trace-r3",
            "candidate_ref": "cand/CANGJIE-R3",
            "reason": (
                "writer fanout task_map has overlapping allowed paths "
                "'packages/core/**' and 'packages/core/src/agent-loop.ts'"
            ),
        }, eid="cx3", corr="trace-r3"),
        _ev("cangjie.module.parity.scan.completed", {
            "pdd_id": "CANGJIE-R4",
            "trace_id": "trace-r4",
            "candidate_ref": "cand/CANGJIE-R4",
            "open_p0_p1_gap_count": 0,
        }, eid="parity-other", corr="trace-r4"),
    ]

    plans = plan_candidate_rework(
        events,
        max_attempts=2,
        config=_replan_config(),
    )

    assert len(plans) == 1
    assert plans[0].action == "replan"


def test_task_map_validation_cancel_plans_replan():
    # avbs-r4 F5: schema_version/verification-scope 校验拒(reason 取自 r4
    # 归档原文)属"synth 必须重拆",此前不在白名单 → 静默停摆需人工回炉。
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "AVBS-PRD-REBUILD-R4",
            "trace_id": "t1",
            "reason": (
                "writer fanout task_map validation failed: unsupported "
                "schema_version 'canonical-dag/v1'; AVBS-ASSEMBLY-001."
                "verification references path outside allowed_paths/"
                "exclusive_files: '$PWD:/work'"
            ),
        }, eid="cx9", corr="t1"),
    ]
    plans = plan_candidate_rework(events, config=_replan_config())
    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert any("schema_version" in f for f in plans[0].feedback)


def test_worker_rejection_backflows_into_rework_feedback():
    """FIX-11(bizsim r4 F11):worker 拒单(out-of-scope)必须回流 replan
    输入作归因反证——r4 dev-sim-core 拒了被误路由的 five-camera 修复。"""
    events = [
        _ev("dev.failed", {
            "child_id": "dev-sim-core-TASK-1",
            "reason": "The only rework finding is a renderer/e2e failure; "
                       "the implicated surface is not owned by this lane.",
            "trace_id": "t1",
        }, eid="d1", corr="t1"),
        _ev("verify.failed", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
        }, eid="vf1", corr="t1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    joined = " ".join(plans[0].feedback)
    assert "worker-rejection dev-sim-core-TASK-1" in joined
    assert "not owned by this lane" in joined
    assert "attribution_evidence" in str(
        plans[0].rework_summary.get("attribution_contract"),
    )


def test_candidate_quality_failure_message_surfaces_check_output():
    # The rework message must carry the real error (stdout/stderr tail), not just
    # the command — otherwise rework is blind and exhausts its cap into escalate.
    quality = {
        "failure_details": {"static": ["cd app && npm install && npm test"]},
        "gate_checks": {
            "static": [{
                "command": "cd app && npm install && npm test",
                "exit_code": 1,
                "passed": False,
                "stdout_tail": "Error: Cannot find module '../lib/task-store'",
                "stderr_tail": "",
            }],
        },
    }
    message = candidate_quality_failure_message(quality)
    assert "candidate quality gates failed" in message
    assert "Cannot find module '../lib/task-store'" in message
    assert "cd app && npm install && npm test" in message


def test_candidate_quality_failure_message_falls_back_without_output():
    assert candidate_quality_failure_message({}) == "candidate quality gates failed"
    assert (
        candidate_quality_failure_message({"gate_checks": {"static": [{"passed": True}]}})
        == "candidate quality gates failed"
    )


def test_quality_gate_contract_mismatch_replans_without_blind_retry() -> None:
    events = [
        _ev("integration.failed", {
            "pdd_id": "SIM4-PRD",
            "trace_id": "sim4-run",
            "failure_scope": "candidate",
            "failure_class": "candidate_quality_gate_contract_mismatch",
            "failure_fingerprint": "candidate-failure-missing-typecheck",
            "failing_command": "npm --prefix app run typecheck",
            "diagnostic_summary": 'npm error Missing script: "typecheck"',
        }, eid="integration-1", corr="sim4-run"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"
    assert "quality_gate_contract_gap" in plans[0].failure_categories


def test_same_candidate_failure_reaches_cap_despite_volatile_diagnostics() -> None:
    config = SimpleNamespace(
        goal=SimpleNamespace(rework_fingerprint=True),
        workflow=SimpleNamespace(rework_routing={}),
    )

    def failure(number: int) -> object:
        return _ev("integration.failed", {
            "pdd_id": "SIM4-PRD",
            "trace_id": "sim4-run",
            "failure_scope": "candidate",
            "failure_class": "candidate_product_quality_failed",
            "failure_fingerprint": f"legacy-volatile-fingerprint-{number}",
            "findings": [{
                "message": (
                    'npm error Missing script: "typecheck"; log: '
                    f"/home/user/.npm/_logs/2026-07-21T01_02_0{number}_00{number}Z-debug-0.log"
                ),
            }],
        }, eid=f"integration-{number}", corr="sim4-run")

    events = [
        failure(1),
        _ev("workflow.resume.applied", {
            "mode": "candidate_rework_integration_only",
            "pdd_id": "SIM4-PRD",
            "source_event_id": "integration-1",
            "rework_source": "integration.failed",
        }, eid="resume-1", corr="sim4-run"),
        failure(2),
        _ev("workflow.resume.applied", {
            "mode": "candidate_rework_integration_only",
            "pdd_id": "SIM4-PRD",
            "source_event_id": "integration-2",
            "rework_source": "integration.failed",
        }, eid="resume-2", corr="sim4-run"),
        failure(3),
    ]

    plans = plan_candidate_rework(
        events,
        max_attempts=2,
        config=config,
    )

    assert len(plans) == 1
    assert plans[0].action == "escalate"
    assert plans[0].attempt == 3
    assert len(plans[0].feedback) == 1


@pytest.mark.parametrize("verdict", ["blocked", "rejected"])
def test_goal_compat_projection_does_not_duplicate_typed_rework(verdict):
    failure = _ev("judge.failed", {
        "workflow_run_id": "run-1", "pdd_id": "PDD-1",
        "candidate_head_commit": "a" * 40,
        "authority": "compat_projection", "verdict": verdict,
    }, eid="compat-failure", corr="run-1")
    assert plan_candidate_rework([failure]) == []
    # A legacy runtime may already have minted an erroneous rework from it.
    # That projection must not consume a real product rejection's retry budget.
    erroneous = _ev("task_map.ready", {
        "pdd_id": "PDD-1", "rework_of": failure.id,
    }, eid="erroneous-rework", corr="run-1")
    real = _ev("test.failed", {
        "pdd_id": "PDD-1", "target_ref": "candidate/PDD-1",
    }, eid="real-failure", corr="run-1")
    plans = plan_candidate_rework([failure, erroneous, real])
    assert len(plans) == 1
    assert plans[0].source_event_id == real.id
    assert plans[0].attempt == 1
