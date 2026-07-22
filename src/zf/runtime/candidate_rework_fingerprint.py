"""Stable semantic identity helpers for candidate recovery."""

from __future__ import annotations

import hashlib
import re
from typing import Any


def rejection_fingerprint(payload: dict[str, Any]) -> str:
    """Return a retry identity without volatile diagnostic paths or times."""

    if (
        str(payload.get("failure_scope") or "") == "candidate"
        and str(payload.get("failure_class") or "").strip()
    ):
        failed_gates = payload.get("quality_gates_failed")
        if not isinstance(failed_gates, list):
            failed_gates = []
        semantic_parts = [
            str(payload.get("failure_class") or "").strip().lower(),
            str(payload.get("failing_command") or "").strip().lower(),
            str(payload.get("exit_code") or "").strip().lower(),
            "|".join(sorted(
                str(item).strip().lower()
                for item in failed_gates
                if str(item).strip()
            )),
        ]
        diagnostics = [
            str(payload.get("diagnostic_summary") or "").strip(),
            str(
                payload.get("primary_failure_reason")
                or payload.get("reason")
                or ""
            ).strip(),
        ]
        findings = payload.get("findings")
        for item in findings if isinstance(findings, list) else []:
            if isinstance(item, dict):
                diagnostics.append(str(
                    item.get("message")
                    or item.get("summary")
                    or item.get("reason")
                    or ""
                ).strip())
            else:
                diagnostics.append(str(item).strip())
        semantic_parts.extend(
            feedback_identity(item)
            for item in diagnostics
            if item
        )
        text = "\n".join(part for part in semantic_parts if part)
        if text:
            return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]

    structured = str(payload.get("failure_fingerprint") or "").strip()
    if structured:
        return structured
    parts = [str(payload.get("reason") or "").strip().lower()]
    findings = payload.get("findings")
    for item in findings if isinstance(findings, list) else []:
        if isinstance(item, dict):
            parts.append(str(
                item.get("message") or item.get("path") or ""
            ).strip().lower())
        else:
            parts.append(str(item).strip().lower())
    report = payload.get("report")
    if isinstance(report, dict):
        for item in report.get("findings") or []:
            if isinstance(item, dict):
                parts.append(str(item.get("message") or "").strip().lower())
    text = "\n".join(sorted(part for part in parts if part))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def dedupe_feedback(feedback: list[str], *, limit: int = 20) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in feedback:
        line = str(value or "").strip()
        if not line:
            continue
        key = feedback_identity(line)
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def feedback_identity(value: str) -> str:
    normalized = value.lower()
    normalized = re.sub(
        r"(?:[a-z]:)?[^\s]*[\\/]\.npm[\\/]_logs[\\/][^\s]+",
        "<npm-log>",
        normalized,
    )
    normalized = re.sub(
        r"\b\d{4}-\d{2}-\d{2}t\d{2}[:_]\d{2}[:_]\d{2}(?:\.\d+)?z?\b",
        "<timestamp>",
        normalized,
    )
    normalized = re.sub(r"\b[0-9a-f]{40}\b", "<commit>", normalized)
    return normalized


__all__ = ["dedupe_feedback", "feedback_identity", "rejection_fingerprint"]
