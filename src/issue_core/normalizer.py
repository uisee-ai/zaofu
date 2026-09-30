"""Project-neutral free-form Issue understanding.

No host, runtime, configuration, or product package is imported here.  Hosts
may replace the deterministic implementation with an agent that returns the
same bounded proposal shape.
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
_EXPECTED_INLINE_RE = re.compile(
    r"(?:预期|期望|应该|应当|希望)(?:是|为|能|可以)?[：:，,、\s]*(.+?)(?:[。！？!?]|$)",
    re.IGNORECASE,
)
_ACTUAL_INLINE_RE = re.compile(
    r"(?:但|但是|实际|结果|导致|因|由于)[：:，,、\s]*(.+?(?:失败|报错|异常|错误|无法|不能|不可|缺少).*)(?:[。！？!?]|$)",
    re.IGNORECASE,
)


class IssueUnderstandingAgent(Protocol):
    def understand(
        self,
        description: str,
        *,
        environment: dict[str, str],
        project_version: str,
    ) -> dict[str, Any]: ...


def normalize_unstructured_report(
    description: str,
    *,
    environment: dict[str, str] | None = None,
    project_version: str = "",
) -> dict[str, Any]:
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
    if not bug:
        bug = raw
    if not steps:
        numbered = [match.group(1).strip() for line in lines if (match := _NUMBERED_STEP_RE.match(line))]
        steps = "\n".join(f"{index}. {value}" for index, value in enumerate(numbered, 1))
    # Free-form reports commonly contain all required facts in prose rather
    # than markdown headings.  Recover those facts before asking the user to
    # repeat information that is already present in the report.
    if not expected:
        match = _EXPECTED_INLINE_RE.search(raw)
        if match:
            expected = match.group(1).strip()
    if not actual:
        match = _ACTUAL_INLINE_RE.search(raw)
        if match:
            actual = match.group(1).strip()
    if not steps and raw:
        # Preserve the reported sequence as a usable reproduction trace.  A
        # provider-backed agent may refine it later, but the baseline agent
        # must not block confirmation solely because headings were omitted.
        steps = raw
    env = environment if isinstance(environment, dict) else {}
    result = {
        "title": title[:240] or "Untitled Issue",
        "summary": bug[:8000],
        "bug_description": bug[:8000],
        "reproduction_steps": steps[:8000],
        "actual_behavior": actual[:8000],
        "expected_behavior": expected[:8000],
        "attachment_context": attachments[:8000],
        # This field is deliberately user-authored in the editable Draft.  The
        # complete raw report remains available through its local evidence
        # reference, so leaving this blank does not discard input.
        "additional_context": "",
        "environment": {"os": str(env.get("os") or "")[:80], "version": str(env.get("version") or "")[:120], **{
            key: str(env[key])[:200] for key in ("arch", "source", "detected_at") if env.get(key)
        }},
        "project_version": str(project_version or "")[:120],
    }
    result["understanding"] = {
        "agent": "provider-neutral-normalizer",
        "source": "free_form_report",
        "missing_fields": [
            field for field, value in (
                ("bug_description", bug), ("reproduction_steps", steps),
                ("actual_behavior", actual), ("expected_behavior", expected),
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
    }
    return result


class DeterministicIssueUnderstandingAgent:
    def understand(self, description: str, *, environment: dict[str, str], project_version: str) -> dict[str, Any]:
        return normalize_unstructured_report(description, environment=environment, project_version=project_version)


def _clean_title(value: str) -> str:
    value = re.sub(r"^\s*#+\s*", "", value).strip()
    value = re.sub(r"^(?:title|bug|issue)\s*[:：]\s*", "", value, flags=re.IGNORECASE)
    return value or "Untitled Issue"


def _sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
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
    return re.sub(r"\s+", " ", value.lower().strip()).replace("：", ":").rstrip(":")


def _section_text(sections: dict[str, list[str]], *names: str) -> str:
    for name in names:
        values = sections.get(_canonical(name))
        if values:
            return "\n".join(value for value in values if value).strip()
    return ""


__all__ = [
    "DeterministicIssueUnderstandingAgent", "IssueUnderstandingAgent",
    "normalize_unstructured_report",
]
