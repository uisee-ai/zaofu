"""Sprint §5 — loop driver orchestration tests.

The driver is tested via dependency injection — every external side
effect (autoresearch run, kanban health refresh, git, LLM reflect,
HEAD-change wait) is passed in as a callable, so unit tests don't
touch subprocess or filesystem state outside the tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

from zf.autoresearch.loop import (
    EvalSnapshot,
    LoopConfig,
    ReflectionResult,
    record_from_dict,
    run_loop,
)


class _StubAutoresearch:
    """Sequence of (status, tasks_done, fatal) tuples for each iter call."""

    def __init__(self, sequence: list[tuple[str, int, dict | None]]):
        self.sequence = sequence
        self.calls: list[dict] = []

    def __call__(self, *, scenario: str, run_id: str, **kw) -> dict:
        idx = len(self.calls)
        status, tasks_done, fatal = self.sequence[idx]
        self.calls.append({"scenario": scenario, "run_id": run_id})
        return {
            "status": status,
            "tasks_done": tasks_done,
            "expected_done": 3,
            "fatal_event": fatal,
            "report_path": f"/tmp/report-{idx}.md",
        }


class _StubEvalCollector:
    """Returns a fixed sequence of EvalSnapshot per iter call."""

    def __init__(self, sequence: list[EvalSnapshot]):
        self.sequence = sequence
        self.calls = 0

    def __call__(self, state_dir: Path) -> EvalSnapshot:
        s = self.sequence[self.calls]
        self.calls += 1
        return s


class _RecordingEvalCollector(_StubEvalCollector):
    def __init__(self, sequence: list[EvalSnapshot]):
        super().__init__(sequence)
        self.paths: list[Path] = []

    def __call__(self, state_dir: Path) -> EvalSnapshot:
        self.paths.append(state_dir)
        return super().__call__(state_dir)


class _StubReflect:
    def __init__(self, results: list[ReflectionResult]):
        self.results = results
        self.calls = 0

    def __call__(self, prompt: str, **kw) -> ReflectionResult:
        r = self.results[self.calls]
        self.calls += 1
        return r


class _StubGit:
    def __init__(self, head_sequence: list[str], diff: str = ""):
        self.heads = head_sequence
        self.idx = 0
        self.diff = diff

    def head(self, state_dir: Path) -> str:
        h = self.heads[min(self.idx, len(self.heads) - 1)]
        self.idx += 1
        return h

    def diff_since(self, parent_dir: Path, base_sha: str) -> str:
        return self.diff


def _improved_then_passed_sequence() -> tuple[list, list, list]:
    """Iter 1: fail / 7 critical.
       Iter 2: fail / 5 critical (improved).
       Iter 3: pass / 2 critical (improved+passed → consecutive_passed=1)."""
    ar = [
        ("failed", 0, None),
        ("failed", 1, None),
        ("passed", 3, None),
    ]
    eval_seq = [
        EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0),   # iter 1
        EvalSnapshot(8, 4, 5, 0.243, 6, 1, 1),   # iter 2 — improved
        EvalSnapshot(9, 4, 2, 0.5, 4, 0, 3),     # iter 3 — improved+passed
    ]
    reflect_seq = [
        ReflectionResult("unknown", [], "medium", "first iter", ""),
        ReflectionResult("best_so_far", ["alt-A"], "low", "run X next", "{...}"),
        ReflectionResult("best_so_far", [], "low", "converged", "{...}"),
    ]
    return ar, eval_seq, reflect_seq


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_driver_writes_journal_per_iter(tmp_path: Path) -> None:
    ar_seq, eval_seq, reflect_seq = _improved_then_passed_sequence()
    cfg = LoopConfig(
        scenarios=["self-eval-backlog"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / ".zf",
        max_iterations=3,
        budget_usd=100.0,
        output_dir=tmp_path / "loop",
    )
    (cfg.parent_state_dir).mkdir(parents=True)

    run_loop(
        cfg,
        autoresearch_fn=_StubAutoresearch(ar_seq),
        eval_collector_fn=_StubEvalCollector(eval_seq),
        reflect_fn=_StubReflect(reflect_seq),
        git_head_fn=_StubGit(["sha1", "sha2", "sha3"]).head,
        git_diff_fn=_StubGit([], diff="").diff_since,
        backlog_fn=lambda _: [],
        wait_for_fix_fn=lambda *_, **__: True,
    )

    journal = cfg.output_dir / "journal.jsonl"
    assert journal.exists()
    lines = journal.read_text().strip().split("\n")
    assert len(lines) == 3
    parsed = [record_from_dict(json.loads(line)) for line in lines]
    assert [r.iter for r in parsed] == [1, 2, 3]
    assert [r.run_status for r in parsed] == ["failed", "failed", "passed"]
    assert parsed[0].delta is None        # first iter, no baseline
    assert parsed[1].delta is not None
    assert parsed[1].delta.verdict == "improved"
    raw = [json.loads(line) for line in lines]
    assert "autoresearch_eval" in raw[0]
    assert "metric_sources" in raw[0]["autoresearch_eval"]
    assert raw[0]["autoresearch_eval"]["autoresearch"]["boundary"] == "worker_task"
    assert raw[0]["autoresearch_eval"]["eval"]["verdict"] == "not_collected"
    assert raw[0]["autoresearch_eval"]["lop"]["recommended_action"] == "continuation"


def test_driver_writes_per_iter_markdown(tmp_path: Path) -> None:
    ar_seq, eval_seq, reflect_seq = _improved_then_passed_sequence()
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / ".zf",
        max_iterations=2,
        output_dir=tmp_path / "loop",
    )
    cfg.parent_state_dir.mkdir(parents=True)
    run_loop(
        cfg,
        autoresearch_fn=_StubAutoresearch(ar_seq),
        eval_collector_fn=_StubEvalCollector(eval_seq),
        reflect_fn=_StubReflect(reflect_seq),
        git_head_fn=_StubGit(["sha1", "sha2"]).head,
        git_diff_fn=_StubGit([], diff="").diff_since,
        backlog_fn=lambda _: [],
        wait_for_fix_fn=lambda *_, **__: True,
    )
    # iter-001.md and iter-002.md must exist with readable summaries.
    assert (cfg.output_dir / "iter-001.md").exists()
    assert (cfg.output_dir / "iter-002.md").exists()
    iter1 = (cfg.output_dir / "iter-001.md").read_text()
    assert "Iteration 1" in iter1
    assert "failed" in iter1
    assert "Autoresearch / Eval / LOP metrics" in iter1
    assert "recommended_action" in iter1


def test_driver_persists_review_gate_metadata_and_refs(tmp_path: Path) -> None:
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / ".zf",
        max_iterations=1,
        output_dir=tmp_path / "loop",
        review_gate="auto",
    )
    cfg.parent_state_dir.mkdir(parents=True)
    review_gate = {
        "mode": "auto",
        "status": "triggered",
        "triggered": True,
        "route": "fanout_gate",
        "severity": "high",
        "reason": "runtime fanout failure",
        "artifact_refs": {
            "summary": str(tmp_path / "run" / "review-gate" / "summary.json"),
            "failure_evidence_pack": str(tmp_path / "run" / "failure.json"),
        },
    }

    run_loop(
        cfg,
        autoresearch_fn=lambda **_: {
            "status": "failed",
            "tasks_done": 0,
            "expected_done": 1,
            "review_gate": review_gate,
        },
        eval_collector_fn=_StubEvalCollector([
            EvalSnapshot(1, 0, 1, 0.0, 0, 0, 0)
        ]),
        reflect_fn=_StubReflect([
            ReflectionResult("unknown", [], "high", "", "")
        ]),
        git_head_fn=_StubGit(["sha1"]).head,
        git_diff_fn=lambda *_, **__: "",
        backlog_fn=lambda _: [],
        wait_for_fix_fn=lambda *_, **__: True,
    )

    row = json.loads((cfg.output_dir / "journal.jsonl").read_text().splitlines()[0])
    assert row["review_gate"]["route"] == "fanout_gate"
    iter_md = (cfg.output_dir / "iter-001.md").read_text()
    assert "Review Gate" in iter_md
    assert "fanout_gate" in iter_md
    eval_result = json.loads(
        (cfg.output_dir / "eval-results" / "iter-001.json").read_text()
    )
    assert eval_result["metadata"]["review_gate"]["status"] == "triggered"
    assert str(tmp_path / "run" / "failure.json") in (
        eval_result["evidence_refs"]["review_gate"]
    )
    experiment = json.loads(
        (cfg.parent_state_dir / "autoresearch" / "experiments" / "events.jsonl")
        .read_text()
        .splitlines()[0]
    )
    assert experiment["payload"]["metadata"]["review_gate"]["route"] == "fanout_gate"


def test_driver_final_report_lists_metric_sources(tmp_path: Path) -> None:
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / ".zf",
        max_iterations=1,
        output_dir=tmp_path / "loop",
    )
    cfg.parent_state_dir.mkdir(parents=True)
    snap = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)

    run_loop(
        cfg,
        autoresearch_fn=_StubAutoresearch([("failed", 0, None)]),
        eval_collector_fn=_StubEvalCollector([snap]),
        reflect_fn=_StubReflect([
            ReflectionResult("unknown", [], "medium", "", "")
        ]),
        git_head_fn=_StubGit(["sha1"]).head,
        git_diff_fn=lambda *_, **__: "",
        backlog_fn=lambda _: [],
        wait_for_fix_fn=lambda *_, **__: True,
    )

    report = (cfg.output_dir / "report.md").read_text()
    assert "Metric sources" in report
    assert "docs/design/45-baseline-strict-harness-profiles.md" in report
    assert "docs/design/46-long-horizon-workunit-feedback-design.md" in report


def test_driver_uses_autoresearch_result_state_dir_for_iteration_metrics(
    tmp_path: Path,
) -> None:
    parent_state = tmp_path / "parent" / ".zf"
    inner_state = tmp_path / "inner" / ".zf"
    parent_state.mkdir(parents=True)
    inner_state.mkdir(parents=True)
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=parent_state,
        max_iterations=1,
        output_dir=tmp_path / "loop",
    )
    snap = EvalSnapshot(1, 0, 0, 0.0, 0, 0, 0)
    collector = _RecordingEvalCollector([snap])
    backlog_paths: list[Path] = []

    def _backlog(state_dir: Path) -> list[dict]:
        backlog_paths.append(state_dir)
        return []

    run_loop(
        cfg,
        autoresearch_fn=lambda **_: {
            "status": "passed",
            "tasks_done": 1,
            "expected_done": 1,
            "state_dir": str(inner_state),
        },
        eval_collector_fn=collector,
        reflect_fn=_StubReflect([
            ReflectionResult("best_so_far", [], "low", "", "")
        ]),
        git_head_fn=_StubGit(["sha1"]).head,
        git_diff_fn=lambda *_, **__: "",
        backlog_fn=_backlog,
        wait_for_fix_fn=lambda *_, **__: True,
    )

    assert collector.paths == [inner_state]
    assert backlog_paths == [inner_state]


# ---------------------------------------------------------------------------
# Scenario rotation
# ---------------------------------------------------------------------------


def test_driver_rotates_scenarios(tmp_path: Path) -> None:
    cfg = LoopConfig(
        scenarios=["scen-A", "scen-B"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / ".zf",
        max_iterations=4,
        output_dir=tmp_path / "loop",
    )
    cfg.parent_state_dir.mkdir(parents=True)
    ar = _StubAutoresearch([
        ("failed", 0, None), ("failed", 0, None),
        ("failed", 0, None), ("failed", 0, None),
    ])
    snap = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    run_loop(
        cfg,
        autoresearch_fn=ar,
        eval_collector_fn=_StubEvalCollector([snap] * 4),
        reflect_fn=_StubReflect([
            ReflectionResult("unknown", [], "medium", "", "")
        ] * 4),
        git_head_fn=_StubGit(["sha"] * 4).head,
        git_diff_fn=lambda *_, **__: "",
        backlog_fn=lambda _: [],
        wait_for_fix_fn=lambda *_, **__: True,
    )
    scenarios = [c["scenario"] for c in ar.calls]
    assert scenarios == ["scen-A", "scen-B", "scen-A", "scen-B"]


# ---------------------------------------------------------------------------
# Termination — max iterations
# ---------------------------------------------------------------------------


def test_driver_stops_at_max_iter_unmet(tmp_path: Path) -> None:
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / ".zf",
        max_iterations=2,
        output_dir=tmp_path / "loop",
    )
    cfg.parent_state_dir.mkdir(parents=True)
    ar = _StubAutoresearch([
        ("failed", 0, None), ("failed", 0, None),
        # 3rd entry should never be consumed
        ("failed", 0, None),
    ])
    snap = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    result = run_loop(
        cfg,
        autoresearch_fn=ar,
        eval_collector_fn=_StubEvalCollector([snap] * 3),
        reflect_fn=_StubReflect([
            ReflectionResult("unknown", [], "medium", "", "")
        ] * 3),
        git_head_fn=_StubGit(["sha1", "sha2", "sha3"]).head,
        git_diff_fn=lambda *_, **__: "",
        backlog_fn=lambda _: [],
        wait_for_fix_fn=lambda *_, **__: True,
    )
    assert result.iterations == 2
    assert result.final_status == "max_iter_unmet"
    assert len(ar.calls) == 2
    last = json.loads((cfg.output_dir / "journal.jsonl").read_text().splitlines()[-1])
    assert last["outcome"] == "failed"
    assert "stop_reason=max_iterations" in last["stop_reason"]
    assert "missing_done=3" in last["stop_reason"]


def test_driver_reports_done_when_expected_done_met_at_max_iter(
    tmp_path: Path,
) -> None:
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / ".zf",
        max_iterations=1,
        output_dir=tmp_path / "loop",
    )
    cfg.parent_state_dir.mkdir(parents=True)
    snap = EvalSnapshot(8, 3, 0, 0.179, 0, 0, 1)

    result = run_loop(
        cfg,
        autoresearch_fn=_StubAutoresearch([("passed", 3, None)]),
        eval_collector_fn=_StubEvalCollector([snap]),
        reflect_fn=_StubReflect([
            ReflectionResult("best_so_far", [], "low", "", "")
        ]),
        git_head_fn=_StubGit(["sha1"]).head,
        git_diff_fn=lambda *_, **__: "",
        backlog_fn=lambda _: [],
        wait_for_fix_fn=lambda *_, **__: True,
    )

    assert result.final_status == "done"
    last = json.loads((cfg.output_dir / "journal.jsonl").read_text().splitlines()[-1])
    assert last["outcome"] == "passed"
    assert last["final_status_if_stopped"] == "done"
    assert "stop_reason=max_iterations" in last["stop_reason"]


def test_driver_treats_passed_after_rework_as_done(tmp_path: Path) -> None:
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / ".zf",
        max_iterations=1,
        output_dir=tmp_path / "loop",
    )
    cfg.parent_state_dir.mkdir(parents=True)
    snap = EvalSnapshot(8, 3, 0, 0.179, 0, 1, 1)

    result = run_loop(
        cfg,
        autoresearch_fn=lambda **_: {
            "status": "passed_after_rework",
            "tasks_done": 1,
            "expected_done": 1,
            "rework_count": 1,
            "passed_after_rework": 1,
            "validation_kinds": ["byte_exact"],
        },
        eval_collector_fn=_StubEvalCollector([snap]),
        reflect_fn=_StubReflect([
            ReflectionResult("best_so_far", [], "low", "", "")
        ]),
        git_head_fn=_StubGit(["sha1"]).head,
        git_diff_fn=lambda *_, **__: "",
        backlog_fn=lambda _: [],
        wait_for_fix_fn=lambda *_, **__: True,
    )

    assert result.final_status == "done"
    report = (cfg.output_dir / "report.md").read_text()
    assert "validation=byte_exact" in report
    last = json.loads((cfg.output_dir / "journal.jsonl").read_text().splitlines()[-1])
    assert last["run_status"] == "passed_after_rework"
    assert last["passed_after_rework"] == 1


# ---------------------------------------------------------------------------
# Termination — convergence
# ---------------------------------------------------------------------------


def test_driver_converges_on_two_consecutive_passed(tmp_path: Path) -> None:
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / ".zf",
        max_iterations=10,
        output_dir=tmp_path / "loop",
    )
    cfg.parent_state_dir.mkdir(parents=True)
    ar = _StubAutoresearch([
        ("failed", 1, None),   # iter 1
        ("passed", 3, None),   # iter 2 — first pass
        ("passed", 3, None),   # iter 3 — 2nd consecutive passed → converge
        ("failed", 0, None),   # shouldn't reach
    ])
    eval_seq = [
        EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0),
        EvalSnapshot(8, 3, 4, 0.243, 5, 0, 1),   # improved (critical -3)
        EvalSnapshot(9, 3, 2, 0.4, 3, 0, 3),     # improved
        EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0),
    ]
    result = run_loop(
        cfg,
        autoresearch_fn=ar,
        eval_collector_fn=_StubEvalCollector(eval_seq),
        reflect_fn=_StubReflect([
            ReflectionResult("unknown", [], "medium", "", ""),
            ReflectionResult("best_so_far", [], "low", "", ""),
            ReflectionResult("best_so_far", [], "low", "", ""),
            ReflectionResult("best_so_far", [], "low", "", ""),
        ]),
        git_head_fn=_StubGit(["sha1", "sha2", "sha3", "sha4"]).head,
        git_diff_fn=lambda *_, **__: "",
        backlog_fn=lambda _: [],
        wait_for_fix_fn=lambda *_, **__: True,
    )
    assert result.final_status == "converged"
    assert result.iterations == 3
    assert len(ar.calls) == 3


# ---------------------------------------------------------------------------
# Termination — 3 regress streak
# ---------------------------------------------------------------------------


def test_driver_stops_on_three_regress_streak(tmp_path: Path) -> None:
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / ".zf",
        max_iterations=10,
        output_dir=tmp_path / "loop",
    )
    cfg.parent_state_dir.mkdir(parents=True)
    ar = _StubAutoresearch([
        ("failed", 0, None), ("failed", 0, None),
        ("failed", 0, None), ("failed", 0, None),
        ("failed", 0, None),
    ])
    # iter 1 → baseline.
    # iters 2, 3, 4 → regressed (critical climbing).
    eval_seq = [
        EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0),
        EvalSnapshot(8, 3, 9, 0.179, 8, 1, 0),    # regressed (+2)
        EvalSnapshot(8, 3, 11, 0.179, 8, 1, 0),   # regressed (+2)
        EvalSnapshot(8, 3, 14, 0.179, 8, 1, 0),   # regressed (+3) → stop after this
        EvalSnapshot(8, 3, 15, 0.179, 8, 1, 0),
    ]
    result = run_loop(
        cfg,
        autoresearch_fn=ar,
        eval_collector_fn=_StubEvalCollector(eval_seq),
        reflect_fn=_StubReflect([
            ReflectionResult("unknown", [], "medium", "", "")
        ] * 5),
        git_head_fn=_StubGit(["sha"] * 5).head,
        git_diff_fn=lambda *_, **__: "",
        backlog_fn=lambda _: [],
        wait_for_fix_fn=lambda *_, **__: True,
    )
    assert result.final_status == "no_progress"
    assert result.iterations == 4
    assert len(ar.calls) == 4


# ---------------------------------------------------------------------------
# Head-change wait
# ---------------------------------------------------------------------------


def test_driver_skips_wait_on_last_iter(tmp_path: Path) -> None:
    """Don't wait for HEAD change after the terminating iteration."""
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / ".zf",
        max_iterations=1,
        output_dir=tmp_path / "loop",
    )
    cfg.parent_state_dir.mkdir(parents=True)
    wait_calls = []

    def _wait(*a, **k):
        wait_calls.append(1)
        return True

    snap = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    run_loop(
        cfg,
        autoresearch_fn=_StubAutoresearch([("failed", 0, None)]),
        eval_collector_fn=_StubEvalCollector([snap]),
        reflect_fn=_StubReflect([
            ReflectionResult("unknown", [], "medium", "", "")
        ]),
        git_head_fn=_StubGit(["sha1"]).head,
        git_diff_fn=lambda *_, **__: "",
        backlog_fn=lambda _: [],
        wait_for_fix_fn=_wait,
    )
    # Since max_iterations=1, no wait should occur (we terminate after iter 1).
    assert wait_calls == []
