"""reader stage 失败机械 replan(prod-e2e:prd/issue 两流死端根治)。"""

from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.runtime.fanout import validate_fanout_report
from zf.runtime.fanout_report_failure import project_report_failure
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_failure_replan import (
    STAGE_REPLAN_CAP,
    plan_reader_stage_replan,
)


def _config():
    stage = SimpleNamespace(
        id="issue-triage",
        attempt_domain="plan",
        topology="fanout_reader",
        trigger="issue.requested",
        failure_event="",
        aggregate=SimpleNamespace(failure_event="issue.triage.failed"),
    )
    return SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))


def _failure(reason="task_map rejected", findings=None):
    return ZfEvent(type="issue.triage.failed", payload={
        "reason": reason,
        "trigger_event_id": "evt-origin",
        **({"findings": findings} if findings else {}),
    })


def test_replan_uses_failed_fanout_origin_instead_of_other_task_latest_trigger():
    original = ZfEvent(id="evt-a", type="issue.requested", correlation_id="run", payload={
        "workflow_run_id": "run", "task_id": "A", "candidate_head_commit": "commit-a",
        "target_ref": "commit-a", "request_id": "request-a",
    })
    started = ZfEvent(type="fanout.started", correlation_id="trace-a", payload={
        "fanout_id": "fanout-a", "stage_id": "issue-triage", "trigger_event_id": original.id,
    })
    unrelated = ZfEvent(type="issue.requested", correlation_id="run", payload={
        "workflow_run_id": "run", "task_id": "B", "candidate_head_commit": "commit-b",
        "target_ref": "commit-b", "request_id": "request-b", "other_task_only": True,
    })
    failure = ZfEvent(type="issue.triage.failed", correlation_id="trace-a", payload={
        "workflow_run_id": "run", "fanout_id": "fanout-a", "target_ref": "commit-a",
    })
    result, _ = plan_reader_stage_replan(_config(), [original, started, unrelated, failure], failure)
    assert result is not None
    assert result.payload["task_id"] == "A"
    assert result.payload["candidate_head_commit"] == "commit-a"
    assert result.payload["request_id"] == "request-a"
    assert "other_task_only" not in result.payload


def test_replan_missing_failed_fanout_origin_does_not_borrow_another_task():
    unrelated = ZfEvent(type="issue.requested", payload={"task_id": "B"})
    failure = ZfEvent(type="issue.triage.failed", payload={"fanout_id": "missing"})
    result, reason = plan_reader_stage_replan(_config(), [unrelated, failure], failure)
    assert result is None
    assert reason == "reader_origin_missing"


def test_replan_re_emits_trigger_with_feedback() -> None:
    origin = ZfEvent(type="issue.requested", payload={"issue_ref": "docs/issues/TODO.md"})
    failure = _failure(findings=[{"severity": "high", "message": "root paths unowned"}])
    replan, note = plan_reader_stage_replan(_config(), [origin, failure], failure)
    assert replan is not None and "issue-triage" in note
    assert replan.type == "issue.requested"
    assert replan.payload["issue_ref"] == "docs/issues/TODO.md"
    assert replan.payload["rework_attempt"] == 1
    assert replan.payload["rework_feedback"][0]["message"] == "root paths unowned"
    assert replan.payload["attempt_domain"] == "plan"
    assert replan.causation_id == failure.id


def test_replan_cap_is_scoped_to_current_workflow_run() -> None:
    previous_run = "run-previous"
    current_run = "run-current"
    events = [
        ZfEvent(
            type="issue.requested",
            correlation_id=previous_run,
            payload={
                "workflow_run_id": previous_run,
                "issue_ref": "docs/issues/previous.md",
            },
        ),
        *[
            ZfEvent(
                type="issue.triage.failed",
                correlation_id=previous_run,
                payload={
                    "workflow_run_id": previous_run,
                    "reason": "previous plan rejected",
                },
            )
            for _ in range(STAGE_REPLAN_CAP + 1)
        ],
        ZfEvent(
            type="issue.requested",
            correlation_id=current_run,
            payload={
                "workflow_run_id": current_run,
                "issue_ref": "docs/issues/current.md",
            },
        ),
    ]
    failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id=current_run,
        payload={
            "workflow_run_id": current_run,
            "reason": "current plan rejected once",
        },
    )
    events.append(failure)

    replan, note = plan_reader_stage_replan(_config(), events, failure)

    assert replan is not None
    assert "cap exhausted" not in note
    assert replan.correlation_id == current_run
    assert replan.payload["issue_ref"] == "docs/issues/current.md"
    assert replan.payload["rework_attempt"] == 1


def test_semantic_scan_rejection_requires_changed_canonical_input() -> None:
    stage = SimpleNamespace(
        id="prd-scan",
        topology="fanout_reader",
        trigger="prd.requested",
        failure_event="",
        aggregate=SimpleNamespace(failure_event="prd.scan.failed"),
    )
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))
    origin = ZfEvent(
        type="prd.requested",
        correlation_id="prd-run",
        payload={
            "prd_ref": "channels/ch-prd/prd/r1.json",
            "requirement_spec_digest": "digest-r1",
            "task_contract_digest": "contract-r1",
        },
    )
    failure = ZfEvent(
        type="prd.scan.failed",
        correlation_id="prd-run",
        payload={
            "recommendation": "needs_rework",
            "failure_class": "semantic_rejection",
            "reason": "implementation contract is incomplete",
        },
    )

    replan, note = plan_reader_stage_replan(
        config,
        [origin, failure],
        failure,
    )

    assert replan is None
    assert note == "input_unchanged"


