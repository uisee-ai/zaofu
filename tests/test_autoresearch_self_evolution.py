from __future__ import annotations

import json
import subprocess
from pathlib import Path

from zf.autoresearch.eval_result import (
    EvalResult,
    GateResult,
    compare_eval_results,
    comparison_to_markdown,
)
from zf.autoresearch.eval_exporter import (
    export_command_eval_result,
    export_run_dir_eval_result,
    export_state_dir_eval_result,
)
from zf.autoresearch.experiment_graph import (
    build_experiment_graph,
    project_experiment_graph,
)
from zf.autoresearch.loop import (
    EvalSnapshot,
    LoopConfig,
    ReflectionResult,
    run_loop,
)
from zf.autoresearch.loop_requests import (
    LOOP_COMPLETED,
    LOOP_REQUESTED,
    build_loop_request_payload,
    project_loop_requests,
)
from zf.autoresearch.projection import project_autoresearch_state
from zf.autoresearch.resident import (
    REVIEW_GATE_ACCEPTED,
    REVIEW_GATE_COMPLETED,
    REVIEW_GATE_REQUESTED,
    REVIEW_GATE_STARTED,
    run_resident_once,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.cli.main import main
from zf.runtime.repair_dispatch import DISPATCH_REQUESTED


def _eval(
    result_id: str,
    *,
    gate: str = "passed",
    correctness: float = 80,
    context_safety: float = 80,
) -> EvalResult:
    return EvalResult(
        result_id=result_id,
        scenario_id="context-critical-resume",
        mode="candidate",
        gates=[GateResult(name="verify", status=gate)],
        scores={
            "correctness": correctness,
            "regression": 80,
            "stability": 80,
            "harness_recovery": 80,
            "context_safety": context_safety,
            "coordination": 80,
            "cost_efficiency": 80,
            "learning_value": 80,
        },
        evidence_refs={"tests": ["tests/test_runtime_snapshot.py"]},
    )


def test_eval_result_round_trips_and_computes_total(tmp_path):
    result = _eval("candidate-1", correctness=100, context_safety=60)
    path = tmp_path / "candidate.json"
    result.write(path)

    loaded = EvalResult.load(path)

    assert loaded.to_dict()["schema_version"] == "eval-result.v1"
    assert loaded.gate_status == "passed"
    assert loaded.evidence_refs["tests"] == ["tests/test_runtime_snapshot.py"]
    assert loaded.total_score == 84.0


def test_eval_comparator_filters_gate_failed_candidate():
    baseline = _eval("baseline", gate="passed", correctness=70)
    candidate = _eval("candidate", gate="failed", correctness=100)

    comparison = compare_eval_results(baseline, candidate)

    assert comparison.winner == "baseline"
    assert any("candidate gate failed" in reason for reason in comparison.reasons)


def test_eval_comparator_prefers_candidate_when_baseline_gate_failed():
    baseline = _eval("baseline", gate="failed", correctness=90)
    candidate = _eval("candidate", gate="passed", correctness=75)

    comparison = compare_eval_results(baseline, candidate)

    assert comparison.winner == "candidate"
    assert comparison.score_delta < 0


def test_experiment_graph_projects_best_path_and_frontier():
    graph = build_experiment_graph([
        {
            "event_type": "autoresearch.experiment.created",
            "payload": {
                "experiment_id": "exp-root",
                "hypothesis": "baseline failure",
                "status": "scored",
                "gate_status": "passed",
                "score_total": 60,
            },
        },
        {
            "event_type": "autoresearch.experiment.created",
            "payload": {
                "experiment_id": "exp-bad",
                "parent_id": "exp-root",
                "hypothesis": "prompt-only fix",
                "status": "scored",
                "gate_status": "failed",
                "score_total": 95,
            },
        },
        {
            "event_type": "autoresearch.experiment.created",
            "payload": {
                "experiment_id": "exp-good",
                "parent_id": "exp-root",
                "hypothesis": "snapshot evidence fix",
                "status": "scored",
                "gate_status": "passed",
                "score_total": 82,
            },
        },
        {
            "event_type": "autoresearch.experiment.created",
            "payload": {
                "experiment_id": "exp-frontier",
                "parent_id": "exp-good",
                "hypothesis": "add holdout",
                "status": "pending",
            },
        },
    ])

    projection = graph.to_dict()

    assert projection["best_experiment_id"] == "exp-good"
    assert projection["best_path"] == ["exp-root", "exp-good"]
    assert "exp-bad" in projection["rejected"]
    assert projection["frontier"] == ["exp-frontier"]


def test_project_experiment_graph_reads_jsonl(tmp_path):
    records = tmp_path / "state" / "autoresearch" / "experiments" / "events.jsonl"
    records.parent.mkdir(parents=True)
    records.write_text(
        json.dumps({
            "event_type": "autoresearch.experiment.created",
            "payload": {
                "experiment_id": "exp-1",
                "gate_status": "passed",
                "score_total": 70,
            },
        }) + "\n",
        encoding="utf-8",
    )

    projection = project_experiment_graph(tmp_path / "state")

    assert projection["source"]["exists"] is True
    assert projection["best_experiment_id"] == "exp-1"


def test_autoresearch_compare_cli_json_and_markdown(tmp_path, capsys):
    baseline = _eval("baseline", gate="failed", correctness=90)
    candidate = _eval("candidate", gate="passed", correctness=75)
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline.write(baseline_path)
    candidate.write(candidate_path)

    assert main([
        "autoresearch",
        "compare",
        "--baseline",
        str(baseline_path),
        "--candidate",
        str(candidate_path),
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["winner"] == "candidate"

    assert main([
        "autoresearch",
        "compare",
        "--baseline",
        str(baseline_path),
        "--candidate",
        str(candidate_path),
        "--format",
        "md",
    ]) == 0
    md = capsys.readouterr().out
    assert "# Autoresearch A/B Eval Comparison" in md
    assert "winner: `candidate`" in md


def test_comparison_markdown_is_reviewable():
    comparison = compare_eval_results(
        _eval("baseline", gate="failed"),
        _eval("candidate", gate="passed"),
    )

    md = comparison_to_markdown(comparison)

    assert "baseline" in md
    assert "candidate" in md
    assert "Reasons" in md


def test_export_command_eval_result_records_real_command(tmp_path):
    log_path = tmp_path / "ok.command.json"

    exported = export_command_eval_result(
        command="python -c 'print(\"ok\")'",
        cwd=tmp_path,
        result_id="command-ok",
        scenario_id="cli-command",
        mode="candidate",
        evidence_log=log_path,
    )

    assert exported.result.gate_passed is True
    assert exported.result.total_score > 80
    assert log_path.exists()
    assert exported.command_log["returncode"] == 0


def test_export_command_eval_result_failed_gate(tmp_path):
    exported = export_command_eval_result(
        command="python -c 'raise SystemExit(7)'",
        cwd=tmp_path,
        result_id="command-failed",
        scenario_id="cli-command",
        mode="baseline",
    )

    assert exported.result.gate_passed is False
    assert exported.result.total_score < 50


def test_export_run_dir_eval_result_from_iterations(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "iterations.tsv").write_text(
        "status\ttasks_done\texpected_done\tfatal_type\n"
        "passed\t3\t3\t\n",
        encoding="utf-8",
    )
    (run_dir / "events-summary.json").write_text(
        json.dumps({"derived_metrics": {"stuck_recoveries": 1}}),
        encoding="utf-8",
    )

    result = export_run_dir_eval_result(
        run_dir=run_dir,
        result_id="run-passed",
        scenario_id="autoresearch-run",
        mode="candidate",
    )

    assert result.gate_passed is True
    assert result.scores["correctness"] == 100
    assert result.metadata["derived_metrics"]["stuck_recoveries"] == 1


def test_autoresearch_export_eval_result_cli(tmp_path, capsys):
    out = tmp_path / "eval.json"

    assert main([
        "autoresearch",
        "export-eval-result",
        "--command",
        "python -c 'print(\"ok\")'",
        "--cwd",
        str(tmp_path),
        "--scenario",
        "cli-command",
        "--mode",
        "candidate",
        "--out",
        str(out),
    ]) == 0

    text = capsys.readouterr().out
    assert "eval-result:" in text
    loaded = EvalResult.load(out)
    assert loaded.gate_passed is True


def test_export_state_dir_eval_result_scores_event_truth(tmp_path):
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task_map.ready", actor="test"))
    log.append(ZfEvent(type="fanout.started", actor="test"))
    log.append(ZfEvent(type="fanout.child.dispatched", actor="test"))
    log.append(ZfEvent(type="dev.build.done", actor="dev"))
    log.append(ZfEvent(type="fanout.aggregate.completed", actor="test"))
    log.append(ZfEvent(type="candidate.ready", actor="test"))

    result = export_state_dir_eval_result(
        state_dir=state_dir,
        result_id="state-candidate",
        scenario_id="writer-fanout",
        mode="candidate",
    )

    assert result.gate_passed is True
    assert result.scores["coordination"] >= 90
    assert result.metadata["derived_metrics"]["fanout_aggregate_completed"] == 1


def test_export_state_dir_eval_result_fails_cancelled_fanout(tmp_path):
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task_map.ready", actor="test"))
    log.append(ZfEvent(type="fanout.cancelled", actor="test"))

    result = export_state_dir_eval_result(
        state_dir=state_dir,
        result_id="state-baseline",
        scenario_id="writer-fanout",
        mode="baseline",
    )

    assert result.gate_passed is False
    assert result.scores["coordination"] < 50


def test_export_eval_result_cli_supports_state_dir(tmp_path, capsys):
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="candidate.ready", actor="test"))
    out = tmp_path / "state-eval.json"

    assert main([
        "autoresearch",
        "export-eval-result",
        "--state-dir",
        str(state_dir),
        "--scenario",
        "writer-fanout",
        "--mode",
        "candidate",
        "--out",
        str(out),
    ]) == 0

    assert "gate=passed" in capsys.readouterr().out
    assert EvalResult.load(out).metadata["source"] == "state_dir"


def test_loop_request_projection_roundtrip(tmp_path):
    state_dir = tmp_path / ".zf"
    payload = build_loop_request_payload(
        {
            "trigger_id": "arinv-1",
            "invocation_id": "arinv-1",
            "fingerprint": "stall:x",
            "evidence_paths": ["records/x.md"],
        },
        source_event_id="evt-1",
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type=LOOP_REQUESTED, actor="zf-autoresearch", payload=payload))

    projection = project_loop_requests(state_dir)

    assert projection["summary"]["pending"] == 1
    assert projection["recent"][0]["loop_request_id"].startswith("arlp-")
    assert projection["recent"][0]["scenarios"] == ["controlled-stuck-recovery"]


