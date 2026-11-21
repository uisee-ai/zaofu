"""Preserve explicitly copied canonical Task rows through a repair amendment."""
from copy import deepcopy


def canonical_gap_rows(base: dict, gaps: list[dict], *, target_commit: str) -> list[dict]:
    original = {row["task_id"]: row for row in base.get("tasks", [])}
    out = []
    for gap in gaps:
        if not isinstance(gap, dict):
            raise ValueError("canonical replacement must be a Task Map row")
        parents = gap.get("supersedes_task_ids")
        if (not isinstance(parents, list) or len(parents) != 1 or not isinstance(parents[0], str)
                or not isinstance(gap.get("task_id"), str) or parents[0] not in original
                or gap.get("task_id") in original or not gap.get("task_id") or not target_commit):
            raise ValueError("canonical replacement requires a new id and one bound predecessor")
        expected = deepcopy(original[parents[0]])
        expected.update(task_id=gap["task_id"], base_commit=target_commit, supersedes_task_ids=parents)
        if gap != expected:
            raise ValueError("canonical replacement changed its predecessor contract")
        row = deepcopy(gap)
        # The legacy gap validator consumes a plural provenance alias. It is
        # derived from the bound singular source, never invented by the Agent.
        if not row.get("source_refs") and row.get("source_ref"):
            row["source_refs"] = [row["source_ref"]]
        row["payload"] = {**row.get("payload", {}), "supersedes_task_ids": list(parents)}
        evidence = row.get("evidence_contract")
        if isinstance(evidence, dict) and "supersedes_task_ids" in evidence:
            evidence["supersedes_task_ids"] = list(parents)
        for command in (row.get("validation") or {}).get("commands", []):
            if command.get("producer_task_id") == parents[0]:
                command["producer_task_id"] = row["task_id"]
        out.append(row)
    return out
