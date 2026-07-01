"""Sprint §1 — autoresearch loop data structures + journal IO tests."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from zf.autoresearch.loop import (
    AutoresearchEvalMetrics,
    EvalDelta,
    EvalSnapshot,
    IterationRecord,
    LoopConfig,
    LoopResult,
    ReflectionResult,
    append_journal_entry,
    record_to_dict,
    record_from_dict,
)
from zf.autoresearch.artifacts import write_reflection_artifacts
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


def test_loop_config_frozen() -> None:
    cfg = LoopConfig(
        scenarios=["s1"],
        worktree=Path("/tmp/x"),
        parent_state_dir=Path("/tmp/x/.zf"),
    )
    with pytest.raises(FrozenInstanceError):
        cfg.max_iterations = 99  # type: ignore[misc]


def test_eval_snapshot_frozen_and_defaults() -> None:
    snap = EvalSnapshot(
        healthy_metrics=8,
        warning_metrics=3,
        critical_metrics=7,
        coordinator_ratio=0.179,
        open_backlog_count=8,
        rework_looped=1,
        completed_tasks=0,
    )
    with pytest.raises(FrozenInstanceError):
        snap.healthy_metrics = 0  # type: ignore[misc]
    assert snap.critical_metrics == 7


def test_eval_delta_verdict_enum() -> None:
    d = EvalDelta(
        healthy_delta=1,
        critical_delta=-2,
        coordinator_delta=0.05,
        backlog_delta=-1,
        completed_delta=1,
        verdict="improved",
    )
    assert d.verdict == "improved"


def test_reflection_result_frozen() -> None:
    r = ReflectionResult(
        verdict="best_so_far",
        alternatives=["try X"],
        risk="low",
        rec_for_next_iter="run controlled-stuck-recovery",
        raw_response="{...}",
    )
    with pytest.raises(FrozenInstanceError):
        r.verdict = "regression"  # type: ignore[misc]


def test_iteration_record_full_shape() -> None:
    snap = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    rec = IterationRecord(
        iter=1,
        started_at="2026-05-19T07:00:00+00:00",
        scenario="self-eval-backlog",
        run_id="run-abc",
        run_status="failed",
        tasks_done=0,
        expected_done=3,
        eval=snap,
        delta=None,
        reflect=None,
        git_head="a1f611e",
        head_changed_since_prev=False,
        summary="iter 1: failed, no baseline",
    )
    assert rec.iter == 1
    assert rec.eval.critical_metrics == 7
    assert rec.delta is None  # first iter → no baseline


def test_loop_result_terminal_states() -> None:
    r = LoopResult(
        iterations=3,
        final_status="converged",
        journal_path=Path("/tmp/journal.jsonl"),
        report_path=Path("/tmp/report.md"),
    )
    assert r.final_status == "converged"


# ---------------------------------------------------------------------------
# JSON roundtrip
# ---------------------------------------------------------------------------


def _make_record(iter: int = 1) -> IterationRecord:
    snap = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    return IterationRecord(
        iter=iter,
        started_at="2026-05-19T07:00:00+00:00",
        scenario="self-eval-backlog",
        run_id=f"run-{iter}",
        run_status="failed",
        tasks_done=0,
        expected_done=3,
        eval=snap,
        delta=None,
        reflect=None,
        git_head="abc1234",
        head_changed_since_prev=False,
        summary=f"iter {iter}",
    )


def test_record_roundtrip_no_delta_no_reflect() -> None:
    rec = _make_record(1)
    d = record_to_dict(rec)
    assert d["iter"] == 1
    assert d["eval"]["critical_metrics"] == 7
    assert d["delta"] is None
    assert d["reflect"] is None
    assert d["autoresearch_eval"]["metric_sources"]["profile"].startswith(
        "docs/design/45",
    )
    assert d["autoresearch_eval"]["lop"]["state"] == "healthy"

    rec2 = record_from_dict(d)
    assert rec2 == rec


def test_record_from_legacy_dict_adds_metric_defaults() -> None:
    rec = _make_record(1)
    d = record_to_dict(rec)
    d.pop("autoresearch_eval")

    rec2 = record_from_dict(d)

    assert isinstance(rec2.autoresearch_eval, AutoresearchEvalMetrics)
    assert rec2.autoresearch_eval.eval.verdict == "not_collected"
    assert "freshness" in rec2.autoresearch_eval.metric_sources


def test_record_roundtrip_with_delta_and_reflect() -> None:
    snap = EvalSnapshot(9, 2, 5, 0.243, 6, 0, 1)
    delta = EvalDelta(
        healthy_delta=1, critical_delta=-2,
        coordinator_delta=0.064, backlog_delta=-2,
        completed_delta=1, verdict="improved",
    )
    reflect = ReflectionResult(
        verdict="best_so_far",
        alternatives=["alt-A", "alt-B"],
        risk="low",
        rec_for_next_iter="run X next",
        raw_response='{"verdict":"best_so_far"}',
    )
    rec = IterationRecord(
        iter=2,
        started_at="2026-05-19T07:10:00+00:00",
        scenario="controlled-stuck-recovery",
        run_id="run-2",
        run_status="passed",
        tasks_done=3,
        expected_done=3,
        eval=snap,
        delta=delta,
        reflect=reflect,
        git_head="def5678",
        head_changed_since_prev=True,
        summary="iter 2: passed",
    )
    d = record_to_dict(rec)
    assert d["delta"]["verdict"] == "improved"
    assert d["reflect"]["risk"] == "low"
    assert d["reflect"]["alternatives"] == ["alt-A", "alt-B"]

    rec2 = record_from_dict(d)
    assert rec2 == rec


# ---------------------------------------------------------------------------
# Journal append (jsonl)
# ---------------------------------------------------------------------------


def test_append_journal_creates_and_appends(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    append_journal_entry(journal, _make_record(1))
    append_journal_entry(journal, _make_record(2))
    append_journal_entry(journal, _make_record(3))

    lines = journal.read_text().strip().split("\n")
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [p["iter"] for p in parsed] == [1, 2, 3]


def test_append_journal_parent_dir_autocreate(tmp_path: Path) -> None:
    journal = tmp_path / "nested" / "deep" / "journal.jsonl"
    append_journal_entry(journal, _make_record(1))
    assert journal.exists()
    assert json.loads(journal.read_text().strip())["iter"] == 1


def test_append_journal_preserves_unicode(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    snap = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    rec = IterationRecord(
        iter=1, started_at="2026-05-19T07:00:00+00:00",
        scenario="self-eval-backlog", run_id="run-1", run_status="failed",
        tasks_done=0, expected_done=3, eval=snap,
        delta=None, reflect=None,
        git_head="abc", head_changed_since_prev=False,
        summary="第 1 轮：失败，无基线",
    )
    append_journal_entry(journal, rec)
    raw = journal.read_text()
    # JSONL must keep zh-CN readable (no \u escapes for CJK).
    assert "第 1 轮" in raw, f"unicode should be preserved verbatim; got {raw!r}"


def test_reflection_artifacts_emit_sidecar_descriptors(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    rec = _make_record(1)
    rec = IterationRecord(
        iter=rec.iter,
        started_at=rec.started_at,
        scenario=rec.scenario,
        run_id=rec.run_id,
        run_status="passed",
        tasks_done=3,
        expected_done=3,
        eval=rec.eval,
        delta=rec.delta,
        reflect=ReflectionResult(
            verdict="best_so_far",
            alternatives=[],
            risk="low",
            rec_for_next_iter="keep this route",
            raw_response='{"verdict":"best_so_far"}',
        ),
        git_head=rec.git_head,
        head_changed_since_prev=rec.head_changed_since_prev,
        summary=rec.summary,
    )

    refs = write_reflection_artifacts(
        tmp_path / "autoresearch-output",
        rec,
        evidence_refs=["journal.jsonl"],
        state_dir=state_dir,
    )

    assert Path(refs["reflection"]).exists()
    assert refs["sidecar_refs"]["reflection"]["ref_schema_version"] == "sidecar-ref.v1"
    hydrated = hydrate_sidecar_ref(state_dir, refs["sidecar_refs"]["reflection"])
    assert hydrated.payload["recommendation"] == "keep this route"
