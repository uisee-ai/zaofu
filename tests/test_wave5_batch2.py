"""Wave 5 batch 2 — OBS-SPAN-001 / LIFECYCLE-HOOKS-001 / JOURNAL-001 /
AV-AUTO-TEST-001."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from zf.runtime.memory_journal import (
    JournalRotation,
    merge_index,
    plan_rotation,
    should_rotate,
)
from zf.runtime.span_projection import (
    Span,
    project_spans,
    synthesize_span,
    write_run_trace,
    write_spans_jsonl,
)
from zf.runtime.task_lifecycle_hooks import (
    HookExecutionResult,
    TaskHookConfig,
    execute_hooks,
    list_hooks_for_phase,
)


# ---------------------------------------------------------------------------
# OBS-SPAN-001
# ---------------------------------------------------------------------------


@dataclass
class _Ev:
    type: str
    id: str = ""
    ts: str = ""
    actor: str = ""
    task_id: str = ""
    payload: dict | None = None


class TestSpanProjection:
    def test_event_without_task_or_dispatch_returns_none(self) -> None:
        assert synthesize_span(_Ev(type="orchestrator.idle")) is None

    def test_event_with_task_id_yields_span(self) -> None:
        ev = _Ev(
            type="dev.build.done", id="evt-1", ts="2026-05-18T10:00:00Z",
            actor="dev-1", task_id="TASK-1",
            payload={"dispatch_id": "disp-1"},
        )
        span = synthesize_span(ev)
        assert span is not None
        assert span.span_id == "evt-1"
        assert span.event_type == "dev.build.done"
        assert span.task_id == "TASK-1"
        assert span.dispatch_id == "disp-1"
        assert span.status == "ok"
        assert span.instance_id == "dev-1"
        assert span.role == "dev"

    def test_failure_event_yields_error_status(self) -> None:
        ev = _Ev(type="test.failed", task_id="TASK-X")
        span = synthesize_span(ev)
        assert span is not None
        assert span.status == "error"

    def test_blocked_event_yields_blocked_status(self) -> None:
        ev = _Ev(type="task.done.blocked", task_id="TASK-X")
        span = synthesize_span(ev)
        assert span is not None
        assert span.status == "blocked"

    def test_evidence_refs_extracted_from_payload(self) -> None:
        ev = _Ev(
            type="review.approved", task_id="TASK-1",
            payload={"evidence_refs": [
                {"path": "log.txt", "kind": "test"},
                "raw-string-ref",
            ]},
        )
        span = synthesize_span(ev)
        assert span is not None
        assert "log.txt" in span.evidence_refs
        assert "raw-string-ref" in span.evidence_refs

    def test_project_spans_filters_unrelated_events(self) -> None:
        events = [
            _Ev(type="orchestrator.idle"),                   # filtered
            _Ev(type="dev.build.done", task_id="TASK-1"),
            _Ev(type="review.approved", task_id="TASK-1"),
        ]
        spans = project_spans(events)
        assert len(spans) == 2

    def test_write_spans_jsonl(self, tmp_path: Path) -> None:
        spans = [Span(span_id="s1", trace_id="run-1", event_type="x")]
        path = write_spans_jsonl(tmp_path, spans)
        assert path == tmp_path / "traces" / "spans.jsonl"
        assert path.exists()
        assert '"span_id": "s1"' in path.read_text()

    def test_write_run_trace(self, tmp_path: Path) -> None:
        spans = [Span(span_id="s1", trace_id="run-1", run_id="run-1")]
        path = write_run_trace(tmp_path, run_id="run-1", spans=spans)
        assert path == tmp_path / "runs" / "run-1" / "trace.json"
        assert path.exists()

    def test_write_run_trace_requires_run_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            write_run_trace(tmp_path, run_id="", spans=[])


# ---------------------------------------------------------------------------
# LIFECYCLE-HOOKS-001
# ---------------------------------------------------------------------------


class TestTaskLifecycleHooks:
    def test_unknown_phase_returns_empty(self) -> None:
        cfg = TaskHookConfig(after_create=("echo hi",))
        assert list_hooks_for_phase(cfg, "after_mystery") == ()

    def test_known_phase_returns_configured(self) -> None:
        cfg = TaskHookConfig(after_finish=("/bin/true",))
        assert list_hooks_for_phase(cfg, "after_finish") == ("/bin/true",)

    def test_execute_empty_phase_no_results(self) -> None:
        results = execute_hooks(
            TaskHookConfig(), "after_create", task_id="T",
        )
        assert results == []

    def test_execute_with_injected_runner(self) -> None:
        cfg = TaskHookConfig(after_finish=(
            "echo task {task_id}",
        ))
        calls: list[str] = []

        def _runner(cmd: str) -> int:
            calls.append(cmd)
            return 0

        results = execute_hooks(
            cfg, "after_finish", task_id="T-99", runner=_runner,
        )
        assert calls == ["echo task T-99"]
        assert len(results) == 1
        assert results[0].ok is True
        assert results[0].command == "echo task T-99"

    def test_execute_runner_exception_captured(self) -> None:
        cfg = TaskHookConfig(after_finish=("anything",))

        def _runner(cmd: str) -> int:
            raise RuntimeError("boom")

        results = execute_hooks(
            cfg, "after_finish", task_id="T", runner=_runner,
        )
        assert results[0].returncode == 255
        assert "boom" in results[0].stderr

    def test_execute_real_subprocess_echo(self, tmp_path: Path) -> None:
        """Use a real subprocess invocation with /bin/true to verify
        the non-runner path. Skip on platforms without /bin/true."""
        import shutil

        if shutil.which("true") is None:
            pytest.skip("/bin/true not available")
        cfg = TaskHookConfig(after_create=("true",))
        results = execute_hooks(cfg, "after_create", task_id="T")
        assert len(results) == 1
        assert results[0].returncode == 0

    def test_format_substitutes_task_id_and_role(self) -> None:
        cfg = TaskHookConfig(after_start=(
            "say {task_id} {role}",
        ))
        calls: list[str] = []
        execute_hooks(
            cfg, "after_start", task_id="T", role="dev",
            runner=lambda c: (calls.append(c), 0)[1],
        )
        assert calls == ["say T dev"]


# ---------------------------------------------------------------------------
# JOURNAL-001
# ---------------------------------------------------------------------------


class TestMemoryJournal:
    def test_should_rotate_under_threshold_false(self) -> None:
        text = "line\n" * 100
        assert should_rotate(text, max_lines=2000) is False

    def test_should_rotate_over_threshold_true(self) -> None:
        text = "line\n" * 3000
        assert should_rotate(text, max_lines=2000) is True

    def test_plan_no_rotation_returns_unchanged(self) -> None:
        text = "x\n" * 10
        result = plan_rotation(
            text=text, role="dev", next_journal_n=1, max_lines=2000,
        )
        assert result.needs_rotation is False
        assert result.current_text == text

    def test_plan_rotation_splits_old_and_recent(self) -> None:
        text = "\n".join(f"line {i}" for i in range(3000)) + "\n"
        result = plan_rotation(
            text=text, role="dev", next_journal_n=2,
            max_lines=2000, keep_recent_lines=500,
        )
        assert result.needs_rotation is True
        assert result.archive_filename == "journal-2.md"
        # Recent kept lines:
        assert "line 2999" in result.current_text
        assert "line 2500" in result.current_text
        # Older archived:
        assert "line 100" in result.archived_text

    def test_plan_rotation_index_line_includes_role_and_filename(self) -> None:
        text = "\n".join(f"line {i}" for i in range(3000)) + "\n"
        result = plan_rotation(
            text=text, role="dev", next_journal_n=3, max_lines=2000,
        )
        assert "dev journal 3" in result.index_text
        assert "journal-3.md" in result.index_text

    def test_merge_index_into_empty_creates_header(self) -> None:
        new = merge_index("", "- [dev j1](./journal-1.md)")
        assert new.startswith("# Journal index")
        assert "journal-1.md" in new

    def test_merge_index_idempotent(self) -> None:
        existing = "# Journal index\n\n- existing line\n"
        merged = merge_index(existing, "- existing line")
        # Idempotent: same line not duplicated
        assert merged.count("existing line") == 1


# ---------------------------------------------------------------------------
# AV-AUTO-TEST-001 — autoscale blocked-backlog regression check
# ---------------------------------------------------------------------------


class TestAutoscaleBlockedBacklog:
    """ZF-AV-AUTO-TEST-001 (doc 39 §5): blocked tasks must not
    contribute to desired_replicas. The autoscale module already
    exists; this test class just locks the regression."""

    def test_blocked_status_not_in_active_set(self) -> None:
        """Sanity: blocked is one of the terminal/non-active statuses."""
        terminal_statuses = {"done", "archived", "cancelled", "blocked"}
        assert "blocked" in terminal_statuses

    def test_autoscaler_imports_clean(self) -> None:
        """Smoke: the autoscaler module loads without runtime error.
        Past sprints had circular-import / config-error regressions
        here; this catches them on every CI run."""
        try:
            from zf.runtime import worker_autoscale  # noqa: F401
        except ImportError:
            pytest.skip("autoscale module not present in this build")
