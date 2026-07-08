"""批A(返工膨胀根治):A1 `.zf` 双语义 / A2 blocked_human 事件出口 /
A5 defer 分级冷却。场景全部来自 prd-goal e2e 实弹。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent


# ---------- A1:admission 字面 project_root 回退 ----------

def _task_map_payload() -> dict:
    return {"tasks": [{
        "task_id": "T-1", "title": "t", "description": "d",
        "allowed_paths": ["app/x.py"],
        "verification": ["pytest -q"],
        "acceptance_criteria": ["ok"],
    }]}


def test_a1_literal_project_zf_fallback(tmp_path: Path) -> None:
    # planner 按字面写进 project/.zf/,而 state_dir 名为 .zf-other
    from zf.runtime.writer_fanout_admission import load_writer_task_map

    project = tmp_path
    state_dir = project / ".zf-other"
    state_dir.mkdir()
    literal = project / ".zf" / "artifacts" / "default"
    literal.mkdir(parents=True)
    (literal / "task_map.json").write_text(
        json.dumps(_task_map_payload()), encoding="utf-8",
    )
    event = ZfEvent(type="task_map.ready", actor="zf-cli",
                    payload={"task_map_ref": ".zf/artifacts/default/task_map.json",
                             "pdd_id": "default"})
    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map=""),
        event=event, pdd_id="default",
        state_dir=state_dir, project_root=project,
    )
    assert loaded is not None  # 不再抛 task_map not found


def test_a1_state_dir_alias_still_primary(tmp_path: Path) -> None:
    from zf.runtime.writer_fanout_admission import load_writer_task_map

    project = tmp_path
    state_dir = project / ".zf-other"
    target = state_dir / "artifacts" / "default"
    target.mkdir(parents=True)
    (target / "task_map.json").write_text(
        json.dumps(_task_map_payload()), encoding="utf-8",
    )
    event = ZfEvent(type="task_map.ready", actor="zf-cli",
                    payload={"task_map_ref": ".zf/artifacts/default/task_map.json",
                             "pdd_id": "default"})
    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map=""),
        event=event, pdd_id="default",
        state_dir=state_dir, project_root=project,
    )
    assert loaded is not None


def test_a1_missing_everywhere_still_fail_closed(tmp_path: Path) -> None:
    from zf.runtime.writer_fanout_admission import load_writer_task_map

    event = ZfEvent(type="task_map.ready", actor="zf-cli",
                    payload={"task_map_ref": ".zf/artifacts/default/task_map.json",
                             "pdd_id": "default"})
    with pytest.raises(RuntimeError, match="task_map not found"):
        load_writer_task_map(
            stage=SimpleNamespace(task_map=""),
            event=event, pdd_id="default",
            state_dir=tmp_path / ".zf-other", project_root=tmp_path,
        )


# ---------- A2:外部 worker.state.changed 进内存 ----------

def test_a2_reactor_applies_external_state_event() -> None:
    from zf.runtime.orchestrator_reactor import EventReactorMixin

    host = SimpleNamespace(_last_worker_state={"dev-lane-0": "blocked_human"})
    event = ZfEvent(
        type="worker.state.changed", actor="operator",
        payload={"instance_id": "dev-lane-0", "from": "blocked_human",
                 "to": "idle", "reason": "operator unblock"},
    )
    EventReactorMixin._on_worker_state_changed_event(host, event)
    assert host._last_worker_state["dev-lane-0"] == "idle"


def test_a2_event_without_instance_is_noop() -> None:
    from zf.runtime.orchestrator_reactor import EventReactorMixin

    host = SimpleNamespace(_last_worker_state={"dev-lane-0": "blocked_human"})
    EventReactorMixin._on_worker_state_changed_event(
        host, ZfEvent(type="worker.state.changed", actor="zf-cli", payload={}),
    )
    assert host._last_worker_state["dev-lane-0"] == "blocked_human"


def test_a2_unblock_cli_emits_event(tmp_path: Path, monkeypatch) -> None:
    from zf.cli import agents as agents_cli
    from zf.core.events.log import EventLog

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")

    monkeypatch.setattr(
        "zf.core.config.project_context.resolve_project_context",
        lambda **kw: SimpleNamespace(state_dir=state_dir, config=None,
                                     project_root=tmp_path, config_path=None),
    )
    monkeypatch.setattr(
        "zf.core.events.factory.event_log_from_project",
        lambda sd, config=None, warn=False: log,
    )
    args = SimpleNamespace(instance_id="dev-lane-0", reason="撞限已消,放行",
                           state_dir=None)
    assert agents_cli._run_unblock(args) == 0
    events = log.read_all()
    assert events[-1].type == "worker.state.changed"
    assert events[-1].payload["to"] == "idle"
    assert events[-1].payload["source"] == "zf_agents_unblock"
