"""E5(131-P2):attempt 账本 — 计数键/supersede 不计/deadletter/F15 宽限。"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.attempt_ledger import (
    counted_failure_events,
    derive_task_ledger,
    failure_fingerprint,
    ledger_summary,
    non_retryable_reason,
)


def _ev(etype: str, task_id: str = "T-1", **payload) -> ZfEvent:
    return ZfEvent(type=etype, task_id=task_id, payload=payload)


def test_counted_failures_ignores_superseded_and_replays() -> None:
    events = [
        # 轮1:真实失败(计数)
        _ev("task.dispatched", role="dev-1", fanout_id="f1"),
        _ev("fanout.child.failed", fanout_id="f1", reason="review blocking"),
        # 轮1 echo 重放(同 fanout 同类型 → 不计数)
        _ev("task.dispatched", role="dev-1", fanout_id="f1"),
        _ev("fanout.child.failed", fanout_id="f1", reason="review blocking"),
        # 轮2:superseded fanout 的失败(不计数)
        _ev("task.dispatched", role="dev-1", fanout_id="f2"),
        ZfEvent(type="fanout.cancelled", payload={
            "fanout_id": "f2", "reason": "superseded_by_latest_fanout",
        }),
        _ev("fanout.child.failed", fanout_id="f2", reason="stale"),
        # 轮3:新 fanout 真实失败(计数)
        _ev("task.dispatched", role="dev-2", fanout_id="f3"),
        _ev("dev.blocked", fanout_id="f3", reason="findings unresolved"),
    ]
    ledger = derive_task_ledger(events, "T-1")
    assert len(ledger.attempts) == 4
    assert ledger.counted_failures() == 2  # f1 首次 + f3;echo 与 superseded 不计
    summary = ledger_summary(ledger)
    assert summary["uncounted"] == 2
    assert summary["last_holder"] == "dev-2"


def test_f16_rework_of_chain_still_accumulates() -> None:
    # F16 场景:每轮新 fanout(rework_of 链),scalar retry_count 归零,
    # 账本按真实失败逐轮累积。
    events = []
    for i in range(5):
        events.append(_ev("task.dispatched", role="dev-1", fanout_id=f"f{i}"))
        events.append(_ev("fanout.child.failed", fanout_id=f"f{i}", reason=f"round {i}"))
    assert derive_task_ledger(events, "T-1").counted_failures() == 5


def test_success_closes_attempt_without_failure_count() -> None:
    events = [
        _ev("task.dispatched", role="dev-1", fanout_id="f1"),
        _ev("dev.build.done", fanout_id="f1", status="completed"),
    ]
    ledger = derive_task_ledger(events, "T-1")
    assert ledger.counted_failures() == 0
    assert ledger.attempts[0].terminal_type == "dev.build.done"


def test_generic_child_events_close_and_count_attempts() -> None:
    events = [
        _ev("task.dispatched", role="dev-1", fanout_id="f1"),
        _ev("impl.child.completed", fanout_id="f1", status="completed"),
        _ev("task.dispatched", role="dev-2", fanout_id="f2"),
        _ev("verify.child.failed", fanout_id="f2", reason="acceptance gap"),
    ]
    ledger = derive_task_ledger(events, "T-1")
    assert len(ledger.attempts) == 2
    assert ledger.attempts[0].terminal_type == "impl.child.completed"
    assert ledger.attempts[1].terminal_type == "verify.child.failed"
    assert ledger.counted_failures() == 1


def test_non_retryable_classification() -> None:
    env = ZfEvent(type="dev.blocked", payload={
        "reason": "Chromium cannot load libnspr4.so under default host path",
    })
    assert non_retryable_reason(env) is not None
    registry_env = ZfEvent(type="env.preflight.failed", payload={})
    assert non_retryable_reason(registry_env) is not None
    normal = ZfEvent(type="review.rejected", payload={"reason": "findings"})
    assert non_retryable_reason(normal) is None


def test_r4_archive_scene_counted_far_below_nominal() -> None:
    # 实弹:r4 SCENE-001 名义 24 次 attempt,大半是 supersede 风暴重绑;
    # 账本的真实计数失败必须远低于名义值(F16 语义验收)。
    from zf.core.events.log import EventLog

    archive = Path("/home/user/workspace/avbs-refactor/state-archive-avbs-r4-final/events.jsonl")
    if not archive.exists():
        import pytest
        pytest.skip("r4 archive not present")
    ledger = derive_task_ledger(EventLog(archive).read_all(), "AVBS-SCENE-001")
    assert len(ledger.attempts) >= 20
    assert ledger.counted_failures() <= len(ledger.attempts) // 2


def test_counted_rework_rounds_cap_semantics() -> None:
    from zf.runtime.attempt_ledger import counted_rework_rounds

    events = []
    # F16 链:3 轮各自新 fanout 的 review.rejected → 累积 3
    for i in range(3):
        events.append(_ev("review.rejected", fanout_id=f"rf{i}", reason=f"r{i}"))
    # echo 重放(同 fanout 同类型)不计
    events.append(_ev("review.rejected", fanout_id="rf0", reason="replay"))
    # superseded fanout 的失败不计
    events.append(ZfEvent(type="fanout.cancelled", payload={
        "fanout_id": "rf9", "reason": "superseded_by_latest_fanout"}))
    events.append(_ev("review.rejected", fanout_id="rf9", reason="stale"))
    assert counted_rework_rounds(events, "T-1") == 3


def test_counted_failure_events_groups_same_semantic_fingerprint() -> None:
    events = [
        _ev("review.rejected", fanout_id="f1", reason="missing expiry test"),
        _ev("review.rejected", fanout_id="f2", reason="missing expiry test"),
        _ev("review.rejected", fanout_id="f3", reason="different finding"),
        # Same fanout echo does not count twice.
        _ev("review.rejected", fanout_id="f1", reason="missing expiry test"),
    ]
    fingerprint = failure_fingerprint(events[0])

    matched = counted_failure_events(
        events,
        "T-1",
        fingerprint=fingerprint,
    )

    assert [event.payload["fanout_id"] for event in matched] == ["f1", "f2"]


def test_counted_failure_events_ignores_stale_superseded_fanout() -> None:
    failure = _ev(
        "verify.failed",
        fanout_id="stale-fanout",
        failure_fingerprint="contract-gap",
    )
    events = [
        ZfEvent(
            type="fanout.child.stale_completion",
            payload={"fanout_id": "stale-fanout"},
        ),
        failure,
    ]

    assert counted_failure_events(
        events,
        "T-1",
        fingerprint="contract-gap",
    ) == []


def test_failure_fingerprint_ignores_dynamic_finding_ids_and_order() -> None:
    first = _ev(
        "review.rejected",
        findings=[
            {"event_id": "evt-1", "category": "test", "message": "missing expiry"},
            {"task_id": "T-1", "category": "scope", "message": "broad edit"},
        ],
    )
    second = _ev(
        "review.rejected",
        findings=[
            {"task_id": "T-2", "category": "scope", "message": "broad edit"},
            {"event_id": "evt-9", "category": "test", "message": "missing expiry"},
        ],
    )

    assert failure_fingerprint(first) == failure_fingerprint(second)


def test_counted_failure_events_ignores_explicit_replay_marker() -> None:
    events = [
        _ev("review.rejected", reason="same gap", replay=True),
        _ev("review.rejected", reason="same gap"),
    ]

    assert len(counted_failure_events(events, "T-1")) == 1


def test_lease_grace_reads_config(monkeypatch=None) -> None:
    """131-P2-3:闲置宽限走 workflow.attempt_lease_grace_s(缺省 900)。"""
    from types import SimpleNamespace

    from zf.runtime.fanout_evidence_queries import FanoutEvidenceQueriesMixin

    class _Probe(FanoutEvidenceQueriesMixin):
        def __init__(self, grace: float | None):
            workflow = (
                SimpleNamespace(attempt_lease_grace_s=grace)
                if grace is not None else SimpleNamespace()
            )
            self.config = SimpleNamespace(workflow=workflow)

        def _fanout_roles(self, names):
            return [SimpleNamespace(name=n, backend="codex") for n in names]

    child = {"role_instance": "reader-1"}
    # 默认 900s:派发后 800s 仍在宽限内
    assert _Probe(None)._fanout_child_idle_grace_active(
        child, dispatch_epoch=0.0, idle_threshold=300.0, now=800.0,
    ) is True
    # 调大到 1800:1200s 仍宽限
    assert _Probe(1800.0)._fanout_child_idle_grace_active(
        child, dispatch_epoch=0.0, idle_threshold=300.0, now=1200.0,
    ) is True
    # 调小到 60:idle_threshold=300 兜底,400s 已出宽限
    assert _Probe(60.0)._fanout_child_idle_grace_active(
        child, dispatch_epoch=0.0, idle_threshold=300.0, now=400.0,
    ) is False


def test_attempt_lease_grace_yaml_round_trip(tmp_path) -> None:
    """131-P2-3 补盲:YAML 键必须过 loader 白名单(r6 点火实弹踩出)。"""
    from zf.core.config.loader import load_config

    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        "version: '1.0'\n"
        "project: {name: t}\n"
        "session: {tmux_session: t}\n"
        "roles:\n- {name: dev, backend: mock}\n"
        "workflow:\n  attempt_lease_grace_s: 1800\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.workflow.attempt_lease_grace_s == 1800.0
