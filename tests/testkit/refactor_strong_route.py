"""Catalog Refactor policies with signed workers and real local acceptance."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import yaml
import pytest

from tests.testkit.refactor_route import RefactorRoute
from zf.core.events import ZfEvent
from zf.runtime.product_acceptance import product_acceptance_binding_from_package


class StrongRefactorRoute(RefactorRoute):
    acceptance_fault = ""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(root, monkeypatch)
        self.evidence.model_name = "official-refactor-strong-route"
        self.evidence.source = Path(__file__).resolve()
        self.evidence.settings = "catalog blocking policies; single lane; signed semantics; real local Git/probes/ship"

    def flow_spec(self) -> dict:
        template = Path(__file__).parents[2] / "examples/prod/controller/refactor-lane-v3.yaml"
        spec = deepcopy(next(yaml.safe_load_all(template.read_text()))["spec"])
        spec.update(lanes=1, objectiveRef="docs/intake/bug.md", parityScope=["core"],
                    sourceRoot=str(self.source_root), targetRoot=str(self.root))
        spec["roleDefaults"]["backend"] = spec["laneRoleTemplate"]["backend"] = "mock"
        assert set(spec["semanticSubmitProfiles"].values()) == {"blocking"}
        assert spec["artifactPackageMode"] == spec["productAcceptanceMode"] == "blocking"
        assert spec["deliveryPolicy"] == "ship_candidate"
        return spec

    def plan_result(self) -> dict:
        plan = super().plan_result()
        if self.acceptance_fault == "missing_spec":
            return plan
        plan["plan_ports"] = [{
            "logical_name": "product_acceptance_spec",
            "body": {
                "schema_version": "product_acceptance_spec.v1",
                "assembly_owner": self.delivery_task_id,
                "entrypoints": [{"entrypoint_id": "answer-cli", "start_command": self.command,
                                 "health_check": self.command, "owner": self.delivery_task_id}],
                "user_journeys": [{
                    "journey_id": "read-answer", "title": "Read the corrected answer",
                    "mandatory": True, "verification_tier": "runtime",
                    "assertions": [{"assertion_id": "answer-fixed", "statement": "answer is fixed",
                                    "observation_kind": "cli", "mandatory": True}],
                }],
                "provider_qualification": {"required_for_goal": False, "providers": [], "ttl_seconds": 0},
            },
        }]
        return plan

    def publish_result(self, dispatch: ZfEvent, payload: dict) -> ZfEvent:
        [requested] = [row for row in self.events("workflow.operation.requested")
                       if row.payload["operation_id"] == payload["operation_id"]]
        descriptor = requested.payload["request_ref"]
        request = self.body(descriptor["ref"], descriptor["sha256"])["request"]
        payload = {**payload, **{key: request[key] for key in (
            "semantic_result_submit_mode", "output_profile_id", "result_scratch_ref",
        )}}
        if "attempt_source_manifest" not in payload:
            payload["attempt_source_manifest"] = self.child(dispatch)["attempt_source_manifest"]
        assert payload["semantic_result_submit_mode"] == "blocking"
        profile = payload["output_profile_id"]
        if profile == "implementation":
            scratch = json.loads((self.state / payload["result_scratch_ref"]).read_text())
            self_check = deepcopy(payload["impl_self_check"])
            self_check["attempt_id"] = scratch["self_check"]["attempt_id"]
            semantic = {
                "verdict": "passed", "target_commit": payload["source_commit"],
                "changed_files": payload["changed_files"], "self_check": self_check,
                "evidence_refs": payload["evidence_refs"], "known_gaps": [],
                "summary": "Committed answer and executed the declared regression.",
            }
        elif profile in {"candidate-verify", "task-verify"}:
            semantic = deepcopy(payload["verification_result"])
            if self.acceptance_fault != "missing_report":
                binding = product_acceptance_binding_from_package(self.state, {
                    "ref": payload["plan_artifact_package_ref"],
                    "sha256": payload["plan_artifact_package_digest"],
                })
                assert binding["required"]
                refs = [{"ref": ref, "sha256": hashlib.sha256((self.state / ref).read_bytes()).hexdigest()}
                        for ref in payload["evidence_refs"]]
                semantic["product_acceptance_report"] = {
                    "schema_version": "product_acceptance_report.v1",
                    "workflow_run_id": binding["package"]["workflow_run_id"],
                    "task_map_generation": binding["package"]["task_map_generation"],
                    "candidate_ref": payload["candidate_ref"], "target_commit": payload["target_commit"],
                    "product_acceptance_spec_ref": binding["spec_ref"],
                    "product_acceptance_spec_digest": binding["spec_digest"],
                    "verdict": "passed", "provider_qualification_receipts": [],
                    "journey_results": [{"journey_id": "read-answer", "status": "passed",
                                         "evidence_refs": refs, "assertion_results": [{
                                             "assertion_id": "answer-fixed", "status": "passed",
                                             "evidence_refs": refs,
                                         }]}],
                }
                if self.acceptance_fault == "stale_target":
                    semantic["product_acceptance_report"]["target_commit"] = self.base
        elif profile == "thin-judge-goal-closure":
            semantic = payload["goal_closure_result"]
        else:
            assert profile == "workflow-read", profile
            semantic = payload["report"]
        return self.submit_semantic(payload, semantic)
