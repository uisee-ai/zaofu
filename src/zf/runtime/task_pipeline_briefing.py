"""Compact worker briefing renderer for Task Pipeline stage operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.schema import RoleConfig
from zf.runtime.verification_commands import verification_command_required_for_stage


def write_task_pipeline_briefing(
    runtime: Any,
    *,
    role: RoleConfig,
    task: Any,
    stage: str,
    workspace: Any,
    contract_snapshot: Mapping[str, Any],
    payload: dict[str, Any],
    prepared: Any,
) -> Path:
    from zf.runtime.cli_command import zf_cli_cmd
    from zf.runtime.stage_execution_card import (
        compact_stage_context,
        prepare_profiled_stage_result,
        prepare_writer_execution_card,
    )

    result_lines: list[str]
    if stage == "impl":
        from zf.runtime.impl_self_check import completion_payload_template

        completion = {
            **payload,
            "source_commit": "<HEAD commit>",
            "files_touched": [],
            "evidence_refs": ["<durable implementation evidence ref>"],
            **completion_payload_template(
                contract_snapshot=contract_snapshot,
                task_item=payload,
                task_id=str(task.id),
                run_id=str(payload.get("workflow_run_id") or ""),
                child_id=prepared.operation_id,
            ),
        }
        command, _, display, result_lines = prepare_writer_execution_card(
            state_dir=Path(runtime.state_dir),
            task_item=payload,
            task_payload=payload,
            completion_payload=completion,
            run_id=str(payload.get("workflow_run_id") or ""),
            cli_command=zf_cli_cmd(),
            completion_command="",
            blocked_command="",
        )
        stage_instructions = [
            "Implement only the admitted Task Contract in this Task Workspace.",
            "Commit task changes with explicit pathspecs before submission.",
            "Set target_commit and every self-check target to the exact HEAD.",
            "Do not update candidate/main refs or runtime truth directly.",
        ]
    elif stage == "verify":
        verification_owner = str(
            contract_snapshot.get("verification_owner") or "task_verify"
        )
        success_payload = {
            "verification_result": verification_result_template(
                contract_snapshot
            )
        }
        command, result_lines = prepare_profiled_stage_result(
            state_dir=Path(runtime.state_dir),
            child_payload=payload,
            success_payload=success_payload,
            run_id=str(payload.get("workflow_run_id") or ""),
            cli_command=zf_cli_cmd(),
        )
        display = compact_stage_context(payload)
        stage_instructions = [
            "Independently verify only this Task Contract against target_commit.",
            "Do not edit product files or substitute another branch/HEAD.",
            f"Run only checks owned by this task's {verification_owner} layer; "
            "a command produced by another Task or owned by another layer is "
            "retained evidence and must not be rerun by this stage.",
            "For acceptance criteria owned by another verification layer, keep "
            "the requirement row as not_applicable; do not claim that it passed "
            "and do not reject/block this Task for evidence that layer will produce.",
            "Attach durable evidence to every result adjudicated by this stage.",
            "For a product gap, submit verdict=rejected with exact rework_items.",
        ]
        if payload.get("external_evidence_bindings"):
            stage_instructions.append(
                "Before running a command that references an external evidence "
                "environment variable, export the exact env/value binding in "
                "Compact Execution Context; do not recompute or replace it."
            )
    else:
        success_payload = {
            "integration_acceptance_result": (
                integration_acceptance_result_template(payload)
            )
        }
        command, result_lines = prepare_profiled_stage_result(
            state_dir=Path(runtime.state_dir),
            child_payload=payload,
            success_payload=success_payload,
            run_id=str(payload.get("workflow_run_id") or ""),
            cli_command=zf_cli_cmd(),
        )
        display = compact_stage_context(payload)
        stage_instructions = [
            "Read the exact admitted Task Contract, target, and Task Verify result.",
            "Do not run product tests, edit code, or mutate Task/Candidate/runtime truth.",
            "Evaluate only residual integration risk; this is not global Goal closure.",
            "Use exactly admit, revise, replan, or block with the typed evidence requested.",
        ]

    if not command.strip():
        raise ValueError(
            f"Task Pipeline {stage} requires a semantic result submit command"
        )

    from zf.runtime.artifact_read_ledger import render_attempt_source_briefing

    controlled_inputs = render_attempt_source_briefing(
        payload,
        state_dir=Path(runtime.state_dir),
    ).strip()
    heartbeat = (
        f"{zf_cli_cmd()} emit worker.heartbeat --task {task.id} "
        f"--actor {role.instance_id} --payload "
        f"'{{\"instance_id\":\"{role.instance_id}\","
        f"\"current_task_id\":\"{task.id}\",\"state\":\"busy\","
        "\"last_action_ts\":\"<ISO8601 UTC>\"}'"
    )
    lines = [
        f"Active task: {task.id}",
        "",
        f"# Task Pipeline {stage.title()}",
        "",
        f"- task_id: `{task.id}`",
        f"- operation_id: `{prepared.operation_id}`",
        f"- operation_generation: `{payload['operation_generation']}`",
        f"- placement_epoch: `{payload['placement_epoch']}`",
        f"- workdir: `{workspace.project_path}`",
        f"- target_commit: `{payload.get('target_commit') or workspace.base_commit}`",
        f"- skills: `{', '.join(role.skills) or 'none'}`",
        "",
        "## Stage Contract",
        "",
        *[f"- {item}" for item in stage_instructions],
        "",
        f"Task instruction: {_task_instruction(task)}",
        "",
        *result_lines,
        "Success command:",
        "```bash",
        command,
        "```",
        "",
    ]
    if controlled_inputs:
        lines.extend([controlled_inputs, ""])
    feedback = payload.get("rework_feedback")
    if feedback:
        lines.extend([
            "## Rework Feedback",
            "",
            "```json",
            json.dumps(feedback, ensure_ascii=False, indent=2),
            "```",
            "",
        ])
    lines.extend([
        "## Compact Execution Context",
        "",
        "```json",
        json.dumps(display, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Periodic Heartbeat (required)",
        "",
        "Approximately every 60 seconds, or before a long tool call, run:",
        "```bash",
        heartbeat,
        "```",
        "",
        "## Recursion Guard (required)",
        "",
        "Do not dispatch same-role sub-tasks. Do not mutate canonical runtime "
        "state directly. Submit only this operation's typed result; candidate "
        "integration and Task terminal state are Kernel-owned.",
        "",
    ])
    briefing_path = (
        Path(runtime.state_dir)
        / "briefings"
        / "task-pipeline"
        / str(payload.get("workflow_run_id") or "run")
        / str(task.id)
        / f"{stage}-g{payload['operation_generation']}-p{payload['placement_epoch']}.md"
    )
    from zf.runtime.briefing_metrics import write_briefing_with_metrics

    write_briefing_with_metrics(
        briefing_path,
        "\n".join(lines),
        state_dir=Path(runtime.state_dir),
        stage=stage,
        role=role.instance_id,
        payload=payload,
    )
    return briefing_path


def verification_result_template(
    contract_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    verification_owner = str(
        contract_snapshot.get("verification_owner") or "task_verify"
    )
    commands = {
        str(item.get("command_id") or ""): item
        for item in contract_snapshot.get("verification_commands") or []
        if isinstance(item, Mapping) and str(item.get("command_id") or "")
        and verification_command_required_for_stage(
            item,
            verification_owner=verification_owner,
            task_id=str(contract_snapshot.get("task_id") or ""),
        )
    }
    probes = [
        {
            "probe_id": f"verify-{command_id}",
            "command_id": command_id,
            "command": str(item.get("command") or ""),
            "command_digest": str(item.get("command_digest") or ""),
            "target_commit": "<exact target_commit from Stage Context>",
            "status": "passed",
            "exit_code": 0,
            "evidence_refs": ["<durable exact command output or event ref>"],
        }
        for command_id, item in commands.items()
    ]
    if not probes:
        probes = [{
            "probe_id": "independent-task-verify",
            "status": "passed",
            "evidence_refs": ["<durable command/test evidence ref>"],
        }]
    return {
        "schema_version": "verification-result.v1",
        "execution_status": "completed",
        "verdict": "passed",
        "verification_owner": verification_owner,
        "verification_tier": str(
            contract_snapshot.get("verification_tier") or "runtime"
        ),
        "reused_command_receipt_ids": [],
        "probe_receipts": probes,
        "rework_items": [],
        "evidence_refs": ["<durable verification report ref>"],
        "requirement_results": [
            _verification_requirement_template(
                item,
                verification_owner=verification_owner,
                commands=commands,
            )
            for item in contract_snapshot.get("acceptance_criteria") or []
            if isinstance(item, Mapping)
        ],
    }


def _verification_requirement_template(
    criterion: Mapping[str, Any],
    *,
    verification_owner: str,
    commands: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    criterion_owner = str(
        criterion.get("verification_owner") or "task_verify"
    )
    from zf.runtime.verification_commands import acceptance_required_for_stage

    owned_here = acceptance_required_for_stage(criterion, verification_owner=verification_owner)
    return {
        "acceptance_id": str(criterion.get("acceptance_id") or ""),
        "status": "passed" if owned_here else "not_applicable",
        "verification_owner": criterion_owner,
        "verification_tier": str(
            criterion.get("verification_tier") or "runtime"
        ),
        "evidence_refs": (
            ["<durable AC evidence ref>"] if owned_here else []
        ),
        "findings": [],
        "reproduction_commands": (
            [
                str(commands[command_id].get("command") or "")
                for command_id in criterion.get("verification_command_ids") or []
                if command_id in commands
            ]
            if owned_here
            else []
        ),
    }


def integration_acceptance_result_template(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "task-integration-acceptance-result.v1",
        "execution_status": "completed",
        "verdict": "admit",
        "summary": "<bounded integration-risk assessment>",
        "evidence_refs": [
            str(payload.get("verification_result_ref") or "")
        ],
        "finding_refs": [],
        "feedback_refs": [],
        "feedback": [],
        "delta_intent": {},
        "blocker": {},
        "residual_risks": [],
    }


def _task_instruction(task: Any) -> str:
    contract = getattr(task, "contract", None)
    return str(
        getattr(contract, "behavior", "")
        or getattr(task, "title", "")
        or f"Complete Task {task.id}"
    ).strip()


__all__ = [
    "integration_acceptance_result_template",
    "verification_result_template",
    "write_task_pipeline_briefing",
]
