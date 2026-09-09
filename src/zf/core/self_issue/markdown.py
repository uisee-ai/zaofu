"""Deterministic Markdown rendering for provider-neutral Self-Issue payloads."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


USER_NOT_PROVIDED = "(User did not provide this information.)"


def render_publication_markdown(disclosed: dict[str, Any], *, marker: str) -> str:
    """Render the exact provider body as readable, deterministic Markdown."""
    sections: list[str] = []
    evidence_status = str(disclosed.get("evidence_collection_status") or "").strip()
    evidence_mode = str(disclosed.get("evidence_collection_mode") or "").strip()
    assessment_status = str(disclosed.get("assessment_status") or "").strip()
    if "bug_description" in disclosed:
        sections.extend(["## Describe the bug", _user_prose(disclosed["bug_description"])])
    if "reproduction_steps" in disclosed:
        sections.extend(["## To reproduce", _user_scalar(disclosed["reproduction_steps"])])
    if "expected_behavior" in disclosed:
        sections.extend(["## Expected behavior", _user_prose(disclosed["expected_behavior"])])
    if "environment" in disclosed:
        environment = disclosed["environment"] if isinstance(disclosed["environment"], dict) else {}
        environment_lines = [
            f"- **Operating system:** {_user_scalar(environment.get('os'))}",
            f"- **OS version:** {_user_scalar(environment.get('version'))}",
            f"- **ZaoFu version:** {_user_scalar(disclosed.get('zaofu_version'))}",
        ]
        if disclosed.get("source_code_revision"):
            environment_lines.append(
                f"- **ZaoFu source commit:** {_user_scalar(disclosed['source_code_revision'])}"
            )
        sections.extend([
            "## Environment",
            "\n".join(environment_lines),
        ])
    elif "zaofu_version" in disclosed:
        sections.extend(["## ZaoFu version", _user_scalar(disclosed["zaofu_version"])])
    if "additional_context" in disclosed:
        sections.extend(["## Additional context", _user_prose(disclosed["additional_context"])])
    if "attachment_context" in disclosed:
        sections.extend([
            "## Attachment context", _user_prose(disclosed["attachment_context"]),
        ])

    facts = [
        ("Classification", disclosed.get("classification")),
        ("Severity", disclosed.get("severity")),
        ("Reproduction status", disclosed.get("reproduction_status")),
        ("Component", disclosed.get("component")),
        ("Impact scope", disclosed.get("impact_scope")),
        ("Assessment confidence", disclosed.get("assessment_confidence")),
        ("Evidence collection", evidence_mode or evidence_status),
        ("Assessment", assessment_status),
    ]
    visible_facts = [(label, value) for label, value in facts if value is not None]
    if visible_facts:
        sections.extend([
            "## Triage",
            "\n".join(f"- **{label}:** {_scalar(value)}" for label, value in visible_facts),
        ])

    if evidence_mode == "limited":
        reason = _scalar(disclosed.get("evidence_limit_reason"))
        sections.extend([
            "## Incident evidence",
            f"Evidence collection was limited. **Reason:** {reason}",
            "## Assessment limitation",
            "A semantic Orchestrator assessment was not performed; triage values may remain "
            "unknown and confidence is low.",
        ])
    elif evidence_status == "interrupted":
        sections.extend([
            "## Incident evidence",
            "Not collected because the user interrupted evidence collection.",
        ])
    elif evidence_status in {"failed", "conflict"}:
        sections.extend(["## Incident evidence", "No incident evidence was collected."])
    elif "analysis" in disclosed:
        sections.extend(["## Analysis", _value(disclosed["analysis"])])
    if "recommended_next_action" in disclosed:
        sections.extend([
            "## Recommended next action",
            _scalar(disclosed["recommended_next_action"]),
        ])
    if "published_attachments" in disclosed:
        sections.extend([
            "## Evidence and attachments",
            _attachments(disclosed["published_attachments"]),
        ])
    if disclosed.get("binary_attachments_omitted") is True:
        sections.extend([
            "## Binary attachments",
            "Binary attachments are not included because GitHub does not provide a "
            "supported Issue attachment upload API. Use Published & View to add them "
            "manually if needed.",
        ])
    sections.append(f"<!-- zf-self-issue:{marker} -->")
    return "\n\n".join(sections)


def _value(value: Any, *, depth: int = 0) -> str:
    if isinstance(value, dict):
        if not value:
            return "Not provided."
        lines: list[str] = []
        for key in sorted(value):
            label = str(key).replace("_", " ").strip().capitalize() or "Value"
            item = value[key]
            prefix = "  " * depth + f"- **{label}:**"
            if isinstance(item, (dict, list, tuple)):
                lines.append(prefix)
                lines.append(_value(item, depth=depth + 1))
            else:
                lines.append(f"{prefix} {_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, (list, tuple)):
        if not value:
            return "Not provided."
        lines = []
        for item in value:
            prefix = "  " * depth + "-"
            if isinstance(item, (dict, list, tuple)):
                lines.append(prefix)
                lines.append(_value(item, depth=depth + 1))
            else:
                lines.append(f"{prefix} {_scalar(item)}")
        return "\n".join(lines)
    return _scalar(value)


def _scalar(value: Any) -> str:
    if value is None or value == "":
        return "Not provided."
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value).strip() or "Not provided."


def _user_scalar(value: Any) -> str:
    if value is None or not str(value).strip():
        return USER_NOT_PROVIDED
    return str(value).strip()


def _user_prose(value: Any) -> str:
    text = _user_scalar(value)
    if text == USER_NOT_PROVIDED:
        return text
    paragraphs = []
    for paragraph in text.split("\n\n"):
        normalized = " ".join(line.strip() for line in paragraph.splitlines() if line.strip())
        if normalized:
            paragraphs.append(normalized)
    return "\n\n".join(paragraphs) or USER_NOT_PROVIDED


def _attachments(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "(User did not provide attachments, and no evidence was externally disclosed.)"
    lines: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        filename = _scalar(item.get("filename"))
        url = str(item.get("url") or "").strip()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or ">" in url:
            continue
        label = filename.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        if str(item.get("content_type") or "").startswith("image/"):
            lines.append(f"![{label}](<{url}>)")
        else:
            lines.append(f"[{label}](<{url}>)")
    return "\n".join(lines) or "Not provided."
