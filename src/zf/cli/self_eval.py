"""CLI entrypoint for deterministic self-eval contracts."""

from __future__ import annotations

import argparse
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.self_eval import SelfEvalContractError, load_self_eval_contract, run_self_eval
from zf.core.self_eval.backlog import write_failure_backlog


def register(subparsers) -> None:
    parser = subparsers.add_parser("self-eval", help="Run deterministic self-eval contracts")
    nested = parser.add_subparsers(dest="self_eval_command")

    validate = nested.add_parser("validate", help="Validate a self-eval YAML contract")
    validate.add_argument("--contract", required=True, type=Path)
    validate.set_defaults(func=_validate)

    run = nested.add_parser("run", help="Run a self-eval YAML contract")
    run.add_argument("--contract", required=True, type=Path)
    run.add_argument("--output", type=Path, default=None)
    run.add_argument(
        "--backlog-on-failure",
        action="store_true",
        help="Upsert a kanban backlog task when the self-eval run fails",
    )
    run.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Runtime state dir for --backlog-on-failure (default: project.state_dir, else .zf)",
    )
    run.set_defaults(func=_run)

    parser.set_defaults(func=_help(parser))


def _help(parser: argparse.ArgumentParser):
    def _inner(_args) -> int:
        parser.print_help()
        return 2
    return _inner


def _validate(args) -> int:
    try:
        contract = load_self_eval_contract(args.contract)
    except SelfEvalContractError as exc:
        for error in exc.errors:
            print(f"ERROR: {error}")
        return 1
    print(
        "OK: self-eval contract valid "
        f"(goal={contract.goal!r}, metric={contract.metric.name!r})"
    )
    return 0


def _run(args) -> int:
    try:
        result = run_self_eval(args.contract, output_dir=args.output, cwd=Path.cwd())
    except SelfEvalContractError as exc:
        for error in exc.errors:
            print(f"ERROR: {error}")
        return 1
    print(
        f"Self-eval {result.status}: score="
        f"{result.score if result.score is not None else 'n/a'} "
        f"output={result.output_dir}"
    )
    if args.backlog_on_failure and not result.ok:
        try:
            context = resolve_project_context(
                explicit_state_dir=getattr(args, "state_dir", None),
            )
            backlog = write_failure_backlog(
                contract_path=args.contract,
                result=result,
                state_dir=context.state_dir,
                config=context.config,
            )
        except (ConfigError, SelfEvalContractError, OSError, ValueError) as exc:
            print(f"Backlog write failed: {exc}")
            return 1
        if backlog is not None:
            print(
                f"Backlog task {backlog.action}: "
                f"{backlog.task_id} key={backlog.key}"
            )
    if result.reason and not result.ok:
        print(f"Reason: {result.reason}")
    return 0 if result.ok else 1