def test_semantic_scan_rejection_replans_after_input_digest_changes() -> None:
    stage = SimpleNamespace(
        id="prd-scan",
        topology="fanout_reader",
        trigger="prd.requested",
        failure_event="",
        aggregate=SimpleNamespace(failure_event="prd.scan.failed"),
    )
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))
    origin = ZfEvent(
        type="prd.requested",
        correlation_id="prd-run",
        payload={
            "prd_ref": "channels/ch-prd/prd/r1.json",
            "requirement_spec_digest": "digest-r1",
        },
    )
    failure = ZfEvent(
        type="prd.scan.failed",
        correlation_id="prd-run",
        payload={
            "recommendation": "needs_rework",
            "failure_class": "semantic_rejection",
            "requirement_spec_digest": "digest-r2",
            "reason": "re-evaluate the revised PRD",
        },
    )

    replan, note = plan_reader_stage_replan(
        config,
        [origin, failure],
        failure,
    )

    assert replan is not None
    assert "prd-scan" in note
    assert replan.payload["requirement_spec_digest"] == "digest-r2"


def test_replan_preserves_compile_gate_diagnostics_for_next_synth() -> None:
    origin = ZfEvent(
        type="issue.requested",
        payload={"issue_ref": "docs/issues/TODO.md"},
    )
    failure = ZfEvent(
        type="issue.triage.failed",
        payload={
            "reason": (
                "plan compile gate failed: allowed_paths overlap: "
                "app/server.mjs"
            ),
            "trigger_event_id": "evt-origin",
            "diagnostics_ref": "artifacts/plan/artifact-gate-diagnostics.json",
            "plan_compile_gate": "failed",
            "artifact_gate": "failed",
            "findings": [{
                "severity": "high",
                "category": "semantic_review",
                "message": "Keep the public API stable.",
            }],
        },
    )

    replan, _ = plan_reader_stage_replan(
        _config(),
        [origin, failure],
        failure,
    )

    assert replan is not None
    assert replan.payload["diagnostics_ref"].endswith(
        "artifact-gate-diagnostics.json"
    )
    assert replan.payload["plan_compile_gate"] == "failed"
    assert replan.payload["artifact_gate"] == "failed"
    assert any(
        finding.get("category") == "semantic_review"
        for finding in replan.payload["rework_feedback"]
    )
    compile_finding = next(
        finding for finding in replan.payload["rework_feedback"]
        if finding.get("category") == "plan_compile_gate"
    )
    assert "app/server.mjs" in compile_finding["message"]
    assert compile_finding["path"].endswith("artifact-gate-diagnostics.json")


def test_contract_critic_finding_reaches_next_plan_rework_context(
    tmp_path,
) -> None:
    validation = validate_fanout_report(
        {
            "child_id": "plan-critic",
            "status": "failed",
            "summary": "Plan contract rejected.",
            "findings": [{
                "severity": "blocker",
                "code": "plan_ports_not_descriptor_array",
                "field": "plan_ports",
                "observed": "plan_ports contains logical-name strings.",
                "expected": "plan_ports contains descriptor objects.",
                "evidence_refs": ["plan-synth-contract.json"],
            }],
            "fix_items": [{
                "task_id": "refactor-plan-synth",
                "acceptance_id": "plan_ports",
                "observed_gap": "The child returned logical-name strings.",
                "required_change": "Return ready plan-port descriptors.",
                "done_when": "Every required logical name is a descriptor.",
                "evidence_refs": ["plan-synth-contract.json"],
            }],
            "recommendation": "reject",
        },
        child_id="plan-critic",
    )
    artifact_payload = {}
    findings = project_report_failure(
        artifact_payload,
        validation.report,
        validation.diagnostics,
    )
    failure = _failure(findings=findings)
    origin = ZfEvent(
        type="issue.requested",
        payload={"issue_ref": "docs/issues/TODO.md"},
    )

    replan, _ = plan_reader_stage_replan(
        _config(),
        [origin, failure],
        failure,
    )

    assert validation.valid is True
    assert artifact_payload["fix_items"][0]["acceptance_id"] == "plan_ports"
    assert "report_diagnostics" not in artifact_payload
    assert replan is not None
    refs = build_plan_handoff_input_refs(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        payload=replan.payload,
        source_event_id=replan.id,
    )
    descriptor = next(
        ref for ref in refs
        if ref["source_id"] == "plan-rework-context"
    )
    context = hydrate_sidecar_ref(tmp_path / ".zf", descriptor).payload
    critic_finding = next(
        finding for finding in context["rework_feedback"]
        if finding.get("code") == "plan_ports_not_descriptor_array"
    )
    assert critic_finding["severity"] == "high"
    assert critic_finding["category"] == "plan_ports_not_descriptor_array"
    assert critic_finding["field"] == "plan_ports"
    assert "logical-name strings" in critic_finding["message"]
    assert "descriptor objects" in critic_finding["message"]
    fix_finding = next(
        finding for finding in context["rework_feedback"]
        if finding.get("source") == "fix_item"
    )
    assert fix_finding["category"] == "plan_ports"
    assert "The child returned logical-name strings." in fix_finding["message"]
    assert "Return ready plan-port descriptors." in fix_finding["message"]
    assert "Every required logical name is a descriptor." in fix_finding["message"]


