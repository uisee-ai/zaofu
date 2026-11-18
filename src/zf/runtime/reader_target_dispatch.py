"""Pin a Reader target, preserving resource waits across controller restarts."""
from zf.core.config.schema import RoleConfig
from zf.core.events import ZfEvent
from zf.runtime.task_contract_snapshot import TaskContractSnapshotError
from zf.runtime.environment_usage import EnvironmentInUse


def pin_reader_target_or_defer(
    self,
    *,
    role: RoleConfig,
    target_ref: str,
    context,
    child,
    run_id: str,
    causation_id: str,
) -> bool:
    """FIX-9/15①(bizsim r4 F9):reader child 派发前锁定审计对象。

    stage 声明了 target_ref 但渲染为空 → 拒派发(r4 judge 五审即
    审基线树);checkout/HEAD 校验失败 → 拒派发。锁定成功把
    target_commit 写进 child payload 供 briefing/证据绑定消费。
    """
    config = getattr(self, "config", None)
    workflow = getattr(config, "workflow", None)
    stages = getattr(workflow, "stages", []) or []
    stage = next(
        (
            s for s in stages
            if getattr(s, "id", "") == context.stage_id
        ),
        None,
    )
    declared = str(getattr(stage, "target_ref", "") or "").strip() if stage else ""
    cleaned = str(target_ref or "").strip()
    reason = ""
    pinned = ""
    from zf.runtime.reader_target_binding import reader_checkout_ref

    try:
        cleaned = reader_checkout_ref(self.state_dir, child.payload or {}, cleaned)
    except TaskContractSnapshotError as exc:
        reason = f"target_binding_invalid: {exc}"
    unrendered = "${" in cleaned or "{{" in cleaned
    if not reason and declared and (not cleaned or unrendered):
        reason = "target_ref_unresolved"
        cleaned = "" if unrendered else cleaned
    elif not reason and cleaned and not self._fanout_target_ref_is_project_path(cleaned):
        from zf.runtime.workdirs import WorkdirManager

        try:
            pinned = WorkdirManager(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
            ).pin_reader_target(role, cleaned)
        except (EnvironmentInUse, TimeoutError):
            _release_idle_owner(self, role, pending_run_id=run_id,
                                workflow_run_id=context.trace_id, stage_id=context.stage_id)
            from zf.runtime.reader_wait_recovery import exhaust_reader_wait
            if exhaust_reader_wait(
                self, role=role, context=context, child=child, run_id=run_id,
                target_ref=cleaned, causation_id=causation_id,
            ):
                return False
            try:
                alive = self.transport.is_alive(role.instance_id)
            except Exception:
                alive = None  # Unknown transport state is not evidence of death.
            self._emit_fanout_dispatch_deferred_once(
                fanout_id=context.fanout_id, trace_id=context.trace_id,
                stage_id=context.stage_id, child_id=child.child_id, run_id=run_id,
                role_instance=role.instance_id, prompt_kind="reader",
                reason="reader_environment_busy", state="waiting_environment",
                alive=alive, dispatchable=False, causation_id=causation_id,
            )
            return False
        except Exception as exc:
            reason = f"pin_failed: {exc}"
    if reason:
        failure_payload = {
            "fanout_id": context.fanout_id,
            "trace_id": context.trace_id,
            "stage_id": context.stage_id,
            "child_id": child.child_id,
            "run_id": run_id,
            "role_instance": role.instance_id,
            "target_ref": cleaned,
            "reason": reason,
        }
        self.event_writer.append(ZfEvent(
            type="fanout.child.workdir_mismatch",
            actor="zf-cli",
            payload=failure_payload,
            causation_id=causation_id,
            correlation_id=context.trace_id,
        ))
        self.event_writer.append(ZfEvent(
            type="fanout.child.failed",
            actor="zf-cli",
            payload={
                **failure_payload,
                "failure_class": "reader_workdir_mismatch",
                "failure_kind": "infra",
            },
            causation_id=causation_id,
            correlation_id=context.trace_id,
        ))
        return False
    if pinned and isinstance(child.payload, dict):
        child.payload["target_commit"] = pinned
    return True


def _release_idle_owner(runtime, role: RoleConfig, *, pending_run_id: str = "",
                        workflow_run_id: str = "", stage_id: str = "") -> None:
    """Release only the managed idle reader; unknown/active holders keep waiting."""
    from zf.runtime.workflow_operation import reduce_workflow_operations
    if not runtime._role_is_on_demand(role):
        return
    try:
        events = runtime.event_log.read_all()
        operations = reduce_workflow_operations(events)
        for op in operations.values():
            if op.get('role_instance') != role.instance_id or op.get('status') not in {
                'requested', 'reserved', 'running', 'suspended',
            }:
                continue
            # Preparing a request before provider delivery does not make an idle
            # provider its consumer. Keep any previously delivered call fenced.
            never_delivered = (
                bool(pending_run_id and workflow_run_id and stage_id)
                and op.get('status') == 'requested'
                and op.get('operation_type') == 'fanout_reader_child'
                and op.get('active_attempt_id') == pending_run_id
                and op.get('workflow_run_id') == workflow_run_id
                and op.get('parent_stage_id') == stage_id
                and not any(
                    event.type in {'workflow.operation.started', 'workflow.operation.retry_started',
                                   'fanout.child.dispatched'}
                    and (event.payload.get('operation_id') == op.get('operation_id')
                         or (event.payload.get('role_instance') == role.instance_id
                             and event.payload.get('run_id') == pending_run_id))
                    for event in events
                )
            )
            if not never_delivered:
                return
        if (runtime._active_provider_turn(role.instance_id)
                or runtime._active_fanout_child_for_instance(role.instance_id) is not None
                or runtime._active_task_for_instance(role.instance_id) is not None):
            return
        if runtime.transport.is_alive(role.instance_id):
            runtime._suspend_role(role, now=runtime._now())
    except Exception:
        # Lifecycle already records failure; the actual holder check on the
        # next tick remains authoritative and cannot be waived here.
        return
