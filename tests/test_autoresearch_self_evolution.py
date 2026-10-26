from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
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
from zf.autoresearch.loop_driver import _write_experiment_record
from zf.autoresearch.loop_types import IterationRecord
from zf.autoresearch.loop_requests import (
    LOOP_COMPLETED,
    LOOP_FAILED,
    LOOP_REQUESTED,
    build_loop_request_payload,
    project_loop_requests,
)
from zf.autoresearch.orchestrator import (
    AutoresearchRunConfig,
    prepare_worktree,
)
from zf.autoresearch.projection import project_autoresearch_state
from zf.autoresearch.resident import (
    REVIEW_GATE_ACCEPTED,
    REVIEW_GATE_COMPLETED,
    REVIEW_GATE_REQUESTED,
    REVIEW_GATE_STARTED,
    ResidentAction,
    _finalize_resident_worktree,
    _resident_provider_metadata,
    run_resident_once,
)
from zf.autoresearch.scenarios import resolve_scenario
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.cli.main import main
from zf.runtime.repair_dispatch import DISPATCH_REQUESTED, RUN_MANAGER_ACCEPTED
from zf.runtime.call_result_envelope import canonical_json_sha256


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


def test_resident_provider_metadata_records_real_artifact_or_explicit_unknown(
    tmp_path: Path,
) -> None:
    action = ResidentAction(
        loop_request_id="loop-1",
        action="run",
        reason="test",
        command=["codex", "exec", "tiny task"],
    )
    unknown = _resident_provider_metadata(action, tmp_path)
    assert unknown["status"] == "unknown"
    assert unknown["command_digest"]

    (tmp_path / "provider.json").write_text(json.dumps({
        "provider": "codex",
        "model": "gpt-test",
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "cost": {"usd": 0.01},
    }), encoding="utf-8")
    recorded = _resident_provider_metadata(action, tmp_path)
    assert recorded == {
        "status": "recorded",
        "provider": "codex",
        "model": "gpt-test",
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "cost": {"usd": 0.01},
        "source": "provider.json",
    }


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
    assert projection["recent"][0]["scenarios"] == ["incident-reproduction"]


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
    assert "--no-backlog-on-failure" in actions[0].command
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


def test_simplify_mode_runs_dedicated_proposal_only_scenario(tmp_path):
    state_dir = tmp_path / ".zf"
    payload = build_loop_request_payload(
        {
            "trigger_id": "t-simplify",
            "mode": "simplify",
            "reason": "repeated fallback growth",
        },
        source_event_id="evt-simplify",
    )
    assert payload["scenarios"] == ["simplification-audit"]
    assert payload["mode_contract"]["artifact_kind"] == "simplification_audit.v1"
    assert payload["mode_contract"]["budget_cap"]["max_minutes"] == 20
    assert payload["direct_mainline_apply"] is False

    scenario = resolve_scenario("simplification-audit")
    assert scenario.expected_done == 1
    assert "zf-find-simplifications" in scenario.seed_text
    assert "Do not edit production source" in scenario.seed_text

    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))

    def _fake_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="audit archived", stderr="")

    actions = run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=True,
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_fake_runner,
    )

    action = actions[0]
    scenario_index = action.command.index("--scenarios") + 1
    assert action.command[scenario_index] == "simplification-audit"
    assert action.research_mode == "simplify"
    assert action.expected_output == [
        "surveyed_areas",
        "protected_seams",
        "simplification_candidates",
        "rejected_candidates",
        "verification_plans",
    ]
    completed = [event for event in log.read_all() if event.type == LOOP_COMPLETED][-1]
    envelope = completed.payload["artifact_envelope"]
    assert completed.payload["mode"] == "simplify"
    assert envelope["artifact_kind"] == "simplification_audit.v1"
    assert envelope["proposal_only"] is True
    assert envelope["direct_mainline_apply"] is False
    assert envelope["simplification_audit"] == {
        "schema_version": "simplification_audit.v1",
        "status": "proposal_only",
        "direct_apply": False,
    }


def test_resident_defaults_raw_simplify_request_to_simplification_scenario(tmp_path):
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type=LOOP_REQUESTED,
        actor="test",
        payload={
            "loop_request_id": "raw-simplify",
            "mode": "simplify",
            "apply_policy": "proposal_only",
        },
    ))

    actions = run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=False,
    )

    assert actions[0].command[actions[0].command.index("--scenarios") + 1] == (
        "simplification-audit"
    )


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


