"""Fixed Research's result template and authoring method, not a quality gate."""

from __future__ import annotations

from typing import Any

from zf.runtime.research_templates import FIXED_RESEARCH_TEMPLATE


def prepare_research_synthesis(
    stage_id: str, completion_payload: dict[str, Any],
) -> list[str]:
    """Expose existing research-report ports at the actual synthesis entrypoint."""
    if stage_id != FIXED_RESEARCH_TEMPLATE.pattern_id:
        return []
    report = completion_payload["report"]
    for key, default in {
        "architecture": {},
        "acceptance_matrix": [],
        "test_matrix": [],
        "task_map": [],
        "evidence_refs": [],
        "open_questions": [],
        "prd_prompt_input": "",
        "refactor_prompt_input": "",
    }.items():
        report.setdefault(key, default)
    return [
        "## Research handoff",
        "",
        "The durable research report consumes these structured fields inside `report`: "
        '"architecture", "acceptance_matrix", "test_matrix", "task_map", '
        '"evidence_refs", "open_questions", "prd_prompt_input", "refactor_prompt_input".',
        "Populate them from the admitted child evidence and the requested research scope. "
        "Do not bury their contents only in findings prose; downstream readers use these ports.",
        "Separate observed facts, supported inferences, proposed work and unknowns. "
        "Keep source refs with each material conclusion; do not invent measurements or citations.",
        "For proposed acceptance rows, state the observable criterion and evidence needed. "
        "For proposed test rows, identify the acceptance row, test method and expected result. "
        "Describe proposed work dependencies and scope in task_map. This is research advice, "
        "not an approved implementation plan; do not create Tasks or start another workflow.",
        "Only derive PRD/refactor prompt inputs when relevant to the requested downstream use. "
        "If a port is not applicable or evidence is insufficient, keep it empty and explain "
        "that limitation in open_questions and summary. Never fabricate completeness.",
        "The runtime persists these fields; their presence does not prove semantic quality "
        "or authorize downstream implementation. Preserve dissent and unresolved owner decisions.",
        "",
    ]
