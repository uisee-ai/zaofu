"""Sprint §3 — reflection prompt builder tests."""

from __future__ import annotations

import json
import subprocess

from zf.autoresearch.loop import (
    EvalDelta,
    EvalSnapshot,
    IterationRecord,
    ReflectionResult,
    build_reflection_prompt,
    invoke_reflection_llm,
    parse_reflection_response,
)


def _make_record(
    iter: int = 1,
    *,
    delta: EvalDelta | None = None,
    summary: str = "iter 1",
) -> IterationRecord:
    snap = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    return IterationRecord(
        iter=iter, started_at="2026-05-19T07:00:00+00:00",
        scenario="self-eval-backlog", run_id=f"run-{iter}",
        run_status="failed", tasks_done=0, expected_done=3,
        eval=snap, delta=delta, reflect=None,
        git_head=f"sha{iter:03d}",
        head_changed_since_prev=(iter > 1),
        summary=summary,
    )


# ---------------------------------------------------------------------------
# build_reflection_prompt
# ---------------------------------------------------------------------------


def test_first_iteration_prompt_notes_no_baseline() -> None:
    curr = _make_record(1, delta=None)
    prompt = build_reflection_prompt(
        curr=curr, prev=None, git_diff="", open_backlog=[],
    )
    assert "第 1 轮" in prompt
    assert "self-eval-backlog" in prompt
    # No baseline → prompt must acknowledge it explicitly so LLM does
    # not hallucinate a comparison.
    assert "无基线" in prompt or "首轮" in prompt or "no baseline" in prompt.lower()


def test_prompt_includes_delta_block() -> None:
    delta = EvalDelta(
        healthy_delta=1, critical_delta=-2,
        coordinator_delta=0.064, backlog_delta=-2,
        completed_delta=1, verdict="improved",
    )
    prev = _make_record(1)
    curr = _make_record(2, delta=delta, summary="iter 2")
    prompt = build_reflection_prompt(
        curr=curr, prev=prev, git_diff="", open_backlog=[],
    )
    # Delta numbers must appear in the prompt so LLM can reason on them.
    assert "critical" in prompt
    assert "-2" in prompt
    assert "improved" in prompt
    assert "coordinator" in prompt or "coord" in prompt


def test_prompt_includes_git_diff_when_provided() -> None:
    diff = """diff --git a/src/x.py b/src/x.py
+def foo():
+    return 42
"""
    curr = _make_record(2, delta=None)
    prompt = build_reflection_prompt(
        curr=curr, prev=_make_record(1), git_diff=diff, open_backlog=[],
    )
    assert "diff --git" in prompt
    assert "def foo" in prompt


def test_prompt_truncates_long_git_diff() -> None:
    """Git diff over 5KB must be truncated so the prompt stays within
    LLM context budgets."""
    huge = "x" * 20_000
    curr = _make_record(2, delta=None)
    prompt = build_reflection_prompt(
        curr=curr, prev=_make_record(1), git_diff=huge, open_backlog=[],
    )
    # Truncated content has a sentinel.
    assert "truncated" in prompt.lower() or "略" in prompt
    # The full 20KB must NOT be in prompt.
    assert prompt.count("x") < 10_000


def test_prompt_lists_open_backlog_top5() -> None:
    backlog = [
        {"id": f"TASK-B{i:03d}", "title": f"bug {i}", "priority": i}
        for i in range(10)
    ]
    curr = _make_record(2, delta=None)
    prompt = build_reflection_prompt(
        curr=curr, prev=_make_record(1), git_diff="", open_backlog=backlog,
    )
    # Top-5 priority shown; lower-priority entries elided.
    assert "TASK-B000" in prompt
    assert "TASK-B004" in prompt
    # 6th and below should NOT all appear (we cap at 5).
    visible = sum(1 for i in range(10) if f"TASK-B{i:03d}" in prompt)
    assert visible <= 5, f"expected top-5 only, got {visible}"


