"""Immutable input binding for plan-synthesis fanout calls."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_ledger import materialize_attempt_source_ref
from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar


PLAN_SYNTH_PROFILE_ID = "plan-synth"
PLAN_SYNTH_PROFILE_REVISION = "1"
PLAN_SYNTH_RESULT_SCHEMA = "plan-synthesis-result.v1"
PLAN_SYNTH_CONTRACT_SCHEMA = "plan-synth-contract.v1"
_CHILD_ARTIFACT_SCALAR_KEYS = (
    "plan_artifact_ref",
    "plan_ref",
    "task_map_ref",
    "source_index_ref",
    "backlog_ref",
    "prd_ref",
    "spec_ref",
    "research_ref",
    "scan_quality_audit_ref",
    "inventory_ref",
    "source_inventory_ref",
    "capability_matrix_ref",
    "acceptance_matrix_ref",
    "test_matrix_ref",
    "regression_test_matrix_ref",
    "real_e2e_matrix_ref",
    "inventory_coverage_matrix_ref",
    "expected_module_parity_report_paths_ref",
)
_PLAN_SOURCE_CANDIDATES = (
    ("goal-objective", "goal_objective", "objective_ref"),
    ("requirement", "requirement_authority", "target_ref"),
    ("requirement", "requirement_spec", "requirement_spec_ref"),
    ("requirement", "requirement_spec", "requirement_ref"),
    ("requirement", "requirement_spec", "prd_ref"),
    ("review-artifact", "review_artifact", "review_artifact_ref"),
    ("plan-diagnostics", "plan_gate_diagnostics", "diagnostics_ref"),
    ("owner-confirmation", "plan_synth_owner_confirmation", "owner_confirmation_ref"),
    ("workflow-input", "workflow_input_manifest", "workflow_input_manifest_ref"),
    ("workflow-prompt", "workflow_prompt", "workflow_prompt_ref"),
)
_PLAN_REWORK_CONTEXT_KEYS = (
    "rework_of",
    "rework_attempt",
    "rework_source",
    "rework_feedback",
    "diagnostics_ref",
    "plan_compile_gate",
    "artifact_gate",
    "rework_categories",
    "rework_summary",
    "replan_classification",
    "classification",
    "failed_task_ids",
    "task_ids",
    "downstream_task_ids",
    "resume_scope",
    "previous_plan_candidate_refs",
    "owner_confirmation_ref",
    "owner_decision_items",
    "owner_decision_resolution",
    "human_resolution",
    "source_commit",
    "candidate_base_commit",
    "required_actions",
    "orchestration_delta",
    "orchestration_delta_ref",
    "orchestration_delta_digest",
    "reason_codes",
    "operator_override",
    "owner_authorization",
)
_TASK_DELIVERY_FACT_TYPES = frozenset({
    "candidate.ready",
    "dev.build.done",
    "impl.self_check.completed",
    "task.ref.accepted",
    "verify.passed",
})
_TASK_BLOCKING_FACT_TYPES = frozenset({
    "dev.blocked",
    "dev.failed",
    "human.escalate",
    "task.ref.rejected",
    "task.rework.capped",
    "verify.failed",
})


def render_plan_synth_completion_command(
    *,
    cli_command: str,
    actor: str,
    state_dir: Path,
    payload: Mapping[str, Any],
) -> str:
    """Render a compact result-file submit command for plan synthesis."""

    del actor
    from zf.runtime.stage_execution_card import prepare_result_file_command

    command, _ = prepare_result_file_command(
        state_dir=Path(state_dir),
        result_scratch_ref=str(payload.get("result_scratch_ref") or ""),
        operation_id=str(payload.get("operation_id") or ""),
        cli_command=cli_command,
        semantic_template=dict(payload),
    )
    return command


def render_plan_synth_validation_section(
    submit_command: str, *, scratch_path: Path | None = None,
) -> list[str]:
    """Render the bounded schema preflight shown before plan submission."""

    from zf.runtime.stage_execution_card import render_result_validate_command

    scratch_lines = (
        [f"Edit `{scratch_path.resolve()}` in place; preserve the prefilled binding fields.",
         "Use native Edit/Write/apply_patch, not shell redirection or Python/shell heredocs. "
         "With apply_patch use `*** Update File`; never delete, add, move, or recreate this path.", ""]
        if scratch_path is not None else []
    )
    return [
        *scratch_lines,
        "Write your actual review summary and explicitly choose status and recommendation "
        "in both the result and report. The two status fields have different contracts: "
        "top-level `status` is execution status (`completed` or `failed`); "
        "`report.status` is the semantic verdict (`passed`, `failed`, `blocked`, "
        "or `suspended`). For a successful review use top-level `status: completed`, "
        "`report.status: passed`, and `recommendation: approve`. Empty verdict fields "
        "are not a completed review.",
        "",
        "Finding severity is an exact enum: use only `info`, `low`, `medium`, "
        "`high`, or `critical`. Record a non-blocking residual risk as `info` "
        "or `low`; never invent `residual-risk` or another label.",
        "",
        "Before final submission, run this schema preflight once. It does not "
        "consume the submit binding. Repair only its structured diagnostics; "
        "after exit 0, submit immediately:",
        "```bash",
        render_result_validate_command(submit_command),
        "```",
        "",
    ]


def build_plan_handoff_input_refs(
    *,
    state_dir: Path,
    project_root: Path,
    payload: Mapping[str, Any],
    source_event_id: str = "",
) -> list[dict[str, Any]]:
    """Materialize canonical requirement and rework inputs for a plan attempt."""

    state_dir = Path(state_dir)
    project_root = Path(project_root)
    trigger = (
        payload.get("trigger_payload")
        if isinstance(payload.get("trigger_payload"), Mapping)
        else {}
    )
    input_refs: list[dict[str, Any]] = []
    seen_sources: set[tuple[str, str]] = set()
    for source_id, kind, key in _PLAN_SOURCE_CANDIDATES:
        ref = str(payload.get(key) or trigger.get(key) or "").strip()
        if not ref:
            continue
        source = materialize_attempt_source_ref(
            state_dir=state_dir,
            project_root=project_root,
            ref=ref,
            source_id=source_id,
            kind=kind,
        )
        identity = (source_id, str(source.get("sha256") or ""))
        if not source or identity in seen_sources:
            continue
        source.setdefault("allowed_paths", ["$"])
        input_refs.append(source)
        seen_sources.add(identity)

    rework_context = {"schema_version": "plan-rework-context.v1"}
    for key in _PLAN_REWORK_CONTEXT_KEYS:
        value = payload.get(key)
        if value in (None, "", [], {}):
            value = trigger.get(key)
        if value not in (None, "", [], {}):
            rework_context[key] = value
    if len(rework_context) > 1:
        canonical_snapshot = _canonical_rework_task_snapshot(
            state_dir=state_dir,
            payload=payload,
            trigger=trigger,
        )
        if canonical_snapshot:
            rework_context["canonical_task_snapshot"] = canonical_snapshot
    if len(rework_context) > 1:
        source = write_immutable_json_sidecar(
            state_dir,
            rework_context,
            root="plan-synth/rework-contexts",
            kind="plan_rework_context",
            schema_version="plan-rework-context.v1",
            created_by="plan-synth-handoff",
            source_event_id=source_event_id,
        )
        source.update({
            "source_id": "plan-rework-context",
            "artifact_id": "plan-rework-context.json",
            "allowed_paths": ["$"],
        })
        input_refs.append(source)
    previous_refs = payload.get("previous_plan_candidate_refs")
    if previous_refs in (None, []):
        previous_refs = trigger.get("previous_plan_candidate_refs")
    if previous_refs not in (None, []) and not isinstance(previous_refs, list):
        raise ValueError("previous Plan candidate refs must be a list")
    for index, descriptor in enumerate(
        previous_refs if isinstance(previous_refs, list) else [],
        start=1,
    ):
        if not isinstance(descriptor, Mapping):
            raise ValueError(
                f"previous Plan candidate descriptor {index} must be an object"
            )
        ref = str(descriptor.get("ref") or "").strip()
        expected = str(
            descriptor.get("sha256") or descriptor.get("digest") or ""
        ).strip()
        if not ref or not expected:
            raise ValueError(
                "previous Plan candidate descriptor "
                f"{index} requires ref and sha256"
            )
        source = materialize_attempt_source_ref(
            state_dir=state_dir,
            project_root=project_root,
            ref=ref,
            source_id=f"previous-plan-candidate-{index}",
            kind=str(descriptor.get("kind") or "plan_candidate_artifact"),
        )
        actual = str(source.get("sha256") or "").strip()
        if actual != expected:
            raise ValueError(
                "previous Plan candidate digest mismatch for "
                f"{ref!r}: expected {expected}, got {actual or 'missing'}"
            )
        source.update({
            "source_id": f"previous-plan-candidate-{index}",
            "artifact_id": str(
                descriptor.get("artifact_id") or Path(ref).name
            ),
            "allowed_paths": ["$"],
        })
        identity = (
            str(source.get("source_id") or ""),
            str(source.get("sha256") or ""),
        )
        if identity not in seen_sources:
            input_refs.append(source)
            seen_sources.add(identity)
    return input_refs


def _canonical_rework_task_snapshot(
    *,
    state_dir: Path,
    payload: Mapping[str, Any],
    trigger: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind current TaskStore truth to a replan instead of an obsolete task map."""

    feature_id = str(
        payload.get("pdd_id")
        or payload.get("feature_id")
        or trigger.get("pdd_id")
        or trigger.get("feature_id")
        or ""
    ).strip()
    if not feature_id:
        return {}
    try:
        active_tasks = [
            task
            for task in TaskStore(state_dir / "kanban.json").list_all()
            if task.id != feature_id
            and str(task.contract.feature_id or "").strip() == feature_id
        ]
    except (OSError, ValueError, TypeError):
        return {}
    if not active_tasks:
        return {}

    task_ids = {task.id for task in active_tasks}
    workflow_run_id = str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or trigger.get("workflow_run_id")
        or trigger.get("run_id")
        or ""
    ).strip()
    latest_delivery: dict[str, dict[str, Any]] = {}
    latest_blocker: dict[str, dict[str, Any]] = {}
    try:
        events = EventLog(state_dir / "events.jsonl").read_all()
    except (OSError, ValueError, TypeError):
        events = []
    for event in events:
        task_id = str(event.task_id or "").strip()
        if task_id not in task_ids:
            continue
        event_payload = event.payload if isinstance(event.payload, dict) else {}
        event_run_id = str(
            event.correlation_id
            or event_payload.get("workflow_run_id")
            or event_payload.get("run_id")
            or ""
        ).strip()
        if workflow_run_id and event_run_id and event_run_id != workflow_run_id:
            continue
        compact = _compact_task_fact(event)
        if event.type in _TASK_DELIVERY_FACT_TYPES:
            latest_delivery[task_id] = compact
        if event.type in _TASK_BLOCKING_FACT_TYPES:
            latest_blocker[task_id] = compact

    snapshots: list[dict[str, Any]] = []
    for task in sorted(active_tasks, key=lambda item: item.id):
        contract = asdict(task.contract)
        snapshots.append({
            "task_id": task.id,
            "title": task.title,
            "status": task.status,
            "assigned_to": task.assigned_to or "",
            "blocked_reason": task.blocked_reason,
            "retry_count": task.retry_count,
            "active_dispatch_id": task.active_dispatch_id,
            "blocked_by": list(task.blocked_by),
            "contract": contract,
            "latest_delivery_fact": latest_delivery.get(task.id, {}),
            "latest_blocking_fact": latest_blocker.get(task.id, {}),
        })
    return {
        "schema_version": "canonical-task-snapshot.v1",
        "feature_id": feature_id,
        "workflow_run_id": workflow_run_id,
        "tasks": snapshots,
    }