def test_replan_preserves_plan_admission_incident_identity() -> None:
    origin = ZfEvent(type="issue.requested", payload={"issue_ref": "docs/issues/TODO.md"})
    failure = _failure(findings=[{"severity": "high", "message": "missing root owner"}])
    failure.payload.update({
        "plan_admission_incident_id": "plan-admission-123",
        "task_map_digest": "abc123",
    })

    replan, _ = plan_reader_stage_replan(_config(), [origin, failure], failure)

    assert replan is not None
    assert replan.payload["plan_admission_incident_id"] == "plan-admission-123"
    assert replan.payload["task_map_digest"] == "abc123"


def test_replan_preserves_complete_previous_plan_candidate_refs() -> None:
    candidate_refs = [{
        "ref": "plan-synth/child-results/planner.json",
        "sha256": "a" * 64,
        "kind": "fanout_child_result",
    }]
    origin = ZfEvent(
        type="issue.requested",
        payload={"issue_ref": "docs/issues/TODO.md"},
    )
    failure = _failure(findings=[{
        "severity": "high",
        "message": "task map producers disagree",
    }])
    failure.payload["previous_plan_candidate_refs"] = candidate_refs

    replan, _ = plan_reader_stage_replan(
        _config(),
        [origin, failure],
        failure,
    )

    assert replan is not None
    assert replan.payload["previous_plan_candidate_refs"] == candidate_refs


def test_replan_recovers_plan_package_identity_from_rejected_trigger() -> None:
    origin = ZfEvent(
        type="issue.requested",
        payload={"issue_ref": "docs/issues/TODO.md"},
    )
    rejected = ZfEvent(
        id="evt-rejected-task-map",
        type="task_map.ready",
        payload={
            "plan_artifact_package_id": "planpkg-current",
            "plan_artifact_package_ref": "artifacts/plan-packages/current.json",
            "plan_artifact_package_digest": "package-digest",
        },
    )
    failure = ZfEvent(
        type="issue.triage.failed",
        causation_id=rejected.id,
        payload={
            "reason": "overlapping allowed paths",
            "trigger_event_id": rejected.id,
        },
    )

    replan, _ = plan_reader_stage_replan(
        _config(),
        [origin, rejected, failure],
        failure,
    )

    assert replan is not None
    assert replan.payload["plan_artifact_package_id"] == "planpkg-current"
    assert replan.payload["plan_artifact_package_ref"] == (
        "artifacts/plan-packages/current.json"
    )
    assert replan.payload["plan_artifact_package_digest"] == "package-digest"


def test_replan_replaces_plan_identity_groups_without_mixing_revisions() -> None:
    origin = ZfEvent(
        type="issue.requested",
        payload={
            "issue_ref": "docs/issues/TODO.md",
            "task_map_ref": "artifacts/plan/old-task-map.json",
            "task_map_digest": "old-task-map-digest",
            "plan_artifact_package_id": "planpkg-old",
            "plan_artifact_package_ref": "artifacts/plan-packages/old.json",
            "plan_artifact_package_digest": "old-package-digest",
        },
    )
    rejected = ZfEvent(
        id="evt-current-task-map",
        type="task_map.ready",
        payload={
            "task_map_ref": "artifacts/plan/current-task-map.json",
            "task_map_digest": "stale-event-digest",
            "plan_artifact_package_id": "planpkg-current",
            "plan_artifact_package_ref": "artifacts/plan-packages/current.json",
            "plan_artifact_package_digest": "current-package-digest",
        },
    )
    failure = ZfEvent(
        type="issue.triage.failed",
        causation_id=rejected.id,
        payload={
            "reason": "task map digest mismatch",
            "trigger_event_id": rejected.id,
            "task_map_ref": "artifacts/plan/current-task-map.json",
            "task_map_digest": "current-task-map-digest",
            "plan_artifact_package_id": "planpkg-current",
            "plan_artifact_package_ref": "artifacts/plan-packages/current.json",
            "plan_artifact_package_digest": "current-package-digest",
        },
    )

    replan, _ = plan_reader_stage_replan(
        _config(),
        [origin, rejected, failure],
        failure,
    )

    assert replan is not None
    assert replan.payload["task_map_ref"] == (
        "artifacts/plan/current-task-map.json"
    )
    assert replan.payload["task_map_digest"] == "current-task-map-digest"
    assert replan.payload["plan_artifact_package_id"] == "planpkg-current"
    assert replan.payload["plan_artifact_package_ref"] == (
        "artifacts/plan-packages/current.json"
    )
    assert replan.payload["plan_artifact_package_digest"] == (
        "current-package-digest"
    )

