"""Bind an explicitly approved reader retry to a committed descendant repair."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from zf.runtime.task_contract_snapshot import (
    build_target_snapshot, descriptor_from_payload, hydrate_task_contract_snapshot,
    hydrate_target_snapshot, target_descriptor_from_payload, target_payload_fields,
    write_target_snapshot,
)


def bind_reader_repair_target(
    *, project_root: Path, state_dir: Path, trigger: Any,
    repair: Any, source_event_id: str,
) -> None:
    """Keep contract authority; create a new immutable target, never move refs."""
    if not isinstance(repair, dict) or set(repair) != {'source_commit', 'commit', 'ref'}:
        raise ValueError('repair_target requires exactly source_commit, commit, ref')
    if trigger.type != 'flow.discovery.requested':
        raise ValueError('repair_target is only supported for a contract-bound discovery reader')
    payload = trigger.payload
    # Compact legacy triggers pin the candidate; dispatch supplies the target
    # snapshot later. An explicit conflicting target still fails below.
    old = str(payload.get('target_commit') or payload.get('candidate_head_commit') or '')
    new = str(repair.get('commit') or '')
    ref = str(repair.get('ref') or '')
    if not re.fullmatch(r'[0-9a-f]{40}', old) or not re.fullmatch(r'[0-9a-f]{40}', new):
        raise ValueError('repair_target requires full commit identities')
    if repair.get('source_commit') != old or old != payload.get('candidate_head_commit'):
        raise ValueError('repair_target source differs from the failed reader target')
    if new == old or not ref.startswith('refs/heads/'):
        raise ValueError('repair_target requires a changed commit and explicit local branch ref')

    def git(*args: str) -> str:
        try:
            return subprocess.check_output(
                ['git', '-C', str(project_root), *args], text=True,
                stderr=subprocess.PIPE, timeout=10,
            ).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('repair_target git identity/ancestry check failed') from exc

    git('check-ref-format', ref)
    if git('rev-parse', '--verify', f'{ref}^{{commit}}') != new:
        raise ValueError('repair_target ref does not resolve to the supplied commit')
    git('merge-base', '--is-ancestor', old, new)
    descriptor = descriptor_from_payload(payload)
    snapshot = hydrate_task_contract_snapshot(state_dir, descriptor, expected={
        key: payload.get(key) for key in ('workflow_run_id', 'task_id', 'task_map_generation')
    })
    if payload.get('target_snapshot_ref') or payload.get('target_snapshot_digest'):
        hydrate_target_snapshot(state_dir, target_descriptor_from_payload(payload), expected={
            'target_commit': old, 'contract_snapshot_ref': descriptor['ref'],
            'contract_snapshot_digest': descriptor['sha256'],
        })
    target = write_target_snapshot(state_dir, build_target_snapshot(
        descriptor, target_commit=new, contract_snapshot=snapshot,
    ), source_event_id=source_event_id)
    payload.update(target_payload_fields(target))
    payload.update(target_commit=new, candidate_head_commit=new, candidate_ref=ref,
                   reader_repair_target=dict(repair))
