"""ZaoFu CLI entry point."""

from __future__ import annotations

import argparse
import sys

import zf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zf",
        description="ZaoFu multi-agent harness CLI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"zf {zf.__version__}",
    )
    parser.set_defaults(func=None)

    subparsers = parser.add_subparsers(dest="command")

    from zf.cli import (init, validate, status, events, start, stop, restart,
                        kanban, gate, cost, memory, handoff, presets, attach,
                        logs, rules, check, cleanup, agents, watch, feature, chat,
                        hook_recv, trace, doctor, workdir, refs, workflow, runs,
                        plan_approval, issue,
                        feishu, autopilot, skills, state, self_eval, panes,
                        autoresearch, update, guard, artifact, bridge, preflight,
                        self_repair, recover, projection)
    from zf.cli import config as config_cli
    from zf.cli import failure as failure_cli
    from zf.cli import report as report_cli
    from zf.cli import metrics as metrics_cli
    from zf.cli import task_trace
    from zf.cli import task_doc as task_doc_cli
    from zf.cli import web
    from zf.cli import spec as spec_cli
    from zf.cli import bug_fix_cycle
    from zf.cli import workspace as workspace_cli
    from zf.cli import backlog as backlog_cli
    from zf.cli import project as project_cli
    from zf.cli import eval_preset as eval_preset_cli
    from zf.cli import ctx as ctx_cli
    from zf.cli import channel as channel_cli
    from zf.cli import profile as profile_cli
    from zf.cli import flow as flow_cli
    init.register(subparsers)
    profile_cli.register(subparsers)
    flow_cli.register(subparsers)
    config_cli.register(subparsers)
    ctx_cli.register(subparsers)
    channel_cli.register(subparsers)
    validate.register(subparsers)
    status.register(subparsers)
    events.register(subparsers)
    start.register(subparsers)
    stop.register(subparsers)
    restart.register(subparsers)
    kanban.register(subparsers)
    gate.register(subparsers)
    cost.register(subparsers)
    memory.register(subparsers)
    handoff.register(subparsers)
    presets.register(subparsers)
    attach.register(subparsers)
    logs.register(subparsers)
    rules.register(subparsers)
    check.register(subparsers)
    cleanup.register(subparsers)
    agents.register(subparsers)
    watch.register(subparsers)
    feature.register(subparsers)
    chat.register(subparsers)
    hook_recv.register(subparsers)
    trace.register(subparsers)
    plan_approval.register(subparsers)
    issue.register(subparsers)
    doctor.register(subparsers)
    workdir.register(subparsers)
    refs.register(subparsers)
    workflow.register(subparsers)
    runs.register(subparsers)
    feishu.register(subparsers)
    autopilot.register(subparsers)
    skills.register(subparsers)
    state.register(subparsers)
    self_eval.register(subparsers)
    panes.register(subparsers)
    autoresearch.register(subparsers)
    update.register(subparsers)
    guard.register(subparsers)
    artifact.register(subparsers)
    preflight.register(subparsers)
    self_repair.register(subparsers)
    recover.register(subparsers)
    projection.register(subparsers)
    report_cli.register(subparsers)
    bridge.register(subparsers)
    metrics_cli.register(subparsers)
    task_trace.register(subparsers)
    task_doc_cli.register(subparsers)
    web.register(subparsers)
    spec_cli.register(subparsers)
    bug_fix_cycle.register(subparsers)
    backlog_cli.register(subparsers)
    workspace_cli.register(subparsers)
    project_cli.register(subparsers)
    eval_preset_cli.register(subparsers)
    failure_cli.register(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.func is None:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
