"""Tests for verification gates."""

from __future__ import annotations

from pathlib import Path

from zf.core.verification.gates import CommandGate, FileExistsGate, GateResult


def test_command_gate_pass():
    gate = CommandGate(name="true-check", command="true")
    result = gate.run()
    assert result.passed
    assert result.exit_code == 0


def test_command_gate_fail():
    gate = CommandGate(name="false-check", command="false")
    result = gate.run()
    assert not result.passed
    assert result.exit_code != 0


def test_command_gate_captures_output():
    gate = CommandGate(name="echo", command="echo hello")
    result = gate.run()
    assert result.passed
    assert "hello" in result.output


def test_command_gate_timeout():
    gate = CommandGate(name="slow", command="sleep 10", timeout=1)
    result = gate.run()
    assert not result.passed
    assert "timeout" in result.output.lower() or result.exit_code != 0


def test_file_exists_gate_pass(tmp_path: Path):
    (tmp_path / "exists.txt").write_text("content")
    gate = FileExistsGate(name="file-check", paths=[str(tmp_path / "exists.txt")])
    result = gate.run()
    assert result.passed


def test_file_exists_gate_fail(tmp_path: Path):
    gate = FileExistsGate(name="file-check", paths=[str(tmp_path / "missing.txt")])
    result = gate.run()
    assert not result.passed
    assert "missing" in result.output.lower()


def test_file_exists_gate_multiple(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a")
    gate = FileExistsGate(
        name="multi",
        paths=[str(tmp_path / "a.txt"), str(tmp_path / "b.txt")],
    )
    result = gate.run()
    assert not result.passed  # b.txt missing


def test_gate_result_structure():
    result = GateResult(name="test", passed=True, exit_code=0, output="ok")
    assert result.name == "test"
    assert result.passed
    assert result.output == "ok"
