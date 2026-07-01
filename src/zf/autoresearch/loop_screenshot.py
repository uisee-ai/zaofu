"""Playwright screenshot capture for autoresearch loop."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_SCREENSHOT_DOCKER_IMAGE = "mcp/playwright:latest"
_DEFAULT_SCREENSHOT_TIMEOUT_SECONDS = 90
_SG_BINARY = "/usr/bin/sg"


@dataclass(frozen=True)
class ScreenshotResult:
    """Result of one playwright-via-docker screenshot attempt.

    ``path`` is None on failure or when skipped (empty URL). ``error``
    explains the failure mode so journal forensics work.
    """
    ok: bool
    path: Path | None
    visible_text: str
    error: str


def capture_kanban_screenshot(
    *,
    url: str,
    output_path: Path,
    shot_js_path: Path,
    docker_image: str = _DEFAULT_SCREENSHOT_DOCKER_IMAGE,
    timeout_seconds: int = _DEFAULT_SCREENSHOT_TIMEOUT_SECONDS,
) -> ScreenshotResult:
    """Capture the web kanban via mcp/playwright docker image.

    Strategy: bind-mount the host output_path's parent to /snapshots in
    the container, copy shot.js to that same dir so docker can read it
    from /snapshots/<basename>, then invoke via ``sg docker -c "..."``
    because the calling user is in docker group but lacks active session
    activation.

    Skip semantics: empty URL → skipped (no docker call). Useful when
    operator wants to disable screenshots for a particular loop run.
    """
    if not url:
        return ScreenshotResult(
            ok=False, path=None, visible_text="",
            error="screenshot disabled (empty url)",
        )

    snapshots_host_dir = output_path.parent
    snapshots_host_dir.mkdir(parents=True, exist_ok=True)
    # mcp/playwright runs as a different uid → host dir must be world-
    # writable so the container can drop the PNG there. Internal tool
    # dir under .zf/autoresearch/loop/, no secrets, acceptable risk.
    try:
        import os as _os
        _os.chmod(snapshots_host_dir, 0o777)
    except Exception:
        pass
    container_shot_js = f"/snapshots/{shot_js_path.name}"
    container_output = f"/snapshots/{output_path.name}"

    # Copy shot.js into the bind-mounted dir so docker can read it.
    try:
        shot_js_text = shot_js_path.read_text()
    except Exception as e:  # noqa: BLE001
        return ScreenshotResult(
            ok=False, path=None, visible_text="",
            error=f"cannot read shot.js at {shot_js_path}: {e}",
        )
    (snapshots_host_dir / shot_js_path.name).write_text(shot_js_text)

    docker_cmd = (
        f"docker run --rm --entrypoint node "
        f"-v {snapshots_host_dir}:/snapshots --network host "
        f"{docker_image} "
        f"{container_shot_js} {url} {container_output}"
    )
    sg_cmd = [_SG_BINARY, "docker", "-c", docker_cmd]

    try:
        proc = subprocess.run(
            sg_cmd, capture_output=True, text=True, timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return ScreenshotResult(
            ok=False, path=None, visible_text="",
            error=f"screenshot timeout after {timeout_seconds}s",
        )
    except FileNotFoundError as e:
        return ScreenshotResult(
            ok=False, path=None, visible_text="",
            error=f"FileNotFoundError: {e}",
        )
    except Exception as e:  # noqa: BLE001
        return ScreenshotResult(
            ok=False, path=None, visible_text="",
            error=f"{type(e).__name__}: {e}",
        )

    if proc.returncode != 0:
        return ScreenshotResult(
            ok=False, path=None, visible_text="",
            error=(proc.stderr or proc.stdout).strip()[:500],
        )

    # Extract visible text dumped by shot.js (between "--- visible text ---"
    # and "saved <path>").
    visible = ""
    if "--- visible text ---" in proc.stdout:
        try:
            visible = proc.stdout.split("--- visible text ---", 1)[1]
            if "\nsaved " in visible:
                visible = visible.split("\nsaved ", 1)[0]
            visible = visible.strip()
        except Exception:
            visible = ""

    return ScreenshotResult(
        ok=True,
        path=output_path,
        visible_text=visible,
        error="",
    )

__all__ = ["ScreenshotResult", "capture_kanban_screenshot"]
