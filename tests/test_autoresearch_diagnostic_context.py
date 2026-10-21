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


def test_loop_forwards_context_to_actual_runner(tmp_path, monkeypatch):
    import zf.cli.autoresearch as cli
    seen = []
    def run(cfg):
        seen.append(cfg)
        return SimpleNamespace(status="failed", tasks_done=0, expected_done=1,
                               fatal_event=None, report_path="report", review_gate={})
    monkeypatch.setattr(cli, "run_autoresearch", run)
    monkeypatch.setattr(cli, "cleanup_autoresearch_run", lambda *a, **kw: None)
    _real_autoresearch_fn(scenario="controlled-stuck-recovery", run_id="diagnose",
                         cfg=LoopConfig(worktree=tmp_path, seed_text="event:evt-failure"))
    assert seen[0].diagnostic_context == "event:evt-failure"


def test_prepare_seed_contains_context_and_benchmark(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    template = tmp_path / "template.yaml"
    template.write_text("version: '1.0'\nproject: {name: diagnostic}\nsession: {}\n")
    scenario = resolve_scenario("controlled-stuck-recovery")
    path = prepare_worktree(AutoresearchRunConfig(
        worktree=worktree, config_template=template, reuse_worktree=True,
        sync_dirty=False, flow_kind="", diagnostic_context="event:evt-failure",
    ), scenario=scenario, run_id="diagnose", run_dir=tmp_path / "run")
    assert scenario.seed_text.strip() in path.read_text()
    assert "event:evt-failure" in path.read_text()
