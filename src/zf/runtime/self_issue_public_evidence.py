"""Prepare bounded evidence candidates for explicit external disclosure."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from zf.core.self_issue.safe_export import safe_report_text
from zf.runtime.self_issue_intake import sanitize_attachment_for_disclosure
from zf.runtime.self_issue_log_evidence import verified_log_candidate_map


_NO_SEMANTIC_LOG_MATCH = (
    "No exception or error log location semantically related to the user's problem "
    "description was identified; only log-tail context is provided."
)


def prepare_public_evidence_attachments(
    state_dir: Path,
    *,
    draft_id: str,
    run_id: str,
    mechanical_evidence: dict[str, Any],
    semantic_log_findings: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Create redacted summary and trusted Playwright screenshot candidates.

    Returned files remain local-only until the existing attachment manifest is
    previewed, confirmed, and uploaded by the Kernel.
    """
    root = Path(state_dir).resolve()
    destination = root / "artifacts" / "self-issues" / draft_id / "public-evidence" / run_id
    descriptors: list[dict[str, Any]] = []

    summary = _evidence_markdown(
        mechanical_evidence,
        semantic_log_findings=semantic_log_findings or [],
    )
    if summary:
        path = destination / "incident-evidence-summary.md"
        _atomic_write(path, summary.encode("utf-8"))
        descriptors.append(_descriptor(
            root, path, content_type="text/markdown",
            kind="self_issue_public_evidence_summary",
            capture_source="kernel_redacted_summary",
            redaction_applied=True,
        ))

    screenshots = mechanical_evidence.get("screenshot_refs")
    screenshots = screenshots if isinstance(screenshots, list) else []
    for index, raw in enumerate(screenshots, start=1):
        if len(descriptors) >= 4 or not isinstance(raw, dict):
            break
        if str(raw.get("capture_source") or "") != "playwright":
            continue
        relative = Path(str(raw.get("ref") or ""))
        source = (root / relative).resolve()
        suffix = source.suffix.lower()
        if (
            relative.is_absolute()
            or not source.is_relative_to(root)
            or not source.is_file()
            or suffix not in {".png", ".jpg", ".jpeg"}
            or hashlib.sha256(source.read_bytes()).hexdigest() != str(raw.get("sha256") or "")
        ):
            continue
        content_type = "image/png" if suffix == ".png" else "image/jpeg"
        sanitized, _ = sanitize_attachment_for_disclosure(
            source.read_bytes(), suffix=suffix, content_type=content_type,
        )
        path = destination / f"playwright-incident-{index}{suffix}"
        _atomic_write(path, sanitized)
        descriptors.append(_descriptor(
            root, path, content_type=content_type,
            kind="self_issue_public_evidence_screenshot",
            capture_source="playwright",
            redaction_applied=True,
        ))
    # Assessment adapters may expose additional bounded artifacts (logs,
    # videos, traces, or test outputs).  Preserve them as downloadable
    # agent evidence instead of embedding their contents in the Issue body.
    extra_refs: list[dict[str, Any]] = []
    for key in ("artifact_refs", "evidence_files", "tool_artifacts", "video_refs"):
        value = mechanical_evidence.get(key)
        if isinstance(value, list):
            extra_refs.extend(item for item in value if isinstance(item, dict))
    seen = {str(item.get("sha256") or "") for item in descriptors}
    for index, raw in enumerate(extra_refs, start=1):
        if len(descriptors) >= 8:
            break
        relative = Path(str(raw.get("ref") or ""))
        source = (root / relative).resolve()
        digest = str(raw.get("sha256") or "")
        content_type = str(raw.get("content_type") or "application/octet-stream").split(";", 1)[0].lower()
        if (
            not digest or digest in seen or relative.is_absolute()
            or not source.is_relative_to(root) or not source.is_file()
            or source.stat().st_size > 20 * 1024 * 1024
            or hashlib.sha256(source.read_bytes()).hexdigest() != digest
        ):
            continue
        suffix = source.suffix.lower()
        data = source.read_bytes()
        if content_type.startswith(("text/", "application/json")):
            data, _ = sanitize_attachment_for_disclosure(data, suffix=suffix, content_type=content_type)
        path = destination / f"agent-evidence-{index}{suffix or '.bin'}"
        _atomic_write(path, data)
        descriptors.append(_descriptor(
            root, path, content_type=content_type,
            kind="self_issue_public_evidence_artifact",
            capture_source="assessment_agent",
            redaction_applied=content_type.startswith(("text/", "application/json")),
        ))
        seen.add(hashlib.sha256(data).hexdigest())
    return descriptors


