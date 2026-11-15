"""Bound source chapters use actual Web routes without rebuilding the full Plan."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.web.plan_health_routes import build_plan_health_router


def fixture(root, content, *, authority=False, accepted=False, ref="artifacts/source.md"):
    state = root / "state"
    (state / "artifacts").mkdir(parents=True)
    path = state / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode())
    digest = hashlib.sha256(content.encode()).hexdigest()
    name = "accepted_plan" if accepted else "requirement_1_source.md" if authority else "requirement_source"
    package = {"schema_version": "plan-preview-bundle.v1", "artifacts": [] if authority else [
        {"name": name, "ref": ref, "sha256": digest}]}
    body = json.dumps(package).encode()
    (state / "artifacts/bundle.json").write_bytes(body)
    writer = EventWriter(EventLog(state / "events.jsonl"))
    writer.append(ZfEvent(type="plan.approval.requested", payload={
        "plan_id": "plan", "plan_artifact_ref": ref if accepted else "artifacts/unrelated-plan.md",
        "plan_preview_bundle_ref": "artifacts/bundle.json",
        "plan_preview_bundle_digest": hashlib.sha256(body).hexdigest(),
        **({"authority_refs": [ref]} if authority else {}),
    }))
    app = FastAPI()
    ctx = SimpleNamespace(state_dir=state, project_root=root, config=None)
    app.include_router(build_plan_health_router(resolve_ctx=lambda project: ctx if project == "p" else None))
    client = TestClient(app)
    return client, state, path, f"/api/projects/p/plans/plan/artifacts/{name}/document", digest


@pytest.mark.parametrize("authority", [False, True])
def test_index_and_selected_chapter_do_not_build_or_deliver_unrelated_bodies(tmp_path, monkeypatch, authority):
    content = "# 标题\n\n开头\n\n<a id=\"stock\"></a>\n\n## 库存\n\n**不得重复扣减**。\n\n## 尾部\n\nUNRELATED-TAIL\n"
    client, state, _, url, digest = fixture(tmp_path, content, authority=authority)
    monkeypatch.setattr("zf.runtime.operator_plan_preview.build_plan_preview",
                        lambda *a, **kw: pytest.fail("full preview must not be rebuilt for a bound source"))
    before = (state / "events.jsonl").read_bytes()
    response = client.get(url, params={"sha256": digest, "anchor": "stock"})
    assert response.status_code == 200, response.text
    index = response.json()
    assert index["section_count"] == 3
    assert index["selected_section"] == 1
    assert "UNRELATED-TAIL" not in response.text
    assert response.headers["cache-control"] == "no-store"
    result = client.get(url + "/sections/1", params={"sha256": digest}).json()
    assert "**不得重复扣减**" in result["content"]
    assert "UNRELATED-TAIL" not in result["content"]
    assert result["section_complete"] is True
    assert result["scope"] == "section_page"
    assert (state / "events.jsonl").read_bytes() == before


@pytest.mark.parametrize("authority", [False, True])
def test_bound_download_does_not_rebuild_unrelated_full_preview(tmp_path, monkeypatch, authority):
    content = "# 需求\n\n选定的完整原文。\n"
    client, state, path, url, digest = fixture(tmp_path, content, authority=authority)
    monkeypatch.setattr("zf.runtime.operator_plan_preview.build_plan_preview",
                        lambda *a, **kw: pytest.fail("download must not rebuild unrelated review bodies"))
    before = (state / "events.jsonl").read_bytes()
    response = client.get(url.removesuffix('/document'), params={"sha256": digest})
    assert response.status_code == 200, response.text
    assert response.text == content
    path.write_text("# 新版\n旧链接不能下载替换后的正文。\n")
    refused = client.get(url.removesuffix('/document'), params={"sha256": digest})
    assert refused.status_code in {404, 409}
    assert '替换后的正文' not in refused.text
    assert (state / "events.jsonl").read_bytes() == before


def test_sections_round_trip_utf8_and_long_lines_without_hidden_truncation(tmp_path):
    content = "intro\r\n\r\n标题\r\n====\r\n\r\n```python\r\n# not a heading\r\n```\r\n\r\n## Huge\n" + "数据😀" * 20000 + "\nEND\n"
    client, _, _, url, digest = fixture(tmp_path, content)
    index = client.get(url, params={"sha256": digest}).json()
    assert index["section_count"] == 3
    chunks = []
    for entry in index["sections"]:
        for page in range(entry["page_count"]):
            response = client.get(url + f"/sections/{entry['id']}", params={"sha256": digest, "page": page})
            assert response.status_code == 200
            part = response.json()
            assert len(part["content"].encode()) <= 65536
            assert part["section_complete"] == (entry["page_count"] == 1)
            chunks.append(part["content"])
    assert "".join(chunks) == content


def test_index_is_paginated_and_bookmark_can_reach_final_chapter(tmp_path):
    content = "".join(f"## Chapter {i}\n\nClause {i}\n\n" for i in range(250))
    client, _, _, url, digest = fixture(tmp_path, content)
    first = client.get(url, params={"sha256": digest, "limit": 30}).json()
    assert len(first["sections"]) == 30
    assert first["next_offset"] == 30
    tail = client.get(url, params={"sha256": digest, "offset": 240, "limit": 30}).json()
    assert len(tail["sections"]) == 10 and tail["next_offset"] is None
    bookmark = client.get(url, params={"sha256": digest, "anchor": "section-250"}).json()
    assert bookmark["selected_section"] == 249
    assert any(row["id"] == 249 for row in bookmark["sections"])


@pytest.mark.parametrize("fault,code", [("digest", 409), ("changed", 409), ("deleted", 404),
                                       ("outside", 403), ("unbound", 404), ("other_project", 404)])
def test_changed_missing_or_unbound_source_never_reuses_cached_body(tmp_path, fault, code):
    client, _, path, url, digest = fixture(tmp_path, "# original\n\nPinned content\n")
    assert client.get(url, params={"sha256": digest}).status_code == 200
    if fault == "digest": digest = "f" * 64
    elif fault == "changed": path.write_text("# changed\nnew source")
    elif fault == "deleted": path.unlink()
    elif fault == "outside":
        outside = tmp_path.parent / f"outside-{tmp_path.name}.md"
        outside.write_text("PRIVATE")
        path.unlink(); path.symlink_to(outside)
    elif fault == "unbound": url = url.replace("requirement_source", "unknown")
    elif fault == "other_project": url = url.replace("/projects/p/", "/projects/other/")
    response = client.get(url + "/sections/0", params={"sha256": digest})
    assert response.status_code == code, response.text
    assert "Pinned content" not in response.text and "PRIVATE" not in response.text


def test_invalid_coordinates_and_digest_are_explicit(tmp_path):
    client, _, _, url, digest = fixture(tmp_path, "# A\n")
    for params in ({"sha256": "bad"}, {"sha256": digest, "offset": -1}, {"sha256": digest, "limit": 1000}):
        assert client.get(url, params=params).status_code == 422
    assert client.get(url + "/sections/4", params={"sha256": digest}).status_code == 404
    assert client.get(url + "/sections/0", params={"sha256": digest, "page": 8}).status_code == 416


def test_warm_chapter_read_does_not_rescan_source(tmp_path, monkeypatch):
    content = "# A\n\n" + "x\n" * 40000 + "\n## B\n\nselected\n"
    client, _, path, url, digest = fixture(tmp_path, content)
    assert client.get(url, params={"sha256": digest}).status_code == 200
    original = Path.open

    class Guard:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): self.stream.__enter__(); return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def __getattr__(self, key): return getattr(self.stream, key)
        def __iter__(self): pytest.fail("cached page must not rescan document lines")
        def read(self, size=-1):
            assert 0 <= size <= 65540, "chapter request must use a bounded range"
            return self.stream.read(size)

    def open_file(self, *args, **kwargs):
        stream = original(self, *args, **kwargs)
        return Guard(stream) if self == path else stream

    monkeypatch.setattr(Path, "open", open_file)
    result = client.get(url + "/sections/1", params={"sha256": digest})
    assert result.status_code == 200, result.text
    assert "selected" in result.json()["content"]


def test_search_is_bounded_and_reaches_body_match_across_page_boundary(tmp_path):
    content = "# Start\n" + "x" * (65536 - 10) + "😀NEEDLE-boundary\n"
    content += "".join(f"\n## End {i}\nneedle result {i}\n" for i in range(110))
    client, state, _, url, digest = fixture(tmp_path, content)
    before = (state / "events.jsonl").read_bytes()
    result = client.get(url, params={"sha256": digest, "q": "needle", "limit": 2}).json()
    assert [row["id"] for row in result["sections"]] == [0, 1]
    assert result["next_offset"] == 2
    assert "needle-boundary" not in json.dumps(result)
    unicode_match = client.get(url, params={"sha256": digest, "q": "😀needle"}).json()
    assert [row["id"] for row in unicode_match["sections"]] == [0]
    result = client.get(url, params={"sha256": digest, "q": "result 109"}).json()
    assert result["sections"][0]["id"] == 110 and result["next_offset"] is None
    assert (state / "events.jsonl").read_bytes() == before


def test_disposable_cache_rebuilds_without_changing_source_or_ledger(tmp_path):
    client, state, path, url, digest = fixture(tmp_path, "# Original\nbody\n")
    before = (state / "events.jsonl").read_bytes(), path.read_bytes()
    expected = client.get(url, params={"sha256": digest}).json()
    cache = next((state / "cache/plan-documents").glob("*.sqlite"))
    cache.write_bytes(b"broken cache")
    response = client.get(url, params={"sha256": digest})
    assert response.status_code == 200 and response.json() == expected
    assert ((state / "events.jsonl").read_bytes(), path.read_bytes()) == before


def test_same_size_rewrite_with_restored_mtime_invalidates_cache(tmp_path):
    import os
    client, _, path, url, digest = fixture(tmp_path, "# Old\n")
    assert client.get(url, params={"sha256": digest}).status_code == 200
    stat = path.stat()
    path.write_text("# New\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert client.get(url, params={"sha256": digest}).status_code == 409


def test_complete_download_checks_requested_version(tmp_path):
    content = "# Original\n完整原文😀\n"
    client, _, _, url, digest = fixture(tmp_path, content)
    response = client.get(url.removesuffix("/document"), params={"sha256": digest})
    assert response.status_code == 200 and response.text == content
    assert client.get(url.removesuffix("/document"), params={"sha256": "f" * 64}).status_code == 409


@pytest.mark.parametrize("fault,status", [("type", 415), ("encoding", 415), ("size", 413)])
def test_unsupported_source_never_looks_like_empty_success(tmp_path, fault, status):
    ref = "artifacts/source.txt" if fault == "type" else "artifacts/source.md"
    client, _, path, url, digest = fixture(tmp_path, "# original\n", ref=ref)
    if fault == "encoding":
        path.write_bytes(b"\xff\n")
    elif fault == "size":
        with path.open("wb") as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
    response = client.get(url, params={"sha256": digest})
    assert response.status_code == status
    assert "sections" not in response.json()


def test_accepted_plan_download_and_chapter_skip_unrelated_review_rendering(tmp_path, monkeypatch):
    content = "# Plan\n\n## First\nKeep current rules.\n\n## Last\nEND-PLAN\n"
    client, state, _, url, digest = fixture(tmp_path, content, accepted=True)
    monkeypatch.setattr("zf.runtime.operator_plan_preview.build_plan_preview",
        lambda *a, **kw: pytest.fail("accepted Plan must not rebuild whole review"))
    monkeypatch.setattr("zf.runtime.plan_canonical_content.canonical_plan_content",
        lambda *a, **kw: pytest.fail("pinned chapter should not reconstruct the whole Plan"))
    before = (state / "events.jsonl").read_bytes()
    response = client.get(url, params={"sha256": digest})
    assert response.status_code == 200, response.text
    assert response.json()["section_count"] == 3
    response = client.get(url + "/sections/2", params={"sha256": digest})
    assert response.status_code == 200 and "END-PLAN" in response.json()["content"]
    assert client.get(url.removesuffix("/document"), params={"sha256": digest}).text == content
    assert (state / "events.jsonl").read_bytes() == before


def test_summary_exposes_approval_identity_without_reading_plan_bodies(tmp_path, monkeypatch):
    client, state, _, _, _ = fixture(tmp_path, "# Requirements\n", accepted=True)
    monkeypatch.setattr("zf.runtime.operator_plan_preview.build_plan_preview",
        lambda *a, **kw: pytest.fail("summary must not render full preview"))
    from zf.runtime.plan_preview_artifacts import read_preview_artifact
    def metadata_only(ref, **kwargs):
        assert ref.endswith("bundle.json"), f"summary read unrelated body {ref}"
        return read_preview_artifact(ref, **kwargs)
    monkeypatch.setattr("zf.runtime.plan_preview_artifacts.read_preview_artifact", metadata_only)
    before = (state / "events.jsonl").read_bytes()
    response = client.get("/api/projects/p/plans/plan/preview/summary")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["status"] == "pending" and data["requested_event_id"]
    assert data["content_state"] == "deferred" and data["markdown"] == ""
    assert data["artifacts"][0]["name"] == "accepted_plan"
    assert data["actions"]["approve"] == "plan-approve"
    assert (state / "events.jsonl").read_bytes() == before
    assert client.get("/api/projects/p/plans/missing/preview/summary").status_code == 404


def test_legacy_summary_keeps_source_and_snapshots_only_selected_version(tmp_path, monkeypatch):
    client, state, path, url, digest = fixture(tmp_path, "# Legacy\n\nSource body\n", authority=True)
    monkeypatch.setattr("zf.runtime.operator_plan_preview.build_plan_preview",
        lambda *a, **kw: pytest.fail("legacy source must not rebuild full Plan"))
    summary = client.get("/api/projects/p/plans/plan/preview/summary").json()
    entry = next(p for p in summary["artifacts"] if p["name"] == "requirement_1_source.md")
    assert entry["sha256"] == ""  # Unknown published version, not an invented approval binding.
    before = (state / "events.jsonl").read_bytes()
    version = client.get(url.removesuffix("/document") + "/version")
    assert version.status_code == 200
    assert version.headers["cache-control"] == "no-store"
    assert version.json()["binding"] == "observed" and version.json()["sha256"] == digest
    assert client.get(url, params={"sha256": digest}).status_code == 200
    path.write_text("# Changed source\n")
    assert client.get(url, params={"sha256": digest}).status_code == 409
    assert (state / "events.jsonl").read_bytes() == before


@pytest.mark.parametrize("owner", [False, True])
@pytest.mark.parametrize("resolution", ["pending", "approved", "rejected", "rerequested"])
def test_summary_and_full_preview_share_approval_identity(tmp_path, owner, resolution):
    client, state, _, _, _ = fixture(tmp_path, "# Plan\n", accepted=True)
    writer = EventWriter(EventLog(state / "events.jsonl"))
    payload = {"plan_id": "plan", "plan_artifact_ref": "artifacts/source.md"}
    if owner:
        payload.update(owner_route="plan_synth_owner_decision", approval_ref="approval/one",
                       checkpoint_id="checkpoint", proposal_ref="proposal/one", plan_preview=True)
    requested_type = "approval.requested" if owner else "plan.approval.requested"
    writer.append(ZfEvent(type=requested_type, payload=payload))
    if resolution != "pending":
        writer.append(ZfEvent(type="approval.resolved" if owner else "plan." + (
            "rejected" if resolution == "rejected" else "approved"), payload={
                "plan_id": "plan", "approval_ref": "approval/one",
                "resolution": "rejected" if resolution == "rejected" else "approved"}))
    if resolution == "rerequested":
        writer.append(ZfEvent(type=requested_type, payload={**payload, "approval_ref": "approval/two"}))
    before = (state / "events.jsonl").read_bytes()
    url = "/api/projects/p/plans/plan/preview"
    summary, full = client.get(url + "/summary").json(), client.get(url).json()
    for key in ("status", "requested_event_id", "resolved_event_id", "source_event_id",
                "actions", "action_payload"):
        assert summary[key] == full[key], key
    assert summary["status"] == ("pending" if resolution == "rerequested" else resolution)
    assert (state / "events.jsonl").read_bytes() == before


@pytest.mark.parametrize("fault", ["unbound", "changed", "deleted", "outside"])
def test_selected_version_never_publishes_unbound_or_changed_source(tmp_path, fault):
    client, state, path, url, _ = fixture(tmp_path, "# Original\n")
    version_url = url.removesuffix("/document") + "/version"
    if fault == "unbound":
        version_url = version_url.replace("requirement_source", "not-bound")
    elif fault == "changed":
        path.write_text("# Replacement\n")
    elif fault == "deleted":
        path.unlink()
    else:
        outside = tmp_path.parent / f"private-{tmp_path.name}.md"
        outside.write_text("PRIVATE")
        path.unlink()
        path.symlink_to(outside)
    before = (state / "events.jsonl").read_bytes()
    response = client.get(version_url)
    assert response.status_code in {403, 404, 409}, response.text
    assert "sha256" not in response.json() and "PRIVATE" not in response.text
    assert (state / "events.jsonl").read_bytes() == before
