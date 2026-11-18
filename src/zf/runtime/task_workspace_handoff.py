"""Release settled task-stage occupants without weakening workspace exclusivity."""
from pathlib import Path

from zf.runtime import environment_usage
from zf.runtime.task_attempt_runtime import active_task_attempt_identities_for_role
from zf.runtime.workflow_operation import reduce_workflow_operations


_ACTIVE = {'requested', 'reserved', 'running', 'suspended'}


def _prior_workspace_transferred(runtime, role, prior_path, metadata, operations):
    """A dispatched Verify may own the previous workspace after this writer exited."""
    try:
        if runtime.transport.is_alive(role.instance_id):
            return False
    except Exception:
        return False
    previous = metadata.get(role.instance_id, {})
    writers = [op for op in operations.values()
               if op.get('role_instance') == role.instance_id
               and op.get('task_stage_session_binding') == previous.get('active_task_stage_binding_key')
               and op.get('task_pipeline_stage') == 'impl' and op.get('status') == 'settled'
               and op.get('semantic_verdict') == 'passed' and op.get('call_result_admitted_event_id')]
    if len(writers) != 1 or previous.get('active_task_workspace') != str(prior_path):
        return False
    if any(op.get('role_instance') == role.instance_id and op.get('status') in _ACTIVE
           for op in operations.values()):
        return False
    writer = writers[0]
    identity = ('workflow_run_id', 'task_id', 'task_map_generation',
                'operation_generation', 'workspace_generation')
    if any(writer.get(key) is None for key in identity):
        return False
    for reader in operations.values():
        reader_meta = metadata.get(reader.get('role_instance'), {})
        if (reader.get('role_instance') != role.instance_id
                and reader.get('task_pipeline_stage') == 'verify' and reader.get('status') == 'running'
                and all(reader.get(key) == writer[key] for key in identity)
                and reader.get('task_stage_session_binding')
                and reader.get('task_stage_session_binding') == reader_meta.get('active_task_stage_binding_key')
                and reader_meta.get('active_task_workspace') == str(prior_path)):
            try:
                return bool(runtime.transport.is_alive(reader['role_instance']))
            except Exception:
                return False
    return False


def ensure_stage_workspace_idle(runtime, registry, role, task, run_id, map_id, project_path, prior_path):
    """Only retire a same-task predecessor whose admitted operation and turn ended."""
    from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting

    events = runtime.event_log.read_all()
    operations = reduce_workflow_operations(events)
    bindings = registry.task_stage_bindings()
    metadata = registry.instance_meta()
    for old_role in runtime.all_roles():
        if old_role.instance_id == role.instance_id:
            continue
        meta = metadata.get(old_role.instance_id, {})
        key = str(meta.get('active_task_stage_binding_key') or '')
        bound = bindings.get(key, {})
        if (meta.get('active_task_workspace') != str(project_path)
                or bound.get('workflow_run_id') != run_id or bound.get('task_id') != str(task.id)):
            continue
        owned = [op for op in operations.values() if op.get('role_instance') == old_role.instance_id]
        if any(op.get('status') in _ACTIVE for op in owned):
            continue
        settled = [op for op in owned if op.get('status') == 'settled'
                   and op.get('call_result_admitted_event_id')
                   and op.get('task_stage_session_binding') == key
                   and op.get('workflow_run_id') == run_id and op.get('task_id') == str(task.id)
                   and op.get('task_map_generation') == map_id]
        if not settled or active_task_attempt_identities_for_role(runtime,
                role_name=old_role.name, instance_id=old_role.instance_id):
            continue
        ids = {op['operation_id'] for op in settled}
        requested = [i for i, event in enumerate(events) if event.type == 'workflow.operation.requested'
                     and event.payload.get('operation_id') in ids]
        closed = [i for i, event in enumerate(events) if event.type == 'provider.turn.closed'
                  and event.actor == old_role.instance_id]
        if not requested or not closed or max(closed) <= max(requested):
            continue
        if any(event.actor == old_role.instance_id and event.type in {
                'provider.turn.started', 'agent.tool.use', 'codex.hook.pre_tool_use', 'agent.text',
        } for event in events[max(closed) + 1:]):
            continue
        # Use the existing lifecycle owner to preserve session and workdir records.
        # The subsequent process-holder probe still rejects surviving children.
        try:
            if runtime.transport.is_alive(old_role.instance_id):
                runtime._suspend_role(old_role, now=runtime._now())
            if runtime.transport.is_alive(old_role.instance_id):
                raise TaskPipelineWaiting('waiting_for_worker_exit', 'predecessor exit is pending')
        except TaskPipelineWaiting:
            raise
        except Exception as exc:
            raise TaskPipelineWaiting('waiting_for_worker_exit', 'predecessor exit cannot be confirmed') from exc
    try:
        candidates = {str(project_path), str(prior_path or '')} - {''}
        if (prior_path and Path(prior_path).resolve() != Path(project_path).resolve()
                and _prior_workspace_transferred(runtime, role, prior_path, metadata, operations)):
            # Verify already crossed the predecessor-exit fence for this exact
            # Task generation. Its lock does not exclude a writer in another Task.
            candidates.discard(str(prior_path))
        for candidate in candidates:
            environment_usage.assert_workspace_idle(Path(candidate))
    except (OSError, ValueError, environment_usage.EnvironmentInUse) as exc:
        raise TaskPipelineWaiting('waiting_for_worker_exit',
                                  'workspace still has a process holder or its exit cannot be confirmed') from exc
