"""CLI for the outer autoresearch supervisor."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from zf.autoresearch.bug_candidates import (
    candidates_from_signals,
    write_candidate_backlogs,
)
from zf.autoresearch.campaign import (
    campaign_names,
    resolve_campaign,
    write_campaign_plan,
)
from zf.autoresearch.eval_result import (
    EvalResult,
    compare_eval_results,
    comparison_to_markdown,
)
from zf.autoresearch.eval_exporter import (
    export_command_eval_result,
    export_run_dir_eval_result,
    export_state_dir_eval_result,
)
from zf.autoresearch.failure_signals import collect_failure_signals
from zf.autoresearch.loop import (
    DEFAULT_REFLECT_BACKEND,
    REFLECT_BACKEND_ENV,
    LoopConfig,
    bypass_inner_run,
    capture_kanban_screenshot,
    collect_eval_snapshot,
    invoke_reflection_llm,
    run_loop,
)
from zf.autoresearch.orchestrator import (
    AutoresearchRunConfig,
    default_run_id,
    run_autoresearch,
    start_tmux_supervisor,
)
from zf.autoresearch.review_gate import (
    REVIEW_GATE_MODES,
    classify_review_gate_policy,
    closeout_review_gate,
)
from zf.autoresearch.review_gate_context import prepare_review_gate_context
from zf.autoresearch.triggers import (
    scan_trigger_decisions,
    trigger_policy_from_config,
    write_trigger_decision,
)
from zf.autoresearch.scenarios import scenario_names
from zf.autoresearch.resident import actions_json, run_resident_once
from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.runtime.maintenance import create_checkpoint, enter_maintenance, exit_maintenance


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "autoresearch",
        help="Run outer autoresearch harness evaluation supervisor",
    )
    nested = parser.add_subparsers(dest="autoresearch_command")

    run = nested.add_parser("run", help="Run an autoresearch evaluation")
    run.add_argument(
        "--scenario",
        default="self-eval-backlog",
        help="Scenario name. Built-ins: " + ", ".join(scenario_names()),
    )
    run.add_argument("--worktree", type=Path, required=True)
    run.add_argument("--config", dest="config_template", type=Path,
                     default=Path("examples/dev-codex-backends.yaml"))
    run.add_argument("--branch", default="")
    run.add_argument("--seed-file", type=Path, default=None)
    run.add_argument("--expected-done", type=int, default=None)
    run.add_argument("--timeout", dest="timeout_seconds", type=int, default=None)
    run.add_argument("--budget-usd", type=float, default=500.0)
    run.add_argument("--reuse-worktree", action="store_true")
    run.add_argument("--keep-running", action="store_true")
    run.add_argument("--runner-module", default="tests.e2e.run_mixed")
    run.add_argument("--run-id", default="")
    run.add_argument("--output-dir", type=Path, default=None)
    run.add_argument("--backlog-on-failure", action="store_true")
    run.add_argument("--backlog-state-dir", type=Path, default=None)
    run.add_argument(
        "--inject-worker-stuck",
        action="store_true",
        help=(
            "After the target worker receives a task, emit an audited "
            "autoresearch.inject.worker_stuck event to exercise recovery"
        ),
    )
    run.add_argument(
        "--inject-worker-stuck-instance",
        default="dev-1",
        help="Worker instance or role to target for stuck injection",
    )
    run.add_argument(
        "--inject-worker-stuck-timeout",
        dest="inject_worker_stuck_timeout_seconds",
        type=int,
        default=600,
        help="Seconds to wait for the target dispatch before giving up injection",
    )
    run.add_argument(
        "--no-sync-dirty",
        dest="sync_dirty",
        action="store_false",
        default=True,
        help=(
            "Strict HEAD-only evaluation: skip overlaying uncommitted "
            "ACDMRT changes from the current checkout into the worktree"
        ),
    )
    run.add_argument(
        "--review-gate",
        choices=sorted(REVIEW_GATE_MODES),
        default="off",
        help=(
            "Opt-in review fanout gate: off disables it, auto prepares evidence "
            "for failed runs and triggers only high-risk fanout cases, always "
            "forces a review-gate artifact for validation."
        ),
    )
    run.add_argument("--confirm", action="store_true")
    run.add_argument(
        "--tmux",
        action="store_true",
        help="Start this outer supervisor in its own tmux session",
    )
    run.add_argument(
        "--no-tmux",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    run.add_argument(
        "--tmux-session",
        default="",
        help="Outer tmux session name (default: zf-ar-supervisor-<run-id>)",
    )
    run.set_defaults(func=_run)

    discover = nested.add_parser(
        "discover-bugs",
        help="Detect autoresearch failure signals and export source backlogs",
    )
    discover.add_argument("--state-dir", type=Path, default=None)
    discover.add_argument("--run-dir", type=Path, default=None)
    discover.add_argument("--out", type=Path, default=Path("backlogs"))
    discover.add_argument("--campaign", default="")
    discover.set_defaults(func=_discover_bugs)

    triggers = nested.add_parser(
        "triggers",
        help="Evaluate autoresearch trigger policy",
    )
    triggers_nested = triggers.add_subparsers(dest="triggers_command")
    scan = triggers_nested.add_parser("scan", help="Read-only trigger scan")
    scan.add_argument("--state-dir", type=Path, default=None)
    scan.add_argument("--run-dir", type=Path, default=None)
    scan.add_argument("--severity-min", default=None)
    scan.add_argument("--cooldown-minutes", type=int, default=None)
    scan.add_argument("--max-triggers-per-hour", type=int, default=None)
    scan.add_argument("--max-daily-runs", type=int, default=None)
    scan.add_argument("--write-events", action="store_true")
    scan.set_defaults(func=_triggers_scan)
    triggers.set_defaults(func=_help(triggers))

    review_gate = nested.add_parser(
        "review-gate",
        help="Prepare and close out opt-in autoresearch review fanout artifacts",
    )
    review_gate_nested = review_gate.add_subparsers(dest="review_gate_command")
    review_prepare = review_gate_nested.add_parser(
        "prepare",
        help="Generate codebase context and failure evidence packs",
    )
    review_prepare.add_argument("--run-dir", type=Path, required=True)
    review_prepare.add_argument("--state-dir", type=Path, required=True)
    review_prepare.add_argument("--source-root", type=Path, default=Path.cwd())
    review_prepare.set_defaults(func=_review_gate_prepare)
    review_closeout = review_gate_nested.add_parser(
        "closeout",
        help="Validate autoresearch.review_council.v1 synth artifact",
    )
    review_closeout.add_argument("--run-dir", type=Path, required=True)
    review_closeout.add_argument("--synth-artifact", type=Path, required=True)
    review_closeout.set_defaults(func=_review_gate_closeout)
    review_gate.set_defaults(func=_help(review_gate))

    repair = nested.add_parser(
        "self-repair",
        help="Supervised self-repair maintenance helpers",
    )
    repair_nested = repair.add_subparsers(dest="self_repair_command")
    prepare = repair_nested.add_parser("prepare", help="Enter maintenance mode")
    prepare.add_argument("--state-dir", type=Path, default=None)
    prepare.add_argument("--trigger", required=True)
    prepare.add_argument("--reason", default="zaofu self-repair requested")
    prepare.set_defaults(func=_self_repair_prepare)
    checkpoint = repair_nested.add_parser("checkpoint", help="Create task checkpoint")
    checkpoint.add_argument("--state-dir", type=Path, default=None)
    checkpoint.add_argument("--task", required=True)
    checkpoint.add_argument("--role", default="")
    checkpoint.add_argument("--worker", default="")
    checkpoint.add_argument("--session-id", default="")
    checkpoint.add_argument("--tmux-session", default="")
    checkpoint.add_argument("--pane-id", default="")
    checkpoint.add_argument("--progress", default="")
    checkpoint.add_argument("--stage", default="")
    checkpoint.add_argument("--transcript-path", default="")
    checkpoint.set_defaults(func=_self_repair_checkpoint)
    validate = repair_nested.add_parser("validate", help="Mark repair validation result")
    validate.add_argument("--state-dir", type=Path, default=None)
    validate.add_argument("--repair-run", required=True)
    validate.add_argument("--summary", default="validated")
    validate.add_argument("--passed", action="store_true")
    validate.set_defaults(func=_self_repair_validate)
    repair.set_defaults(func=_help(repair))

    compare = nested.add_parser(
        "compare",
        help="Compare baseline/candidate eval-result.v1 artifacts",
    )
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument(
        "--format",
        choices=("json", "md"),
        default="json",
        help="Output format",
    )
    compare.add_argument(
        "--min-delta",
        type=float,
        default=0.0,
        help="Minimum score delta required to avoid tie when both gates pass",
    )
    compare.set_defaults(func=_compare_eval_results)

    export_eval = nested.add_parser(
        "export-eval-result",
        help="Export command or run_dir evidence as eval-result.v1",
    )
    source = export_eval.add_mutually_exclusive_group(required=True)
    source.add_argument("--command", default="")
    source.add_argument("--run-dir", type=Path, default=None)
    source.add_argument("--state-dir", type=Path, default=None)
    export_eval.add_argument("--cwd", type=Path, default=Path.cwd())
    export_eval.add_argument("--timeout", type=int, default=120)
    export_eval.add_argument("--scenario", required=True)
    export_eval.add_argument("--mode", choices=("baseline", "candidate"), required=True)
    export_eval.add_argument("--result-id", default="")
    export_eval.add_argument("--out", type=Path, required=True)
    export_eval.set_defaults(func=_export_eval_result)

    resident = nested.add_parser(
        "resident",
        help="Opt-in resident consumer for autoresearch loop requests",
    )
    resident.add_argument("--state-dir", type=Path, default=None)
    resident.add_argument(
        "--worktree-root",
        type=Path,
        default=Path("/tmp/zaofu-autoresearch-resident/worktrees"),
    )
    resident.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Default: <state_dir>/autoresearch/resident",
    )
    resident.add_argument(
        "--execute",
        action="store_true",
        help="Actually run pending loop requests; requires ZF_AUTORESEARCH_RESIDENT=authorized",
    )
    resident.add_argument(
        "--self-repair-consumer",
        action="store_true",
        help="Also consume pending autoresearch.repair.dispatch_requested events",
    )
    resident.add_argument(
        "--self-repair-spawn",
        action="store_true",
        help="When consuming self-repair requests, pass --spawn to zf self-repair run",
    )
    resident.add_argument(
        "--self-repair-backend",
        default="",
        help="Backend id passed to zf self-repair run --backend when --self-repair-spawn is set",
    )
    resident.add_argument(
        "--watch",
        action="store_true",
        help="Keep polling for pending loop requests instead of running once",
    )
    resident.add_argument(
        "--interval-seconds",
        type=float,
        default=10.0,
        help="Polling interval for --watch",
    )
    resident.add_argument(
        "--max-actions-per-tick",
        type=int,
        default=0,
        help="Maximum pending actions consumed per polling tick; 0 means unlimited",
    )
    resident.set_defaults(func=_resident)

    loop = nested.add_parser(
        "loop",
        help=(
            "Closed-loop autoresearch + eval + LLM reflection. "
            "Runs scenarios in rotation, evaluates delta vs prior iter, "
            "reflects on whether a better fix exists, writes journal.jsonl + "
            "iter-NNN.md, and waits for parent HEAD to change between iter "
            "(so inner harness can land fixes)."
        ),
    )
    loop.add_argument(
        "--scenarios", nargs="+", required=True,
        help="Scenarios to rotate through (one per iter). Built-ins: " + ", ".join(scenario_names()),
    )
    loop.add_argument("--worktree", type=Path, required=True)
    loop.add_argument(
        "--parent-state-dir", type=Path, default=None,
        help="Parent runtime state dir (default: project.state_dir from zf.yaml)",
    )
    loop.add_argument("--max-iterations", type=int, default=10)
    loop.add_argument("--budget-usd", type=float, default=200.0)
    loop.add_argument(
        "--output-dir", type=Path, default=None,
        help=(
            "Where journal.jsonl + iter-NNN.md + report.md land "
            "(default: <project.state_dir>/autoresearch/loop)"
        ),
    )
    loop.add_argument(
        "--reflect-backend",
        default=os.environ.get(REFLECT_BACKEND_ENV, DEFAULT_REFLECT_BACKEND),
        help=(
            "LLM backend for the reflection step "
            f"(default: ${REFLECT_BACKEND_ENV} or {DEFAULT_REFLECT_BACKEND})"
        ),
    )
    loop.add_argument(
        "--fix-wait-strategy", choices=("head_change", "manual", "none"),
        default="head_change",
        help="Between iter, wait for parent git HEAD to change (head_change), "
             "for operator confirm (manual), or skip (none)",
    )
    loop.add_argument("--fix-wait-timeout", type=int, default=1800)
    loop.add_argument(
        "--config", dest="config_template", type=Path,
        default=Path("examples/dev-codex-backends.yaml"),
    )
    loop.add_argument(
        "--review-gate",
        choices=sorted(REVIEW_GATE_MODES),
        default="off",
        help="Pass review gate mode through to each real autoresearch iteration.",
    )
    loop.add_argument(
        "--screenshot-url", default="",
        help="zf web URL to screenshot via docker mcp/playwright each iter "
             "(e.g. http://127.0.0.1:8765). Empty = disabled.",
    )
    loop.add_argument(
        "--screenshot-docker-image", default="mcp/playwright:latest",
    )
    loop.add_argument(
        "--screenshot-shot-js", type=Path,
        default=Path("tools/playwright-shot.js"),
        help="Host path to playwright-shot.js (will be copied into the "
             "bind-mounted snapshots dir for docker).",
    )
    loop.add_argument(
        "--bypass-autoresearch", action="store_true",
        help="Skip autoresearch scaffold (scenarios, prepare_worktree). "
             "Each iter: rm .zf + cp --yaml-template + zf init + zf start + "
             "emit user.message --seed-text + poll terminal done + zf stop. "
             "Use when you want to drive your own seed against an arbitrary yaml.",
    )
    loop.add_argument(
        "--yaml-template", type=Path, default=None,
        help="(bypass mode) path to a zf.yaml that will be copied into "
             "<worktree>/zf.yaml each iter (e.g. ~/workspace/<project>/zf.yaml).",
    )
    loop.add_argument(
        "--seed-text", default="",
        help="(bypass mode) user.message text emitted at iter start "
             "(JSON-wrapped). Pass the task description here.",
    )
    loop.add_argument(
        "--expected-done", type=int, default=1,
        help=(
            "(bypass mode) how many terminal done events constitute success "
            "(task.status_changed to done, task.archived, or legacy task.done)"
        ),
    )
    loop.add_argument(
        "--inner-wait-timeout", type=int, default=900,
        help="(bypass mode) seconds to poll terminal done before declaring failed.",
    )
    loop.set_defaults(func=_run_loop)

    campaign = nested.add_parser(
        "campaign",
        help="Plan multi-scenario autoresearch campaigns",
    )
    campaign_nested = campaign.add_subparsers(dest="campaign_command")
    plan = campaign_nested.add_parser(
        "plan",
        help="Write a runnable campaign plan without starting providers",
    )
    plan.add_argument(
        "--campaign",
        default="harness-hardening",
        help="Campaign name. Built-ins: " + ", ".join(campaign_names()),
    )
    plan.add_argument("--output-dir", type=Path, required=True)
    plan.add_argument(
        "--worktree-root",
        type=Path,
        default=Path("/tmp/zaofu-autoresearch-campaign"),
    )
    plan.add_argument(
        "--config",
        dest="config_template",
        type=Path,
        default=Path("examples/dev-codex-backends.yaml"),
    )
    plan.add_argument(
        "--no-tmux",
        action="store_true",
        help="Generate direct foreground commands instead of tmux supervisor commands",
    )
    plan.add_argument(
        "--review-gate",
        choices=sorted(REVIEW_GATE_MODES),
        default="off",
        help="Pass review gate mode into every generated autoresearch run command.",
    )
    plan.set_defaults(func=_campaign_plan)
    campaign.set_defaults(func=_help(campaign))

    parser.set_defaults(func=_help(parser))


def _help(parser: argparse.ArgumentParser):
    def _inner(_args) -> int:
        parser.print_help()
        return 2
    return _inner


def _run(args) -> int:
    run_id = args.run_id or default_run_id(args.scenario)
    if (
        args.tmux
        and not args.no_tmux
        and os.environ.get("ZF_AUTORESEARCH_IN_TMUX") != "1"
    ):
        session = args.tmux_session or f"zf-ar-supervisor-{run_id}"
        start_tmux_supervisor(
            [sys.executable, "-m", "zf.cli.main", *sys.argv[1:]],
            worktree=args.worktree,
            session=session,
        )
        print(f"Autoresearch supervisor started in tmux session: {session}")
        print(f"Attach with: tmux attach -t {session}")
        return 0

    cfg = AutoresearchRunConfig(
        scenario_name=args.scenario,
        worktree=args.worktree,
        config_template=args.config_template,
        branch=args.branch,
        seed_file=args.seed_file,
        expected_done=args.expected_done,
        timeout_seconds=args.timeout_seconds,
        budget_usd=args.budget_usd,
        confirm=args.confirm,
        reuse_worktree=args.reuse_worktree,
        keep_running=args.keep_running,
        runner_module=args.runner_module,
        run_id=run_id,
        output_dir=args.output_dir,
        backlog_on_failure=args.backlog_on_failure,
        backlog_state_dir=args.backlog_state_dir,
        inject_worker_stuck=args.inject_worker_stuck,
        inject_worker_stuck_instance=args.inject_worker_stuck_instance,
        inject_worker_stuck_timeout_seconds=(
            args.inject_worker_stuck_timeout_seconds
        ),
        sync_dirty=getattr(args, "sync_dirty", True),
        review_gate=args.review_gate,
    )
    try:
        result = run_autoresearch(cfg)
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2

    print(
        f"Autoresearch {result.status}: "
        f"done={result.tasks_done}/{result.expected_done} "
        f"run_dir={result.run_dir}"
    )
    print(f"Report: {result.report_path}")
    if result.backlog_task_id:
        print(f"Backlog task: {result.backlog_task_id}")
    return 0 if result.ok or result.status == "dry-run" else 1


def _resolve_state_dir(explicit: Path | None) -> Path:
    return _resolve_context(explicit).state_dir


def _resolve_context(explicit: Path | None):
    return resolve_project_context(
        explicit_state_dir=explicit,
        load_config_with_explicit=explicit is not None,
    )


def _discover_bugs(args) -> int:
    state_dir = _resolve_state_dir(args.state_dir)
    signals = collect_failure_signals(state_dir, run_dir=args.run_dir)
    candidates = candidates_from_signals(signals)
    results = write_candidate_backlogs(candidates, out_dir=args.out)
    print(json.dumps({
        "state_dir": str(state_dir),
        "signals": [signal.to_dict() for signal in signals],
        "results": [
            {
                "path": str(result.path),
                "created": result.created,
                "dedupe_key": result.candidate.dedupe_key,
                "bug_id": result.candidate.bug_id,
                "reason": result.reason,
            }
            for result in results
        ],
        "campaign": args.campaign,
    }, ensure_ascii=False, indent=2))
    return 0


def _triggers_scan(args) -> int:
    ctx = _resolve_context(args.state_dir)
    state_dir = ctx.state_dir
    policy = trigger_policy_from_config(
        ctx.config,
        severity_min=args.severity_min,
        cooldown_minutes=args.cooldown_minutes,
        max_triggers_per_hour=args.max_triggers_per_hour,
        max_daily_runs=args.max_daily_runs,
    )
    decisions = scan_trigger_decisions(
        state_dir,
        policy=policy,
        run_dir=args.run_dir,
    )
    if args.write_events:
        for decision in decisions:
            write_trigger_decision(state_dir, decision)
    print(json.dumps([
        _decision_output_with_spine_hint(d, state_dir=state_dir)
        for d in decisions
    ], ensure_ascii=False, indent=2))
    return 0


def _review_gate_prepare(args) -> int:
    try:
        result = prepare_review_gate_context(
            run_dir=args.run_dir,
            state_dir=args.state_dir,
            source_root=args.source_root,
        )
        failure_pack = json.loads(
            Path(result.failure_evidence_pack).read_text(encoding="utf-8")
        )
        policy = classify_review_gate_policy(failure_pack)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 2
    payload = result.to_dict()
    payload["policy"] = policy.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _review_gate_closeout(args) -> int:
    try:
        result = closeout_review_gate(
            run_dir=args.run_dir,
            synth_artifact=args.synth_artifact,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.accepted else 1


def _decision_output_with_spine_hint(decision, *, state_dir: Path) -> dict:
    row = decision.to_dict()
    if (
        row.get("decision") == "accepted"
        and str(row.get("severity") or "").lower() in {"critical", "high"}
    ):
        row["spine_review_hint"] = {
            "reason": "accepted high-severity runtime signal should be reviewed against project spine before repair",
            "command": f"zf project review-spine --state-dir {state_dir} --format md",
        }
    return row


def _self_repair_prepare(args) -> int:
    state_dir = _resolve_state_dir(args.state_dir)
    path = enter_maintenance(
        state_dir,
        trigger_id=args.trigger,
        reason=args.reason,
    )
    print(f"maintenance entered: {path}")
    return 0


def _self_repair_checkpoint(args) -> int:
    context = resolve_project_context(
        explicit_state_dir=args.state_dir,
        load_config_with_explicit=args.state_dir is not None,
    )
    checkpoint = create_checkpoint(
        context.state_dir,
        project_root=context.project_root,
        task_id=args.task,
        role=args.role,
        assigned_worker=args.worker,
        session_id=args.session_id,
        tmux_session=args.tmux_session,
        pane_id=args.pane_id,
        last_progress=args.progress,
        current_stage=args.stage,
        transcript_path=args.transcript_path,
    )
    print(json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _self_repair_validate(args) -> int:
    state_dir = _resolve_state_dir(args.state_dir)
    if not args.passed:
        print("repair validation failed; maintenance remains active")
        return 1
    path = exit_maintenance(
        state_dir,
        repair_run_id=args.repair_run,
        validation_summary=args.summary,
    )
    print(f"maintenance exited: {path}")
    return 0


def _compare_eval_results(args) -> int:
    try:
        baseline = EvalResult.load(args.baseline)
        candidate = EvalResult.load(args.candidate)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    comparison = compare_eval_results(
        baseline,
        candidate,
        min_delta=args.min_delta,
    )
    if args.format == "md":
        print(comparison_to_markdown(comparison))
    else:
        print(json.dumps(comparison.to_dict(), ensure_ascii=False, indent=2))
    return 0 if comparison.winner in {"candidate", "tie"} else 1


def _export_eval_result(args) -> int:
    result_id = args.result_id or f"{args.mode}-{args.scenario}"
    try:
        if args.command:
            evidence_log = args.out.with_suffix(".command.json")
            exported = export_command_eval_result(
                command=args.command,
                cwd=args.cwd,
                result_id=result_id,
                scenario_id=args.scenario,
                mode=args.mode,
                timeout_seconds=args.timeout,
                evidence_log=evidence_log,
            )
            result = exported.result
        else:
            if args.run_dir is not None:
                result = export_run_dir_eval_result(
                    run_dir=args.run_dir,
                    result_id=result_id,
                    scenario_id=args.scenario,
                    mode=args.mode,
                )
            else:
                result = export_state_dir_eval_result(
                    state_dir=args.state_dir,
                    result_id=result_id,
                    scenario_id=args.scenario,
                    mode=args.mode,
                )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}")
        return 2
    result.write(args.out)
    print(f"eval-result: {args.out}")
    print(f"gate={result.gate_status} score={result.total_score:.2f}")
    return 0 if result.gate_passed else 1


def _resident(args) -> int:
    state_dir = _resolve_state_dir(args.state_dir)
    output_root = (
        args.output_root
        if args.output_root is not None
        else state_dir / "autoresearch" / "resident"
    )
    interval = max(float(getattr(args, "interval_seconds", 10.0) or 10.0), 0.1)
    max_actions_per_tick = max(
        int(getattr(args, "max_actions_per_tick", 0) or 0),
        0,
    )
    while True:
        actions = run_resident_once(
            state_dir=state_dir,
            worktree_root=args.worktree_root,
            output_root=output_root,
            execute=args.execute,
            self_repair_consumer=args.self_repair_consumer,
            self_repair_spawn=args.self_repair_spawn,
            self_repair_backend=args.self_repair_backend,
            max_actions_per_tick=max_actions_per_tick,
        )
        print(actions_json(actions), flush=True)
        if not getattr(args, "watch", False):
            break
        import time as _time

        _time.sleep(interval)
    return 0


def _refresh_health_cache(parent_state_dir: Path) -> None:
    """Run `zf kanban health --format json` and stash the result under
    <parent_state_dir>/projections/health.json so collect_eval_snapshot
    can read it. Best-effort; on failure we leave the cache stale and
    the collector falls back to zeros."""
    import json as _json
    import subprocess as _sub

    try:
        proc = _sub.run(
            [sys.executable, "-m", "zf.cli.main",
             "kanban", "--state-dir", str(parent_state_dir),
             "health", "--format", "json"],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            target = parent_state_dir / "projections" / "health.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            # Validate JSON shape before writing.
            _json.loads(proc.stdout)
            target.write_text(proc.stdout)
    except Exception:
        pass


def _real_autoresearch_fn(*, scenario: str, run_id: str, cfg: LoopConfig) -> dict:
    """Adapter: zf.autoresearch.orchestrator.run_autoresearch wrapped to
    return the dict shape the loop driver expects.

    confirm=True so each iter actually executes the inner harness rather
    than landing in dry-run. reuse_worktree=True so a single worktree is
    reused across iters (otherwise prepare_worktree refuses non-empty dir).
    """
    ar_cfg = AutoresearchRunConfig(
        scenario_name=scenario,
        worktree=cfg.worktree,
        config_template=cfg.config_template,
        run_id=run_id,
        confirm=True,
        reuse_worktree=True,
        backlog_on_failure=True,
        backlog_state_dir=cfg.parent_state_dir,
        review_gate=cfg.review_gate,
    )
    result = run_autoresearch(ar_cfg)
    return {
        "status": result.status,
        "tasks_done": result.tasks_done,
        "expected_done": result.expected_done,
        "fatal_event": result.fatal_event,
        "report_path": str(result.report_path),
        "review_gate": result.review_gate or {},
    }


def _real_eval_collector(state_dir: Path):
    _refresh_health_cache(state_dir)
    return collect_eval_snapshot(state_dir)


def _project_root_for_state_dir(state_dir: Path) -> Path:
    try:
        return resolve_project_context(
            explicit_state_dir=state_dir,
            load_config_with_explicit=True,
        ).project_root
    except ConfigError:
        # Legacy/unit-test fallback for callers that pass a raw .zf directory
        # without a zf.yaml control-plane anchor.
        return state_dir.parent


def _real_git_head(state_dir: Path) -> str:
    import subprocess as _sub
    project_root = _project_root_for_state_dir(state_dir)
    try:
        proc = _sub.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return ""


def _real_git_diff(state_dir: Path, base_sha: str) -> str:
    if not base_sha:
        return ""
    import subprocess as _sub
    project_root = _project_root_for_state_dir(state_dir)
    try:
        proc = _sub.run(
            ["git", "diff", "--no-color", f"{base_sha}..HEAD"],
            cwd=project_root, capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            return proc.stdout
    except Exception:
        pass
    return ""


def _real_backlog_reader(state_dir: Path) -> list[dict]:
    import json as _json
    kanban = state_dir / "kanban.json"
    if not kanban.exists():
        return []
    try:
        data = _json.loads(kanban.read_text() or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [
        {
            "id": t.get("id", ""),
            "title": t.get("title", ""),
            "priority": int(t.get("priority", 3)),
        }
        for t in data
        if isinstance(t, dict) and t.get("status") in {"backlog", "in_progress"}
    ]


def _real_wait_for_head_change(
    *,
    parent_state_dir: Path,
    prev_head: str,
    strategy: str,
    timeout_seconds: int,
    git_head_fn,
) -> bool:
    """Poll parent repo HEAD until it differs from prev_head or timeout."""
    if strategy == "none":
        return False
    if strategy == "manual":
        try:
            input("[autoresearch loop] press ENTER when fix landed: ")
        except EOFError:
            pass
        return True
    # head_change strategy
    import time as _time
    deadline = _time.time() + timeout_seconds
    while _time.time() < deadline:
        head = git_head_fn(parent_state_dir)
        if head and head != prev_head:
            return True
        _time.sleep(15)
    return False


def _run_loop(args) -> int:
    context = resolve_project_context(
        explicit_state_dir=args.parent_state_dir,
        load_config_with_explicit=args.parent_state_dir is not None,
    )
    parent_state_dir = context.state_dir
    output_dir = (
        args.output_dir
        if args.output_dir is not None
        else parent_state_dir / "autoresearch" / "loop"
    )
    cfg = LoopConfig(
        scenarios=list(args.scenarios),
        worktree=args.worktree,
        parent_state_dir=parent_state_dir,
        max_iterations=args.max_iterations,
        budget_usd=args.budget_usd,
        output_dir=output_dir,
        reflect_backend=args.reflect_backend,
        fix_wait_strategy=args.fix_wait_strategy,
        fix_wait_timeout=args.fix_wait_timeout,
        config_template=args.config_template,
        screenshot_url=args.screenshot_url,
        screenshot_docker_image=args.screenshot_docker_image,
        screenshot_shot_js=args.screenshot_shot_js,
        bypass_autoresearch=args.bypass_autoresearch,
        yaml_template=args.yaml_template,
        seed_text=args.seed_text,
        expected_done=args.expected_done,
        inner_wait_timeout=args.inner_wait_timeout,
        review_gate=args.review_gate,
    )
    inner_fn = bypass_inner_run if args.bypass_autoresearch else _real_autoresearch_fn
    result = run_loop(
        cfg,
        autoresearch_fn=inner_fn,
        eval_collector_fn=_real_eval_collector,
        reflect_fn=invoke_reflection_llm,
        git_head_fn=_real_git_head,
        git_diff_fn=_real_git_diff,
        backlog_fn=_real_backlog_reader,
        wait_for_fix_fn=_real_wait_for_head_change,
        screenshot_fn=capture_kanban_screenshot if args.screenshot_url else None,
    )
    print()
    print(f"Loop final status: {result.final_status}")
    print(f"Iterations: {result.iterations}")
    print(f"Journal: {result.journal_path}")
    print(f"Report:  {result.report_path}")
    return 0 if result.final_status in {"done", "converged"} else 1


def _campaign_plan(args) -> int:
    try:
        campaign = resolve_campaign(args.campaign)
        paths = write_campaign_plan(
            campaign=campaign,
            output_dir=args.output_dir,
            worktree_root=args.worktree_root,
            config_template=args.config_template,
            use_tmux=not args.no_tmux,
            review_gate=args.review_gate,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2

    print(f"Autoresearch campaign plan: {campaign.name}")
    print(f"JSON: {paths.json_path}")
    print(f"Markdown: {paths.markdown_path}")
    print(f"Script: {paths.script_path}")
    return 0
