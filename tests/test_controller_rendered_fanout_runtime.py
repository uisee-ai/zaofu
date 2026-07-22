"""Runtime regression for rendered controller lane fanout.

This covers the 2026-07-07 PRD failure class: config render output must be
directly runnable, keep affinity lane queue semantics, and avoid falling back
to non-affinity writer fanout cancellation when task_count > lane_count.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import load_config
from zf.core.config.render import renderable_config_to_primitive
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_DIR = ROOT / "examples" / "prod" / "controller"


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((str(role_name), Path(briefing_path), str(prompt), context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "branch", "-M", "main")
    return _git(root, "rev-parse", "HEAD")


def _commit_file(workdir: Path, file_name: str, content: str) -> str:
    (workdir / file_name).parent.mkdir(parents=True, exist_ok=True)
    (workdir / file_name).write_text(content, encoding="utf-8")
    _git(workdir, "add", file_name)
    _git(workdir, "commit", "-q", "-m", f"complete {file_name}")
    return _git(workdir, "rev-parse", "HEAD")


def _task_items(kind: str) -> list[dict]:
    scopes = ["api", "ui", "state", "tests"]
    return [
        {
            "task_id": f"TASK-{kind.upper()}-{index}",
            "title": f"{kind} {scope}",
            "scope": scope,
            "affinity_tag": f"{kind}-{scope}",
            "allowed_paths": [f"{kind}-{scope}.txt"],
            "verification": f"test -f {kind}-{scope}.txt",
            "payload": {
                "instruction": f"Create {kind}-{scope}.txt",
                "affinity_tag": f"{kind}-{scope}",
            },
        }
        for index, scope in enumerate(scopes, start=1)
    ]


def _render_controller(source: Path, output: Path) -> Path:
    rendered = renderable_config_to_primitive(load_config(source))
    output.write_text(
        yaml.safe_dump(rendered, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return output


def _manifest(state_dir: Path, fanout_id: str) -> dict:
    return json.loads(
        (state_dir / "fanouts" / fanout_id / "manifest.json").read_text(
            encoding="utf-8",
        )
    )


@pytest.mark.parametrize(
    ("kind", "yaml_name", "initial_roles", "released_role"),
    [
        ("prd", "prd-fanout-v3.yaml", ["dev-lane-0", "dev-lane-1"], "dev-lane-0"),
        (
            "issue",
            "issue-fanout-v3.yaml",
            ["fix-lane-0"],
            "fix-lane-0",
        ),
        (
            "refactor",
            "refactor-lane-v3.yaml",
            ["dev-lane-0", "dev-lane-1"],
            "dev-lane-0",
        ),
    ],
)
def test_rendered_controller_queue_releases_next_impl_task(
    tmp_path: Path,
    kind: str,
    yaml_name: str,
    initial_roles: list[str],
    released_role: str,
) -> None:
    project = tmp_path / kind / "project"
    _init_repo(project)
    rendered = _render_controller(
        CONTROLLER_DIR / yaml_name,
        tmp_path / kind / "zf.yaml",
    )
    config = load_config(rendered)
    config.project.root = str(project)
    config.project.state_dir = str(project / f".zf-{kind}-rendered-sim")
    config.workflow.plan_approval_enabled = False
    config.runtime.git.candidate_base_ref = "main"
    state_dir = Path(config.project.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    task_map_ref = f"{state_dir.name}/artifacts/F-SIM/task_map.json"
    task_map = project / task_map_ref
    task_map.parent.mkdir(parents=True, exist_ok=True)
    items = _task_items(kind)
    task_map.write_text(
        json.dumps({"schema_version": "task-map.v1", "tasks": items}),
        encoding="utf-8",
    )

    store = TaskStore(state_dir / "kanban.json")
    for item in items:
        store.add(Task(
            id=item["task_id"],
            title=item["title"],
            status="backlog",
            contract=TaskContract(
                feature_id="F-SIM",
                scope=item["allowed_paths"],
                behavior=item["payload"]["instruction"],
                verification=item["verification"],
                evidence_contract={"source_refs": {"task_map_ref": task_map_ref}},
            ),
        ))

    transport = _RecordingTransport()
    orchestrator = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    orchestrator.run_once(events=[ZfEvent(
        type="task_map.ready",
        actor="sim",
        correlation_id=f"trace-{kind}",
        payload={"pdd_id": "F-SIM", "task_map_ref": task_map_ref},
    )])

    log = EventLog(state_dir / "events.jsonl")
    events = log.read_all()
    impl_stage = next(
        stage.id for stage in config.workflow.stages
        if stage.topology == "fanout_writer_scoped" and stage.id.endswith("-impl")
    )
    verify_stage = next(
        stage.id for stage in config.workflow.stages
        if stage.id.endswith("-verify")
    )
    started = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == impl_stage
    ]
    dispatched = [
        event for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("stage_id") == impl_stage
    ]
    queued = [
        event for event in events
        if event.type == "fanout.child.queued"
        and event.payload.get("stage_id") == impl_stage
    ]

    assert len(started) == 1
    assert [item[0] for item in transport.sent] == initial_roles
    assert len(dispatched) == len(initial_roles)
    assert len(queued) == len(items) - len(initial_roles)
    assert not [event for event in events if event.type == "fanout.cancelled"]

    fanout_id = started[0].payload["fanout_id"]
    child = next(
        item for item in _manifest(state_dir, fanout_id)["children"]
        if item.get("status") == "dispatched"
    )
    source_commit = _commit_file(
        Path(child["workdir"]),
        child["payload"]["allowed_paths"][0],
        kind,
    )
    orchestrator.run_once(events=[ZfEvent(
        type="dev.build.done",
        actor=child["role_instance"],
        task_id=child["task_id"],
        correlation_id=f"trace-{kind}",
        payload={
            "fanout_id": fanout_id,
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "dispatch_id": child["run_id"],
            "pdd_id": "F-SIM",
            "source_commit": source_commit,
            "source_branch": child["source_branch"],
            "workdir": child["workdir"],
        },
    )])

    after = log.read_all()
    queued_task_ids = {event.payload["task_id"] for event in queued}
    assert [
        event for event in after
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == verify_stage
    ]
    reassigned = [
        event for event in after
        if event.type == "fanout.slot.assigned"
        and event.payload.get("task_id") in queued_task_ids
    ]
    assert [event.payload["role_instance"] for event in reassigned] == [released_role]
