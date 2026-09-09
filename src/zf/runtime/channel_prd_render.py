"""Render and source-reference helpers for a durable Channel PRD artifact."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.runtime.channel_reply_parsing import (
    reply_question_texts,
    string_items,
)
from zf.runtime.channel_sidecar import hydrate_channel_message_text


def render_channel_prd_artifact(
    *,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    source_requirement: str,
    synthesis: dict[str, Any],
    summary: str,
    source_refs: list[str],
) -> str:
    title = str(
        synthesis.get("title")
        or channel.get("name")
        or f"Channel requirement {channel_id}"
    ).strip()
    decisions = string_items(synthesis.get("decisions"))
    primary_decision = str(synthesis.get("decision") or "").strip()
    if primary_decision and primary_decision not in decisions:
        decisions.insert(0, primary_decision)
    assumptions = string_items(synthesis.get("assumptions"))
    out_of_scope = string_items(synthesis.get("out_of_scope"))
    acceptance = string_items(synthesis.get("acceptance_criteria"))
    verification_commands = string_items(synthesis.get("verification_commands"))
    risks = string_items(synthesis.get("risks"))
    dissent = string_items(synthesis.get("dissent"))
    open_questions = reply_question_texts(synthesis)
    for question in channel.get("open_questions") or []:
        if not isinstance(question, dict):
            continue
        if str(question.get("thread_id") or "main") != thread_id:
            continue
        if str(question.get("status") or "") != "resolved":
            continue
        question_text = str(question.get("question") or "").strip()
        answer = str(question.get("answer") or "").strip()
        resolved_decision = (
            f"{question_text}: {answer}"
            if question_text and answer
            else ""
        )
        if resolved_decision and resolved_decision not in decisions:
            decisions.append(resolved_decision)
    workflow = (
        synthesis.get("recommended_workflow")
        if isinstance(synthesis.get("recommended_workflow"), dict)
        else {}
    )

    def section(name: str, values: list[str]) -> list[str]:
        return [
            f"## {name}",
            *([f"- {item}" for item in values] or ["- None."]),
            "",
        ]

    lines = [
        f"# {title}",
        "",
        "## Source Requirement",
        source_requirement or "No source requirement supplied.",
        "",
        "## Requirement",
        summary or "No summary supplied.",
        "",
        *section("Decisions", decisions),
        *section("Assumptions", assumptions),
        *section("Out of Scope", out_of_scope),
        *section("Acceptance Criteria", acceptance),
        *section(
            "Verification Commands",
            [f"`{command}`" for command in verification_commands],
        ),
        *section("Risks", risks),
        *section("Dissent", dissent),
        *section("Open Questions", open_questions),
        "## Recommended Workflow",
        "```json",
        json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Provenance",
        f"- Channel: `{channel_id}`",
        f"- Thread: `{thread_id}`",
        *[f"- Source: `{ref}`" for ref in source_refs],
        "",
    ]
    return "\n".join(lines)


def channel_requirement_text(
    state_dir: Path,
    channel: dict[str, Any],
    thread_id: str,
) -> str:
    discussions = channel.get("discussions")
    session = (
        discussions.get(thread_id)
        if isinstance(discussions, dict)
        else {}
    )
    requirement_id = (
        str(session.get("requirement_message_id") or "")
        if isinstance(session, dict)
        else ""
    )
    for message in channel.get("messages") or []:
        if not isinstance(message, dict):
            continue
        if requirement_id and str(message.get("message_id") or "") != requirement_id:
            continue
        if str(message.get("thread_id") or "main") != thread_id:
            continue
        text = hydrate_channel_message_text(
            state_dir,
            message,
            strict=False,
        ).strip()
        if text:
            return text
    return ""


def channel_prd_event_refs(
    channel: dict[str, Any],
    thread_id: str,
) -> list[str]:
    relevant_types = {
        "channel.finding.recorded",
        "channel.message.posted",
        "channel.question.opened",
        "channel.question.resolved",
        "channel.questions.frozen",
        "channel.synthesis.requested",
    }
    refs: list[str] = []
    for event in channel.get("linked_events") or []:
        if not isinstance(event, dict):
            continue
        payload = (
            event.get("payload")
            if isinstance(event.get("payload"), dict)
            else {}
        )
        if str(payload.get("thread_id") or "main") != thread_id:
            continue
        if str(event.get("type") or "") not in relevant_types:
            continue
        event_id = str(event.get("id") or "").strip()
        if event_id:
            refs.append(f"event:{event_id}")
    return list(dict.fromkeys(refs))[-64:]


__all__ = [
    "channel_prd_event_refs",
    "channel_requirement_text",
    "render_channel_prd_artifact",
]
