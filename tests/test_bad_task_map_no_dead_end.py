"""坏 task_map 不死端:按拓扑发上游 stage failure_event(prod-e2e 实弹)。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.orchestrator_fanout import FanoutCoordinationMixin


class _Probe(FanoutCoordinationMixin):
    def __init__(self, tmp_path: Path, stages):
        state_dir = tmp_path / ".zf"
        state_dir.mkdir(exist_ok=True)
        self.event_log = EventLog(state_dir / "events.jsonl")
        self.event_writer = EventWriter(self.event_log)
        self.config = SimpleNamespace(workflow=SimpleNamespace(stages=stages))


def _plan_stage(success: str, failure: str):
    return SimpleNamespace(
        id="prd-plan",
        success_event="",
        failure_event="",
        aggregate=SimpleNamespace(success_event=success, failure_event=failure),
    )


def test_bad_task_map_emits_upstream_failure(tmp_path: Path) -> None:
    probe = _Probe(tmp_path, [_plan_stage("task_map.ready", "prd.plan.failed")])
    trigger = ZfEvent(type="task_map.ready", payload={"task_map_ref": "docs/p.md"})
    probe._emit_upstream_failure_for_bad_task_map(
        trigger_event=trigger, trace_id="t1", pdd_id="default",
        reason="invalid JSON: docs/p.md",
    )
    events = probe.event_log.read_all()
    assert [e.type for e in events] == ["prd.plan.failed"]
    payload = events[0].payload
    assert payload["trigger_event_id"] == trigger.id
    assert "invalid JSON" in payload["reason"]
    assert payload["findings"][0]["severity"] == "high"

    # 幂等:同 trigger 不重发
    probe._emit_upstream_failure_for_bad_task_map(
        trigger_event=trigger, trace_id="t1", pdd_id="default", reason="again",
    )
    assert len(probe.event_log.read_all()) == 1


def test_no_upstream_stage_no_emit(tmp_path: Path) -> None:
    probe = _Probe(tmp_path, [_plan_stage("other.event", "other.failed")])
    probe._emit_upstream_failure_for_bad_task_map(
        trigger_event=ZfEvent(type="task_map.ready", payload={}),
        trace_id="t1", pdd_id="default", reason="x",
    )
    assert probe.event_log.read_all() == []


def test_plan_briefing_contract_lines_exist() -> None:
    """briefing 合同与 admission 合同对齐的守卫:task_map.ready 家族的
    plan briefing 指南必须出现 JSON task map 强制条款(源码级断言,
    防止模板回退到只讲 markdown 的旧文案)。"""
    from pathlib import Path

    source = Path("src/zf/runtime/orchestrator_fanout.py").read_text(encoding="utf-8")
    assert "you MUST also" in source and "task_map.json" in source
    assert "task_map_ref_prefill" in source  # 预填走变量,不再是硬编码空串


def test_affinity_tag_falls_back_to_task_id() -> None:
    """affinity_tag 缺失 → 回退 task_id(prod-e2e:合同没教的字段不许整盘取消)。"""
    from types import SimpleNamespace

    from zf.runtime.writer_fanout_data import WriterFanoutDataMixin

    class _P(WriterFanoutDataMixin):
        def _fanout_affinity_key(self, stage):
            return "affinity_tag"

    stage = SimpleNamespace(assignment=SimpleNamespace(lane_profile="", stage_slot="impl"))
    item = _P()._writer_affinity_task_item(stage, {"task_id": "CONV-CLI-001"})
    assert item["affinity_tag"] == "CONV-CLI-001"
    # 显式 tag 仍优先
    item2 = _P()._writer_affinity_task_item(
        stage, {"task_id": "T2", "affinity_tag": "lane-x"},
    )
    assert item2["affinity_tag"] == "lane-x"


def test_fanout_briefing_carries_active_waivers(tmp_path) -> None:
    """r6-F4:fanout child briefing 必须携带活跃 waiver(F6 只盖了
    injection 路径,verify 审角色看不见豁免令 → waive 对 fanout 无效)。"""
    from pathlib import Path

    source = Path("src/zf/runtime/orchestrator_fanout.py").read_text(encoding="utf-8")
    assert "Active Operator Waivers" in source
    assert "load_active_waivers" in source


def test_evidence_paths_merged_into_contract_scope() -> None:
    """r6-F3:required_runtime_evidence 自动并入 scope(合同自洽)。"""
    from zf.runtime.product_delivery import _merge_evidence_paths_into_scope

    merged = _merge_evidence_paths_into_scope(
        ["src/scene/**"],
        {"required_runtime_evidence": [
            "docs/validation/screenshots/P2-medium-scene.png",
            "docs/validation/perf/canvas-nonblack.json",
        ]},
    )
    assert "docs/validation/screenshots/P2-medium-scene.png" in merged
    assert merged[0] == "src/scene/**"
    # 零声明零扩权
    assert _merge_evidence_paths_into_scope(["src/**"], {}) == ["src/**"]
    # 无 scope 的任务不引入约束
    assert _merge_evidence_paths_into_scope([], {"required_runtime_evidence": ["x"]}) == []
