from __future__ import annotations

from datetime import datetime, timezone

from zf.integrations.feishu.clients import MockFeishuBitableClient
from zf.integrations.feishu.renderers import build_automation_insight_records
from zf.integrations.feishu.sync import FeishuSyncLedger, sync_automation_bitable


def test_automation_insight_records_include_highlight_fields() -> None:
    projection = {
        "project_id": "proj-a",
        "project_name": "Proj A",
        "generated_at": "2026-05-28T00:00:00+00:00",
        "items": [{
            "automation_id": "project-monitor",
            "status": "idle",
            "outputs": [{
                "summary": "runtime monitor",
                "insights": [
                    {
                        "id": "runtime-alert",
                        "severity": "warn",
                        "category": "runtime",
                        "title": "Runtime alerts active",
                        "summary": "provider tool alert",
                    },
                    {
                        "id": "decision",
                        "severity": "info",
                        "category": "channels",
                        "title": "Proposal waiting for decision",
                        "summary": "operator decision needed",
                    },
                    {
                        "id": "ok",
                        "severity": "ok",
                        "category": "summary",
                        "title": "No immediate automation concerns",
                        "summary": "clear",
                    },
                    {
                        "id": "zero-delivery",
                        "severity": "info",
                        "category": "delivery",
                        "title": "Weekly throughput",
                        "summary": "0 rework signals and 0 recent failures",
                        "metric": "0",
                    },
                ],
            }],
        }],
    }

    rows = build_automation_insight_records(projection, synced_at="2026-05-28T01:00:00+00:00")
    by_key = {row["Row Key"].split(":")[-1]: row for row in rows}

    assert by_key["runtime-alert"]["Highlight"] == "Runtime Alert"
    assert by_key["runtime-alert"]["Highlight Rank"] == "30"
    assert by_key["decision"]["Highlight"] == "Decision Needed"
    assert by_key["decision"]["Highlight Rank"] == "10"
    assert by_key["ok"]["Highlight"] == "Normal"
    assert by_key["ok"]["Highlight Reason"] == ""
    assert by_key["zero-delivery"]["Highlight"] == "Normal"


def test_automation_sync_marks_missing_today_rows_stale(tmp_path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    ledger = FeishuSyncLedger.for_state_dir(state_dir)
    today = datetime.now(timezone.utc).date().isoformat()
    stale_key = f"{today}:proj-a:project-monitor:insight:stale-alert"
    ledger.set_bitable_record_id(
        app_token="app",
        table_id="tbl",
        task_id=stale_key,
        record_id="rec-stale",
    )
    client = MockFeishuBitableClient()

    result = sync_automation_bitable(
        state_dir=state_dir,
        project_id="proj-a",
        project_name="Proj A",
        app_token="app",
        table_id="tbl",
        client=client,
        ledger=ledger,
    )

    stale_updates = [row for row in client.updated if row[2] == "rec-stale"]
    assert result["stale_updated"] == 1
    assert stale_updates[0][3]["Record Type"] == "stale"
    assert stale_updates[0][3]["Highlight"] == "Normal"
