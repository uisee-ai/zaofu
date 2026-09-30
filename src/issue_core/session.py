"""Portable continuous Issue Agent session primitives.

This module is deliberately host-neutral.  It does not import ``zf`` and does
not assume a particular web server, event ledger, runtime or provider.
Hosts may persist these immutable-ish records in their own stores, while the
session runner keeps the lifecycle and late-result rules identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from contextlib import contextmanager, nullcontext
import fcntl
from hashlib import sha256
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence
from uuid import uuid4

if TYPE_CHECKING:
    from .report_snapshot import IssueReportSnapshot


SESSION_SCHEMA = "issue-session.v1"
WORKING_SET_SCHEMA = "issue-working-set.v1"
AGENT_RESULT_SCHEMA = "issue-agent-result.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(encoded.encode("utf-8")).hexdigest()


class SessionStatus(StrEnum):
    CREATED = "created"
    INVESTIGATING = "investigating"
    WAITING_FOR_USER = "waiting_for_user"
    READY_FOR_CONFIRMATION = "ready_for_confirmation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass(frozen=True)
class AttachmentRef:
    ref: str
    name: str
    media_type: str = "application/octet-stream"
    digest: str = ""
    disclosure: str = "private"

    def validate(self) -> None:
        if not self.ref or not self.name:
            raise ValueError("attachment requires ref and name")
        if self.disclosure not in {"private", "report", "public"}:
            raise ValueError("unsupported attachment disclosure")

    def to_dict(self) -> dict[str, str]:
        self.validate()
        return {
            "ref": self.ref,
            "name": self.name,
            "media_type": self.media_type,
            "digest": self.digest,
            "disclosure": self.disclosure,
        }


@dataclass(frozen=True)
class IssueSession:
    session_id: str
    raw_report: str
    attachments: tuple[AttachmentRef, ...] = ()
    target_binding: Mapping[str, Any] = field(default_factory=dict)
    source_binding: Mapping[str, Any] = field(default_factory=dict)
    status: SessionStatus = SessionStatus.CREATED
    revision: int = 1
    working_set_ref: str = ""
    active_run_id: str = ""
    user_question: str = ""
    user_replies: tuple[str, ...] = ()
    final_report_ref: str = ""
    publication_snapshot_ref: str = ""
    # Optional bridge into the host's existing Draft/publication workflow.
    # Keeping this on the portable Session makes the hand-off recoverable
    # after a browser refresh or process restart without coupling issue_core
    # to any particular forge implementation.
    publication_draft_id: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    schema_version: str = SESSION_SCHEMA

    def validate(self) -> None:
        if self.schema_version != SESSION_SCHEMA:
            raise ValueError("unsupported Issue Session schema")
        if not self.session_id or not self.raw_report.strip():
            raise ValueError("Issue Session requires a non-empty raw report")
        if self.revision < 1:
            raise ValueError("Issue Session revision must be positive")
        for attachment in self.attachments:
            attachment.validate()
        if self.status == SessionStatus.COMPLETED and not self.final_report_ref:
            raise ValueError("completed Session requires final_report_ref")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "raw_report": self.raw_report,
            "attachments": [item.to_dict() for item in self.attachments],
            "target_binding": dict(self.target_binding),
            "source_binding": dict(self.source_binding),
            "status": self.status.value,
            "revision": self.revision,
            "working_set_ref": self.working_set_ref,
            "active_run_id": self.active_run_id,
            "user_question": self.user_question,
            "user_replies": list(self.user_replies),
            "final_report_ref": self.final_report_ref,
            "publication_snapshot_ref": self.publication_snapshot_ref,
            "publication_draft_id": self.publication_draft_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class IssueWorkingSet:
    session_id: str
    revision: int
    fields: Mapping[str, Any] = field(default_factory=dict)
    field_provenance: Mapping[str, Any] = field(default_factory=dict)
    claims: tuple[Mapping[str, Any], ...] = ()
    evidences: tuple[Mapping[str, Any], ...] = ()
    gaps: tuple[str, ...] = ()
    hypotheses: tuple[Mapping[str, Any], ...] = ()
    conflicts: tuple[Mapping[str, Any], ...] = ()
    report: Mapping[str, Any] = field(default_factory=dict)
    workflow: Mapping[str, Any] = field(default_factory=dict)
    digest: str = ""
    schema_version: str = WORKING_SET_SCHEMA

    def with_digest(self) -> "IssueWorkingSet":
        payload = {
            "session_id": self.session_id,
            "revision": self.revision,
            "fields": dict(self.fields),
            "field_provenance": dict(self.field_provenance),
            "claims": list(self.claims),
            "evidences": list(self.evidences),
            "gaps": list(self.gaps),
            "hypotheses": list(self.hypotheses),
            "conflicts": list(self.conflicts),
            "report": dict(self.report),
        }
        if self.workflow:
            payload["workflow"] = dict(self.workflow)
        return replace(self, digest=stable_digest(payload))

    def validate(self) -> None:
        if self.schema_version != WORKING_SET_SCHEMA:
            raise ValueError("unsupported Working Set schema")
        if not self.session_id or self.revision < 1:
            raise ValueError("Working Set requires session and positive revision")
        computed = self.with_digest().digest
        if self.digest and self.digest != computed:
            raise ValueError("Working Set digest mismatch")

    def to_dict(self) -> dict[str, Any]:
        value = self.with_digest()
        value.validate()
        return {
            "schema_version": value.schema_version,
            "session_id": value.session_id,
            "revision": value.revision,
            "fields": dict(value.fields),
            "field_provenance": dict(value.field_provenance),
            "claims": list(value.claims),
            "evidences": list(value.evidences),
            "gaps": list(value.gaps),
            "hypotheses": list(value.hypotheses),
            "conflicts": list(value.conflicts),
            "report": dict(value.report),
            "workflow": dict(value.workflow),
            "digest": value.digest,
        }


@dataclass(frozen=True)
class AgentResult:
    status: str
    working_set: IssueWorkingSet
    question: str = ""
    reason: str = ""
    validation: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = AGENT_RESULT_SCHEMA

    def validate(self) -> None:
        if self.schema_version != AGENT_RESULT_SCHEMA:
            raise ValueError("unsupported Agent result schema")
        if self.status not in {"continue", "ask_user", "ready", "failed"}:
            raise ValueError("unsupported Agent result status")
        self.working_set.validate()
        if not isinstance(self.validation, Mapping):
            raise ValueError("Agent result validation must be an object")
        if self.status == "ask_user" and not self.question.strip():
            raise ValueError("ask_user result requires a question")
        if self.status == "ready" and not self.working_set.report:
            raise ValueError("ready result requires a composed Issue Report")


class IssueAgent(Protocol):
    """One continuous Agent contract; internal phases are implementation details."""

    def run(
        self,
        *,
        session: IssueSession,
        working_set: IssueWorkingSet,
        tools: "InvestigationTools",
    ) -> AgentResult: ...


class InvestigationTools(Protocol):
    """Read-only, host-neutral investigation capabilities."""

    def read_text(self, path: str) -> str: ...

    def search(self, pattern: str, *, path: str = ".") -> Sequence[str]: ...

    def run_read_only(self, command: Sequence[str], *, timeout_seconds: int) -> Mapping[str, Any]: ...


class IssueSessionStore(Protocol):
    def load_session(self, session_id: str) -> IssueSession: ...

    def save_session(self, session: IssueSession, *, expected_revision: int) -> IssueSession: ...

    def load_working_set(self, session_id: str) -> IssueWorkingSet: ...

    def save_working_set(self, working_set: IssueWorkingSet, *, expected_revision: int) -> IssueWorkingSet: ...

    def append_user_reply(self, session_id: str, reply: str, *, expected_revision: int) -> IssueSession: ...

    def cancel_session(self, session_id: str, *, expected_revision: int) -> IssueSession: ...

    def save_publication_snapshot(self, snapshot: "IssueReportSnapshot") -> "IssueReportSnapshot": ...

    def load_publication_snapshot(self, session_id: str) -> "IssueReportSnapshot": ...

    def confirm_session(
        self,
        session_id: str,
        *,
        expected_revision: int,
        report_ref: str = "",
        snapshot_ref: str = "",
    ) -> IssueSession: ...


class InMemoryIssueSessionStore:
    """Deterministic store used by portable tests and lightweight hosts."""

    def __init__(self) -> None:
        self._sessions: dict[str, IssueSession] = {}
        self._working_sets: dict[str, IssueWorkingSet] = {}
        self._publication_snapshots: dict[str, "IssueReportSnapshot"] = {}

    def create(
        self,
        raw_report: str,
        *,
        attachments: Sequence[AttachmentRef] = (),
        target_binding: Mapping[str, Any] | None = None,
        source_binding: Mapping[str, Any] | None = None,
    ) -> IssueSession:
        session_id = f"issue-session-{uuid4().hex[:12]}"
        session = IssueSession(
            session_id=session_id,
            raw_report=raw_report,
            attachments=tuple(attachments),
            target_binding=dict(target_binding or {}),
            source_binding=dict(source_binding or {}),
        )
        session.validate()
        working = IssueWorkingSet(session_id=session_id, revision=1).with_digest()
        self._sessions[session_id] = session
        self._working_sets[session_id] = working
        return session

    def load_session(self, session_id: str) -> IssueSession:
        return self._sessions[session_id]

    def save_session(self, session: IssueSession, *, expected_revision: int) -> IssueSession:
        current = self.load_session(session.session_id)
        if current.revision != expected_revision:
            raise ValueError("Issue Session revision conflict")
        session.validate()
        self._sessions[session.session_id] = session
        return session

    def load_working_set(self, session_id: str) -> IssueWorkingSet:
        return self._working_sets[session_id]

    def save_working_set(self, working_set: IssueWorkingSet, *, expected_revision: int) -> IssueWorkingSet:
        current = self.load_working_set(working_set.session_id)
        if current.revision != expected_revision:
            raise ValueError("Working Set revision conflict")
        value = working_set.with_digest()
        value.validate()
        self._working_sets[working_set.session_id] = value
        return value

    def append_user_reply(self, session_id: str, reply: str, *, expected_revision: int) -> IssueSession:
        current = self.load_session(session_id)
        if current.revision != expected_revision:
            raise ValueError("Issue Session revision conflict")
        if not str(reply).strip():
            raise ValueError("user reply must not be empty")
        updated = replace(
            current,
            revision=current.revision + 1,
            user_replies=(*current.user_replies, str(reply).strip()),
            user_question="",
            status=SessionStatus.INVESTIGATING,
            updated_at=_now(),
        )
        updated.validate()
        self._sessions[session_id] = updated
        return updated

    def cancel_session(self, session_id: str, *, expected_revision: int) -> IssueSession:
        current = self.load_session(session_id)
        if current.revision != expected_revision:
            raise ValueError("Issue Session revision conflict")
        updated = replace(current, revision=current.revision + 1, status=SessionStatus.CANCELLED, updated_at=_now())
        self._sessions[session_id] = updated
        return updated

    def save_publication_snapshot(self, snapshot: "IssueReportSnapshot") -> "IssueReportSnapshot":
        snapshot.validate()
        self._publication_snapshots[snapshot.session_id] = snapshot
        return snapshot

    def load_publication_snapshot(self, session_id: str) -> "IssueReportSnapshot":
        return self._publication_snapshots[session_id]

    def confirm_session(
        self,
        session_id: str,
        *,
        expected_revision: int,
        report_ref: str = "",
        snapshot_ref: str = "",
    ) -> IssueSession:
        current = self.load_session(session_id)
        if current.revision != expected_revision:
            raise ValueError("Issue Session revision conflict")
        if current.status != SessionStatus.READY_FOR_CONFIRMATION:
            raise ValueError("Issue Session is not ready for confirmation")
        if not report_ref and not snapshot_ref:
            raise ValueError("confirmation requires a report or snapshot reference")
        updated = replace(
            current,
            revision=current.revision + 1,
            status=SessionStatus.COMPLETED,
            final_report_ref=report_ref or snapshot_ref,
            publication_snapshot_ref=snapshot_ref,
            updated_at=_now(),
        )
        updated.validate()
        self._sessions[session_id] = updated
        return updated


class JsonIssueSessionStore:
    """Atomic JSON persistence for a portable host or standalone worker."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        self._validate_id(session_id)
        return self.root / f"{session_id}.session.json"

    def _working_path(self, session_id: str) -> Path:
        self._validate_id(session_id)
        return self.root / f"{session_id}.working.json"

    def _snapshot_path(self, session_id: str) -> Path:
        self._validate_id(session_id)
        return self.root / f"{session_id}.publication.json"

    @staticmethod
    def _validate_id(session_id: str) -> None:
        if not session_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in session_id):
            raise ValueError("invalid Issue Session id")

    @contextmanager
    def transaction(self, session_id: str):
        """Serialize a checkpoint commit with pause/cancel and progress writers."""
        self._validate_id(session_id)
        with (self.root / f"{session_id}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _write(path: Path, value: Mapping[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    def create(
        self,
        raw_report: str,
        *,
        attachments: Sequence[AttachmentRef] = (),
        target_binding: Mapping[str, Any] | None = None,
        source_binding: Mapping[str, Any] | None = None,
    ) -> IssueSession:
        session_id = f"issue-session-{uuid4().hex[:12]}"
        session = IssueSession(
            session_id=session_id,
            raw_report=raw_report,
            attachments=tuple(attachments),
            target_binding=dict(target_binding or {}),
            source_binding=dict(source_binding or {}),
        )
        session.validate()
        self._write(self._session_path(session_id), session.to_dict())
        self._write(self._working_path(session_id), IssueWorkingSet(session_id=session_id, revision=1).to_dict())
        return session

    def load_session(self, session_id: str) -> IssueSession:
        value = json.loads(self._session_path(session_id).read_text(encoding="utf-8"))
        session = IssueSession(
            session_id=str(value["session_id"]),
            raw_report=str(value["raw_report"]),
            attachments=tuple(AttachmentRef(**item) for item in value.get("attachments", [])),
            target_binding=dict(value.get("target_binding") or {}),
            source_binding=dict(value.get("source_binding") or {}),
            status=SessionStatus(str(value.get("status") or SessionStatus.CREATED.value)),
            revision=int(value.get("revision") or 1),
            working_set_ref=str(value.get("working_set_ref") or ""),
            active_run_id=str(value.get("active_run_id") or ""),
            user_question=str(value.get("user_question") or ""),
            user_replies=tuple(str(item) for item in value.get("user_replies", [])),
            final_report_ref=str(value.get("final_report_ref") or ""),
            publication_snapshot_ref=str(value.get("publication_snapshot_ref") or ""),
            publication_draft_id=str(value.get("publication_draft_id") or ""),
            created_at=str(value.get("created_at") or _now()),
            updated_at=str(value.get("updated_at") or _now()),
        )
        session.validate()
        return session

    def save_session(self, session: IssueSession, *, expected_revision: int) -> IssueSession:
        current = self.load_session(session.session_id)
        if current.revision != expected_revision:
            raise ValueError("Issue Session revision conflict")
        session.validate()
        self._write(self._session_path(session.session_id), session.to_dict())
        return session

    def load_working_set(self, session_id: str) -> IssueWorkingSet:
        value = json.loads(self._working_path(session_id).read_text(encoding="utf-8"))
        working = IssueWorkingSet(
            session_id=str(value["session_id"]),
            revision=int(value.get("revision") or 1),
            fields=dict(value.get("fields") or {}),
            field_provenance=dict(value.get("field_provenance") or {}),
            claims=tuple(value.get("claims") or ()),
            evidences=tuple(value.get("evidences") or ()),
            gaps=tuple(str(item) for item in value.get("gaps", [])),
            hypotheses=tuple(value.get("hypotheses") or ()),
            conflicts=tuple(value.get("conflicts") or ()),
            report=dict(value.get("report") or {}),
            workflow=dict(value.get("workflow") or {}),
            digest=str(value.get("digest") or ""),
        )
        working.validate()
        return working

    def save_working_set(self, working_set: IssueWorkingSet, *, expected_revision: int) -> IssueWorkingSet:
        current = self.load_working_set(working_set.session_id)
        if current.revision != expected_revision:
            raise ValueError("Working Set revision conflict")
        value = working_set.with_digest()
        value.validate()
        self._write(self._working_path(working_set.session_id), value.to_dict())
        return value

    def append_user_reply(self, session_id: str, reply: str, *, expected_revision: int) -> IssueSession:
        current = self.load_session(session_id)
        if current.revision != expected_revision:
            raise ValueError("Issue Session revision conflict")
        if not str(reply).strip():
            raise ValueError("user reply must not be empty")
        updated = replace(
            current,
            revision=current.revision + 1,
            user_replies=(*current.user_replies, str(reply).strip()),
            user_question="",
            status=SessionStatus.INVESTIGATING,
            updated_at=_now(),
        )
        return self.save_session(updated, expected_revision=current.revision)

    def cancel_session(self, session_id: str, *, expected_revision: int) -> IssueSession:
        current = self.load_session(session_id)
        if current.revision != expected_revision:
            raise ValueError("Issue Session revision conflict")
        return self.save_session(
            replace(current, revision=current.revision + 1, status=SessionStatus.CANCELLED, updated_at=_now()),
            expected_revision=current.revision,
        )

    def save_publication_snapshot(self, snapshot: "IssueReportSnapshot") -> "IssueReportSnapshot":
        snapshot.validate()
        self._write(
            self._snapshot_path(snapshot.session_id),
            snapshot.to_dict(),
        )
        return snapshot

    def load_publication_snapshot(self, session_id: str) -> "IssueReportSnapshot":
        from .report_snapshot import IssueReportSnapshot

        value = json.loads(self._snapshot_path(session_id).read_text(encoding="utf-8"))
        return IssueReportSnapshot.from_dict(value)

    def confirm_session(
        self,
        session_id: str,
        *,
        expected_revision: int,
        report_ref: str = "",
        snapshot_ref: str = "",
    ) -> IssueSession:
        current = self.load_session(session_id)
        if current.revision != expected_revision:
            raise ValueError("Issue Session revision conflict")
        if current.status != SessionStatus.READY_FOR_CONFIRMATION:
            raise ValueError("Issue Session is not ready for confirmation")
        if not report_ref and not snapshot_ref:
            raise ValueError("confirmation requires a report or snapshot reference")
        return self.save_session(
            replace(
                current,
                revision=current.revision + 1,
                status=SessionStatus.COMPLETED,
                final_report_ref=report_ref or snapshot_ref,
                publication_snapshot_ref=snapshot_ref,
                updated_at=_now(),
            ),
            expected_revision=current.revision,
        )


