"""ZaoFu host adapter for the portable Structure Agent contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from issue_core.structure import StructureOutputError, TransientStructureError
from zf.web.headless_agent import KanbanHeadlessAgent, canonical_headless_backend


class HeadlessIssueStructureAgent:
    def __init__(
        self,
        *,
        state_dir: Path,
        project_root: Path,
        backend: str,
        agent_factory: Callable[..., KanbanHeadlessAgent] = KanbanHeadlessAgent,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.project_root = Path(project_root)
        self.backend = canonical_headless_backend(backend)
        self.agent_factory = agent_factory

    def understand(
        self,
        description: str,
        *,
        environment: dict[str, str],
        project_version: str,
    ) -> dict[str, Any]:
        if not self.backend:
            raise StructureOutputError("Structure Agent backend is unavailable")
        agent = self.agent_factory(
            state_dir=self.state_dir,
            project_root=self.project_root,
            timeout_s=180,
        )
        result = agent.run_turn(
            backend=self.backend,
            message=_structure_prompt(
                description,
                environment=environment,
                project_version=project_version,
            ),
            scope="project",
            thread_key="self-issue-structure",
            context={"operation_kind": "sidecar_operation"},
            permission_profile="read_only",
        )
        if not result.ok:
            reason = str(result.error or result.status or "structure agent failed")
            if any(token in reason.lower() for token in (
                "timeout", "tempor", "unavailable", "rate", "overload", "5xx",
            )):
                raise TransientStructureError(reason)
            raise StructureOutputError(reason)
        return _normalize_structure_output(
            _parse_json_object(result.reply),
            detected_environment=environment,
            detected_project_version=project_version,
        )


def configured_structure_backend(config: Any) -> str:
    configured = str(
        getattr(getattr(config, "self_issue", None), "structure_backend", "")
    )
    return canonical_headless_backend(configured)


def _parse_json_object(reply: str) -> dict[str, Any]:
    text = str(reply or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1) if fenced else text
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise StructureOutputError("Structure Agent did not return valid JSON") from exc
    if not isinstance(value, dict):
        raise StructureOutputError("Structure Agent output must be a JSON object")
    return value


def _normalize_structure_output(
    value: Mapping[str, Any],
    *,
    detected_environment: Mapping[str, str],
    detected_project_version: str,
) -> dict[str, Any]:
    """Normalize safe provider variations at the host adapter boundary.

    ``issue_core`` deliberately requires ``environment`` to be a string map.
    Models commonly return the same facts as one readable string, however.
    Rejecting an otherwise complete proposal loses useful understanding and
    turns the whole Draft into a Limited Draft.  Preserve the provider text as
    ``details`` and extract only explicit, mechanically recognizable facts;
    the detected host values remain the fallback for everything else.
    """

    normalized = dict(value)
    raw_environment = value.get("environment")
    environment = {
        str(key): str(item)
        for key, item in detected_environment.items()
        if isinstance(key, str) and isinstance(item, str)
    }
    if isinstance(raw_environment, Mapping):
        environment.update({
            str(key): str(item)
            for key, item in raw_environment.items()
            if isinstance(key, str) and isinstance(item, str) and item.strip()
        })
    elif isinstance(raw_environment, str) and raw_environment.strip():
        details = raw_environment.strip()[:2000]
        environment.update(_explicit_environment_facts(details))
        environment["details"] = details
        environment["source"] = "structure_agent"
    normalized["environment"] = environment
    if not str(normalized.get("project_version") or "").strip():
        normalized["project_version"] = str(detected_project_version or "")
    return normalized


def _explicit_environment_facts(details: str) -> dict[str, str]:
    facts: dict[str, str] = {}
    os_match = re.search(
        r"\b(Ubuntu|Debian|Fedora|CentOS|Red Hat|Windows|macOS|Linux)"
        r"(?:\s+([0-9]+(?:\.[0-9]+)*))?\b",
        details,
        re.IGNORECASE,
    )
    if os_match:
        canonical_os = {
            "ubuntu": "Ubuntu",
            "debian": "Debian",
            "fedora": "Fedora",
            "centos": "CentOS",
            "red hat": "Red Hat",
            "windows": "Windows",
            "macos": "macOS",
            "linux": "Linux",
        }
        facts["os"] = canonical_os[os_match.group(1).lower()]
        if os_match.group(2):
            facts["version"] = os_match.group(2)
    arch_match = re.search(r"\b(x86_64|amd64|aarch64|arm64|i[3-6]86)\b", details, re.IGNORECASE)
    if arch_match:
        facts["arch"] = arch_match.group(1)
    return facts


def _structure_prompt(
    description: str,
    *,
    environment: dict[str, str],
    project_version: str,
) -> str:
    return (
        "Convert the user's plain-language issue report into exactly one JSON object. "
        "Understand the meaning; do not copy the full report into title and do not invent facts. "
        "Use empty strings for unknown text fields and preserve the user's language. "
        "Do not use Markdown fences. The JSON must have exactly this shape (values below are "
        "type examples, not report facts):\n"
        '{"title":"","summary":"","bug_description":"",'
        '"reproduction_steps":"","actual_behavior":"","expected_behavior":"",'
        '"attachment_context":"","additional_context":"",'
        '"environment":{"os":"","version":"","arch":"","details":""},'
        '"project_version":"","field_provenance":'
        '{"title":{"source":"user|inferred|host|unknown","confidence":0.0}}}\n'
        "environment MUST be a JSON object whose values are strings, never a sentence or array. "
        "field_provenance must contain one entry for every field except field_provenance itself; "
        "each confidence must be a number from 0 to 1.\n\n"
        f"Detected environment: {json.dumps(environment, ensure_ascii=False)}\n"
        f"Detected project version: {project_version}\n\n"
        "User report:\n"
        f"{str(description or '')[:24000]}"
    )


__all__ = [
    "HeadlessIssueStructureAgent", "configured_structure_backend",
]
