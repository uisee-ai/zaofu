"""Incident references must reach the isolated diagnostic seed."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.autoresearch.loop_types import LoopConfig
from zf.autoresearch.orchestrator import AutoresearchRunConfig, prepare_worktree
from zf.autoresearch.resident import _command_for_request
from zf.autoresearch.scenarios import resolve_scenario
from zf.cli.autoresearch import _real_autoresearch_fn
from zf.core.events.model import ZfEvent
from zf.runtime.autoresearch_invocation import (
    acceptance_payload, build_invocation_request_from_run_manager_event,
    trigger_payload_from_invocation,
)
from zf.autoresearch.loop_requests import build_loop_request_payload
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref, write_sidecar_json


def test_run_manager_evidence_survives_diagnostic_handoff(tmp_path):
    state = tmp_path / "state"
    context = write_sidecar_json(
        state, "diagnostics/run-manager/request/context.json", {"finding": "blocked"},
        kind="diagnostic_trace", schema_version="context.v1", created_by="test",
        access_scope={"visibility": "project"}, required=True,
    )
    read_set = write_sidecar_json(
        state, "diagnostics/run-manager/request/read_set.json", {"refs": ["evt-original"]},
        kind="diagnostic_trace", schema_version="read-set.v1", created_by="test",
        access_scope={"visibility": "project"}, required=True,
    )
    request = ZfEvent(id="rm-request", type="run.manager.autoresearch.requested", payload={
        "request_id": "request", "workflow_run_id": "run-1", "run_id": "run-1",
        "trace_id": "trace-1", "recovery_case_id": "case-1", "task_id": "TASK-1",
        "failure_class": "no_progress_breaker", "source_event_ids": ["evt-original"],
        "source_ref": "events.jsonl#evt-original", "context_ref": context,
        "read_set_ref": read_set,
    })
    invocation = build_invocation_request_from_run_manager_event(request, events=[request])
    assert invocation is not None
    accepted = acceptance_payload(invocation.payload, source_event_id=invocation.id)
    trigger = trigger_payload_from_invocation(invocation.payload, source_event_id="accepted")
    loop = build_loop_request_payload(trigger, source_event_id="trigger")
    command = _command_for_request(loop, worktree=tmp_path / "wt", state_dir=state, output_root=tmp_path)
    seed = json.loads(command[command.index("--seed-text") + 1])

    for payload in (invocation.payload, accepted, trigger, loop, seed):
        assert payload["context_ref"] == context
        assert payload["read_set_ref"] == read_set
        assert payload["workflow_run_id"] == "run-1"
        assert payload["run_id"] == "run-1"
        assert payload["trace_id"] == "trace-1"
        assert payload["recovery_case_id"] == "case-1"
        assert payload["failure_class"] == "no_progress_breaker"
        assert payload["source_event_ids"] == ["evt-original"]
    assert seed["task_id"] == "TASK-1"
    assert seed["source_insight_ref"] == context["ref"]
    assert context["ref"] in seed["evidence_paths"]
    assert all(not path.startswith("{") for path in seed["evidence_paths"])
    assert hydrate_sidecar_ref(state, seed["context_ref"]).payload == {"finding": "blocked"}
    tampered = {**seed["context_ref"], "sha256": "0" * 64}
    with pytest.raises(SidecarRefError, match="hash mismatch"):
        hydrate_sidecar_ref(state, tampered)


def test_unrecognized_diagnostic_descriptor_is_not_promoted_to_a_sidecar():
    for bad_ref in (
        {"ref": "arbitrary.txt", "sha256": "bad"},
        {"ref_schema_version": "sidecar-ref.v1", "ref": "diagnostics/a.json", "sha256": "bad"},
        {"ref_schema_version": "sidecar-ref.v1", "ref": "../outside", "sha256": "a" * 64},
    ):
        request = ZfEvent(type="run.manager.autoresearch.requested", payload={
            "request_id": "request", "context_ref": bad_ref,
        })
        invocation = build_invocation_request_from_run_manager_event(request, events=[request])
        assert invocation is not None
        assert "context_ref" not in invocation.payload
        assert invocation.payload["evidence_paths"] == []
        assert invocation.payload["source_insight_ref"] == "projections/run_manager.json#run_context_bundle"


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
