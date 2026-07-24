"""Compact, ref-backed execution instructions shared by stage briefings."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Mapping

from zf.core.state.atomic_io import atomic_write_text


_CONTEXT_KEYS = (
    "workflow_run_id",
    "task_id",
    "fanout_id",
    "stage_id",
    "child_id",
    "run_id",
    "attempt_id",
    "operation_id",
    "contract_revision",
    "task_map_generation",
    "base_commit",
    "task_ref",
    "target_commit",
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
_IMMUTABLE_RESULT_FIELDS = frozenset({
    "workflow_run_id", "operation_id", "request_hash", "task_id",
    "fanout_id", "stage_id", "child_id", "run_id", "role_instance",
    "attempt_id", "dispatch_id", "lease_id", "contract_revision",
    "task_map_generation", "base_commit", "task_ref",
    "contract_snapshot_ref", "contract_snapshot_digest",
    "target_snapshot_ref", "target_commit", "target_snapshot_digest",
    "goal_id", "flow_kind", "objective_ref", "goal_claim_set_ref",
    "goal_claim_set_digest", "planning_result_ref", "candidate_ref",
    "closure_fact_ref", "closure_fact_digest", "output_profile_id",
    "output_profile_revision",
})


def compact_stage_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    context = {
        key: payload[key]
        for key in _CONTEXT_KEYS
        if payload.get(key) not in (None, "", [], {})
    }
    instruction = str(
        payload.get("instruction")
        or payload.get("summary")
        or ""
    ).strip()
    if instruction:
        context["instruction"] = instruction
    return context


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
        "--result-file",
        shlex.quote(str(scratch)),
    ])
    return command, scratch


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
        "- Submit only the stage semantic result below. The Kernel supplies "
        "operation/run/task/attempt/dispatch identity and selects the canonical event.",
        f"- Edit the complete semantic result at `{scratch}`; the file is "
        "the signed scratch input for both success and failure.",
        "- For failure, set `execution_status`/`verdict` and exact findings in "
        "that file before running the same submit command.",
        "- The transport provides submit authorization; do not print or inspect its credential.",
        "",
    ]
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
        "- For a blocker, set `execution_status` to `failed`, describe "
        "the reproducible blocker, and run the same submit command.",
        "- Kernel supplies operation/run/task/attempt identity and selects "
        "the canonical success or failure event.",
        "",
    ]
    return command, command, display, lines


__all__ = [
    "compact_stage_context",
    "prepare_profiled_stage_result",
    "prepare_result_file_command",
    "prepare_writer_execution_card",
]
