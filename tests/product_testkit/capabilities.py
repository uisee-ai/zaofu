"""Host capability probes used to classify product-test harness failures."""

from __future__ import annotations

import shutil
import socket
import subprocess


def allocate_port() -> int:
    """Reserve an ephemeral TCP port long enough to return its number."""

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def docker_available() -> tuple[bool, str]:
    """Return availability without creating or mutating Docker resources."""

    executable = shutil.which("docker")
    if executable is None:
        return False, "docker executable not found"
    try:
        result = subprocess.run([executable, "info"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"docker probe failed: {exc}"
    if result.returncode != 0:
        return False, (result.stderr or "docker daemon unavailable").strip()
    return True, "docker daemon available"

