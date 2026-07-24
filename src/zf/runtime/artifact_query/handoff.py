"""Canonical-first resolver used by real provider dispatch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.schema import ZfConfig
from zf.core.task.store import TaskStore
from zf.runtime.artifact_query.service import ArtifactQueryService
from zf.runtime.artifact_read_ledger import (
    ArtifactReadError,
    source_manifest_from_payload,
)
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError,
    hydrate_plan_artifact_package,
    reduce_plan_artifact_packages,
)
from zf.runtime.plan_artifact_ports import canonical_plan_port_name
from zf.runtime.sidecar_refs import SidecarRefError
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError,
    current_task_contract_identity,
    hydrate_target_snapshot,
    hydrate_task_contract_snapshot,
)


class CanonicalHandoffResolver:
    def __init__(
        self,
        *,
        state_dir: Path,
        project_root: Path,
        config: ZfConfig | None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.project_root = Path(project_root)
        self.config = config
        self.query = ArtifactQueryService(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=config,
        )

    def resolve_payload(
        self,
        *,
        payload: Mapping[str, Any],
        workflow_run_id: str,
        task_id: str,
        attempt_id: str,
        dispatch_id: str,
        source_event_id: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        mutable = dict(payload)
        currentness = self._validate_currentness(
            payload=mutable,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
        )
        profile = str(mutable.get("output_profile_id") or "")
        plan_port_sources = (
            self._current_plan_port_sources(
                workflow_run_id=workflow_run_id,
                task_id=task_id,
                currentness=currentness,
            )
            if profile in {"implementation", "task-verify", "candidate-verify"}
            else []
        )
        if plan_port_sources:
            artifact_refs = (
                list(mutable.get("artifact_refs") or [])
                if isinstance(mutable.get("artifact_refs"), list)
                else []
            )
            mutable["artifact_refs"] = [*artifact_refs, *plan_port_sources]
        context = self.query.context(
            actor="zf-kernel",
            role="kernel",
            purpose="handoff",
            mode="canonical",
            limit=1,
        )
        self.query.catalog_list(
            context=context,
            task_id=task_id,
            run_id=workflow_run_id,
        )
        metadata = {
            **currentness,
            "source_snapshot": self._stable_source_snapshot(
                payload=mutable,
                currentness=currentness,
            ),
            "resolver": {
                "schema_version": "canonical-handoff-resolver.v1",
                "selection": "projection-candidate-canonical-verify",
            },
        }
        manifest, descriptor = source_manifest_from_payload(
            state_dir=self.state_dir,
            project_root=self.project_root,
            payload=mutable,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            dispatch_id=dispatch_id,
            source_event_id=source_event_id,
            manifest_metadata=metadata,
        )
        if plan_port_sources:
            expected_sources = {
                str(source.get("source_id") or "")
                for source in plan_port_sources
            }
            materialized_sources = {
                str(source.get("source_id") or "")
                for source in manifest.get("sources", [])
                if isinstance(source, Mapping)
            }
            missing_sources = sorted(expected_sources - materialized_sources)
            if missing_sources:
                raise ArtifactReadError(
                    "required Plan Artifact Package sources could not be "
                    "materialized: " + ", ".join(missing_sources)
                )
        return manifest, descriptor

    def _validate_currentness(
        self,
        *,
        payload: Mapping[str, Any],
        workflow_run_id: str,
        task_id: str,
    ) -> dict[str, str]:
        result = {
            "contract_revision": str(payload.get("contract_revision") or ""),
            "task_map_generation": str(payload.get("task_map_generation") or ""),
            "base_commit": str(payload.get("base_commit") or ""),
            "target_commit": str(payload.get("target_commit") or ""),
            "task_ref": str(payload.get("task_ref") or ""),
            "plan_artifact_package_id": str(
                payload.get("plan_artifact_package_id")
                or payload.get("package_id")
                or ""
            ),
            "plan_artifact_package_ref": str(
                payload.get("plan_artifact_package_ref") or ""
            ),
            "plan_artifact_package_digest": str(
                payload.get("plan_artifact_package_digest") or ""
            ),
        }
        profile = str(payload.get("output_profile_id") or "")
        task_bound_profile = profile in {
            "implementation",
            "task-verify",
            "candidate-verify",
        }
        task = TaskStore(self.state_dir / "kanban.json").get(task_id) if task_id else None
        if task is not None:
            try:
                current = current_task_contract_identity(task)
            except TaskContractSnapshotError as exc:
                raise ArtifactReadError(
                    f"cannot prove current task contract identity: {exc}"
                ) from exc
            self._require_match(
                "contract_revision",
                result["contract_revision"],
                str(current.get("contract_revision") or ""),
            )
            self._require_match(
                "task_map_generation",
                result["task_map_generation"],
                str(current.get("task_map_generation") or ""),
            )
            result["contract_revision"] = str(
                current.get("contract_revision")
                or result["contract_revision"]
            )
            result["task_map_generation"] = str(
                current.get("task_map_generation")
                or result["task_map_generation"]
            )

        task_ref = self._task_ref_entry(task_id)
        expected_ref = str(task_ref.get("task_ref") or "")
        self._require_match("task_ref", result["task_ref"], expected_ref)
        result["task_ref"] = expected_ref or result["task_ref"]

        package_id = result["plan_artifact_package_id"]
        if workflow_run_id and (package_id or (task is not None and task_bound_profile)):
            events = self.query._events()
            reduced = reduce_plan_artifact_packages(
                events,
                workflow_run_id=workflow_run_id,
            )
            current_package = reduced.get("current")
            current_package = (
                current_package if isinstance(current_package, Mapping) else {}
            )
            expected_package = str(
                current_package.get("plan_artifact_package_id")
                or current_package.get("package_id")
                or ""
            )
            expected_ref = str(current_package.get("package_ref") or "")
            expected_digest = str(current_package.get("package_digest") or "")
            for field, incoming, current in (
                ("plan_artifact_package_id", package_id, expected_package),
                (
                    "plan_artifact_package_ref",
                    result["plan_artifact_package_ref"],
                    expected_ref,
                ),
                (
                    "plan_artifact_package_digest",
                    result["plan_artifact_package_digest"],
                    expected_digest,
                ),
            ):
                if incoming and not current:
                    raise ArtifactReadError(
                        f"cannot prove current {field}: incoming {incoming!r}"
                    )
                if current or incoming:
                    self._require_match(field, incoming, current)
            result["plan_artifact_package_id"] = expected_package or package_id
            result["plan_artifact_package_ref"] = (
                expected_ref or result["plan_artifact_package_ref"]
            )
            result["plan_artifact_package_digest"] = (
                expected_digest or result["plan_artifact_package_digest"]
            )
        if task is not None and task_bound_profile:
            self._validate_task_snapshots(
                payload=payload,
                workflow_run_id=workflow_run_id,
                task_id=task_id,
                profile=profile,
                currentness=result,
            )
        return result

    def _current_plan_port_sources(
        self,
        *,
        workflow_run_id: str,
        task_id: str,
        currentness: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        task = TaskStore(self.state_dir / "kanban.json").get(task_id) if task_id else None
        if task is None:
            return []
        evidence = (
            task.contract.evidence_contract
            if isinstance(task.contract.evidence_contract, dict)
            else {}
        )
        declared = self._declared_required_ports(
            evidence.get("required_plan_ports")
        )
        package_ref = str(currentness.get("plan_artifact_package_ref") or "")
        package_digest = str(currentness.get("plan_artifact_package_digest") or "")
        if not package_ref or not package_digest:
            if declared:
                raise ArtifactReadError(
                    "task contract declares required_plan_ports but no current "
                    "Plan Artifact Package is bound"
                )
            return []
        try:
            package = hydrate_plan_artifact_package(
                self.state_dir,
                {"ref": package_ref, "sha256": package_digest},
            )
        except (PlanArtifactPackageError, SidecarRefError) as exc:
            raise ArtifactReadError(
                f"current Plan Artifact Package cannot be hydrated: {exc}"
            ) from exc
        expected = {
            "workflow_run_id": workflow_run_id,
            "task_map_generation": str(
                currentness.get("task_map_generation") or ""
            ),
        }
        for field, value in expected.items():
            if value and str(package.get(field) or "") != value:
                raise ArtifactReadError(
                    f"current Plan Artifact Package {field} mismatch: "
                    f"expected {value!r}, got {package.get(field)!r}"
                )
        package_required = self._declared_required_ports(
            package.get("required_ports")
        )
        missing_declarations = sorted(set(declared) - set(package_required))
        if missing_declarations:
            raise ArtifactReadError(
                "current Plan Artifact Package does not bind task required ports: "
                + ", ".join(missing_declarations)
            )
        required = list(dict.fromkeys([*package_required, *declared]))
        ports = {
            str(item.get("logical_name") or ""): item
            for group in ("produced", "inherited")
            for item in package.get(group, [])
            if isinstance(item, Mapping)
        }
        missing = [name for name in required if name not in ports]
        if missing:
            raise ArtifactReadError(
                "current Plan Artifact Package is missing required ports: "
                + ", ".join(missing)
            )
        return [
            {
                "source_id": f"plan-port-{name}",
                "artifact_id": name,
                "kind": str(ports[name].get("artifact_kind") or name),
                "ref": str(ports[name].get("ref") or ""),
                "sha256": str(ports[name].get("sha256") or ""),
                "allowed_paths": ["$"],
            }
            for name in required
        ]

    @staticmethod
    def _declared_required_ports(value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise ArtifactReadError("required_plan_ports must be a list")
        ports: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ArtifactReadError(
                    "required_plan_ports entries must be non-empty strings"
                )
            name = canonical_plan_port_name(item)
            if name in ports:
                raise ArtifactReadError(
                    f"duplicate required_plan_ports entry: {name}"
                )
            ports.append(name)
        return ports

    def _validate_task_snapshots(
        self,
        *,
        payload: Mapping[str, Any],
        workflow_run_id: str,
        task_id: str,
        profile: str,
        currentness: Mapping[str, str],
    ) -> None:
        contract_ref = str(payload.get("contract_snapshot_ref") or "")
        contract_digest = str(payload.get("contract_snapshot_digest") or "")
        if not contract_ref or not contract_digest:
            raise ArtifactReadError(
                "task-bound handoff requires contract snapshot ref/digest"
            )
        expected = {
            "workflow_run_id": workflow_run_id,
            "task_id": task_id,
            **{
                key: value
                for key, value in currentness.items()
                if key != "target_commit" and value
            },
        }
        try:
            contract = hydrate_task_contract_snapshot(
                self.state_dir,
                {
                    "ref": contract_ref,
                    "sha256": contract_digest,
                },
                expected=expected,
            )
        except TaskContractSnapshotError as exc:
            raise ArtifactReadError(
                f"invalid current contract snapshot: {exc}"
            ) from exc

        if profile not in {"task-verify", "candidate-verify"}:
            return
        target_ref = str(payload.get("target_snapshot_ref") or "")
        target_digest = str(payload.get("target_snapshot_digest") or "")
        target_commit = str(payload.get("target_commit") or "")
        if not target_ref or not target_digest or not target_commit:
            raise ArtifactReadError(
                "verify handoff requires target snapshot ref/digest/commit"
            )
        try:
            hydrate_target_snapshot(
                self.state_dir,
                {
                    "ref": target_ref,
                    "sha256": target_digest,
                },
                expected={
                    **{
                        key: contract.get(key)
                        for key in (
                            "workflow_run_id",
                            "task_id",
                            "contract_revision",
                            "task_map_generation",
                            "base_commit",
                            "task_ref",
                            "plan_artifact_package_id",
                            "plan_artifact_package_ref",
                            "plan_artifact_package_digest",
                        )
                    },
                    "contract_snapshot_ref": contract_ref,
                    "contract_snapshot_digest": contract_digest,
                    "target_commit": target_commit,
                },
            )
        except TaskContractSnapshotError as exc:
            raise ArtifactReadError(
                f"invalid current target snapshot: {exc}"
            ) from exc

    @staticmethod
    def _stable_source_snapshot(
        *,
        payload: Mapping[str, Any],
        currentness: Mapping[str, str],
    ) -> dict[str, Any]:
        source_rows: list[tuple[str, str, str]] = []
        for field in ("artifact_refs", "input_refs"):
            values = payload.get(field)
            for item in values if isinstance(values, list) else []:
                if not isinstance(item, Mapping):
                    continue
                source_rows.append((
                    str(item.get("kind") or item.get("source_id") or ""),
                    str(item.get("ref") or item.get("path") or ""),
                    str(item.get("sha256") or ""),
                ))
        for prefix in (
            "contract_snapshot",
            "target_snapshot",
            "impl_self_check",
            "rework_feedback",
            "parent_call_result",
        ):
            ref = str(payload.get(f"{prefix}_ref") or "")
            digest = str(payload.get(f"{prefix}_digest") or "")
            if ref or digest:
                source_rows.append((prefix, ref, digest))
        body = json.dumps(
            {
                "currentness": dict(currentness),
                "sources": sorted(source_rows),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "schema_version": "handoff-source-snapshot.v1",
            "identity_digest": hashlib.sha256(body).hexdigest(),
        }

    def _task_ref_entry(self, task_id: str) -> dict[str, Any]:
        if not task_id:
            return {}
        path = self.state_dir / "refs" / "task-index.json"
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        value = payload.get(task_id)
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _require_match(field: str, incoming: str, current: str) -> None:
        if current and incoming != current:
            raise ArtifactReadError(
                f"stale or missing {field}: incoming {incoming!r}, "
                f"current {current!r}"
            )


__all__ = ["CanonicalHandoffResolver"]
