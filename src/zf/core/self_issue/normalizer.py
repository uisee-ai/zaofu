"""Provider-neutral normalization for a free-form ``/issue`` report.

This module deliberately has no ZaoFu imports.  A host may replace the
normalizer with an LLM-backed Issue-understanding agent, but the kernel still
receives the same bounded Draft proposal shape and keeps the reporter's text
as evidence instead of inventing missing facts.
"""

from __future__ import annotations

import re
from typing import Any, Protocol


_SECTION_RE = re.compile(
    r"^\s{0,3}(?:#+\s*)?(title|summary|bug|bug description|description|"
    r"steps?|reproduction(?: steps?)?|actual(?: behavior)?|expected(?: behavior)?|attachments?|"
    r"additional context)\s*[:：]?\s*$",
    re.IGNORECASE,
)
_NUMBERED_STEP_RE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.+)$")


def normalize_unstructured_report(
    description: str,
    *,
    environment: dict[str, str] | None = None,
    project_version: str = "",
) -> dict[str, Any]:
    """Turn one user description into an editable Draft proposal.

    The result is intentionally conservative: when a section is absent the
    original report is retained in ``bug_description`` and unknown fields stay
    empty.  This makes the operation safe to run before any project
    assessment and allows a host-specific agent to improve the proposal later.
    """
    raw = str(description or "").strip()
    lines = [line.rstrip() for line in raw.splitlines()]
    non_empty = [line.strip() for line in lines if line.strip()]
    title = _clean_title(non_empty[0] if non_empty else raw)
    sections = _sections(lines)
    bug = _section_text(sections, "bug", "bug description", "description", "summary")
    steps = _section_text(sections, "steps", "step", "reproduction", "reproduction steps")
    actual = _section_text(sections, "actual", "actual behavior")
    expected = _section_text(sections, "expected", "expected behavior")
    attachments = _section_text(sections, "attachments", "attachment")
    additional = _section_text(sections, "additional context")
    if not bug:
        bug = raw
    if not steps:
        numbered = [match.group(1).strip() for line in lines if (match := _NUMBERED_STEP_RE.match(line))]
        steps = "\n".join(f"{index}. {value}" for index, value in enumerate(numbered, 1))
    env = environment if isinstance(environment, dict) else {}
    return {
        "title": title[:240],
        "summary": bug[:8000],
        "bug_description": bug[:8000],
        "reproduction_steps": steps[:8000],
        "actual_behavior": actual[:8000],
        "expected_behavior": expected[:8000],
        "attachment_context": attachments[:8000],
        "additional_context": additional[:8000],
        "environment": {
            "os": str(env.get("os") or "")[:80],
            "version": str(env.get("version") or "")[:120],
        },
        "project_version": str(project_version or "")[:120],
        # Compatibility alias for existing ZaoFu Draft storage/publication
        # snapshots. New hosts should consume project_version.
        "zaofu_version": str(project_version or "")[:120],
        "understanding": {
            "agent": "provider-neutral-normalizer",
            "source": "free_form_report",
            "missing_fields": [
                field for field, value in (
                    ("bug_description", bug),
                    ("reproduction_steps", steps),
                    ("actual_behavior", actual),
                    ("expected_behavior", expected),
                ) if not value
            ],
            "field_provenance": {
                "title": {"source": "inferred" if title else "unknown", "confidence": 0.7 if title else 0.0},
                "bug_description": {"source": "inferred" if bug else "unknown", "confidence": 0.6 if bug else 0.0},
                "reproduction_steps": {"source": "inferred" if steps else "unknown", "confidence": 0.6 if steps else 0.0},
                "actual_behavior": {"source": "inferred" if actual else "unknown", "confidence": 0.6 if actual else 0.0},
                "expected_behavior": {"source": "inferred" if expected else "unknown", "confidence": 0.6 if expected else 0.0},
                "attachment_context": {"source": "inferred" if attachments else "unknown", "confidence": 0.5 if attachments else 0.0},
                "environment": {"source": "host", "confidence": 1.0 if env else 0.0},
                "project_version": {"source": "host", "confidence": 1.0 if project_version else 0.0},
                "additional_context": {"source": "user", "confidence": 0.0},
            },
        },
    }


class IssueUnderstandingAgent(Protocol):
    """Replaceable, project-neutral boundary for free-form Issue parsing."""

    def understand(
        self,
        description: str,
        *,
        environment: dict[str, str],
        project_version: str,
    ) -> dict[str, Any]: ...


class DeterministicIssueUnderstandingAgent:
    """Safe local fallback when a host does not supply an LLM-backed agent."""

    def understand(
        self,
        description: str,
        *,
        environment: dict[str, str],
        project_version: str,
    ) -> dict[str, Any]:
        return normalize_unstructured_report(
            description,
            environment=environment,
            project_version=project_version,
        )


def _clean_title(value: str) -> str:
    value = re.sub(r"^\s*#+\s*", "", value).strip()
    value = re.sub(r"^(?:title|bug|issue)\s*[:：]\s*", "", value, flags=re.IGNORECASE)
    return value or "Untitled Issue"


def _sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = { }
    current = ""
    for line in lines:
        match = _SECTION_RE.match(line)
        if match:
            current = _canonical(match.group(1))
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line.strip())
    return sections


def _canonical(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.lower().strip())
    return normalized.replace("：", ":").rstrip(":")


def _section_text(sections: dict[str, list[str]], *names: str) -> str:
    for name in names:
        values = sections.get(_canonical(name))
        if values:
            return "\n".join(value for value in values if value).strip()
    return ""
