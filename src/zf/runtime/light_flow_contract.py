"""Preserve canonical Task constraints when light delivery synthesizes one slice."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.verification_commands import task_contract_verification_commands


def inherit_light_task_contract(
    task_map: dict[str, Any], *, task_store: Any, task_id: str,
    source_refs: Mapping[str, Any],
) -> None:
    expected = str(source_refs.get('task_contract_digest') or '')
    task = task_store.get(task_id) if task_store is not None and task_id else None
    if expected and (task is None or task_workflow_binding_digest(task) != expected):
        raise ValueError('approved root Task contract is missing or changed')
    if task is None:
        return  # Legacy entry without a tracked Task keeps its existing contract.
    source = task.contract
    target = task_map['tasks'][0]
    if source.scope:
        target['allowed_paths'] = list(source.scope)
        target['allowed_paths_reason'] = 'single slice inherits the canonical root Task write scope'
        # The approved single-slice scope owns layout; do not add a guessed
        # tests/ convention that rejects existing root-level product tests.
        task_map.get('shared_conventions', {}).pop('test_path_prefix', None)
    for key in ('exclusions', 'explicit_non_goals', 'acceptance_criteria', 'goal_claim_ids'):
        value = getattr(source, key, None)
        if value:
            target[key] = deepcopy(value)
    if source.acceptance and source.acceptance != 'exit_code=0':
        target['acceptance'] = [source.acceptance]
    validation = source.validation
    if not isinstance(validation, dict) or not validation.get('commands'):
        return
    # Preserve IDs/owners/timeouts and AC links; retain additional configured
    # checks without duplicating the canonical commands under generated IDs.
    inherited = deepcopy(validation)
    commands = task_contract_verification_commands(source)
    inherited['commands'] = commands
    known_commands = {str(item.get('command') or '') for item in commands}
    known_ids = {str(item.get('id') or '') for item in commands}
    for extra in target.get('validation', {}).get('commands', []):
        if extra.get('command') in known_commands:
            continue
        item = deepcopy(extra)
        while item['id'] in known_ids:
            item['id'] += '-extra'
        commands.append(item)
        known_commands.add(item['command'])
        known_ids.add(item['id'])
    target['validation'] = inherited
