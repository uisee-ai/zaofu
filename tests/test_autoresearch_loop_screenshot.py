"""Sprint §9 — Playwright web-kanban screenshot capture tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from zf.autoresearch.loop import (
    ScreenshotResult,
    capture_kanban_screenshot,
)


_DOCKER_IMAGE = "mcp/playwright:latest"


def _fake_shot_js(tmp_path: Path) -> Path:
    """Create a stub shot.js so capture_kanban_screenshot's
    'read + copy into mount' step succeeds in tests."""
    p = tmp_path / "fake-shot.js"
    p.write_text("// fake shot.js for unit tests\n")
    return p


def _stub_completed_process(
    returncode: int, stdout: str = "", stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["sg", "docker", "-c", "..."], returncode=returncode,
        stdout=stdout, stderr=stderr,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_capture_invokes_docker_with_sg(tmp_path: Path) -> None:
    out = tmp_path / "iter-001.png"
    out.write_text("PNG STUB")  # simulate docker writing the file
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.return_value = _stub_completed_process(
            0, stdout="--- visible text ---\nIn Progress 4\nsaved /snapshots/iter-001.png\n",
        )
        r = capture_kanban_screenshot(
            url="http://127.0.0.1:8765",
            output_path=out,
            shot_js_path=_fake_shot_js(tmp_path),
        )
    assert isinstance(r, ScreenshotResult)
    assert r.ok is True
    assert r.path == out
    assert "In Progress 4" in r.visible_text
    # Verify the docker command shape: sg docker -c "docker run ..."
    args = mock_run.call_args[0][0]
    assert args[0] == "/usr/bin/sg"
    assert "docker" in args
    assert "-c" in args
    bash_cmd = args[args.index("-c") + 1]
    assert "docker run" in bash_cmd
    assert "--entrypoint node" in bash_cmd
    assert _DOCKER_IMAGE in bash_cmd
    assert "http://127.0.0.1:8765" in bash_cmd


def test_capture_mounts_output_dir(tmp_path: Path) -> None:
    """The host directory containing output_path must be bind-mounted
    so docker can write the PNG."""
    out = tmp_path / "snaps" / "iter-002.png"
    out.parent.mkdir()
    out.write_text("stub")
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.return_value = _stub_completed_process(0)
        capture_kanban_screenshot(
            url="http://x", output_path=out,
            shot_js_path=_fake_shot_js(tmp_path),
        )
    bash_cmd = mock_run.call_args[0][0][-1]
    # bind mount: -v <host_dir>:/snapshots
    assert f"-v {tmp_path / 'snaps'}:/snapshots" in bash_cmd
    # The shot.js host path is mounted into /snapshots and invoked from there.
    assert "/snapshots/" in bash_cmd  # shot.js path translated


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_capture_docker_failure_returns_not_ok(tmp_path: Path) -> None:
    out = tmp_path / "x.png"
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.return_value = _stub_completed_process(
            1, stderr="permission denied to docker socket",
        )
        r = capture_kanban_screenshot(
            url="http://127.0.0.1:8765",
            output_path=out,
            shot_js_path=_fake_shot_js(tmp_path),
        )
    assert r.ok is False
    assert "permission denied" in r.error


def test_capture_timeout_returns_not_ok(tmp_path: Path) -> None:
    out = tmp_path / "x.png"
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="sg docker -c ...", timeout=60,
        )
        r = capture_kanban_screenshot(
            url="http://127.0.0.1:8765",
            output_path=out,
            shot_js_path=_fake_shot_js(tmp_path),
            timeout_seconds=60,
        )
    assert r.ok is False
    assert "timeout" in r.error.lower()


def test_capture_missing_sg_falls_back(tmp_path: Path) -> None:
    out = tmp_path / "x.png"
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("sg")
        r = capture_kanban_screenshot(
            url="http://127.0.0.1:8765",
            output_path=out,
            shot_js_path=_fake_shot_js(tmp_path),
        )
    assert r.ok is False
    assert "not found" in r.error.lower() or "filenotfound" in r.error.lower()


def test_capture_disabled_returns_skipped(tmp_path: Path) -> None:
    """When url is empty string (operator-disabled), skip without
    invoking docker."""
    out = tmp_path / "x.png"
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        r = capture_kanban_screenshot(
            url="",
            output_path=out,
            shot_js_path=_fake_shot_js(tmp_path),
        )
    assert r.ok is False
    assert r.path is None
    assert "disabled" in r.error.lower() or "skipped" in r.error.lower()
    mock_run.assert_not_called()
