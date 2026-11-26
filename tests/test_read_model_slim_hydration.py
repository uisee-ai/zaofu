"""P0-C — slim hydration from the read-model index (no per-event raw reads).

graph (workflow_graph) and trace grouping hydrated every matching event by
seeking the raw bytes per event (hydrate_event_at), which cost 40-50s on large
logs. They only need structural fields + a few slim refs, so they now read
straight from the indexed columns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import EventSigningConfig, SecurityConfig, ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.web.projections import read_model


def _write_line(path: Path, event: ZfEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(event.to_json() + "\n")


def _seed(state_dir: Path) -> None:
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="fanout.started", id="evt-1", actor="orch", task_id="T-1",
        correlation_id="trace-a", payload={"fanout_id": "fo-1", "summary": "kick"}))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="orchestrator.decision.recorded", id="evt-2", actor="orch", task_id="T-1",
        correlation_id="trace-a", payload={"run_id": "run-1"}))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="dev.build.done", id="evt-3", actor="dev-1", task_id="T-2",
        correlation_id="trace-b", payload={"summary": "built"}))


@pytest.mark.parametrize("by_ref", [True, False])
def test_raw_slice_reuses_one_project_decoder_only_for_this_query(tmp_path, monkeypatch, by_ref):
    state = tmp_path / "project-state"
    key = "ZF_TEST_SLICE_SIGNING"
    cfg = ZfConfig(security=SecurityConfig(event_signing=EventSigningConfig(enabled=True, secret_env=key)))
    monkeypatch.setenv(key, "original-fixture")
    log = event_log_from_project(state, config=cfg)
    log.append(ZfEvent(type="task.created", id="older", task_id="T-1", payload={"full_body": "archive"}))
    archive = state / "events" / "2026-06-21.jsonl"
    archive.parent.mkdir(exist_ok=True)
    log.path.rename(archive)
    log.append(ZfEvent(type="dev.build.done", id="newer", task_id="T-1", payload={"full_body": "active"}))
    read_model.rebuild(state, config=cfg)

    created = []
    original = EventLog._build_index

    def record_creation(event_log):
        created.append(event_log.path)
        return original(event_log)

    monkeypatch.setattr(EventLog, "_build_index", record_creation)

    def query(task="T-1", slim=False):
        if by_ref:
            return read_model.hydrate_events_by_ref(state, ref_kind="task", ref_id=task, config=cfg, slim=slim)
        return read_model.hydrate_events(state, task_id=task, config=cfg, slim=slim)

    assert query("unknown") == []
    assert [e.id for e in query(slim=True)] == ["older", "newer"]
    assert created == []
    assert [e.payload["full_body"] for e in query()] == ["archive", "active"]
    assert created == [log.path]
    monkeypatch.setenv(key, "different-fixture")
    assert [e.type for e in query()] == ["event.malformed", "event.malformed"]
    assert created == [log.path, log.path]


def test_empty_and_missing_raw_rows_do_not_require_a_signer(tmp_path, monkeypatch):
    from zf.core.events.segments import hydrate_indexed_events

    key = "ZF_TEST_SLICE_UNCONFIGURED"
    monkeypatch.delenv(key, raising=False)
    config = ZfConfig(security=SecurityConfig(event_signing=EventSigningConfig(enabled=True, secret_env=key)))
    (tmp_path / "events.jsonl").write_bytes(b"\n")
    positions = [("events/2026-06-21.jsonl", 0, 64), ("events.jsonl", 0, 1), ("events.jsonl", 100, 4)]
    assert hydrate_indexed_events(tmp_path, positions, config=config) == []


def test_slim_hydration_matches_structural_fields(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _seed(state_dir)
    read_model.rebuild(state_dir)

    full = read_model.hydrate_events(state_dir)
    slim = read_model.hydrate_events(state_dir, slim=True)

    assert [e.id for e in slim] == [e.id for e in full]
    assert [e.type for e in slim] == [e.type for e in full]
    assert [e.task_id for e in slim] == [e.task_id for e in full]
    assert [e.correlation_id for e in slim] == [e.correlation_id for e in full]
    # slim keep-set carries fanout_id / run_id (what graph topology reads)
    by_id = {e.id: e for e in slim}
    assert by_id["evt-1"].payload.get("fanout_id") == "fo-1"
    assert by_id["evt-2"].payload.get("run_id") == "run-1"


def test_slim_hydration_type_filter(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _seed(state_dir)
    read_model.rebuild(state_dir)

    only = read_model.hydrate_events(state_dir, types=["fanout.started"], slim=True)
    assert [e.id for e in only] == ["evt-1"]


def test_slim_hydration_keeps_tool_call_identity_and_outcome(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="agent.session.part.delta", id="evt-tool", actor="web",
        payload={"message_type": "tool_result", "tool_call_id": "call-1", "status": "failed"},
    ))
    read_model.rebuild(state_dir)
    tool = read_model.hydrate_events(state_dir, slim=True)[0]
    assert tool.payload["tool_call_id"] == "call-1"
    assert tool.payload["status"] == "failed"
