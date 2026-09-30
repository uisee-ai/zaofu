from __future__ import annotations

from pathlib import Path

from issue_core import JsonIssueSessionStore

from zf.core.events import EventLog, EventWriter
from zf.core.events.model import ZfEvent
from zf.core.issue_triage.models import IssueMirror
from zf.core.issue_triage.store import IssueMirrorStore
from zf.integrations.feishu.issue_sync import build_issue_records, issue_field_specs, sync_issue_bitable
from zf.integrations.feishu.mock_clients import MockFeishuBitableClient
from zf.integrations.feishu.sync import FeishuSyncLedger
from zf.integrations.feishu.lark_cli import _field_json


def test_issue_bitable_projects_portable_session_and_is_idempotent(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    store = JsonIssueSessionStore(state_dir / "self-issues" / "portable-sessions")
    session = store.create("基础 wheel 未声明 httpx", attachments=[])

    records = build_issue_records(state_dir, synced_at="2026-09-29T00:00:00+00:00")
    assert len(records) == 1
    row = records[0]
    assert row["Issue Key"] == f"session:{session.session_id}"
    assert row["Created At"].startswith("20") and "+00:00" in row["Created At"]
    assert row["Current State"] == "created"

    client = MockFeishuBitableClient()
    ledger = FeishuSyncLedger.for_state_dir(state_dir)
    first = sync_issue_bitable(
        state_dir=state_dir, project_id="p", project_name="P", app_token="app",
        table_id="tbl", client=client, ledger=ledger,
        writer=EventWriter(EventLog(state_dir / "events.jsonl")),
    )
    second = sync_issue_bitable(
        state_dir=state_dir, project_id="p", project_name="P", app_token="app",
        table_id="tbl", client=client, ledger=ledger,
    )
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["updated"] == 1
    assert any(event.type == "feishu.issue_bitable.synced" for event in EventLog(state_dir / "events.jsonl").read_all())


def test_issue_bitable_dry_run_has_gantt_dates(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    JsonIssueSessionStore(state_dir / "self-issues" / "portable-sessions").create("bug")
    result = sync_issue_bitable(
        state_dir=state_dir, project_id="p", project_name="P", app_token="app",
        table_id="tbl", client=MockFeishuBitableClient(),
        ledger=FeishuSyncLedger.for_state_dir(state_dir), dry_run=True,
    )
    assert result["dry_run"] is True
    assert "ZaoFu Issue Sync" in result["markdown"]
    assert result["records"][0]["Start Time"]


def test_issue_date_fields_use_lark_datetime_schema() -> None:
    spec = next(item for item in issue_field_specs() if item["field_name"] == "Start Time")
    assert _field_json(spec) == {"name": "Start Time", "type": "datetime"}


def test_issue_bitable_projects_delivery_merged_from_issue_events(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    mirror = IssueMirror(
        issue_key="github:repo-1:7", provider="github", repository_id="repo-1",
        repository="acme/demo", number=7, node_id="I_7",
        html_url="https://github.com/acme/demo/issues/7", title="Merge me",
        author_login="owner", github_state="open",
        created_at="2026-09-14T00:00:00+00:00", updated_at="2026-09-14T00:00:00+00:00",
        last_seen_at="2026-09-14T00:00:00+00:00",
    )
    IssueMirrorStore(state_dir).upsert(mirror, "body")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(type="external_issue.received", payload={"source_key": mirror.issue_key, "source_revision": "r1"}))
    writer.append(ZfEvent(type="external_issue.triage.queued", task_id="ISSUE-1", payload={"source_key": mirror.issue_key, "workflow_run_id": "run-1", "source_revision": "r1"}))
    writer.append(ZfEvent(type="delivery.merged", task_id="ISSUE-1", correlation_id="run-1", payload={
        "source_key": mirror.issue_key, "pull_request_url": "https://github.com/acme/demo/pull/8",
        "merge_commit_sha": "abc123", "status": "merged",
    }))
    row = next(item for item in build_issue_records(state_dir, synced_at="2026-09-29T00:00:00+00:00") if item["Issue Key"] == mirror.issue_key)
    assert row["Current State"] == "merged"
    assert row["PR URL"] == "https://github.com/acme/demo/pull/8"
    assert row["Merge SHA"] == "abc123"


def test_issue_bitable_ignores_stale_delivery_run(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    mirror = IssueMirror(
        issue_key="github:repo-1:8", provider="github", repository_id="repo-1",
        repository="acme/demo", number=8, node_id="I_8",
        html_url="https://github.com/acme/demo/issues/8", title="Retry",
        author_login="owner", github_state="open",
        created_at="2026-09-14T00:00:00+00:00", updated_at="2026-09-14T00:00:00+00:00",
        last_seen_at="2026-09-14T00:00:00+00:00",
    )
    IssueMirrorStore(state_dir).upsert(mirror, "body")
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(type="external_issue.triage.queued", task_id="ISSUE-8", payload={
        "source_key": mirror.issue_key, "workflow_run_id": "run-new", "source_revision": "r2",
    }))
    writer.append(ZfEvent(type="delivery.merged", task_id="ISSUE-8", correlation_id="run-old", payload={
        "source_key": mirror.issue_key, "pull_request_url": "https://github.com/acme/demo/pull/1",
        "merge_commit_sha": "stale", "status": "merged",
    }))
    row = next(item for item in build_issue_records(state_dir, synced_at="2026-09-29T00:00:00+00:00") if item["Issue Key"] == mirror.issue_key)
    assert row["Merge SHA"] == ""
    assert row["PR URL"] == ""
