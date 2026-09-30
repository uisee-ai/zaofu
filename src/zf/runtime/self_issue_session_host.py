"""Optional ZaoFu host adapter for the portable continuous Issue Agent.

The adapter owns only persistence/projection at the host edge.  The actual
Session, Worker, tools and Agent contracts live in ``issue_core`` and do not
import this module or any other ``zf`` package.
"""

from __future__ import annotations

from pathlib import Path
import json
import re
from dataclasses import replace
from uuid import uuid4
from zf.runtime.self_issue_workspace import IssueWorkspaceMixin
from zf.core.security.redaction import redact_obj
from typing import Any, Callable, Mapping, Sequence

from issue_core import (
    AgentResult,
    AttachmentRef,
    ContinuousIssueAgent,
    IssueAgent,
    JsonIssueSessionStore,
    ReadOnlyInvestigationTools,
    SessionStatus,
    IssueSessionWorker,
    IssueSession,
)
from issue_core.session import _now
from zf.web.headless_agent import KanbanHeadlessAgent, canonical_headless_backend
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.self_issue.models import CLASSIFICATIONS
from zf.core.self_issue.report_projection import stage_report_contract
from zf.runtime.self_issue_service import SelfIssueService
from zf.runtime.sidecar_refs import sidecar_path
from zf.runtime import self_issue_session_execution as execution


