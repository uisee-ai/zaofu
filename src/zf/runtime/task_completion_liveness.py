"""Task liveness guards backed by admitted writer completion evidence."""

from __future__ import annotations


class TaskCompletionLivenessMixin:
    def _task_has_admitted_writer_completion(self, task_id: str) -> bool:
        """Return true when the latest writer dispatch already settled."""

        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        # Task Pipeline owns already-admitted terminal outcomes, including
        # blocked/abstained results awaiting recovery. They are not lost workers.
        from zf.runtime.workflow_operation import reduce_workflow_operations

        dispatch = next((e for e in reversed(events) if e.type in {"task.dispatched", "fanout.child.dispatched"}
                         and str(e.task_id or e.payload.get("task_id") or "") == task_id), None)
        if dispatch is not None and dispatch.type == "task.dispatched" and dispatch.payload.get("operation_id"):
            operation = reduce_workflow_operations(events).get(dispatch.payload["operation_id"], {})
            return bool(
                operation.get("task_id") == task_id
                and operation.get("task_pipeline_stage")
                and operation.get("status") == "settled"
                and operation.get("call_result_admitted_event_id")
                and operation.get("admitted_control_result_ref")
                and operation.get("dispatch_id") == dispatch.payload.get("dispatch_id")
            )
        latest_dispatch = -1
        latest_completion = -1
        for index, event in enumerate(events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            event_task_id = str(event.task_id or payload.get("task_id") or "")
            if event_task_id != task_id:
                continue
            if event.type == "fanout.child.dispatched":
                latest_dispatch = index
                latest_completion = -1
                continue
            if index <= latest_dispatch or event.type != "fanout.child.completed":
                continue
            admitted_ref = payload.get("admitted_call_result_ref")
            if (
                isinstance(admitted_ref, dict)
                and str(admitted_ref.get("ref") or "").strip()
                and str(admitted_ref.get("sha256") or "").strip()
            ):
                latest_completion = index
        return latest_dispatch >= 0 and latest_completion > latest_dispatch
