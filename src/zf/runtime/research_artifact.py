"""ZF-TR-RESEARCH-001 — evidence-backed research artifact protocol (doc 39 §2.1.6).

Research outputs (from autoresearch / arch / critic / dev investigation
phases) must produce a structured Markdown artifact with:

- research_question
- sources fetched / inspected
- evidence snippets with path:line
- pattern / constraint / risk
- not answered / uncertainty
- downstream impact on plan/spec/test

These artifacts are persisted under
``.zf/research/<task_id>/<topic>.md`` and ``docs/artifacts/research/...``
and referenced by State Packet evidence + Context Manifest research kind.

This module supplies the renderer + validator. Writers (research role
LLMs) generate the dict; this module renders Markdown + validates
required fields are non-empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from zf.core.state.atomic_io import atomic_write_text


@dataclass(frozen=True)
class ResearchEvidence:
    """One evidence snippet — must cite source + line numbers."""

    source_path: str
    line_range: str = ""  # e.g. "L42-L56" or "L42"
    snippet: str = ""
    note: str = ""


@dataclass(frozen=True)
class ResearchArtifact:
    """Structured research artifact — what dev/arch/critic produce when
    investigating a question outside the codebase."""

    task_id: str
    topic: str
    research_question: str
    sources: tuple[str, ...] = ()
    evidence: tuple[ResearchEvidence, ...] = ()
    patterns: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    not_answered: tuple[str, ...] = ()
    downstream_impact: str = ""
    generated_at: str = ""
    generated_by: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_artifact(artifact: ResearchArtifact) -> list[str]:
    """Return list of human-readable validation errors. Empty list
    means the artifact is acceptable as published research.

    Required fields:
    - task_id / topic / research_question all non-empty
    - At least one source AND at least one evidence snippet
    - Each evidence has source_path (line_range optional but
      recommended for code citations)
    """
    errors: list[str] = []
    if not artifact.task_id:
        errors.append("task_id is required")
    if not artifact.topic:
        errors.append("topic is required")
    if not artifact.research_question:
        errors.append("research_question is required")
    if not artifact.sources:
        errors.append("sources list must be non-empty")
    if not artifact.evidence:
        errors.append("evidence list must be non-empty (no chat-only research)")
    for i, ev in enumerate(artifact.evidence):
        if not ev.source_path:
            errors.append(f"evidence[{i}].source_path is required")
    return errors


def render_artifact_md(artifact: ResearchArtifact) -> str:
    lines = [
        f"# Research · {artifact.task_id} · {artifact.topic}",
        "",
        "> generated_by: "
        f"{artifact.generated_by or 'unknown'}  ·  "
        f"generated_at: {artifact.generated_at or '(unset)'}",
        "",
        "## Research question",
        artifact.research_question,
        "",
    ]
    if artifact.sources:
        lines.append("## Sources inspected")
        for s in artifact.sources:
            lines.append(f"- {s}")
        lines.append("")
    if artifact.evidence:
        lines.append("## Evidence")
        for ev in artifact.evidence:
            head = f"- `{ev.source_path}`"
            if ev.line_range:
                head += f":{ev.line_range}"
            if ev.note:
                head += f" — {ev.note}"
            lines.append(head)
            if ev.snippet:
                lines.append("  ```")
                for snippet_line in ev.snippet.splitlines()[:20]:
                    lines.append("  " + snippet_line)
                lines.append("  ```")
        lines.append("")
    if artifact.patterns:
        lines.append("## Patterns / constraints observed")
        for p in artifact.patterns:
            lines.append(f"- {p}")
        lines.append("")
    if artifact.risks:
        lines.append("## Risks")
        for r in artifact.risks:
            lines.append(f"- {r}")
        lines.append("")
    if artifact.not_answered:
        lines.append("## Open questions / not answered")
        for q in artifact.not_answered:
            lines.append(f"- {q}")
        lines.append("")
    if artifact.downstream_impact:
        lines.append("## Downstream impact on plan / spec / test")
        lines.append(artifact.downstream_impact)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_research_artifact(
    state_dir: Path,
    artifact: ResearchArtifact,
) -> Path:
    """Persist an artifact under ``.zf/research/<task_id>/<topic>.md``.

    Raises ``ValueError`` when validation fails. Caller MUST handle
    by either fixing inputs or downgrading to a memory.note (research
    artifacts are public artifacts — they must be complete).
    """
    errors = validate_artifact(artifact)
    if errors:
        raise ValueError(
            "research artifact validation failed: " + "; ".join(errors)
        )
    if not artifact.generated_at:
        artifact_with_ts = ResearchArtifact(
            task_id=artifact.task_id,
            topic=artifact.topic,
            research_question=artifact.research_question,
            sources=artifact.sources,
            evidence=artifact.evidence,
            patterns=artifact.patterns,
            risks=artifact.risks,
            not_answered=artifact.not_answered,
            downstream_impact=artifact.downstream_impact,
            generated_at=_now_iso(),
            generated_by=artifact.generated_by,
        )
    else:
        artifact_with_ts = artifact
    target_dir = state_dir / "research" / artifact.task_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{artifact.topic}.md"
    atomic_write_text(target, render_artifact_md(artifact_with_ts))
    return target