def _placeholder(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return (
        not text
        or text in {"unknown", "not provided", "not provided."}
        or text.startswith("未提供")
        or "user did not provide" in text
    )


def _mapping_or_fallback(value: Any, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Coerce model sections without letting malformed JSON crash the Session."""
    if isinstance(value, Mapping):
        return dict(value)
    return dict(fallback or {})


def _mapping_sequence(value: Any, fallback: Sequence[Mapping[str, Any]] = ()) -> tuple[Mapping[str, Any], ...]:
    """Normalize model arrays so one malformed string cannot crash projection."""
    if not isinstance(value, (list, tuple)):
        return tuple(fallback)
    normalized: list[Mapping[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            normalized.append(dict(item))
        elif item not in (None, ""):
            normalized.append({"kind": "agent_output", "value": str(item)})
    return tuple(normalized)


_PHASE_LABELS = {
    "queued": "已接收，准备开始",
    "understanding": "正在理解问题和项目上下文",
    "investigation": "正在调查代码、测试、日志和证据",
    "gap_analysis": "正在检查 Issue 是否缺少关键信息",
    "ask_user": "等待你补充用户意图",
    "reconcile": "正在消解冲突证据",
    "synthesize": "正在整理完整 Issue 报告",
    "validate": "正在验证报告完整性和证据一致性",
    "ready_for_confirmation": "报告已生成，等待确认",
    "failed": "处理失败，等待恢复",
}


_SESSION_SYSTEM_PROMPT = """You are the single continuous Issue Agent for /issue. Work in one
session: understand the user's report semantically, investigate the authorized project with
read-only tools, inspect code/tests/logs/browser evidence when available, reconcile conflicts,
then synthesize and validate an Issue body. The assessment turn has full dangerous tool permission
for the authorized workspace and related checkouts, so it may inspect any reachable source, wheel,
logs, tests and browser artifacts needed to close evidence gaps. Do not use deterministic field extraction and do not
ask for facts you can obtain yourself. Ask only for unresolved user intent, and batch all questions.
Before declaring ready, collect inexpensive environment and repository facts yourself when the
authorized tools can provide them: operating system, architecture, available Python version,
repository revision, relevant packaging/configuration files, tests and logs. A Git revision is not
a project/package version. Do not emit "unknown" or "not provided" for facts a safe command can
determine. Do not expose internal validation check trees in the public Issue body.
For the fixed Environment block, collect and return report.environment with os, version,
arch and python (Operating system, OS version, Architecture, Python version), plus
report.project_version and report.inspected_source_revision (Project version, Project source commit).
This block describes the investigation environment and the inspected project source, not an
unverified historical affected host or a wheel build commit. Obtain OS/OS version/architecture
from safe platform commands (for example uname -s/-r/-m), Python from the investigation
interpreter, project version from the inspected project metadata, and its source commit from
git -C <inspected project> rev-parse HEAD. Keep the original affected environment and wheel
build provenance separate in evidence. Do not substitute the Issue workspace revision for
the inspected project's revision. Do not put this inspected commit in source_code_revision or
wheel_reported_source_revision unless the reported artifact's build provenance establishes it.
Explicitly include report.impact_scope: which users/environments/functions are affected and
the boundaries of the impact, supported by collected evidence. Do not replace it with unknown,
not collected, or a user-question when code, artifacts or bounded reproduction can establish it.
Before ready, check these six Environment values and impact_scope and continue collecting any
obtainable missing facts; if a fact is genuinely inaccessible, record the concrete evidence gap
rather than inventing a value. Do not add presentation fields or change the report's template.
Return JSON only with status (continue|ask_user|ready|failed), fields, field_provenance, claims,
evidences, gaps, hypotheses, conflicts, report, validation, question and reason. Validation failures
must be routable with type, field, message and recoverable_by (investigation|ask_user|reconcile|
re_synthesize). For every report, perform these bounded checks before ready: identify the exact Git
revision with a read-only command; search for packaging/source/CLI locations; attempt a minimal safe
reproduction (for example an import, --help, or existing clean-install test) when the required
artifact exists; if it cannot run, return status=not_run plus a concrete reason and a recoverable
investigation gap. A missing source checkout is itself evidence, not a reason to skip code-location
search. Record code locations as evidence only when actually found, and never invent a path.
In report, explicitly include severity (P0-P3, or needs_assessment with a concrete reason),
severity_reason and severity_status=provisional. Never omit severity or invent it from a store default.
Include classification (for example bug, configuration, performance,
security, or unknown with a reason), reproduction_status, assessment_confidence, and
evidence_collection_mode. These are semantic conclusions you must justify with evidence;
the renderer must not invent them. static_only means only static inspection was done;
running a bounded reproduction or relevant test must be distinguished from merely reading
an old reproduction log. State the executed command, outcome, artifact and evidence refs.
Keep the affected/reported environment separate from the investigator environment, and
the Issue workspace revision separate from the inspected source checkout and wheel's
reported source revision. Inspect available artifacts for version/build provenance; record
reproduction_artifact_source and reproduction_version when established. A wheel filename
or the current checkout HEAD alone does not prove its build commit. Do not invent future
fix, candidate PR or merge revisions at this stage. Return reproduction_steps as an array.
Each claim must include claim, confidence, status and support. Each support entry must include
evidence_ref and conclusion explaining what that evidence supports for this claim, not just an ID.
Each hypothesis must include hypothesis, confidence, status and support in that order.
Use conflict, support and resolution for conflicts. Do the same evidence linking for validation
checks. Put conflict subject before resolution.
Claim IDs are optional internal bookkeeping, not required public report fields. Preserve limitations.
Evidence IDs are internal links only: keep them in structured evidence_ref fields, never embed
E1/E2-style labels in claim prose, conclusions, reasons or other public-facing text. Supply the
actual evidence file/location and its conclusion so the host can present #59-style source support.
Evidence records must reference files explicitly using ref, locations, artifact or metadata_ref;
capture actual command output as a safe evidence file and reference it. Each new execution log
must include the exact command (and script input when supplied on stdin), working directory,
ordered stdout/stderr output, and exit status; record start/end times when available. For tests,
prefer verbose per-test results (for example pytest -vv --no-cov) over summary-only -q output.
Keep setup, reproduction, and control steps in execution order, not just the final verdict.
Do not invent missing output or rerun historical tests just to fill a log. Mark unavailable or
truncated process output explicitly. A command string alone is not an uploaded log. Do not
present old attached logs as a new execution.
Inspect issue-test-assets and attached logs/metadata before marking independent_reproduction=not_run.
Artifact-backed reproduction is valid bounded evidence even when the executable package is absent;
use not_run only when neither an executable artifact nor a reproduction log exists.
For every validation check include name, status, evidence_refs, meaning, reason, and next_action.
For independent_reproduction, never use not_run without explaining which prerequisite is absent.
For this /issue stage, external_issue_disposition must remain needs_triage or
unverified_candidate; only the separate triage gate may produce ready_to_fix. Keep investigation_gaps
(missing source/evidence that this Agent could still investigate) separate from downstream_verification_tasks
(work to perform after a fix). Never claim evidence that you did not obtain. Never edit product
source or canonical runtime state. Evidence files may only be written in designated scratch/artifact
locations and must not overwrite user data."""


class HeadlessIssueSessionAgent:
    """Host adapter that keeps understanding, investigation and synthesis in one model thread."""

    def __init__(self, *, state_dir: Path, project_root: Path, backend: str,
                 progress: Callable[..., None] | None = None) -> None:
        self.state_dir = Path(state_dir)
        self.agent = KanbanHeadlessAgent(
            state_dir=Path(state_dir), project_root=Path(project_root), timeout_s=180,
        )
        self.backend = canonical_headless_backend(backend)
        self.progress = progress

    def _report(self, phase: str, activity: str, summary: str = "", detail: Mapping[str, Any] | None = None) -> None:
        if self.progress is not None:
            self.progress(phase, activity, summary, dict(detail or {}))

    def run(self, *, session, working_set, tools) -> AgentResult:
        del tools  # the headless model owns the read-only tool loop in this adapter
        self._report("understanding", "正在理解用户描述", "把原始描述与当前 Working Set 对齐")

        def on_message(message: Any) -> None:
            message_type = str(getattr(message, "type", "") or "")
            if message_type == "tool_use":
                tool = str(getattr(message, "tool", "") or "")
                labels = {
                    "read_text": "阅读项目文件",
                    "search": "搜索代码和测试",
                    "run_read_only": "执行只读检查",
                }
                activity = labels.get(tool, "调查项目证据")
                raw_input = getattr(message, "input", None)
                detail: dict[str, Any] = {"kind": "tool_call", "tool": tool, "status": "running"}
                if isinstance(raw_input, Mapping):
                    for key in ("path", "pattern", "command", "target", "cmd", "code"):
                        value = raw_input.get(key)
                        if value:
                            detail[{"cmd": "command", "code": "command"}.get(key, key)] = str(value)
                self._report("investigation", activity, "Agent 正在收集可验证证据", detail)
            elif message_type == "tool_result":
                output = str(getattr(message, "output", "") or "")
                self._report("investigation", "整理调查结果", "刚完成一项只读证据检查", {
                    "kind": "tool_result", "tool": str(getattr(message, "tool", "") or ""),
                    "status": "completed",
                    "result_summary": output[:1000],
                    "result_detail": output,
                    "result_truncated": False,
                    "result_chars": len(output),
                })
            elif message_type == "thinking":
                self._report("investigation", "分析已收集的信息", "正在判断下一步调查路径", {
                    "kind": "analysis", "status": "running",
                })

        agent_working = working_set.to_dict()
        agent_working["workflow"] = {
            key: value for key, value in working_set.workflow.items() if key != "reanalysis_history"
        }
        prompt = _SESSION_SYSTEM_PROMPT + "\n\n" + json.dumps({
            "raw_report": session.raw_report,
            "source_binding": dict(session.source_binding),
            "attachments": [
                {**item.to_dict(), "path": str(sidecar_path(
                    Path(getattr(self.agent, "state_dir", self.state_dir)), item.ref,
                ))}
                for item in session.attachments
            ],
            "user_replies": list(session.user_replies),
            "working_set": agent_working,
            "reanalysis_request": dict(working_set.workflow.get("reanalysis_request") or {}),
            "instruction": (
                "Continue from the last committed Working Set and saved tool-result refs. "
                "If reanalysis_request is present, investigate the selected fields using the user's per-field comments; "
                "retain unrelated facts and evidence, replace stale evidence for corrected claims, and explain changes. "
                "Do not repeat completed checks unless their evidence is stale. "
                "If evidence is insufficient, investigate before asking."
            ),
        }, ensure_ascii=False)
        result = self.agent.run_turn(
            backend=self.backend,
            message=prompt,
            scope="project",
            thread_key=f"self-issue-session:{session.session_id}",
            context={"operation_kind": "sidecar_operation", "issue_session_id": session.session_id,
                     "turn_id": f"{session.session_id}:{session.revision}"},
            permission_profile="issue_investigation",
            on_message=on_message,
        )
        if not result.ok:
            self._report("failed", "Agent 没有返回可用结果", result.error or result.status)
            return AgentResult(status="failed", working_set=working_set, reason=result.error or result.status)
        text = str(result.reply or "").strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
        try:
            value = json.loads(fenced.group(1) if fenced else text)
        except (json.JSONDecodeError, AttributeError) as exc:
            self._report("failed", "Agent 返回的结果无法解析", str(exc))
            return AgentResult(status="failed", working_set=working_set, reason=f"invalid Issue Agent JSON: {exc}")
        if not isinstance(value, Mapping):
            self._report("failed", "Agent 返回格式不正确", "结果不是 JSON 对象")
            return AgentResult(status="failed", working_set=working_set, reason="Issue Agent output must be an object")
        from dataclasses import replace
        raw_report = value.get("report")
        report = _mapping_or_fallback(raw_report, working_set.report)
        disposition = str(report.get("external_issue_disposition") or "").strip().lower()
        if disposition != "needs_triage":
            report["external_issue_disposition"] = "needs_triage"
        if "investigation_gaps" not in report:
            report["investigation_gaps"] = list(value.get("investigation_gaps") or ())
        if "downstream_verification_tasks" not in report:
            report["downstream_verification_tasks"] = list(value.get("downstream_verification_tasks") or ())
        updated = replace(
            working_set,
            fields=_mapping_or_fallback(value.get("fields"), working_set.fields),
            field_provenance=_mapping_or_fallback(value.get("field_provenance"), working_set.field_provenance),
            claims=_mapping_sequence(value.get("claims"), working_set.claims),
            evidences=_mapping_sequence(value.get("evidences"), working_set.evidences),
            gaps=tuple(str(item) for item in value.get("gaps") or ()),
            hypotheses=_mapping_sequence(value.get("hypotheses"), working_set.hypotheses),
            conflicts=_mapping_sequence(value.get("conflicts"), working_set.conflicts),
            report=report,
        )
        status = str(value.get("status") or "failed")
        reason = str(value.get("reason") or "")
        if status == "ready" and not updated.report:
            status = "failed"
            reason = reason or "Issue Agent returned ready without a mapping report"
        if status == "ask_user":
            self._report("ask_user", "需要补充用户意图", str(value.get("question") or ""))
        elif status == "ready":
            self._report("validate", "正在确认 Issue 报告", "检查字段、证据和冲突是否一致")
        else:
            self._report("gap_analysis", "检查信息缺口", "判断是否还需要继续调查")
        return AgentResult(
            status=status, working_set=updated,
            question=str(value.get("question") or ""), reason=reason,
            validation=_mapping_or_fallback(value.get("validation")),
        )


class SelfIssueSessionHost(IssueWorkspaceMixin):
    def __init__(
        self,
        state_dir: Path,
        project_root: Path,
        *,
        agent: IssueAgent | None = None,
        policy: Any | None = None,
        execution_owner: str = "local",
    ) -> None:
        self.state_dir = Path(state_dir)
        self.project_root = Path(project_root)
        self.policy = policy
        self.execution_owner = execution_owner
        self.store = JsonIssueSessionStore(
            self.state_dir / "self-issues" / "portable-sessions",
        )
        self.agent = agent or ContinuousIssueAgent()

    def _tools(self) -> ReadOnlyInvestigationTools:
        return ReadOnlyInvestigationTools(self.project_root, allowed_paths=(".",))

    def _writer(self) -> EventWriter:
        return EventWriter(event_log_from_project(self.state_dir))

    def _service(self) -> SelfIssueService:
        return SelfIssueService(
            self.state_dir,
            self._writer(),
            project_root=self.project_root,
            policy=self.policy,
        )

    def _progress(
        self, session_id: str, phase: str, activity: str, summary: str = "",
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        with self.store.transaction(session_id):
            if self.store.load_session(session_id).status in {SessionStatus.PAUSED, SessionStatus.CANCELLED}:
                return
            self._progress_locked(session_id, phase, activity, summary, detail)

    def _progress_locked(self, session_id, phase, activity, summary="", detail=None):
        """Persist a safe, user-facing progress projection without exposing model text."""
        try:
            working = self.store.load_working_set(session_id)
            workflow = dict(working.workflow)
            now = _now()
            history = list(workflow.get("history") or [])
            activity_log = list(workflow.get("activity_log") or [])
            previous = str(workflow.get("phase") or "")
            if previous and previous != phase:
                history.append({"phase": previous, "label": _PHASE_LABELS.get(previous, previous), "completed_at": now})
            workflow.update({
                "phase": phase,
                "route": phase,
                "label": _PHASE_LABELS.get(phase, phase),
                "activity": str(activity or "")[:500],
                "summary": str(summary or "")[:1000],
                "updated_at": now,
                "heartbeat_at": now,
                "history": history[-20:],
            })
            if detail:
                detail = dict(redact_obj(detail))
                if detail.get("result_detail"):
                    detail["result_ref"] = self._save_activity_result(session_id, detail.pop("result_detail"))
                activity_log.append({
                    "id": f"act-{uuid4().hex}",
                    "phase": phase,
                    "label": str(activity or "")[:300],
                    "summary": str(summary or "")[:1000],
                    "at": now,
                    **{str(key): value for key, value in detail.items() if key in {
                        "kind", "tool", "status", "path", "pattern", "command", "target", "result_summary",
                        "result_detail", "result_truncated", "result_chars", "result_ref", "upstream_truncated",
                    }},
                })
                workflow["activity_log"] = activity_log
            self.store.save_working_set(replace(working, workflow=workflow), expected_revision=working.revision)
        except (FileNotFoundError, KeyError, ValueError):
            # A cancellation or a newer revision may legitimately win the race.
            return

    def _mark_investigating(self, session_id: str, *, phase: str = "queued") -> IssueSession:
        session = self.store.load_session(session_id)
        working = self.store.load_working_set(session_id)
        next_revision = max(session.revision, working.revision) + 1
        self.store.save_working_set(
            replace(working, revision=next_revision, workflow={
                **dict(working.workflow),
                "phase": phase,
                "route": phase,
                "label": _PHASE_LABELS.get(phase, phase),
                "activity": "已接收，后台 Agent 即将开始",
                "summary": "提交已保存；页面会持续刷新这个 Session 的进度",
                "updated_at": _now(),
                "heartbeat_at": _now(),
                "reason": "", "interruption_source": "", "resume_after_restart": False,
            }),
            expected_revision=working.revision,
        )
        return self.store.save_session(
            replace(session, revision=next_revision, status=SessionStatus.INVESTIGATING, user_question="", updated_at=_now()),
            expected_revision=session.revision,
        )

    def _run_background(self, session_id: str) -> None:
        try:
            cancel_event = execution.cancellation(self, session_id)
            agent = self.agent
            if isinstance(agent, HeadlessIssueSessionAgent):
                agent.progress = lambda phase, activity, summary="", detail=None: self._progress(
                    session_id, phase, activity, summary, detail,
                )
            self._progress(session_id, "understanding", "正在启动 Issue Agent", "后台任务已启动")
            result = IssueSessionWorker(
                self.store, max_turns=3,
                cancel_requested=(cancel_event.is_set if cancel_event is not None else None),
            ).run(session_id, agent, self._tools())
            current = self.store.load_session(session_id)
            if current.status in {SessionStatus.CANCELLED, SessionStatus.PAUSED}:
                return
            if result.status == "ready_for_confirmation":
                self._progress(session_id, "ready_for_confirmation", "报告已生成，等待确认", "调查、合成和验证已完成")
            elif result.status == "cancelled":
                self._progress(session_id, "cancelled", "分析已中断", "用户中断；已保留最近一次安全检查点")
            elif result.status in {"budget_exhausted", "turn_limit_exhausted"}:
                self.pause(session_id, reason=result.reason or "达到分析预算，已保留已收集证据", user=False)
            elif result.status == "waiting_for_user":
                self._progress(session_id, "ask_user", "等待用户回答", result.reason)
            elif result.status == "failed":
                self._progress(session_id, "failed", "Issue Agent 执行失败", result.reason)
        except Exception as exc:
            try:
                if self.store.load_session(session_id).status not in {SessionStatus.CANCELLED, SessionStatus.PAUSED}:
                    self._mark_failed(session_id, exc)
            except Exception:
                pass

    def _schedule(self, session_id: str) -> dict[str, Any]:
        return execution.start(self, session_id)

    @execution.admitted
    def create_and_schedule(
        self, raw_report: str, *, attachments: Sequence[Mapping[str, Any]] | None = None,
        target_binding: Mapping[str, Any] | None = None,
        source_binding: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from zf.web.kanban_agent_attachments import normalize_kanban_agent_attachments
        verified_attachments = normalize_kanban_agent_attachments(self.state_dir, list(attachments or []))
        session = self.store.create(
            raw_report,
            attachments=self._attachments(verified_attachments),
            target_binding=target_binding,
            source_binding=source_binding,
        )
        self._mark_investigating(session.session_id, phase="queued")
        return self._schedule(session.session_id)

    @staticmethod
    def _attachments(values: Sequence[Mapping[str, Any]] | None) -> tuple[AttachmentRef, ...]:
        result: list[AttachmentRef] = []
        for value in values or ():
            if not isinstance(value, Mapping):
                continue
            ref = str(value.get("ref") or value.get("attachment_id") or "")
            name = str(value.get("name") or value.get("filename") or ref or "attachment")
            if ref:
                result.append(AttachmentRef(
                    ref=ref,
                    name=name,
                    media_type=str(value.get("media_type") or value.get("content_type") or "application/octet-stream"),
                    digest=str(value.get("digest") or value.get("sha256") or ""),
                    disclosure=str(value.get("disclosure") or "private"),
                ))
        return tuple(result)

    @staticmethod
    def _projection(session, result=None, working=None) -> dict[str, Any]:
        working = result.working_set if result is not None else working
        status = result.status if result is not None else session.status.value
        workflow = dict(working.workflow) if working is not None else {}
        return {
            "session_id": session.session_id,
            "session_status": session.status.value,
            "status": status,
            "revision": session.revision,
            "target_binding": dict(session.target_binding),
            "source_binding": dict(session.source_binding),
            "question": result.question if result is not None else session.user_question,
            "reason": result.reason if result is not None else str(workflow.get("reason") or ""),
            "working_set": working.to_dict() if working is not None else None,
            "workflow": workflow,
            "report": (
                SelfIssueSessionHost._stage_report_contract(
                    working.report, working.fields, working.gaps,
                )
                if working is not None else {}
            ),
            "final_report_ref": session.final_report_ref,
            "publication_snapshot_ref": session.publication_snapshot_ref,
            "publication_draft_id": session.publication_draft_id,
        }

    def create_and_run(
        self,
        raw_report: str,
        *,
        attachments: Sequence[Mapping[str, Any]] | None = None,
        target_binding: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = self.store.create(
            raw_report,
            attachments=self._attachments(attachments),
            target_binding=target_binding,
        )
        try:
            result = IssueSessionWorker(self.store, max_turns=3).run(
                session.session_id, self.agent, self._tools(),
            )
        except Exception as exc:
            return self._mark_failed(session.session_id, exc)
        return self._projection(self.store.load_session(session.session_id), result.last_result)

    def answer(self, session_id: str, answer: str) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        try:
            result = IssueSessionWorker(self.store, max_turns=3).runner.answer_user(
                session_id, answer, self.agent, self._tools(),
            )
        except Exception as exc:
            return self._mark_failed(session_id, exc)
        return self._projection(self.store.load_session(session_id), result)

    @execution.admitted
    def answer_and_schedule(
        self,
        session_id: str,
        answer: str,
        *,
        attachments: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from zf.web.kanban_agent_attachments import (
            MAX_KANBAN_ATTACHMENTS, normalize_kanban_agent_attachments,
        )

        if not answer.strip():
            raise ValueError("user reply must not be empty")
        verified = normalize_kanban_agent_attachments(self.state_dir, list(attachments or []))
        with self.store.transaction(session_id):
            session = self.store.load_session(session_id)
            if session.status != SessionStatus.WAITING_FOR_USER:
                raise ValueError("Issue Session is not waiting for user input")
            combined = {item.ref: item for item in session.attachments}
            combined.update({item.ref: item for item in self._attachments(verified)})
            if len(combined) > MAX_KANBAN_ATTACHMENTS:
                raise ValueError(f"at most {MAX_KANBAN_ATTACHMENTS} attachments are allowed")
            self.store.save_session(
                replace(
                    session,
                    revision=session.revision + 1,
                    attachments=tuple(combined.values()),
                    user_replies=(*session.user_replies, answer.strip()),
                    user_question="",
                    status=SessionStatus.INVESTIGATING,
                    updated_at=_now(),
                ),
                expected_revision=session.revision,
            )
        self._progress(session_id, "investigation", "已收到回答，继续调查", "正在根据用户补充信息重新检查缺口")
        return self._schedule(session_id)

    @execution.admitted
    def reanalyze_and_schedule(
        self, session_id: str, selected_fields: Sequence[str], comment: str,
    ) -> dict[str, Any]:
        """Start another bounded analysis turn without destroying the prior report.

        The previous Working Set is retained in ``workflow.reanalysis_history``;
        the agent receives the selected fields and user comment as a scoped
        investigation request and may add/replace evidence in the new revision.
        """
        session = self.store.load_session(session_id)
        if session.status not in {SessionStatus.READY_FOR_CONFIRMATION, SessionStatus.COMPLETED}:
            raise ValueError("Finish or resume the current analysis before starting another round")
        working = self.store.load_working_set(session_id)
        fields = tuple(dict.fromkeys(str(item).strip() for item in selected_fields if str(item).strip()))
        note = str(comment or "").strip()
        if not fields and not note:
            raise ValueError("select at least one report item or add a comment")
        history = list(working.workflow.get("reanalysis_history") or [])
        history.append({
            "revision": working.revision,
            "report": self._stage_report_contract(working.report, working.fields, working.gaps),
            "evidences": list(working.evidences),
            "activity_log": list(working.workflow.get("activity_log") or []),
            "validation": dict(working.workflow.get("validation") or {}),
            "fields": list(fields),
            "comment": note,
            "status": "superseded_by_reanalysis",
            "at": _now(),
        })
        workflow = {
            **dict(working.workflow),
            "reanalysis_request": {"fields": list(fields), "comment": note, "at": _now()},
            "reanalysis_history": history,
            "activity_log": [],
            "phase": "investigation",
            "route": "investigation",
            "label": _PHASE_LABELS["investigation"],
            "activity": "根据用户选中的报告项重新调查",
            "summary": f"已选中 {len(fields)} 项；正在补充证据并重新验证",
            "updated_at": _now(),
        }
        next_revision = max(session.revision, working.revision) + 1
        saved_working = self.store.save_working_set(
            replace(working, revision=next_revision, workflow=workflow),
            expected_revision=working.revision,
        )
        saved_session = self.store.save_session(
            replace(session, revision=next_revision, status=SessionStatus.INVESTIGATING, user_question="", updated_at=_now()),
            expected_revision=session.revision,
        )
        del saved_working, saved_session
        return self._schedule(session_id)

    def cancel(self, session_id: str) -> dict[str, Any]:
        cancel_event = execution.cancellation(self, session_id)
        if cancel_event is not None:
            cancel_event.set()
        with self.store.transaction(session_id):
            session = IssueSessionWorker(self.store, max_turns=1).runner.cancel(session_id)
        return self._projection(session, working=self.store.load_working_set(session_id))

    def pause_and_resume_later(
        self, session_id: str, *, host_shutdown: bool = False,
    ) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        if host_shutdown and session.status not in {SessionStatus.CREATED, SessionStatus.INVESTIGATING}:
            return self.get(session_id)
        result = self.pause(
            session_id, reason="飞书服务关闭，已保存检查点，重启后继续" if host_shutdown else "用户中断分析",
            user=not host_shutdown, resume_after_restart=host_shutdown,
        )
        event = execution.cancellation(self, session_id)
        if event is not None:
            event.set()
        from zf.web.agent_session_runtime import cancel_agent_session_run, run_key
        cancel_agent_session_run(run_key(
            run_id=f"{session_id}:{session.revision}", thread_id=f"self-issue-session:{session_id}",
        ))
        return result

    def retry(self, session_id: str) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        if session.status in {SessionStatus.CANCELLED, SessionStatus.COMPLETED}:
            raise ValueError(f"Issue Session is terminal: {session.status.value}")
        try:
            result = IssueSessionWorker(self.store, max_turns=3).run(
                session_id, self.agent, self._tools(),
            )
        except Exception as exc:
            return self._mark_failed(session_id, exc)
        return self._projection(self.store.load_session(session_id), result.last_result)

    def retry_and_schedule(self, session_id: str) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        if session.status in {SessionStatus.CANCELLED, SessionStatus.COMPLETED}:
            raise ValueError(f"Issue Session is terminal: {session.status.value}")
        if execution.running(self, session_id):
            raise ValueError("上一轮进程仍在退出；安全检查点已保存，请稍后恢复")
        return self._schedule(session_id)

    def recover_and_schedule(self, session_id: str, *, automatic: bool = False) -> dict[str, Any]:
        """Resume a non-terminal run through an explicit action or host lifecycle."""
        session = self.store.load_session(session_id)
        if session.status not in {SessionStatus.CREATED, SessionStatus.INVESTIGATING, SessionStatus.PAUSED}:
            return self.get(session_id)
        return execution.start(self, session_id, automatic=automatic)

    def _mark_failed(self, session_id: str, error: Exception) -> dict[str, Any]:
        with self.store.transaction(session_id):
            if self.store.load_session(session_id).status in {SessionStatus.PAUSED, SessionStatus.CANCELLED}:
                return self.get(session_id)
            return self._mark_failed_locked(session_id, error)

    def _mark_failed_locked(self, session_id: str, error: Exception) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        working = self.store.load_working_set(session_id)
        reason = f"{type(error).__name__}: {error}"[:2000]
        next_revision = max(session.revision, working.revision) + 1
        failed_working = replace(
            working,
            revision=next_revision,
            workflow={**dict(working.workflow), "phase": "failed", "route": "diagnose", "reason": reason},
        )
        self.store.save_working_set(failed_working, expected_revision=working.revision)
        failed_session = replace(
            session,
            revision=next_revision,
            status=SessionStatus.FAILED,
            user_question="",
            updated_at=_now(),
        )
        self.store.save_session(failed_session, expected_revision=session.revision)
        return self._projection(failed_session, AgentResult(
            status="failed", working_set=failed_working, reason=reason,
        ))

    def confirm(
        self,
        session_id: str,
        *,
        confirmed_by: str = "user",
        provider: str = "",
        target: str = "",
        disclosure_policy: str = "handoff_private",
    ) -> dict[str, Any]:
        runner = IssueSessionWorker(self.store, max_turns=1).runner
        session = runner.confirm(
            session_id,
            confirmed_by=confirmed_by,
            provider=provider,
            target=target,
            disclosure_policy=disclosure_policy,
        )
        return self._projection(session, working=self.store.load_working_set(session_id))

    @staticmethod
    def _report_text(report: Mapping[str, Any], key: str, default: str = "") -> str:
        value = report.get(key, default)
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False)
        return str(value or default)

    @staticmethod
    def _stage_report_contract(
        report: Mapping[str, Any], fields: Mapping[str, Any], gaps: Sequence[Any],
    ) -> dict[str, Any]:
        return stage_report_contract(report, fields, gaps)

    @staticmethod
    def _normalize_classification(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in CLASSIFICATIONS:
            return normalized
        if normalized == "bug" or any(token in normalized for token in ("packaging", "dependency", "regression")):
            return "bug"
        return "unknown"


    def confirm_and_materialize(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        confirmed = self.confirm(session_id, **kwargs)
        return {**confirmed, **self.materialize_publication_draft(session_id)}

    def get(self, session_id: str) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        working = self.store.load_working_set(session_id)
        result = {
            **self._projection(session, working=working),
            "working_set": working.to_dict(),
            "report": self._stage_report_contract(
                working.report, working.fields, working.gaps,
            ),
        }
        if session.publication_draft_id:
            draft = self._service().drafts.get(session.publication_draft_id)
            if draft is not None:
                result["draft"] = self._service()._draft_view(draft)
                result["editor"] = self.editor(session_id)
        return result


__all__ = ["HeadlessIssueSessionAgent", "SelfIssueSessionHost"]