def test_resident_executes_evolution_request_with_identity_and_terminal(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-evolution-trial-request",
        type="evolution.trial.requested",
        actor="run-manager",
        payload={
            "campaign_id": "campaign-1",
            "trial_id": "trial-1",
            "arm": "candidate",
            "timeout_seconds": 30,
        },
    ))
    calls: list[list[str]] = []

    def _fake_runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="{}", stderr="failed")

    actions = run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=True,
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_fake_runner,
    )

    assert len(actions) == 1
    assert actions[0].action == "run_evolution_trial"
    assert "trial-execute" in calls[0]
    events = log.read_all()
    for event_type in (
        "evolution.trial.execution.accepted",
        "evolution.trial.execution.started",
        "evolution.trial.execution.failed",
    ):
        event = next(event for event in events if event.type == event_type)
        assert event.payload["request_event_id"] == "evt-evolution-trial-request"
        assert event.payload["campaign_id"] == "campaign-1"
        assert event.payload["trial_id"] == "trial-1"

    # A settled resident execution is not executed again on a later poll.
    assert run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=True,
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_fake_runner,
    ) == []
    assert len(calls) == 1


def test_resident_executes_optimizer_agent_request_with_own_terminal_types(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-skill-optimizer-request",
        type="evolution.skill_optimizer.proposal.requested",
        actor="run-manager",
        payload={
            "campaign_id": "skillopt-1",
            "request_key": "request-key-1",
            "timeout_seconds": 30,
        },
    ))
    calls: list[list[str]] = []

    def _fake_runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    actions = run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=True,
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_fake_runner,
    )

    assert len(actions) == 1
    assert actions[0].action == "run_skill_optimizer_agent"
    assert "skill-opt-agent-execute" in calls[0]
    events = log.read_all()
    for event_type in (
        "evolution.skill_optimizer.execution.accepted",
        "evolution.skill_optimizer.execution.started",
        "evolution.skill_optimizer.execution.completed",
    ):
        event = next(event for event in events if event.type == event_type)
        assert event.payload["request_event_id"] == "evt-skill-optimizer-request"
        assert event.payload["campaign_id"] == "skillopt-1"
        assert event.payload["optimizer_request_key"] == "request-key-1"


def test_resident_execute_respects_max_actions_per_tick(tmp_path):
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    for index in range(3):
        payload = build_loop_request_payload(
            {"trigger_id": f"t-limit-{index}"},
            source_event_id=f"evt-limit-{index}",
        )
        log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))
    calls = []

    def _fake_runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    actions = run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=True,
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_fake_runner,
        max_actions_per_tick=1,
    )

    assert len(actions) == 1
    assert len(calls) == 1
    event_types = [event.type for event in log.read_all()]
    assert event_types.count("autoresearch.loop.completed") == 1
    assert event_types.count("autoresearch.loop.requested") == 3


def test_resident_finishes_current_action_then_stops_before_next(tmp_path):
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    for index in range(2):
        log.append(ZfEvent(
            type=LOOP_REQUESTED,
            actor="test",
            payload=build_loop_request_payload(
                {"trigger_id": f"t-shutdown-{index}"},
                source_event_id=f"evt-shutdown-{index}",
            ),
        ))
    shutdown_requested = False
    calls: list[list[str]] = []

    def _fake_runner(command, **kwargs):
        nonlocal shutdown_requested
        calls.append(command)
        shutdown_requested = True
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=True,
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_fake_runner,
        should_stop=lambda: shutdown_requested,
    )

    assert len(calls) == 1
    event_types = [event.type for event in log.read_all()]
    assert event_types.count("autoresearch.loop.accepted") == 2
    assert event_types.count("autoresearch.loop.started") == 1
    assert event_types.count("autoresearch.loop.completed") == 1


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
        type=RUN_MANAGER_ACCEPTED,
        actor="run-manager",
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


