from __future__ import annotations

import pytest

from .containers import classify_container_probe, docker_container_command


def test_container_command_has_isolated_ephemeral_boundary():
    command = docker_container_command(
        name="zf-product-db-1",
        image="alpine:3.20",
        command="sh -c 'while true; do sleep 1; done'",
    )
    assert command[:5] == ["docker", "run", "--detach", "--rm", "--name"]
    assert "zf-product-db-1" in command
    assert "alpine:3.20" in command
    assert command[-3:] == ["sh", "-c", "sh -c 'while true; do sleep 1; done'"]


def test_container_command_rejects_unsafe_identity():
    with pytest.raises(ValueError):
        docker_container_command(name="bad/name", image="alpine:3.20", command="sleep 1")
    with pytest.raises(ValueError):
        docker_container_command(name="safe", image="alpine:3.20\nunsafe", command="sleep 1")


def test_container_command_adds_explicit_port_mappings():
    command = docker_container_command(
        name="zf-db",
        image="postgres:16",
        command="postgres",
        ports=("15432:5432",),
    )
    assert "-p" in command
    assert "15432:5432" in command


def test_container_probe_classification_is_fail_closed():
    assert classify_container_probe(exit_code=0, timed_out=False, stderr="") == {
        "verdict": "pass", "reason": "health_passed"
    }
    assert classify_container_probe(exit_code=1, timed_out=False, stderr="connection refused")["verdict"] == "environment_failure"
    assert classify_container_probe(exit_code=2, timed_out=False, stderr="assertion failed")["verdict"] == "fail"
