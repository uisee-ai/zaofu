"""P0-C — slim hydration from the read-model index (no per-event raw reads).

graph (workflow_graph) and trace grouping hydrated every matching event by
seeking the raw bytes per event (hydrate_event_at), which cost 40-50s on large
logs. They only need structural fields + a few slim refs, so they now read
straight from the indexed columns.
"""

from __future__ import annotations

from pathlib import Path

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