def test_resident_self_repair_consumer_waits_for_run_manager_acceptance(tmp_path):
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type=DISPATCH_REQUESTED,
        actor="zf-autoresearch",
        payload={
            "fingerprint": "failure:fatal:worker.respawn.failed:dev-1",
            "attempt": 1,
            "candidate_id": "HIC-1",
            "candidate_path": str(tmp_path / "HIC-1.json"),
        },
    ))

    actions = run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=False,
        self_repair_consumer=True,
    )

    assert actions == []


def test_run_loop_writes_eval_result_experiment_and_artifacts(
    tmp_path: Path,
    monkeypatch,
):
    configured_state = tmp_path / "configured-project" / ".zf"
    configured_state.mkdir(parents=True)
    configured_events = configured_state / "events.jsonl"
    configured_events.write_bytes(b'{"sentinel":"configured-state"}\n')
    configured_before = configured_events.read_bytes()
    monkeypatch.setenv("ZF_STATE_DIR", str(configured_state))
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
    assert configured_events.read_bytes() == configured_before


def test_experiment_ledger_concurrent_writers_are_complete_and_digest_bound(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"

    def write(index: int) -> None:
        record = IterationRecord(
            iter=index,
            started_at=f"2026-01-01T00:00:{index:02d}+00:00",
            scenario="controlled-stuck-recovery",
            run_id=f"run-{index}",
            run_status="passed",
            tasks_done=1,
            expected_done=1,
            eval=EvalSnapshot(3, 0, 0, 0.1, 0, 0, 1),
            delta=None,
            reflect=None,
            git_head=f"head-{index}",
            head_changed_since_prev=True,
            summary=f"iteration {index}",
        )
        _write_experiment_record(
            state_dir=state_dir,
            record=record,
            prev=None,
            eval_result=_eval(f"eval-{index}"),
            eval_path=tmp_path / f"eval-{index}.json",
            trace_refs=[f"trace://{index}"],
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(20)))

    path = state_dir / "autoresearch" / "experiments" / "events.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 20
    assert {row["payload"]["experiment_id"] for row in rows} == {
        f"run-{index}" for index in range(20)
    }
    for row in rows:
        digest = row.pop("record_digest")
        assert digest == canonical_json_sha256(row)


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


def test_resident_bounds_hung_loop_by_declared_budget(tmp_path):
    """2026-07-10 R4: the resident consumes requests single-file with a
    synchronous runner — one unbounded loop (inner 2h test runner) starved 11
    pending requests of any lifecycle. The runner is now bounded by the loop's
    own declared budget (budget_cap.max_minutes, 2x grace) and a timeout
    surfaces as LOOP_FAILED rc=124 so the resident moves on."""
    state_dir = tmp_path / ".zf"
    payload = build_loop_request_payload(
        {"trigger_id": "t-hang", "mode": "predict"},  # predict: max_minutes=10
        source_event_id="evt-hang",
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))
    seen_timeouts = []

    def _hung_runner(command, **kwargs):
        seen_timeouts.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout"))

    run_resident_once(
        state_dir=state_dir,
        worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out",
        execute=True,
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_hung_runner,
    )

    assert seen_timeouts == [1200]  # 10 min declared * 60 * 2x grace
    failed = [event for event in log.read_all() if event.type == LOOP_FAILED]
    assert failed
    assert failed[-1].payload["returncode"] == 124
    assert "bounded loop" in failed[-1].payload["stderr_tail"]


def test_loop_runner_timeout_from_budget_or_fallback():
    from zf.autoresearch.resident import _loop_runner_timeout_s

    assert _loop_runner_timeout_s({"max_minutes": 10}) == 1200
    assert _loop_runner_timeout_s({}) == 1800
    assert _loop_runner_timeout_s(None) == 1800
    assert _loop_runner_timeout_s({"max_minutes": "junk"}) == 1800


