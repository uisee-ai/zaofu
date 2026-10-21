"""Retry briefing renderer for one existing fanout child."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.stage_failure_replan import reader_stage_lineage_payload
from zf.runtime.workflow_inputs import render_workflow_input_briefing_section


def write_blocking_reader_retry_briefing(
    runtime: Any,
    *,
    role: RoleConfig,
    manifest: dict,
    child: dict,
    run_id: str,
    contract_handoff_keys: tuple[str, ...],
) -> Path | None:
    """Reuse the durable reader briefing for a blocking operation retry."""

    child_payload = (
        dict(child.get("payload"))
        if isinstance(child.get("payload"), dict)
        else {}
    )
    for key in (
        *contract_handoff_keys,
        "semantic_result_submit_mode",
        "output_profile_id",
        "output_profile_revision",
        "result_scratch_ref",
    ):
        value = child.get(key)
        if value not in (None, "", [], {}):
            child_payload.setdefault(key, value)
    if (
        str(manifest.get("topology") or "") != "fanout_reader"
        or str(child_payload.get("semantic_result_submit_mode") or "")
        != "blocking"
        or not str(child_payload.get("operation_id") or "").strip()
    ):
        return None
    stage = runtime._fanout_stage_by_id(str(manifest.get("stage_id") or ""))
    aggregate = getattr(stage, "aggregate", None) if stage else None
    if aggregate is None:
        return None

    from zf.runtime.fanout import FanoutContext

    context = FanoutContext(
        fanout_id=str(manifest.get("fanout_id") or ""),
        stage_id=str(manifest.get("stage_id") or ""),
        topology="fanout_reader",
        trace_id=str(manifest.get("trace_id") or ""),
        trigger_event_id=str(manifest.get("trigger_event_id") or ""),
        target_ref=str(
            child.get("target_ref") or manifest.get("target_ref") or ""
        ),
        expected_children=[],
    )
    return runtime._write_fanout_briefing(
        role=role,
        context=context,
        child_id=str(child.get("child_id") or ""),
        run_id=run_id,
        aggregate=aggregate,
        child_payload=child_payload,
        skill_entries=[],
    )


def write_fanout_retry_briefing(
    runtime: Any,
    *,
    role: RoleConfig,
    manifest: dict,
    child: dict,
    run_id: str,
) -> Path:
    briefings_dir = runtime.state_dir / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    fanout_id = str(manifest.get("fanout_id") or "")
    child_id = str(child.get("child_id") or "")
    path = briefings_dir / f"{role.instance_id}-{fanout_id}-{child_id}-retry.md"
    aggregate_config = (
        manifest.get("aggregate_config")
        if isinstance(manifest.get("aggregate_config"), dict)
        else {}
    )
    child_payload = (
        dict(child.get("payload"))
        if isinstance(child.get("payload"), dict)
        else {}
    )
    lineage = reader_stage_lineage_payload(
        runtime.event_log.read_all(),
        stage_id=str(manifest.get("stage_id") or ""),
        correlation_id=str(manifest.get("trace_id") or ""),
        fanout_id=fanout_id,
    )
    current_trigger = (
        child_payload.get("trigger_payload")
        if isinstance(child_payload.get("trigger_payload"), dict)
        else {}
    )
    child_payload = {**lineage, **child_payload}
    child_payload["trigger_payload"] = {
        **lineage,
        **current_trigger,
    }
    child_success_event, child_failure_event = (
        runtime._fanout_child_result_events(aggregate_config)
    )
    success_payload = {
        "fanout_id": fanout_id,
        "stage_id": str(manifest.get("stage_id") or ""),
        "child_id": child_id,
        "run_id": run_id,
        "role_instance": role.instance_id,
        "status": "completed",
        "report": {
            "child_id": child_id,
            "status": "passed",
            "summary": "Short retry outcome summary.",
            "findings": [],
            "recommendation": "approve",
        },
    }
    success_event = str(aggregate_config.get("success_event") or "")
    is_plan_artifact_stage = runtime._is_plan_artifact_stage(
        role=role,
        stage_id=str(manifest.get("stage_id") or ""),
        success_event=success_event,
        child_success_event=child_success_event,
    )
    retry_contract_lines: list[str] = []
    if is_plan_artifact_stage:
        plan_ref = (
            f"docs/plans/{manifest.get('stage_id', '')}-"
            f"{child_id}-plan.md"
        )
        task_map_ref = (
            "artifacts/plan/task_map.json"
            if success_event == "task_map.ready"
            else ""
        )
        artifact_refs = [ref for ref in (plan_ref, task_map_ref) if ref]
        success_payload.update({
            "plan_artifact_ref": plan_ref,
            "task_map_ref": task_map_ref,
            "artifact_refs": artifact_refs,
            "evidence_refs": [],
            "plan_ports": [],
        })
        success_payload["report"].update({
            "plan_artifact_ref": plan_ref,
            "task_map_ref": task_map_ref,
            "artifact_refs": artifact_refs,
            "evidence_refs": [],
            "plan_ports": [],
        })
        retry_contract_lines = [
            "",
            "## Planning Retry Contract",
            "",
            *runtime._plan_artifact_contract_lines(),
            *runtime._plan_port_contract_lines(
                flow_kind=str(
                    child_payload.get("flow_kind")
                    or current_trigger.get("flow_kind")
                    or ""
                )
            ),
        ]
        if task_map_ref:
            retry_contract_lines.extend([
                "Write the machine-readable task map to the workdir-relative "
                f"path `{task_map_ref}` and report that same ref; the kernel "
                "relocates it before synth.",
                "This remains task-map synthesis, not findings-only triage. "
                "Do not emit success until the task map and every required "
                "ready plan_port are present.",
            ])
    failure_payload = {
        **success_payload,
        "status": "failed",
        "reason": "Retry could not complete the assigned child scope.",
        "report": {
            "child_id": child_id,
            "status": "failed",
            "summary": "Short retry failure summary.",
            "findings": [],
            "recommendation": "reject",
        },
    }
    workflow_input_section = render_workflow_input_briefing_section(
        child_payload,
    ).strip()
    workflow_input_lines = (
        ["", *workflow_input_section.splitlines()]
        if workflow_input_section
        else []
    )

    def emit_command(event_type: str, payload: dict) -> str:
        if not event_type:
            return "# no event configured"
        cli_parts = shlex.split(zf_cli_cmd()) or ["zf"]
        return " ".join([
            *[shlex.quote(part) for part in cli_parts],
            "emit",
            shlex.quote(event_type),
            "--actor",
            shlex.quote(role.instance_id),
            "--state-dir",
            shlex.quote(str(runtime.state_dir)),
            "--payload",
            shlex.quote(json.dumps(payload, ensure_ascii=False)),
        ])

    lines: list[str] = []
    if manifest.get("topology") == "fanout_writer_scoped":
        task_id = str(child.get("task_id") or "")
        if task_id:
            lines.append(f"Active task: {task_id}")
    lines.extend([
        f"# Fanout Retry: {child_id}",
        "",
        f"- fanout_id: `{fanout_id}`",
        f"- stage_id: `{manifest.get('stage_id', '')}`",
        f"- child_id: `{child_id}`",
        f"- run_id: `{run_id}`",
        f"- target_ref: `{manifest.get('target_ref', '')}`",
        "",
        "This is a retry of the same fanout child. Keep the same child_id and use the new run_id.",
    ])
    if manifest.get("topology") == "fanout_writer_scoped":
        lines.extend([
            f"- task_id: `{child.get('task_id', '')}`",
            f"- workdir: `{child.get('workdir', '')}`",
            f"- worker_branch: `{child.get('source_branch', '')}`",
            "",
            f"Emit dev.build.done with `--state-dir {runtime.state_dir}` and the same fanout_id and child_id when finished.",
        ])
    else:
        lines.extend([
            "",
            "Child payload:",
            "```json",
            json.dumps(child_payload, indent=2, ensure_ascii=False),
            "```",
            "",
            "Aggregate contract:",
            "```json",
            json.dumps(aggregate_config, indent=2, ensure_ascii=False),
            "```",
            *workflow_input_lines,
            *retry_contract_lines,
            "",
            "## Child Result Authority",
            "",
            "Any root Task id in the payload is a lineage anchor for this "
            "workflow operation; this reader child is not the canonical Task "
            "assignee. Do not run `zf guard ownership` for that Task. Use the "
            "new retry run_id and submit exactly one result with a command below.",
            "",
            "Success command:",
            "```bash",
            emit_command(child_success_event, success_payload),
            "```",
            "",
            "Failure command:",
            "```bash",
            emit_command(child_failure_event, failure_payload),
            "```",
            "",
            "Do not emit the aggregate success/failure event directly; the kernel publishes it after the fanout barrier or synth role finishes.",
            "Emit once: command exit 0 is success; there is no asynchronous ack. "
            "Never retry or re-send (stale generations are discarded). Then stop.",
            "`fanout_id`, `stage_id`, `child_id`, `run_id`, `role_instance`, and `status` must stay as top-level payload fields.",
            "Finding schema: use `severity` = info|low|medium|high|critical, `path`, `message`, and optional integer `line`.",
        ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
