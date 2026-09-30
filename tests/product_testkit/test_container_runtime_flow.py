from __future__ import annotations

import subprocess
import uuid

import pytest

from .containers import exec_container, start_container, stop_container


@pytest.mark.integration
def test_ephemeral_dependency_container_starts_health_checks_and_is_removed():
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("Docker daemon unavailable")
    if subprocess.run(["docker", "image", "inspect", "alpine:3.20"], capture_output=True).returncode != 0:
        pytest.skip("alpine fixture image unavailable")
    name = f"zf-testkit-dependency-{uuid.uuid4().hex[:10]}"
    container_id = start_container(name=name, image="alpine:3.20", command="while true; do sleep 1; done")
    try:
        health = exec_container(name=name, command="test -f /etc/alpine-release")
        assert health.returncode == 0, health.stderr
        assert container_id
    finally:
        stopped = stop_container(name=name)
        assert stopped.returncode == 0, stopped.stderr
    assert subprocess.run(["docker", "inspect", name], capture_output=True).returncode != 0