def test_replan_rebinds_rejected_package_to_canonical_current() -> None:
    run_id = "issue-run"
    origin = ZfEvent(
        type="issue.requested",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "issue_ref": "docs/issues/TODO.md",
            "task_map_generation": "generation-rejected",
            "plan_artifact_package_id": "planpkg-rejected",
            "plan_artifact_package_ref": "artifacts/plan-packages/rejected.json",
            "plan_artifact_package_digest": "rejected-digest",
        },
    )
    rejected = ZfEvent(
        id="evt-rejected-task-map",
        type="task_map.ready",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "plan_artifact_package_id": "planpkg-rejected",
            "plan_artifact_package_ref": "artifacts/plan-packages/rejected.json",
            "plan_artifact_package_digest": "rejected-digest",
            "goal_claim_set_ref": "artifacts/claims/rejected.json",
            "goal_claim_set_digest": "rejected-claims-digest",
        },
    )
    current_claims = ZfEvent(
        id="evt-current-claims",
        type="goal.claim_set.pinned",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "task_map_generation": "generation-current",
            "goal_claim_set_ref": "artifacts/claims/current.json",
            "goal_claim_set_digest": "current-claims-digest",
        },
    )
    current = ZfEvent(
        id="evt-current-package",
        type="plan.artifact_package.admitted",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "package_slot": "execution_plan",
            "task_map_generation": "generation-current",
            "package_id": "planpkg-current",
            "package_ref": "artifacts/plan-packages/current.json",
            "package_digest": "current-digest",
        },
    )
    failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id=run_id,
        causation_id=rejected.id,
        payload={
            "workflow_run_id": run_id,
            "reason": "rejected package cannot continue",
            "trigger_event_id": rejected.id,
        },
    )

    replan, _ = plan_reader_stage_replan(
        _config(),
        [origin, rejected, current_claims, current, failure],
        failure,
    )

    assert replan is not None
    assert replan.payload["task_map_generation"] == "generation-current"
    assert replan.payload["plan_artifact_package_id"] == "planpkg-current"
    assert replan.payload["plan_artifact_package_ref"] == (
        "artifacts/plan-packages/current.json"
    )
    assert replan.payload["plan_artifact_package_digest"] == "current-digest"
    assert replan.payload["goal_claim_set_ref"] == "artifacts/claims/current.json"
    assert replan.payload["goal_claim_set_digest"] == "current-claims-digest"


def test_replan_preserves_failure_target_ref_without_origin_trigger() -> None:
    failure = ZfEvent(type="issue.triage.failed", payload={
        "reason": "bad task_map",
        "target_ref": "docs/issues/fix-list.md",
        "source_refs": {"source_ref": "docs/issues/fix-list.md"},
    })

    replan, note = plan_reader_stage_replan(_config(), [failure], failure)

    assert replan is not None and "issue-triage" in note
    assert replan.payload["target_ref"] == "docs/issues/fix-list.md"
    assert replan.payload["issue_ref"] == "docs/issues/fix-list.md"
    assert replan.payload["source_refs"]["source_ref"] == "docs/issues/fix-list.md"


def test_refactor_replan_does_not_promote_objective_path_to_git_target() -> None:
    stage = SimpleNamespace(
        id="flow-scan",
        topology="fanout_reader",
        trigger="refactor.scan.requested",
        failure_event="",
        aggregate=SimpleNamespace(
            failure_event="zaofu.refactor.scan.blocked",
        ),
    )
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))
    origin = ZfEvent(
        type="refactor.scan.requested",
        payload={
            "objective_ref": "docs/intake/refactor.md",
            "source_refs": {"source_ref": "docs/intake/refactor.md"},
        },
    )
    failure = ZfEvent(
        type="zaofu.refactor.scan.blocked",
        payload={"reason": "scan child failed"},
    )

    replan, _ = plan_reader_stage_replan(config, [origin, failure], failure)

    assert replan is not None
    assert "target_ref" not in replan.payload
    assert replan.payload["objective_ref"] == "docs/intake/refactor.md"
    assert replan.payload["source_refs"]["source_ref"] == "docs/intake/refactor.md"


def test_replan_recovers_pattern_bridge_input_lineage_and_synth_summary() -> None:
    rich_trigger = ZfEvent(
        id="evt-rich-trigger",
        type="task.fanout.requested",
        correlation_id="issue-run",
        payload={
            "target_ref": "docs/issues/docker.md",
            "workflow_run_id": "issue-run",
            "workflow_input_manifest_ref": "artifacts/workflow/input.json",
            "source_refs": {
                "acceptance_matrix_ref": "artifacts/workflow/acceptance.json",
                "test_matrix_ref": "artifacts/workflow/test.json",
            },
            "artifact_refs": [{"path": "artifacts/workflow/input.json"}],
        },
    )
    fanout_started = ZfEvent(
        type="fanout.started",
        correlation_id="issue-run",
        payload={
            "fanout_id": "fanout-issue",
            "stage_id": "issue-triage",
            "trace_id": "issue-run",
            "trigger_event_id": rich_trigger.id,
        },
    )
    failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id="issue-run",
        payload={
            "fanout_id": "fanout-issue",
            "target_ref": "docs/issues/docker.md",
            "summary": "plan ports are missing from the child handoff",
        },
    )

    replan, _ = plan_reader_stage_replan(
        _config(),
        [rich_trigger, fanout_started, failure],
        failure,
    )

    assert replan is not None
    assert (
        replan.payload["workflow_input_manifest_ref"]
        == "artifacts/workflow/input.json"
    )
    assert replan.payload["source_refs"]["test_matrix_ref"].endswith(
        "test.json"
    )
    assert replan.payload["artifact_refs"] == [
        {"path": "artifacts/workflow/input.json"}
    ]
    assert replan.payload["rework_feedback"] == [{
        "severity": "high",
        "message": "plan ports are missing from the child handoff",
    }]


