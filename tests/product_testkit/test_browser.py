from __future__ import annotations

from pathlib import Path

import pytest

from .browser import build_playwright_command, classify_playwright_result


def _report(*, expected=1, unexpected=0, skipped=0, flaky=0, errors=()):
    return {
        "stats": {
            "expected": expected,
            "unexpected": unexpected,
            "skipped": skipped,
            "flaky": flaky,
        },
        "errors": list(errors),
        "suites": [{"title": "smoke", "specs": [{"title": "journey"}]}],
    }


def test_playwright_command_is_pinned_to_docker_boundary(tmp_path: Path):
    command = build_playwright_command(
        project_root=tmp_path,
        image="mcp/playwright:latest",
        spec="tests/e2e/smoke.spec.ts",
        base_url="http://127.0.0.1:8002",
        install_timeout_seconds=60,
        report_path=tmp_path / "playwright.json",
        install_browser=True,
    )
    assert command[:8] == [
        "docker", "run", "--rm", "--network", "host", "--user",
        "0:0", "--entrypoint",
    ] or command[:6] == ["docker", "run", "--rm", "--network", "host", "--user"]
    rendered = " ".join(command)
    assert "mcp/playwright:latest" in rendered
    assert "PLAYWRIGHT_BROWSERS_PATH=0" in rendered
    assert "timeout 60s" in rendered
    assert "playwright install chromium" in rendered
    assert "playwright test" in rendered


def test_playwright_command_rejects_escape_and_invalid_url(tmp_path: Path):
    with pytest.raises(ValueError):
        build_playwright_command(project_root=tmp_path, image="image", spec="../x", base_url="http://x",
                                 install_timeout_seconds=1, report_path=tmp_path / "r.json")
    with pytest.raises(ValueError):
        build_playwright_command(project_root=tmp_path, image="image", spec="x", base_url="http://u:p@x",
                                 install_timeout_seconds=1, report_path=tmp_path / "r.json")


def test_playwright_result_requires_real_tests_and_no_findings():
    result = classify_playwright_result(_report(), outer_exit_code=0, outer_timed_out=False,
                                        outer_environment_error=False)
    assert result["verdict"] == "pass"
    result = classify_playwright_result(_report(unexpected=1), outer_exit_code=1,
                                        outer_timed_out=False, outer_environment_error=False)
    assert result["verdict"] == "fail"
    result = classify_playwright_result(_report(expected=0, skipped=1), outer_exit_code=0,
                                        outer_timed_out=False, outer_environment_error=False)
    assert result["verdict"] == "not_run"


def test_playwright_runner_errors_are_not_product_findings():
    result = classify_playwright_result(_report(errors=("browser disconnected",)), outer_exit_code=1,
                                        outer_timed_out=False, outer_environment_error=False)
    assert result["verdict"] == "harness_failure"
    result = classify_playwright_result({}, outer_exit_code=None, outer_timed_out=True,
                                        outer_environment_error=False)
    assert result["verdict"] == "environment_failure"
