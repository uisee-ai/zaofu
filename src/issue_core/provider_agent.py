"""Tool-calling continuous Issue Agent independent from any host runtime."""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Any, Mapping

from .provider import IssueAgentProvider, ProviderRequest
from .session import AgentResult, InvestigationTools, IssueSession, IssueWorkingSet


def _mapping_or_fallback(value: Any, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return dict(fallback or {})


def _mapping_sequence(value: Any, fallback=()):
    if not isinstance(value, (list, tuple)):
        return tuple(fallback)
    return tuple(
        dict(item) if isinstance(item, Mapping)
        else {"kind": "agent_output", "value": str(item)}
        for item in value if item not in (None, "")
    )


_SYSTEM = """You are one continuous Issue Agent. First understand the raw report semantically; do
not use deterministic field extraction or treat the first interpretation as truth. Then inspect
the authorized project with read-only tools (code, tests, logs and available browser evidence),
reconcile claims and conflicts, synthesize a complete Issue Report, and validate it. Ask the user
only for unresolved user intent that cannot be obtained from evidence, and ask all such questions
in one batch. The raw report is authoritative user input; the Working Set is only a candidate
interpretation. Return tool calls when evidence is needed. When done, return one JSON object with
status (continue|ask_user|ready|failed), fields, field_provenance, claims, evidences, gaps,
hypotheses, conflicts, report, validation, question and reason. Validation failures must be
routable with type, field, message and recoverable_by (investigation|ask_user|reconcile|
re_synthesize). Before ready, run bounded read-only checks: resolve the exact Git revision,
search packaging/source/CLI locations, and attempt a minimal safe reproduction such as import,
--help, or an existing clean-install test when its artifact is available. If a check cannot run,
return status=not_run with a concrete prerequisite reason and an investigation gap; do not silently
skip it. Inspect issue-test-assets and attached logs/metadata before marking independent_reproduction
not_run; artifact-backed reproduction is valid bounded evidence even when the executable package is
absent. Include check name, status, evidence_refs, meaning, reason, and next_action. Never claim
an observation without an evidence entry and never modify source files."""


class ToolCallingIssueAgent:
    def __init__(
        self,
        provider: IssueAgentProvider,
        *,
        max_tool_rounds: int = 20,
        timeout_seconds: int = 180,
    ) -> None:
        self.provider = provider
        self.max_tool_rounds = max_tool_rounds
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        *,
        session: IssueSession,
        working_set: IssueWorkingSet,
        tools: InvestigationTools,
    ) -> AgentResult:
        observations: list[Mapping[str, Any]] = []
        for _round in range(self.max_tool_rounds + 1):
            request = ProviderRequest(
                system=_SYSTEM,
                user=json.dumps({
                    "raw_report": session.raw_report,
                    "user_replies": list(session.user_replies),
                    "attachments": [item.to_dict() for item in session.attachments],
                    "working_set": working_set.to_dict(),
                }, ensure_ascii=False),
                context={"observations": observations},
                allowed_tools=("read_text", "search", "run_read_only"),
                timeout_seconds=self.timeout_seconds,
            )
            response = self.provider.complete(request)
            if not response.ok:
                return AgentResult(
                    status="failed",
                    working_set=working_set,
                    reason=response.error or "Issue Agent provider failed",
                )
            if response.tool_calls:
                if _round >= self.max_tool_rounds:
                    return AgentResult(
                        status="failed",
                        working_set=working_set,
                        reason="Issue Agent tool-round budget exhausted",
                    )
                observations.extend(self._execute_calls(response.tool_calls, tools))
                continue
            return self._result(response.text, working_set)
        return AgentResult(status="failed", working_set=working_set, reason="Issue Agent loop exhausted")

    @staticmethod
    def _execute_calls(calls, tools: InvestigationTools) -> list[Mapping[str, Any]]:
        observations: list[Mapping[str, Any]] = []
        for call in calls:
            name = str(call.get("name") or "")
            arguments = call.get("arguments")
            arguments = dict(arguments) if isinstance(arguments, Mapping) else {}
            try:
                if name == "read_text":
                    output: Any = tools.read_text(str(arguments.get("path") or ""))
                elif name == "search":
                    output = list(tools.search(
                        str(arguments.get("pattern") or ""),
                        path=str(arguments.get("path") or "."),
                    ))
                elif name == "run_read_only":
                    command = arguments.get("command")
                    if not isinstance(command, list):
                        raise ValueError("run_read_only command must be a list")
                    output = dict(tools.run_read_only(
                        tuple(str(item) for item in command),
                        timeout_seconds=int(arguments.get("timeout_seconds") or 180),
                    ))
                else:
                    raise ValueError(f"unsupported Issue Agent tool: {name}")
                observations.append({"tool": name, "ok": True, "output": output})
            except Exception as exc:  # tool errors are observations, not host crashes
                observations.append({"tool": name, "ok": False, "error": str(exc)[:1000]})
        return observations

    @staticmethod
    def _result(text: str, working_set: IssueWorkingSet) -> AgentResult:
        try:
            value = json.loads(str(text or ""))
        except json.JSONDecodeError as exc:
            raise ValueError("Issue Agent returned invalid JSON") from exc
        if not isinstance(value, Mapping):
            raise ValueError("Issue Agent result must be a JSON object")
        updated = replace(
            working_set,
            fields=_mapping_or_fallback(value.get("fields"), working_set.fields),
            field_provenance=_mapping_or_fallback(value.get("field_provenance"), working_set.field_provenance),
            claims=_mapping_sequence(value.get("claims"), working_set.claims),
            evidences=_mapping_sequence(value.get("evidences"), working_set.evidences),
            gaps=tuple(str(item) for item in value.get("gaps") or ()),
            hypotheses=_mapping_sequence(value.get("hypotheses"), working_set.hypotheses),
            conflicts=_mapping_sequence(value.get("conflicts"), working_set.conflicts),
            report=_mapping_or_fallback(value.get("report"), working_set.report),
        )
        status = str(value.get("status") or "failed")
        reason = str(value.get("reason") or "")
        if status == "ready" and not updated.report:
            status = "failed"
            reason = reason or "Issue Agent returned ready without a mapping report"
        result = AgentResult(
            status=status,
            working_set=updated,
            question=str(value.get("question") or ""),
            reason=reason,
            validation=_mapping_or_fallback(value.get("validation")),
        )
        return result


__all__ = ["ToolCallingIssueAgent"]