def test_idempotent_per_failure_event() -> None:
    origin = ZfEvent(type="issue.requested", payload={})
    failure = _failure()
    already = ZfEvent(type="issue.requested", payload={"rework_attempt": 1},
                      causation_id=failure.id)
    replan, note = plan_reader_stage_replan(
        _config(), [origin, failure, already], failure,
    )
    assert replan is None and note == "already_replanned"


def test_causal_gap_plan_settles_reader_stage_replan() -> None:
    origin = ZfEvent(type="issue.requested", payload={})
    failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id="semantic-replan-child",
        payload={
            "workflow_run_id": "workflow-1",
            "pdd_id": "PDD-1",
            "reason": "task_map rejected",
        },
    )
    gap_plan = ZfEvent(
        type="flow.gap_plan.ready",
        correlation_id="workflow-1",
        causation_id=failure.id,
        payload={
            "trace_id": "workflow-1",
            "pdd_id": "PDD-1",
            "source_event_id": failure.id,
        },
    )

    replan, note = plan_reader_stage_replan(
        _config(), [origin, failure, gap_plan], failure,
    )

    assert replan is None
    assert note == "handled_by_gap_plan"


def test_unrelated_gap_plan_does_not_settle_reader_stage_replan() -> None:
    origin = ZfEvent(type="issue.requested", payload={})
    failure = _failure()
    unrelated = ZfEvent(
        type="flow.gap_plan.ready",
        causation_id="different-failure",
        payload={"source_event_id": "different-failure"},
    )

    replan, note = plan_reader_stage_replan(
        _config(), [origin, failure, unrelated], failure,
    )

    assert replan is not None
    assert "issue-triage" in note


def test_cap_exhausted_escalates() -> None:
    origin = ZfEvent(type="issue.requested", payload={})
    priors = [_failure() for _ in range(STAGE_REPLAN_CAP)]
    failure = _failure()
    replan, note = plan_reader_stage_replan(
        _config(), [origin, *priors, failure], failure,
    )
    assert replan is None and note == "cap_exhausted"


def test_intermediate_plan_success_does_not_reset_rework_lineage() -> None:
    run_id = "run-plan"
    origin = ZfEvent(
        type="issue.requested",
        correlation_id=run_id,
        payload={"issue_ref": "docs/issues/TODO.md"},
    )
    first_failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id=run_id,
        payload={"reason": "critic rejected candidate"},
    )
    first_replan = ZfEvent(
        type="issue.requested",
        correlation_id=run_id,
        causation_id=first_failure.id,
        payload={
            "issue_ref": "docs/issues/TODO.md",
            "rework_of": first_failure.id,
            "rework_attempt": 1,
        },
    )
    intermediate_success = ZfEvent(
        type="task_map.ready",
        correlation_id=run_id,
        payload={"workflow_run_id": run_id},
    )
    writer_failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id=run_id,
        payload={"reason": "writer admission rejected candidate"},
    )

    replan, _ = plan_reader_stage_replan(
        _config(),
        [origin, first_failure, first_replan, intermediate_success, writer_failure],
        writer_failure,
    )

    assert replan is not None
    assert replan.payload["rework_attempt"] == 2

    second_success = ZfEvent(
        type="task_map.ready",
        correlation_id=run_id,
        payload={"workflow_run_id": run_id},
    )
    final_failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id=run_id,
        payload={"reason": "writer admission rejected replacement again"},
    )
    events = [
        origin,
        first_failure,
        first_replan,
        intermediate_success,
        writer_failure,
        replan,
        second_success,
        final_failure,
    ]

    exhausted, note = plan_reader_stage_replan(_config(), events, final_failure)

    assert exhausted is None
    assert note == "cap_exhausted"


def test_cap_ignores_failures_from_other_workflow_runs() -> None:
    old_failures = [
        ZfEvent(
            type="issue.triage.failed",
            correlation_id=f"old-run-{index}",
            payload={"pdd_id": "PDD-1", "reason": "old failure"},
        )
        for index in range(STAGE_REPLAN_CAP)
    ]
    origin = ZfEvent(
        type="issue.requested",
        correlation_id="current-run",
        payload={"pdd_id": "PDD-1", "issue_ref": "docs/issues/current.md"},
    )
    failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id="current-run",
        payload={
            "pdd_id": "PDD-1",
            "reason": "current admission failure",
            "plan_admission_incident_id": "plan-admission-current",
        },
    )

    replan, note = plan_reader_stage_replan(
        _config(),
        [*old_failures, origin, failure],
        failure,
    )

    assert replan is not None and "issue-triage" in note
    assert replan.payload["rework_attempt"] == 1
    assert replan.payload["issue_ref"] == "docs/issues/current.md"
    assert replan.payload["plan_admission_incident_id"] == (
        "plan-admission-current"
    )


