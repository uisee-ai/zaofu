"""B4: source-index 双向锚门(doc 91 P1;1345 record per-task 断裂)。"""

from __future__ import annotations

from zf.runtime.source_index_gate import (
    evaluate_source_index_gate,
    synthesize_degraded_source_index,
)


def _items(n: int = 3, *, anchored: bool) -> list[dict]:
    out = []
    for i in range(n):
        item = {"task_id": f"T{i}", "allowed_paths": [f"packages/p{i}/**"]}
        if anchored:
            item["source_key"] = f"plan.md#sec-{i}"
            item["source_ref"] = f"docs/plan.md:L{i*10}"
        out.append(item)
    return out


def test_strict_fail_closed_on_empty_source_index():
    # doc 91 P1 验收: 空 source-index + 多 task,strict 拒绝(R25 形态)
    result = evaluate_source_index_gate(
        task_items=_items(6, anchored=False),
        source_index={"schema_version": "source-index.v1", "tasks": []},
        findings=None,
        harness_profile="strict",
    )
    assert result.passed is False
    assert result.mode == "fail_closed"
    assert len(result.missing_anchor_task_ids) == 6


def test_baseline_synthesizes_degraded_index():
    result = evaluate_source_index_gate(
        task_items=_items(2, anchored=False),
        source_index=None,
        findings=None,
        harness_profile="baseline",
    )
    assert result.passed is True
    assert result.mode == "degraded"
    assert result.degraded_index is not None
    assert result.degraded_index["degraded"] is True
    assert [t["task_id"] for t in result.degraded_index["tasks"]] == ["T0", "T1"]


def test_anchored_tasks_pass_clean():
    result = evaluate_source_index_gate(
        task_items=_items(3, anchored=True),
        source_index=None,
        findings=None,
        harness_profile="strict",
    )
    assert result.passed is True
    assert result.mode == "ok"


def test_source_keys_on_task_items_count_as_anchors():
    result = evaluate_source_index_gate(
        task_items=[
            {"task_id": "T0", "source_keys": ["scan/findings.json#F-1"]},
            {"task_id": "T1", "source_keys": {"finding": "scan/findings.json#F-2"}},
        ],
        source_index=None,
        findings=None,
        harness_profile="strict",
    )
    assert result.passed is True
    assert result.mode == "ok"


def test_index_file_anchors_count_when_items_bare():
    # 锚也可以住在 source_index.tasks[](item 本身裸)
    index = {"tasks": [
        {"task_id": "T0", "source_key": "plan.md#a"},
        {"task_id": "T1", "source_ref": "scan/findings.json#f3"},
    ]}
    result = evaluate_source_index_gate(
        task_items=[{"task_id": "T0"}, {"task_id": "T1"}],
        source_index=index,
        findings=None,
        harness_profile="strict",
    )
    assert result.passed is True


def test_task_sources_entries_anchor_bare_items():
    # Refactor E2E 产物使用 task_sources[]，不是 source_index.tasks[]。
    index = {"task_sources": [
        {"task_id": "T0", "source_keys": ["scan/findings.json#F-1"]},
        {"task_id": "T1", "source_refs": {"finding": "scan/findings.json#F-2"}},
    ]}
    result = evaluate_source_index_gate(
        task_items=[{"task_id": "T0"}, {"task_id": "T1"}],
        source_index=index,
        findings=None,
        harness_profile="strict",
    )
    assert result.passed is True


def test_source_facts_task_ids_anchor_bare_items():
    index = {"source_facts": [
        {"source_key": "scan/findings.json#F-1", "task_ids": ["T0", "T1"]},
    ]}
    result = evaluate_source_index_gate(
        task_items=[{"task_id": "T0"}, {"task_id": "T1"}],
        source_index=index,
        findings=None,
        harness_profile="strict",
    )
    assert result.passed is True


def test_single_task_legacy_sources_global_passes_strict():
    # Issue E2E 里单任务旧格式只有全局 sources[]。单任务没有拆分歧义，
    # strict 可接受；多任务仍必须 fail-closed。
    result = evaluate_source_index_gate(
        task_items=[{"task_id": "ISSUE-1"}],
        source_index={"sources": [{"path": "docs/issues/ISSUE-1.md"}]},
        findings=None,
        harness_profile="strict",
    )
    assert result.passed is True


def test_multi_task_legacy_sources_global_still_fails_strict():
    result = evaluate_source_index_gate(
        task_items=[{"task_id": "T0"}, {"task_id": "T1"}],
        source_index={"sources": [{"path": "docs/issues/batch.md"}]},
        findings=None,
        harness_profile="strict",
    )
    assert result.passed is False
    assert result.mode == "fail_closed"
    assert result.missing_anchor_task_ids == ["T0", "T1"]


def test_reverse_audit_flags_unclaimed_findings():
    # B4 反向 per-finding 覆盖审计: 没人认领的 finding 被点名
    items = [
        {"task_id": "T0", "source_refs": ["F-1"]},
        {"task_id": "T1", "source_refs": ["F-2"]},
    ]
    findings = [{"id": "F-1"}, {"id": "F-2"}, {"id": "F-3"}]
    result = evaluate_source_index_gate(
        task_items=items,
        source_index=None,
        findings=findings,
        harness_profile="strict",
    )
    assert result.passed is True  # 正向锚齐(source_refs 即锚)
    assert result.unclaimed_finding_ids == ["F-3"]


def test_degraded_synthesis_is_explicit_not_fake():
    out = synthesize_degraded_source_index(
        [{"task_id": "X"}], reason="test",
    )
    assert out["degraded"] is True
    assert out["tasks"][0]["degraded"] is True
    assert out["tasks"][0]["source_key"] == ""  # 不伪造锚
