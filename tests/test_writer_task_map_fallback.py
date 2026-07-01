"""R13 backlog §C: writer fanout task_map template fallback.

R13 had two impl fanouts triggered by task_map.ready; one event carried
``task_map_ref`` and loaded fine, the other had ``task_map_ref=None`` so the
self-referential config ``task_map: ${task_map_ref}`` stayed literal and 404'd
(fanout.cancelled "writer fanout task_map not found: .../${task_map_ref}"). The
loader must fall back to the canonical pdd-scoped artifact.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.core.workflow.lane_pipeline import parse_lane_pipeline
from zf.runtime.writer_fanout_admission import load_writer_task_map


def _write_task_map(state_dir, pdd):
    art = state_dir / "artifacts" / pdd
    art.mkdir(parents=True)
    (art / "task_map.json").write_text(
        json.dumps({
            "feature_id": pdd,
            "tasks": [{"task_id": "T1", "allowed_paths": ["src/a/"]}],
        }),
        encoding="utf-8",
    )


def test_falls_back_to_canonical_when_event_lacks_task_map_ref(tmp_path):
    state_dir = tmp_path / ".zf-cj-min-refactor"
    pdd = "CJMIN-RX"
    _write_task_map(state_dir, pdd)

    # config has the self-referential template; the trigger carries NO task_map_ref
    stage = SimpleNamespace(task_map="${task_map_ref}")
    event = ZfEvent(type="task_map.ready", payload={"pdd_id": pdd})

    loaded = load_writer_task_map(
        stage=stage, event=event, pdd_id=pdd,
        state_dir=state_dir, project_root=tmp_path,
    )
    assert [t["task_id"] for t in loaded.task_items] == ["T1"]
    # task_map_ref now points at the canonical artifact, not a literal ${...}
    assert "${" not in loaded.task_map_ref
    assert loaded.task_map_ref.endswith(f"artifacts/{pdd}/task_map.json")


def test_event_task_map_ref_still_wins_when_present(tmp_path):
    # The sibling fanout whose event DID carry the ref must still use it directly.
    state_dir = tmp_path / ".zf-cj-min-refactor"
    pdd = "CJMIN-RX"
    _write_task_map(state_dir, pdd)

    stage = SimpleNamespace(task_map="${task_map_ref}")
    ref = ".zf-cj-min-refactor/artifacts/CJMIN-RX/task_map.json"
    event = ZfEvent(
        type="task_map.ready", payload={"pdd_id": pdd, "task_map_ref": ref},
    )

    loaded = load_writer_task_map(
        stage=stage, event=event, pdd_id=pdd,
        state_dir=state_dir, project_root=tmp_path,
    )
    assert loaded.task_map_ref == ref
    assert [t["task_id"] for t in loaded.task_items] == ["T1"]


def test_state_dir_relative_artifacts_ref_resolves_from_runtime_state(tmp_path):
    state_dir = tmp_path / ".zf-prod-new"
    task_map = (
        state_dir
        / "artifacts"
        / "fanouts"
        / "fanout-issue-map-evt-1"
        / "issue-plan"
        / "artifacts"
        / "issue-map"
        / "task_map.json"
    )
    task_map.parent.mkdir(parents=True)
    task_map.write_text(
        json.dumps({
            "feature_id": "ISSUE-1",
            "tasks": [{"task_id": "ISSUE-CORE-001", "allowed_paths": ["scripts/hello.py"]}],
        }),
        encoding="utf-8",
    )
    ref = "artifacts/fanouts/fanout-issue-map-evt-1/issue-plan/artifacts/issue-map/task_map.json"
    event = ZfEvent(
        type="task_map.ready",
        payload={"pdd_id": "ISSUE-1", "task_map_ref": ref},
    )

    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map="${task_map_ref}"),
        event=event,
        pdd_id="ISSUE-1",
        state_dir=state_dir,
        project_root=tmp_path,
    )

    assert loaded.task_map_path == task_map
    assert loaded.task_map_ref == ref
    assert [t["task_id"] for t in loaded.task_items] == ["ISSUE-CORE-001"]


def test_gap_only_resume_validates_lane_pipeline_against_full_task_map(tmp_path):
    state_dir = tmp_path / ".zf"
    path = state_dir / "artifacts" / "CANGJIE" / "task_map.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "feature_id": "CANGJIE",
            "tasks": [
                {
                    "task_id": "CJMIN-ASSEMBLY-001",
                    "root_owner_class": "assembly",
                    "allowed_paths": ["package.json"],
                },
                {
                    "task_id": "CANGJIE-GAP-001",
                    "title": "Parity gap",
                    "affinity_tag": "pi-core",
                    "allowed_paths": ["src/gap.ts"],
                },
            ],
        }),
        encoding="utf-8",
    )
    pipeline = parse_lane_pipeline({
        "id": "cj-min-refactor-lane-pipeline",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "affinity_key": "affinity_tag",
        "lane_count": 1,
        "assembly": {"task": "CJMIN-ASSEMBLY-001"},
        "stages": [{"id": "impl"}],
    })
    event = ZfEvent(
        type="task_map.ready",
        payload={
            "pdd_id": "CANGJIE",
            "task_map_ref": ".zf/artifacts/CANGJIE/task_map.json",
            "resume_scope": "gap_tasks_only",
            "task_ids": ["CANGJIE-GAP-001"],
        },
    )

    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map="${task_map_ref}"),
        event=event,
        pdd_id="CANGJIE",
        state_dir=state_dir,
        project_root=tmp_path,
        pipeline_spec=pipeline,
    )

    assert [item["task_id"] for item in loaded.task_items] == ["CANGJIE-GAP-001"]
