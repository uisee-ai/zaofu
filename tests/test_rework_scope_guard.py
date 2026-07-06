"""avbs-r4 F1-D2: rework 路由与 findings 归属错位告警(r3 活锁签名)。"""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.rework_scope_guard import rework_scope_mismatch


def _task(scope: list[str]) -> Task:
    return Task(
        id="AVBS-SCENE-001", title="scene", status="in_progress",
        contract=TaskContract(feature_id="F-1", scope=scope),
    )


def _rejection(paths: list[str]) -> ZfEvent:
    return ZfEvent(type="review.rejected", payload={
        "findings": [
            {"severity": "high", "path": p, "message": "blocking"} for p in paths
        ],
    })


def test_flags_when_no_finding_path_in_scope() -> None:
    # r3 原型:engine 文件归 assembly,rework 却固定路由到 scene
    warning = rework_scope_mismatch(
        _task(["src/scenario/**", "src/world/**"]),
        _rejection(["src/simulation/engine/SimulationEngine.ts"]),
    )
    assert warning is not None
    assert warning["finding_paths"] == ["src/simulation/engine/SimulationEngine.ts"]
    assert "cannot fix" in warning["reason"]


def test_silent_when_any_path_fixable() -> None:
    warning = rework_scope_mismatch(
        _task(["src/scenario/**"]),
        _rejection(["src/simulation/engine/E.ts", "src/scenario/Loader.ts"]),
    )
    assert warning is None


def test_silent_without_findings_or_scope() -> None:
    assert rework_scope_mismatch(_task(["src/**"]), ZfEvent(type="review.rejected", payload={})) is None
    assert rework_scope_mismatch(_task([]), _rejection(["src/a.ts"])) is None


def test_reads_findings_from_report_wrapper() -> None:
    event = ZfEvent(type="review.rejected", payload={
        "report": {"findings": [{"path": "src/simulation/metrics/M.ts", "message": "x"}]},
    })
    warning = rework_scope_mismatch(_task(["src/scenario/**"]), event)
    assert warning is not None