def test_resident_dry_run_plans_without_executing(tmp_path):
    state_dir = tmp_path / ".zf"
    payload = build_loop_request_payload({"trigger_id": "t1"}, source_event_id="evt-1")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))

    actions = run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=False,
    )

    assert len(actions) == 1
    assert actions[0].action == "run_loop"
    assert "autoresearch" in actions[0].command
    events = log.read_all()
    assert [event.type for event in events] == [LOOP_REQUESTED]


def test_resident_dry_run_exposes_research_mode_envelope(tmp_path):
    state_dir = tmp_path / ".zf"
    payload = build_loop_request_payload(
        {
            "trigger_id": "t-scenario",
            "mode": "scenario",
            "scenarios": ["browser-check"],
            "runbook_refs": ["docs/runbooks/e2e.md"],
            "e2e_prompt_refs": ["prompt/case.md"],
        },
        source_event_id="evt-scenario",
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))

    actions = run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=False,
    )

    row = actions[0].to_dict()
    assert row["research_mode"] == "scenario"
    assert row["artifact_kind"] == "scenario_pack.v1"
    assert row["budget_cap"]["max_runs"] == 1
    assert row["artifact_envelope"]["scenario_pack"]["schema_version"] == "scenario_pack.v1"
    assert row["artifact_envelope"]["apply_policy"] == "proposal_only"


