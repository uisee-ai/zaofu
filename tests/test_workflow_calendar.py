from datetime import datetime
import pytest
from zf.runtime.workflow_calendar import WorkflowCalendar


def test_cron_skips_spring_gap_and_only_uses_earlier_fold():
    d = datetime.fromisoformat
    zone = "America/New_York"
    spring = WorkflowCalendar("30 2 * * *", zone)
    assert spring.due(d("2026-03-08T00:00:00Z"), d("2026-03-09T00:00:00Z")) == []
    fall = WorkflowCalendar("30 1 * * *", zone)
    assert fall.due(d("2026-11-01T00:00:00Z"), d("2026-11-02T00:00:00Z"), limit=3) == [
        d("2026-11-01T05:30:00Z")
    ]


def test_cron_replays_latest_or_bounded_catchup():
    d = datetime.fromisoformat
    job = WorkflowCalendar("*/15 8-9 * * 1,2", "UTC")
    assert job.due(d("2026-11-01T00:00:00Z"), d("2026-11-03T08:31:00Z"), limit=2) == [
        d("2026-11-03T08:15:00Z"),
        d("2026-11-03T08:30:00Z"),
    ]
    for expr in ("1 1 2", "1-0 * * * *", "*/0 * * * *", "60 2 * * *"):
        with pytest.raises(ValueError):
            WorkflowCalendar(expr, "UTC")
