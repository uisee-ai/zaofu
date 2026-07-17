"""Resolve writer fanout result events to their canonical child manifest."""

from __future__ import annotations

from zf.core.events.model import ZfEvent


class WriterFanoutResultBindingMixin:
    def _fanout_result_payload(self, event: ZfEvent) -> dict:
        payload = event.payload if isinstance(event.payload, dict) else {}
        report = payload.get("report")
        promoted = dict(payload)
        if isinstance(report, dict):
            for key in (
                "fanout_id",
                "stage_id",
                "child_id",
                "child_run",
                "run_id",
                "role_instance",
                "status",
                "reason",
                "summary",
                "recommendation",
            ):
                if promoted.get(key) in (None, "") and report.get(key) not in (None, ""):
                    promoted[key] = report.get(key)
        if event.type in {"task.ref.updated", "task.ref.rejected"} and not promoted.get(
            "fanout_id"
        ):
            trigger_event_id = str(
                promoted.get("trigger_event_id") or event.causation_id or ""
            )
            source_event = None
            if trigger_event_id:
                try:
                    source_event = next(
                        item
                        for item in reversed(self.event_log.read_all())
                        if item.id == trigger_event_id
                    )
                except (StopIteration, OSError):
                    source_event = None
            if source_event is not None and source_event.type == "dev.build.done":
                source_payload = (
                    source_event.payload if isinstance(source_event.payload, dict) else {}
                )
                for key in (
                    "fanout_id",
                    "stage_id",
                    "child_id",
                    "child_run",
                    "run_id",
                    "role_instance",
                    "task_map_ref",
                    "source_index_ref",
                    "pdd_id",
                    "feature_id",
                ):
                    value = source_payload.get(key)
                    if value not in (None, ""):
                        promoted.setdefault(key, value)
                promoted.setdefault("role_instance", source_event.actor or "")
        if promoted.get("fanout_id") and (
            promoted.get("child_id") or promoted.get("child_run")
        ):
            return promoted
        target = self._writer_fanout_completion_target(
            event=event,
            payload=promoted,
            current_fanout_id="",
            statuses={"dispatched", "failed", "completed"},
        )
        if target is None:
            return promoted
        manifest, child = target
        enriched = dict(promoted)
        enriched.setdefault("fanout_id", str(manifest.get("fanout_id") or ""))
        enriched.setdefault("stage_id", str(manifest.get("stage_id") or ""))
        enriched.setdefault("trace_id", str(manifest.get("trace_id") or ""))
        enriched.setdefault("child_id", str(child.get("child_id") or ""))
        enriched.setdefault("run_id", str(child.get("run_id") or ""))
        enriched.setdefault("role_instance", str(child.get("role_instance") or ""))
        enriched.setdefault("task_id", str(child.get("task_id") or ""))
        enriched.setdefault(
            "task_map_ref",
            str(child.get("task_map_ref") or manifest.get("task_map_ref") or ""),
        )
        enriched.setdefault(
            "source_index_ref",
            str(child.get("source_index_ref") or manifest.get("source_index_ref") or ""),
        )
        enriched.setdefault("pdd_id", str(manifest.get("pdd_id") or ""))
        enriched.setdefault("feature_id", str(manifest.get("feature_id") or ""))
        enriched.setdefault("scope", str(child.get("scope") or ""))
        enriched.setdefault("workdir", str(child.get("workdir") or ""))
        enriched.setdefault("source_branch", str(child.get("source_branch") or ""))
        return enriched

    def _writer_fanout_completion_target(
        self,
        *,
        event: ZfEvent,
        payload: dict,
        current_fanout_id: str,
        statuses: set[str],
    ) -> tuple[dict, dict] | None:
        if event.type not in {
            "dev.build.done",
            "dev.blocked",
            "dev.failed",
            "task.ref.updated",
            "task.ref.rejected",
        }:
            return None
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        if not task_id:
            return None
        event_task_map_ref = str(payload.get("task_map_ref") or "").strip()
        role_instance = str(
            payload.get("role_instance")
            or (
                payload.get("actor")
                if event.type in {"task.ref.updated", "task.ref.rejected"}
                else None
            )
            or (
                event.actor
                if event.type not in {"task.ref.updated", "task.ref.rejected"}
                else None
            )
            or ""
        ).strip()
        root = self.state_dir / "fanouts"
        if not root.exists():
            return None
        candidates: list[tuple[float, dict, dict]] = []
        for manifest_path in root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_writer_scoped":
                continue
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("task_id") or "") != task_id:
                    continue
                status = str(child.get("status") or "")
                if status not in statuses:
                    continue
                child_role = str(child.get("role_instance") or "")
                if role_instance and child_role and role_instance != child_role:
                    continue
                child_task_map_ref = str(
                    child.get("task_map_ref") or manifest.get("task_map_ref") or ""
                ).strip()
                if (
                    event_task_map_ref
                    and child_task_map_ref
                    and event_task_map_ref != child_task_map_ref
                ):
                    continue
                try:
                    mtime = manifest_path.stat().st_mtime
                except OSError:
                    mtime = 0.0
                if status == "dispatched":
                    mtime += 1000.0
                if fanout_id == current_fanout_id:
                    mtime += 1_000_000_000.0
                candidates.append((mtime, manifest, child))
        if not candidates:
            return None
        _, manifest, child = sorted(candidates, key=lambda item: item[0])[-1]
        return manifest, child


__all__ = ["WriterFanoutResultBindingMixin"]
