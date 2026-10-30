"""Durable semantic call-result commands."""

from __future__ import annotations

import argparse
import json
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
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome.get("status") == "passed" else 1


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