def test_prompt_asks_canonical_questions_and_generalization_reflection() -> None:
    curr = _make_record(1)
    prompt = build_reflection_prompt(
        curr=curr, prev=None, git_diff="", open_backlog=[],
    )
    # The prompt must ask the canonical reflection questions.
    assert "更好" in prompt or "替代" in prompt
    assert "下一轮" in prompt or "下轮" in prompt
    assert "风险" in prompt
    assert "更优雅" in prompt
    assert "通用" in prompt
    assert "其他产品" in prompt
    # JSON-format ask must be present so we can parse the reply.
    assert "JSON" in prompt or "json" in prompt
    assert "verdict" in prompt
    assert "alternatives" in prompt
    assert "rec_for_next_iter" in prompt


# ---------------------------------------------------------------------------
# parse_reflection_response
# ---------------------------------------------------------------------------


def test_parse_clean_json_response() -> None:
    raw = json.dumps({
        "verdict": "best_so_far",
        "alternatives": ["try X", "try Y"],
        "risk": "low",
        "rec_for_next_iter": "run controlled-stuck-recovery",
    })
    r = parse_reflection_response(raw)
    assert isinstance(r, ReflectionResult)
    assert r.verdict == "best_so_far"
    assert r.alternatives == ["try X", "try Y"]
    assert r.risk == "low"
    assert r.rec_for_next_iter == "run controlled-stuck-recovery"
    assert r.raw_response == raw


def test_parse_response_with_prose_around_json() -> None:
    """LLMs often pad JSON with explanation prose. The parser must
    extract the first valid JSON block."""
    raw = """Let me think about this...

```json
{
  "verdict": "better_fix_exists",
  "alternatives": ["fix P0-2 first"],
  "risk": "medium",
  "rec_for_next_iter": "tighten static_gate"
}
```

That's my recommendation."""
    r = parse_reflection_response(raw)
    assert r.verdict == "better_fix_exists"
    assert "fix P0-2 first" in r.alternatives
    assert r.risk == "medium"


def test_parse_invalid_json_falls_back() -> None:
    """If the response has no valid JSON, return an 'unknown' verdict
    fallback rather than raising."""
    raw = "I cannot determine the answer here."
    r = parse_reflection_response(raw)
    assert r.verdict == "unknown"
    assert r.risk == "medium"   # safe default
    assert r.alternatives == []
    assert r.raw_response == raw


def test_parse_unknown_verdict_normalizes() -> None:
    """If LLM returns a verdict outside the enum, fall back to 'unknown'."""
    raw = json.dumps({
        "verdict": "needs_more_thinking",
        "alternatives": [],
        "risk": "low",
        "rec_for_next_iter": "x",
    })
    r = parse_reflection_response(raw)
    assert r.verdict == "unknown"  # outside enum → coerced


def test_parse_missing_fields_filled_with_defaults() -> None:
    raw = json.dumps({"verdict": "best_so_far"})
    r = parse_reflection_response(raw)
    assert r.verdict == "best_so_far"
    assert r.alternatives == []
    assert r.risk == "medium"
    assert r.rec_for_next_iter == ""


def test_invoke_reflection_unsupported_backend_has_deterministic_fallback() -> None:
    r = invoke_reflection_llm("prompt", backend="missing-backend")

    assert r.verdict == "unknown"
    assert r.risk == "medium"
    assert r.alternatives
    assert "deterministic fallback" in r.alternatives[0]
    assert "self-eval-backlog" in r.rec_for_next_iter
    assert "unsupported backend" in r.raw_response


def test_invoke_reflection_nonzero_backend_has_deterministic_fallback(monkeypatch) -> None:
    def fake_run(*args, **kwargs):  # noqa: ANN001, ANN202 - pytest stub
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    r = invoke_reflection_llm("prompt", backend="claude-code")

    assert r.verdict == "unknown"
    assert r.risk == "medium"
    assert r.alternatives
    assert "prompt-only" in r.alternatives[1]
    assert "why_not_done_count" in r.rec_for_next_iter
    assert "reflection llm exit 1" in r.raw_response
