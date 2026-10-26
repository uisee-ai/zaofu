"""Incident references must reach the isolated diagnostic seed."""
from pathlib import Path
from types import SimpleNamespace

from zf.autoresearch.loop_types import LoopConfig
from zf.autoresearch.orchestrator import AutoresearchRunConfig, prepare_worktree
from zf.autoresearch.resident import _command_for_request
from zf.autoresearch.scenarios import resolve_scenario
from zf.cli.autoresearch import _real_autoresearch_fn


def test_resident_passes_incident_refs_without_copying_arbitrary_payload(tmp_path):
    command = _command_for_request({
        "loop_request_id": "ar-incident", "source_event_id": "evt-failure",
        "evidence_paths": ["events/incident.json"], "password": "do-not-copy",
    }, worktree=tmp_path / "wt", state_dir=tmp_path / "state", output_root=tmp_path)
    context = command[command.index("--seed-text") + 1]
    assert "evt-failure" in context and "events/incident.json" in context
    assert str(tmp_path / "state") in context
    assert "do-not-copy" not in context
    scenarios = command[command.index("--scenarios") + 1:command.index("--worktree")]
    assert scenarios == ["incident-reproduction"]


def test_explicit_stuck_exercise_remains_opt_in(tmp_path):
    command = _command_for_request({
        "loop_request_id": "exercise", "scenarios": ["controlled-stuck-recovery"],
    }, worktree=tmp_path, state_dir=tmp_path, output_root=tmp_path)
    assert command[command.index("--scenarios") + 1] == "controlled-stuck-recovery"
    assert resolve_scenario("controlled-stuck-recovery").requires_worker_stuck_injection


def test_loop_forwards_context_to_actual_runner(tmp_path, monkeypatch):
    import zf.autoresearch.loop_iteration as iteration
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\nproject: {name: diagnostic, state_dir: diagnostic-state}\nsession: {}\n"
    )
    seen = []
    def run(cfg):
        seen.append(cfg)
        return SimpleNamespace(worktree=tmp_path, status="failed", tasks_done=0, expected_done=1,
                               fatal_event=None, report_path="report", review_gate={})
    monkeypatch.setattr(iteration, "run_autoresearch", run)
    monkeypatch.setattr(iteration, "cleanup_autoresearch_run", lambda *a, **kw: None)
    result = _real_autoresearch_fn(scenario="incident-reproduction", run_id="diagnose",
                         cfg=LoopConfig(worktree=tmp_path, seed_text="event:evt-failure"))
    assert seen[0].diagnostic_context == "event:evt-failure"
    assert result["state_dir"] == str(tmp_path / "diagnostic-state")


def test_prepare_seed_contains_context_and_benchmark(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    template = tmp_path / "template.yaml"
    template.write_text("version: '1.0'\nproject: {name: diagnostic}\nsession: {}\n")
    scenario = resolve_scenario("incident-reproduction")
    assert not scenario.requires_worker_stuck_injection
    path = prepare_worktree(AutoresearchRunConfig(
        worktree=worktree, config_template=template, reuse_worktree=True,
        sync_dirty=False, flow_kind="", diagnostic_context="event:evt-failure",
    ), scenario=scenario, run_id="diagnose", run_dir=tmp_path / "run")
    assert scenario.seed_text.strip() in path.read_text()
    assert "event:evt-failure" in path.read_text()
