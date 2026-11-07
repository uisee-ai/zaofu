"""Replay only the dependency rewrites already declared by gap amendments."""
from copy import deepcopy
from typing import Any

from zf.runtime.module_gap_replacement import raise_dependency_waves, rewire_superseded_dependents


def task_has_only_replayed_dependency_changes(task_id: str, maps: list[dict[str, Any]]) -> bool:
    """Prove each immutable package edge, including unchanged intermediate maps."""
    for before, after in zip(maps, maps[1:]):
        previous = {row['task_id']: row for row in before.get('tasks', [])}
        current = {row['task_id']: row for row in after.get('tasks', [])}
        old, new = previous.get(task_id), current.get(task_id)
        if old is None or new is None:
            return False
        if old == new:
            continue
        changed = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
        if changed - {'blocked_by', 'dependencies', 'wave'}:
            return False
        amend = after.get('amend')
        if not isinstance(amend, dict) or amend.get('kind') not in {'goal_gap', 'module_parity_gap'}:
            return False
        superseded, gap_ids = amend.get('superseded_task_ids'), amend.get('gap_task_ids')
        if not isinstance(superseded, list) or not superseded or not isinstance(gap_ids, list) or not gap_ids:
            return False
        if any(not isinstance(value, str) or not value for value in [*superseded, *gap_ids]):
            return False
        if (len(set(gap_ids)) != len(gap_ids) or len(set(superseded)) != len(superseded)
                or not set(superseded) <= previous.keys() or set(superseded) & current.keys()):
            return False
        replacements = []
        covered = set()
        for successor in gap_ids:
            row = current.get(successor)
            if row is None:
                return False
            payload, evidence = row.get('payload') or {}, row.get('evidence_contract') or {}
            if not isinstance(payload, dict) or not isinstance(evidence, dict):
                return False
            parents = payload.get('supersedes_task_ids') or []
            if not isinstance(parents, list) or any(not isinstance(v, str) for v in parents):
                return False
            if not set(parents) <= set(superseded):
                return False
            if evidence.get('supersedes_task_ids') is not None and evidence['supersedes_task_ids'] != parents:
                return False
            replacements.append({'task_id': successor, 'supersedes_task_ids': parents})
            covered.update(parents)
        if covered != set(superseded):
            return False
        expected = rewire_superseded_dependents([deepcopy(old)], gap_tasks=replacements)[0]
        replay = [expected if row['task_id'] == task_id else deepcopy(row) for row in after['tasks']]
        raise_dependency_waves(replay)
        if expected != new:
            return False
    return True
