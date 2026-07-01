"""Real-provider Star topology E2E runner.

The runner uses the current ``examples/star-*.yaml`` presets, rewrites their
mock roles to a real provider, then drives the same kernel event path an
operator would use:

  verifier        candidate.ready -> fanout_reader -> verify.passed/failed
  review          candidate.ready -> fanout_reader -> synth -> review.*
  writer          task_map.ready  -> fanout_writer_scoped -> candidate.*
  writer-conflict task_map.ready  -> fanout.serialize

Usage:
  PYTHONPATH="$(pwd)/src" python -m tests.e2e.run_star --scenario verifier --confirm
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from tests.e2e.star_smoke_support import (
    _candidate_base_ref,
    _create_controlled_candidate_ref,
    _init_state,
    _kill_lingering,
    _read_events,
    _read_session_name,
    _remove_existing_worktree,
    _run,
    _write_json,
    start_harness,
    start_watcher,
    stop_harness,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANDIDATE_REF = "candidate/zf-star-smoke"
DEFAULT_BACKEND = "codex"
DEFAULT_PERMISSION_MODE = "bypass"


@dataclass(frozen=True)
class StarScenario:
    name: str
    config: Path
    stage_id: str
    trigger_event: str
    pdd_id: str
    wait_event: str
    expected_children: int
    create_candidate: bool = False
    create_task_map: bool = False
    conflict_task_map: bool = False


@dataclass
class StarSummary:
    scenario: str
    status: str
    elapsed_s: float
    events: int
    stage_id: str
    fanout_id: str
    terminal_event: str
    aggregate_status: str
    child_dispatched: int
    child_completed: int
    child_failed: int
    synth_dispatched: int
    synth_completed: int
    serialize_count: int
    cancelled_count: int
    aggregate_event: str
    total_cost_usd: float
    backend_usage: dict[str, int]
    timed_out: bool


SCENARIOS: dict[str, StarScenario] = {
    "verifier": StarScenario(
        name="verifier",
        config=REPO_ROOT / "examples" / "star-verifier-reader.yaml",
        stage_id="verify-candidate",
        trigger_event="candidate.ready",
        pdd_id="PDD-STAR-VERIFY",
        wait_event="fanout.aggregate.completed",
        expected_children=3,
        create_candidate=True,
    ),
    "review": StarScenario(
        name="review",
        config=REPO_ROOT / "examples" / "star-critic-review-reader.yaml",
        stage_id="review-wave",
        trigger_event="candidate.ready",
        pdd_id="PDD-STAR-REVIEW",
        wait_event="fanout.aggregate.completed",
        expected_children=3,
        create_candidate=True,
    ),
    "writer": StarScenario(
        name="writer",
        config=REPO_ROOT / "examples" / "star-supervisor-worker-writer.yaml",
        stage_id="supervisor-worker-dev-fanout",
        trigger_event="task_map.ready",
        pdd_id="PDD-STAR-WRITER",
        wait_event="fanout.aggregate.completed",
        expected_children=2,
        create_task_map=True,
    ),
    "writer-conflict": StarScenario(
        name="writer-conflict",
        config=REPO_ROOT / "examples" / "star-supervisor-worker-writer.yaml",
        stage_id="supervisor-worker-dev-fanout",
        trigger_event="task_map.ready",
        pdd_id="PDD-STAR-WRITER-CONFLICT",
        wait_event="fanout.serialize",
        expected_children=0,
        create_task_map=True,
        conflict_task_map=True,
    ),
}


def scenario_spec(name: str) -> StarScenario:
    try:
        return SCENARIOS[name]
    except KeyError as exc:
        raise ValueError(f"unknown star scenario: {name}") from exc


def default_worktree(scenario: StarScenario) -> Path:
    return Path("/tmp") / f"zaofu-star-{scenario.name}-smoke"


def _writer_task_items(*, conflict: bool) -> list[dict]:
    shared_contract_path = "star-smoke/shared-conflict.txt"
    base = [
        {
            "task_id": "TASK-STAR-AUTH",
            "scope": "Create star-smoke/auth/README.md with an auth smoke note.",
            "allowed_paths": ["star-smoke/auth/**"],
            "exclusive_files": [shared_contract_path if conflict else "star-smoke/auth/README.md"],
            "instruction": "Create star-smoke/auth/README.md with one short paragraph about the auth slice, commit it, then emit dev.build.done.",
        },
        {
            "task_id": "TASK-STAR-GATEWAY",
            "scope": "Create star-smoke/gateway/README.md with a gateway smoke note.",
            "allowed_paths": ["star-smoke/gateway/**"],
            "exclusive_files": [shared_contract_path if conflict else "star-smoke/gateway/README.md"],
            "instruction": "Create star-smoke/gateway/README.md with one short paragraph about the gateway slice, commit it, then emit dev.build.done.",
        },
    ]
    return [
        {
            "task_id": item["task_id"],
            "scope": item["scope"],
            "allowed_paths": item["allowed_paths"],
            "protected_paths": [".zf/**", "docs/design/**"],
            "payload": {"instruction": item["instruction"]},
            "_exclusive_files": item["exclusive_files"],
        }
        for item in base
    ]


def _write_writer_task_map(worktree: Path, scenario: StarScenario) -> Path:
    items = _writer_task_items(conflict=scenario.conflict_task_map)
    path = worktree / ".zf" / "artifacts" / scenario.pdd_id / "task_map.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, {"tasks": [{k: v for k, v in item.items() if not k.startswith("_")} for item in items]})
    return path


def _seed_writer_tasks(worktree: Path, scenario: StarScenario) -> None:
    from zf.core.task.schema import Task, TaskContract
    from zf.core.task.store import TaskStore

    store = TaskStore(worktree / ".zf" / "kanban.json")
    task_map_ref = f".zf/artifacts/{scenario.pdd_id}/task_map.json"
    for item in _writer_task_items(conflict=scenario.conflict_task_map):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        store.add(Task(
            id=str(item["task_id"]),
            title=str(item["scope"]),
            status="backlog",
            contract=TaskContract(
                feature_id=scenario.pdd_id,
                behavior=str(payload.get("instruction") or item["scope"]),
                verification="commit + dev.build.done",
                scope=list(item["allowed_paths"]),
                exclusive_files=list(item["_exclusive_files"]),
                acceptance="commit + dev.build.done",
                evidence_contract={
                    "source": "star_writer_e2e_seed",
                    "source_refs": {"task_map_ref": task_map_ref},
                },
            ),
        ))


def _provider_config(
    data: dict,
    *,
    scenario: StarScenario,
    backend: str,
    permission_mode: str,
    model: str,
    budget_usd: float,
) -> dict:
    out = copy.deepcopy(data)
    suffix = f"star-{scenario.name}-{int(time.time())}"
    out.setdefault("project", {})["name"] = f"zaofu-{suffix}"
    out.setdefault("project", {})["state_dir"] = ".zf"
    out.setdefault("session", {})["tmux_session"] = f"zf-{suffix}"
    out["global_budget_usd"] = budget_usd
    runtime = out.setdefault("runtime", {})
    runtime.setdefault("workdirs", {})["enabled"] = True
    runtime.setdefault("workdirs", {})["mode"] = "worktree"
    git_config = runtime.setdefault("git", {})
    git_config["candidate_base_ref"] = _candidate_base_ref()
    git_config["writer_branch_prefix"] = f"worker/{suffix}"
    git_config["task_ref_prefix"] = f"task/{suffix}"
    git_config["candidate_branch_prefix"] = f"candidate/{suffix}"
    for role in out.get("roles", []) or []:
        if not isinstance(role, dict):
            continue
        role["backend"] = backend
        role["permission_mode"] = permission_mode
        role["transport"] = "tmux"
        role.setdefault("spawn_ready_timeout_seconds", 180)
        if model:
            role["model"] = model
    return out


def materialize_workspace(
    *,
    scenario: StarScenario,
    config_path: Path,
    worktree: Path,
    candidate_ref: str,
    backend: str,
    permission_mode: str,
    model: str,
    budget_usd: float,
    clean: bool,
) -> Path:
    if clean:
        _remove_existing_worktree(worktree)
    if not worktree.exists():
        proc = _run(["git", "worktree", "add", "--detach", str(worktree), "HEAD"], capture=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stdout or f"git worktree add failed: {worktree}")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"invalid yaml root: {config_path}")
    rendered = _provider_config(
        data,
        scenario=scenario,
        backend=backend,
        permission_mode=permission_mode,
        model=model,
        budget_usd=budget_usd,
    )
    (worktree / "zf.yaml").write_text(
        yaml.safe_dump(rendered, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    _init_state(worktree)
    if scenario.create_candidate:
        _create_controlled_candidate_ref(worktree, candidate_ref)
    if scenario.create_task_map:
        _write_writer_task_map(worktree, scenario)
        _seed_writer_tasks(worktree, scenario)
    return worktree


def emit_scenario_trigger(worktree: Path, scenario: StarScenario, candidate_ref: str) -> None:
    payload = {
        "pdd_id": scenario.pdd_id,
        "trace_id": f"trace-{scenario.name}-{scenario.pdd_id}",
        "scenario": f"real provider star {scenario.name}",
    }
    if scenario.create_candidate:
        payload["candidate_ref"] = candidate_ref
    print(f"[seed] {scenario.trigger_event} pdd_id={scenario.pdd_id}")
    proc = _run(
        [
            "zf",
            "emit",
            scenario.trigger_event,
            "--actor",
            "e2e",
            "--payload",
            json.dumps(payload, ensure_ascii=False),
        ],
        cwd=worktree,
        capture=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout or f"{scenario.trigger_event} emit failed")
    if proc.stdout:
        print(proc.stdout.strip())


def _stage_events(events: list[dict], event_type: str, stage_id: str) -> list[dict]:
    return [
        event for event in events
        if event.get("type") == event_type
        and (event.get("payload") or {}).get("stage_id") == stage_id
    ]


def wait_for_star(worktree: Path, scenario: StarScenario, timeout_s: int) -> tuple[dict | None, bool]:
    events_path = worktree / ".zf" / "events.jsonl"
    print(f"[wait] expecting {scenario.stage_id} {scenario.wait_event} (timeout {timeout_s}s)")
    started = time.time()
    last = ""
    while time.time() - started < timeout_s:
        events = _read_events(events_path)
        counts = Counter(event.get("type") for event in events)
        terminal = _stage_events(events, scenario.wait_event, scenario.stage_id)
        aggregates = _stage_events(events, "fanout.aggregate.completed", scenario.stage_id)
        serializes = _stage_events(events, "fanout.serialize", scenario.stage_id)
        progress = (
            f"fanout.started={counts.get('fanout.started', 0)} "
            f"dispatched={counts.get('fanout.child.dispatched', 0)} "
            f"child.done={counts.get('fanout.child.completed', 0)} "
            f"child.failed={counts.get('fanout.child.failed', 0)} "
            f"synth.done={counts.get('fanout.synth.completed', 0)} "
            f"serialize={len(serializes)} aggregate={len(aggregates)}"
        )
        if progress != last:
            elapsed = int(time.time() - started)
            print(f"  [{elapsed:>4}s] {progress}")
            last = progress
        if terminal:
            return terminal[-1], False
        time.sleep(5)
    return None, True


def collect_summary(
    worktree: Path,
    scenario: StarScenario,
    terminal: dict | None,
    elapsed_s: float,
    timed_out: bool,
) -> StarSummary:
    events = _read_events(worktree / ".zf" / "events.jsonl")
    counts = Counter(event.get("type") for event in events)
    payload = terminal.get("payload") if terminal else {}
    payload = payload if isinstance(payload, dict) else {}
    fanout_id = str(payload.get("fanout_id") or "")
    aggregate_status = str(payload.get("status") or "")
    aggregate_event = str(payload.get("success_event") or payload.get("failure_event") or "")
    backend_usage = Counter()
    for event in events:
        if event.get("type") == "agent.usage":
            backend_usage[(event.get("payload") or {}).get("backend", "unknown")] += 1
    total_cost = 0.0
    try:
        from zf.core.cost.tracker import CostTracker

        cost_path = worktree / ".zf" / "cost.jsonl"
        if cost_path.exists():
            total_cost = round(CostTracker(cost_path).total_usd(), 4)
    except Exception:
        total_cost = 0.0
    terminal_event = str(terminal.get("type") if terminal else "")
    child_dispatched = counts.get("fanout.child.dispatched", 0)
    ok = False
    if not timed_out and terminal_event == "fanout.serialize":
        ok = child_dispatched == scenario.expected_children
        aggregate_status = aggregate_status or "serialized"
    elif not timed_out and terminal_event == "fanout.aggregate.completed":
        ok = (
            aggregate_status == "completed"
            and child_dispatched >= scenario.expected_children
        )
    status = "TIMEOUT" if timed_out else ("OK" if ok else "FAILED")
    return StarSummary(
        scenario=scenario.name,
        status=status,
        elapsed_s=elapsed_s,
        events=len(events),
        stage_id=scenario.stage_id,
        fanout_id=fanout_id,
        terminal_event=terminal_event,
        aggregate_status=aggregate_status,
        child_dispatched=child_dispatched,
        child_completed=counts.get("fanout.child.completed", 0),
        child_failed=counts.get("fanout.child.failed", 0),
        synth_dispatched=counts.get("fanout.synth.dispatched", 0),
        synth_completed=counts.get("fanout.synth.completed", 0),
        serialize_count=counts.get("fanout.serialize", 0),
        cancelled_count=counts.get("fanout.cancelled", 0),
        aggregate_event=aggregate_event,
        total_cost_usd=total_cost,
        backend_usage=dict(backend_usage),
        timed_out=timed_out,
    )


def print_summary(summary: StarSummary) -> int:
    print("\n========== star provider summary ==========")
    print(f"scenario:         {summary.scenario}")
    print(f"status:           {summary.status}")
    print(f"elapsed:          {summary.elapsed_s:.1f}s")
    print(f"events:           {summary.events}")
    print(f"stage_id:         {summary.stage_id}")
    print(f"terminal event:   {summary.terminal_event}")
    print(f"fanout_id:        {summary.fanout_id}")
    print(f"aggregate status: {summary.aggregate_status}")
    print(f"aggregate event:  {summary.aggregate_event}")
    print(f"children sent:    {summary.child_dispatched}")
    print(f"children done:    {summary.child_completed}")
    print(f"children failed:  {summary.child_failed}")
    print(f"synth done:       {summary.synth_completed}/{summary.synth_dispatched}")
    print(f"serialize:        {summary.serialize_count}")
    print(f"cancelled:        {summary.cancelled_count}")
    print(f"total cost:       ${summary.total_cost_usd:.4f}")
    print(f"backend usage:    {summary.backend_usage}")
    return 0 if summary.status == "OK" else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="verifier")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--worktree", type=Path)
    parser.add_argument("--candidate-ref", default=DEFAULT_CANDIDATE_REF)
    parser.add_argument("--backend", choices=["codex", "claude-code"], default=DEFAULT_BACKEND)
    parser.add_argument("--permission-mode", default=DEFAULT_PERMISSION_MODE)
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--real-budget-usd", type=float, default=100.0)
    parser.add_argument("--reuse-worktree", action="store_true")
    parser.add_argument("--no-stop", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scenario = scenario_spec(args.scenario)
    config_path = args.config or scenario.config
    worktree = args.worktree or default_worktree(scenario)
    print(f"plan: star provider E2E scenario={scenario.name} worktree={worktree}")
    print(f"yaml: {config_path}")
    print(f"backend: {args.backend} permission_mode={args.permission_mode}")
    if scenario.create_candidate:
        print(f"candidate_ref: {args.candidate_ref}")
    if not config_path.exists():
        print(f"[error] config does not exist: {config_path}", file=sys.stderr)
        return 2
    if not args.confirm:
        print("\n[dry-run] pass --confirm to start real provider CLIs.")
        return 0

    root = materialize_workspace(
        scenario=scenario,
        config_path=config_path,
        worktree=worktree,
        candidate_ref=args.candidate_ref,
        backend=args.backend,
        permission_mode=args.permission_mode,
        model=args.model,
        budget_usd=args.real_budget_usd,
        clean=not args.reuse_worktree,
    )
    session_name = _read_session_name(root)
    _kill_lingering(root, session_name)
    if start_harness(root) != 0:
        return 3
    watcher_pid = start_watcher(root)
    if watcher_pid <= 0:
        print("[error] watcher failed before loop.started; see .zf/logs/watcher.log", file=sys.stderr)
        if not args.no_stop:
            stop_harness(root, session_name)
        return 4
    started = time.time()
    terminal: dict | None = None
    timed_out = True
    try:
        emit_scenario_trigger(root, scenario, args.candidate_ref)
        terminal, timed_out = wait_for_star(root, scenario, args.timeout)
    finally:
        if not args.no_stop:
            stop_harness(root, session_name)
    elapsed = time.time() - started
    summary = collect_summary(root, scenario, terminal, elapsed, timed_out)
    return print_summary(summary)


if __name__ == "__main__":
    raise SystemExit(main())
