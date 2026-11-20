"""R13 backlog §C: writer fanout task_map template fallback.

R13 had two impl fanouts triggered by task_map.ready; one event carried
``task_map_ref`` and loaded fine, the other had ``task_map_ref=None`` so the
self-referential config ``task_map: ${task_map_ref}`` stayed literal and 404'd
(fanout.cancelled "writer fanout task_map not found: .../${task_map_ref}"). The
loader must fall back to the canonical pdd-scoped artifact.
"""

from __future__ import annotations

import json
import subprocess
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


def test_gap_task_map_amend_is_loaded_as_replan(tmp_path):
    state_dir = tmp_path / ".zf"
    pdd = "ISSUE-GAP"
    _write_task_map(state_dir, pdd)
    ref = f".zf/artifacts/{pdd}/task_map.json"

    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map="${task_map_ref}"),
        event=ZfEvent(
            type="task_map.ready",
            payload={
                "pdd_id": pdd,
                "task_map_ref": ref,
                "amend_of": ".zf/artifacts/ISSUE-GAP/task-map-v1.json",
                "resume_scope": "gap_tasks_only",
                "task_ids": ["T1"],
            },
        ),
        pdd_id=pdd,
        state_dir=state_dir,
        project_root=tmp_path,
    )

    assert loaded.is_replan is True


def test_initial_task_map_is_not_loaded_as_replan(tmp_path):
    state_dir = tmp_path / ".zf"
    pdd = "ISSUE-INITIAL"
    _write_task_map(state_dir, pdd)

    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map="${task_map_ref}"),
        event=ZfEvent(
            type="task_map.ready",
            payload={
                "pdd_id": pdd,
                "task_map_ref": f".zf/artifacts/{pdd}/task_map.json",
            },
        ),
        pdd_id=pdd,
        state_dir=state_dir,
        project_root=tmp_path,
    )

    assert loaded.is_replan is False


def test_loader_preserves_planner_target_commit_as_writer_dispatch_base(tmp_path):
    state_dir = tmp_path / ".zf"
    pdd = "PRD-BASELINE"
    target_commit = "a" * 40
    _write_task_map(state_dir, pdd)
    task_map = state_dir / "artifacts" / pdd / "task_map.json"
    payload = json.loads(task_map.read_text(encoding="utf-8"))
    payload["target_commit"] = target_commit
    task_map.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map="${task_map_ref}"),
        event=ZfEvent(
            type="task_map.ready",
            payload={
                "pdd_id": pdd,
                "task_map_ref": f".zf/artifacts/{pdd}/task_map.json",
            },
        ),
        pdd_id=pdd,
        state_dir=state_dir,
        project_root=tmp_path,
    )

    assert loaded.dispatch_base_commit == target_commit


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


def test_gap_only_resume_loads_requested_task_from_full_task_map(tmp_path):
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
    assert loaded.is_replan is True


def test_gap_only_successor_map_rebinds_stale_rework_task_id(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    state_dir = tmp_path / ".zf"
    path = state_dir / "artifacts" / "PRD" / "task_map.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "feature_id": "PRD",
            "resume_scope": "gap_tasks_only",
            "tasks": [{
                "task_id": "RELEASE-R7",
                "title": "Continue verified release closure",
                "allowed_paths": ["scripts/release/**"],
                "supersedes_task_ids": ["RELEASE-R5"],
                "base_commit": head,
                "source_refs": [f"git:{head}"],
            }],
        }),
        encoding="utf-8",
    )
    event = ZfEvent(
        type="task_map.ready",
        payload={
            "pdd_id": "PRD",
            "task_map_ref": ".zf/artifacts/PRD/task_map.json",
            "resume_scope": "failed_children_only",
            "task_ids": ["RELEASE-R2"],
            "rework_of": "evt-old-contract-blocker",
        },
    )

    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map="${task_map_ref}"),
        event=event,
        pdd_id="PRD",
        state_dir=state_dir,
        project_root=tmp_path,
    )

    assert loaded.requested_task_ids == ["RELEASE-R7"]
    assert [item["task_id"] for item in loaded.task_items] == ["RELEASE-R7"]


def test_failed_and_downstream_scope_maps_unique_successor_and_keeps_audit(
    tmp_path,
):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    state_dir = tmp_path / ".zf"
    path = state_dir / "artifacts" / "PRD" / "task_map.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "feature_id": "PRD",
            "resume_scope": "failed_children_and_downstream",
            "metadata": {
                "dispatch_delta_task_ids": ["AC8-R2", "RELEASE-AUDIT"],
                "target_commit": head,
            },
            "tasks": [
                {
                    "task_id": "ALREADY-DONE",
                    "title": "retained historical slice",
                    "allowed_paths": ["src/history/**"],
                },
                {
                    "task_id": "AC8-R2",
                    "title": "replace failed AC8 receipt",
                    "allowed_paths": ["scripts/ac8/**"],
                    "supersedes_task_ids": ["AC8-R1"],
                    "base_commit": head,
                    "source_refs": [f"git:{head}"],
                },
                {
                    "task_id": "RELEASE-AUDIT",
                    "title": "read-only release audit",
                    "allowed_paths": [],
                },
            ],
        }),
        encoding="utf-8",
    )
    event = ZfEvent(
        type="task_map.ready",
        payload={
            "pdd_id": "PRD",
            "task_map_ref": ".zf/artifacts/PRD/task_map.json",
            "resume_scope": "failed_children_and_downstream",
            "task_ids": ["AC8-R1", "RELEASE-AUDIT"],
            "rework_of": "plan-rejected-1",
        },
    )

    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map="${task_map_ref}"),
        event=event,
        pdd_id="PRD",
        state_dir=state_dir,
        project_root=tmp_path,
    )

    assert loaded.requested_task_ids == ["AC8-R2", "RELEASE-AUDIT"]
    assert [item["task_id"] for item in loaded.task_items] == [
        "AC8-R2",
        "RELEASE-AUDIT",
    ]


def test_gap_only_resume_skips_global_lane_pipeline_root_owner_gate(tmp_path):
    state_dir = tmp_path / ".zf"
    path = state_dir / "artifacts" / "PRD" / "task_map.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "feature_id": "PRD",
            "tasks": [
                {
                    "task_id": "PRD-BASE-001",
                    "title": "base",
                    "allowed_paths": ["app/**"],
                    "allowed_paths_reason": "feature code",
                    "acceptance": ["base"],
                    "verify_commands": ["npm test"],
                },
                {
                    "task_id": "PRD-GAP-001",
                    "title": "Gap",
                    "affinity_tag": "web",
                    "allowed_paths": ["app/**"],
                    "allowed_paths_reason": "gap task",
                    "acceptance": ["gap"],
                    "verify_commands": ["npm test"],
                },
            ],
        }),
        encoding="utf-8",
    )
    pipeline = parse_lane_pipeline({
        "id": "prd-lanes",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "affinity_key": "affinity_tag",
        "lane_count": 1,
        "assembly": "none",
        "stages": [{"id": "impl"}],
    })
    event = ZfEvent(
        type="task_map.ready",
        payload={
            "pdd_id": "PRD",
            "task_map_ref": ".zf/artifacts/PRD/task_map.json",
            "resume_scope": "gap_tasks_only",
            "task_ids": ["PRD-GAP-001"],
        },
    )

    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map="${task_map_ref}"),
        event=event,
        pdd_id="PRD",
        state_dir=state_dir,
        project_root=tmp_path,
        pipeline_spec=pipeline,
    )

    assert [item["task_id"] for item in loaded.task_items] == ["PRD-GAP-001"]
