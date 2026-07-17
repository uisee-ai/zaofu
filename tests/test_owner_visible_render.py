"""Card-quality regressions for owner_visible_render (2026-07-17 review).

The /tmp/runm.png forensics: internal enums leaked into owner cards
(``事件判定:no_missing``), and the three highest-frequency Run Manager
messages shipped their English internals verbatim.
"""

from __future__ import annotations

from zf.runtime.owner_visible_render import (
    humanize_owner_title,
    render_owner_message,
)


def _payload(**overrides):
    base = {
        "severity": "high",
        "title": "",
        "summary": "",
        "human_action_required": True,
    }
    base.update(overrides)
    return base


class TestVerdictWhitelist:
    def test_unmapped_verdict_line_is_dropped_not_leaked(self):
        # no_missing is an internal enum with no human meaning on a card; the
        # old fallback printed it verbatim under an unrelated title.
        body = render_owner_message(_payload(
            summary="claimed artifact missing on disk: a/b.md",
            events_derived_state={"verdict": "no_missing", "missing": []},
        ))
        assert "no_missing" not in body
        assert "事件判定" not in body

    def test_mapped_verdict_still_renders(self):
        body = render_owner_message(_payload(
            summary="worker.stuck",
            events_derived_state={"verdict": "stalled", "missing": []},
        ))
        assert "事件判定:任务疑似停滞" in body


class TestHighFrequencyReasonMappings:
    def test_budget_exceeded_renders_chinese(self):
        body = render_owner_message(_payload(
            title="cost.budget.exceeded requires runtime diagnosis",
            summary="cost.budget.exceeded requires runtime diagnosis",
        ))
        assert "成本已超预算上限" in body
        assert "cost.budget.exceeded" not in body.splitlines()[0]

    def test_requested_human_decision_renders_chinese(self):
        body = render_owner_message(_payload(
            title="Runtime escalated to human",
            summary="resident Run Manager requested human decision",
        ))
        assert "监工在等你的决定" in body

    def test_claimed_artifact_missing_renders_chinese(self):
        body = render_owner_message(_payload(
            title="Completion event claims artifacts/head that do not exist",
            summary="claimed artifact missing on disk: artifacts/x/plan.md",
        ))
        assert "完成证据不可信" in body


class TestHumanizeOwnerTitle:
    def test_known_title_maps_to_chinese(self):
        assert (
            humanize_owner_title(
                "Completion event claims artifacts/head that do not exist"
            )
            == "任务完成证据不可信(声称的产物在磁盘上不存在)"
        )

    def test_generic_title_collapses_to_standard_prompt(self):
        assert humanize_owner_title("Runtime escalated to human") == "运行需要你关注"

    def test_unknown_title_passes_through(self):
        assert humanize_owner_title("某个新问题") == "某个新问题"
