"""Controlled persistence for Issue workspace drafts and full activity results."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Mapping

from issue_core.session import SessionStatus, _now
from zf.core.security.redaction import redact_obj
from zf.core.state.locks import locked_path
from zf.core.self_issue.models import IssueDraft, SEVERITIES, REPRODUCTION_STATUSES, EVIDENCE_COLLECTION_MODES
from zf.core.self_issue.report_projection import publication_facts, version_context
from zf.runtime.sidecar_refs import write_sidecar_bytes, sidecar_path
from zf.runtime.self_issue_intake import sanitize_attachment_for_disclosure
from zf.core.self_issue.safe_export import safe_report_text

_AGENT_EVIDENCE_LIMIT = 20 * 1024 * 1024
_AGENT_TEXT_SUFFIXES = {".py", ".pyi", ".toml", ".ini", ".cfg", ".txt", ".md", ".log", ".json", ".yaml", ".yml", ".xml", ".csv", ".rst", ".sh"}
_AGENT_BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".mp4", ".webm", ".pdf"}
_SENSITIVE_EVIDENCE_NAMES = {".git", ".ssh", ".aws", ".codex", ".gnupg", "credentials", "secrets", "id_rsa", "id_ed25519", ".npmrc", ".netrc", ".pypirc"}


def _disclosable_evidence_text(text: str) -> str:
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b", "[REDACTED_GITHUB_TOKEN]", text)
    text = re.sub(r'''(?im)(\b[\w-]*(?:token|password|secret|api_key)[\w-]*["']?\s*[:=]\s*)["'][^"'\n]+["']''', r'\1"[REDACTED_SECRET]"', text)
    return safe_report_text(text)


def _portable_evidence_ref(value: Any) -> str:
    """Keep useful repo/file location while removing host-specific prefixes."""
    text = str(value or "")
    if not text.startswith("/"):
        return text
    location = re.search(r":\d+(?:-\d+)?$", text)
    lines = location.group(0) if location else ""
    path = Path(text[:-len(lines)] if lines else text)
    for parent in path.parents:
        if (parent / ".git").exists():
            return path.relative_to(parent).as_posix() + lines
    for index, part in enumerate(path.parts):
        if part in {"src", "tests", "docs", "scripts"}:
            return "/".join(path.parts[index:]) + lines
    return path.name + lines


def _portable_evidence_item(item: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(item)
    if result.get("ref"):
        result["ref"] = _portable_evidence_ref(result["ref"])
    return result


class IssueWorkspaceMixin:
    """Host edge only: portable session code does not depend on this module."""

    def materialize_publication_draft(self, session_id: str, *, report_index: int = -1) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues/workspace.lock"):
            with locked_path(self.state_dir / "self-issues/update.lock"):
                return self._materialize_publication_draft(session_id, report_index=report_index)

    def _materialize_publication_draft(self, session_id: str, *, report_index: int = -1) -> dict[str, Any]:
        """Bridge a confirmed portable report into the existing publication UI.

        The portable session remains the source of truth.  This creates one
        mutable Draft projection solely so the established preview/confirm/
        publish flow can be reused without a second Agent or a second report.
        """
        session = self.store.load_session(session_id)
        if report_index < 0 and session.status not in {SessionStatus.READY_FOR_CONFIRMATION, SessionStatus.COMPLETED}:
            raise ValueError("Issue Session must finish assessment before creating a Draft")
        working = self.store.load_working_set(session_id)
        current_report = self._stage_report_contract(working.report, working.fields, working.gaps)
        if report_index >= 0:
            history = list(working.workflow.get("reanalysis_history") or [])
            if report_index >= len(history):
                raise ValueError("unknown report version")
            previous = history[report_index]
            working = replace(working, report=previous["report"], fields={},
                              evidences=tuple(previous.get("evidences") or []),
                              workflow={**dict(working.workflow), "validation": previous.get("validation") or {},
                                        "activity_log": previous.get("activity_log") or [],
                                        "reanalysis_history": history[:report_index]})
        service = self._service()
        existing_draft = None
        if session.publication_draft_id:
            existing_draft = service.drafts.get(session.publication_draft_id)
            # A portable session is the source of truth.  Older builds returned
            # the first Draft forever, which left restored sessions showing
            # ``unknown``/placeholder fields and stale preview controls.  Only
            # published (or an in-flight/uncertain publication) Draft is
            # immutable; every other historical projection is safely refreshed
            # from the current Working Set below.
            if existing_draft is not None and existing_draft.publication_state in {
                "published", "partially_published", "publishing", "outcome_unknown",
            }:
                return {
                    "ok": True,
                    "status": "publication_draft_ready",
                    "session_id": session_id,
                    "publication_draft_id": session.publication_draft_id,
                    "draft": service._draft_view(existing_draft),
                    "report": self._stage_report_contract(
                        working.report, working.fields, working.gaps,
                    ),
                }

        # Semantic fields are the canonical issue facts.  The report contains
        # synthesis/governance metadata, so it must not be used as the only
        # source when creating the host Draft.
        report = self._stage_report_contract(working.report, working.fields, working.gaps)
        validation = dict(working.workflow.get("validation") or {})
        verification_summary = []
        for item in validation.get("checks") or []:
            if isinstance(item, Mapping):
                verification_summary.append({
                    **dict(item),
                    "name": str(item.get("name") or "check"),
                    "status": str(item.get("status") or "not_run"),
                    "meaning": str(item.get("meaning") or ""),
                    "reason": str(item.get("reason") or ""),
                    "next_action": str(item.get("next_action") or ""),
                    "next_action_scope": str(item.get("next_action_scope") or ""),
                    "evidence_refs": [str(ref) for ref in item.get("evidence_refs") or []],
                })
        target = service._target_binding(session.target_binding)
        fingerprint = hashlib.sha256(
            f"portable:{session.session_id}:{session.raw_report}".encode("utf-8")
        ).hexdigest()
        classification = self._normalize_classification(self._report_text(report, "classification", "unknown"))
        severity = self._report_text(report, "severity", "P2")
        reproduction_status = self._report_text(report, "reproduction_status", "unverified")
        environment_value = report.get("environment")
        environment = dict(environment_value) if isinstance(environment_value, Mapping) else {}
        if "architecture" in environment and "arch" not in environment:
            environment["arch"] = environment["architecture"]
        for key in ("os", "version", "arch", "python"):
            current = str(environment.get(key) or "").strip().lower()
            if current.startswith("未提供") or "user did not provide" in current:
                environment.pop(key, None)
        # Never fill reproduction fields from the machine rendering the Draft.
        # That machine can differ from both the investigator and affected host.
        versions = version_context(report, session.source_binding)
        if severity not in SEVERITIES:
            severity = "P2"
        if reproduction_status not in REPRODUCTION_STATUSES:
            reproduction_status = "unverified"
        evidence_refs = [
            _portable_evidence_item(item) for item in working.evidences
            if isinstance(item, Mapping) and str(item.get("ref") or "")
        ]
        draft = IssueDraft(
            draft_id=(existing_draft.draft_id if existing_draft is not None
                      else f"sid-pub-{session.session_id.removeprefix('issue-session-')}"),
            subject_scope=str(target.get("project") or "project"),
            target_binding=target,
            incident_fingerprint=fingerprint,
            title=self._report_text(report, "title", "Observed project issue"),
            summary=self._report_text(report, "summary", self._report_text(report, "bug_description")),
            classification=classification,
            severity=severity,
            reproduction_status=reproduction_status,
            evidence_refs=evidence_refs,
            disclosure_fields=[
                "bug_description", "reproduction_steps", "actual_behavior",
                "expected_behavior", "environment", "project_version",
                "additional_context", "classification", "severity",
                "reproduction_status", "component", "impact_scope",
                "assessment_confidence", "analysis", "recommended_next_action",
                "source_code_revision", "evidence_collection_status",
                "runtime_status", "assessment_status", "evidence_collection_mode",
                "evidence_limit_reason",
            ],
            analysis={
                "understanding": {"status": "completed", "source": "portable_issue_session"},
                "portable_session_id": session.session_id,
                "external_issue_disposition": str(report.get("external_issue_disposition") or "needs_triage"),
                "investigation_gaps": list(report.get("investigation_gaps") or ()),
                "downstream_verification_tasks": list(report.get("downstream_verification_tasks") or ()),
                "suggested_fix_directions": report.get("suggested_fix_directions") or [],
                "root_cause_status": report.get("root_cause_status") or report.get("assessment") or "",
                "risks": report.get("risks") or [],
                "validation": dict(working.workflow.get("validation") or {}),
                "verification_summary": verification_summary,
                "evidence_summary": [_portable_evidence_item(item) for item in working.evidences if isinstance(item, Mapping)][:50],
                "claims": [dict(item) for item in working.claims if isinstance(item, Mapping)][:50],
                "hypotheses": [dict(item) for item in working.hypotheses if isinstance(item, Mapping)][:50],
                "conflicts": [dict(item) for item in working.conflicts if isinstance(item, Mapping)][:50],
                "version_context": versions,
                "report_facts": publication_facts(report),
                "scope": report.get("scope") or report.get("bounded_scope") or "",
                "suggested_components": report.get("suggested_components") or [],
                "severity_status": report.get("severity_status") or "",
                "classification_status": report.get("classification_status") or ("needs_triage" if classification == "unknown" else "provisional"),
                "assessment_note": report.get("assessment_note") or "",
                "bounded_fix_target": report.get("bounded_fix_target") or "",
                "acceptance": report.get("acceptance") or "",
                **{key: report[key] for key in (
                    "root_cause", "test_assessment", "executed_reproduction",
                    "assessment_confidence_reason", "classification_reason", "severity_reason",
                ) if key in report},
            },
            bug_description=self._report_text(report, "bug_description", self._report_text(report, "description")),
            reproduction_steps=("\n".join(f"{i}. {step}" for i, step in enumerate(report["reproduction_steps"], 1))
                                if isinstance(report.get("reproduction_steps"), (list, tuple))
                                else self._report_text(report, "reproduction_steps")),
            actual_behavior=self._report_text(report, "actual_behavior"),
            expected_behavior=self._report_text(report, "expected_behavior"),
            reported_expected_behavior=self._report_text(report, "expected_behavior"),
            canonical_expected_behavior=self._report_text(report, "expected_behavior"),
            environment=environment,
            project_version=self._report_text(report, "project_version", self._report_text(report, "zaofu_version")),
            zaofu_version=self._report_text(report, "zaofu_version", self._report_text(report, "project_version")),
            source_code_revision=str(versions.get("reported_source_revision") or versions.get("evaluated_source_revision") or ""),
            additional_context=self._report_text(report, "additional_context"),
            attachment_context=self._report_text(report, "attachment_context"),
            component=self._report_text(report, "component", "unknown"),
            impact_scope=self._report_text(report, "impact_scope", "unknown"),
            assessment_confidence=self._report_text(report, "assessment_confidence", "low"),
            recommended_next_action=self._report_text(report, "recommended_next_action"),
            runtime_status="unknown",
            evidence_status="completed",
            evidence_collection_mode=(str(report.get("evidence_collection_mode"))
                                      if report.get("evidence_collection_mode") in EVIDENCE_COLLECTION_MODES
                                      else "pending"),
            assessment_status="completed",
            reporter_context={"source": "portable_issue_session", "session_id": session.session_id},
        )
        # Portable `/issue` attachments are uploaded before the Session starts
        # and live in the controlled Kanban sidecar store.  Copy them into the
        # Draft-owned store so publication/retry can verify ownership and
        # publish a stable download artifact.
        attachment_info = {
            str(item.get("ref") or ""): item for item in report.get("attachments") or []
            if isinstance(item, Mapping)
        }
        for attachment in session.attachments:
            source = sidecar_path(self.state_dir, attachment.ref)
            content = source.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if not attachment.digest or attachment.digest.removeprefix("sha256:") != digest:
                raise ValueError(f"attachment digest mismatch: {attachment.name}")
            if Path(attachment.name).name != attachment.name or "\\" in attachment.name:
                raise ValueError("unsafe attachment filename")
            if content:
                descriptor = write_sidecar_bytes(
                    self.state_dir,
                    Path("artifacts") / "self-issues" / draft.draft_id / "attachments" / digest / attachment.name,
                    content,
                    kind="self_issue_user_attachment",
                    schema_version="self-issue-attachment.v1",
                    created_by="user",
                    access_scope={"external_disclosure": False},
                    retention={"class": "user_controlled"},
                    required=False,
                    content_type=attachment.media_type,
                    preview=attachment.name,
                )
                descriptor.update({
                    "source": "user",
                    "description": str(attachment_info.get(attachment.ref, {}).get("description") or f"User-provided attachment ({attachment.name})."),
                    "purpose": str(attachment_info.get(attachment.ref, {}).get("purpose") or "Provide user-supplied context for Issue assessment."),
                    "attachment_id": attachment.ref.rsplit("/", 1)[-1],
                    "filename": attachment.name,
                })
                draft.attachment_refs.append(descriptor)
        self._copy_agent_evidence_attachments(draft, working.evidences)
        self._copy_agent_process_attachment(draft, working.workflow, session.session_id)
        if existing_draft is not None:
            # Preserve the user-facing identity and local attachments while
            # invalidating all previews/confirmations tied to the old revision.
            draft = replace(
                draft,
                revision=existing_draft.revision + 1,
                created_at=existing_draft.created_at,
                attachment_refs=list({item["ref"]: item for item in [
                    *(item for item in existing_draft.attachment_refs if item.get("source") != "agent"),
                    *draft.attachment_refs,
                ]}.values()),
                published_attachments=list(existing_draft.published_attachments),
                attachment_context= draft.attachment_context or existing_draft.attachment_context,
                publication_state="draft",
            )
        draft.validate()
        if existing_draft is not None:
            service.intents.invalidate_unpublished(
                draft.draft_id, reason="Portable Issue Session report refreshed",
            )
            service.batches.invalidate_unpublished(
                draft.draft_id, reason="Portable Issue Session report refreshed",
            )
            service.attachments.invalidate_unprepared(
                draft.draft_id, reason="Portable Issue Session report refreshed",
            )
        service.drafts.save(draft)
        self._record_draft_version(draft, reason="generated")
        service._seed_field_metadata(draft, source="portable_issue_session", user_locked=False)
        # Draft identity is publication metadata, not a new analysis turn.
        # Do not revoke a running reanalysis when generating an older report.
        with self.store.transaction(session_id):
            current = self.store.load_session(session_id)
            self.store.save_session(
                replace(current, publication_draft_id=draft.draft_id, updated_at=_now()),
                expected_revision=current.revision,
            )
        return {
            "ok": True,
            "status": "publication_draft_ready",
            "session_id": session_id,
            "publication_draft_id": draft.draft_id,
            "draft": service._draft_view(draft),
            "report": current_report,
            "editor": self.editor(session_id),
        }

    def _copy_agent_evidence_attachments(self, draft: IssueDraft, evidences: Any) -> None:
        """Snapshot explicitly referenced, bounded Agent evidence for publication.

        Evidence refs are hints, not arbitrary filesystem upload permission. We
        accept existing files from the project checkout or /tmp, reject likely
        secret material, redact text/metadata, and bind every copy to a digest.
        Non-file refs (``e1`` and similar) remain in the report only.
        """
        existing = {str(item.get("sha256") or "") for item in draft.attachment_refs}
        roots = [self.project_root.resolve(), self.state_dir.resolve(), Path("/tmp")]
        # Nested harness fixtures can cite files from their containing checkout.
        for parent in self.project_root.resolve().parents:
            if (parent / ".git").exists():
                roots.append(parent)
                break
        total_bytes = 0
        warnings = []
        expanded_evidences = []
        for item in evidences or ():
            if not isinstance(item, Mapping):
                continue
            refs = []
            for key in ("ref", "artifact", "metadata_ref"):
                if item.get(key):
                    refs.append(str(item[key]))
            locations = item.get("locations")
            if isinstance(locations, (list, tuple)):
                refs.extend(str(location) for location in locations if str(location).strip())
            for ref in dict.fromkeys(refs):
                entry = {**item, "ref": ref}
                if ref != (item.get("ref") or item.get("artifact")):
                    # The primary artifact digest is not the digest of its
                    # metadata file or each source location.
                    entry.pop("sha256", None)
                    entry.pop("digest", None)
                expanded_evidences.append(entry)
        for item in expanded_evidences:
            if not isinstance(item, Mapping):
                continue
            raw_ref = str(item.get("ref") or "").strip()
            if not raw_ref:
                continue
            # Source refs commonly carry a line range after a colon.
            candidate = raw_ref
            if ":" in candidate and not candidate.startswith(("http://", "https://")):
                prefix, suffix = candidate.rsplit(":", 1)
                if suffix.replace("-", "").isdigit() and prefix:
                    candidate = prefix
            source = Path(candidate)
            if not source.is_absolute():
                source = (self.state_dir / source) if (self.state_dir / source).is_file() else (self.project_root / source)
            try:
                if not source.exists() and not Path(candidate).is_absolute() and "/" not in candidate:
                    continue  # An evidence identifier, not a file ref.
                if any(part.lower() in _SENSITIVE_EVIDENCE_NAMES or part.lower().startswith(".env") for part in source.parts):
                    raise ValueError("sensitive_file")
                source = source.resolve()
                if not any(source.is_relative_to(root) for root in roots):
                    raise ValueError("outside_evidence_roots")
                if any(part.lower() in _SENSITIVE_EVIDENCE_NAMES or part.lower().startswith(".env") for part in source.parts):
                    raise ValueError("sensitive_file")
                if not source.is_file() or source.stat().st_size <= 0 or source.stat().st_size > _AGENT_EVIDENCE_LIMIT:
                    raise ValueError("missing_empty_or_over_20mb")
                if total_bytes + source.stat().st_size > 100 * 1024 * 1024:
                    raise ValueError("aggregate_limit_100mb")
                suffix = source.suffix.lower()
                if suffix not in _AGENT_TEXT_SUFFIXES | _AGENT_BINARY_SUFFIXES:
                    raise ValueError("unsupported_file_type")
                with source.open("rb") as handle:
                    original = handle.read(_AGENT_EVIDENCE_LIMIT + 1)
                if len(original) > _AGENT_EVIDENCE_LIMIT:
                    raise ValueError("attachment_too_large")
                digest = hashlib.sha256(original).hexdigest()
                expected = str(item.get("sha256") or item.get("digest") or "").removeprefix("sha256:")
                if expected and expected != digest:
                    raise ValueError("evidence_digest_changed")
                if digest in existing:
                    continue
                content_type = mimetypes.guess_type(source.name)[0] or ("text/plain" if suffix in _AGENT_TEXT_SUFFIXES else "application/octet-stream")
                content = original
                redaction_applied = False
                if suffix in _AGENT_TEXT_SUFFIXES:
                    try:
                        text = original.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise ValueError("invalid_utf8") from exc
                    content_type = "text/plain"  # Source/log snapshots are downloads, not executable content.
                    content = _disclosable_evidence_text(text).encode("utf-8")
                    redaction_applied = content != original
                else:
                    try:
                        content, redaction_applied = sanitize_attachment_for_disclosure(
                            original, suffix=suffix, content_type=content_type,
                        )
                    except ValueError as exc:
                        raise ValueError("invalid_binary_artifact") from exc
                safe_name = source.name.replace("/", "_").replace("\\", "_")
                descriptor = write_sidecar_bytes(
                    self.state_dir,
                    Path("artifacts") / "self-issues" / draft.draft_id / "attachments" / hashlib.sha256(content).hexdigest() / f"agent-{safe_name}",
                    content,
                    kind="self_issue_agent_evidence",
                    schema_version="self-issue-attachment.v1",
                    created_by="agent",
                    access_scope={"external_disclosure": False},
                    retention={"class": "assessment_evidence"},
                    required=False,
                    content_type=content_type,
                    preview=safe_name,
                )
                descriptor.update({
                    "source": "agent", "capture_source": "assessment_agent",
                    "description": str(item.get("summary") or item.get("result") or f"Agent-collected evidence: {safe_name}"),
                    "purpose": str(item.get("purpose") or "Support the cited Issue finding; file snapshot captured when this Draft was generated."),
                    "filename": safe_name, "evidence_ref": raw_ref,
                    "redaction_applied": redaction_applied,
                })
                draft.attachment_refs.append(descriptor)
                existing.add(digest)
                total_bytes += len(content)
            except (OSError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else "artifact_unavailable"
                warning = {"ref": _portable_evidence_ref(raw_ref), "reason": reason}
                if warning not in warnings:
                    warnings.append(warning)
        if warnings:
            draft.analysis["attachment_warnings"] = warnings

    def _copy_agent_process_attachment(self, draft: IssueDraft, workflow: Mapping[str, Any], session_id: str) -> None:
        """Publish a bounded transcript of the read-only investigation steps.

        Individual evidence files often contain the final command output only.
        The activity sidecars retain the tool call and its complete result, so
        include one durable transcript in the Evidence comment as an audit
        trail without changing the report body or user attachments.
        """
        rounds = [*(workflow.get("reanalysis_history") or []), workflow]
        activities = [item for entry in rounds if isinstance(entry, Mapping)
                      for item in (entry.get("activity_log") or [])]
        lines: list[str] = ["ZaoFu Issue investigation process",
                            "Recorded tool activity in order; not model reasoning. Earlier analysis rounds may be superseded.", ""]
        included = 0
        total_bytes = 0
        result_root = (self.state_dir / "artifacts/self-issues/activity" / session_id).resolve()
        for item in activities:
            if not isinstance(item, Mapping) or str(item.get("kind") or "") not in {"tool_call", "tool_result"}:
                continue
            included += 1
            lines.append(f"[{item.get('at') or 'time unavailable'}] {item.get('phase') or 'investigation'}")
            lines.append(f"Activity: {item.get('label') or 'evidence check'}")
            if item.get("tool"):
                lines.append(f"Tool: {item['tool']}")
            for key in ("path", "pattern", "command", "target"):
                if item.get(key):
                    value = item[key]
                    if isinstance(value, (list, tuple)):
                        value = " ".join(str(part) for part in value)
                    lines.append(f"{key}: {value}")
            result_ref = item.get("result_ref")
            if isinstance(result_ref, Mapping) and result_ref.get("ref"):
                try:
                    path = sidecar_path(self.state_dir, str(result_ref["ref"]))
                    if not path.resolve().is_relative_to(result_root):
                        raise ValueError("result outside session activity store")
                    if path.stat().st_size > _AGENT_EVIDENCE_LIMIT - total_bytes:
                        lines.append("[Process transcript truncated: attachment size limit reached.]")
                        break
                    raw = path.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != result_ref.get("sha256"):
                        raise ValueError("activity result digest mismatch")
                    content = raw.decode("utf-8")
                except (OSError, ValueError) as exc:
                    content = f"[Full result unavailable: {type(exc).__name__}; only the recorded summary follows.]\n" + str(item.get("result_summary") or "")
            else:
                content = str(item.get("result_detail") or item.get("result_summary") or "")
                if item.get("kind") == "tool_result" and not item.get("result_detail"):
                    content = "[Full result not recorded; summary only.]\n" + content
            if content:
                lines.extend(["Result:", content.rstrip()])
            if item.get("upstream_truncated") or item.get("result_truncated"):
                lines.append("[The upstream tool marked this output as truncated.]")
            lines.append("")
            total_bytes = sum(len(line.encode("utf-8")) + 1 for line in lines)
            if total_bytes >= _AGENT_EVIDENCE_LIMIT:
                lines.append("[Process transcript truncated: attachment size limit reached.]")
                break
        if not included:
            return
        original = "\n".join(lines)
        content = _disclosable_evidence_text(original).encode("utf-8")
        if len(content) > _AGENT_EVIDENCE_LIMIT:
            content = content[:_AGENT_EVIDENCE_LIMIT - 100].decode("utf-8", errors="ignore").encode() + b"\n[Process transcript truncated: attachment size limit reached.]\n"
        digest = hashlib.sha256(content).hexdigest()
        if any(str(item.get("sha256") or "") == digest for item in draft.attachment_refs):
            return
        descriptor = write_sidecar_bytes(
            self.state_dir,
            Path("artifacts") / "self-issues" / draft.draft_id / "attachments" / digest / "investigation-process.log",
            content,
            kind="self_issue_agent_evidence",
            schema_version="self-issue-attachment.v1",
            created_by="issue-agent",
            access_scope={"external_disclosure": False},
            retention={"class": "assessment_evidence"},
            required=False,
            content_type="text/plain",
            preview="investigation-process.log",
        )
        descriptor.update({
            "source": "agent", "capture_source": "assessment_agent",
            "description": "Recorded investigation process: tool calls and results in execution order; missing or truncated output is marked.",
            "purpose": "Show how the cited evidence was collected; the report body keeps the concise findings.",
            "filename": "investigation-process.log", "redaction_applied": content != original.encode("utf-8"),
        })
        draft.attachment_refs.append(descriptor)

    def pause(self, session_id: str, *, reason: str = "用户中断分析", user: bool = True,
              resume_after_restart: bool = False) -> dict[str, Any]:
        with self.store.transaction(session_id):
            session = self.store.load_session(session_id)
            if resume_after_restart and session.status not in {SessionStatus.CREATED, SessionStatus.INVESTIGATING}:
                return self.get(session_id)
            if session.status == SessionStatus.PAUSED and not user:
                return self.get(session_id)
            if session.status not in {SessionStatus.INVESTIGATING, SessionStatus.CREATED, SessionStatus.FAILED, SessionStatus.PAUSED}:
                return self.get(session_id)
            working = self.store.load_working_set(session_id)
            self.store.save_session(replace(
                session, status=SessionStatus.PAUSED, revision=session.revision + 1, updated_at=_now(),
            ), expected_revision=session.revision)
            self.store.save_working_set(replace(working, workflow={
                **dict(working.workflow), "phase": "paused", "label": "分析已暂停",
                "activity": reason, "reason": reason,
                "interruption_source": "user" if user else "system", "updated_at": _now(),
                "resume_after_restart": resume_after_restart,
            }), expected_revision=working.revision)
        return self.get(session_id)

    def activity_result(self, session_id: str, activity_id: str) -> dict[str, Any]:
        working = self.store.load_working_set(session_id)
        rounds = list(working.workflow.get("reanalysis_history") or [])
        logs = list(working.workflow.get("activity_log") or [])
        for previous in rounds:
            logs.extend(previous.get("activity_log") or [])
        activity = next((item for item in logs if item.get("id") == activity_id), None)
        if activity is None:
            raise ValueError("activity not found in this Issue Session")
        descriptor = activity.get("result_ref")
        if not isinstance(descriptor, dict):
            return {"ok": True, "body": str(activity.get("result_detail") or activity.get("result_summary") or "")}
        content = sidecar_path(self.state_dir, descriptor["ref"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != descriptor["sha256"]:
            raise ValueError("activity result digest mismatch")
        return {"ok": True, "body": content.decode("utf-8"), "upstream_truncated": activity.get("upstream_truncated", False)}

    def _save_activity_result(self, session_id: str, content: str) -> dict[str, Any]:
        safe = str(redact_obj(content))
        digest = hashlib.sha256(safe.encode()).hexdigest()
        return write_sidecar_bytes(
            self.state_dir, Path("artifacts/self-issues/activity") / session_id / f"{digest}.txt",
            safe.encode(), kind="self_issue_tool_result", schema_version="issue-activity.v1",
            created_by="issue-agent", access_scope={"external_disclosure": False},
            retention={"class": "user_controlled"}, required=True, content_type="text/plain",
        )

    def _record_draft_version(self, draft, *, reason: str) -> None:
        body = draft.markdown_body
        if body is None:
            body = self._service()._publication_payload(draft, marker="", provider="github")[0]["body"]
            body = body.replace("<!-- zf-self-issue: -->", "").rstrip() + "\n"
        write_sidecar_bytes(
            self.state_dir,
            Path("artifacts/self-issues") / draft.draft_id / "versions" / f"{draft.revision:08d}.json",
            json.dumps({"version": draft.revision, "title": draft.title, "body": body,
                        "reason": reason, "created_at": _now()}, ensure_ascii=False).encode(),
            kind="self_issue_draft_version", schema_version="issue-draft-version.v1",
            created_by="user", access_scope={"external_disclosure": False},
            retention={"class": "user_controlled"}, required=True, content_type="application/json",
        )

    def editor(self, session_id: str) -> dict[str, Any] | None:
        session = self.store.load_session(session_id)
        if not session.publication_draft_id:
            return None
        service = self._service()
        draft = service.drafts.get(session.publication_draft_id)
        if draft is None:
            return None
        body = draft.markdown_body
        if body is None:
            body = service._publication_payload(draft, marker="", provider="github")[0]["body"]
            body = body.replace("<!-- zf-self-issue: -->", "").rstrip() + "\n"
        versions_root = self.state_dir / "artifacts/self-issues" / draft.draft_id / "versions"
        versions = [int(path.stem) for path in sorted(versions_root.glob("*.json")) if path.stem.isdigit()]
        return {"draft_id": draft.draft_id, "revision": draft.revision, "title": draft.title,
                "body": body, "versions": versions, "publication_state": draft.publication_state,
                "attachments": [{key: item.get(key) for key in ("filename", "source", "description", "purpose", "sha256", "byte_count", "redaction_applied")}
                                for item in draft.attachment_refs],
                "attachment_warnings": draft.analysis.get("attachment_warnings") or []}

    def draft_version(self, session_id: str, version: int) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        if version < 1 or not session.publication_draft_id:
            raise ValueError("invalid Draft version")
        ref = f"artifacts/self-issues/{session.publication_draft_id}/versions/{version:08d}.json"
        return {"ok": True, **json.loads(sidecar_path(self.state_dir, ref).read_text())}

    def save_editor(self, session_id: str, *, revision: int, title: str, body: str) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        if not session.publication_draft_id:
            raise ValueError("create a Draft first")
        if not title.strip() or not body.strip():
            raise ValueError("Draft title and body must not be empty")
        if len(body.encode()) > 200_000:
            raise ValueError("Draft body exceeds 200 KB")
        with locked_path(self.state_dir / "self-issues/workspace.lock"):
            service = self._service()
            result = service.update({"draft_id": session.publication_draft_id, "revision": revision,
                                     "title": title, "markdown_body": body})
            if not result.get("ok"):
                return result
            draft = service.drafts.get(session.publication_draft_id)
            if result["status"] != "draft_unchanged":
                self._record_draft_version(draft, reason="autosave")
        return {"ok": True, "editor": self.editor(session_id), "draft": result["draft"]}
