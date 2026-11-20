"""Prove a reused lane's live turn belongs to an independent task workspace."""
from collections.abc import Mapping
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.environment_usage import EnvironmentInUse, assert_workspace_idle
from zf.runtime.workflow_operation import reduce_workflow_operations


def unrelated_task_turn_isolated(service, operation: Mapping, root: Path,
                                 turn: object, events: list[ZfEvent]) -> bool:
    """Unknown bindings stay blocked; role reuse alone is never sufficient."""
    if not isinstance(turn, Mapping) or operation.get('task_pipeline_stage') not in {'impl', 'verify'}:
        return False
    role = operation.get('role_instance')
    submits = [e for e in events if e.type == 'codex.hook.user_prompt_submit'
               and e.origin == 'kernel' and e.actor == role
               and e.payload.get('session_id') == turn.get('session_id')
               and e.payload.get('turn_id') == turn.get('turn_id')]
    if len(submits) != 1 or not submits[0].causation_id:
        return False
    submit = submits[0]
    dispatch = next((e for e in events if e.id == submit.causation_id), None)
    if (dispatch is None or dispatch.type != 'task.pipeline.stage.dispatched'
            or dispatch.origin != 'kernel' or dispatch.actor != 'orchestrator'):
        return False
    payload = dispatch.payload
    owners = [op for op in reduce_workflow_operations(events).values()
              if op.get('role_instance') == role
              and op.get('status') in {'requested', 'reserved', 'running', 'suspended'}]
    if (len(owners) != 1 or owners[0].get('status') != 'running'
            or owners[0].get('task_pipeline_stage') not in {'impl', 'verify'}
            or owners[0].get('operation_id') == operation.get('operation_id')
            or owners[0].get('task_id') == operation.get('task_id')
            or any(owners[0].get(k) != payload.get(k) or not payload.get(k)
                   for k in ('operation_id', 'task_id', 'role_instance', 'workflow_run_id', 'dispatch_id'))
            or payload.get('workflow_run_id') != operation.get('workflow_run_id')
            or submit.correlation_id != payload.get('workflow_run_id')
            or submit.payload.get('cwd') != payload.get('workdir')):
        return False
    try:
        other = Path(payload['workdir'])
        if (not other.is_absolute() or other.resolve(strict=True) != other
                or not other.is_relative_to(service.state_dir.resolve())
                or other.is_relative_to(root) or root.is_relative_to(other)):
            return False
        # Includes surviving provider/tool children; shared dependency readers
        # in the other workspace do not imply a writer in this workspace.
        assert_workspace_idle(root)
    except (EnvironmentInUse, OSError, ValueError, KeyError, TypeError):
        return False
    return True
