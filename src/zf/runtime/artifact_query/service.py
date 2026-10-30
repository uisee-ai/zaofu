"""Provider-neutral artifact queries over canonical facts and SQLite metadata."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.segments import build_event_manifest
from zf.runtime.artifact_query.models import (
    QueryContext,
    QueryResult,
    SourceSnapshot,
)
from zf.runtime.artifact_query.object_view import ArtifactObjectViewMixin
from zf.runtime.artifact_query.extractors import iter_catalog_descriptors
from zf.runtime.artifact_query.store import (
    EXTRACTOR_VERSION,
    catch_up_catalog,
    catalog_matching_rows,
    catalog_rows,
    catalog_show_rows,
    catalog_status,
    descriptor_record,
    get_reducer_projection,
    lineage_rows,
    projection_db_path,
    set_reducer_projection,
)
from zf.runtime.attempt_handoff_reducer import (
    SCHEMA_VERSION as ATTEMPT_REDUCER_VERSION,
    reduce_attempt_handoffs,
)
from zf.runtime.plan_artifact_package import (
    PLAN_ARTIFACT_PACKAGE_SCHEMA as PLAN_PACKAGE_SCHEMA,
    reduce_plan_artifact_packages,
)
from zf.runtime.sidecar_refs import (
    SidecarRefError,
    hydrate_sidecar_ref,
)


QUERY_SCHEMA_VERSION = "artifact-query-result.v1"
ATTEMPT_INSPECT_SCHEMA = "attempt-artifact-view.v1"
PACKAGE_PROJECTION_VERSION = f"{PLAN_PACKAGE_SCHEMA}:reducer.v1"
GOAL_DOSSIER_CACHE_VERSION = "goal-dossier-cache.v6"
CATALOG_CATCH_UP_WAIT_SECONDS = 2.5

_CATALOG_CATCH_UPS: dict[str, threading.Event] = {}
_CATALOG_CATCH_UPS_LOCK = threading.Lock()


class ArtifactQueryError(ValueError):
    """A query cannot be answered without violating its requested mode."""


class ArtifactQueryService(ArtifactObjectViewMixin):
    def __init__(
        self,
        *,
        state_dir: Path,
        project_root: Path,
        config: ZfConfig | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.project_root = Path(project_root)
        self.config = config

    def context(
        self,
        *,
        actor: str = "operator",
        role: str = "",
        purpose: str = "query",
        mode: str = "advisory",
        limit: int = 200,
        offset: int = 0,
    ) -> QueryContext:
        normalized_mode = "canonical" if mode == "canonical" else "advisory"
        return QueryContext(
            project_root=self.project_root,
            state_dir=self.state_dir,
            actor=actor,
            role=role,
            purpose=purpose,
            mode=normalized_mode,
            limit=limit,
            offset=offset,
        )

    def catalog_list(
        self,
        *,
        context: QueryContext,
        kind: str = "",
        semantic_kind: str = "",
        ref: str = "",
        task_id: str = "",
        claim_id: str = "",
        run_id: str = "",
        attempt_id: str = "",
        operation_id: str = "",
        package_id: str = "",
        view: str = "objects",
    ) -> dict[str, Any]:
        normalized_view = str(view or "objects").strip().lower()
        if normalized_view not in {"objects", "occurrences"}:
            raise ArtifactQueryError(
                "artifact catalog view must be objects or occurrences"
            )
        status, fallback = self._ensure_catalog(context)
        filters = {
            "kind": kind,
            "semantic_kind": semantic_kind,
            "ref": ref,
            "task_id": task_id,
            "claim_id": claim_id,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "operation_id": operation_id,
            "package_id": package_id,
        }
        if fallback:
            rows = self._canonical_catalog_rows(**filters)
            if normalized_view == "objects":
                items = self._catalog_object_items(rows, context)
            else:
                items = [
                    self._catalog_visibility(row, context)
                    for row in rows
                ]
            start = context.bounded_offset()
            end = start + context.bounded_limit()
            result = QueryResult(
                schema_version=QUERY_SCHEMA_VERSION,
                items=items[start:end],
                source_snapshot=self.source_snapshot(
                    projected_seq=len(self._events())
                ),
                projection_state=status.get("projection_state", "degraded"),
                projection_lag=None,
                source="canonical",
                fallback_used=True,
                fallback_source="event-log-descriptor-scan",
                limit=context.bounded_limit(),
                offset=start,
                has_more=len(items) > end,
                diagnostics=self._status_diagnostics(status),
            ).to_dict()
            result["view"] = normalized_view
            return result
        if normalized_view == "occurrences":
            rows, has_more = catalog_rows(
                self.state_dir,
                **filters,
                limit=context.bounded_limit(),
                offset=context.bounded_offset(),
            )
            items = [
                self._catalog_visibility(row, context)
                for row in rows
            ]
        else:
            rows, truncated = catalog_matching_rows(
                self.state_dir,
                **filters,
            )
            objects = self._catalog_object_items(rows, context)
            start = context.bounded_offset()
            end = start + context.bounded_limit()
            items = objects[start:end]
            has_more = len(objects) > end or truncated
        result = QueryResult(
            schema_version=QUERY_SCHEMA_VERSION,
            items=items,
            source_snapshot=self.source_snapshot(
                projected_seq=int(status.get("projected_seq") or 0)
            ),
            projection_state="ready",
            projection_lag=0,
            limit=context.bounded_limit(),
            offset=context.bounded_offset(),
            has_more=has_more,
        ).to_dict()
        result["view"] = normalized_view
        if normalized_view == "objects" and truncated:
            result["diagnostics"] = [{
                "code": "catalog_object_scan_truncated",
                "message": "object aggregation reached its occurrence scan bound",
            }]
        return result

    def catalog_show(
        self,
        identity: str,
        *,
        context: QueryContext,
    ) -> dict[str, Any]:
        status, fallback = self._ensure_catalog(context)
        rows: list[dict[str, Any]] = []
        matched_by = ""
        if not fallback:
            rows, matched_by = catalog_show_rows(self.state_dir, identity)
        if fallback or not rows:
            rows, matched_by = self._matching_canonical_rows(
                identity,
                self._canonical_catalog_rows(),
            )
            fallback = True
        item = self._catalog_detail(
            rows,
            matched_by=matched_by,
            context=context,
        )
        return QueryResult(
            schema_version=QUERY_SCHEMA_VERSION,
            item=item,
            source_snapshot=self.source_snapshot(
                projected_seq=int(status.get("projected_seq") or 0)
            ),
            projection_state=(
                status.get("projection_state", "degraded")
                if fallback
                else "ready"
            ),
            projection_lag=None if fallback else 0,
            source="canonical" if fallback else "read_model.sqlite",
            fallback_used=fallback,
            fallback_source="event-log-descriptor-scan" if fallback else "",
            limit=1,
            diagnostics=self._status_diagnostics(status) if fallback else [],
        ).to_dict()

    def lineage(
        self,
        *,
        subject_kind: str,
        subject_id: str,
        context: QueryContext,
    ) -> dict[str, Any]:
        status, fallback = self._ensure_catalog(context)
        if fallback:
            records = self._canonical_catalog_rows()
            items = [
                {
                    "subject_kind": subject_kind,
                    "subject_id": subject_id,
                    "relation": row["relation"],
                    "occurrence_id": row["occurrence_id"],
                    "locator_id": row["locator_id"],
                    "source_event_id": row["source_event_id"],
                    "causation_event_id": row["causation_event_id"],
                    "result_event_id": row["result_event_id"],
                    "source_seq": row["source_seq"],
                    "attempt_domain": row["attempt_domain"],
                    "ref": row["ref"],
                    "kind": row["kind"],
                    "object_id": row["object_id"],
                    "sha256": row["sha256"],
                }
                for row in records
                if str(row.get(f"{subject_kind}_id") or "") == subject_id
            ][:context.bounded_limit()]
        else:
            items = lineage_rows(
                self.state_dir,
                subject_kind=subject_kind,
                subject_id=subject_id,
                limit=context.bounded_limit(),
            )
        return QueryResult(
            schema_version="artifact-lineage.v1",
            items=[self._lineage_visibility(item, context) for item in items],
            source_snapshot=self.source_snapshot(
                projected_seq=int(status.get("projected_seq") or 0)
            ),
            projection_state=(
                status.get("projection_state", "degraded")
                if fallback
                else "ready"
            ),
            projection_lag=None if fallback else 0,
            source="canonical" if fallback else "read_model.sqlite",
            fallback_used=fallback,
            fallback_source="event-log-descriptor-scan" if fallback else "",
            limit=context.bounded_limit(),
            diagnostics=self._status_diagnostics(status) if fallback else [],
        ).to_dict()

    def task_artifacts(
        self,
        task_id: str,
        *,
        context: QueryContext,
    ) -> dict[str, Any]:
        result = self.catalog_list(
            context=context,
            task_id=task_id,
            view="occurrences",
        )
        result["schema_version"] = "task-artifact-view.v1"
        result["task_id"] = task_id
        return result

    def attempt_inspect(
        self,
        attempt_id: str,
        *,
        context: QueryContext,
    ) -> dict[str, Any]:
        events = self._events()
        run_id = self._run_for_attempt(events, attempt_id)
        snapshot = self.source_snapshot(projected_seq=len(events))
        snapshot_key = self.source_snapshot_key(snapshot)
        cache_id = run_id or attempt_id
        reduced = get_reducer_projection(
            self.state_dir,
            projection_kind="attempt-handoff",
            subject_id=cache_id,
            source_snapshot_key=snapshot_key,
        )
        if reduced is None:
            reduced = reduce_attempt_handoffs(
                events,
                workflow_run_id=run_id or None,
            )
            set_reducer_projection(
                self.state_dir,
                projection_kind="attempt-handoff",
                subject_id=cache_id,
                source_snapshot_key=snapshot_key,
                source_seq=len(events),
                reducer_version=ATTEMPT_REDUCER_VERSION,
                payload=reduced,
            )
        required_reads = self._required_reads(events, attempt_id)
        read_rows = self._read_rows(attempt_id)
        from zf.runtime.artifact_read_ledger import required_read_satisfied

        missing = [
            row
            for row in required_reads
            if not required_read_satisfied(read_rows, row)
        ]
        result = self.catalog_list(
            context=context,
            attempt_id=attempt_id,
            view="occurrences",
        )
        result.update({
            "schema_version": ATTEMPT_INSPECT_SCHEMA,
            "attempt_id": attempt_id,
            "workflow_run_id": run_id,
            "attempt_domain": self._attempt_domain(events, attempt_id),
            "handoff": reduced,
            "required_reads": required_reads,
            "read_count": len(read_rows),
            "missing_reads": missing,
            "protocol_repair_required": bool(missing),
            "semantic_rework_required": False,
        })
        return result

    def attempt_missing_reads(
        self,
        attempt_id: str,
        *,
        context: QueryContext,
    ) -> dict[str, Any]:
        inspected = self.attempt_inspect(attempt_id, context=context)
        return {
            "schema_version": "attempt-read-compliance.v1",
            "attempt_id": attempt_id,
            "attempt_domain": inspected.get("attempt_domain", ""),
            "required_read_count": len(inspected.get("required_reads") or []),
            "read_count": inspected.get("read_count", 0),
            "missing_reads": inspected.get("missing_reads") or [],
            "protocol_repair_required": inspected.get(
                "protocol_repair_required", False
            ),
            "semantic_rework_required": False,
            "source_snapshot": inspected.get("source_snapshot"),
            "projection_state": inspected.get("projection_state"),
            "source": inspected.get("source"),
        }

    def plan_package_projection(
        self,
        run_id: str,
        *,
        context: QueryContext,
    ) -> dict[str, Any]:
        events = self._events()
        snapshot = self.source_snapshot(projected_seq=len(events))
        snapshot_key = self.source_snapshot_key(snapshot)
        reduced = get_reducer_projection(
            self.state_dir,
            projection_kind="plan-package",
            subject_id=run_id,
            source_snapshot_key=snapshot_key,
        )
        if reduced is None:
            reduced = reduce_plan_artifact_packages(
                events,
                workflow_run_id=run_id,
            )
            set_reducer_projection(
                self.state_dir,
                projection_kind="plan-package",
                subject_id=run_id,
                source_snapshot_key=snapshot_key,
                source_seq=len(events),
                reducer_version=PACKAGE_PROJECTION_VERSION,
                payload=reduced,
            )
        return {
            "schema_version": "plan-package-advisory.v1",
            "is_derived_projection": True,
            "authority": "canonical_lifecycle_reducer",
            "workflow_run_id": run_id,
            "current": reduced.get("current"),
            "history": reduced.get("history") or [],
            "diagnostics": reduced.get("diagnostics") or [],
            "source_snapshot": snapshot.to_dict(),
            "projection_state": "ready",
            "source": "read_model.sqlite",
        }

    def cached_goal_dossier(
        self,
        run_id: str,
        *,
        builder: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot = self.source_snapshot()
        snapshot_key = (
            f"{GOAL_DOSSIER_CACHE_VERSION}:"
            f"{self.source_snapshot_key(snapshot)}"
        )
        cached = get_reducer_projection(
            self.state_dir,
            projection_kind="goal-dossier",
            subject_id=run_id,
            source_snapshot_key=snapshot_key,
        )
        if cached is not None:
            cached.setdefault("cache", {})
            cached["cache"].update({
                "hit": True,
                "source_snapshot_key": snapshot_key,
            })
            return cached
        dossier = builder()
        set_reducer_projection(
            self.state_dir,
            projection_kind="goal-dossier",
            subject_id=run_id,
            source_snapshot_key=snapshot_key,
            source_seq=snapshot.projected_seq,
            reducer_version=GOAL_DOSSIER_CACHE_VERSION,
            payload=dossier,
        )
        dossier["cache"] = {
            "hit": False,
            "source_snapshot_key": snapshot_key,
        }
        return dossier

    def hydrate(
        self,
        identity: str,
        *,
        context: QueryContext,
        max_bytes: int = 4 * 1024 * 1024,
    ) -> Any:
        if not str(identity or "").startswith("occurrence:"):
            raise ArtifactQueryError(
                "artifact hydrate requires an exact occurrence identity"
            )
        result = self.catalog_show(identity, context=context)
        item = result.get("item")
        if not isinstance(item, dict):
            raise ArtifactQueryError(f"artifact not found: {identity}")
        if not item.get("authorized"):
            raise ArtifactQueryError("artifact hydrate is not authorized")
        occurrences = item.get("occurrences")
        locators = item.get("locators")
        if (
            not isinstance(occurrences, list)
            or len(occurrences) != 1
            or not isinstance(occurrences[0], dict)
            or not isinstance(locators, list)
        ):
            raise ArtifactQueryError(
                "artifact hydrate requires one authorized occurrence"
            )
        occurrence = occurrences[0]
        locator = next(
            (
                row for row in locators
                if isinstance(row, dict)
                and row.get("locator_id") == occurrence.get("locator_id")
            ),
            None,
        )
        if not isinstance(locator, dict):
            raise ArtifactQueryError("artifact locator is unavailable")
        object_metadata = item.get("object")
        if not isinstance(object_metadata, dict):
            raise ArtifactQueryError("artifact object metadata is unavailable")
        descriptor = {
            "kind": locator["storage_kind"],
            "ref": locator["ref"],
            "sha256": object_metadata["sha256"],
            "byte_count": object_metadata["byte_count"],
            "content_type": locator["content_type"],
            "schema_version": locator["schema_version"],
            "encoding": locator["encoding"],
            "required": occurrence["required"],
            "access_scope": occurrence["access_scope"],
            "retention": occurrence["retention"],
        }
        return hydrate_sidecar_ref(
            self.state_dir,
            descriptor,
            actor=context.actor or context.role,
            purpose=context.purpose,
            max_bytes=max_bytes,
        ).payload

    def source_snapshot(self, *, projected_seq: int | None = None) -> SourceSnapshot:
        manifest = build_event_manifest(self.state_dir)
        status: Mapping[str, Any] = {}
        if projected_seq is None:
            try:
                status = catalog_status(self.state_dir)
            except (OSError, sqlite3.Error):
                status = {}
        return SourceSnapshot(
            projected_seq=(
                int(projected_seq)
                if projected_seq is not None
                else int(status.get("projected_seq") or 0)
            ),
            event_manifest_digest=manifest.digest,
            task_store_digest=self._state_digest(
                ["kanban.json", "kanban-terminal-index.json", "kanban/*.json"]
            ),
            feature_store_digest=self._state_digest(
                ["feature_list.json", "feature_list/*.json"]
            ),
            session_store_digest=self._state_digest(
                ["session.yaml", "role_sessions.yaml"]
            ),
            task_ref_index_digest=self._state_digest(["refs/task-index.json"]),
            package_reducer_version=PACKAGE_PROJECTION_VERSION,
            attempt_handoff_reducer_version=ATTEMPT_REDUCER_VERSION,
            descriptor_extractor_version=EXTRACTOR_VERSION,
        )

    @staticmethod
    def source_snapshot_key(snapshot: SourceSnapshot) -> str:
        return _digest(snapshot.to_dict())

    def _ensure_catalog(
        self,
        context: QueryContext,
    ) -> tuple[dict[str, Any], bool]:
        try:
            status = catalog_status(self.state_dir)
            if status.get("projection_state") in {"missing", "stale"}:
                self._catch_up_catalog_single_flight()
                status = catalog_status(self.state_dir)
            return status, status.get("projection_state") != "ready"
        except (OSError, sqlite3.Error, SidecarRefError, ValueError) as exc:
            status = {
                "projection_state": "degraded",
                "diagnostic": str(exc),
                "projected_seq": 0,
            }
            if context.mode == "canonical":
                return status, True
            return status, True

    def _catch_up_catalog_single_flight(self) -> None:
        key = str(projection_db_path(self.state_dir).resolve())
        with _CATALOG_CATCH_UPS_LOCK:
            event = _CATALOG_CATCH_UPS.get(key)
            owner = event is None
            if owner:
                event = threading.Event()
                _CATALOG_CATCH_UPS[key] = event
        if owner:
            try:
                catch_up_catalog(
                    self.state_dir,
                    project_root=self.project_root,
                    config=self.config,
                )
            finally:
                with _CATALOG_CATCH_UPS_LOCK:
                    _CATALOG_CATCH_UPS.pop(key, None)
                event.set()
            return
        if not event.wait(CATALOG_CATCH_UP_WAIT_SECONDS):
            raise TimeoutError("artifact catalog catch-up wait timed out")

    def _canonical_catalog_rows(self, **filters: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for seq, event in enumerate(self._events(), start=1):
            payload = event.payload if isinstance(event.payload, dict) else {}
            for descriptor in iter_catalog_descriptors(
                self.state_dir,
                payload,
            ):
                row = descriptor_record(
                    project_root=self.project_root,
                    state_dir=self.state_dir,
                    event=event,
                    descriptor=descriptor,
                    source_seq=seq,
                )
                if row is None:
                    continue
                if any(
                    value and str(row.get(key) or "") != value
                    for key, value in filters.items()
                ):
                    continue
                rows.append(row)
        return sorted(
            rows,
            key=lambda item: (
                int(item.get("source_seq") or 0),
                str(item.get("occurrence_id") or ""),
            ),
            reverse=True,
        )

    def _events(self) -> list[ZfEvent]:
        return event_log_from_project(
            self.state_dir,
            config=self.config,
        ).read_all()

    def _state_digest(self, patterns: Iterable[str]) -> str:
        rows: list[tuple[str, str]] = []
        for pattern in patterns:
            for path in sorted(self.state_dir.glob(pattern)):
                if not path.is_file():
                    continue
                try:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    continue
                rows.append((path.relative_to(self.state_dir).as_posix(), digest))
        return _digest(rows)

    @staticmethod
    def _run_for_attempt(events: Iterable[ZfEvent], attempt_id: str) -> str:
        for event in reversed(list(events)):
            payload = event.payload if isinstance(event.payload, dict) else {}
            identities = {
                str(payload.get(key) or "")
                for key in (
                    "attempt_id",
                    "active_attempt_id",
                    "dispatch_id",
                    "run_id",
                )
            }
            if attempt_id in identities:
                return str(
                    payload.get("workflow_run_id")
                    or payload.get("run_id")
                    or event.correlation_id
                    or ""
                )
        return ""

    @staticmethod
    def _attempt_domain(events: Iterable[ZfEvent], attempt_id: str) -> str:
        for event in reversed(list(events)):
            payload = event.payload if isinstance(event.payload, dict) else {}
            if attempt_id in {
                str(payload.get("attempt_id") or ""),
                str(payload.get("active_attempt_id") or ""),
                str(payload.get("dispatch_id") or ""),
            }:
                return str(payload.get("attempt_domain") or "")
        return ""

    @staticmethod
    def _required_reads(
        events: Iterable[ZfEvent],
        attempt_id: str,
    ) -> list[dict[str, Any]]:
        for event in reversed(list(events)):
            payload = event.payload if isinstance(event.payload, dict) else {}
            if attempt_id not in {
                str(payload.get("attempt_id") or ""),
                str(payload.get("active_attempt_id") or ""),
                str(payload.get("dispatch_id") or ""),
            }:
                continue
            rows = payload.get("required_reads")
            if isinstance(rows, list):
                return [dict(row) for row in rows if isinstance(row, Mapping)]
        return []

    def _read_rows(self, attempt_id: str) -> list[dict[str, Any]]:
        safe = "".join(
            char if char.isalnum() or char in "._-" else "-"
            for char in attempt_id
        ).strip("-._") or "attempt"
        root = self.state_dir / "artifacts" / "attempts" / safe
        paths = [*sorted(root.glob("read-ledger-*.jsonl"))]
        active = root / "read-ledger.active.jsonl"
        if active.exists():
            paths.append(active)
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in paths:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                body = json.dumps(row, sort_keys=True, separators=(",", ":"))
                if body not in seen:
                    seen.add(body)
                    rows.append(row)
        return rows

    @staticmethod
    def _read_matches(
        read: Mapping[str, Any],
        required: Mapping[str, Any],
    ) -> bool:
        return all(
            str(read.get(read_key) or "") == str(required.get(required_key) or "")
            for read_key, required_key in (
                ("source_id", "source_id"),
                ("artifact_id", "artifact_id"),
                ("artifact_sha256", "artifact_sha256"),
                ("json_path", "json_path"),
            )
        )

    @staticmethod
    def _status_diagnostics(status: Mapping[str, Any]) -> list[dict[str, Any]]:
        diagnostic = str(status.get("diagnostic") or "")
        return (
            [{"code": "projection_degraded", "message": diagnostic}]
            if diagnostic
            else []
        )


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ArtifactQueryError",
    "ArtifactQueryService",
    "QUERY_SCHEMA_VERSION",
]
