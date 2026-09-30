"""Docker capability checks and reproducible Playwright container commands."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess


@dataclass(frozen=True)
class DockerProbe:
    available: bool
    detail: str


def probe_docker() -> DockerProbe:
    executable = shutil.which("docker")
    if executable is None:
        return DockerProbe(False, "docker executable not found")
    try:
        result = subprocess.run([executable, "info"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DockerProbe(False, f"docker info failed: {type(exc).__name__}")
    if result.returncode != 0:
        return DockerProbe(False, result.stderr.strip() or "docker daemon unavailable")
    return DockerProbe(True, result.stdout.splitlines()[0] if result.stdout else "docker available")


def playwright_command(*, image: str = "mcp/playwright:latest", url: str, spec: str) -> list[str]:
    if not image or any(char in image for char in "\n\r"):
        raise ValueError("image must be a single non-empty line")
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must use http or https")
    if not spec.strip():
        raise ValueError("spec must not be empty")
    return ["docker", "run", "--rm", "--network", "host", image, "npx", "playwright", "test", spec, "--base-url", url]

