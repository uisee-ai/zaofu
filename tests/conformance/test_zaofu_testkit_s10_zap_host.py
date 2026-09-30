"""Explicit owned-container passive scan. Missing images are a red host tier.

Provide preloaded digest-pinned ZF_TESTKIT_ZAP_IMAGE and ZF_TESTKIT_ZAP_APP_IMAGE.
This is not baseline crawling, authenticated scanning or general Web security.
"""

import os
from pathlib import Path

import pytest

from tests.testkit.zap_launcher import run_zap


pytestmark = pytest.mark.host


def test_zap_owned_target_passive_host(tmp_path: Path) -> None:
    result = run_zap(
        output_dir=tmp_path / "owned-zap",
        zap_image=os.environ.get("ZF_TESTKIT_ZAP_IMAGE", ""),
        app_image=os.environ.get("ZF_TESTKIT_ZAP_APP_IMAGE", ""),
        timeout_seconds=180, max_risk=1,
    )
    assert result["execution_kind"] == "docker-zap"
    assert result["status"] == "pass", {
        "status": result["status"], "reason": result.get("reason"),
        "cleanup_errors": result["cleanup_errors"], "evidence": str(tmp_path / "owned-zap/zap-run.json"),
    }
    assert result["cleanup_ok"] is True
    assert result["coverage_proof"]["requests"] == {"/health": 1, "/api/state": 1}