def _evidence_markdown(
    evidence: dict[str, Any], *, semantic_log_findings: list[dict[str, str]],
) -> str:
    sections = [
        "# ZaoFu incident evidence summary",
        "This file contains bounded, redacted evidence selected for explicit disclosure.",
    ]
    timing = evidence.get("web_api_timing")
    timing = timing if isinstance(timing, dict) else {}
    routes = timing.get("routes")
    routes = routes if isinstance(routes, list) else []
    has_evidence = False
    if routes:
        has_evidence = True
        lines = [
            "| Method | Route | Status | Count | p50 ms | p95 ms | Max ms |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for raw in routes[:30]:
            if not isinstance(raw, dict):
                continue
            route = safe_report_text(str(raw.get("route") or "")).replace("|", "\\|")
            lines.append(
                f"| {raw.get('method', '')} | `{route}` | {raw.get('status_code', '')} | "
                f"{raw.get('count', '')} | {raw.get('p50_ms', '')} | "
                f"{raw.get('p95_ms', '')} | {raw.get('max_ms', '')} |"
            )
        sections.extend(["## Web API timing", "\n".join(lines)])

    log_refs = evidence.get("log_refs")
    log_refs = log_refs if isinstance(log_refs, list) else []
    candidate_map = verified_log_candidate_map(evidence.get("log_error_candidates"))
    semantic_matches = []
    for finding in semantic_log_findings[:20]:
        if not isinstance(finding, dict):
            continue
        candidate = candidate_map.get(str(finding.get("candidate_id") or ""))
        if (
            candidate is None
            or str(finding.get("relation") or "") == "uncertain"
            or str(finding.get("confidence") or "") == "low"
        ):
            continue
        path = safe_report_text(str(candidate["path"])).replace("`", "")
        line = int(candidate["line"])
        category = safe_report_text(str(candidate["category"]))
        relation = safe_report_text(str(finding.get("relation") or ""))
        confidence = safe_report_text(str(finding.get("confidence") or ""))
        reason = safe_report_text(str(finding.get("reason") or ""))
        log_line = safe_report_text(str(candidate["redacted_line"])).replace(
            "```", "``\u200b`",
        )
        semantic_matches.append(
            f"### `{path}:{line}`\n\n"
            f"- **Category:** {category}\n"
            f"- **Relationship:** {relation}\n"
            f"- **Assessment confidence:** {confidence}\n"
            f"- **Reason:** {reason}\n\n"
            f"```text\n{log_line}\n```"
        )
    if log_refs or candidate_map:
        has_evidence = True
        sections.extend([
            "## Semantically related error log locations",
            "\n\n".join(semantic_matches) if semantic_matches else _NO_SEMANTIC_LOG_MATCH,
        ])

    excerpts = evidence.get("log_excerpts")
    excerpts = excerpts if isinstance(excerpts, list) else []
    safe_excerpts = []
    for raw in excerpts[:10]:
        if not isinstance(raw, dict):
            continue
        path = safe_report_text(str(raw.get("path") or "local log"))
        tail = _meaningful_log_excerpt(
            safe_report_text(str(raw.get("redacted_tail") or "")),
        )
        if not tail:
            continue
        tail = tail.replace("```", "``\u200b`")
        safe_excerpts.append(f"### `{path}`\n\n```text\n{tail}\n```")
    if safe_excerpts:
        has_evidence = True
        sections.extend(["## Redacted log excerpts", "\n\n".join(safe_excerpts)])

    event_log = evidence.get("event_log")
    event_log = event_log if isinstance(event_log, dict) else {}
    event_refs = event_log.get("recent_failure_refs")
    event_refs = event_refs if isinstance(event_refs, list) else []
    safe_events = []
    for raw in event_refs[:20]:
        if not isinstance(raw, dict):
            continue
        safe_events.append(
            "- " + " · ".join(
                safe_report_text(str(value))
                for value in (
                    raw.get("ts") or "unknown time",
                    raw.get("type") or "unknown event",
                    raw.get("event_id") or "unknown id",
                )
            )
        )
    if safe_events:
        has_evidence = True
        sections.extend(["## Recent failure event references", "\n".join(safe_events)])

    locations = evidence.get("code_locations")
    locations = locations if isinstance(locations, list) else []
    safe_locations = [
        f"- `{safe_report_text(str(value)).replace('`', '')}`"
        for value in locations[:50]
        if str(value).strip()
    ]
    if safe_locations:
        has_evidence = True
        sections.extend(["## Referenced code locations", "\n".join(safe_locations)])

    browser = evidence.get("browser_capture")
    browser = browser if isinstance(browser, dict) else {}
    browser_status = safe_report_text(str(browser.get("status") or "not_available"))
    browser_reason = safe_report_text(str(browser.get("reason") or ""))
    sections.extend([
        "## Playwright capture status",
        f"- **Status:** {browser_status}\n- **Reason:** {browser_reason or 'Not provided.'}",
    ])
    return "\n\n".join(sections).strip() + "\n" if has_evidence else ""


def _meaningful_log_excerpt(value: str) -> str:
    lines = []
    for raw in str(value or "").splitlines():
        stripped = raw.strip()
        if not stripped or stripped in {"[]", "{}", "null", "None"}:
            continue
        lines.append(raw.rstrip())
    return "\n".join(lines[-120:]).strip()


def _descriptor(
    root: Path,
    path: Path,
    *,
    content_type: str,
    kind: str,
    capture_source: str,
    redaction_applied: bool,
) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "ref_schema_version": "sidecar-ref.v1",
        "kind": kind,
        "ref": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "byte_count": len(content),
        "content_type": content_type,
        "schema_version": "self-issue-public-evidence.v1",
        "encoding": "utf-8" if content_type.startswith("text/") else "binary",
        "created_by": "kernel",
        # ``source`` is the stable disclosure identity used by publication
        # projections.  ``capture_source`` remains the more detailed
        # provenance (for example ``playwright``).
        "source": "agent",
        "access_scope": {"external_disclosure": False},
        "retention": {"class": "user_controlled"},
        "required": False,
        "preview": path.name,
        "attachment_id": f"evidence-{hashlib.sha256(content).hexdigest()[:12]}",
        "filename": path.name,
        "redaction_applied": redaction_applied,
        "capture_source": capture_source,
        "description": _description_for(kind, path.name),
        "purpose": _purpose_for(kind),
        "public_disclosure_confirmed": False,
    }


def _description_for(kind: str, filename: str) -> str:
    if kind == "self_issue_public_evidence_screenshot":
        return f"Agent-captured browser evidence ({filename})."
    if kind == "self_issue_public_evidence_summary":
        return "Redacted summary of evidence collected during assessment."
    return f"Agent-collected evidence artifact ({filename})."


def _purpose_for(kind: str) -> str:
    if kind == "self_issue_public_evidence_screenshot":
        return "Support the reported behavior with a clean, passive browser capture."
    if kind == "self_issue_public_evidence_summary":
        return "Preserve the redacted evidence inventory and verification context."
    return "Provide a downloadable artifact for Issue triage and verification."


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
