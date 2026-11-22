"""Read-only host recovery declarations and scoped evidence for the diagnostician."""
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def host_recovery_context(
    state_dir: Path, events: list[ZfEvent], config: ZfConfig | None, run_id: str,
    *, operation_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    def policy(name: str) -> dict[str, Any]:
        value = getattr(config.runtime, name, None) if config else None
        result = {'enabled': value.enabled if value else None}
        if value:
            for key in ('approval_ref', 'commands', 'image', 'max_executions_per_run', 'timeout_seconds',
                        'max_artifact_bytes', 'max_total_evidence_bytes'):
                if hasattr(value, key):
                    result[key] = getattr(value, key)
        return result

    effects = []
    for event in events:
        p = event.payload
        if (not run_id or event.origin != 'kernel' or p.get('workflow_run_id') != run_id
                or (operation_ids is not None and p.get('operation_id') not in operation_ids)
                or p.get('action') != 'task-host-assist'
                or not event.type.startswith('runtime.action.attempt.')):
            continue
        row = {'event_id': event.id, 'event_type': event.type, 'at': event.ts,
            **{key:p.get(key) for key in ('operation_id','kind','manifest_digest','status','reason','effect_status')}}
        ref = p.get('receipt_ref')
        if isinstance(ref, dict):
            row['receipt_ref'] = ref
            try:
                hydrated = hydrate_sidecar_ref(state_dir, ref)
                body = hydrated.payload
                if (not hydrated.ok or not isinstance(body, dict)
                        or body.get('schema_version') != 'task-host-receipt.v1'
                        or body.get('workflow_run_id') != run_id
                        or body.get('operation_id') != p.get('operation_id')
                        or body.get('kind') != p.get('kind')):
                    raise ValueError('host receipt identity or digest is unavailable')
                row.update(receipt_status='observed', **{key:body.get(key) for key in (
                    'command_id', 'command', 'exit_code', 'target_commit', 'source_stable',
                    'execution_origin', 'policy_digest', 'evidence_status', 'collection_error',
                    'output_ref', 'output_truncated')})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                row.update(receipt_status='unavailable', receipt_error=str(exc))
        effects.append(row)
    return {'workflow_run_id':run_id,
        'host_verification':policy('host_verification'),
        'task_publication':policy('task_publication'),
        'execution_owner':'Run Manager runtime controlled host recovery',
        'effects':effects,
        'as_of_event_id':events[-1].id if events else '',
        'limits':'Configuration declares authority, not execution or current eligibility. '
            'A completed command is not a product verdict. Compare commands with the bound contract; '
            'partial receipts do not prove the remaining host queue is absent. This is a snapshot: '
            'reconcile newer scoped action events and their receipts before recommending escalation. '
            'No human gate is released and no new authority is granted by this context.'}
