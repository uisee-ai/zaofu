"""Durable semantic call-result commands."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.result_submit import (
    ResultSubmitError,
    SemanticResultSubmitService,
    SubmittedSemanticResult,
    credential_from_environment,
    parse_semantic_result_bytes,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("result", help="Durable call-result commands")
    nested = parser.add_subparsers(dest="result_command")
    submit = nested.add_parser("submit", help="Submit one operation semantic result")
    _add_result_input_arguments(submit)
    submit.set_defaults(func=_run_submit)
    validate = nested.add_parser(
        "validate",
        help="Preflight one profiled result without consuming submit capability",
    )
    _add_result_input_arguments(validate)
    validate.add_argument("--submit", action="store_true", help="Submit immediately if preflight passes")
    validate.add_argument(
        "--summary", action="store_true",
        help="Print bounded diagnostics; omit this option for the complete preflight report",
    )
    validate.set_defaults(func=_run_validate)
    parser.set_defaults(func=_run_help)


def _add_result_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--operation",
        required=True,
        help="Pinned workflow operation id",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--stdin",
        action="store_true",
        help="Read result JSON from stdin",
    )
    source.add_argument(
        "--result-file",
        type=Path,
        help="Kernel-issued result scratch path",
    )
    source.add_argument(
        "--scratch",
        action="store_true",
        help="Read the Kernel-issued scratch bound to this operation",
    )
    parser.add_argument("--state-dir", type=Path, default=None)


def _run_help(_args: argparse.Namespace) -> int:
    print(
        "usage: zf result {validate,submit} --operation ID "
        "(--stdin|--result-file PATH|--scratch)"
    )
    return 0


def _print_error(exc: Exception, *, fallback_code: str) -> None:
    code = str(getattr(exc, "code", fallback_code))
    print(f"Error [{code}]: {exc}", file=sys.stderr)
    details = getattr(exc, "details", None)
    if isinstance(details, dict) and details:
        print(json.dumps({
            "schema_version": "result-submit-error.v1",
            "status": "error",
            "code": code,
            "message": str(exc),
            "details": details,
        }, ensure_ascii=False, sort_keys=True), file=sys.stderr)


def _run_validate(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=args.state_dir,
            load_config_with_explicit=args.state_dir is not None,
        )
        log = event_log_from_project(
            context.state_dir,
            config=context.config,
            warn=False,
        )
        role, credential = credential_from_environment()
        semantic_result = (
            parse_semantic_result_bytes(sys.stdin.buffer.read())
            if args.stdin else None
        )
        service = SemanticResultSubmitService(
            state_dir=context.state_dir, event_log=log, event_writer=EventWriter(log),
        )
        outcome = service.validate_plan_candidate(
            operation_id=str(args.operation),
            semantic_result=semantic_result,
            result_file=args.result_file,
            use_operation_scratch=bool(args.scratch),
            role_instance=role,
            credential=credential,
            project_root=Path.cwd(),
        )
        if outcome.get("status") == "passed" and getattr(args, "submit", False):
            submitted = service.submit(
                operation_id=str(args.operation), semantic_result=semantic_result,
                result_file=args.result_file, use_operation_scratch=bool(args.scratch),
                role_instance=role, credential=credential,
            )
            _print_submission(submitted)
            return 0
    except (ConfigError, ResultSubmitError, OSError, ValueError) as exc:
        _print_error(exc, fallback_code="result_validate_failed")
        return 1
    display = _validation_summary(outcome) if getattr(args, "summary", False) else outcome
    print(json.dumps(display, ensure_ascii=False, sort_keys=True))
    return 0 if outcome.get("status") == "passed" else 1


def _validation_summary(outcome: dict) -> dict:
    """Bound model-facing diagnostics without changing the actual preflight."""
    raw_errors = outcome.get("errors")
    errors = raw_errors if isinstance(raw_errors, list) else []
    # Round-robin distinct reasons/field patterns so repeats on many tasks do not hide another repair.
    groups = {}
    for item in errors:
        if isinstance(item, dict):
            key = (str(item.get("code") or ""), re.sub(r"\[\d+\]", "[]", str(item.get("field") or "")))
        else:
            key = ("", "")
        groups.setdefault(key, []).append(item)
    first_per_code = {}
    for group in groups:
        first_per_code.setdefault(group[0], group)
    ordered = [*first_per_code.values(), *(g for g in groups if g != first_per_code[g[0]])]
    representatives = []
    limit = 12
    for index in range(max((len(items) for items in groups.values()), default=0)):
        representatives.extend(groups[g][index] for g in ordered if index < len(groups[g]))
        if len(representatives) >= limit:
            break
    shown = []
    for item in representatives[:limit]:
        if isinstance(item, dict):
            shown.append({key: str(item.get(key) or "")[:512] for key in ("code", "field", "message")})
        else:
            shown.append({"message": str(item)[:512]})
    return {
        "schema_version": "result-validation-summary.v1",
        "status": outcome.get("status"),
        "error_count": len(errors) if isinstance(raw_errors, list) else None,
        "errors": shown,
        "errors_omitted": max(0, len(errors) - len(shown)),
        **({"plan_input_contract": {
            key: [str(name)[:128] for name in names[:12]]
            for key, names in outcome["plan_input_contract"].items() if isinstance(names, list)
        }, "plan_input_contract_port_counts": {
            key: len(names) for key, names in outcome["plan_input_contract"].items() if isinstance(names, list)
        }} if isinstance(outcome.get("plan_input_contract"), dict) else {}),
        **_execution_comparison_summary(outcome.get("execution_review")),
        "details_truncated": True,
        "full_report": "Rerun the same validation command without --summary for the complete report.",
    }


def _execution_comparison_summary(review: dict | None) -> dict:
    comparisons = review.get("execution_comparisons") if isinstance(review, dict) else None
    if not comparisons:
        return {}
    summary = {}
    for name, comparison in comparisons.items():
        row = {}
        task_ids = comparison.get("task_ids")
        if "candidate_commands" in comparison:
            commands = comparison["candidate_commands"]
            task_ids = list(dict.fromkeys(
                entry["task_id"] for entries in commands.values() for entry in entries
            ))
            row["candidate_command_count"] = len(commands)
            row["host_permitted_only_count"] = len(comparison["host_permitted_only"])
        if task_ids is not None:
            row["task_count"] = len(task_ids)
            row["sample_task_ids"] = [task_id for task_id in task_ids[:3] if len(task_id) <= 128]
        if "before_rework" in comparison:
            row["before_rework"] = comparison["before_rework"]
        stages = {entry["first_stage_on_admission"]
                  for entry in comparison.get("facts", {}).get("entries", [])
                  if "first_stage_on_admission" in entry}
        if stages:
            row["first_stages_on_admission"] = sorted(stages)
        summary[name] = row
    return {"execution_comparisons": summary}


def _run_submit(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=args.state_dir,
            load_config_with_explicit=args.state_dir is not None,
        )
        log = event_log_from_project(
            context.state_dir,
            config=context.config,
            warn=False,
        )
        role, credential = credential_from_environment()
        semantic_result = (
            parse_semantic_result_bytes(sys.stdin.buffer.read())
            if args.stdin else None
        )
        outcome = SemanticResultSubmitService(
            state_dir=context.state_dir,
            event_log=log,
            event_writer=EventWriter(log),
        ).submit(
            operation_id=str(args.operation),
            semantic_result=semantic_result,
            result_file=args.result_file,
            use_operation_scratch=bool(args.scratch),
            role_instance=role,
            credential=credential,
        )
    except (ConfigError, ResultSubmitError, OSError, ValueError) as exc:
        _print_error(exc, fallback_code="result_submit_failed")
        return 1
    _print_submission(outcome)
    return 0


def _print_submission(outcome: SubmittedSemanticResult) -> None:
    print(json.dumps({
        "status": "admitted",
        "operation_id": outcome.operation_id,
        "canonical_event_id": outcome.canonical_event_id,
        "canonical_event_type": outcome.canonical_event_type,
        "admitted_event_id": outcome.admitted_event_id,
        "envelope_ref": outcome.envelope_ref,
        "control_result_ref": outcome.control_result_ref,
    }, ensure_ascii=False, sort_keys=True))


__all__ = ["register"]