def test_resident_queue_acks_all_pending_and_executes_capped(tmp_path):
    """2026-07-10 R5: queued requests had zero lifecycle until executed, so
    the requester staled them while they waited behind a long bounded loop.
    Every pending run_loop request now gets ACCEPTED (queue ack) before the
    per-tick cap, stays pending until STARTED, and is never re-acked."""
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    for i in range(3):
        payload = build_loop_request_payload(
            {"trigger_id": f"t-q{i}", "request_id": f"rmar-q{i}"},
            source_event_id=f"evt-q{i}",
        )
        log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))

    def _quick_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    for _ in range(2):  # two ticks, 1 action per tick
        run_resident_once(
            state_dir=state_dir,
            worktree_root=tmp_path / "worktrees",
            output_root=tmp_path / "out",
            execute=True,
            max_actions_per_tick=1,
            env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
            runner=_quick_runner,
        )

    events = log.read_all()
    accepted = [e for e in events if e.type == "autoresearch.loop.accepted"]
    started = [e for e in events if e.type == "autoresearch.loop.started"]
    completed = [e for e in events if e.type == LOOP_COMPLETED]
    # tick1 acks all 3 queued requests; no re-acks on tick2
    assert len(accepted) == 3
    # cap=1 per tick, two ticks -> exactly 2 executed; accepted-only stayed pending
    assert len(started) == 2
    assert len(completed) == 2
    started_ids = {e.payload["loop_request_id"] for e in started}
    assert len(started_ids) == 2


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _git_repo_with_worktree(
    tmp_path: Path,
    *,
    branch: str,
) -> tuple[Path, Path]:
    repo = tmp_path / f"repo-{branch.replace('/', '-')}"
    repo.mkdir()
    _git(repo, "init", "-b", "dev")
    _git(repo, "config", "user.email", "autoresearch-test@example.invalid")
    _git(repo, "config", "user.name", "Autoresearch Test")
    (repo / ".gitignore").write_text(
        ".zf/\nnode_modules/\n",
        encoding="utf-8",
    )
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "tracked.txt")
    _git(repo, "commit", "-m", "test: seed repository")
    worktree = tmp_path / f"worktree-{branch.replace('/', '-')}"
    _git(repo, "worktree", "add", "-b", branch, str(worktree), "HEAD")
    return repo, worktree


def test_resident_removes_clean_terminal_worktree_and_branch(
    tmp_path: Path,
) -> None:
    repo, worktree = _git_repo_with_worktree(
        tmp_path,
        branch="experiment/clean",
    )

    outcome = _finalize_resident_worktree(
        worktree=worktree,
        loop_request_id="loop-clean",
    )

    assert outcome["status"] == "cleaned"
    assert outcome["event_type"] == "autoresearch.worktree.cleaned"
    assert outcome["branch_deleted"] is True
    assert not worktree.exists()
    branches = _git(repo, "branch", "--format=%(refname:short)").stdout.splitlines()
    assert "experiment/clean" not in branches


def test_resident_retains_clean_worktree_when_archive_is_missing(
    tmp_path: Path,
) -> None:
    _, worktree = _git_repo_with_worktree(
        tmp_path,
        branch="experiment/archive-missing",
    )

    outcome = _finalize_resident_worktree(
        worktree=worktree,
        loop_request_id="loop-archive-missing",
        archive_required=True,
        archive_manifest=tmp_path / "missing-manifest.json",
    )

    assert outcome["status"] == "retained"
    assert outcome["reason"] == "archive_missing"
    assert worktree.exists()


