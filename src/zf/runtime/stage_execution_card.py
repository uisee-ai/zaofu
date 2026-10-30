"""Compact, ref-backed execution instructions shared by stage briefings."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Mapping

from zf.core.state.atomic_io import atomic_write_text


_CONTEXT_KEYS = (
    "workflow_run_id",
    "goal_id",
    "task_id",
    "fanout_id",
    "stage_id",
    "child_id",
    "run_id",
    "attempt_id",
    "operation_id",
    "task_pipeline_stage",
    "task_pipeline_entry_mode",
    "operation_generation",
    "workspace_generation",
    "placement_epoch",
    "pipeline_key",
    "task_stage_session_binding",
    "contract_revision",
    "task_map_generation",
    "workflow_generation",
    "request_revision",
    "generic_workflow_contract_digest",
    "workflow_intent",
    "workflow_template",
    "completion_profile",
    "goal_claim_set_ref",
    "goal_claim_set_digest",
    "generic_workflow_operation",
    "workflow_dependencies",
    "workflow_input_ports",
    "workflow_output_ports",
    "base_commit",
    "task_ref",
    "target_commit",
    "verification_owner",
    "verification_tier",
    "external_evidence_bindings",
    "risk_class",
    "integration_admission_profile",
    "exact_task_target_commit",
    "verification_result_ref",
    "verification_result_digest",
    "risk_review_timeout_seconds",
    "risk_review_max_turns",
    "risk_review_budget_usd",
    "candidate_ref",
    "source_branch",
    "workdir",
    "lane_id",
    "lane_profile",
    "affinity_tag",
    "scope",
    "expected_output",
    "allowed_paths",
    "protected_paths",
)
_GENERIC_ARTIFACT_CONTEXT_KEYS = (
    "required_delivery_artifacts",
    "input_result_refs",
    "run_contract_ref",
    "run_contract_digest",
)
_FANOUT_DUPLICATE_IDENTITY_KEYS = frozenset({
    "workflow_run_id",
    "task_id",
    "fanout_id",
    "stage_id",
    "child_id",
    "run_id",
    "attempt_id",
    "operation_id",
})
ARTIFACT_DELIVERY_SUBJECT_GUIDANCE = (
    "SUBJECT OF REVIEW: this artifact-delivery profile has no candidate branch. "
    "Verify only the current Run's Controlled Artifact Inputs, declared "
    "`input_result_refs`, and required delivery artifacts. Do not enumerate "
    "`candidate/*`, global runtime artifacts, or evidence from another "
    "`workflow_run_id`; the Kernel rejects unbound verification evidence.",
)
ARTIFACT_DELIVERY_RESULT_GUIDANCE = (
    "Artifact-delivery identity and `input_result_refs` are pinned by the "
    "Kernel. Do not add raw child result files, source manifests, or transcript "
    "refs as stage inputs.",
    "`verification_evidence_refs` may cite only this Run's declared "
    "`input_result_refs`, evidence/control refs carried by those admitted "
    "envelopes, or the immutable refs declared in `artifacts[]`. Evidence from "
    "another Run or from global runtime-state discovery is rejected.",
    "`goal_coverage[].supporting_artifact_refs` may reference only the immutable "
    "refs declared in `artifacts[]`.",
    "Test commands, screenshots, demos, and verification receipts belong in "
    "`verification_evidence_refs`, not in supporting artifact refs unless they "
    "are themselves declared delivery artifacts.",
    "Use the mandatory claim ids from the controlled Goal claim-set input; do "
    "not invent or rename claims.",
    "The Kernel pre-fills `artifacts[]` from immutable producer outputs. Preserve "
    "those descriptors and verify their content; never remove them or replace "
    "them with transcript/raw workdir paths.",
    "Use only verdicts `passed`, `rejected`, or `blocked`. For `rejected`, set "
    "recommended_action to `gap_plan` or `replan`; for `blocked`, use `human` "
    "or `hold`. Never emit verdict `failed` or action `rework`.",
)
_IMMUTABLE_RESULT_FIELDS = frozenset({
    "workflow_run_id", "operation_id", "request_hash", "task_id",
    "fanout_id", "stage_id", "child_id", "run_id", "role_instance",
    "attempt_id", "dispatch_id", "lease_id", "contract_revision",
    "task_map_generation", "base_commit", "task_ref",
    "contract_snapshot_ref", "contract_snapshot_digest",
    "target_snapshot_ref", "target_commit", "target_snapshot_digest",
    "impl_self_check_ref", "impl_self_check_digest",
    "goal_id", "flow_kind", "objective_ref", "goal_claim_set_ref",
    "goal_claim_set_digest", "planning_result_ref", "candidate_ref",
    "closure_fact_ref", "closure_fact_digest", "output_profile_id",
    "product_acceptance_required", "product_acceptance_spec_ref",
    "product_acceptance_spec_digest", "product_acceptance_report_ref",
    "product_acceptance_report_digest", "product_acceptance_verdict",
    "provider_qualification_required", "provider_qualification_status",
    "output_profile_revision",
    "workflow_generation", "request_revision",
    "generic_workflow_contract_digest", "workflow_intent",
    "workflow_template", "completion_profile",
    "required_delivery_artifacts", "verifier_stage_id", "verifier_role",
    "run_contract_ref", "run_contract_digest",
    "input_result_refs",
    "risk_class", "integration_admission_profile",
    "exact_task_target_commit", "verification_result_ref",
    "verification_result_digest", "execution_profile_id",
    "execution_profile_digest", "risk_review_timeout_seconds",
    "risk_review_max_turns", "risk_review_budget_usd",
    "required_read_ledger_ref", "required_read_ledger_digest",
})


def render_review_subject_lines(
    *,
    candidate_ref: str,
    candidate_head: str,
    candidate_prefix: str,
    subject_pdd_id: str,
    verification_reader: bool,
    artifact_delivery: bool,
    handoff_kind: str = "",
    target_ref: str = "",
    target_commit: str = "",
) -> list[str]:
    if handoff_kind == "task_base_recovery":
        return [
            f"- previous_candidate_ref: `{candidate_ref}`",
            f"- previous_candidate_head_commit: `{candidate_head}`",
            f"- recovery_target_ref: `{target_ref}`",
            f"- recovery_target_commit: `{target_commit}`",
            "",
            "EVALUATE THE RECOVERY TARGET: judge/inspect `target_ref` at "
            "`target_commit` as pinned in Child-Specific Context. This "
            "handoff intentionally recovered to a task base newer than the "
            "last admitted candidate; `candidate_ref` and "
            "`candidate_head_commit` are previous candidate provenance only. "
            "Do not switch the audit to that older candidate.",
            "",
        ]
    if candidate_ref and not artifact_delivery:
        return [
            f"- candidate_ref: `{candidate_ref}`",
            f"- candidate_head_commit: `{candidate_head}`",
            "",
            "EVALUATE THE CANDIDATE: judge/inspect `candidate_ref` at "
            "`candidate_head_commit` — this is the deliverable under review. "
            "`target_ref` is only the merge DESTINATION after ship; it may be "
            "unresolved or stale at review time and its state MUST NOT be a "
            "rejection reason.",
            "",
        ]
    if artifact_delivery:
        return [*ARTIFACT_DELIVERY_SUBJECT_GUIDANCE, ""]
    if not verification_reader:
        return []
    example = (
        f", e.g. `{candidate_prefix}/{subject_pdd_id}`"
        if subject_pdd_id else ""
    )
    return [
        "SUBJECT OF REVIEW: no candidate_ref accompanied this dispatch. If a "
        f"deliverable branch exists (default prefix `{candidate_prefix}/`"
        f"{example}), evaluate THAT branch at its head. `target_ref` is only "
        "the merge DESTINATION after ship; it may be unresolved or empty at "
        "review time and its state MUST NOT be a rejection reason.",
        "",
    ]


def compact_stage_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    context = {
        key: payload[key]
        for key in _CONTEXT_KEYS
        if payload.get(key) not in (None, "", [], {})
    }
    if (
        str(payload.get("flow_kind") or "").strip().lower() == "workflow"
        and str(payload.get("completion_profile") or "").strip().lower()
        == "artifact_delivery"
    ):
        context.update({
            key: payload[key]
            for key in _GENERIC_ARTIFACT_CONTEXT_KEYS
            if payload.get(key) not in (None, "", [], {})
        })
    instruction = str(
        payload.get("instruction")
        or payload.get("summary")
        or ""
    ).strip()
    if instruction:
        context["instruction"] = instruction
    return context


def compact_fanout_stage_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Drop identities already pinned by the fanout header/attempt section."""

    return {
        key: value
        for key, value in compact_stage_context(payload).items()
        if key not in _FANOUT_DUPLICATE_IDENTITY_KEYS
    }