def _compact_task_fact(event: Any) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    self_check = (
        payload.get("impl_self_check")
        if isinstance(payload.get("impl_self_check"), dict)
        else {}
    )
    command_receipts = self_check.get("command_receipts")
    acceptance_results = self_check.get("acceptance_results")
    return {
        "event_id": str(event.id or ""),
        "event_type": str(event.type or ""),
        "timestamp": str(event.ts or ""),
        "source_commit": str(
            payload.get("source_commit")
            or self_check.get("source_commit")
            or ""
        ),
        "target_commit": str(
            payload.get("target_commit")
            or self_check.get("target_commit")
            or ""
        ),
        "task_map_ref": str(payload.get("task_map_ref") or ""),
        "contract_snapshot_ref": str(
            payload.get("contract_snapshot_ref")
            or self_check.get("contract_snapshot_ref")
            or ""
        ),
        "contract_snapshot_digest": str(
            payload.get("contract_snapshot_digest")
            or self_check.get("contract_snapshot_digest")
            or ""
        ),
        "failure_class": str(payload.get("failure_class") or ""),
        "reason": str(payload.get("reason") or ""),
        "command_receipt_count": (
            len(command_receipts) if isinstance(command_receipts, list) else 0
        ),
        "passed_acceptance_count": (
            sum(
                1
                for item in acceptance_results
                if isinstance(item, dict) and item.get("status") == "passed"
            )
            if isinstance(acceptance_results, list)
            else 0
        ),
        "evidence_refs": list(self_check.get("evidence_refs") or []),
        "residual_risks": list(self_check.get("residual_risks") or []),
    }