def test_same_run_admission_failure_remains_bounded() -> None:
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[SimpleNamespace(
        id="prd-post-verify-discovery",
        topology="fanout_reader",
        trigger="flow.discovery.requested",
        failure_event="",
        aggregate=SimpleNamespace(
            success_event="flow.discovery.completed",
            failure_event="flow.discovery.failed",
        ),
    )]))
    origin = ZfEvent(
        type="flow.discovery.requested",
        correlation_id="current-run",
        payload={"pdd_id": "PDD-1", "target_ref": "partial456"},
    )
    prior = ZfEvent(
        type="flow.discovery.failed",
        correlation_id="current-run",
        payload={"pdd_id": "PDD-1", "reason": "bounded gap discovered"},
    )
    failure = ZfEvent(
        type="flow.discovery.failed",
        correlation_id="current-run",
        payload={
            "pdd_id": "PDD-1",
            "reason": "gap task has 9 acceptance criteria, max is 8",
            "plan_admission_incident_id": "plan-admission-current",
        },
    )

    replan, _ = plan_reader_stage_replan(
        config,
        [origin, prior, failure],
        failure,
    )

    assert replan is not None
    assert replan.type == "flow.discovery.requested"
    assert replan.payload["rework_attempt"] == 2
    assert replan.payload["plan_admission_incident_id"] == (
        "plan-admission-current"
    )
    assert "9 acceptance criteria" in (
        replan.payload["rework_feedback"][0]["message"]
    )


def test_candidate_ready_stops_recursive_plan_replan() -> None:
    origin = ZfEvent(
        type="issue.requested",
        correlation_id="trace-1",
        payload={
            "pdd_id": "PDD-1",
            "issue_ref": "docs/issues/TODO.md",
            "rework_source": "integration.failed",
        },
    )
    integration_failure = ZfEvent(
        type="integration.failed",
        correlation_id="trace-1",
        payload={"pdd_id": "PDD-1"},
    )
    candidate_ready = ZfEvent(
        type="candidate.ready",
        correlation_id="trace-1",
        payload={"pdd_id": "PDD-1", "candidate_head_commit": "abc"},
    )
    failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id="trace-1",
        payload={"pdd_id": "PDD-1", "reason": "planner timed out"},
    )

    replan, note = plan_reader_stage_replan(
        _config(),
        [origin, integration_failure, candidate_ready, failure],
        failure,
    )

    assert replan is None
    assert note == "superseded_by_candidate_ready"


def test_candidate_ready_from_another_run_does_not_stop_replan() -> None:
    origin = ZfEvent(
        type="issue.requested",
        correlation_id="trace-1",
        payload={
            "pdd_id": "PDD-1",
            "issue_ref": "docs/issues/TODO.md",
            "rework_source": "integration.failed",
        },
    )
    other_ready = ZfEvent(
        type="candidate.ready",
        correlation_id="trace-2",
        payload={"pdd_id": "PDD-1"},
    )
    failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id="trace-1",
        payload={"pdd_id": "PDD-1", "reason": "planner timed out"},
    )

    replan, note = plan_reader_stage_replan(
        _config(),
        [origin, other_ready, failure],
        failure,
    )

    assert replan is not None
    assert "issue-triage" in note


def test_unknown_failure_event_ignored() -> None:
    failure = ZfEvent(type="something.else.failed", payload={})
    replan, note = plan_reader_stage_replan(_config(), [failure], failure)
    assert replan is None and note == "no_reader_stage_for_failure"


def test_stage_replan_ignores_failure_from_another_flow_in_same_run() -> None:
    run_id = "prd-run"
    flow_marker = ZfEvent(
        type="task_map.ready",
        correlation_id=run_id,
        payload={"workflow_run_id": run_id, "flow_kind": "prd"},
    )
    failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id=run_id,
        payload={"trace_id": run_id, "reason": "stale issue fanout timed out"},
    )

    replan, note = plan_reader_stage_replan(
        _config(),
        [flow_marker, failure],
        failure,
    )

    assert replan is None
    assert note == "cross_flow_failure"


def test_stage_replan_keeps_failure_from_canonical_flow() -> None:
    run_id = "issue-run"
    flow_marker = ZfEvent(
        type="task_map.ready",
        correlation_id=run_id,
        payload={"workflow_run_id": run_id, "flow_kind": "issue"},
    )
    failure = ZfEvent(
        type="issue.triage.failed",
        correlation_id=run_id,
        payload={"trace_id": run_id, "reason": "current issue fanout timed out"},
    )

    replan, note = plan_reader_stage_replan(
        _config(),
        [flow_marker, failure],
        failure,
    )

    assert replan is not None
    assert "issue-triage" in note


def test_shared_failure_event_selects_scoped_reader_stage() -> None:
    stages = [
        SimpleNamespace(
            id="issue-verify",
            flow_kind="issue",
            topology="fanout_reader",
            trigger="issue.verify.requested",
            aggregate=SimpleNamespace(
                failure_event="test.failed",
                success_event="test.passed",
            ),
        ),
        SimpleNamespace(
            id="prd-verify",
            flow_kind="prd",
            topology="fanout_reader",
            trigger="prd.verify.requested",
            aggregate=SimpleNamespace(
                failure_event="test.failed",
                success_event="test.passed",
            ),
        ),
    ]
    config = SimpleNamespace(workflow=SimpleNamespace(stages=stages))
    origin = ZfEvent(
        type="prd.verify.requested",
        correlation_id="run-prd",
        payload={"workflow_run_id": "run-prd", "flow_kind": "prd"},
    )
    failure = ZfEvent(
        type="test.failed",
        correlation_id="run-prd",
        payload={
            "workflow_run_id": "run-prd",
            "flow_kind": "prd",
            "stage_id": "prd-verify",
            "reason": "acceptance mismatch",
        },
    )

    replan, note = plan_reader_stage_replan(
        config,
        [origin, failure],
        failure,
    )

    assert replan is not None
    assert replan.type == "prd.verify.requested"
    assert note == "replan prd-verify"


