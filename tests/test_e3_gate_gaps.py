"""131-E3: coverage 门强制(strict)+ floor 词表键聚合透传(D3 dead-end 修复)。"""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.product_delivery import ingest_task_map_to_kanban

from tests.test_writer_fanout_runtime import _FanoutPayloadProbe
from tests.test_product_delivery import _source_index, _task_map


def _state_dir(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    return state_dir


def test_strict_requires_coverage_report(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    result = ingest_task_map_to_kanban(
        state_dir,
        _task_map(),
        source_index=_source_index(),
        source_index_ref="si.json",
        task_map_ref="tm.json",
        require_coverage_report=True,
        writer=writer,
        actor="zf-cli",
    )
    assert result.passed is False
    rejected = [
        e for e in writer.event_log.read_all()
        if e.type == "product_delivery.task_map.rejected"
    ]
    assert rejected and any(
        "coverage_report required" in err for err in rejected[0].payload["errors"]
    )


def test_default_stays_presence_gated(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    result = ingest_task_map_to_kanban(
        state_dir,
        _task_map(),
        source_index=_source_index(),
        source_index_ref="si.json",
        task_map_ref="tm.json",
        writer=writer,
        actor="zf-cli",
    )
    assert result.passed is True  # baseline 零回归


def test_aggregate_payload_carries_quality_floor_refs() -> None:
    # D3 dead-end 根因:judge children 报了 floor 证据,聚合把键丢掉,
    # _reject_flow_judge_evidence_gap 永拒。
    payload = _FanoutPayloadProbe()._generic_fanout_success_payload(
        manifest={
            "fanout_id": "fanout-judge",
            "children": [{
                "child_id": "judge-1",
                "payload": {
                    "demo_refs": ["docs/demo/run.md"],
                    "e2e_refs": ["test-results/e2e.json"],
                    "repro_ref": "docs/repro.md",
                    "evidence_refs": ["docs/judge-report.md"],
                },
            }],
        },
        success_event="judge.passed",
    )
    assert payload["demo_refs"] == ["docs/demo/run.md"]
    assert payload["e2e_refs"] == ["test-results/e2e.json"]
    assert payload["repro_ref"] == "docs/repro.md"
    for ref in ("docs/demo/run.md", "test-results/e2e.json", "docs/repro.md"):
        assert ref in payload["evidence_refs"]


def test_aggregate_payload_without_floor_refs_unchanged() -> None:
    payload = _FanoutPayloadProbe()._generic_fanout_success_payload(
        manifest={"fanout_id": "f", "children": [{
            "child_id": "c", "payload": {"evidence_refs": ["a.md"]},
        }]},
        success_event="review.approved",
    )
    for key in ("demo_refs", "e2e_refs", "repro_ref", "parity_refs"):
        assert key not in payload
