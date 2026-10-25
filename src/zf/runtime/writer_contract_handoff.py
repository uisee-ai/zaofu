"""Typed contract handoff reconciliation for writer fanout results."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.impl_self_check import (
    ImplSelfCheckError,
    descriptor_from_payload as self_check_descriptor_from_payload,
    hydrate_impl_self_check,
    normalize_impl_self_check,
    self_check_payload_fields,
    write_impl_self_check,
)
from zf.runtime.task_contract_snapshot import (
    TASK_CONTRACT_AUTHORITY_FIELDS,
    TaskContractSnapshotError,
    build_target_snapshot,
    build_task_contract_snapshot,
    contract_snapshot_identity_fields,
    current_task_contract_identity,
    descriptor_from_payload,
    hydrate_target_snapshot,
    hydrate_task_contract_snapshot,
    snapshot_payload_fields,
    target_payload_fields,
    target_descriptor_from_payload,
    write_target_snapshot,
    write_task_contract_snapshot,
)


_HANDOFF_IDENTITY_FAILURE_PREFIX = "writer contract handoff snapshot failed:"


def recoverable_writer_handoff_failure(event: ZfEvent) -> bool:
    """Return whether one failed terminal may be reconciled once."""

    if event.type != "fanout.child.failed":
        return False
    payload = event.payload if isinstance(event.payload, dict) else {}
    if str(payload.get("failure_class") or "") != "verifier_contract_failure":
        return False
    reason = str(payload.get("reason") or "")
    if not reason.startswith(_HANDOFF_IDENTITY_FAILURE_PREFIX):
        return False
    try:
        attempt = int(payload.get("handoff_recovery_attempt") or 0)
    except (TypeError, ValueError):
        return False
    return attempt < 1


class WriterContractHandoffMixin:
    """Resolve source-generation identity into the current typed handoff."""

    @staticmethod
    def _nonempty_payload_merge(base: dict, overlay: dict) -> dict:
        merged = dict(base)
        for key, value in overlay.items():
            if value not in (None, ""):
                merged[key] = value
        return merged

    def _writer_completion_source_payload(self, event: ZfEvent) -> dict:
        payload = event.payload if isinstance(event.payload, dict) else {}
        source = dict(payload)
        report = payload.get("report")
        if isinstance(report, dict):
            source = self._nonempty_payload_merge(report, source)
        if event.type not in {"task.ref.updated", "task.ref.rejected"}:
            return source
        trigger_event_id = str(
            source.get("trigger_event_id") or event.causation_id or ""
        ).strip()
        if not trigger_event_id:
            return source
        try:
            trigger = next(
                item
                for item in reversed(self.event_log.read_all())
                if item.id == trigger_event_id
            )
        except (StopIteration, OSError):
            return source
        if trigger.type != "dev.build.done":
            return source
        trigger_payload = (
            trigger.payload if isinstance(trigger.payload, dict) else {}
        )
        trigger_source = dict(trigger_payload)
        trigger_report = trigger_payload.get("report")
        if isinstance(trigger_report, dict):
            trigger_source = self._nonempty_payload_merge(
                trigger_report,
                trigger_source,
            )
        trigger_commit = str(trigger_source.get("source_commit") or "").strip()
        ref_commit = str(source.get("source_commit") or "").strip()
        if trigger_commit and ref_commit and trigger_commit != ref_commit:
            raise TaskContractSnapshotError(
                "task-ref source commit does not match triggering completion"
            )
        return self._nonempty_payload_merge(trigger_source, source)

    def _writer_source_fanout_child(
        self,
        source_payload: dict,
        *,
        task_id: str,
    ) -> dict:
        fanout_id = str(source_payload.get("fanout_id") or "").strip()
        if not fanout_id:
            return {}
        manifest = self._fanout_manifest(fanout_id)
        if not manifest or manifest.get("topology") != "fanout_writer_scoped":
            return {}
        child_id = str(
            source_payload.get("child_id")
            or source_payload.get("child_run")
            or ""
        ).strip()
        if child_id:
            child = self._fanout_child(manifest, child_id)
            if child and str(child.get("task_id") or "") == task_id:
                return child
            return {}
        matches = [
            item
            for item in manifest.get("children", []) or []
            if isinstance(item, dict)
            and str(item.get("task_id") or "") == task_id
        ]
        return matches[0] if len(matches) == 1 else {}

    @staticmethod
    def _writer_child_base_commit(child: dict) -> str:
        child_payload = (
            child.get("payload")
            if isinstance(child.get("payload"), dict)
            else {}
        )
        return str(
            child.get("dispatch_base_commit")
            or child.get("base_commit")
            or child_payload.get("dispatch_base_commit")
            or child_payload.get("base_commit")
            or ""
        ).strip()

    def _writer_dispatch_base_commit(
        self,
        *,
        source_payload: dict,
        child: dict,
        source_snapshot: dict | None,
        task_id: str,
    ) -> str:
        source_child = self._writer_source_fanout_child(
            source_payload,
            task_id=task_id,
        )
        source_child_base = self._writer_child_base_commit(source_child)
        current_child_base = self._writer_child_base_commit(child)
        snapshot_base = str(
            (source_snapshot or {}).get("base_commit") or ""
        ).strip()
        dispatch_id = str(
            source_payload.get("dispatch_id")
            or source_payload.get("attempt_id")
            or source_payload.get("run_id")
            or ""
        ).strip()
        dispatch_base = ""
        if dispatch_id:
            try:
                dispatch_event = next(
                    item
                    for item in reversed(self.event_log.read_all())
                    if item.type == "task.dispatched"
                    and str((item.payload or {}).get("dispatch_id") or "")
                    == dispatch_id
                    and str(item.task_id or "") == task_id
                )
            except (StopIteration, OSError):
                dispatch_event = None
            if dispatch_event is not None and isinstance(
                dispatch_event.payload,
                dict,
            ):
                dispatch_base = str(
                    dispatch_event.payload.get("base_git_head")
                    or dispatch_event.payload.get("base_commit")
                    or ""
                ).strip()

        authoritative = source_child_base or dispatch_base or snapshot_base
        if authoritative:
            for name, value in (
                ("source fanout", source_child_base),
                ("task dispatch", dispatch_base),
                ("contract snapshot", snapshot_base),
            ):
                if value and value != authoritative:
                    raise TaskContractSnapshotError(
                        "writer dispatch base lineage mismatch: "
                        f"{name} has {value!r}, expected {authoritative!r}"
                    )
            return authoritative
        if current_child_base:
            return current_child_base
        raise TaskContractSnapshotError(
            f"adopted writer completion lacks dispatch base commit for {task_id}"
        )

    @staticmethod
    def _reported_contract_identity(source_payload: dict) -> tuple[str, str]:
        self_check = (
            source_payload.get("impl_self_check")
            if isinstance(source_payload.get("impl_self_check"), dict)
            else {}
        )
        return (
            str(
                source_payload.get("contract_revision")
                or self_check.get("contract_revision")
                or ""
            ).strip(),
            str(
                source_payload.get("task_map_generation")
                or self_check.get("task_map_generation")
                or ""
            ).strip(),
        )

    def _writer_completion_matches_current_contract(
        self,
        *,
        event: ZfEvent,
        task_id: str,
        task_map_ref: str,
    ) -> bool:
        """Reject typed cross-generation adoption from an obsolete contract."""

        source_payload = self._writer_completion_source_payload(event)
        policy_payload = {
            **source_payload,
            "task_id": task_id,
            "task_map_ref": task_map_ref,
        }
        if not self._typed_task_contract_handoff_enabled(policy_payload):
            return True
        task = self.task_store.get(task_id) if task_id else None
        if task is None:
            return False
        expected = current_task_contract_identity(
            task,
            task_map_ref=task_map_ref,
        )
        reported_revision, reported_generation = (
            self._reported_contract_identity(source_payload)
        )
        if not reported_revision or not reported_generation:
            return False
        if reported_revision != expected["contract_revision"]:
            return False
        if reported_generation != expected["task_map_generation"]:
            return False
        source_task_map_ref = str(
            source_payload.get("task_map_ref") or ""
        ).strip()
        return not (
            source_task_map_ref
            and task_map_ref
            and source_task_map_ref != task_map_ref
        )

    def _ensure_writer_completion_contract_identity(
        self,
        *,
        event: ZfEvent,
        child: dict,
        base_payload: dict,
    ) -> None:
        source_payload = self._writer_completion_source_payload(event)
        contract_input = {
            **(
                child.get("payload")
                if isinstance(child.get("payload"), dict)
                else {}
            ),
            **child,
            **base_payload,
            **source_payload,
        }
        if not self._typed_task_contract_handoff_enabled(contract_input):
            return
        target_commit = str(
            source_payload.get("target_commit")
            or source_payload.get("source_commit")
            or ""
        ).strip()
        task_id = str(base_payload.get("task_id") or "").strip()
        if not target_commit:
            raise TaskContractSnapshotError(
                f"adopted writer completion lacks target commit for {task_id}"
            )
        task = self.task_store.get(task_id) if task_id else None
        if task is None:
            raise TaskContractSnapshotError(
                f"cannot snapshot missing canonical task {task_id!r}"
            )
        expected_identity = current_task_contract_identity(
            task,
            task_map_ref=str(base_payload.get("task_map_ref") or ""),
        )
        reported_revision, reported_generation = (
            self._reported_contract_identity(source_payload)
        )
        if (
            reported_revision
            and reported_revision != expected_identity["contract_revision"]
        ):
            raise TaskContractSnapshotError(
                "task contract snapshot contract_revision mismatch: "
                f"expected {expected_identity['contract_revision']!r}, "
                f"got {reported_revision!r}"
            )
        if (
            reported_generation
            and reported_generation != expected_identity["task_map_generation"]
        ):
            raise TaskContractSnapshotError(
                "task contract snapshot task_map_generation mismatch: "
                f"expected {expected_identity['task_map_generation']!r}, "
                f"got {reported_generation!r}"
            )

        descriptor_payload = next(
            (
                candidate
                for candidate in (source_payload, base_payload)
                if candidate.get("contract_snapshot_ref")
                and candidate.get("contract_snapshot_digest")
            ),
            {},
        )
        source_snapshot = None
        descriptor = None
        if descriptor_payload:
            descriptor = descriptor_from_payload(descriptor_payload)
            source_snapshot = hydrate_task_contract_snapshot(
                self.state_dir,
                descriptor,
            )
            for key in ("task_id", "task_map_generation"):
                expected = expected_identity[key]
                actual = str(source_snapshot.get(key) or "")
                if actual != expected:
                    raise TaskContractSnapshotError(
                        f"task contract snapshot {key} mismatch: "
                        f"expected {expected!r}, got {actual!r}"
                    )
            expected_run = str(
                base_payload.get("workflow_run_id")
                or source_payload.get("workflow_run_id")
                or event.correlation_id
                or ""
            ).strip()
            actual_run = str(source_snapshot.get("workflow_run_id") or "")
            if expected_run and actual_run != expected_run:
                raise TaskContractSnapshotError(
                    "task contract snapshot workflow_run_id mismatch: "
                    f"expected {expected_run!r}, got {actual_run!r}"
                )

        base_commit = self._writer_dispatch_base_commit(
            source_payload=source_payload,
            child=child,
            source_snapshot=source_snapshot,
            task_id=task_id,
        )
        workflow_run_id = str(
            base_payload.get("workflow_run_id")
            or source_payload.get("workflow_run_id")
            or event.correlation_id
            or ""
        ).strip()
        snapshot_is_current = bool(
            source_snapshot
            and str(source_snapshot.get("contract_revision") or "")
            == expected_identity["contract_revision"]
        )
        if (
            source_snapshot
            and not snapshot_is_current
            and reported_revision != expected_identity["contract_revision"]
        ):
            raise TaskContractSnapshotError(
                "task contract snapshot contract_revision mismatch: "
                f"expected {expected_identity['contract_revision']!r}, "
                f"got {source_snapshot.get('contract_revision')!r}"
            )
        if snapshot_is_current:
            snapshot = source_snapshot
            actual_base = str(snapshot.get("base_commit") or "")
            if actual_base != base_commit:
                raise TaskContractSnapshotError(
                    "task contract snapshot base_commit mismatch: "
                    f"expected {base_commit!r}, got {actual_base!r}"
                )
        else:
            snapshot = build_task_contract_snapshot(
                task,
                workflow_run_id=workflow_run_id,
                task_map_generation_id=expected_identity["task_map_generation"],
                base_commit=base_commit,
                task_ref=f"{self.config.runtime.git.task_ref_prefix}/{task_id}",
            )
            descriptor = write_task_contract_snapshot(
                self.state_dir,
                snapshot,
                source_event_id=event.id,
            )
        if descriptor is None:
            raise TaskContractSnapshotError("contract snapshot descriptor missing")

        target_snapshot = build_target_snapshot(
            descriptor,
            target_commit=target_commit,
            contract_snapshot=snapshot,
        )
        try:
            target_payload = next(
                candidate
                for candidate in (source_payload, base_payload)
                if candidate.get("target_snapshot_ref")
                and candidate.get("target_snapshot_digest")
            )
            target_descriptor = target_descriptor_from_payload(target_payload)
            target_snapshot = hydrate_target_snapshot(
                self.state_dir,
                target_descriptor,
                expected={
                    "contract_snapshot_ref": str(descriptor.get("ref") or ""),
                    "contract_snapshot_digest": str(
                        descriptor.get("sha256") or ""
                    ),
                    "target_commit": target_commit,
                },
            )
        except (StopIteration, TaskContractSnapshotError):
            target_descriptor = write_target_snapshot(
                self.state_dir,
                target_snapshot,
                source_event_id=event.id,
            )
        base_payload.update({
            **contract_snapshot_identity_fields(snapshot),
            "target_commit": target_commit,
            **snapshot_payload_fields(descriptor),
            **target_payload_fields(target_descriptor),
        })
        self._ensure_impl_self_check_handoff(
            event=event,
            payload=source_payload,
            base_payload=base_payload,
            contract_snapshot=snapshot,
            target_snapshot=target_snapshot,
        )

    def _recoverable_handoff_failure_for_source(
        self,
        source_event_id: str,
    ) -> ZfEvent | None:
        if not source_event_id:
            return None
        try:
            events = self.event_log.read_all()
        except OSError:
            return None
        for recorded in reversed(events):
            if recorded.type not in {
                "fanout.child.completed",
                "fanout.child.failed",
            }:
                continue
            payload = (
                recorded.payload if isinstance(recorded.payload, dict) else {}
            )
            if source_event_id not in {
                str(payload.get("result_event_id") or ""),
                str(recorded.causation_id or ""),
            }:
                continue
            return (
                recorded
                if recoverable_writer_handoff_failure(recorded)
                else None
            )
        return None

    def _ensure_impl_self_check_handoff(
        self,
        *,
        event: ZfEvent,
        payload: dict,
        base_payload: dict,
        contract_snapshot: dict,
        target_snapshot: dict,
    ) -> None:
        required = bool(getattr(
            getattr(self.config, "workflow", None),
            "impl_self_check_required",
            False,
        ))
        if isinstance(payload.get("impl_self_check"), dict):
            attempt_id = str(
                payload.get("attempt_id")
                or payload.get("dispatch_id")
                or payload["impl_self_check"].get("attempt_id")
                or ""
            ).strip()
            self_check = dict(payload["impl_self_check"])
            expected_identity = {
                **{key: str(contract_snapshot[key]) for key in TASK_CONTRACT_AUTHORITY_FIELDS
                   if contract_snapshot.get(key) not in (None, "", 0)},
                "workflow_run_id": str(
                    contract_snapshot.get("workflow_run_id") or ""
                ),
                "task_id": str(contract_snapshot.get("task_id") or ""),
                "attempt_id": attempt_id,
                "contract_revision": str(
                    contract_snapshot.get("contract_revision") or ""
                ),
                "task_map_generation": str(
                    contract_snapshot.get("task_map_generation") or ""
                ),
                "source_commit": str(
                    target_snapshot.get("target_commit") or ""
                ),
                "target_commit": str(
                    target_snapshot.get("target_commit") or ""
                ),
                "contract_snapshot_ref": str(
                    target_snapshot.get("contract_snapshot_ref") or ""
                ),
                "contract_snapshot_digest": str(
                    target_snapshot.get("contract_snapshot_digest") or ""
                ),
            }
            for key, value in expected_identity.items():
                if self_check.get(key) in (None, ""):
                    self_check[key] = value
            body = normalize_impl_self_check(
                {**payload, "impl_self_check": self_check},
                contract_snapshot=contract_snapshot,
                target_snapshot=target_snapshot,
                expected_attempt_id=attempt_id,
                strict=True,
            )
            descriptor = write_impl_self_check(
                self.state_dir,
                body,
                source_event_id=event.id,
                created_by=str(event.actor or "worker"),
            )
            fields = self_check_payload_fields(descriptor)
            base_payload.update(fields)
            self.event_writer.append(ZfEvent(
                type="impl.self_check.completed",
                actor="orchestrator",
                task_id=str(base_payload.get("task_id") or ""),
                payload={
                    **fields,
                    "workflow_run_id": str(
                        contract_snapshot.get("workflow_run_id") or ""
                    ),
                    "contract_revision": str(
                        contract_snapshot.get("contract_revision") or ""
                    ),
                    "target_commit": str(
                        target_snapshot.get("target_commit") or ""
                    ),
                    "attempt_id": str(body.get("attempt_id") or ""),
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
            return

        descriptor_payload: dict = {}
        for source in (payload, base_payload):
            if (
                source.get("impl_self_check_ref")
                and source.get("impl_self_check_digest")
            ):
                descriptor_payload = source
                break
        if not descriptor_payload:
            target_commit = str(target_snapshot.get("target_commit") or "")
            try:
                prior = next(
                    item
                    for item in reversed(self.event_log.read_all())
                    if item.type == "impl.self_check.completed"
                    and str(item.task_id or "")
                    == str(base_payload.get("task_id") or "")
                    and isinstance(item.payload, dict)
                    and str(item.payload.get("target_commit") or "")
                    == target_commit
                )
            except (StopIteration, OSError):
                prior = None
            if prior is not None:
                descriptor_payload = prior.payload
        if descriptor_payload:
            descriptor = self_check_descriptor_from_payload(descriptor_payload)
            hydrate_impl_self_check(
                self.state_dir,
                descriptor,
                contract_snapshot=contract_snapshot,
                target_snapshot=target_snapshot,
            )
            base_payload.update(self_check_payload_fields(descriptor))
            return
        if required:
            raise ImplSelfCheckError(
                "writer completion lacks required impl_self_check sidecar"
            )


__all__ = [
    "WriterContractHandoffMixin",
    "recoverable_writer_handoff_failure",
]