def build_plan_candidate_input_refs(
    *,
    state_dir: Path,
    project_root: Path,
    reports: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Materialize every immutable producer ref for one Plan candidate."""

    state_dir = Path(state_dir)
    project_root = Path(project_root)
    input_refs: list[dict[str, Any]] = []
    child_bindings: list[dict[str, str]] = []
    for index, report in enumerate(reports, start=1):
        child_id = str(report.get("child_id") or f"child-{index}")
        report_path = str(report.get("report_path") or "")
        if report_path:
            source = materialize_attempt_source_ref(
                state_dir=state_dir,
                project_root=project_root,
                ref=report_path,
                source_id=f"child-result-{child_id}",
                kind="fanout_child_result",
            )
        else:
            body = report.get("report")
            body = dict(body) if isinstance(body, Mapping) else {
                "child_id": child_id,
                "status": str(report.get("status") or "completed"),
            }
            source = write_immutable_json_sidecar(
                state_dir,
                body,
                root="plan-synth/child-results",
                kind="fanout_child_result",
                schema_version="fanout-child-result.v1",
                created_by="plan-synth-handoff",
                source_event_id=str(report.get("result_event_id") or ""),
            )
            source.update({
                "source_id": f"child-result-{child_id}",
                "artifact_id": f"{child_id}.json",
                "allowed_paths": ["$"],
            })
        if not source:
            continue
        source.setdefault("source_id", f"child-result-{child_id}")
        source.setdefault(
            "artifact_id",
            Path(str(source.get("ref") or "result.json")).name,
        )
        source.setdefault("allowed_paths", ["$"])
        input_refs.append(source)
        child_bindings.append({
            "child_id": child_id,
            "result_event_id": str(report.get("result_event_id") or ""),
            "source_id": str(source.get("source_id") or ""),
            "artifact_id": str(source.get("artifact_id") or ""),
            "sha256": str(source.get("sha256") or ""),
        })
        from zf.runtime.result_handoff_sources import (
            result_handoff_source_entries,
        )

        for result_source in result_handoff_source_entries(report):
            result_source = dict(result_source)
            result_source["source_id"] = (
                f"child-{child_id}-{result_source['source_id']}"
            )
            input_refs.append(result_source)
        body = (
            report.get("report")
            if isinstance(report.get("report"), Mapping)
            else {}
        )
        for artifact_index, ref in enumerate(
            _child_artifact_refs(body),
            start=1,
        ):
            artifact_source = materialize_attempt_source_ref(
                state_dir=state_dir,
                project_root=project_root,
                ref=ref,
                source_id=f"child-artifact-{child_id}-{artifact_index}",
                kind="fanout_child_artifact",
            )
            if not artifact_source:
                continue
            artifact_source.setdefault("allowed_paths", ["$"])
            input_refs.append(artifact_source)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in input_refs:
        identity = (
            str(source.get("ref") or ""),
            str(source.get("sha256") or ""),
        )
        if not all(identity) or identity in seen:
            continue
        seen.add(identity)
        deduped.append(source)
    return deduped, child_bindings


def build_plan_synth_call_payload(
    *,
    state_dir: Path,
    project_root: Path,
    manifest: Mapping[str, Any],
    reports: list[Mapping[str, Any]],
    run_id: str,
    role_instance: str,
) -> dict[str, Any]:
    """Build the immutable input set pinned before a plan synth dispatch."""

    state_dir = Path(state_dir)
    project_root = Path(project_root)
    fanout_id = str(manifest.get("fanout_id") or "")
    stage_id = str(manifest.get("stage_id") or "")
    trigger_event_id = str(manifest.get("trigger_event_id") or "")
    workflow_run_id = str(
        manifest.get("workflow_run_id")
        or manifest.get("trace_id")
        or manifest.get("pdd_id")
        or fanout_id
    )
    input_refs, child_bindings = build_plan_candidate_input_refs(
        state_dir=state_dir,
        project_root=project_root,
        reports=reports,
    )

    seen_sources = {
        (str(item.get("source_id") or ""), str(item.get("sha256") or ""))
        for item in input_refs
    }
    for source in build_plan_handoff_input_refs(
        state_dir=state_dir,
        project_root=project_root,
        payload=manifest,
        source_event_id=trigger_event_id,
    ):
        identity = (
            str(source.get("source_id") or ""),
            str(source.get("sha256") or ""),
        )
        if identity not in seen_sources:
            input_refs.append(source)
            seen_sources.add(identity)

    revision_basis = {
        "schema_version": PLAN_SYNTH_CONTRACT_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "fanout_id": fanout_id,
        "stage_id": stage_id,
        "trigger_event_id": trigger_event_id,
        "target_ref": str(manifest.get("target_ref") or ""),
        "child_bindings": child_bindings,
        "source_bindings": [
            {
                "source_id": str(item.get("source_id") or ""),
                "artifact_id": str(item.get("artifact_id") or ""),
                "ref": str(item.get("ref") or ""),
                "sha256": str(item.get("sha256") or ""),
            }
            for item in input_refs
        ],
    }
    plan_revision = f"plan-r{canonical_json_sha256(revision_basis)[:12]}"
    contract = {**revision_basis, "plan_revision": plan_revision}
    contract_ref = write_immutable_json_sidecar(
        state_dir,
        contract,
        root="plan-synth/contracts",
        kind="plan_synth_contract",
        schema_version=PLAN_SYNTH_CONTRACT_SCHEMA,
        created_by="plan-synth-handoff",
        source_event_id=trigger_event_id,
    )
    input_refs.insert(0, {
        **contract_ref,
        "source_id": "plan-synth-contract",
        "artifact_id": "plan-synth-contract.json",
        "allowed_paths": ["$"],
    })
    return {
        "workflow_run_id": workflow_run_id,
        "fanout_id": fanout_id,
        "stage_id": stage_id,
        "child_id": "synth",
        "run_id": run_id,
        "role_instance": role_instance,
        "target_ref": str(manifest.get("target_ref") or ""),
        "plan_revision": plan_revision,
        "plan_synth_contract_ref": str(contract_ref.get("ref") or ""),
        "plan_synth_contract_digest": str(contract_ref.get("sha256") or ""),
        "reviewed_plan_candidate_digest": str(
            contract_ref.get("sha256") or ""
        ),
        "input_refs": input_refs,
        "output_profile_id": PLAN_SYNTH_PROFILE_ID,
        "output_profile_revision": PLAN_SYNTH_PROFILE_REVISION,
        "result_protocol_mode": "blocking",
        "canonical_success_event": "fanout.synth.completed",
        "canonical_failure_event": "fanout.synth.completed",
    }


def _child_artifact_refs(report: Mapping[str, Any]) -> list[str]:
    refs = [
        str(report.get(key) or "").strip()
        for key in _CHILD_ARTIFACT_SCALAR_KEYS
    ]
    artifact_refs = report.get("artifact_refs")
    if isinstance(artifact_refs, list):
        refs.extend(str(item or "").strip() for item in artifact_refs)
    plan_ports = report.get("plan_ports")
    if isinstance(plan_ports, list):
        refs.extend(
            str(item.get("ref") or "").strip()
            for item in plan_ports
            if isinstance(item, Mapping)
        )
    return list(dict.fromkeys(ref for ref in refs if ref))


__all__ = [
    "PLAN_SYNTH_CONTRACT_SCHEMA",
    "PLAN_SYNTH_PROFILE_ID",
    "PLAN_SYNTH_PROFILE_REVISION",
    "PLAN_SYNTH_RESULT_SCHEMA",
    "build_plan_handoff_input_refs",
    "build_plan_candidate_input_refs",
    "build_plan_synth_call_payload",
    "render_plan_synth_completion_command",
    "render_plan_synth_validation_section",
]