def test_shared_failure_event_without_identity_fails_closed() -> None:
    stages = [
        SimpleNamespace(
            id=f"{kind}-verify",
            flow_kind=kind,
            topology="fanout_reader",
            trigger=f"{kind}.verify.requested",
            aggregate=SimpleNamespace(failure_event="test.failed"),
        )
        for kind in ("issue", "prd")
    ]
    config = SimpleNamespace(workflow=SimpleNamespace(stages=stages))
    failure = ZfEvent(type="test.failed", payload={"reason": "unknown flow"})

    replan, note = plan_reader_stage_replan(config, [failure], failure)

    assert replan is None
    assert note == "ambiguous_reader_stage_for_failure"


def test_lane_verify_failure_is_not_generic_reader_stage_replan() -> None:
    """Lane Verify owns its bounded back-edge; reader replan must not race it."""
    stage = SimpleNamespace(
        id="prd-lanes-verify",
        topology="fanout_reader",
        trigger="lane.stage.completed",
        failure_event="",
        aggregate=SimpleNamespace(failure_event="lane.stage.failed"),
        on_fail=SimpleNamespace(
            event="verify.child.failed",
            restart_stage="prd-lanes-impl",
        ),
        assignment=SimpleNamespace(strategy="affinity_stage_slots"),
    )
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))
    failure = ZfEvent(
        type="lane.stage.failed",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "pipeline_id": "prd-lanes",
            "lane_id": "lane0",
            "stage_slot": "verify",
            "reason": "verification rejected",
        },
    )

    replan, note = plan_reader_stage_replan(config, [failure], failure)

    assert replan is None
    assert note == "no_reader_stage_for_failure"


def test_stage_replan_rejects_discriminator_blocked_trigger() -> None:
    from zf.runtime.workflow_resume import WorkflowResumeCheckpoint
    from zf.runtime.workflow_resume_apply import _apply_stage_replan

    failure = _failure()
    checkpoint = WorkflowResumeCheckpoint(
        task_id="TASK-1",
        last_trusted_event_id=failure.id,
        last_completed_stage="issue.triage",
        expected_next_stage="replan:issue-triage",
        expected_next_role="",
        blocking_event_id=failure.id,
        safe_resume_action="needs_stage_replan",
        idempotency_key="stage-replan-rejected",
    )

    class RejectingWriter:
        def __init__(self) -> None:
            self.events: list[ZfEvent] = []

        def append(self, event: ZfEvent) -> ZfEvent:
            if event.type == "issue.requested":
                event = ZfEvent(
                    type="discriminator.failed",
                    payload={
                        "blocked_event_type": "issue.requested",
                        "reason": "schema rejected",
                    },
                )
            self.events.append(event)
            return event

    writer = RejectingWriter()
    result = _apply_stage_replan(
        writer,  # type: ignore[arg-type]
        checkpoint,
        [],
        config=_config(),
        events=[failure],
    )

    assert result.applied is False
    assert "discriminator.failed" in result.reason
    assert not any(event.type == "workflow.resume.applied" for event in writer.events)
    assert any(event.type == "workflow.resume.rejected" for event in writer.events)


def test_layer2_active_reader_stage_failure_replans_in_kernel(tmp_path) -> None:
    from zf.core.config.schema import (
        FanoutAggregateConfig,
        ProjectConfig,
        RoleConfig,
        SessionConfig,
        WorkflowConfig,
        WorkflowStageConfig,
        ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.core.state.session import SessionStore
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import TmuxTransport

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    log = EventLog(state_dir / "events.jsonl")
    origin = ZfEvent(type="prd.scan.completed", actor="zf-cli",
                     payload={"target_ref": "docs/prd/TODO.md"})
    failure = ZfEvent(type="prd.plan.failed", actor="zf-cli",
                      payload={"reason": "bad task_map"})
    log.append(origin)
    log.append(failure)
    cfg = ZfConfig(
        project=ProjectConfig(name="stage-replan"),
        session=SessionConfig(tmux_session="stage-replan"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(name="planner", backend="mock"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="prd-plan",
                trigger="prd.scan.completed",
                topology="fanout_reader",
                aggregate=FanoutAggregateConfig(
                    success_event="task_map.ready",
                    failure_event="prd.plan.failed",
                ),
            ),
        ]),
    )
    orch = Orchestrator(
        state_dir,
        cfg,
        TmuxTransport(TmuxSession(session_name="stage-replan", dry_run=True)),
    )

    orch.run_once(events=[failure])

    replans = [
        event for event in log.read_all()
        if event.type == "prd.scan.completed" and event.causation_id == failure.id
    ]
    assert len(replans) == 1
    assert replans[0].payload["rework_attempt"] == 1


