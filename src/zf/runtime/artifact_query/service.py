"""Provider-neutral artifact queries over canonical facts and SQLite metadata."""

from __future__ import annotations

import hashlib
import json
import sqlite3
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
from zf.runtime.artifact_query.store import (
    EXTRACTOR_VERSION,
    catalog_rows,
    catalog_show,
    catalog_status,
    descriptor_record,
    get_reducer_projection,
    lineage_rows,
    rebuild_catalog,
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
    iter_sidecar_ref_descriptors,
)


QUERY_SCHEMA_VERSION = "artifact-query-result.v1"
ATTEMPT_INSPECT_SCHEMA = "attempt-artifact-view.v1"
PACKAGE_PROJECTION_VERSION = f"{PLAN_PACKAGE_SCHEMA}:reducer.v1"
GOAL_DOSSIER_CACHE_VERSION = "goal-dossier-cache.v1"


class ArtifactQueryError(ValueError):
    """A query cannot be answered without violating its requested mode."""


class ArtifactQueryService:
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
        ref: str = "",
        task_id: str = "",
        run_id: str = "",
        attempt_id: str = "",
        operation_id: str = "",
        package_id: str = "",
    ) -> dict[str, Any]:
        status, fallback = self._ensure_catalog(context)
        if fallback:
            rows = self._canonical_catalog_rows(
                kind=kind,
                ref=ref,
                task_id=task_id,
                run_id=run_id,
                attempt_id=attempt_id,
                operation_id=operation_id,
                package_id=package_id,
            )
            start = context.bounded_offset()
            end = start + context.bounded_limit()
            page = rows[start:end]
            return QueryResult(
                schema_version=QUERY_SCHEMA_VERSION,
                items=[self._catalog_visibility(row, context) for row in page],
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
                has_more=len(rows) > end,
                diagnostics=self._status_diagnostics(status),
            ).to_dict()
        rows, has_more = catalog_rows(
            self.state_dir,
            kind=kind,
            ref=ref,
            task_id=task_id,
            run_id=run_id,
            attempt_id=attempt_id,
            operation_id=operation_id,
            package_id=package_id,
            limit=context.bounded_limit(),
            offset=context.bounded_offset(),
        )
        return QueryResult(
            schema_version=QUERY_SCHEMA_VERSION,
            items=[self._catalog_visibility(row, context) for row in rows],
            source_snapshot=self.source_snapshot(
                projected_seq=int(status.get("projected_seq") or 0)
            ),
            projection_state="ready",
            projection_lag=0,
            limit=context.bounded_limit(),
            offset=context.bounded_offset(),
            has_more=has_more,
        ).to_dict()

    def catalog_show(
        self,
        identity: str,
        *,
        context: QueryContext,
    ) -> dict[str, Any]:
        status, fallback = self._ensure_catalog(context)
        item: dict[str, Any] | None = None
        if not fallback:
            item = catalog_show(self.state_dir, identity)
        if fallback or item is None:
            item = next(
                (
                    row
                    for row in self._canonical_catalog_rows()
                    if identity in {
                        row.get("occurrence_id"),
                        row.get("locator_id"),
                        row.get("object_id"),
                        row.get("sha256"),
                        row.get("ref"),
                    }
                ),
                None,
            )
            fallback = True
        return QueryResult(
            schema_version=QUERY_SCHEMA_VERSION,
            item=(
                self._catalog_visibility(item, context)
                if item is not None
                else None
            ),
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
            items=items,
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
        result = self.catalog_list(context=context, task_id=task_id)
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
        missing = [
            row
            for row in required_reads
            if not any(self._read_matches(item, row) for item in read_rows)
        ]
        result = self.catalog_list(
            context=context,
            attempt_id=attempt_id,
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
        snapshot_key = self.source_snapshot_key(snapshot)
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
        descriptor = {
            "kind": item["kind"],
            "ref": item["ref"],
            "sha256": item["sha256"],
            "byte_count": item["byte_count"],
            "content_type": item["content_type"],
            "schema_version": item["schema_version"],
            "encoding": item["encoding"],
            "required": item["required"],
            "access_scope": item["access_scope"],
            "retention": item["retention"],
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
            if status.get("projection_state") != "ready":
                rebuild_catalog(
                    self.state_dir,
                    project_root=self.project_root,
                    config=self.config,
                )
                status = catalog_status(self.state_dir)
            return status, False
        except (OSError, sqlite3.Error, SidecarRefError, ValueError) as exc:
            status = {
                "projection_state": "degraded",
                "diagnostic": str(exc),
                "projected_seq": 0,
            }
            if context.mode == "canonical":
                return status, True
            return status, True

    def _canonical_catalog_rows(self, **filters: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for seq, event in enumerate(self._events(), start=1):
            payload = event.payload if isinstance(event.payload, dict) else {}
            for descriptor in iter_sidecar_ref_descriptors(payload):
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

    def _catalog_visibility(
        self,
        row: Mapping[str, Any],
        context: QueryContext,
    ) -> dict[str, Any]:
        item = dict(row)
        authorized = self._authorized(
            item.get("access_scope"),
            context=context,
        )
        item["authorized"] = authorized
        if not authorized:
            item["preview"] = ""
        return item

    @staticmethod
    def _authorized(
        scope_value: object,
        *,
        context: QueryContext,
    ) -> bool:
        scope = scope_value if isinstance(scope_value, Mapping) else {}
        visibility = str(scope.get("visibility") or "project")
        if visibility not in {"project", "public"}:
            return False
        expected_actor = str(scope.get("actor") or "").strip()
        if expected_actor and expected_actor not in {context.actor, context.role}:
            return False
        expected_purpose = str(scope.get("purpose") or "").strip()
        if expected_purpose and context.purpose != expected_purpose:
            return False
        return True

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
