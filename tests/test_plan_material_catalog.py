"""The human catalog reports metadata; listing is not content acceptance."""
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_plan_document_reading import fixture
from zf.core.events import EventLog, EventWriter, ZfEvent


def test_catalog_is_bound_metadata_without_loading_the_source(tmp_path, monkeypatch):
    client, state, path, _, digest = fixture(tmp_path, "# Source\n", accepted=True)
    original = Path.open
    def metadata_only(self, *args, **kwargs):
        assert self != path, "listing must not load the source body"
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", metadata_only)
    before = (state / "events.jsonl").read_bytes()
    response = client.get("/api/projects/p/plans/plan/preview/materials")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["catalog_complete"] and data["next_offset"] is None
    item = data["items"][0]
    assert item["name"] == "accepted_plan" and item["sha256"] == digest
    assert item["media_type"] == "text/markdown" and item["size_bytes"] == len("# Source\n".encode())
    assert item["observed_state"] == "available" and item["integrity"] == "unread"
    assert item["supersedes"] is None
    assert response.headers["cache-control"] == "no-store"
    assert (state / "events.jsonl").read_bytes() == before


@pytest.mark.parametrize("fault,expected", [("empty", "empty"), ("missing", "unavailable"), ("outside", "forbidden")])
def test_catalog_does_not_conflate_empty_missing_and_forbidden(tmp_path, fault, expected):
    client, _, path, _, _ = fixture(tmp_path, "# Source\n", accepted=True)
    if fault == "empty": path.write_bytes(b"")
    elif fault == "missing": path.unlink()
    else:
        outside = tmp_path.parent / f"outside-{tmp_path.name}"
        outside.write_text("private")
        path.unlink(); path.symlink_to(outside)
    response = client.get("/api/projects/p/plans/plan/preview/materials")
    if fault == "outside":
        # This also invalidates the package's bound accepted_plan identity;
        # reject the entire untrusted catalog before publishing its entries.
        assert response.status_code == 409 and "items" not in response.json()
        assert "private" not in response.text
        return
    item = response.json()["items"][0]
    assert item["observed_state"] == expected and item["integrity"] == "unread"
    assert "private" not in response.text


def test_catalog_pagination_and_approval_identity(tmp_path):
    client, state, _, _, digest = fixture(tmp_path, "# Source\n", accepted=True)
    ports = [{"name": f"source-{i}", "ref": f"artifacts/source-{i}.md", "sha256": digest} for i in range(120)]
    body = json.dumps({"schema_version": "plan-preview-bundle.v1", "artifacts": ports}).encode()
    (state / "artifacts/large.json").write_bytes(body)
    writer = EventWriter(EventLog(state / "events.jsonl"))
    event = writer.append(ZfEvent(type="plan.approval.requested", payload={"plan_id": "plan",
        "plan_preview_bundle_ref": "artifacts/large.json", "plan_preview_bundle_digest": hashlib.sha256(body).hexdigest()}))
    url = "/api/projects/p/plans/plan/preview/materials"
    data = client.get(url, params={"limit": 30, "request_event_id": event.id}).json()
    assert len(data["items"]) == 30 and data["next_offset"] == 30 and not data["catalog_complete"]
    assert data["total"] == 120
    assert client.get(url, params={"offset": 90, "limit": 30}).json()["catalog_complete"]
    assert client.get(url, params={"request_event_id": "old"}).status_code == 409
    assert client.get(url, params={"limit": 101}).status_code == 422
    assert client.get(url.replace("/plan/", "/missing/")).status_code == 404