def test_layer2_reader_stage_cap_converges_run_and_parent_task(
    tmp_path,
) -> None:
    from zf.core.config.schema import (
        FanoutAggregateConfig,
        ProjectConfig,
        RoleConfig,
        SessionConfig,
        WorkflowConfig,
        WorkflowStageConfig,
        ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.core.state.session import SessionStore
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import TmuxTransport
    from zf.runtime.workflow_anchor import mark_workflow_managed_task

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    SessionStore(state_dir / "session.yaml").create(
        project_root=str(tmp_path)
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(mark_workflow_managed_task(Task(
        id="TASK-PLAN",
        title="Issue workflow",
        status="in_progress",
    )))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="run.goal.started",
        task_id="TASK-PLAN",
        correlation_id="run-cap",
        payload={"run_id": "run-cap", "objective": "fix issue"},
    ))
    log.append(ZfEvent(
        type="issue.requested",
        task_id="TASK-PLAN",
        correlation_id="run-cap",
        payload={"issue_ref": "docs/issues/TODO.md"},
    ))
    for _ in range(STAGE_REPLAN_CAP):
        log.append(ZfEvent(
            type="issue.triage.failed",
            task_id="TASK-PLAN",
            correlation_id="run-cap",
            payload={"reason": "bad task map"},
        ))
    current = ZfEvent(
        type="issue.triage.failed",
        task_id="TASK-PLAN",
        correlation_id="run-cap",
        payload={"reason": "bad task map again"},
    )
    log.append(current)
    cfg = ZfConfig(
        project=ProjectConfig(name="stage-cap"),
        session=SessionConfig(tmux_session="stage-cap"),
        roles=[RoleConfig(name="planner", backend="mock")],
        workflow=WorkflowConfig(stages=[WorkflowStageConfig(
            id="issue-triage",
            trigger="issue.requested",
            topology="fanout_reader",
            aggregate=FanoutAggregateConfig(
                success_event="task_map.ready",
                failure_event="issue.triage.failed",
            ),
        )]),
    )
    orch = Orchestrator(
        state_dir,
        cfg,
        TmuxTransport(TmuxSession(session_name="stage-cap", dry_run=True)),
    )

    orch.run_once(events=[current])

    events = log.read_all()
    types = [event.type for event in events]
    assert types.index("run.goal.blocked") < types.index("task.status_changed")
    assert types.index("task.status_changed") < types.index(
        "run.manager.autoresearch.requested"
    )
    task = store.get("TASK-PLAN")
    assert task is not None and task.status == "blocked"


def test_workflow_resume_replays_reader_stage_failure_as_stage_replan(
    tmp_path,
) -> None:
    from zf.core.config.schema import (
        FanoutAggregateConfig,
        ProjectConfig,
        SessionConfig,
        WorkflowConfig,
        WorkflowStageConfig,
        ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.workflow_anchor import mark_workflow_fanout_anchor
    from zf.runtime.workflow_resume import (
        apply_workflow_resume,
        build_workflow_resume_projection,
    )

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    anchor = mark_workflow_fanout_anchor(
        Task(id="PRD-WFINT-1", title="PRD flow", status="in_progress"),
        request_id="wfint-1",
        pattern_id="prd-scan",
    )
    store.add(anchor)
    log = EventLog(state_dir / "events.jsonl")
    origin = ZfEvent(type="prd.scan.completed", actor="zf-cli",
                     payload={"target_ref": "docs/prd/TODO.md"})
    failure = ZfEvent(type="prd.plan.failed", actor="zf-cli",
                      payload={"reason": "bad task_map"})
    log.append(origin)
    log.append(failure)
    cfg = ZfConfig(
        project=ProjectConfig(name="stage-replan"),
        session=SessionConfig(tmux_session="stage-replan"),
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="prd-plan",
                trigger="prd.scan.completed",
                topology="fanout_reader",
                aggregate=FanoutAggregateConfig(
                    success_event="task_map.ready",
                    failure_event="prd.plan.failed",
                ),
            ),
        ]),
    )

    projection = build_workflow_resume_projection(state_dir, cfg)
    pending = [
        item for item in projection["checkpoints"]
        if item["safe_resume_action"] != "no_action"
    ]
    assert [item["safe_resume_action"] for item in pending] == [
        "needs_stage_replan",
    ]
    assert pending[0]["task_id"] == "PRD-WFINT-1"

    result = apply_workflow_resume(state_dir, cfg)

    assert result["applied"] == 1
    events = log.read_all()
    assert any(
        event.type == "prd.scan.completed" and event.causation_id == failure.id
        for event in events
    )
    assert any(
        event.type == "workflow.resume.applied"
        and event.payload.get("mode") == "stage_replan_trigger"
        for event in events
    )
    after = build_workflow_resume_projection(state_dir, cfg)
    assert all(
        item["safe_resume_action"] == "no_action"
        for item in after["checkpoints"]
    )


def test_workflow_resume_does_not_broadcast_taskless_events_to_all_tasks() -> None:
    from zf.runtime.workflow_resume import _events_for_task

    explicit = ZfEvent(
        type="lane.stage.completed",
        actor="zf-cli",
        task_id="cli-impl",
        payload={"child_id": "verify-lane-0-cli-impl"},
    )
    taskless_child_name_only = ZfEvent(
        type="lane.stage.completed",
        actor="zf-stall-redispatch",
        payload={"child_id": "verify-lane-0-cli-impl"},
    )
    taskless_exact_payload = ZfEvent(
        type="runtime.attention.needed",
        actor="zf-supervisor",
        payload={"task_id": "cli-tests"},
    )
    taskless_aggregate_payload = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload={"completed_task_ids": ["cli-impl", "cli-tests"]},
    )
    events = [
        explicit,
        taskless_child_name_only,
        taskless_exact_payload,
        taskless_aggregate_payload,
    ]

    assert _events_for_task(events, "cli-impl") == [explicit]
    assert _events_for_task(events, "scaffold") == []
    assert _events_for_task(events, "cli-tests") == [taskless_exact_payload]
