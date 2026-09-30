"""Provider-neutral continuous Issue Agent baseline.

The implementation is intentionally deterministic and injectable.  A real
Codex/Claude adapter can replace ``understanding_agent`` and ``investigator``
without changing the Session or Worker contracts.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from .normalizer import DeterministicIssueUnderstandingAgent, IssueUnderstandingAgent
from .session import AgentResult, InvestigationTools, IssueSession, IssueWorkingSet, stable_digest


Investigator = Callable[
    [IssueSession, IssueWorkingSet, InvestigationTools], Mapping[str, Any]
]


def default_read_only_investigator(
    session: IssueSession,
    working_set: IssueWorkingSet,
    tools: InvestigationTools,
) -> Mapping[str, Any]:
    """Collect bounded, host-neutral observations for the baseline Agent.

    This is intentionally conservative: it records command output as Evidence
    but never turns it into a root-cause claim or edits the Working Set fields.
    Hosts/providers can replace it with a richer investigator.
    """

    del working_set
    evidences: list[Mapping[str, Any]] = []
    try:
        status = tools.run_read_only(("git", "status", "--short"), timeout_seconds=10)
        evidences.append({
            "kind": "workspace_status",
            "command": ["git", "status", "--short"],
            "observation": dict(status),
        })
    except Exception as exc:  # tool failures remain explicit observations
        evidences.append({
            "kind": "workspace_status",
            "command": ["git", "status", "--short"],
            "error": str(exc)[:500],
        })
    return {"evidences": tuple(evidences)}


def _dedupe_mappings(values: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        marker = stable_digest(dict(value))
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


class ContinuousIssueAgent:
    """Run understanding, investigation, reconciliation and composition in one Session."""

    def __init__(
        self,
        *,
        understanding_agent: IssueUnderstandingAgent | None = None,
        investigator: Investigator | None = None,
    ) -> None:
        self.understanding_agent = understanding_agent or DeterministicIssueUnderstandingAgent()
        self.investigator = investigator or default_read_only_investigator

    def run(
        self,
        *,
        session: IssueSession,
        working_set: IssueWorkingSet,
        tools: InvestigationTools,
    ) -> AgentResult:
        environment = {}
        proposal = self.understanding_agent.understand(
            "\n\n".join((session.raw_report, *session.user_replies)),
            environment=environment,
            project_version="",
        )
        fields = dict(working_set.fields)
        for key in (
            "title", "summary", "bug_description", "reproduction_steps",
            "actual_behavior", "expected_behavior", "attachment_context",
            "additional_context", "environment", "project_version",
        ):
            value = proposal.get(key)
            if value not in (None, "", {}, []):
                fields[key] = value
        understanding = proposal.get("understanding")
        provenance = dict(working_set.field_provenance)
        if isinstance(understanding, Mapping):
            provenance.update(dict(understanding.get("field_provenance") or {}))
        claims = list(working_set.claims)
        if not claims:
            claims.append({"kind": "reported", "value": session.raw_report, "source": "raw_report"})

        evidence = list(working_set.evidences)
        hypotheses = list(working_set.hypotheses)
        conflicts = list(working_set.conflicts)
        auto_gaps = {"reproduction steps", "expected behavior"}
        gaps = [item for item in working_set.gaps if item not in auto_gaps]
        findings = dict(self.investigator(session, working_set, tools) or {})
        evidence.extend(tuple(findings.get("evidences") or ()))
        claims.extend(tuple(findings.get("claims") or ()))
        hypotheses.extend(tuple(findings.get("hypotheses") or ()))
        conflicts.extend(tuple(findings.get("conflicts") or ()))
        gaps = list(dict.fromkeys([*gaps, *(str(item) for item in findings.get("gaps") or ())]))
        for key, value in dict(findings.get("field_updates") or {}).items():
            if key not in fields or fields[key] in ("", {}, []):
                fields[key] = value
                provenance[key] = {"source": "observed", "confidence": 1.0}

        missing = [
            label for label, key in (
                ("reproduction steps", "reproduction_steps"),
                ("expected behavior", "expected_behavior"),
            ) if not str(fields.get(key) or "").strip()
        ]
        gaps = list(dict.fromkeys([*gaps, *missing]))
        updated = replace(
            working_set,
            fields=fields,
            field_provenance=provenance,
            claims=tuple(_dedupe_mappings(claims)),
            evidences=tuple(_dedupe_mappings(evidence)),
            hypotheses=tuple(_dedupe_mappings(hypotheses)),
            conflicts=tuple(_dedupe_mappings(conflicts)),
            gaps=tuple(gaps),
        )
        if gaps:
            return AgentResult(
                status="ask_user",
                working_set=updated,
                question="请补充以下信息：" + "、".join(gaps),
            )
        report = {
            "title": str(fields.get("title") or "Issue report"),
            "description": str(fields.get("bug_description") or fields.get("summary") or ""),
            "reproduction_steps": str(fields.get("reproduction_steps") or ""),
            "actual_behavior": str(fields.get("actual_behavior") or ""),
            "expected_behavior": str(fields.get("expected_behavior") or ""),
            "environment": dict(fields.get("environment") or {}),
            "attachment_context": str(fields.get("attachment_context") or ""),
            "evidence_summary": [dict(item) for item in evidence],
            "known_limitations": list(gaps),
            "conflicts": [dict(item) for item in conflicts],
        }
        return AgentResult(status="ready", working_set=replace(updated, report=report))


__all__ = ["ContinuousIssueAgent", "Investigator", "default_read_only_investigator"]
