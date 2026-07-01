from __future__ import annotations

from zf.runtime.fanout_briefing_scope import (
    build_fanout_scope_summary,
    render_fanout_scope_briefing_lines,
)


def test_fanout_scope_summary_splits_with_and_without_output() -> None:
    summary = build_fanout_scope_summary(
        {
            "fanout_id": "fanout-1",
            "stage_id": "review",
            "children": [
                {
                    "child_id": "review-a",
                    "role_instance": "review-a",
                    "status": "completed",
                    "report_path": "fanouts/fanout-1/children/review-a/report.json",
                },
                {
                    "child_id": "review-b",
                    "role_instance": "review-b",
                    "status": "dispatched",
                },
            ],
        },
    )

    assert summary["total_children"] == 2
    assert [child["child_id"] for child in summary["with_output"]] == ["review-a"]
    assert [child["child_id"] for child in summary["without_output"]] == ["review-b"]


def test_render_fanout_scope_briefing_lines_names_missing_children() -> None:
    lines = render_fanout_scope_briefing_lines(
        {
            "fanout_id": "fanout-1",
            "stage_id": "review",
            "children": [
                {"child_id": "review-a", "status": "completed"},
                {"child_id": "review-b", "status": "dispatched"},
            ],
        },
        reports=[{
            "child_id": "review-a",
            "report": {"summary": "ok"},
        }],
    )
    text = "\n".join(lines)

    assert "## Fanout Scope Summary" in text
    assert "with_output: `review-a`" in text
    assert "without_output: `review-b`" in text
    assert "Treat every child in `without_output`" in text


def test_render_fanout_scope_briefing_lines_budget_fallback() -> None:
    children = [
        {
            "child_id": f"review-{idx}",
            "role_instance": f"review-{idx}",
            "status": "completed",
            "report_path": f"fanouts/fanout-1/children/review-{idx}/report.json",
        }
        for idx in range(5)
    ]

    lines = render_fanout_scope_briefing_lines(
        {
            "fanout_id": "fanout-1",
            "stage_id": "review",
            "children": children,
        },
        max_children=2,
    )
    text = "\n".join(lines)

    assert "context_budget_fallback: showing 2/5 child rows" in text
    assert "with_output: `review-0`, `review-1` (+3 omitted)" in text
    assert "`review-2` role=" not in text
    assert "3 child rows omitted for context budget" in text


def test_render_fanout_scope_briefing_filters_stale_instance() -> None:
    manifest = {
        "fanout_id": "fanout-old",
        "stage_id": "review",
        "children": [
            {
                "child_id": "review-a",
                "role_instance": "review-a",
                "status": "completed",
                "report_path": "fanouts/fanout-old/children/review-a/report.json",
            },
        ],
    }

    summary = build_fanout_scope_summary(
        manifest,
        current_status={
            "current": False,
            "stale_reason": "superseded_by_latest_fanout",
            "superseded_by": "fanout-new",
        },
    )
    lines = render_fanout_scope_briefing_lines(
        manifest,
        current_status={
            "current": False,
            "stale_reason": "superseded_by_latest_fanout",
            "superseded_by": "fanout-new",
        },
    )
    text = "\n".join(lines)

    assert summary["stale_filtered"] is True
    assert summary["total_children"] == 0
    assert summary["with_output"] == []
    assert "current_instance: false" in text
    assert "superseded_by: `fanout-new`" in text
    assert "`review-a` role=" not in text
