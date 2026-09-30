"""Deterministic Markdown rendering for provider-neutral Self-Issue payloads."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit
from zf.core.self_issue.report_projection import report_gap


USER_NOT_PROVIDED = "(User did not provide this information.)"


def render_publication_markdown(disclosed: dict[str, Any], *, marker: str) -> str:
    """Render the exact provider body as readable, deterministic Markdown."""
    sections: list[str] = []
    evidence_status = str(disclosed.get("evidence_collection_status") or "").strip()
    evidence_mode = str(disclosed.get("evidence_collection_mode") or "").strip()
    assessment_status = str(disclosed.get("assessment_status") or "").strip()
    analysis = disclosed.get("analysis") if isinstance(disclosed.get("analysis"), dict) else {}
    if _has_value(disclosed.get("bug_description")):
        sections.extend(["## Describe the bug", _user_prose(disclosed["bug_description"])])
    if _has_value(disclosed.get("reproduction_steps")):
        sections.extend(["## To reproduce", _user_scalar(disclosed["reproduction_steps"])])
    if _has_value(disclosed.get("actual_behavior")):
        sections.extend(["## Actual behavior", _user_prose(disclosed["actual_behavior"])])
    if _has_value(disclosed.get("expected_behavior")):
        sections.extend(["## Expected behavior", _user_prose(disclosed["expected_behavior"])])
    if "environment" in disclosed:
        environment = disclosed["environment"] if isinstance(disclosed["environment"], dict) else {}
        environment_lines = []
        # Fixed six-field presentation; rich provenance remains in the report.
        # Legacy investigator aliases refer to the investigation host, never to
        # the historical affected machine. Do not query the renderer's host.
        for label, aliases in (
            ("Operating system", ("os", "operating_system", "investigator_os")),
            ("OS version", ("version", "os_version", "operating_system_version", "investigator_kernel")),
            ("Architecture", ("arch", "architecture", "investigator_architecture")),
            ("Python version", ("python", "python_version", "investigator_python", "investigator_default_python")),
        ):
            value = next((environment[key] for key in aliases if _has_value(environment.get(key))), None)
            if _has_value(value):
                rendered = _user_scalar(value)
                if label == "OS version" and rendered.startswith("Linux "):
                    rendered = rendered.removeprefix("Linux ")
                environment_lines.append(f"- **{label}:** " + rendered.replace("\n", "\n  "))
        project_version = disclosed.get("project_version") or disclosed.get("zaofu_version")
        if _has_value(project_version):
            environment_lines.append(f"- **Project version:** {_user_scalar(project_version)}")
        source_revision = disclosed.get("source_code_revision")
        if _has_value(source_revision):
            environment_lines.append(
                f"- **Project source commit:** {_user_scalar(source_revision)}"
            )
        if environment_lines:
            sections.extend(["## Environment", "\n".join(environment_lines)])
    elif "project_version" in disclosed or "zaofu_version" in disclosed:
        sections.extend(["## Project version", _user_scalar(disclosed.get("project_version") or disclosed.get("zaofu_version"))])
    if _has_value(disclosed.get("additional_context")):
        sections.extend(["## Additional context", _user_prose(disclosed["additional_context"])])
    if _has_value(disclosed.get("attachment_context")):
        sections.extend([
            "## Attachment context", _user_prose(disclosed["attachment_context"]),
        ])

    facts = [
        ("Classification", disclosed.get("classification")),
        ("Classification status", analysis.get("classification_status")),
        ("Severity", disclosed.get("severity")),
        ("Severity status", analysis.get("severity_status")),
        ("Severity reason", analysis.get("severity_reason")),
        ("Reproduction status", disclosed.get("reproduction_status")),
        ("Component", disclosed.get("component")),
        ("Impact scope", disclosed.get("impact_scope")),
        ("Evidence collection", evidence_mode),
        ("Evidence collection status", evidence_status),
        ("Assessment confidence", disclosed.get("assessment_confidence")),
        ("Assessment", assessment_status),
    ]
    visible_facts = [
        (label, value) for label, value in facts
        if _has_value(value) and str(value).strip().lower() not in {"unknown", "not provided", "not provided."}
    ]
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
            "A project assessment was not performed; triage values may remain "
            "unknown and confidence is low.",
        ])
    elif evidence_status == "interrupted":
        sections.extend([
            "## Incident evidence",
            "Not collected because the user interrupted evidence collection.",
        ])
    elif evidence_status in {"failed", "conflict"}:
        sections.extend(["## Incident evidence", "No incident evidence was collected."])
    analysis_only = {
        key: value for key, value in analysis.items()
        if key not in {
            "evidence_summary", "verification_summary", "investigation_gaps",
            "downstream_verification_tasks", "version_context", "classification_status",
            "severity_status", "severity_reason",
        }
    }
    if _has_value(analysis_only):
        sections.extend(["## Analysis", _analysis_markdown(analysis)])
    if _has_value(analysis.get("evidence_summary")):
        sections.extend(["## Evidence summary", _evidence_list(analysis["evidence_summary"])])
    if _has_value(analysis.get("verification_summary")):
        sections.extend(["## Verification summary", _verification_list(analysis["verification_summary"], analysis.get("evidence_summary"))])
    if _has_value(analysis.get("investigation_gaps")):
        sections.extend(["## Investigation gaps", _ordered_values(_expand_evidence_refs(analysis["investigation_gaps"], analysis.get("evidence_summary")))])
    if _has_value(analysis.get("downstream_verification_tasks")):
        sections.extend(["## Downstream verification tasks", _ordered_values(analysis["downstream_verification_tasks"])])
    if _has_value(analysis.get("version_context")):
        sections.extend(["## Version context", _value(_expand_evidence_refs(analysis["version_context"], analysis.get("evidence_summary")))])
    if _has_value(disclosed.get("recommended_next_action")):
        sections.extend([
            "## Recommended next action",
            _scalar(disclosed["recommended_next_action"]),
        ])
    if _has_value(disclosed.get("published_attachments")):
        sections.extend([
            "## Evidence and attachments",
            _attachments(disclosed["published_attachments"]),
        ])
    if disclosed.get("binary_attachments_omitted") is True:
        sections.extend([
            "## Binary attachments",
            "Binary attachments are not embedded in the Issue body. After publication, "
            "the host publishes download links, sources, descriptions and purposes in a separate Evidence comment.",
        ])
    sections.append(f"<!-- zf-self-issue:{marker} -->")
    return "\n\n".join(sections)


def _has_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple, dict, set)):
        return any(_has_value(item) for item in (value.values() if isinstance(value, dict) else value))
    text = str(value).strip()
    return text not in {"", USER_NOT_PROVIDED, "Not provided."} and not text.startswith("未提供")


def _value(value: Any, *, depth: int = 0) -> str:
    if isinstance(value, dict):
        if not value:
            return "Not provided."
        lines: list[str] = []
        for key in sorted(value):
            label = str(key).replace("_", " ").strip().capitalize() or "Value"
            item = value[key]
            if not _has_value(item):
                continue
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
            if not _has_value(item):
                continue
            prefix = "  " * depth + "-"
            if isinstance(item, (dict, list, tuple)):
                lines.append(prefix)
                lines.append(_value(item, depth=depth + 1))
            else:
                lines.append(f"{prefix} {_scalar(item)}")
        return "\n".join(lines)
    return _scalar(value)


def _ordered_values(value: Any) -> str:
    """Render report lists as stable ordered Markdown, never Python reprs."""
    items = value if isinstance(value, (list, tuple)) else [value]
    lines: list[str] = []
    for item in items:
        item = report_gap(item)
        if isinstance(item, dict):
            order = ("description", "message", "field", "kind", "blocking", "recoverable_by")
            ordered = {key: item[key] for key in order if key in item}
            ordered.update({key: val for key, val in item.items() if key not in ordered})
            lines.append(_record(len(lines) + 1, ordered))
        elif _has_value(item):
            lines.append(f"{len(lines) + 1}. {_scalar(item)}")
    return "\n".join(lines) or "Not provided."


def _evidence_index(evidence: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(evidence, (list, tuple)):
        return {}
    return {str(item[key]): item for item in evidence if isinstance(item, dict)
            for key in ("id", "ref") if item.get(key)}


def _is_evidence_id(value: str) -> bool:
    return bool(re.fullmatch(r"[Ee]\d+|evidence[:_-].+", value))


def _evidence_support(refs: Any, evidence: Any) -> str:
    index = _evidence_index(evidence)
    values = refs if isinstance(refs, (list, tuple)) else [refs]
    lines: list[str] = []
    for ref in values:
        direct_conclusion = ""
        if isinstance(ref, dict):
            direct_conclusion = str(ref.get("conclusion") or ref.get("support") or "").strip()
            key = str(ref.get("evidence_ref") or ref.get("ref") or ref.get("id") or "").strip()
        else:
            key = str(ref or "").strip()
        if not key:
            continue
        item = index.get(key, {})
        detail = direct_conclusion or str(item.get("conclusion") or item.get("result") or item.get("summary") or item.get("meaning") or "").strip()
        # IDs bind the internal evidence graph; #59 displays the actual source.
        source = key
        if _is_evidence_id(key):
            source = next((str(item[name]) for name in ("ref", "metadata_ref", "artifact")
                           if isinstance(item.get(name), str) and item[name]
                           and not _is_evidence_id(item[name])), "")
            if not source and isinstance(item.get("locations"), (list, tuple)):
                source = "; ".join(str(location) for location in item["locations"])
        if detail:
            lines.append(f"{source}：{detail}" if source else detail)
        elif key in index or _is_evidence_id(key):
            lines.append(f"{source}：未找到可核验的证据结论。" if source else "未找到可核验的证据结论。")
        else:
            lines.append(key)  # Legacy support may already be human prose.
    return "\n\n".join(f"{_letter(i)}. {line}" for i, line in enumerate(lines)) or "暂无证据支撑（报告未给出）。"


def _evidence_list(value: Any) -> str:
    items = value if isinstance(value, (list, tuple)) else [value]
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            if _has_value(item):
                lines.append(f"{len(lines) + 1}. {_scalar(item)}")
            continue
        # Preserve commands, locations, scope, artifacts and metadata refs too.
        order = ("id", "kind", "result", "summary", "execution_scope", "command", "commands",
                 "exit_code", "locations", "artifact", "ref", "metadata_ref", "sha256")
        ordered = {key: item[key] for key in order if key in item and key != "id"}
        ordered.update({key: val for key, val in item.items() if key not in ordered and key != "id"})
        if isinstance(ordered.get("ref"), str) and _is_evidence_id(ordered["ref"]):
            ordered.pop("ref")
        lines.append(_record(len(lines) + 1, ordered, multiline_fields={"locations"}))
    return "\n\n".join(lines) or "Not provided."


def _analysis_markdown(value: dict[str, Any]) -> str:
    """Human-oriented Analysis contract; claims/conflicts are peer entries."""
    evidence = value.get("evidence_summary")
    sections: list[str] = []
    claims = value.get("claims")
    if isinstance(claims, (list, tuple)) and claims:
        sections.append("### 一、Claims")
        for index, item in enumerate(claims, 1):
            if not isinstance(item, dict):
                sections.append(f"{index}. {_scalar(item)}")
                continue
            claim = item.get("claim") or item.get("statement") or item.get("text") or item.get("description")
            confidence = item.get("confidence") or item.get("assessment_confidence") or "未单独评估（报告未给出）"
            status = item.get("status") or item.get("state") or "尚未评估（报告未给出）"
            support = item.get("support") or item.get("evidence_refs") or item.get("evidence")
            record = {"Claim": claim, "Confidence": confidence, "Status": status,
                      "Support": _evidence_support(support, evidence)}
            record.update({key: val for key, val in item.items() if key not in {
                "id", "claim_id", "claim", "statement", "text", "description", "confidence",
                "assessment_confidence", "status", "state", "support", "evidence_refs", "evidence",
            }})
            sections.append(_record(index, record, multiline_fields={"Support"}))
    conflicts = value.get("conflicts")
    if isinstance(conflicts, (list, tuple)) and conflicts:
        sections.append("### 二、Conflicts")
        for index, item in enumerate(conflicts, 1):
            if not isinstance(item, dict):
                sections.append(f"{index}. **Conflict:** {_scalar(item)}")
                continue
            subject = item.get("conflict") or item.get("subject") or item.get("topic") or item.get("details")
            refs = item.get("support") or item.get("evidence_refs") or item.get("evidence")
            resolution = item.get("resolution") or item.get("reconcile") or item.get("disposition")
            record = {"Conflict": subject}
            if item.get("details") and item["details"] != subject:
                record["Details"] = item["details"]
            if refs:
                record["Support"] = _evidence_support(refs, evidence)
            if resolution:
                record["Resolution"] = resolution
            record.update({key: val for key, val in item.items() if key not in {
                "conflict", "subject", "topic", "details", "support", "evidence_refs", "evidence",
                "resolution", "reconcile", "disposition",
            }})
            sections.append(_record(index, record, multiline_fields={"Support"}))
    hypotheses = value.get("hypotheses")
    if isinstance(hypotheses, (list, tuple)) and hypotheses:
        sections.append("### 三、Hypotheses")
        for index, item in enumerate(hypotheses, 1):
            if isinstance(item, dict):
                statement = item.get("hypothesis") or item.get("statement") or item.get("claim") or item.get("description")
                record = {"Hypothesis": statement}
                if item.get("confidence"):
                    record["Confidence"] = item["confidence"]
                if item.get("status") or item.get("state"):
                    record["Status"] = item.get("status") or item["state"]
                refs = item.get("support") or item.get("evidence_refs") or item.get("evidence")
                if refs:
                    record["Support"] = _evidence_support(refs, evidence)
                record.update({key: val for key, val in item.items() if key not in {
                    "statement", "hypothesis", "claim", "description", "confidence", "support", "evidence_refs", "evidence",
                    "status", "state",
                }})
                sections.append(_record(index, record, multiline_fields={"Support"}))
            else:
                sections.append(f"{index}. {_scalar(item)}")
    # Preserve all other analysis fields, but keep them ordered and readable.
    other = {key: item for key, item in value.items() if key not in {
        "claims", "conflicts", "hypotheses", "evidence_summary", "verification_summary",
        "investigation_gaps", "downstream_verification_tasks", "version_context", "classification_status",
        "severity_status", "severity_reason",
    } and _has_value(item)}
    if other:
        sections.append("### 四、Other analysis")
        sections.append(_ordered_mapping(_expand_evidence_refs(other, evidence)))
    return "\n\n".join(sections) or "Not provided."


def _verification_list(value: Any, evidence: Any) -> str:
    items = value if isinstance(value, (list, tuple)) else [value]
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            lines.append(f"{len(lines) + 1}. {_scalar(item)}")
            continue
        order = ("name", "status", "meaning", "reason", "support", "evidence_refs", "next_action", "next_action_scope")
        ordered = {key: item[key] for key in order if key in item}
        ordered.update({key: val for key, val in item.items() if key not in ordered})
        lines.append(_record(
            len(lines) + 1,
            _expand_evidence_refs(ordered, evidence),
            multiline_fields={"evidence_refs"},
        ))
    return "\n\n".join(lines) or "Not provided."


def _ordered_mapping(value: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, item in value.items():
        label = str(key).replace("_", " ").strip().capitalize()
        separator = "  \n   " if isinstance(item, (dict, list, tuple)) else " "
        lines.append(f"{len(lines) + 1}. **{label}:**" + separator + _readable(item).replace("\n", "\n   "))
    return "\n\n".join(lines)


def _letter(index: int) -> str:
    return (_letter(index // 26 - 1) if index >= 26 else "") + chr(97 + index % 26)


def _readable(value: Any, *, depth: int = 0) -> str:
    if isinstance(value, dict):
        return "\n\n".join(
            f"{_nested_marker(i, depth)}. **{key.replace('_', ' ').capitalize()}:** "
            f"{_readable(item, depth=depth + 1)}"
            for i, (key, item) in enumerate(value.items()) if _has_value(item)
        )
    if isinstance(value, (list, tuple)):
        return "\n\n".join(
            (f"{_nested_marker(i, depth)}.  \n   " + _readable(item, depth=depth + 1).replace("\n", "\n   ")
             if isinstance(item, (dict, list, tuple))
             else f"{_nested_marker(i, depth)}. {_readable(item, depth=depth + 1)}")
            for i, item in enumerate(value) if _has_value(item)
        )
    return _scalar(value)


def _nested_marker(index: int, depth: int) -> str:
    """Use letters for the first nested level and Roman numerals below it."""
    return _letter(index) if depth == 0 else _roman(index)


def _roman(index: int) -> str:
    value = index + 1
    result = ""
    for number, symbol in ((10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while value >= number:
            result += symbol
            value -= number
    return result


def _record(index: int, value: dict[str, Any], *, multiline_fields: set[str] | None = None) -> str:
    fields = [(key, item) for key, item in value.items() if _has_value(item)]
    lines = []
    for position, (key, item) in enumerate(fields):
        label = key.replace("_", " ").capitalize()
        prefix = f"{index}. " if position == 0 else f"   ({position}) "
        rendered = _readable(item)
        if multiline_fields and key in multiline_fields:
            # Markdown hard breaks, not new CSS or a new list style. Keep all
            # lettered entries aligned even in the inline-paragraph preview.
            rendered = rendered.replace("\n\n", "  \n")
            lines.append(f"{prefix}**{label}:**  \n   " + rendered.replace("\n", "\n   "))
        else:
            lines.append(f"{prefix}**{label}:** " + rendered.replace("\n", "\n   "))
    return "\n\n".join(lines)


def _expand_evidence_refs(value: Any, evidence: Any) -> Any:
    if isinstance(value, dict):
        return {key: _evidence_support(item, evidence) if key in {"evidence_refs", "support"}
                else _expand_evidence_refs(item, evidence) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_expand_evidence_refs(item, evidence) for item in value]
    return value


def _scalar(value: Any) -> str:
    if value is None or value == "":
        return "Not provided."
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value).strip() or "Not provided."


def _user_scalar(value: Any) -> str:
    if value is None or not str(value).strip():
        return USER_NOT_PROVIDED
    if isinstance(value, str) and value.lstrip().startswith(("[", "{")):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, (list, dict)):
                value = decoded
        except ValueError:
            pass
    if isinstance(value, dict):
        return _value(value)
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value if str(item).strip()]
        if not items:
            return USER_NOT_PROVIDED
        return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))
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
        lines.append(f"[{label}](<{url}>)")
    return "\n".join(lines) or "Not provided."