def test_resident_completed_event_carries_learn_deposition_envelope(tmp_path):
    state_dir = tmp_path / ".zf"
    payload = build_loop_request_payload(
        {
            "trigger_id": "t-learn",
            "mode": "learn",
            "trigger_conditions": ["replan adopted after probe"],
            "verification_refs": ["tests/test_delivery_trace.py"],
        },
        source_event_id="evt-learn",
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))

    def _fake_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=True,
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_fake_runner,
    )

    completed = [event for event in log.read_all() if event.type == LOOP_COMPLETED][-1]
    envelope = completed.payload["artifact_envelope"]
    assert completed.payload["mode"] == "learn"
    assert envelope["artifact_kind"] == "capability_deposition.v1"
    assert envelope["deposition"]["schema_version"] == "capability_deposition.v1"
    assert envelope["deposition"]["status"] == "proposal_only"


def test_resident_execute_requires_authorization_and_emits_markers(tmp_path):
    state_dir = tmp_path / ".zf"
    payload = build_loop_request_payload({"trigger_id": "t2"}, source_event_id="evt-2")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))
    calls = []

    def _fake_runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=True,
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_fake_runner,
    )

    assert calls
    event_types = [event.type for event in log.read_all()]
    assert "autoresearch.loop.accepted" in event_types
    assert "autoresearch.loop.started" in event_types
    assert "autoresearch.loop.completed" in event_types


