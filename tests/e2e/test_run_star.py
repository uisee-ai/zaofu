from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.e2e.run_star import (
    REPO_ROOT,
    SCENARIOS,
    _provider_config,
    _seed_writer_tasks,
    _write_writer_task_map,
    scenario_spec,
    wait_for_star,
)
from tests.e2e.star_smoke_support import (
    _init_state,
    _watcher_preflight_failure,
    start_watcher,
)
from zf.core.config.loader import load_config
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.core.workflow.inspection import build_workflow_inspection_report
from zf.runtime.writer_fanout_admission import (
    admit_writer_fanout,
    load_writer_task_map,
)


def test_star_scenarios_point_to_current_examples() -> None:
    assert set(SCENARIOS) == {"verifier", "review", "writer", "writer-conflict"}
    for scenario in SCENARIOS.values():
        assert scenario.config.exists(), scenario.config
        assert scenario.stage_id
        assert scenario.trigger_event
        assert scenario.wait_event in {"fanout.aggregate.completed", "fanout.serialize"}


@pytest.mark.parametrize("scenario_name", sorted(SCENARIOS))
def test_star_examples_declare_external_entrypoints_without_stop_diagnostics(
    scenario_name: str,
    tmp_path: Path,
) -> None:
    scenario = scenario_spec(scenario_name)
    cfg = load_config(scenario.config)

    report = build_workflow_inspection_report(
        cfg,
        project_root=REPO_ROOT,
        state_dir=tmp_path / ".zf",
    )

    stop_kinds = {
        str(item.get("kind") or "")
        for item in report["diagnostics"]
        if item.get("severity") == "STOP"
    }
    assert "trigger_without_producer" not in stop_kinds
    assert "terminal_event_without_producer" not in stop_kinds


def test_provider_config_rewrites_mock_roles_to_real_provider(monkeypatch) -> None:
    monkeypatch.setattr("tests.e2e.run_star._candidate_base_ref", lambda: "HEAD")
    scenario = scenario_spec("verifier")
    data = yaml.safe_load(scenario.config.read_text(encoding="utf-8"))

    rendered = _provider_config(
        data,
        scenario=scenario,
        backend="codex",
        permission_mode="bypass",
        model="gpt-test",
        budget_usd=3.5,
    )

    assert rendered["project"]["state_dir"] == ".zf"
    assert rendered["session"]["tmux_session"].startswith("zf-star-verifier-")
    assert rendered["global_budget_usd"] == 3.5
    assert rendered["runtime"]["git"]["candidate_base_ref"] == "HEAD"
    assert rendered["runtime"]["git"]["writer_branch_prefix"].startswith(
        "worker/star-verifier-"
    )
    assert rendered["runtime"]["git"]["task_ref_prefix"].startswith(
        "task/star-verifier-"
    )
    assert rendered["runtime"]["git"]["candidate_branch_prefix"].startswith(
        "candidate/star-verifier-"
    )
    for role in rendered["roles"]:
        assert role["backend"] == "codex"
        assert role["permission_mode"] == "bypass"
        assert role["transport"] == "tmux"
        assert role["model"] == "gpt-test"


def test_writer_task_map_and_task_contracts_are_seeded(tmp_path: Path) -> None:
    scenario = scenario_spec("writer")
    _init_state(tmp_path)

    task_map = _write_writer_task_map(tmp_path, scenario)
    _seed_writer_tasks(tmp_path, scenario)

    data = json.loads(task_map.read_text(encoding="utf-8"))
    assert [item["task_id"] for item in data["tasks"]] == [
        "TASK-STAR-AUTH",
        "TASK-STAR-GATEWAY",
    ]
    assert all("_exclusive_files" not in item for item in data["tasks"])
    store = TaskStore(tmp_path / ".zf" / "kanban.json")
    tasks = {task.id: task for task in store.list_all()}
    assert tasks["TASK-STAR-AUTH"].contract.exclusive_files == [
        "star-smoke/auth/README.md",
    ]
    auth_refs = tasks["TASK-STAR-AUTH"].contract.evidence_contract["source_refs"]
    assert auth_refs["task_map_ref"] == ".zf/artifacts/PDD-STAR-WRITER/task_map.json"
    assert tasks["TASK-STAR-AUTH"].contract.behavior
    assert tasks["TASK-STAR-AUTH"].contract.verification
    assert tasks["TASK-STAR-GATEWAY"].contract.exclusive_files == [
        "star-smoke/gateway/README.md",
    ]
    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map=".zf/artifacts/${pdd_id}/task_map.json"),
        event=ZfEvent(
            type="task_map.ready",
            actor="e2e",
            payload={"pdd_id": scenario.pdd_id},
        ),
        pdd_id=scenario.pdd_id,
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
    )
    admission = admit_writer_fanout(
        task_store=store,
        loaded=loaded,
    )
    assert admission.passed is True


def test_writer_conflict_seed_uses_contract_conflict_not_bad_task_map(tmp_path: Path) -> None:
    scenario = scenario_spec("writer-conflict")
    _init_state(tmp_path)

    task_map = _write_writer_task_map(tmp_path, scenario)
    _seed_writer_tasks(tmp_path, scenario)

    data = json.loads(task_map.read_text(encoding="utf-8"))
    allowed = [tuple(item["allowed_paths"]) for item in data["tasks"]]
    assert len(set(allowed)) == len(allowed)
    store = TaskStore(tmp_path / ".zf" / "kanban.json")
    tasks = {task.id: task for task in store.list_all()}
    assert tasks["TASK-STAR-AUTH"].contract.exclusive_files == [
        "star-smoke/shared-conflict.txt",
    ]
    assert tasks["TASK-STAR-GATEWAY"].contract.exclusive_files == [
        "star-smoke/shared-conflict.txt",
    ]


def test_wait_for_star_accepts_configured_terminal_event(tmp_path: Path) -> None:
    scenario = scenario_spec("writer-conflict")
    state = tmp_path / ".zf"
    state.mkdir()
    event = {
        "type": "fanout.serialize",
        "payload": {
            "stage_id": scenario.stage_id,
            "fanout_id": "fanout-test",
            "status": "serialized",
        },
    }
    (state / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

    terminal, timed_out = wait_for_star(tmp_path, scenario, timeout_s=1)

    assert timed_out is False
    assert terminal == event


def test_watcher_preflight_failure_extracts_log_tail(tmp_path: Path) -> None:
    log_path = tmp_path / ".zf" / "logs" / "watcher.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "Error: workflow preflight failed:\n"
        "  - STOP trigger_without_producer: candidate.ready\n"
        "Workflow inspect artifacts: .zf/config/workflow-inspect.json\n",
        encoding="utf-8",
    )

    reason = _watcher_preflight_failure(log_path)

    assert "workflow preflight failed before loop.started" in reason
    assert "trigger_without_producer" in reason


def test_start_watcher_returns_negative_when_zf_start_exits_before_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class FakePopen:
        pid = 4242

        def __init__(self, cmd, **kwargs):  # noqa: ANN001, ANN003
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

        def poll(self) -> int:
            return 2

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr("tests.e2e.star_smoke_support._count_event", lambda *a, **k: 0)
    times = iter([0.0, 1.0])
    monkeypatch.setattr("tests.e2e.star_smoke_support.time.time", lambda: next(times))
    monkeypatch.setattr("tests.e2e.star_smoke_support.time.sleep", lambda *_: None)
    _init_state(tmp_path)

    result = start_watcher(tmp_path)

    assert result == -2
    assert captured["cmd"] == ["zf", "start"]
