"""Mechanical reconciliation of declared Plan inputs before a local review.

This validates declared identities and mappings, not the meaning or quality of
source text. The independent reviewer still owns semantic coverage.
"""
from __future__ import annotations

from typing import Any, Mapping


def declared_coverage_complete(task_map: Mapping[str, Any], source_index: Mapping[str, Any],
                               ledger: Mapping[str, Any], inventory: Mapping[str, Any], *,
                               affected: set[str]) -> bool:
    """Reject incomplete or ambiguous declarations instead of guessing scope."""
    if (source_index.get('schema_version') != 'source-index.v1'
            or ledger.get('schema_version') != 'coverage-ledger.v1' or not affected):
        return False
    for body in (source_index, ledger, inventory):
        if (body.get('complete') is False or body.get('truncated') is True
                or body.get('next_cursor') not in (None, '')):
            return False
    tasks, mapped = task_map.get('tasks'), source_index.get('tasks')
    trace, entries = source_index.get('traceability'), ledger.get('entries')
    if not all(isinstance(rows, list) and rows for rows in (tasks, mapped, trace, entries)):
        return False

    def unique(rows: list[Any], key: str) -> dict[str, Mapping[str, Any]] | None:
        result: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                return None
            identity = row.get(key)
            if not isinstance(identity, str) or not identity or identity in result:
                return None
            result[identity] = row
        return result

    task_rows = unique(tasks, 'task_id')
    index_rows = unique(mapped, 'task_id')
    source_rows = unique(trace, 'source_id')
    coverage = unique(entries, 'source_id')
    if (not task_rows or not index_rows or not source_rows or not coverage
            or set(task_rows) != set(index_rows) or set(source_rows) != set(coverage)
            or not affected <= set(task_rows)):
        return False
    visual_lists = [inventory.get(name) for name in ('pages', 'forms') if name in inventory]
    if not visual_lists or any(not isinstance(rows, list) for rows in visual_lists):
        return False
    for rows in visual_lists:
        for row in rows:
            if not isinstance(row, Mapping):
                return False
            task_ids, images = row.get('task_ids', []), row.get('reference_images', [])
            if (not isinstance(task_ids, list) or not isinstance(images, list)
                    or any(not isinstance(task, str) or task not in task_rows for task in task_ids)
                    or any(not isinstance(image, str) or not image for image in images)):
                return False
            # Current source-read receipts cover text, not viewing image pixels.
            # Never narrow a potentially visual review on a text-only claim.
            if images and (not task_ids or affected.intersection(task_ids)):
                return False
    assets = inventory.get('visual_assets', [])
    if not isinstance(assets, list):
        return False
    for asset in assets:
        if not isinstance(asset, Mapping):
            return False  # An unassigned image may apply to any changed task.
        task_ids = asset.get('task_ids')
        if (not isinstance(task_ids, list) or not task_ids
                or any(not isinstance(task, str) or task not in task_rows for task in task_ids)
                or affected.intersection(task_ids)):
            return False
    for row in source_rows.values():
        if not isinstance(row.get('source_ref'), str) or not row['source_ref']:
            return False
    for source_id, row in coverage.items():
        status = row.get('disposition')
        tasks_for_source = row.get('task_ids', [])
        if (not isinstance(status, str) or not status or not isinstance(tasks_for_source, list)
                or any(not isinstance(task, str) or task not in task_rows for task in tasks_for_source)
                or len(set(tasks_for_source)) != len(tasks_for_source)):
            return False
        resolved = {'covered', 'excluded', 'out_of_scope', 'not_applicable', 'explicit_non_goal'}
        if status not in resolved and (not tasks_for_source or affected.intersection(tasks_for_source)):
            return False
        if status != 'covered' and (not isinstance(row.get('reason'), str) or not row['reason'].strip()):
            return False
        if status == 'covered' and not tasks_for_source:
            return False
    for task_id in affected:
        task, index = task_rows[task_id], index_rows[task_id]
        refs = task.get('source_refs', [])
        indexed_refs = index.get('source_refs', [])
        if (not isinstance(refs, list) or not isinstance(indexed_refs, list)
                or not refs or any(not isinstance(ref, str) or not ref for ref in refs)
                or any(not isinstance(ref, str) or not ref for ref in indexed_refs)
                or not set(refs) <= set(indexed_refs)):
            return False
        criteria = task.get('acceptance_criteria', [])
        declared = index.get('acceptance_ids', [])
        if not isinstance(criteria, list) or not isinstance(declared, list):
            return False
        ids = [row.get('id') for row in criteria if isinstance(row, Mapping)]
        if len(ids) != len(criteria) or any(not isinstance(item, str) or not item for item in ids):
            return False
        if any(not isinstance(item, str) or not item for item in declared) or not set(ids) <= set(declared):
            return False
        relevant = {source_id: row for source_id, row in coverage.items() if task_id in row.get('task_ids', [])}
        if not relevant:
            return False
        if any(not any(source_rows[source_id]['source_ref'] == ref for source_id in relevant) for ref in refs):
            return False
        linked_ac: set[str] = set()
        for row in relevant.values():
            values = row.get('acceptance_ids', [])
            if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
                return False
            linked_ac.update(values)
        if not set(ids) <= linked_ac:
            return False
    return True
