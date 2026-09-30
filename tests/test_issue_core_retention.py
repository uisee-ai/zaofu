from datetime import datetime, timedelta, timezone

from issue_core.retention import IssueRetentionPolicy


def test_retention_clock_keeps_active_work_and_expires_terminal_classes() -> None:
    policy = IssueRetentionPolicy()
    terminal = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert not policy.due("private_evidence", now=terminal + timedelta(days=300), terminal_at=None)
    assert not policy.due(
        "private_evidence", now=terminal + timedelta(days=29, hours=23),
        terminal_at=terminal,
    )
    assert policy.due(
        "private_evidence", now=terminal + timedelta(days=30), terminal_at=terminal,
    )
    assert policy.due(
        "semantic_metadata", now=terminal + timedelta(days=90), terminal_at=terminal,
    )
    assert policy.due(
        "draft_recovery", now=terminal + timedelta(days=7), terminal_at=terminal,
    )
