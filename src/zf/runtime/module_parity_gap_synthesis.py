"""Synthesize module parity gap tasks from verifier findings."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParityGapSynthesis:
    gap_tasks: list[dict[str, Any]] = field(default_factory=list)
    open_p0_p1_gap_count: int = 0
    open_findings: list[dict[str, Any]] = field(default_factory=list)


def synthesize_gap_tasks_from_parity_payloads(
    payloads: list[dict[str, Any]],
    *,
    pdd_id: str = "",
    source_index_ref: str = "",
    evidence_refs: list[str] | None = None,
) -> ParityGapSynthesis:
    """Turn P0/P1 module parity findings into canonical gap task inputs.

    Verifier roles should prefer emitting explicit ``gap_tasks``. This helper is
    the deterministic safety net for older/looser verifier output that only
    reports structured findings.
    """

    open_findings = [
        finding
        for finding in _collect_findings(payloads)
        if _finding_priority(finding) in {"P0", "P1"}
    ]
    grouped_findings = _group_findings_by_claim_path(open_findings)
    tasks = [
        _finding_to_gap_task(
            finding_group,
            pdd_id=pdd_id,
            source_index_ref=source_index_ref,
            evidence_refs=evidence_refs or [],
            ordinal=idx + 1,
        )
        for idx, finding_group in enumerate(grouped_findings)
    ]
    return ParityGapSynthesis(
        gap_tasks=tasks,
        open_p0_p1_gap_count=len(open_findings),
        open_findings=open_findings,
    )


def filter_open_p0_p1_gap_tasks(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only explicit open P0/P1 parity gap tasks.

    Some verifier outputs include informational findings that mention the words
    "P0/P1" only to say a gap is *not* open. Those must not be promoted into
    implementation work.
    """

    out: list[dict[str, Any]] = []
    for task in tasks:
        if not _gap_task_priority_is_open_p0_p1(task):
            continue
        if _task_declares_non_gap(task):
            continue
        findings = task.get("findings")
        if isinstance(findings, list) and findings:
            structured_findings = [
                item for item in findings if isinstance(item, dict)
            ]
            if structured_findings and not any(
                _finding_priority(item) in {"P0", "P1"}
                for item in structured_findings
            ):
                continue
        out.append(task)
    return out


