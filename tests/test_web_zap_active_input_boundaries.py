"""Regression probes for ZAP-discovered untrusted Web query boundaries.

These tests intentionally remain strict-xfail until the Web handlers translate
filesystem/query validation failures into bounded 4xx responses.  They must
not be made green by changing the oracle or by suppressing the request.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.web.server import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "kanban.json").write_text("[]", encoding="utf-8")
    (state / "events.jsonl").write_text("", encoding="utf-8")
    return TestClient(create_app(state, project_root=tmp_path), raise_server_exceptions=False)


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-061: ZAP-discovered invalid artifact view escapes as HTTP 500",
)
def test_invalid_artifact_view_is_structured_4xx(client: TestClient) -> None:
    response = client.get(
        "/api/artifacts/catalog",
        params={"view": "http://www.google.com/", "limit": 200, "offset": 0},
    )
    assert response.status_code in {400, 404, 422}, response.text


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-061: ZAP-discovered oversized profile path escapes as HTTP 500",
)
@pytest.mark.parametrize("endpoint", ["/api/profile/detect", "/api/profile/recommend"])
def test_oversized_profile_path_is_structured_4xx(
    client: TestClient,
    endpoint: str,
) -> None:
    response = client.get(endpoint, params={"path": "Z" * 4096})
    assert response.status_code in {400, 404, 422}, response.text
