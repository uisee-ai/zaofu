from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.artifact_read_ledger import source_manifest_from_payload
from zf.runtime.plan_synth_handoff import (
    build_plan_candidate_input_refs,
    build_plan_handoff_input_refs,
    build_plan_synth_call_payload,
    render_plan_synth_completion_command,
    render_plan_synth_validation_section,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_execution_card import (
    compact_fanout_stage_context,
    compact_stage_context,
    prepare_fanout_result_guidance,
    prepare_result_file_command,
)


def test_plan_synth_guidance_distinguishes_execution_and_report_status() -> None:
    section = "\n".join(render_plan_synth_validation_section("zf result submit --scratch"))
    assert "top-level `status` is execution status (`completed` or `failed`)" in section
    assert "`report.status` is the semantic verdict (`passed`, `failed`, `blocked`, or `suspended`)" in section
    guidance, semantic_submit, _ = prepare_fanout_result_guidance(
        child_payload={
            "output_profile_id": "plan-synth",
            "semantic_result_submit_mode": "blocking",
            "operation_id": "operation-1",
        },
        has_contract_snapshot=False,
    )
    assert semantic_submit is True
    assert any("Plan Synth status fields are intentionally distinct" in line for line in guidance)


def test_plan_synth_completion_command_round_trips_shell_sensitive_json(
    tmp_path,
) -> None:
    payload = {
        "fanout_id": "fanout-plan",
        "stage_id": "plan",
        "child_id": "synth",
        "operation_id": "wop-plan-synth",
        "result_scratch_ref": "tmp/result-submit/wop-plan-synth/a/result.json",
        "report": {
            "child_id": "synth",
            "plan_md": (
                "Run python3 -c \"from pathlib import Path; "
                "assert Path('app/result.txt').read_bytes() == b'ok\\n'\"."
            ),
        },
    }

    command = render_plan_synth_completion_command(
        cli_command="uv --project /repo run zf",
        actor="plan-critic",
        state_dir=tmp_path / ".zf",
        payload=payload,
    )

    parsed = subprocess.run(
        ["bash", "-n"],
        input=command,
        text=True,
        capture_output=True,
        check=False,
    )
    assert parsed.returncode == 0, parsed.stderr
    argv = shlex.split(command)
    assert argv[-1] == "--scratch"
    scratch = tmp_path / ".zf" / payload["result_scratch_ref"]
    assert json.loads(scratch.read_text(encoding="utf-8")) == payload


def test_result_scratch_is_bounded_and_preserves_agent_edits(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    command, scratch = prepare_result_file_command(
        state_dir=state_dir,
        result_scratch_ref="tmp/result-submit/op-1/a/result.json",
        operation_id="op-1",
        cli_command="zf",
        semantic_template={"summary": "initial"},
    )
    scratch.write_text('{"summary":"agent edit"}\n', encoding="utf-8")

    repeated, repeated_scratch = prepare_result_file_command(
        state_dir=state_dir,
        result_scratch_ref="tmp/result-submit/op-1/a/result.json",
        operation_id="op-1",
        cli_command="zf",
        semantic_template={"summary": "replacement"},
    )

    assert repeated == command
    assert repeated_scratch == scratch
    assert json.loads(scratch.read_text(encoding="utf-8")) == {
        "summary": "agent edit",
    }
    with pytest.raises(ValueError, match="escapes state dir"):
        prepare_result_file_command(
            state_dir=state_dir,
            result_scratch_ref="../outside.json",
            operation_id="op-1",
            cli_command="zf",
            semantic_template={},
        )


def test_compact_stage_context_excludes_copied_semantic_bodies() -> None:
    compact = compact_stage_context({
        "workflow_run_id": "run-1",
        "task_id": "T1",
        "contract_revision": "r2",
        "expected_output": {"schema": "implementation-result.v1"},
        "raw_task": {"acceptance": ["AC-OLD"]},
        "contract_snapshot": {"acceptance_criteria": ["AC-CURRENT"]},
        "instruction": "Implement current contract.",
    })

    assert compact == {
        "workflow_run_id": "run-1",
        "task_id": "T1",
        "contract_revision": "r2",
        "expected_output": {"schema": "implementation-result.v1"},
        "instruction": "Implement current contract.",
    }


def test_compact_fanout_context_omits_elsewhere_pinned_identity() -> None:
    compact = compact_fanout_stage_context({
        "workflow_run_id": "run-1",
        "task_id": "T1",
        "fanout_id": "fanout-1",
        "stage_id": "verify",
        "child_id": "verify-1",
        "run_id": "child-run-1",
        "attempt_id": "attempt-1",
        "operation_id": "operation-1",
        "task_map_generation": "generation-1",
        "goal_claim_set_ref": "artifacts/claims.json",
        "target_commit": "abc123",
    })

    assert compact == {
        "task_map_generation": "generation-1",
        "goal_claim_set_ref": "artifacts/claims.json",
        "target_commit": "abc123",
    }


def test_plan_synth_handoff_pins_requirement_and_rework_context(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    requirement = state_dir / "requirements" / "revision-2.json"
    requirement.parent.mkdir()
    requirement.write_text(
        json.dumps({
            "objective": "Preserve behavior.",
            "acceptance": ["Canonical acceptance text."],
        }),
        encoding="utf-8",
    )
    diagnostics = state_dir / "artifacts" / "plan" / "diagnostics.json"
    diagnostics.parent.mkdir(parents=True)
    diagnostics.write_text(
        json.dumps([
            "allowed_paths overlap: app/server.mjs is owned by two tasks",
        ]),
        encoding="utf-8",
    )

    payload = build_plan_synth_call_payload(
        state_dir=state_dir,
        project_root=project_root,
        manifest={
            "fanout_id": "fanout-replan",
            "trace_id": "run-replan",
            "stage_id": "flow-plan",
            "trigger_event_id": "evt-replan",
            "trigger_payload": {
                "requirement_spec_ref": str(requirement),
                "rework_of": "evt-package-rejected",
                "rework_attempt": 2,
                "rework_source": "plan.artifact_package.rejected",
                "rework_feedback": [
                    "Keep the canonical acceptance text unchanged.",
                ],
                "diagnostics_ref": str(diagnostics),
                "plan_compile_gate": "failed",
                "artifact_gate": "failed",
                "rework_categories": ["goal_claim_identity_drift"],
                "replan_classification": "design_issue",
                "source_commit": "a" * 40,
                "candidate_base_commit": "b" * 40,
                "required_actions": [
                    "Bind candidate verification to the frozen source commit.",
                ],
                "orchestration_delta": {
                    "immutable_completed_baseline": "git:" + "a" * 40,
                },
                "reason_codes": ["candidate_identity_rebound"],
                "operator_override": True,
                "owner_authorization": "continue_until_complete",
            },
        },
        reports=[{
            "child_id": "planner",
            "report": {"status": "passed"},
        }],
        run_id="run-fanout-replan-synth",
        role_instance="plan-critic",
    )

    sources = {
        source["source_id"]: source
        for source in payload["input_refs"]
    }
    assert {
        "requirement",
        "plan-diagnostics",
        "plan-rework-context",
    } <= sources.keys()
    requirement_body = hydrate_sidecar_ref(
        state_dir,
        sources["requirement"],
    ).payload
    assert requirement_body["acceptance"] == ["Canonical acceptance text."]
    diagnostics_body = hydrate_sidecar_ref(
        state_dir,
        sources["plan-diagnostics"],
    ).payload
    assert "app/server.mjs" in diagnostics_body[0]
    rework_body = hydrate_sidecar_ref(
        state_dir,
        sources["plan-rework-context"],
    ).payload
    assert rework_body == {
        "schema_version": "plan-rework-context.v1",
        "rework_of": "evt-package-rejected",
        "rework_attempt": 2,
        "rework_source": "plan.artifact_package.rejected",
        "rework_feedback": [
            "Keep the canonical acceptance text unchanged.",
        ],
        "diagnostics_ref": str(diagnostics),
        "plan_compile_gate": "failed",
        "artifact_gate": "failed",
        "rework_categories": ["goal_claim_identity_drift"],
        "replan_classification": "design_issue",
        "source_commit": "a" * 40,
        "candidate_base_commit": "b" * 40,
        "required_actions": [
            "Bind candidate verification to the frozen source commit.",
        ],
        "orchestration_delta": {
            "immutable_completed_baseline": "git:" + "a" * 40,
        },
        "reason_codes": ["candidate_identity_rebound"],
        "operator_override": True,
        "owner_authorization": "continue_until_complete",
    }


def test_plan_handoff_pins_causal_human_resolution(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    human_resolution = {
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

    refs = build_plan_handoff_input_refs(
        state_dir=state_dir,
        project_root=tmp_path,
        payload={
            "rework_of": "plan-rejected-1",
            "human_resolution": human_resolution,
        },
        source_event_id="replan-1",
    )

    context_ref = next(
        item for item in refs
        if item["source_id"] == "plan-rework-context"
    )
    context = hydrate_sidecar_ref(state_dir, context_ref).payload
    assert context["human_resolution"] == human_resolution


def test_plan_synth_handoff_pins_file_target_as_exact_requirement(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    authority = state_dir / "channels" / "confirmed" / "prd" / "r7.json"
    authority.parent.mkdir(parents=True)
    authority.write_text(
        json.dumps({
            "revision": 7,
            "decision_map": {
                "task_ids": ["T-01", "T-02", "T-03", "T-04"],
            },
        }),
        encoding="utf-8",
    )

    payload = build_plan_synth_call_payload(
        state_dir=state_dir,
        project_root=project_root,
        manifest={
            "fanout_id": "fanout-prd-plan",
            "trace_id": "run-prd-plan",
            "stage_id": "prd-plan",
            "trigger_event_id": "evt-prd-plan",
            "target_ref": str(authority),
        },
        reports=[{
            "child_id": "planner",
            "report": {"status": "passed"},
        }],
        run_id="run-fanout-prd-plan-synth",
        role_instance="prd-plan-critic",
    )

    exact = [
        source for source in payload["input_refs"]
        if source["source_id"] == "requirement"
        and source["artifact_id"] == "r7.json"
    ]
    assert len(exact) == 1
    assert hydrate_sidecar_ref(state_dir, exact[0]).payload["decision_map"] == {
        "task_ids": ["T-01", "T-02", "T-03", "T-04"],
    }
    source_manifest, _ = source_manifest_from_payload(
        state_dir=state_dir,
        project_root=project_root,
        payload=payload,
        workflow_run_id=payload["workflow_run_id"],
        task_id="prd-plan",
        attempt_id="attempt-prd-plan-synth",
        dispatch_id="dispatch-prd-plan-synth",
    )
    authority_sections = [
        section for section in source_manifest["context_sections"]
        if section["source_id"] == "requirement"
        and section["artifact_id"] == "r7.json"
    ]
    assert len(authority_sections) == 1
    assert authority_sections[0]["required"] is True


def test_plan_rework_pins_complete_previous_candidate_refs(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    plan = project_root / "docs" / "plans" / "issue.md"
    task_map = project_root / "artifacts" / "plan" / "task_map.json"
    source_index = project_root / "artifacts" / "plan" / "source-index.json"
    plan.parent.mkdir(parents=True)
    task_map.parent.mkdir(parents=True)
    plan.write_text("# Plan\n", encoding="utf-8")
    task_map.write_text('{"schema_version":"task-map.v1"}\n', encoding="utf-8")
    source_index.write_text(
        '{"schema_version":"source-index.v1"}\n',
        encoding="utf-8",
    )
    admitted = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "call-result-envelope.v1"},
        root="call-results/admitted",
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
    )
    control = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "workflow-read-result.v1"},
        root="call-results/control",
        kind="workflow_read_result",
        schema_version="workflow-read-result.v1",
        created_by="test",
    )
    candidate_refs, _ = build_plan_candidate_input_refs(
        state_dir=state_dir,
        project_root=project_root,
        reports=[{
            "child_id": "planner",
            "admitted_call_result_ref": admitted,
            "control_result_ref": control,
            "report": {
                "plan_artifact_ref": "docs/plans/issue.md",
                "task_map_ref": "artifacts/plan/task_map.json",
                "source_index_ref": "artifacts/plan/source-index.json",
                "artifact_refs": [
                    "docs/plans/issue.md",
                    "artifacts/plan/task_map.json",
                    "artifacts/plan/source-index.json",
                ],
            },
        }],
    )

    refs = build_plan_handoff_input_refs(
        state_dir=state_dir,
        project_root=project_root,
        payload={
            "rework_of": "evt-plan-failed",
            "rework_attempt": 1,
            "previous_plan_candidate_refs": candidate_refs,
        },
        source_event_id="evt-replan",
    )

    previous = [
        item for item in refs
        if item["source_id"].startswith("previous-plan-candidate-")
    ]
    assert len(previous) == len(candidate_refs)
    assert {
        item["kind"] for item in previous
    } >= {
        "fanout_child_result",
        "call_result_envelope",
        "workflow_read_result",
        "fanout_child_artifact",
    }
    context_ref = next(
        item for item in refs
        if item["source_id"] == "plan-rework-context"
    )
    context = hydrate_sidecar_ref(state_dir, context_ref).payload
    assert context["previous_plan_candidate_refs"] == candidate_refs


def test_plan_rework_rejects_previous_candidate_digest_drift(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    artifact = project_root / "candidate.json"
    artifact.write_text('{"version":1}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="candidate digest mismatch"):
        build_plan_handoff_input_refs(
            state_dir=state_dir,
            project_root=project_root,
            payload={
                "rework_of": "evt-plan-failed",
                "previous_plan_candidate_refs": [{
                    "ref": "candidate.json",
                    "sha256": "0" * 64,
                    "kind": "fanout_child_result",
                }],
            },
        )


def test_plan_synth_large_child_body_is_immutable_ref_not_inline(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    sentinel = "PLAN-BODY-SENTINEL-" + ("x" * 150_000)

    payload = build_plan_synth_call_payload(
        state_dir=state_dir,
        project_root=tmp_path,
        manifest={
            "fanout_id": "fanout-large",
            "trace_id": "run-large",
            "stage_id": "plan",
            "trigger_event_id": "evt-large",
        },
        reports=[{
            "child_id": "planner",
            "report": {"status": "passed", "task_map": sentinel},
        }],
        run_id="run-large-synth",
        role_instance="plan-critic",
    )

    serialized = json.dumps(payload, ensure_ascii=False)
    child = next(
        item for item in payload["input_refs"]
        if item["source_id"] == "child-result-planner"
    )
    hydrated = hydrate_sidecar_ref(state_dir, child).payload
    assert "PLAN-BODY-SENTINEL" not in serialized
    assert hydrated["task_map"] == sentinel
    assert child["sha256"]


@pytest.mark.parametrize(
    "descriptor",
    [
        "candidate.json",
        {"sha256": "a" * 64},
        {"ref": "candidate.json"},
    ],
)
def test_plan_rework_rejects_incomplete_previous_candidate_descriptor(
    tmp_path: Path,
    descriptor: object,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    with pytest.raises(ValueError, match="previous Plan candidate descriptor"):
        build_plan_handoff_input_refs(
            state_dir=state_dir,
            project_root=tmp_path,
            payload={
                "rework_of": "evt-plan-failed",
                "previous_plan_candidate_refs": [descriptor],
            },
        )


def test_plan_rework_context_pins_current_task_and_delivery_checkpoint(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="GAP-RELEASE-R5",
        title="Current release successor",
        status="blocked",
        assigned_to="prd-dev-lane-1",
        blocked_reason="rework_triage:environment_issue",
        retry_count=5,
        contract=TaskContract(
            feature_id="TASK-ROOT",
            parent_task_id="GAP-RELEASE-R4",
            source_task_id="GAP-RELEASE-R5",
            source_ref="event:evt-r5",
            source_revision="source-r5",
            contract_revision="contract-r5",
            product_contract_ref="artifacts/TASK-ROOT/r5/task_map.json",
            scope=["scripts/release/**"],
            acceptance_criteria=["All release commands pass."],
            validation={"commands": [{
                "id": "release-gate",
                "command": "python scripts/release/release_gate.py",
            }]},
        ),
    ))
    event_log = EventLog(state_dir / "events.jsonl")
    event_log.append(ZfEvent(
        type="dev.build.done",
        actor="prd-dev-lane-1",
        task_id="GAP-RELEASE-R5",
        correlation_id="workflow-1",
        payload={
            "workflow_run_id": "workflow-1",
            "source_commit": "9" * 40,
            "target_commit": "9" * 40,
            "task_map_ref": "artifacts/TASK-ROOT/r5/task_map.json",
            "contract_snapshot_ref": "artifacts/contracts/r5.json",
            "contract_snapshot_digest": "a" * 64,
            "impl_self_check": {
                "command_receipts": [
                    {"status": "passed"},
                    {"status": "passed"},
                ],
                "acceptance_results": [
                    {"status": "passed"},
                ],
                "evidence_refs": [f"git:{'9' * 40}"],
                "residual_risks": [],
            },
        },
    ))
    event_log.append(ZfEvent(
        type="dev.failed",
        actor="prd-dev-lane-1",
        task_id="GAP-RELEASE-R5",
        correlation_id="workflow-1",
        payload={
            "workflow_run_id": "workflow-1",
            "failure_class": "contract_scope_base_conflict",
            "reason": "The inherited checkpoint was compared to the wrong base.",
        },
    ))

    payload = build_plan_synth_call_payload(
        state_dir=state_dir,
        project_root=project_root,
        manifest={
            "fanout_id": "fanout-replan",
            "workflow_run_id": "workflow-1",
            "stage_id": "prd-plan",
            "trigger_event_id": "evt-replan",
            "trigger_payload": {
                "pdd_id": "TASK-ROOT",
                "workflow_run_id": "workflow-1",
                "rework_of": "evt-stale-r2",
                "rework_attempt": 1,
                "failed_task_ids": ["GAP-RELEASE-R2"],
                "rework_feedback": [{
                    "severity": "high",
                    "message": "superseded_task_map",
                }],
            },
        },
        reports=[{
            "child_id": "planner",
            "report": {"status": "passed"},
        }],
        run_id="run-fanout-replan-synth",
        role_instance="plan-critic",
    )

    sources = {
        source["source_id"]: source
        for source in payload["input_refs"]
    }
    rework_body = hydrate_sidecar_ref(
        state_dir,
        sources["plan-rework-context"],
    ).payload
    snapshot = rework_body["canonical_task_snapshot"]
    assert snapshot["feature_id"] == "TASK-ROOT"
    assert snapshot["workflow_run_id"] == "workflow-1"
    assert len(snapshot["tasks"]) == 1
    current = snapshot["tasks"][0]
    assert current["task_id"] == "GAP-RELEASE-R5"
    assert current["status"] == "blocked"
    assert current["contract"]["contract_revision"] == "contract-r5"
    assert current["contract"]["product_contract_ref"].endswith("r5/task_map.json")
    assert current["latest_delivery_fact"] == {
        "event_id": current["latest_delivery_fact"]["event_id"],
        "event_type": "dev.build.done",
        "timestamp": current["latest_delivery_fact"]["timestamp"],
        "source_commit": "9" * 40,
        "target_commit": "9" * 40,
        "task_map_ref": "artifacts/TASK-ROOT/r5/task_map.json",
        "contract_snapshot_ref": "artifacts/contracts/r5.json",
        "contract_snapshot_digest": "a" * 64,
        "failure_class": "",
        "reason": "",
        "command_receipt_count": 2,
        "passed_acceptance_count": 1,
        "evidence_refs": [f"git:{'9' * 40}"],
        "residual_risks": [],
    }
    assert current["latest_blocking_fact"]["event_type"] == "dev.failed"
    assert current["latest_blocking_fact"]["failure_class"] == (
        "contract_scope_base_conflict"
    )