def test_resident_dry_run_plans_review_gate_prepare(tmp_path):
    state_dir = tmp_path / ".zf"
    run_dir = tmp_path / "run"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type=REVIEW_GATE_REQUESTED,
        actor="zf-autoresearch",
        payload={
            "request_id": "rg-1",
            "run_dir": str(run_dir),
            "state_dir": str(state_dir),
            "source_root": str(tmp_path),
            "mode": "auto",
            "failure_fingerprint": "fatal:fanout.timed_out:F1",
            "attempt": 1,
            "attempt_cap": 2,
            "budget_cap": {"max_runs": 1, "max_minutes": 45},
        },
    ))

    actions = run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=False,
    )

    assert len(actions) == 1
    assert actions[0].kind == "review_gate"
    assert actions[0].action == "run_review_gate_prepare"
    assert "review-gate" in actions[0].command
    assert actions[0].budget_cap == {"max_runs": 1, "max_minutes": 45}
    assert [event.type for event in log.read_all()] == [REVIEW_GATE_REQUESTED]


def test_resident_executes_authorized_review_gate_prepare(tmp_path):
    state_dir = tmp_path / ".zf"
    run_dir = tmp_path / "run"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type=REVIEW_GATE_REQUESTED,
        actor="zf-autoresearch",
        payload={
            "request_id": "rg-2",
            "run_dir": str(run_dir),
            "state_dir": str(state_dir),
            "source_root": str(tmp_path),
            "mode": "auto",
            "failure_fingerprint": "fatal:fanout.timed_out:F2",
        },
    ))
    calls = []

    def _fake_runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
            "codebase_context_pack": str(run_dir / "codebase.json"),
            "failure_evidence_pack": str(run_dir / "failure.json"),
            "events_summary": str(run_dir / "events-summary.json"),
            "policy": {"route": "fanout_gate", "severity": "high"},
        }), stderr="")

    run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=True,
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_fake_runner,
    )

    assert calls
    assert calls[0][2:6] == ["zf.cli.main", "autoresearch", "review-gate", "prepare"]
    events = log.read_all()
    event_types = [event.type for event in events]
    assert REVIEW_GATE_ACCEPTED in event_types
    assert REVIEW_GATE_STARTED in event_types
    assert REVIEW_GATE_COMPLETED in event_types
    completed = [event for event in events if event.type == REVIEW_GATE_COMPLETED][-1]
    assert completed.payload["route"] == "fanout_gate"
    assert completed.payload["artifact_refs"]["failure_evidence_pack"].endswith("failure.json")


