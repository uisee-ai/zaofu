import pytest

from .docker import playwright_command, probe_docker


def test_docker_probe_returns_structured_capability_result():
    result = probe_docker()
    assert isinstance(result.available, bool)
    assert result.detail


def test_playwright_command_uses_host_network_and_fixed_image():
    command = playwright_command(url="http://127.0.0.1:8002", spec="tests/smoke.spec.ts")
    assert command[:6] == ["docker", "run", "--rm", "--network", "host", "mcp/playwright:latest"]
    assert command[-2:] == ["--base-url", "http://127.0.0.1:8002"]


def test_playwright_command_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        playwright_command(url="ftp://host", spec="smoke")
    with pytest.raises(ValueError):
        playwright_command(url="http://host", spec="")
