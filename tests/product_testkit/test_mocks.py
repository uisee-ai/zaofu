from __future__ import annotations

import json
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from .mocks import start_mock_service


def test_fake_service_returns_declared_response_records_requests_and_stops(tmp_path: Path):
    handle = start_mock_service(
        {
            "id": "provider",
            "routes": [{
                "method": "GET", "path": "/health", "status": 200,
                "body": {"ok": True},
            }],
        },
        output_dir=tmp_path,
    )
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(Request(f"{handle.base_url}/health", method="GET"), timeout=2) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"ok": True}
    finally:
        receipt = handle.stop()

    assert receipt.passed
    assert receipt.request_count == 1
    assert json.loads(Path(receipt.requests_ref).read_text(encoding="utf-8"))[0]["path"] == "/health"
    assert Path(receipt.receipt_ref).exists()


def test_fake_service_can_inject_bounded_transient_failure(tmp_path: Path):
    handle = start_mock_service(
        {
            "id": "provider",
            "routes": [{
                "method": "POST", "path": "/publish", "status": 200,
                "body": {"accepted": True}, "failures_before_success": 2,
                "failure_status": 503,
            }],
        },
        output_dir=tmp_path,
    )
    try:
        statuses = []
        opener = build_opener(ProxyHandler({}))
        for _ in range(3):
            try:
                with opener.open(Request(f"{handle.base_url}/publish", data=b"{}", method="POST")) as response:
                    statuses.append(response.status)
            except Exception as exc:  # noqa: BLE001 - assert the HTTP failure shape below
                statuses.append(exc.code)
    finally:
        receipt = handle.stop()

    assert statuses == [503, 503, 200]
    assert receipt.request_count == 3
