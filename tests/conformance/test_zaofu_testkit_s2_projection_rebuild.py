"""STO-09: remove only rebuildable projections, not canonical state/evidence."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest

from tests.testkit.evidence import write_json_atomic
from tests.testkit.model_evidence import ModelEvidence
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.workflow_spine_projection import refresh_spine_projections
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


class PartialProjectionRebuildMismatch(AssertionError):
    """Successful refresh after a single missing projection differs from full replay."""


PROJECTIONS = ("task_attempts.json", "stage_spine.json",
               "workflow_health.json", "workflow_spine.json")


PARTIAL_REBUILD = pytest.mark.xfail(
    strict=True, raises=PartialProjectionRebuildMismatch,
    reason="ZF-TEST-042: shared spine cursor loses or double-counts singly rebuilt projections",
)


def _documents(state: Path) -> dict:
    return {name: json.loads((state / "projections" / name).read_text()) for name in PROJECTIONS}


@pytest.mark.parametrize("removed", ["all", *(
    pytest.param(name, marks=PARTIAL_REBUILD) for name in PROJECTIONS
)])
def test_rebuild_preserves_complete_projection_and_authority(tmp_path: Path, request, removed: str) -> None:
    state = tmp_path / "custom-state"
    attach_runtime_case_evidence(
        request, state,
        given="canonical ledger and projections contain a complete workflow history",
        when=f"remove projection {removed!r} and rebuild from the ledger",
        then="canonical state, evidence and unaffected projections remain authoritative",
        forbidden=["canonical truth deletion", "projection duplication", "state divergence"],
        inputs={"removed_projection": removed},
    )
    state.mkdir()
    TaskStore(state / "kanban.json").add(Task(id="T-ONE", title="seed", status="in_progress"))
    FeatureStore(state / "feature_list.json").add(Feature(id="F-ONE", title="seed"))
    SessionStore(state / "session.yaml").create(str(tmp_path))
    RoleSessionRegistry(state / "role_sessions.yaml", project_root=str(tmp_path)).get_or_create("dev")
    write_json_atomic(state / "required-evidence.json", {"result": "fixture evidence", "task": "T-ONE"})
    log = EventLog(state / "events.jsonl")
    events = [
        ("task.dispatched", {"role": "dev", "dispatch_id": "D-ONE"}),
        ("dev.build.done", {"dispatch_id": "D-ONE", "status": "completed"}),
        ("fanout.started", {"stage_id": "review", "fanout_id": "FAN-ONE"}),
        ("review.rejected", {"stage_id": "review", "workflow_run_id": "RUN-ONE", "reason": "rework"}),
        ("verify.passed", {"stage_id": "verify", "workflow_run_id": "RUN-ONE"}),
    ]
    for i, (event_type, payload) in enumerate(events):
        # This is a ledger/projector test input, not a worker dispatch or a
        # claim that semantic gate evidence has actually passed.
        log.append(ZfEvent(id=f"event-{i}", ts=f"2026-09-14T00:00:0{i}+00:00",
                           type=event_type, task_id="T-ONE", payload=payload))
    assert refresh_spine_projections(state, log)["events_folded"] == len(events)
    baseline = _documents(state)
    assert baseline[PROJECTIONS[0]]["tasks"]["T-ONE"]["attempt_count"] == 1
    assert baseline[PROJECTIONS[1]]["stages"]["review"]["rounds"] == 1
    assert baseline[PROJECTIONS[2]]["counters"]["review.rejected"] == 1
    assert baseline[PROJECTIONS[3]]["runs"]["RUN-ONE"]["milestones"] == 2
    # Normal incremental refresh is the nonempty idempotence control.
    assert refresh_spine_projections(state, log)["events_folded"] == 0
    assert _documents(state) == baseline
    preserved = {p.relative_to(state).as_posix(): p.read_bytes() for p in state.rglob("*")
                 if p.is_file() and "projections" not in p.relative_to(state).parts
                 and p.suffix != ".lock"}
    write_json_atomic(tmp_path / "baseline.json", baseline)
    model = ModelEvidence(
        root=tmp_path, model_name="FixedPartialSpineRebuild", source=Path(__file__),
        settings="all-four positive; each missing file separately; complete JSON comparison",
        reference=lambda: {"removed": removed, "expected": baseline},
    )
    with model.guard("remove-and-refresh", {"removed": removed}):
        for name in PROJECTIONS if removed == "all" else (removed,):
            (state / "projections" / name).unlink()
        stats = refresh_spine_projections(state, log)
        actual = _documents(state)
        mismatch = [name for name in PROJECTIONS if actual[name] != baseline[name]]
        write_json_atomic(tmp_path / "observed.json", {
            "stats": stats, "actual": actual, "mismatched_projections": mismatch,
        })
        assert all((state / name).read_bytes() == body for name, body in preserved.items())
        # Classify only the exact loss/duplication already reproduced. Any
        # additional projection corruption or IO error remains an ordinary red.
        known_fault = json.loads(json.dumps(baseline))
        if removed == "task_attempts.json":
            known_fault["stage_spine.json"]["stages"]["review"]["rounds"] = 2
            known_fault["workflow_health.json"]["counters"]["review.rejected"] = 2
            known_fault["workflow_spine.json"]["runs"]["RUN-ONE"]["milestones"] = 4
        elif removed != "all":
            known_fault[removed] = {key: baseline[removed][key] for key in ("schema_version", "cursor_offset")}
            if removed == "stage_spine.json":
                known_fault[removed]["stages"] = {}
            elif removed == "workflow_health.json":
                known_fault[removed]["counters"] = {}
        expected_folded = len(events) if removed == "task_attempts.json" else 0
        if (removed != "all" and mismatch and actual == known_fault
                and stats["events_folded"] == expected_folded):
            raise PartialProjectionRebuildMismatch(f"removed={removed}; divergent={mismatch}")
        assert actual == baseline
        assert refresh_spine_projections(state, log)["events_folded"] == 0
        assert _documents(state) == baseline


def test_terminal_index_rebuild_preserves_real_archive_and_dependency_queries(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="PRE", title="prerequisite"))
    store.add(Task(id="NEXT", title="dependent", blocked_by=["PRE"]))
    store.update("PRE", status="done")
    expected = [asdict(task) for task in store.list_all_with_archive()]
    assert [task.id for task in store.ready()] == ["NEXT"]
    canonical = {p: p.read_bytes() for p in [store.path, *(tmp_path / "kanban").glob("*.json")]}
    assert len(canonical) == 2
    (tmp_path / "kanban-terminal-index.json").unlink()
    cold = TaskStore(store.path)
    rebuilt = cold.rebuild_terminal_index_from_archive()
    assert rebuilt == {"PRE": next(path.stem for path in canonical if path != store.path)}
    assert [asdict(task) for task in cold.list_all_with_archive()] == expected
    assert [task.id for task in cold.ready()] == ["NEXT"]
    assert cold.get("PRE").status == "done"
    assert all(path.read_bytes() == body for path, body in canonical.items())