def test_resident_self_repair_consumer_calls_authorized_dispatch(tmp_path):
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type=DISPATCH_REQUESTED,
        actor="test",
        payload={
            "fingerprint": "failure:fatal:worker.respawn.failed:dev-1",
            "attempt": 1,
            "candidate_id": "HIC-1",
            "candidate_path": str(tmp_path / "HIC-1.json"),
            "repair_task_payload": {
                "title": "repair respawn failure",
                "contract": {
                    "scope": ["src/zf/**", "tests/**"],
                    "verification": "uv run pytest tests/test_repair_dispatch.py",
                },
            },
        },
    ))
    calls = []

    actions = run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=False,
        self_repair_consumer=True,
    )

    assert actions[0].kind == "repair_dispatch"
    assert actions[0].action == "run_self_repair"
    assert "self-repair" in actions[0].command

    def _fake_runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="prepared", stderr="")

    run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=True,
        self_repair_consumer=True,
        self_repair_spawn=True,
        self_repair_backend="codex",
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_fake_runner,
    )

    assert calls
    assert calls[0][:5] == [
        actions[0].command[0],
        "-m",
        "zf.cli.main",
        "self-repair",
        "run",
    ]
    assert "--spawn" in calls[0]
    assert "codex" in calls[0]


def test_run_loop_writes_eval_result_experiment_and_artifacts(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    output_dir = tmp_path / "loop"
    cfg = LoopConfig(
        scenarios=["controlled-stuck-recovery"],
        worktree=tmp_path / "wt",
        parent_state_dir=state_dir,
        output_dir=output_dir,
        max_iterations=1,
        fix_wait_strategy="none",
    )

    result = run_loop(
        cfg,
        autoresearch_fn=lambda **kwargs: {
            "status": "passed",
            "tasks_done": 1,
            "expected_done": 1,
        },
        eval_collector_fn=lambda _state_dir: EvalSnapshot(3, 0, 0, 0.1, 0, 0, 1),
        reflect_fn=lambda *args, **kwargs: ReflectionResult(
            verdict="best_so_far",
            alternatives=[],
            risk="low",
            rec_for_next_iter="keep scenario as holdout",
            raw_response="{}",
        ),
        git_head_fn=lambda _state_dir: "abc123",
        git_diff_fn=lambda _state_dir, _base: "",
        backlog_fn=lambda _state_dir: [],
        wait_for_fix_fn=lambda **kwargs: True,
    )

    eval_path = output_dir / "eval-results" / "iter-001.json"
    graph_path = state_dir / "autoresearch" / "experiments" / "events.jsonl"
    assert result.final_status == "done"
    assert EvalResult.load(eval_path).gate_passed is True
    assert graph_path.exists()
    assert list((output_dir / "artifacts").glob("*reflection.json"))
    assert list((output_dir / "artifacts").glob("*deposition.json"))


def test_autoresearch_projection_includes_loop_and_eval_results(tmp_path):
    state_dir = tmp_path / ".zf"
    payload = build_loop_request_payload({"trigger_id": "t3"}, source_event_id="evt-3")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))
    eval_path = state_dir / "autoresearch" / "loop" / "eval-results" / "iter-001.json"
    _eval("projection-eval").write(eval_path)

    projection = project_autoresearch_state(state_dir, project_root=tmp_path)

    assert projection["loop_requests"]["summary"]["pending"] == 1
    assert projection["eval_results"][0]["result_id"] == "projection-eval"
