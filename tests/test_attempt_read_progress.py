from pathlib import Path
from types import SimpleNamespace

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_ledger import append_artifact_read
from zf.runtime.attempt_read_progress import required_read_progress
from zf.runtime.orchestrator import Orchestrator


REQUIREMENT = {
    "source_id": "plan-port-spec", "artifact_id": "spec", "artifact_sha256": "a" * 64,
    "json_path": "$", "min_returned_bytes": 1,
}


def _page(start: int, end: int, **overrides):
    return {
        "schema_version": "artifact-read.v1", "attempt_id": "a1", "status": "read",
        **REQUIREMENT, "page_start": start, "page_end": end, "page_total": 100,
        "selected_bytes": 100, "selected_sha256": "b" * 64,
        "returned_bytes": end - start, **overrides,
    }


def test_required_progress_counts_unique_coverage_and_ignores_other_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "zf.runtime.attempt_read_progress.required_reads_for_attempt", lambda *a, **k: [REQUIREMENT],
    )
    for row in [_page(0, 20), _page(0, 20), _page(10, 30), _page(30, 80, source_id="other")]:
        append_artifact_read(tmp_path, row)
    assert required_read_progress(tmp_path, [], attempt_id="a1", operation_id="o1") == 30
    append_artifact_read(tmp_path, _page(30, 70))
    assert required_read_progress(tmp_path, [], attempt_id="a1", operation_id="o1") == 70


def test_progress_nudge_requires_new_coverage_and_closed_turn_even_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "zf.runtime.attempt_read_progress.required_reads_for_attempt", lambda *a, **k: [REQUIREMENT],
    )
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_attempt_recovery.live_task_pipeline_attempt_for_role",
        lambda runtime, role: {"instance_id": role, "task_id": "T1", "dispatch_id": "d1",
                               "attempt_id": "a1", "operation_id": "o1", "status": "sent"},
    )
    log = EventLog(tmp_path / "events.jsonl")
    sent = []
    runtime = SimpleNamespace(
        state_dir=tmp_path, event_log=log, event_writer=EventWriter(log),
        _expected_terminal_event_for_role=lambda role: "dev.build.done",
        _stuck_detectors={}, _stuck_already_reported=set(),
        _set_worker_state=lambda *a, **k: None,
        _inject_terminal_completion_nudge_prompt=lambda **kwargs: sent.append(kwargs),
    )
    role, task = SimpleNamespace(instance_id="dev-1", name="dev"), SimpleNamespace(id="T1")
    append_artifact_read(tmp_path, _page(0, 20))
    invoke = lambda: Orchestrator._request_terminal_completion_nudge(
        runtime, role=role, task=task, dispatch_id="d1", reason="stuck",
    )
    assert invoke() is not None
    append_artifact_read(tmp_path, _page(0, 20))
    log.append(ZfEvent(type="provider.turn.closed", actor="dev-1"))
    assert invoke() is None
    append_artifact_read(tmp_path, _page(20, 40))
    log.append(ZfEvent(type="codex.hook.pre_tool_use", actor="dev-1"))
    assert invoke() is None
    log.append(ZfEvent(type="provider.turn.closed", actor="dev-1"))
    assert invoke() is not None
    del runtime._completion_nudge_requests
    assert invoke() is None
    assert len(sent) == 2
    assert [e.payload["input_read_progress"] for e in log.read_all()
            if e.type == "worker.stuck.recovered"] == [20, 40]