def test_resident_archives_reports_before_clean_worktree_removal(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    payload = build_loop_request_payload(
        {"trigger_id": "archive-before-cleanup"},
        source_event_id="evt-archive-before-cleanup",
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))
    worktree_root = tmp_path / "resident-worktrees"
    output_root = tmp_path / "resident-output"
    action = run_resident_once(
        state_dir=state_dir,
        worktree_root=worktree_root,
        output_root=output_root,
        execute=False,
    )[0]

    repo = tmp_path / "resident-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "dev")
    _git(repo, "config", "user.email", "autoresearch-test@example.invalid")
    _git(repo, "config", "user.name", "Autoresearch Test")
    (repo / ".gitignore").write_text(".zf/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "tracked.txt")
    _git(repo, "commit", "-m", "test: seed resident repository")
    worktree = worktree_root / action.loop_request_id
    worktree.parent.mkdir(parents=True)
    _git(
        repo,
        "worktree",
        "add",
        "-b",
        f"experiment/{action.loop_request_id}",
        str(worktree),
        "HEAD",
    )
    EventLog(worktree / ".zf" / "events.jsonl").append(
        ZfEvent(type="session.started", actor="zf-cli")
    )

    def _runner(command, **kwargs):
        output_dir = Path(command[command.index("--output-dir") + 1])
        inner_dir = output_dir / "runs" / "inner-1"
        inner_dir.mkdir(parents=True)
        (output_dir / "report.md").write_text("loop report\n", encoding="utf-8")
        (output_dir / "journal.jsonl").write_text("{}\n", encoding="utf-8")
        (inner_dir / "report.md").write_text("inner report\n", encoding="utf-8")
        (inner_dir / "events-summary.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    run_resident_once(
        state_dir=state_dir,
        worktree_root=worktree_root,
        output_root=output_root,
        execute=True,
        env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        runner=_runner,
    )

    completed = [
        event for event in log.read_all() if event.type == LOOP_COMPLETED
    ][-1]
    archive_refs = completed.payload["archive_refs"]
    assert Path(archive_refs["manifest"]).is_file()
    assert Path(archive_refs["loop_report"]).is_file()
    assert all(Path(path).is_file() for path in archive_refs["inner_reports"])
    assert completed.payload["worktree_lifecycle"]["status"] == "cleaned"
    assert not worktree.exists()


def test_resident_retains_terminal_worktree_with_candidate_diff(
    tmp_path: Path,
) -> None:
    _, worktree = _git_repo_with_worktree(
        tmp_path,
        branch="experiment/dirty",
    )
    (worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")

    outcome = _finalize_resident_worktree(
        worktree=worktree,
        loop_request_id="loop-dirty",
    )

    assert outcome["status"] == "retained"
    assert outcome["event_type"] == "autoresearch.worktree.retained"
    assert outcome["reason"] == "candidate_diff_present"
    assert any("candidate.txt" in path for path in outcome["dirty_paths"])
    assert worktree.exists()


def _prepare_interrupted_resident_worktree(
    tmp_path: Path,
    worktree: Path,
    monkeypatch,
) -> tuple[Path, Path]:
    template = tmp_path / "template.yaml"
    template.write_text(
        "version: '1.0'\nproject: {name: template}\nsession: {}\n",
        encoding="utf-8",
    )
    dependency_source = tmp_path / "shared-node-modules"
    dependency_source.mkdir(exist_ok=True)

    def _link_dependencies(root: Path, *, log_path: Path) -> str:
        target = root / "web" / "node_modules"
        target.parent.mkdir(parents=True)
        target.symlink_to(dependency_source, target_is_directory=True)
        return "linked"

    monkeypatch.setattr(
        "zf.autoresearch.orchestrator.ensure_web_dependencies",
        _link_dependencies,
    )
    run_dir = worktree / ".zf" / "autoresearch" / "runs" / "interrupted"
    prepare_worktree(
        AutoresearchRunConfig(
            worktree=worktree,
            config_template=template,
            reuse_worktree=True,
            sync_dirty=False,
        ),
        scenario=resolve_scenario("self-eval-backlog"),
        run_id="interrupted",
        run_dir=run_dir,
    )
    return worktree / "zf.yaml", worktree / "web" / "node_modules"


def test_resident_cleans_interrupted_preparation_before_removing_worktree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, worktree = _git_repo_with_worktree(
        tmp_path,
        branch="experiment/interrupted-clean",
    )
    zf_yaml, dependencies = _prepare_interrupted_resident_worktree(
        tmp_path,
        worktree,
        monkeypatch,
    )
    assert zf_yaml.exists()
    assert dependencies.is_symlink()

    outcome = _finalize_resident_worktree(
        worktree=worktree,
        loop_request_id="loop-interrupted-clean",
    )

    assert outcome["status"] == "cleaned"
    assert outcome["preparation_cleanup"]["status"] == "cleaned"
    assert not worktree.exists()


def test_resident_keeps_candidate_after_cleaning_interrupted_preparation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, worktree = _git_repo_with_worktree(
        tmp_path,
        branch="experiment/interrupted-dirty",
    )
    _prepare_interrupted_resident_worktree(tmp_path, worktree, monkeypatch)
    (worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")

    outcome = _finalize_resident_worktree(
        worktree=worktree,
        loop_request_id="loop-interrupted-dirty",
    )

    assert outcome["status"] == "retained"
    assert outcome["preparation_cleanup"]["status"] == "cleaned"
    assert outcome["dirty_paths"] == ["?? candidate.txt"]
    assert worktree.exists()