def prepare_result_file_command(
    *,
    state_dir: Path,
    result_scratch_ref: str,
    operation_id: str,
    cli_command: str,
    semantic_template: Mapping[str, Any],
) -> tuple[str, Path]:
    state_root = Path(state_dir).expanduser().resolve()
    scratch_ref = str(result_scratch_ref or "").strip()
    if not scratch_ref:
        raise ValueError("semantic result submit requires result_scratch_ref")
    scratch = (state_root / scratch_ref).resolve()
    if state_root not in scratch.parents:
        raise ValueError("result_scratch_ref escapes state dir")
    if not scratch.exists():
        atomic_write_text(
            scratch,
            json.dumps(
                dict(semantic_template),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n",
        )
    cli = " ".join(
        shlex.quote(part)
        for part in shlex.split(cli_command) or ["zf"]
    )
    command = " ".join([
        cli,
        "result submit",
        "--operation",
        shlex.quote(operation_id),
        "--state-dir",
        shlex.quote(str(state_root)),
        "--scratch",
    ])
    return command, scratch


def render_result_validate_command(submit_command: str) -> str:
    marker = "result submit"
    if marker not in submit_command:
        raise ValueError("semantic submit command has no result submit action")
    return submit_command.replace(marker, "result validate", 1)


def prepare_fanout_result_guidance(
    *,
    child_payload: Mapping[str, Any],
    has_contract_snapshot: bool,
) -> tuple[list[str], bool, str]:
    semantic_submit = (
        str(child_payload.get("semantic_result_submit_mode") or "") == "blocking"
        and bool(str(child_payload.get("operation_id") or "").strip())
    )
    result_prefix = "" if semantic_submit else "report."
    guidance = [
        "Finding schema: use `severity` = info|low|medium|high|critical, "
        "`path`, `message`, and optional integer `line`.",
        f"`{result_prefix}recommendation` is an enum: use exactly `approve`, "
        "`reject`, `needs_rework`, or `abstain`; never append rationale to "
        f"the enum value, and put rationale in `{result_prefix}summary` or "
        f"`{result_prefix}findings`.",
    ]
    if semantic_submit and str(child_payload.get("output_profile_id") or "") == "plan-synth":
        guidance.extend([
            "Plan Synth status fields are intentionally distinct: top-level "
            "`status` is only `completed` or `failed`; nested `report.status` "
            "is only `passed`, `failed`, `blocked`, or `suspended`. A successful "
            "review therefore uses `status: completed`, `report.status: passed`, "
            "and `recommendation: approve`.",
        ])
    if not semantic_submit:
        guidance.append(
            "`fanout_id`, `stage_id`, `child_id`, `run_id`, `role_instance`, "
            "and `status` must stay as top-level payload fields; do not place "
            "them only inside `report`."
        )
    if has_contract_snapshot:
        verification_prefix = "" if semantic_submit else "verification_result."
        guidance.extend([
            f"For `{verification_prefix}requirement_results[].status`, use only "
            "`passed`, `failed`, `blocked`, `waived`, or `not_applicable`; "
            "a `rejected` verdict requires at least one `failed` requirement.",
            "Reuse only listed `reusable_impl_receipts`; record their ids in "
            f"`{verification_prefix}reused_command_receipt_ids`. New canonical "
            "checks in `probe_receipts` must preserve exact `command_id`, `command`, "
            "`command_digest`, and `target_commit`; no substitutions. Passing needs "
            "status=passed, exit_code=0, and durable report/probe evidence refs.",
            "For a rejected or blocked verdict, replace the sample with exact "
            "`rework_items`: classify missing/incomplete/incorrect/unverified/blocked "
            "and state observed gap, required delta, scope, done_when, next gate, "
            "and owner.",
        ])
    return guidance, semantic_submit, result_prefix


def render_fanout_submit_commands(
    *,
    success_command: str,
    failure_command: str,
    semantic_submit: bool,
) -> list[str]:
    if semantic_submit:
        return ["Success command:", "```bash", success_command, "```", ""]
    return [
        "Success command:",
        "```bash",
        success_command,
        "```",
        "",
        "Failure command:",
        "```bash",
        failure_command,
        "```",
        "",
    ]


def _render_plan_candidate_policy(
    validation: Mapping[str, Any],
) -> list[str]:
    """Render the pinned Plan admission limits before candidate authoring."""

    writer_policy = validation.get("writer_policy")
    writer_policy = writer_policy if isinstance(writer_policy, Mapping) else {}
    work_units = writer_policy.get("work_units")
    work_units = work_units if isinstance(work_units, Mapping) else {}
    split_quality = work_units.get("split_quality")
    split_quality = (
        split_quality if isinstance(split_quality, Mapping) else {}
    )
    metadata = validation.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    task_pipeline = metadata.get("task_pipeline")
    task_pipeline = task_pipeline if isinstance(task_pipeline, Mapping) else {}
    candidate = task_pipeline.get("candidate")
    candidate = candidate if isinstance(candidate, Mapping) else {}

    policy_rows: list[str] = []
    from zf.runtime.plan_artifact_package import required_plan_ports

    ports = required_plan_ports(flow_kind=str(metadata.get("flow_kind") or ""), metadata=metadata)
    authored_ports = list(dict.fromkeys([
        *(name for name in ports if name not in {"goal_claim_set", "planning_result"}),
        "source_index",
    ]))
    policy_rows.append("- required Plan inputs/outputs: " + ", ".join(f"`{name}`" for name in authored_ports))
    policy_rows.append(
        "- Preserve current immutable input refs when already supplied. Author missing outputs "
        "through top-level `plan_ports` or the documented named refs; do not rewrite intake "
        "or copy the full skill-adapter catalog into the result. The Kernel derives "
        "`goal_claim_set` and `planning_result`."
    )
    if "candidate_quality_source" in writer_policy:
        policy_rows.append(
            "- candidate quality source: "
            f"`{writer_policy['candidate_quality_source']}`"
        )
    if "enabled" in work_units:
        policy_rows.append(
            f"- work-unit splitting enabled: `{bool(work_units['enabled'])}`"
        )
    if "mode" in split_quality:
        policy_rows.append(
            f"- split-quality mode: `{split_quality['mode']}`"
        )
    if "max_scope_files" in split_quality:
        policy_rows.append(
            "- maximum scope paths per task: "
            f"`{split_quality['max_scope_files']}` (`0` means unbounded)"
        )
    if "max_acceptance_criteria" in split_quality:
        policy_rows.append(
            "- maximum acceptance criteria per task: "
            f"`{split_quality['max_acceptance_criteria']}` "
            "(`0` means unbounded)"
        )
    if "require_validation_surface" in split_quality:
        policy_rows.append(
            "- validation surface required: "
            f"`{bool(split_quality['require_validation_surface'])}`"
        )
    rolling_smoke = str(candidate.get("rolling_smoke") or "").strip()
    if rolling_smoke:
        policy_rows.append(f"- rolling smoke policy: `{rolling_smoke}`")
        if rolling_smoke == "required":
            policy_rows.append(
                "- every task must mark at least one validation command with "
                "`rolling_smoke: true`, `deterministic: true`, "
                "`reusable: true`, and tier `static` or `runtime`"
            )
    if not policy_rows:
        return []
    return [
        "## Effective Plan Admission Policy (kernel-pinned)",
        "",
        "Apply these hard constraints before authoring the first candidate; "
        "do not wait for validation diagnostics to discover them.",
        *policy_rows,
        "",
    ]


def prepare_profiled_stage_result(
    *,
    state_dir: Path,
    child_payload: Mapping[str, Any],
    success_payload: Mapping[str, Any],
    run_id: str,
    cli_command: str,
) -> tuple[str, list[str]]:
    from zf.runtime.call_result_adapters import ControlResultAdapterRegistry

    profile_id = str(child_payload.get("output_profile_id") or "")
    profile_revision = str(child_payload.get("output_profile_revision") or "")
    profile = ControlResultAdapterRegistry().profile(profile_id, profile_revision)
    semantic_body = success_payload.get(profile.semantic_field)
    semantic_body = semantic_body if isinstance(semantic_body, Mapping) else {}
    semantic_body = {
        key: value
        for key, value in semantic_body.items()
        if key not in _IMMUTABLE_RESULT_FIELDS
    }
    output_names = [
        str(port.get("name") or "").strip()
        for port in child_payload.get("workflow_output_ports") or []
        if isinstance(port, Mapping) and str(port.get("name") or "").strip()
    ]
    if output_names:
        outputs = (
            dict(semantic_body.get("outputs") or {})
            if isinstance(semantic_body.get("outputs"), Mapping)
            else {}
        )
        for name in output_names:
            outputs.setdefault(name, "")
        semantic_body["outputs"] = outputs
    command, scratch = prepare_result_file_command(
        state_dir=state_dir,
        result_scratch_ref=str(
            child_payload.get("result_scratch_ref")
            or (
                f"tmp/result-submit/{child_payload['operation_id']}/"
                f"{child_payload.get('attempt_id') or run_id}/result.json"
            )
        ),
        operation_id=str(child_payload["operation_id"]),
        cli_command=cli_command,
        semantic_template=semantic_body,
    )
    lines = [
        "## Output Contract",
        "",
        f"- profile: `{profile_id}` revision `{profile_revision}`",
        f"- schema: `{profile.schema_version}`",
        f"- Edit `{scratch}`. Its JSON root is the complete "
        f"`{profile.semantic_field}` body; preserve prefilled fields at the root. "
        f"Do not wrap it under `{profile.semantic_field}` or add identity fields. "
        "The Kernel supplies immutable identity and selects the canonical event.",
        "- Update the prefilled scratch file in place. With apply_patch use "
        "`*** Update File`; never delete, add, move, or recreate the scratch path.",
        "- For failure, set `execution_status`/`verdict` and exact findings before "
        "running the same command.",
        "- Complete authoring contract: this briefing, prefilled result, and skills. "
        "Do not inspect ZaoFu runtime source/tests/examples/package for hidden fields.",
        "- Submit authorization is transport-owned; do not print or inspect it.",
        "",
    ]
    if profile_id in {"task-verify", "candidate-verify"}:
        lines[7:7] = [
            "- Verification command IDs are a closed set. Use `command_id` only "
            "for an exact command already present in the prefilled template; "
            "record extra independent probes without `command_id` and attach "
            "their command/evidence through requirement reproduction evidence.",
        ]
    if profile_id == "candidate-verify" and semantic_body.get("product_acceptance_report"):
        lines.extend([
            "- Candidate Product Acceptance is separate from task requirement rows: "
            "verify every mandatory product journey/assertion on this target, even "
            "when task rows carry `verification_owner=task_verify`. Do not defer "
            "mandatory product coverage to siblings or synth.",
            "- Product report evidence is an object with exact ref and sha256, "
            "not an event string or a placeholder. Reuse the current tool-output "
            "sidecar descriptor. Task-row not_applicable does not waive product coverage.",
            "",
        ])
    if output_names:
        output_fields = ", ".join(f"`outputs.{name}`" for name in output_names)
        lines[7:7] = [
            f"- Required stage output bodies: {output_fields}. Replace every "
            "prefilled empty value with the complete non-placeholder artifact body "
            "before submission.",
        ]
    if str(child_payload.get("result_semantics") or "") == "artifact_production":
        lines[7:7] = [
            "- Artifact-production status rule: `execution_status` reports whether "
            "this role produced its required outputs, not whether the inspected "
            "subject is healthy. Keep it `completed` when outputs are complete, "
            "including with high/critical findings or `needs_rework`/`reject`; use "
            "`failed` only when the assigned output cannot be produced.",
        ]
    if (
        child_payload.get("workflow_output_ports")
        and child_payload.get("required_delivery_artifacts")
    ):
        lines[7:7] = [
            "- Stage-scope rule: produce this stage's `workflow_output_ports`. "
            "Run-level `required_delivery_artifacts` assigned to downstream stages "
            "are not missing outputs for this operation and must not make it fail.",
        ]
    plan_candidate_validation = child_payload.get("plan_candidate_validation")
    if isinstance(plan_candidate_validation, Mapping):
        lines.extend(_render_plan_candidate_policy(plan_candidate_validation))
        validate_command = render_result_validate_command(command)
        lines.extend([
            "- Plan candidate rule: after authoring, run only the pre-submit "
            "validation command below. It uses the same runtime evaluator as "
            "formal Plan admission and does not consume the submit capability.",
            "- If validation fails, edit this same scratch/artifact set from the "
            "structured diagnostics and rerun validation in this turn. After exit "
            "0, the next tool call MUST be the Success command; do not run any "
            "other audit, search, test, or readback.",
            "- On validation or submission rejection, follow structured "
            "diagnostics only; do not reverse-engineer the harness.",
            "",
            "Plan candidate pre-submit validation:",
            "```bash",
            validate_command,
            "```",
            "",
        ])
    elif profile_id in {"task-verify", "candidate-verify"}:
        command = render_result_validate_command(command) + " --submit"
        lines.extend([
            "- Once the result scratch and evidence are ready, run the Success command below. "
            "It preflights schema, pinned contract and Product Acceptance, then submits "
            "immediately only if preflight passes, without another model turn.",
            "- On rejection, correct this same scratch using structured diagnostics. "
            "Submission still checks current authority, target and evidence; preflight "
            "alone is not admission or a product verdict. Do not repeat completed reads/tests.",
        ])
    else:
        lines.extend([
            "- Terminal submit rule: once the required artifacts and prefilled "
            "result are complete, the next tool call MUST be the Success command "
            "below. Do not run a post-authoring audit, search, test, or result "
            "readback; `result submission` performs deterministic schema/admission "
            "validation and returns structured diagnostics on rejection.",
            "- On rejection, follow structured diagnostics only; do not "
            "reverse-engineer the harness.",
            "",
        ])
    return command, lines


def prepare_writer_execution_card(
    *,
    state_dir: Path,
    task_item: Mapping[str, Any],
    task_payload: Mapping[str, Any],
    completion_payload: Mapping[str, Any],
    run_id: str,
    cli_command: str,
    completion_command: str,
    blocked_command: str,
) -> tuple[str, str, dict[str, Any], list[str]]:
    display = compact_stage_context({**task_item, **task_payload})
    if (
        str(task_item.get("semantic_result_submit_mode") or "") != "blocking"
        or not str(task_item.get("operation_id") or "").strip()
    ):
        return completion_command, blocked_command, display, []
    command, scratch = prepare_result_file_command(
        state_dir=state_dir,
        result_scratch_ref=str(
            task_item.get("result_scratch_ref")
            or (
                f"tmp/result-submit/{task_item['operation_id']}/"
                f"{task_item.get('attempt_id') or run_id}/result.json"
            )
        ),
        operation_id=str(task_item["operation_id"]),
        cli_command=cli_command,
        semantic_template={
            "schema_version": "implementation-result.v1",
            "execution_status": "completed",
            "verdict": "passed",
            "failure_class": "none",
            "blocker_kind": "none",
            "target_commit": "<HEAD commit>",
            "changed_files": [],
            "evidence_refs": ["<implementation summary artifact or event ref>"],
            "self_check": dict(completion_payload.get("impl_self_check") or {}),
            "known_gaps": [],
            "summary": "<concise implementation outcome>",
        },
    )
    lines = [
        "## Output Contract",
        "",
        "- profile: `implementation` revision `1`",
        f"- Edit the complete semantic result at `{scratch}`.",
        "- Update the prefilled scratch file in place. With apply_patch use "
        "`*** Update File`; never delete, add, move, or recreate the scratch path.",
        "- For a blocker, set `execution_status` to `failed`, describe "
        "the reproducible blocker, and run the same submit command.",
        "- If task capsule/briefing/snapshot versions disagree, stop and set "
        "both `failure_class` and `blocker_kind` to `task_context_stale`; "
        "attach the exact conflicting revisions as evidence. This is a "
        "runtime context repair, not an upstream product scope gap.",
        "- If the blocker proves this task contract is unsatisfiable inside "
        "its allowed paths, set both `failure_class` and `blocker_kind` to "
        "one of `task_contract_unsatisfiable`, `upstream_contract_gap`, or "
        "`scope_contract_gap`; keep `none` for ordinary product failures.",
        "- Kernel supplies operation/run/task/attempt identity and selects "
        "the canonical success or failure event.",
        "- Self-check command IDs are a closed set. Include a command receipt "
        "only for an exact command already present in the prefilled template; "
        "record extra checks as evidence instead of inventing a `command_id`.",
        "",
    ]
    return command, command, display, lines


__all__ = [
    "ARTIFACT_DELIVERY_RESULT_GUIDANCE",
    "compact_stage_context",
    "prepare_fanout_result_guidance",
    "prepare_profiled_stage_result",
    "prepare_result_file_command",
    "prepare_writer_execution_card",
    "render_fanout_submit_commands",
    "render_review_subject_lines",
]
