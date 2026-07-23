"""Immutable input binding for plan-synthesis fanout calls."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_read_ledger import materialize_attempt_source_ref
from zf.runtime.call_result_envelope import (
    canonical_json_sha256,
    write_immutable_json_sidecar,
)


PLAN_SYNTH_PROFILE_ID = "plan-synth"
PLAN_SYNTH_PROFILE_REVISION = "1"
PLAN_SYNTH_RESULT_SCHEMA = "plan-synthesis-result.v1"
PLAN_SYNTH_CONTRACT_SCHEMA = "plan-synth-contract.v1"


def render_plan_synth_completion_command(
    *,
    cli_command: str,
    actor: str,
    state_dir: Path,
    payload: Mapping[str, Any],
) -> str:
    """Render a shell-safe stdin payload command for editable synth results."""

    body = json.dumps(dict(payload), ensure_ascii=False, indent=2)
    delimiter = f"ZF_PLAN_SYNTH_PAYLOAD_{canonical_json_sha256(payload)[:12]}"
    command = " ".join([
        *[
            shlex.quote(part)
            for part in shlex.split(cli_command) or ["zf"]
        ],
        "emit",
        "fanout.synth.completed",
        "--actor",
        shlex.quote(actor),
        "--state-dir",
        shlex.quote(str(Path(state_dir))),
        "--payload-file",
        "-",
        f"<<'{delimiter}'",
    ])
    return f"{command}\n{body}\n{delimiter}"


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
        source.setdefault("artifact_id", Path(str(source.get("ref") or "result.json")).name)
        source.setdefault("allowed_paths", ["$"])
        input_refs.append(source)
        child_bindings.append({
            "child_id": child_id,
            "result_event_id": str(report.get("result_event_id") or ""),
            "source_id": str(source.get("source_id") or ""),
            "artifact_id": str(source.get("artifact_id") or ""),
            "sha256": str(source.get("sha256") or ""),
        })

    trigger = (
        manifest.get("trigger_payload")
        if isinstance(manifest.get("trigger_payload"), Mapping)
        else {}
    )
    source_candidates = (
        ("goal-objective", "goal_objective", "objective_ref"),
        ("requirement", "requirement_spec", "requirement_ref"),
        ("requirement", "requirement_spec", "prd_ref"),
        ("review-artifact", "review_artifact", "review_artifact_ref"),
        ("workflow-input", "workflow_input_manifest", "workflow_input_manifest_ref"),
        ("workflow-prompt", "workflow_prompt", "workflow_prompt_ref"),
    )
    seen_sources = {
        (str(item.get("source_id") or ""), str(item.get("sha256") or ""))
        for item in input_refs
    }
    for source_id, kind, key in source_candidates:
        ref = str(trigger.get(key) or manifest.get(key) or "").strip()
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
        if source and identity not in seen_sources:
            source.setdefault("allowed_paths", ["$"])
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
        "input_refs": input_refs,
        "output_profile_id": PLAN_SYNTH_PROFILE_ID,
        "output_profile_revision": PLAN_SYNTH_PROFILE_REVISION,
        "result_protocol_mode": "blocking",
        "canonical_success_event": "fanout.synth.completed",
        "canonical_failure_event": "fanout.synth.completed",
    }


__all__ = [
    "PLAN_SYNTH_CONTRACT_SCHEMA",
    "PLAN_SYNTH_PROFILE_ID",
    "PLAN_SYNTH_PROFILE_REVISION",
    "PLAN_SYNTH_RESULT_SCHEMA",
    "build_plan_synth_call_payload",
    "render_plan_synth_completion_command",
]
