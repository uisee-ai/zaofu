"""loop-view.v1 投影测试 — 2026-07 五 run dry-run 教训逐条固化。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.stage_loop_projection import build_loop_view

R6_ARCHIVE = Path("/home/user/workspace/avbs-refactor/state-archive-avbs-r6-final")


def _log(tmp_path: Path) -> EventLog:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    return EventLog(state_dir / "events.jsonl")


def _sd(tmp_path: Path) -> Path:
    return tmp_path / ".zf"


def test_pump_events_excluded_from_semantic_activity(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append(ZfEvent(type="fanout.child.dispatched",
                       payload={"stage_id": "impl", "child_id": "c1"}))
    for _ in range(5):
        log.append(ZfEvent(type="run.manager.tick.started"))
    view = build_loop_view(_sd(tmp_path))
    assert view["run"]["event_count"] == 6
    assert view["run"]["semantic_event_count"] == 1
    assert view["pump"]["total"] == 5


def test_transport_receipt_deduped_against_semantic_completion(tmp_path: Path) -> None:
    """同一轮的 verify.child.completed 与 fanout.child.completed 双回执 = 1 attempt。"""
    log = _log(tmp_path)
    key = {"stage_id": "verify", "child_id": "lane-1"}
    log.append(ZfEvent(type="fanout.child.dispatched", payload=key))
    log.append(ZfEvent(type="verify.child.completed", payload=key))
    log.append(ZfEvent(type="fanout.child.completed", payload=key))
    view = build_loop_view(_sd(tmp_path))
    (task,) = view["tasks"]
    assert len(task["attempts"]) == 1
    assert task["attempts"][0]["terminal"]["type"] == "verify.child.completed"


def test_orphan_completion_preserved(tmp_path: Path) -> None:
    """dispatch 在日志窗口外的失败完成不得被静默丢弃(scan-contract 教训)。"""
    log = _log(tmp_path)
    log.append(ZfEvent(type="refactor.scan.failed",
                       payload={"stage_id": "scan", "child_id": "scan-contract"}))
    view = build_loop_view(_sd(tmp_path))
    (task,) = view["tasks"]
    assert task["attempts"][0].get("orphan") is True
    assert task["fails"] == 1


def test_superseded_open_attempt_is_uncounted_via_spine(tmp_path: Path) -> None:
    """E5 账本语义:被后续 attempt 顶替的 open attempt 不计数。"""
    sd = _sd(tmp_path)
    sd.mkdir(exist_ok=True)
    (sd / "events.jsonl").write_text("", encoding="utf-8")
    proj = sd / "projections"
    proj.mkdir()
    (proj / "task_attempts.json").write_text(json.dumps({
        "schema_version": "shadow-spine.v1",
        "tasks": {"T-1": {"attempts": [
            {"started_ts": "2026-07-06T01:00:00+00:00", "role": "dev", "terminal": None},
            {"started_ts": "2026-07-06T01:10:00+00:00", "role": "dev",
             "terminal": {"type": "dev.build.done", "ts": "2026-07-06T01:20:00+00:00"}},
        ]}},
    }), encoding="utf-8")
    view = build_loop_view(sd)
    (task,) = view["tasks"]
    assert task["source"] == "task_attempts.json"
    assert [a["counted"] for a in task["attempts"]] == [False, True]
    assert task["counted"] == 1


def test_zero_state_loops_and_companions_absent(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append(ZfEvent(type="fanout.child.dispatched",
                       payload={"stage_id": "impl", "child_id": "c1"}))
    view = build_loop_view(_sd(tmp_path))
    assert "parity" not in view["loops"]
    assert "replan" not in view["loops"]
    assert view["companions"] == {}
    assert view["faults"] == []


def test_flow_shapes_differ_without_code_branches(tmp_path: Path) -> None:
    """issue 与 refactor 夹具:阶段链与业务环在场集合不同,同一函数消费。"""
    issue = tmp_path / "issue"
    issue.mkdir()
    log = EventLog(issue / ".zf" / "events.jsonl")
    for stage in ("issue-map", "issue-impl", "issue-verify", "issue-judge"):
        log.append(ZfEvent(type="fanout.started", payload={"stage_id": stage}))
    refactor = tmp_path / "refactor"
    refactor.mkdir()
    log2 = EventLog(refactor / ".zf" / "events.jsonl")
    log2.append(ZfEvent(type="fanout.started", payload={"stage_id": "refactor-scan"}))
    log2.append(ZfEvent(type="module.parity.closed",
                        payload={"pdd_id": "P", "open_p0_p1_gap_count": "0"}))

    issue_view = build_loop_view(issue / ".zf")
    refactor_view = build_loop_view(refactor / ".zf")
    assert [s["id"] for s in issue_view["stages"]] == [
        "issue-map", "issue-impl", "issue-verify", "issue-judge"]
    assert "parity" not in issue_view["loops"]
    assert refactor_view["loops"]["parity"]["health"] == "closed"
    assert refactor_view["loops"]["parity"]["arc"]["state"] == "flow"


def test_spine_preferred_and_event_fallback_equivalent(tmp_path: Path) -> None:
    log = _log(tmp_path)
    key = {"stage_id": "impl", "child_id": "dev-1"}
    log.append(ZfEvent(type="fanout.child.dispatched", payload=key))
    log.append(ZfEvent(type="fanout.child.failed", payload={**key, "status": "failed"}))
    sd = _sd(tmp_path)
    proj = sd / "projections"
    proj.mkdir()
    (proj / "task_attempts.json").write_text(json.dumps({
        "tasks": {"dev-1": {"attempts": [
            {"started_ts": "2026-07-06T01:00:00+00:00", "role": "dev",
             "terminal": {"type": "fanout.child.failed", "ts": "2026-07-06T01:05:00+00:00"}},
        ]}},
    }), encoding="utf-8")
    spine_view = build_loop_view(sd)
    (proj / "task_attempts.json").unlink()
    event_view = build_loop_view(sd)
    assert spine_view["tasks"][0]["source"] == "task_attempts.json"
    assert event_view["tasks"][0]["source"] == "events"
    assert spine_view["tasks"][0]["counted"] == event_view["tasks"][0]["counted"] == 1
    assert spine_view["tasks"][0]["fails"] == event_view["tasks"][0]["fails"] == 1


def test_promise_contract_preferred_generic_fallback(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append(ZfEvent(type="scan.completed", payload={}))
    log.append(ZfEvent(type="run.completed", payload={}))
    root = tmp_path
    (root / "zf.yaml").write_text(
        "workflow_completion:\n  promise: delivery.complete\n  required_events:\n"
        "    - scan.completed\n    - run.completed\n",
        encoding="utf-8",
    )
    with_contract = build_loop_view(_sd(tmp_path), project_root=root)
    assert with_contract["run"]["promise"]["source"] == "workflow_completion contract"
    assert with_contract["run"]["promise"]["satisfied"] == 2
    assert with_contract["run"]["latched"] is True

    without = build_loop_view(_sd(tmp_path), project_root=None)
    assert without["run"]["promise"]["source"] == "generic fallback"
    assert without["run"]["latched"] is True  # run.completed 在通用链里


def test_broken_arc_semantics_for_approval_and_replan(tmp_path: Path) -> None:
    """escalate 无裁决回声 = 断环;reflect 无采纳 = 断环。"""
    log = _log(tmp_path)
    for _ in range(3):
        log.append(ZfEvent(type="human.escalate", payload={}))
    log.append(ZfEvent(type="run.manager.reflect.completed", payload={}))
    view = build_loop_view(_sd(tmp_path))
    assert view["loops"]["approval"]["arc"]["state"] == "broken"
    assert view["loops"]["approval"]["health"] == "broken"
    assert view["loops"]["replan"]["arc"]["state"] == "broken"
    assert view["faults"] == [
        {"kind": "human.escalate", "count": 3, "owner_loop": "approval"},
    ]


@pytest.mark.skipif(not R6_ARCHIVE.exists(), reason="r6 archive not on this machine")
def test_r6_archive_integration() -> None:
    view = build_loop_view(R6_ARCHIVE)
    counters = view["health_counters"]
    assert counters["human.escalate"] == 25
    assert counters["verify.failed"] == 14
    assert view["companions"]["learning"]["reflections"] == 150
    assert len(view["tasks"]) == 3
    assert view["loops"]["approval"]["arc"]["state"] == "broken"
    assert view["loops"]["replan"]["arc"]["state"] == "broken"
    assert view["loops"]["recovery"]["arc"]["state"] == "flow"
    assert view["run"]["latched"] is False


def test_stage_backflow_edges_from_event_pairing(tmp_path: Path) -> None:
    """verify.failed(verify 阶段)→ 下一次 impl 重派 = 一条边级回流。"""
    log = _log(tmp_path)
    log.append(ZfEvent(type="fanout.child.dispatched",
                       payload={"stage_id": "impl", "child_id": "d1"}))
    log.append(ZfEvent(type="verify.failed", payload={"stage_id": "verify"}))
    log.append(ZfEvent(type="fanout.child.dispatched",
                       payload={"stage_id": "impl", "child_id": "d1"}))
    view = build_loop_view(_sd(tmp_path))
    assert view["backflows"] == [
        {"from_stage": "verify", "to_stage": "impl", "kind": "rework", "count": 1},
    ]


def test_subscriber_chains_event_paired(tmp_path: Path) -> None:
    """131 §8.1:trigger → 下一个消费事件,解释下一阶段为何被唤醒。"""
    log = _log(tmp_path)
    log.append(ZfEvent(type="task_map.ready", payload={}))
    log.append(ZfEvent(type="fanout.child.dispatched",
                       payload={"stage_id": "impl", "child_id": "dev-1"}))
    view = build_loop_view(_sd(tmp_path))
    (chain,) = view["subscriber_chains"]
    assert chain["topic"] == "task_map.ready"
    assert chain["subscriber"] == "impl"
    assert chain["result"] == "dev-1"


def test_loop_members_are_projection_slices(tmp_path: Path) -> None:
    """点击环卡显示的成员 = 投影切片(最坏在前),非重算。"""
    log = _log(tmp_path)
    for cid, fail in (("dev-1", True), ("dev-2", False)):
        key = {"stage_id": "impl", "child_id": cid}
        log.append(ZfEvent(type="fanout.child.dispatched", payload=key))
        log.append(ZfEvent(
            type="fanout.child.failed" if fail else "verify.child.completed",
            payload={**key, **({"status": "failed"} if fail else {})}))
    view = build_loop_view(_sd(tmp_path))
    members = view["loops"]["delivery"]["members"]
    assert members[0]["id"] == "dev-1" and "1✗" in members[0]["note"]
    assert members[1]["id"] == "dev-2"


def test_loop_node_stats_per_shape_node(tmp_path: Path) -> None:
    """环卡 graph 节点各带自己的读数(hover 数据源)。"""
    log = _log(tmp_path)
    for _ in range(2):
        log.append(ZfEvent(type="human.escalate", payload={}))
    view = build_loop_view(_sd(tmp_path))
    ns = view["loops"]["approval"]["node_stats"]
    assert ns["escalate"] == {"in": 2}
    assert ns["inbox"] == {"pending": 2}
    assert ns["re-inject"] == {"back": 0}
