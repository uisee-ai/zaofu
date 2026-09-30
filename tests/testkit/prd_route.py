"""Default PRD fanout route: scripted workers, unmodified Kernel gates."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tests.testkit.issue_route import IssueRoute
from tests.testkit.model_evidence import ModelEvidence
from zf.core.events import ZfEvent
from zf.runtime.artifact_read_ledger import read_attempt_artifact, seal_read_ledger
from zf.runtime import result_submit
from zf.runtime.sidecar_refs import write_sidecar_json


class PrdRoute(IssueRoute):
    delivery_task_id = "TASK-PRD-ANSWER"
    artifact_namespace = "testkit-prd"

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch, *, flow_kind: str = "prd",
                 flow_spec: dict | None = None) -> None:
        super().__init__(root, flow_kind=flow_kind, flow_spec=flow_spec)
        self.credentials = {}
        for role in self.config.roles:
            token = f"testkit-public-prd-token-{role.name}"
            with monkeypatch.context() as issuance:
                issuance.setattr(result_submit.secrets, "token_urlsafe", lambda _size, token=token: token)
                path = result_submit.provision_role_submit_credential(self.state, role.name)
            self.credentials[role.name] = path.read_text().strip()
        self.evidence = ModelEvidence(
            root=self.state, model_name="official-prd-route", source=Path(__file__),
            reference=lambda: {"base": self.base, "invoke": self.invoke.to_json() if self.invoke else None},
            settings="default fanout PRD; local Git/probes; event readers and signed synth; 12 ticks per stage",
            max_files=2048, max_bytes=32 * 1024 * 1024,
        )

    def plan_to_writer(self, *, reopen_after_plan: bool = False) -> ZfEvent:
        self.start()
        assert self.submit()["ok"]
        assert len(self.events("workflow.invoke.requested")) == 1
        return self.continue_to_writer(reopen_after_plan=reopen_after_plan)

    def continue_to_writer(self, *, reopen_after_plan: bool = False) -> ZfEvent:
        """Consume the already admitted Run, including browser-origin invokes."""
        for role in ("product-scan", "tech-scan"):
            self.complete_reader(self.dispatch("prd-scan", role))
        self.complete_reader(self.dispatch("prd-plan"), plan=True)
        if reopen_after_plan:
            self.reopen()
        self.complete_critic()
        writer = self.dispatch("prd-lanes-impl")
        assert writer.payload["task_id"] == "TASK-PRD-ANSWER"
        assert self.events("plan.artifact_package.admitted")
        return writer

    def dispatch(self, stage_id: str, role: str = "") -> ZfEvent:
        for _ in range(12):
            self.tick()
            rows = [event for event in self.events("fanout.child.dispatched")
                    if event.payload["stage_id"] == stage_id
                    and (not role or event.payload["role_instance"] == role)]
            if rows:
                event = rows[0]
                assert event.payload["trace_id"] == self.invoke.payload["workflow_run_id"]
                assert any(row[0] == event.payload["role_instance"] for row in [*self.deliveries, *self.transport.sent])
                return event
        raise AssertionError(f"Missing {stage_id}/{role}: " + json.dumps([
            {"type": event.type, "reason": event.payload.get("reason"), "issues": event.payload.get("issues")}
            for event in self.log.read_all()
        ]))

    def consume(self, payload: dict) -> dict:
        descriptor = payload["attempt_source_manifest"]
        source = self.body(descriptor["ref"], descriptor["sha256"])
        reads = payload.get("required_reads") or [
            {"source_id": row["source_id"], "artifact_id": row["artifact_id"], "json_path": "$"}
            for row in source["sources"]
        ]
        for item in reads:
            read_attempt_artifact(
                self.state, manifest=source, source_id=item["source_id"], artifact_id=item["artifact_id"],
                json_path=item.get("json_path", "$"), actor=payload["role_instance"],
                role=payload["role_instance"], provider="mock",
            )
        return seal_read_ledger(self.state, source["attempt_id"])

    def submit_semantic(self, payload: dict, semantic: dict) -> ZfEvent:
        self.consume(payload)
        scratch = self.state / payload["result_scratch_ref"]
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text(json.dumps(semantic))
        submitted = result_submit.SemanticResultSubmitService(
            state_dir=self.state, event_log=self.log, event_writer=self.writer,
        ).submit(
            operation_id=payload["operation_id"], use_operation_scratch=True,
            role_instance=payload["role_instance"], credential=self.credentials[payload["role_instance"]],
        )
        [event] = [row for row in self.log.read_all() if row.id == submitted.canonical_event_id]
        return event

    def complete_reader(self, dispatch: ZfEvent, *, plan: bool = False, extra: dict | None = None) -> ZfEvent:
        # The dispatch is durable before its child manifest projection catches up.
        payload = deepcopy(dispatch.payload["payload"])
        result = {}
        result.update(status="passed", recommendation="approve", findings=[],
                      summary="Fixture PRD requires answer.txt to contain fixed.",
                      evidence_refs=["docs/intake/bug.md"])
        if plan:
            result.update(self.plan_result())
        result.update(extra or {})
        ledger = self.consume(payload)
        payload.update(status="completed", report=result, evidence_refs=result["evidence_refs"],
                       read_ledger_ref=ledger["ref"], read_ledger_digest=ledger["sha256"],
                       input_consumption_status="satisfied")
        return self.publish_result(dispatch, payload)

    def plan_result(self) -> dict:
        task_map = {
            "schema_version": "task-map.v1", "feature_id": self.invoke.payload["goal_id"], "target_root": ".",
            "goal_claims": [{"goal_claim_id": "CLAIM-FIXED", "text": "answer.txt contains fixed", "mandatory": True}],
            "tasks": [{
                "task_id": self.delivery_task_id, "title": "Deliver the requested answer", "owner_role": "dev",
                "goal_claim_ids": ["CLAIM-FIXED"], "allowed_paths": ["answer.txt"],
                "verification_read_paths": ["tests/verify_issue.py"],
                "scope": ["answer.txt"], "allowed_paths_reason": "The single fixture deliverable.",
                "blocked_by": [], "verification": self.command,
                "acceptance_criteria": [{"id": "AC-FIXED", "statement": "answer.txt contains fixed", "mandatory": True,
                                         "verification_owner": "task_verify", "verification_tier": "task_non_smoke",
                                         "verification_command_ids": ["answer-probe"]}],
                "validation": {"commands": [{"id": "answer-probe", "command": self.command,
                                             "acceptance_ids": ["AC-FIXED"], "owner": "impl_self_check",
                                             "tier": "task_non_smoke", "deterministic": True, "reusable": True,
                                             "timeout_seconds": 15}]},
            }],
        }
        source_index = {"schema_version": "source-index.v1", "tasks": [{
            "task_id": self.delivery_task_id, "source_key": "fixture-prd-answer",
            "source_ref": "docs/intake/bug.md", "source_excerpt": "answer.txt must contain fixed.",
        }]}
        refs = {}
        for name, body in (("task-map", task_map), ("source-index", source_index)):
            refs[name] = write_sidecar_json(
                self.state, f"artifacts/{self.artifact_namespace}/{name}.json", body,
                kind=name, schema_version=body["schema_version"], created_by="planner",
            )
        self.plan = {"task_map": task_map, "task_map_ref": refs["task-map"]["ref"],
                     "source_index_ref": refs["source-index"]["ref"],
                     "plan_md": "# PRD plan\nDeliver answer.txt and independently run the declared probe.\n",
                     "evidence_refs": [refs["task-map"]["ref"], refs["source-index"]["ref"]]}
        return self.plan

    def complete_critic(self) -> ZfEvent:
        for _ in range(12):
            self.tick()
            rows = self.events("fanout.synth.dispatched")
            if rows:
                [dispatch] = rows
                payload = dispatch.payload
                assert payload["output_profile_id"] == "plan-synth"
                result = json.loads((self.state / payload["result_scratch_ref"]).read_text())
                result.update(status="completed", recommendation="approve", summary="The bounded plan matches the fixture PRD.")
                result["report"].update(self.plan, status="passed", recommendation="approve", summary=result["summary"])
                return self.submit_semantic(payload, result)
        raise AssertionError("No critic dispatch: " + json.dumps([
            {"type": event.type, "issues": event.payload.get("issues"),
             "reason": event.payload.get("reason"), "feedback": event.payload.get("rework_feedback")}
            for event in self.log.read_all()
            if any(word in event.type for word in ("failed", "rejected", "rework", "blocked"))
        ]))
