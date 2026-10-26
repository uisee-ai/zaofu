"""Resolve a Refactor Task Map from ports, reviewed refs, or a legacy body."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.plan_artifact_ports import canonical_plan_port_name, coerce_plan_port_descriptors
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def resolve_refactor_task_map(
    state_dir: Path, *, legacy: Any, ports: Any, reviewed: dict[str, Any] | None = None,
) -> Any:
    selected = [port for port in coerce_plan_port_descriptors(ports)
        if canonical_plan_port_name(str(port.get('logical_name') or port.get('artifact_kind')
                                       or port.get('kind') or '')) == 'task_map']
    if not selected:
        if legacy not in (None, '', [], {}):
            return legacy
        return _reviewed_task_map(state_dir, reviewed or {})
    if len(selected) != 1:
        raise ValueError('duplicate task_map ports')
    port = selected[0]
    body = port.get('body')
    if not isinstance(body, dict):
        descriptor = {'ref': str(port.get('ref') or ''),
                      'sha256': str(port.get('sha256') or port.get('digest') or '')}
        if not all(descriptor.values()):
            raise ValueError('task_map port requires body or ref/digest')
        body = hydrate_sidecar_ref(state_dir, descriptor).payload
    if not isinstance(body, dict) or not isinstance(body.get('tasks'), list):
        raise ValueError('task_map port requires an object with tasks')
    if legacy not in (None, '', [], {}):
        comparable = {'tasks': legacy} if isinstance(legacy, list) else legacy
        if comparable != body:
            raise ValueError('legacy task_map and canonical task_map port disagree')
    return body


def _reviewed_task_map(state_dir: Path, reviewed: dict[str, Any]) -> Any:
    ref = str(reviewed.get('plan_synth_contract_ref') or '')
    if not ref:
        return None
    digest = str(reviewed.get('plan_synth_contract_digest') or '')
    if not digest or reviewed.get('reviewed_plan_candidate_digest') != digest:
        raise ValueError('Task Map is not bound to the reviewed Plan candidate')
    contract = hydrate_sidecar_ref(state_dir, {'ref': ref, 'sha256': digest}).payload
    if not isinstance(contract, dict):
        raise ValueError('reviewed Plan contract must be an object')
    if (contract.get('schema_version') != 'plan-synth-contract.v1'
            or contract.get('fanout_id') != reviewed.get('fanout_id')):
        raise ValueError('reviewed Plan contract identity mismatch')
    sources = contract.get('source_bindings') or []
    children = contract.get('child_bindings') or []
    if not isinstance(sources, list) or not isinstance(children, list) or not all(
        isinstance(row, dict) for row in [*sources, *children]
    ):
        raise ValueError('reviewed Plan source bindings must be object lists')
    maps = []
    for child in children:
        matches = [source for source in sources
            if source.get('source_id') == child.get('source_id')
            and source.get('artifact_id') == child.get('artifact_id')
            and source.get('sha256') == child.get('sha256') and source.get('ref')]
        if len(matches) != 1:
            raise ValueError('reviewed child report requires one exact source ref')
        report = hydrate_sidecar_ref(state_dir, matches[0]).payload
        if not isinstance(report, dict):
            raise ValueError('reviewed child report must be an object')
        task_map_ref = str(report.get('task_map_ref') or '')
        if not task_map_ref:
            continue
        bound = [source for source in sources if source.get('ref') == task_map_ref]
        if not bound or len({source.get('sha256') for source in bound}) != 1:
            raise ValueError('Task Map ref is absent or ambiguous in reviewed sources')
        body = hydrate_sidecar_ref(state_dir, bound[0]).payload
        if not isinstance(body, dict) or not isinstance(body.get('tasks'), list):
            raise ValueError('reviewed Task Map requires an object with tasks')
        maps.append(body)
    if len(maps) != 1:
        raise ValueError('reviewed Plan requires one unambiguous Task Map producer')
    return maps[0]
