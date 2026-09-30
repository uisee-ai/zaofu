from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from issue_core.structure import TransientStructureError, validate_structure_proposal
from zf.runtime.self_issue_structure_agent import (
    HeadlessIssueStructureAgent,
    configured_structure_backend,
)


class _FakeHeadless:
    def __init__(self, result: object) -> None:
        self.result = result
        self.call: dict[str, object] = {}

    def run_turn(self, **kwargs: object) -> object:
        self.call = dict(kwargs)
        return self.result


def test_headless_structure_adapter_parses_json_and_stays_read_only(tmp_path: Path) -> None:
    payload = {
        "title": "Structured title",
        "summary": "Summary",
        "bug_description": "Description",
        "reproduction_steps": "Steps",
        "actual_behavior": "Actual",
        "expected_behavior": "Expected",
        "attachment_context": "Attachment",
        "additional_context": "",
        "environment": {"os": "Linux", "version": "24.04"},
        "project_version": "1.0",
        "field_provenance": {},
    }
    fake = _FakeHeadless(SimpleNamespace(
        ok=True,
        status="completed",
        error="",
        reply=f"```json\n{json.dumps(payload)}\n```",
    ))
    adapter = HeadlessIssueStructureAgent(
        state_dir=tmp_path / ".state",
        project_root=tmp_path,
        backend="codex",
        agent_factory=lambda **_: fake,  # type: ignore[arg-type]
    )
    assert adapter.understand(
        "plain report",
        environment={"os": "Linux", "version": "24.04"},
        project_version="1.0",
    ) == payload
    assert fake.call["permission_profile"] == "read_only"
    assert fake.call["backend"] == "codex-headless"


def test_headless_structure_adapter_normalizes_readable_environment_string(
    tmp_path: Path,
) -> None:
    payload = {
        "title": "基础 wheel 缺少 httpx 导致 CLI 无法启动",
        "summary": "安装基础 wheel 后 CLI 无法启动。",
        "bug_description": "CLI 导入 httpx 时失败。",
        "reproduction_steps": "安装 wheel，然后执行 zf --help。",
        "actual_behavior": "ModuleNotFoundError: httpx",
        "expected_behavior": "CLI 显示帮助。",
        "attachment_context": "附有日志和 METADATA。",
        "additional_context": "",
        "environment": "Ubuntu 24.04 x86_64，Python 3.12，干净虚拟环境",
        "project_version": "0.0.7",
        "field_provenance": {},
    }
    fake = _FakeHeadless(SimpleNamespace(
        ok=True,
        status="completed",
        error="",
        reply=json.dumps(payload, ensure_ascii=False),
    ))
    adapter = HeadlessIssueStructureAgent(
        state_dir=tmp_path / ".state",
        project_root=tmp_path,
        backend="codex",
        agent_factory=lambda **_: fake,  # type: ignore[arg-type]
    )

    result = adapter.understand(
        "plain report",
        environment={"os": "Linux", "version": "host", "arch": "amd64"},
        project_version="0.0.7",
    )

    assert result["title"] == payload["title"]
    assert result["environment"] == {
        "os": "Ubuntu",
        "version": "24.04",
        "arch": "x86_64",
        "details": payload["environment"],
        "source": "structure_agent",
    }
    prompt = str(fake.call["message"])
    assert "environment MUST be a JSON object" in prompt
    assert '"confidence":0.0' in prompt
    assert validate_structure_proposal(result)["understanding"]["status"] == "completed"


def test_headless_structure_adapter_classifies_timeout_as_transient(tmp_path: Path) -> None:
    fake = _FakeHeadless(SimpleNamespace(
        ok=False, status="timeout", error="provider timeout", reply="",
    ))
    adapter = HeadlessIssueStructureAgent(
        state_dir=tmp_path / ".state",
        project_root=tmp_path,
        backend="codex",
        agent_factory=lambda **_: fake,  # type: ignore[arg-type]
    )
    with pytest.raises(TransientStructureError):
        adapter.understand("report", environment={}, project_version="")


def test_structure_backend_is_explicit_self_issue_policy() -> None:
    config = SimpleNamespace(
        self_issue=SimpleNamespace(structure_backend="claude"),
        orchestrator=SimpleNamespace(backend="codex"),
    )
    assert configured_structure_backend(config) == "claude-headless"
    config.self_issue.structure_backend = ""
    assert configured_structure_backend(config) == ""
