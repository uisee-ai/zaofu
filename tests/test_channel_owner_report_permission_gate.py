"""P0.3 — channel.owner_report must verify the requesting owner_id is a channel
member with `report_owner` permission. Mirrors the rejection pattern of
channel.member.add.rejected: 403 + channel.owner_report.rejected event +
_completed(status='rejected'), no owner_report.requested event written.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.web.server import create_app


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]")
    (sd / "feature_list.json").write_text("[]")
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    return sd


def _seed_member(
    state_dir: Path,
    *,
    channel_id: str,
    member_id: str,
    member_type: str = "owner_delegate",
    permissions: list[str] | None = None,
) -> None:
    payload = {
        "channel_id": channel_id,
        "member_id": member_id,
        "member_type": member_type,
    }
    if permissions is not None:
        payload["permissions"] = permissions
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="channel.member.added",
        actor="web",
        payload=payload,
        correlation_id=channel_id,
    ))


def _request_owner_report(
    client: TestClient,
    *,
    channel_id: str,
    owner_id: str,
    member_id: str = "boss-agent",
):
    return client.post(
        "/api/actions/channel.owner_report.request",
        headers={"x-zf-web-token": "test-token"},
        json={
            "channel_id": channel_id,
            "thread_id": "main",
            "owner_id": owner_id,
            "member_id": member_id,
            "period": "current",
        },
    )


class TestOwnerReportPermissionGate:
    def test_rejects_when_owner_id_not_a_member(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        client = TestClient(create_app(state_dir))

        r = _request_owner_report(client, channel_id="ch-zaofu", owner_id="ghost:nobody")

        assert r.status_code == 403
        body = r.json()
        assert body["ok"] is False
        assert body["status"] == "rejected"
        assert "lacks report_owner permission" in body["reason"]
        types = [e.type for e in EventLog(state_dir / "events.jsonl").read_all()]
        assert "channel.owner_report.rejected" in types
        assert "channel.owner_report.requested" not in types
        assert "channel.owner_report.generated" not in types

    def test_rejects_when_member_lacks_report_owner_permission(
        self, state_dir, monkeypatch
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        client = TestClient(create_app(state_dir))
        _seed_member(
            state_dir,
            channel_id="ch-zaofu",
            member_id="reader:min",
            member_type="observer",
            permissions=["read", "summarize"],
        )

        r = _request_owner_report(client, channel_id="ch-zaofu", owner_id="reader:min")

        assert r.status_code == 403
        types = [e.type for e in EventLog(state_dir / "events.jsonl").read_all()]
        assert "channel.owner_report.rejected" in types
        assert "channel.owner_report.generated" not in types

    def test_accepts_when_owner_delegate_with_default_permissions(
        self, state_dir, monkeypatch
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        client = TestClient(create_app(state_dir))
        _seed_member(
            state_dir,
            channel_id="ch-zaofu",
            member_id="owner:min",
            member_type="owner_delegate",
        )

        r = _request_owner_report(client, channel_id="ch-zaofu", owner_id="owner:min")

        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "generated"
        types = [e.type for e in EventLog(state_dir / "events.jsonl").read_all()]
        assert "channel.owner_report.requested" in types
        assert "channel.owner_report.generated" in types
        assert "channel.owner_report.rejected" not in types

    def test_rejected_event_carries_correlation_and_reason(
        self, state_dir, monkeypatch
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        client = TestClient(create_app(state_dir))

        _request_owner_report(client, channel_id="ch-zaofu", owner_id="ghost:nobody")

        rejected = [
            e for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "channel.owner_report.rejected"
        ]
        assert len(rejected) == 1
        evt = rejected[0]
        assert evt.correlation_id == "ch-zaofu"
        assert evt.payload["owner_id"] == "ghost:nobody"
        assert evt.payload["channel_id"] == "ch-zaofu"
        assert "report_owner" in evt.payload["reason"]