def _collect_findings(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for payload in payloads:
        for source in (payload, payload.get("report")):
            if not isinstance(source, dict):
                continue
            raw = source.get("findings")
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    finding = dict(item)
                    key = _finding_key(finding)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(finding)
    return findings


def _group_findings_by_claim_path(
    findings: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for finding in findings:
        path = str(finding.get("path") or "").strip()
        module_id = _module_id_from_path(path)
        key = (module_id, path or f"packages/{module_id}/**")
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(finding)
    return [grouped[key] for key in order]


def _finding_key(finding: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(finding.get("severity") or "").strip().lower(),
        str(finding.get("category") or "").strip().lower(),
        str(finding.get("path") or "").strip(),
        str(finding.get("line") or "").strip(),
        str(finding.get("message") or finding.get("summary") or "").strip(),
    )


def _finding_to_gap_task(
    findings: list[dict[str, Any]],
    *,
    pdd_id: str,
    source_index_ref: str,
    evidence_refs: list[str],
    ordinal: int,
) -> dict[str, Any]:
    finding = findings[0] if findings else {}
    path = str(finding.get("path") or "").strip()
    messages = [
        str(item.get("message") or item.get("summary") or "").strip()
        for item in findings
        if isinstance(item, dict)
    ]
    messages = [message for message in messages if message]
    message = messages[0] if messages else ""
    module_id = _module_id_from_path(path)
    priorities = [_finding_priority(item) for item in findings]
    priority = "P0" if "P0" in priorities else "P1"
    task_id = _gap_task_id(
        pdd_id=pdd_id,
        module_id=module_id,
        path=path,
        message="|".join(messages) or message,
        ordinal=ordinal,
    )
    refs = _dedupe([
        *([path] if path else []),
        *[ref for item in messages for ref in _python_refs(item)],
        *([source_index_ref] if source_index_ref else []),
        *evidence_refs[:6],
    ])
    acceptance = [_acceptance_text(item) for item in messages]
    acceptance = _dedupe(acceptance) or [
        "Restore goal parity for these findings.",
    ]
    claim_paths = _claim_paths(path, module_id, findings)
    verify_commands = _verify_commands(findings)
    return {
        "task_id": task_id,
        "module_id": module_id,
        "affinity_tag": _affinity_tag(module_id),
        "owner_role": "dev",
        "priority": priority,
        "gap_kind": "module_parity_gap",
        "title": _task_title(priority, module_id, messages, path),
        "claim_paths": claim_paths,
        "acceptance": acceptance,
        "verify_commands": verify_commands,
        "source_refs": refs or ["module parity scan findings"],
        "findings": [_finding_summary(item) for item in findings],
    }


def _finding_summary(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in finding.items()
        if key in {"severity", "category", "path", "line", "message"}
    }


def _finding_priority(finding: dict[str, Any]) -> str:
    if _finding_declares_non_gap(finding):
        return "P2"
    explicit = _explicit_priority(finding)
    if explicit:
        return explicit
    severity = str(finding.get("severity") or "").strip().lower()
    if severity in {"critical", "blocker", "high"}:
        return "P0"
    if severity in {"medium", "major"}:
        return "P1"
    declared = _message_declared_priority(
        " ".join([
            str(finding.get("category") or ""),
            str(finding.get("message") or finding.get("summary") or ""),
        ])
    )
    if declared:
        return declared
    return "P2"


def _explicit_priority(item: dict[str, Any]) -> str:
    for key in ("priority", "gap_priority", "severity"):
        value = str(item.get(key) or "").strip().upper()
        if value in {"P0", "P1", "P2"}:
            return value
    return ""


def _message_declared_priority(text: str) -> str:
    raw = str(text or "")
    patterns = (
        r"^\s*(?:\[[^\]]+\]\s*)?P([01])\b",
        r"\b(?:priority|severity|level|risk)\s*[:=]\s*P([01])\b",
        r"\bP([01])\s+(?:module\s+parity\s+)?gap\b",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            return f"P{match.group(1)}"
    return ""


def _finding_declares_non_gap(finding: dict[str, Any]) -> bool:
    severity = str(finding.get("severity") or "").strip().lower()
    text = _normalized_gap_text([
        finding.get("category"),
        finding.get("message"),
        finding.get("summary"),
    ])
    return _text_declares_non_gap(text) or (
        severity in {"info", "informational", "notice", "low", "debug"}
        and not _explicit_priority({
            "priority": finding.get("priority"),
            "gap_priority": finding.get("gap_priority"),
        })
        and not _message_declared_priority(
            str(finding.get("message") or finding.get("summary") or "")
        )
    )


def _gap_task_priority_is_open_p0_p1(task: dict[str, Any]) -> bool:
    priority = _explicit_priority(task)
    if priority:
        return priority in {"P0", "P1"}
    return _message_declared_priority(
        " ".join([
            str(task.get("title") or ""),
            str(task.get("summary") or ""),
        ])
    ) in {"P0", "P1"}


def _task_declares_non_gap(task: dict[str, Any]) -> bool:
    parts: list[Any] = [
        task.get("title"),
        task.get("summary"),
        task.get("message"),
    ]
    for key in ("acceptance", "findings"):
        value = task.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    parts.extend([
                        item.get("message"),
                        item.get("summary"),
                        item.get("category"),
                        item.get("severity"),
                    ])
                else:
                    parts.append(item)
    return _text_declares_non_gap(_normalized_gap_text(parts))


def _normalized_gap_text(parts: list[Any]) -> str:
    return " ".join(str(part or "") for part in parts).lower().replace("_", " ")


def _text_declares_non_gap(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return False
    phrases = (
        "not a capability gap",
        "not a parity gap",
        "not a gap",
        "not a defect",
        "no open p0/p1",
        "no open p0 or p1",
        "no p0/p1",
        "0 open p0/p1",
        "open p0/p1=0",
        "open p0/p1 = 0",
        "do not raise as p0/p1",
        "do not raise as p0 or p1",
        "0 个未决 p0/p1",
        "无 p0/p1",
    )
    return any(phrase in compact for phrase in phrases)


def _module_id_from_path(path: str) -> str:
    match = re.search(r"(?:^|/)packages/([^/]+)", path)
    if match:
        return match.group(1).strip().replace("_", "-") or "runtime"
    if path:
        return path.split("/", 1)[0].strip().replace("_", "-") or "runtime"
    return "runtime"


def _affinity_tag(module_id: str) -> str:
    if module_id in {"contracts", "core", "test-harness"}:
        return "pi-core"
    if module_id in {"providers", "tools", "mcp"}:
        return "provider-tools-mcp"
    if module_id in {"state", "security"}:
        return "state-security"
    if module_id in {"gateway", "cli"}:
        return "gateway-cli"
    if module_id in {"tui-adapter", "web-adapter", "web"}:
        return "web-tui"
    if module_id in {"skills", "plugins"}:
        return "skills-plugins"
    return module_id or "runtime"


def _gap_task_id(
    *,
    pdd_id: str,
    module_id: str,
    path: str,
    message: str,
    ordinal: int,
) -> str:
    base = _safe_id_part(pdd_id) or "MODULE"
    if len(base) > 32:
        base = f"{base[:22]}-{_short_hash(base)}"
    module = _safe_id_part(module_id) or "RUNTIME"
    digest = _short_hash("|".join([path, message, str(ordinal)]))
    return f"{base}-PARITY-{module}-{digest}"


def _safe_id_part(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip()).strip("-")
    return text.upper()


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:8].upper()


def _task_title(
    priority: str,
    module_id: str,
    messages: list[str],
    path: str,
) -> str:
    prefix = f"{priority} module parity gap"
    location = path or module_id
    message = messages[0] if messages else ""
    summary = message.split("Gap task hint:", 1)[0].strip() or message
    if len(messages) > 1:
        summary = f"{len(messages)} findings: {summary}"
    if len(summary) > 140:
        summary = summary[:137].rstrip() + "..."
    return f"{prefix}: {location} - {summary}"


def _claim_paths(path: str, module_id: str, findings: list[dict[str, Any]]) -> list[str]:
    explicit: list[str] = []
    for finding in findings:
        for key in (
            "claim_paths",
            "source_paths",
            "target_paths",
            "affected_paths",
            "paths",
        ):
            explicit.extend(_string_list(finding.get(key)))
    explicit = _dedupe(explicit)
    if explicit:
        return explicit
    if path:
        return [path]
    if module_id and module_id != "runtime":
        return [f"**/{module_id}/**"]
    return ["**"]


def _acceptance_text(message: str) -> str:
    hint = ""
    if "Gap task hint:" in message:
        hint = message.split("Gap task hint:", 1)[1].strip()
    text = hint or message or "Restore goal parity for this finding."
    if len(text) > 500:
        text = text[:497].rstrip() + "..."
    return text


def _verify_commands(findings: list[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    for finding in findings:
        for key in (
            "verify_commands",
            "verification_commands",
            "verification",
            "test_commands",
        ):
            commands.extend(_string_list(finding.get(key)))
    return _dedupe(commands) or [
        "Project-specific verification command required by workflow profile or scan artifact.",
    ]


def _python_refs(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_./-]+\.py(?::\d+(?:-\d+)?)?", text or "")


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


__all__ = [
    "ParityGapSynthesis",
    "filter_open_p0_p1_gap_tasks",
    "synthesize_gap_tasks_from_parity_payloads",
]
