"""Current authority checks for admitted agent call results."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.call_result_adapters import AdaptedControlResult
from zf.runtime.call_result_envelope import (
    CallResultEnvelopeError,
    hydrate_call_result_envelope,
)
from zf.runtime.candidate_result_binding import (
    candidate_task_source_commits,
    same_task_map_generation,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError,
    current_task_contract_identity,
    hydrate_target_snapshot,
    hydrate_task_contract_snapshot,
)


class CallResultAuthorityMixin:
    """Validate result identity against current canonical runtime authority."""

    def _task_result_currentness_issues(
        self,
        envelope: Mapping[str, Any],
        adapted: AdaptedControlResult,
    ) -> list[dict[str, str]]:
        if adapted.schema_version not in {
            "implementation-result.v1",
            "verification-result.v1",
        }:
            return []
        identity = (
            envelope.get("identity")
            if isinstance(envelope.get("identity"), Mapping)
            else {}
        )
        task_id = str(identity.get("task_id") or adapted.payload.get("task_id") or "")
        contract_ref = str(identity.get("contract_snapshot_ref") or "")
        contract_digest = str(identity.get("contract_snapshot_digest") or "")
        if not task_id or not contract_ref or not contract_digest:
            return []

        task = TaskStore(self.state_dir / "kanban.json").get(task_id)
        if task is None:
            return []
        if str(task.status or "") in {"cancelled", "superseded"}:
            return [_currentness_issue(
                "control_result.task_id",
                "stale_task_authority",
                f"canonical task {task_id!r} is {task.status}",
            )]

        try:
            current = current_task_contract_identity(task)
        except TaskContractSnapshotError as exc:
            return [_currentness_issue(
                "control_result.task_map_generation",
                "stale_task_authority",
                str(exc),
            )]
        issues: list[dict[str, str]] = []
        for field, code in (
            ("contract_revision", "stale_contract_revision"),
            ("task_map_generation", "stale_task_map_generation"),
        ):
            actual = str(identity.get(field) or adapted.payload.get(field) or "")
            expected = str(current.get(field) or "")
            matches = (
                same_task_map_generation(actual, expected)
                if field == "task_map_generation"
                else actual == expected
            )
            if expected and not matches:
                issues.append(_currentness_issue(
                    f"control_result.{field}",
                    code,
                    f"current TaskStore expects {expected}, got {actual or '<missing>'}",
                ))

        snapshot_expected = {
            key: identity.get(key)
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
            if identity.get(key) not in (None, "")
        }
        snapshot_expected.update(current)
        try:
            contract_snapshot = hydrate_task_contract_snapshot(
                self.state_dir,
                {"ref": contract_ref, "sha256": contract_digest},
                expected=snapshot_expected,
            )
        except TaskContractSnapshotError as exc:
            issues.append(_currentness_issue(
                "control_result.contract_snapshot_ref",
                "stale_contract_snapshot",
                str(exc),
            ))
            return issues

        if adapted.schema_version != "verification-result.v1":
            return issues
        target_ref = str(identity.get("target_snapshot_ref") or "")
        target_digest = str(identity.get("target_snapshot_digest") or "")
        target_commit = str(identity.get("target_commit") or "")
        try:
            hydrate_target_snapshot(
                self.state_dir,
                {"ref": target_ref, "sha256": target_digest},
                expected={
                    **{
                        key: contract_snapshot.get(key)
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
            issues.append(_currentness_issue(
                "control_result.target_snapshot_ref",
                "stale_target_snapshot",
                str(exc),
            ))
            return issues

        current_candidate = self._latest_candidate_for_run(
            str(identity.get("workflow_run_id") or ""),
        )
        if current_candidate is None:
            return issues
        candidate_payload = (
            current_candidate.payload
            if isinstance(current_candidate.payload, dict)
            else {}
        )
        expected_generation = str(candidate_payload.get("task_map_generation") or "")
        if expected_generation and not same_task_map_generation(
            str(identity.get("task_map_generation") or ""),
            expected_generation,
        ):
            issues.append(_currentness_issue(
                "control_result.task_map_generation",
                "stale_task_map_generation",
                f"current candidate expects {expected_generation}",
            ))
        candidate_commit = str(candidate_payload.get("candidate_head_commit") or "")
        allowed_targets = {candidate_commit} if candidate_commit else set()
        allowed_targets.update(candidate_task_source_commits(
            self.event_log.read_all(),
            workflow_run_id=str(identity.get("workflow_run_id") or ""),
            candidate_head_commit=candidate_commit,
        ).values())
        if allowed_targets and target_commit not in allowed_targets:
            issues.append(_currentness_issue(
                "control_result.target_commit",
                "stale_target_commit",
                f"current candidate targets {sorted(allowed_targets)}, got {target_commit}",
            ))
        return issues

    def _plan_package_currentness_issues(
        self,
        envelope: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        identity = (
            envelope.get("identity")
            if isinstance(envelope.get("identity"), Mapping)
            else {}
        )
        workflow_run_id = str(identity.get("workflow_run_id") or "")
        if not workflow_run_id:
            return []
        from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages

        reduced = reduce_plan_artifact_packages(
            self.event_log.read_all(),
            workflow_run_id=workflow_run_id,
        )
        current = reduced.get("current")
        if not isinstance(current, Mapping):
            return []
        actual_digest = str(identity.get("plan_artifact_package_digest") or "")
        blocking = str(current.get("mode") or "") == "blocking"
        if not actual_digest and not blocking:
            return []
        issues: list[dict[str, str]] = []
        for field, event_field in (
            ("plan_artifact_package_id", "package_id"),
            ("plan_artifact_package_ref", "package_ref"),
            ("plan_artifact_package_digest", "package_digest"),
        ):
            expected = str(current.get(event_field) or "")
            actual = str(identity.get(field) or "")
            if expected and actual != expected:
                issues.append(_currentness_issue(
                    f"control_result.{field}",
                    "stale_plan_artifact_package",
                    f"current package expects {expected}, got {actual or '<missing>'}",
                ))
        return issues

    def _operation_result_currentness_issues(
        self,
        envelope: Mapping[str, Any],
        operation: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        expected = operation.get("result_identity")
        if not isinstance(expected, Mapping):
            return []
        identity = (
            envelope.get("identity")
            if isinstance(envelope.get("identity"), Mapping)
            else {}
        )
        fields = (
            ("workflow_run_id", "workflow_run_id", "stale_operation_identity"),
            ("task_id", "task_id", "stale_operation_identity"),
            ("fanout_id", "fanout_id", "stale_operation_identity"),
            ("stage_id", "producer_stage_id", "stale_operation_identity"),
            ("child_id", "child_id", "stale_operation_identity"),
            ("run_id", "attempt_id", "stale_operation_identity"),
            ("attempt_domain", "attempt_domain", "stale_attempt_domain"),
            ("role_instance", "producer_role", "stale_operation_identity"),
            ("plan_revision", "plan_revision", "stale_plan_revision"),
            (
                "plan_synth_contract_ref",
                "plan_synth_contract_ref",
                "stale_plan_revision",
            ),
            (
                "plan_synth_contract_digest",
                "plan_synth_contract_digest",
                "stale_plan_revision",
            ),
            ("contract_revision", "contract_revision", "stale_contract_revision"),
            ("task_map_generation", "task_map_generation", "stale_task_map_generation"),
            (
                "plan_artifact_package_id",
                "plan_artifact_package_id",
                "stale_plan_artifact_package",
            ),
            (
                "plan_artifact_package_ref",
                "plan_artifact_package_ref",
                "stale_plan_artifact_package",
            ),
            (
                "plan_artifact_package_digest",
                "plan_artifact_package_digest",
                "stale_plan_artifact_package",
            ),
            ("base_commit", "base_commit", "stale_contract_snapshot"),
            ("task_ref", "task_ref", "stale_contract_snapshot"),
            ("contract_snapshot_ref", "contract_snapshot_ref", "stale_contract_snapshot"),
            ("contract_snapshot_digest", "contract_snapshot_digest", "stale_contract_snapshot"),
            ("target_snapshot_ref", "target_snapshot_ref", "stale_target_snapshot"),
            ("target_snapshot_digest", "target_snapshot_digest", "stale_target_snapshot"),
            ("target_commit", "target_commit", "stale_target_commit"),
        )
        issues: list[dict[str, str]] = []
        for request_field, envelope_field, code in fields:
            expected_value = str(expected.get(request_field) or "")
            if not expected_value:
                continue
            actual_value = str(identity.get(envelope_field) or "")
            if actual_value != expected_value:
                issues.append(_currentness_issue(
                    f"operation.result_identity.{request_field}",
                    code,
                    f"dispatch pinned {expected_value}, got {actual_value or '<missing>'}",
                ))
        return issues

    def _latest_candidate_for_run(self, workflow_run_id: str) -> ZfEvent | None:
        for event in reversed(self.event_log.read_all()):
            if event.type != "candidate.ready":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            event_run_id = str(
                payload.get("workflow_run_id")
                or payload.get("trace_id")
                or event.correlation_id
                or ""
            )
            if not workflow_run_id or event_run_id == workflow_run_id:
                return event
        return None

    def _goal_closure_issues(
        self,
        result: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        from zf.runtime.goal_closure_result import claim_set_issues
        from zf.runtime.sidecar_refs import SidecarRefError

        issues: list[dict[str, str]] = []
        claim_ref = str(result.get("goal_claim_set_ref") or "")
        claim_digest = str(result.get("goal_claim_set_digest") or "")
        try:
            hydrated = hydrate_sidecar_ref(
                self.state_dir,
                {"ref": claim_ref, "sha256": claim_digest},
            )
            claim_set = hydrated.payload if isinstance(hydrated.payload, dict) else {}
        except (SidecarRefError, OSError, ValueError) as exc:
            issues.append({
                "field": "control_result.goal_claim_set_ref",
                "code": "claim_set_unreadable",
                "message": str(exc),
            })
            claim_set = {}

        events = self.event_log.read_all()
        workflow_run_id = str(result.get("workflow_run_id") or "")
        task_map_generation = str(result.get("task_map_generation") or "")
        target_commit = str(result.get("target_commit") or "")
        task_source_commits = candidate_task_source_commits(
            events,
            workflow_run_id=workflow_run_id,
            candidate_head_commit=target_commit,
        )
        admitted_refs: set[str] = set()
        for event in events:
            if event.type != "workflow.call.result.admitted" or not isinstance(event.payload, dict):
                continue
            if str(event.payload.get("workflow_run_id") or "") != workflow_run_id:
                continue
            descriptor = event.payload.get("envelope_ref")
            if not isinstance(descriptor, Mapping):
                continue
            try:
                envelope = hydrate_call_result_envelope(self.state_dir, descriptor)
            except (CallResultEnvelopeError, OSError, ValueError):
                continue
            identity = (
                envelope.get("identity")
                if isinstance(envelope.get("identity"), Mapping)
                else {}
            )
            result_generation = str(identity.get("task_map_generation") or "")
            result_target = str(identity.get("target_commit") or "")
            if result_generation and not same_task_map_generation(
                result_generation,
                task_map_generation,
            ):
                continue
            if result_target and result_target != target_commit:
                result_task_id = str(identity.get("task_id") or "")
                if task_source_commits.get(result_task_id) != result_target:
                    continue
            ref = str(descriptor.get("ref") or "")
            if ref:
                admitted_refs.add(ref)
        issues.extend(claim_set_issues(
            result,
            claim_set,
            admitted_result_refs=admitted_refs,
            claim_set_descriptor_digest=claim_digest,
        ))
        for ref in _string_values(result.get("input_result_refs")):
            if ref not in admitted_refs:
                issues.append({
                    "field": "control_result.input_result_refs",
                    "code": "result_not_admitted",
                    "message": ref,
                })

        latest_claim_pin = None
        for candidate in reversed(events):
            if candidate.type != "goal.claim_set.pinned":
                continue
            body = candidate.payload if isinstance(candidate.payload, dict) else {}
            if (
                str(body.get("workflow_run_id") or "") == workflow_run_id
                and str(body.get("goal_id") or "") == str(result.get("goal_id") or "")
            ):
                latest_claim_pin = body
                break
        if latest_claim_pin is not None:
            for result_key, pin_key in {
                "task_map_generation": "task_map_generation",
                "goal_claim_set_ref": "goal_claim_set_ref",
                "goal_claim_set_digest": "goal_claim_set_digest",
            }.items():
                expected = str(latest_claim_pin.get(pin_key) or "")
                actual = str(result.get(result_key) or "")
                matches = (
                    same_task_map_generation(actual, expected)
                    if result_key == "task_map_generation"
                    else actual == expected
                )
                if expected and not matches:
                    issues.append({
                        "field": f"control_result.{result_key}",
                        "code": "stale_closure_identity",
                        "message": (
                            "latest pinned Goal claim set expects "
                            f"{expected}, got {actual}"
                        ),
                    })

        from zf.runtime.waivers import active_waivers

        coverage = result.get("goal_coverage")
        for index, item in enumerate(coverage if isinstance(coverage, list) else []):
            if not isinstance(item, Mapping) or str(item.get("status") or "") != "waived":
                continue
            waiver_ref = str(item.get("waiver_ref") or "").strip()
            claim_id = str(item.get("goal_claim_id") or "").strip()
            active: list[dict[str, Any]] = []
            for scope in dict.fromkeys((claim_id, str(result.get("goal_id") or ""))):
                if scope:
                    active.extend(active_waivers(events, scope))
            valid_refs = {
                str(value)
                for waiver in active
                for value in (waiver.get("signature"), waiver.get("event_id"))
                if str(value or "").strip()
            }
            if waiver_ref not in valid_refs:
                issues.append({
                    "field": f"control_result.goal_coverage[{index}].waiver_ref",
                    "code": "waiver_not_active",
                    "message": waiver_ref,
                })

        current = None
        for event in reversed(events):
            if event.type not in {"flow.goal.closed", "module.parity.closed"}:
                continue
            body = event.payload if isinstance(event.payload, dict) else {}
            if (
                str(body.get("workflow_run_id") or "") == workflow_run_id
                and str(body.get("goal_id") or "") == str(result.get("goal_id") or "")
            ):
                current = body
                break
        if current is None:
            issues.append({
                "field": "control_result.closure_fact_ref",
                "code": "closure_not_current",
                "message": "no current closure fact for run/goal",
            })
        else:
            for result_key, closure_key in {
                "task_map_generation": "task_map_generation",
                "target_commit": "candidate_head_commit",
                "goal_claim_set_ref": "goal_claim_set_ref",
                "goal_claim_set_digest": "goal_claim_set_digest",
                "closure_fact_ref": "closure_fact_ref",
                "closure_fact_digest": "closure_fact_digest",
            }.items():
                expected = str(current.get(closure_key) or "")
                actual = str(result.get(result_key) or "")
                if expected and actual != expected:
                    issues.append({
                        "field": f"control_result.{result_key}",
                        "code": "stale_closure_identity",
                        "message": f"expected {expected}, got {actual}",
                    })
        return issues


def _currentness_issue(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _string_values(value: Any) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else [value] if value else []
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


__all__ = ["CallResultAuthorityMixin"]