class IssueSessionRunner:
    """Run one turn of the same Issue Agent and persist its state atomically."""

    def __init__(self, store: IssueSessionStore) -> None:
        self.store = store

    def run_once(self, session_id: str, agent: IssueAgent, tools: InvestigationTools) -> AgentResult:
        session = self.store.load_session(session_id)
        working_set = self.store.load_working_set(session_id)
        if session.status in {SessionStatus.CANCELLED, SessionStatus.COMPLETED, SessionStatus.PAUSED}:
            raise ValueError(f"cannot run terminal Issue Session: {session.status.value}")
        result = agent.run(session=session, working_set=working_set, tools=tools)
        # Agents may return a structural update by replacing fields on the
        # previous Working Set, which necessarily invalidates its old digest.
        # Normalize that host-neutral result before validating/persisting it.
        result = replace(result, working_set=result.working_set.with_digest())
        result.validate()
        # The workflow owns recovery routing. A model may diagnose a failed
        # validation, but it cannot bypass the investigation/user/re-synthesis
        # gates by returning a superficially ready report.
        validation = dict(result.validation or {})
        failures = validation.get("failures") or []
        checks = validation.get("checks")
        if isinstance(checks, list):
            normalized_checks = []
            for raw_check in checks:
                if not isinstance(raw_check, Mapping):
                    continue
                check = dict(raw_check)
                name = str(check.get("name") or "check")
                status = str(check.get("status") or "not_run").strip().lower()
                check["status"] = status
                refs = check.get("evidence_refs")
                check["evidence_refs"] = [str(item) for item in refs] if isinstance(refs, (list, tuple)) else []
                check.setdefault("meaning", {
                    "repository_revision": "确认调查使用的 Git revision",
                    "packaging_source_presence": "确认打包配置或源码是否存在",
                    "independent_reproduction": "尝试在安全边界内复现用户报告",
                    "zf_instructions": "确认项目操作约束可用",
                }.get(name, "确认一项 Issue 证据"))
                if status == "not_run" and not str(check.get("reason") or "").strip():
                    check["reason"] = "前置证据或可执行复现工件尚未获得"
                check.setdefault("next_action", {
                    "passed": "保留证据并继续下一项检查",
                    "failed": "回到 Investigation 补齐或解释该证据缺口",
                    "not_run": "先获取前置工件，再执行该检查",
                }.get(status, "由工作流决定下一步"))
                next_action = str(check.get("next_action") or "")
                investigation_words = (
                    "获取", "定位", "搜索", "重做", "补齐", "切换", "checkout", "source",
                )
                if (
                    status == "not_run"
                    or str(check.get("recoverable_by") or "") == "investigation"
                    or any(word.casefold() in next_action.casefold() for word in investigation_words)
                ):
                    check["next_action_scope"] = "investigation"
                elif status == "failed":
                    check["next_action_scope"] = "investigation"
                elif status == "passed":
                    check["next_action_scope"] = "triage_or_fix"
                else:
                    check["next_action_scope"] = "investigation"
                normalized_checks.append(check)
            validation["checks"] = normalized_checks
            if not str(validation.get("status") or "").strip():
                validation["status"] = (
                    "failed" if any(item.get("status") == "failed" for item in normalized_checks)
                    else "partial" if any(item.get("status") == "not_run" for item in normalized_checks)
                    else "passed"
                )
        workflow = dict(result.working_set.workflow)
        if validation:
            workflow["validation"] = validation
        validation_status = str(validation.get("status") or "").strip().lower()
        if result.status == "ready" and validation_status in {"fail", "partial", "failed"}:
            failures = validation.get("failures") or []
            routes = {
                str(item.get("recoverable_by") or "")
                for item in failures if isinstance(item, Mapping)
            }
            if not routes:
                # A partial validation without explicit failures is still not
                # a confirmation gate.  Treat it as an evidence gap so the
                # continuous agent gets another bounded investigation turn.
                routes = {"investigation"}
            if "ask_user" in routes:
                questions = [
                    str(item.get("message") or item.get("field") or "")
                    for item in failures
                    if isinstance(item, Mapping) and item.get("recoverable_by") == "ask_user"
                ]
                result = replace(result, status="ask_user", question="请补充以下信息：" + "；".join(questions))
                workflow["phase"] = "ask_user"
                workflow["route"] = "ask_user"
            elif routes & {"investigation", "reconcile", "re_synthesize"}:
                result = replace(
                    result,
                    status="continue",
                    reason="validation_failed:" + ",".join(sorted(routes)),
                )
                route = "reconcile" if "reconcile" in routes else (
                    "investigation" if "investigation" in routes else "re_synthesize"
                )
                workflow["phase"] = route
                workflow["route"] = route
        elif result.status == "ask_user":
            workflow["phase"] = "ask_user"
            workflow["route"] = "ask_user"
        elif result.status == "ready":
            workflow["phase"] = "validate"
            workflow["route"] = "confirm"
        else:
            workflow["phase"] = "investigate"
        if result.reason:
            workflow["reason"] = result.reason
        result = replace(result, working_set=replace(result.working_set, workflow=workflow))
        next_status = {
            "continue": SessionStatus.INVESTIGATING,
            "ask_user": SessionStatus.WAITING_FOR_USER,
            "ready": SessionStatus.READY_FOR_CONFIRMATION,
            "failed": SessionStatus.FAILED,
        }[result.status]
        transaction = getattr(self.store, "transaction", None)
        with transaction(session_id) if transaction else nullcontext():
            current = self.store.load_session(session_id)
            if current.revision != session.revision or current.status in {SessionStatus.PAUSED, SessionStatus.CANCELLED}:
                raise ValueError("Issue Session turn superseded; late result discarded")
            latest = self.store.load_working_set(session_id)
            # Progress is written while the provider runs. Never overwrite its
            # complete activity log with the pre-turn Working Set snapshot.
            workflow = {**dict(latest.workflow), **workflow}
            for key in ("activity_log", "history", "heartbeat_at", "reanalysis_history", "reanalysis_request"):
                if key in latest.workflow:
                    workflow[key] = latest.workflow[key]
            next_revision = max(session.revision, latest.revision) + 1
            saved_working = self.store.save_working_set(
                replace(result.working_set, revision=next_revision, workflow=workflow),
                expected_revision=latest.revision,
            )
            self.store.save_session(
                replace(current, status=next_status, revision=next_revision,
                        working_set_ref=saved_working.digest, user_question=result.question, updated_at=_now()),
                expected_revision=session.revision,
            )
        return replace(result, working_set=saved_working)

    def answer_user(
        self,
        session_id: str,
        reply: str,
        agent: IssueAgent,
        tools: InvestigationTools,
    ) -> AgentResult:
        session = self.store.load_session(session_id)
        if session.status != SessionStatus.WAITING_FOR_USER:
            raise ValueError("Issue Session is not waiting for user input")
        self.store.append_user_reply(session_id, reply, expected_revision=session.revision)
        return self.run_once(session_id, agent, tools)

    def cancel(self, session_id: str) -> IssueSession:
        session = self.store.load_session(session_id)
        return self.store.cancel_session(session_id, expected_revision=session.revision)

    def confirm(
        self,
        session_id: str,
        *,
        report_ref: str = "",
        confirmed_by: str = "user",
        provider: str = "",
        target: str = "",
        disclosure_policy: str = "handoff_private",
    ) -> IssueSession:
        session = self.store.load_session(session_id)
        if session.status != SessionStatus.READY_FOR_CONFIRMATION:
            raise ValueError(f"Issue Session is not ready for confirmation: {session.status.value}")
        working_set = self.store.load_working_set(session_id)
        from .report_snapshot import IssueReportSnapshot

        snapshot = IssueReportSnapshot.from_session(
            session,
            working_set,
            confirmed_by=confirmed_by,
            confirmed_at=_now(),
            provider=provider or str(session.target_binding.get("provider") or ""),
            target=target or str(session.target_binding.get("project") or session.target_binding.get("target") or ""),
            disclosure_policy=disclosure_policy,
        )
        if report_ref and report_ref not in {working_set.digest, snapshot.digest}:
            raise ValueError("report reference does not match the confirmed Working Set")
        self.store.save_publication_snapshot(snapshot)
        return self.store.confirm_session(
            session_id,
            expected_revision=session.revision,
            report_ref=report_ref or snapshot.digest,
            snapshot_ref=snapshot.digest,
        )


__all__ = [
    "AGENT_RESULT_SCHEMA",
    "AttachmentRef",
    "AgentResult",
    "InvestigationTools",
    "InMemoryIssueSessionStore",
    "JsonIssueSessionStore",
    "IssueAgent",
    "IssueSession",
    "IssueSessionStore",
    "IssueSessionRunner",
    "IssueWorkingSet",
    "SESSION_SCHEMA",
    "SessionStatus",
    "WORKING_SET_SCHEMA",
    "stable_digest",
]
