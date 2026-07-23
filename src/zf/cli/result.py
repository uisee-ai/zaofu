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
    credential_from_environment,
    parse_semantic_result_bytes,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("result", help="Durable call-result commands")
    nested = parser.add_subparsers(dest="result_command")
    submit = nested.add_parser("submit", help="Submit one operation semantic result")
    submit.add_argument("--operation", required=True, help="Pinned workflow operation id")
    source = submit.add_mutually_exclusive_group(required=True)
    source.add_argument("--stdin", action="store_true", help="Read result JSON from stdin")
    source.add_argument("--result-file", type=Path, help="Kernel-issued result scratch path")
    submit.add_argument("--state-dir", type=Path, default=None)
    submit.set_defaults(func=_run_submit)
    parser.set_defaults(func=_run_help)


def _run_help(_args: argparse.Namespace) -> int:
    print("usage: zf result submit --operation ID (--stdin|--result-file PATH)")
    return 0


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
            role_instance=role,
            credential=credential,
        )
    except (ConfigError, ResultSubmitError, OSError, ValueError) as exc:
        code = getattr(exc, "code", "result_submit_failed")
        print(f"Error [{code}]: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "admitted",
        "operation_id": outcome.operation_id,
        "canonical_event_id": outcome.canonical_event_id,
        "canonical_event_type": outcome.canonical_event_type,
        "admitted_event_id": outcome.admitted_event_id,
        "envelope_ref": outcome.envelope_ref,
        "control_result_ref": outcome.control_result_ref,
    }, ensure_ascii=False, sort_keys=True))
    return 0


__all__ = ["register"]
