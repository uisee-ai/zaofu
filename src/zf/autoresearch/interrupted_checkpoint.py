"""Durable diagnosis ownership and explicit closure after a proven owner exit."""
from __future__ import annotations

import os
from pathlib import Path

from zf.core.state.locks import locked_path
from zf.runtime.sidecar_refs import write_sidecar_json


def _identity(pid: int) -> dict:
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return {'owner_pid': pid, 'owner_start_ticks': fields[19],
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'process_state': fields[0]}


def checkpoint_started(writer, *, state_dir: Path, request_id: str, command: list[str], worktree: Path):
    identity = _identity(os.getpid())
    body = {'schema_version': 'diagnosis-checkpoint.v1', 'loop_request_id': request_id,
            'phase': 'execution', 'worktree': str(worktree), 'command': command,
            'interruption_policy': 'preserve_and_escalate', **identity}
    # request id is never used as an unchecked path segment.
    from hashlib import sha256
    key = sha256(request_id.encode()).hexdigest()
    ref = write_sidecar_json(state_dir, f'diagnostics/autoresearch/{key}/checkpoint.json', body,
        kind='diagnosis_checkpoint', schema_version='diagnosis-checkpoint.v1',
        created_by='zf-autoresearch-resident', required=True)
    return writer.emit('autoresearch.loop.started', actor='zf-autoresearch-resident',
                       payload={**body, 'checkpoint_ref': ref})


def owner_gone(payload: dict) -> bool:
    try:
        pid = int(payload['owner_pid'])
        if pid <= 1 or not str(payload['owner_start_ticks']).isdigit() or not payload['boot_id']:
            return False
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if boot_id != payload['boot_id']:
            return True
        try:
            observed = _identity(pid)
        except FileNotFoundError:
            return True
        return observed['owner_start_ticks'] != payload['owner_start_ticks'] or observed['process_state'] == 'Z'
    except (KeyError, OSError, ValueError, IndexError):
        return False  # missing identity is not proof of exit


def reconcile_interrupted(writer, *, state_dir: Path) -> int:
    with locked_path(state_dir / 'locks' / 'diagnosis-interruption'):
        events = writer.event_log.read_all()
        closed = {e.payload.get('loop_request_id') for e in events if e.type in {
            'autoresearch.loop.completed', 'autoresearch.loop.failed', 'autoresearch.loop.skipped'}}
        count = 0
        for event in events:
            p = event.payload
            request = p.get('loop_request_id')
            if event.type != 'autoresearch.loop.started' or request in closed or not owner_gone(p):
                continue
            # Never replay an arbitrary saved command or clean a dirty worktree.
            # A new bounded repair decision can consume this exact checkpoint.
            writer.emit('autoresearch.loop.failed', actor='zf-autoresearch-resident', causation_id=event.id,
                payload={'loop_request_id': request, 'reason': 'diagnosis_owner_interrupted',
                    'returncode': 125, 'checkpoint_ref': p.get('checkpoint_ref'),
                    'worktree_lifecycle': {'status': 'retained', 'worktree': p.get('worktree'),
                        'reason': 'interrupted_before_archive'}, 'archive_refs': {},
                    'resume_condition': 'new bounded repair decision bound to retained checkpoint'})
            writer.emit('human.escalate', actor='zf-autoresearch-resident', causation_id=event.id,
                payload={'reason': 'diagnosis_owner_interrupted', 'loop_request_id': request,
                         'checkpoint_ref': p.get('checkpoint_ref'), 'owner_route': 'run_manager',
                         'blocking_scope': 'diagnosis'})
            closed.add(request)
            count += 1
        return count
