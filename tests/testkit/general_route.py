"""General five-stage workflow through actual proposal, dispatch and delivery."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from tests.e2e.generic_workflow_complex_support import (
    REQUEST_ID, _RecordingTransport, _proposal, _submit_proposal, _write_base_config,
)
from tests.testkit.issue_route import git
from tests.testkit.model_evidence import ModelEvidence
from zf.cli.flow import build_flow_intake
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.artifact_delivery_result import artifact_delivery_success_payload
from zf.runtime.artifact_read_ledger import read_attempt_artifact, seal_read_ledger, validate_required_reads
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime import result_submit


class GeneralRoute:
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.state = root / "custom-state"
        self.state.mkdir()
        self.config_path = root / "zf.yaml"
        _write_base_config(self.config_path)
        documents = list(yaml.safe_load_all(self.config_path.read_text()))
        documents[0]["spec"]["project"]["state_dir"] = "custom-state"
        self.config_path.write_text(yaml.safe_dump_all(documents, sort_keys=False))
        (root / ".gitignore").write_text("custom-state/\nworkspace/\ndocs/intake/\n")
        (root / "README.md").write_text("Fixture fact: local evidence has no remote dependency.\n")
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.name", "TestKit")
        git(root, "config", "user.email", "testkit@example.invalid")
        git(root, "add", "README.md", "zf.yaml", ".gitignore")
        git(root, "commit", "-q", "-m", "general fixture baseline")
        self.base = git(root, "rev-parse", "HEAD")
        self.invoke: ZfEvent | None = None
        self.deliveries: list = []
        self.reopen()
        self.credentials = {}
        for role in self.config.roles:
            token = f"testkit-public-general-token-{role.name}"
            with monkeypatch.context() as issuance:
                issuance.setattr(result_submit.secrets, "token_urlsafe", lambda _size, token=token: token)
                path = result_submit.provision_role_submit_credential(self.state, role.name)
            self.credentials[role.name] = path.read_text().strip()
        self.evidence = ModelEvidence(
            root=self.state, model_name="official-general-route", source=Path(__file__),
            reference=lambda: {"base": self.base, "invoke": self.invoke.to_json() if self.invoke else None},
            settings="real five-stage reactor; scripted semantic results; 12 ticks per stage",
            max_files=2048, max_bytes=32 * 1024 * 1024,
        )

    def reopen(self) -> None:
        if hasattr(self, "transport"):
            self.deliveries.extend(self.transport.sent)
        self.config = load_config(self.config_path)
        self.log = EventLog(self.state / "events.jsonl")
        self.writer = EventWriter(self.log)
        self.transport = _RecordingTransport()
        self.runtime = Orchestrator(self.state, self.config, self.transport, project_root=self.root)
        self.service = ControlledActionService(
            self.state, self.writer, config=self.config, project_root=self.root,
            actor="mock-operator", source="generic-workflow-e2e", surface="test",
        )
        self.cursor = 0

    def events(self, kind: str) -> list[ZfEvent]:
        return [event for event in self.log.read_all() if event.type == kind]

    def tick(self) -> None:
        rows = self.log.read_all()
        pending = rows[self.cursor:]
        self.cursor = len(rows)
        self.runtime.run_once(events=pending)

    def prepare(self, *, pattern_id: str = "", scope: tuple[str, ...] = ()) -> tuple[Path, dict]:
        """Prepare an installable proposal without starting any Run."""
        intake = build_flow_intake(
            kind="workflow", objective="Deliver a report proving that fixture evidence is local.",
            backend="mock", project_id=self.config.project.name, request_id=REQUEST_ID,
            acceptance=("The report cites the fixture's local evidence and is independently verified.",),
            scope=scope,
            output=self.root / "docs/intake" / f"{REQUEST_ID}.md",
        )
        intake_ref = Path(intake["intake_ref"])
        _, _, preview = _proposal(
            project_root=self.root, state_dir=self.state, config_path=self.config_path,
            intake_ref=intake_ref, manifest_ref=Path(intake["workflow_input_manifest_ref"]),
            writer=self.writer, confirm=True, pattern_id=pattern_id,
        )
        expected = {stage.id for stage in self.config.workflow.stages} | {
            "scope", "collect-1", "collect-2", "synthesize", "verify",
        }
        nodes = preview["proposal"]["stage_graph"]["nodes"]
        assert {node["id"] for node in nodes} == expected
        assert len(nodes) == len(expected)
        assert not self.events("workflow.invoke.requested")
        return intake_ref, preview

    def start(self) -> ZfEvent:
        intake_ref, preview = self.prepare()
        _submit_proposal(service=self.service, writer=self.writer, preview=preview, intake_ref=intake_ref)
        [self.invoke] = self.events("workflow.invoke.requested")
        assert len(self.events("workflow.config.change.applied")) == 1
        self.reopen()  # Load the config installed by the approved proposal.
        return self.dispatch("scope")

    def dispatch(self, stage: str) -> ZfEvent:
        for _ in range(12):
            self.tick()
            rows = [event for event in self.events("fanout.child.dispatched") if event.payload["stage_id"] == stage]
            if rows:
                [event] = rows
                assert event.payload["trace_id"] == self.invoke.payload["workflow_run_id"]
                assert any(row[0] == event.payload["role_instance"] for row in [*self.deliveries, *self.transport.sent])
                return event
        raise AssertionError(f"Missing {stage}: " + json.dumps([
            {"type": event.type, "reason": event.payload.get("reason"), "issues": event.payload.get("issues")}
            for event in self.log.read_all()
        ]))

    def body(self, ref: str, digest: str) -> dict:
        raw = (self.state / ref).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
        return json.loads(raw)

    def result(self, dispatch: ZfEvent, *, read_inputs: bool = True) -> tuple[dict, dict]:
        # Consume this attempt's handoff, never the mutable fanout projection.
        payload = deepcopy(dispatch.payload["payload"])
        child = {**payload, **deepcopy(dispatch.payload)}
        if read_inputs:
            source = self.body(child["attempt_source_manifest_ref"], child["attempt_source_manifest_digest"])
            policy = child.get("input_consumption_policy", {})
            reads = policy.get("required_reads") or [
                {"source_id": row["source_id"], "artifact_id": row["artifact_id"], "json_path": "$"}
                for row in source["sources"]
            ]
            assert reads
            for item in reads:
                read_attempt_artifact(
                    self.state, manifest=source, source_id=item["source_id"], artifact_id=item["artifact_id"],
                    json_path=item["json_path"], actor=child["role_instance"], role=child["role_instance"], provider="mock",
                )
            ledger = seal_read_ledger(self.state, source["attempt_id"])
            if policy:
                assert validate_required_reads(self.state, policy=policy, ledger_descriptor=ledger) == []
            payload.update(read_ledger_ref=ledger["ref"], read_ledger_digest=ledger["sha256"],
                           input_consumption_status="satisfied")
        return child, payload

    def emit(self, dispatch: ZfEvent, child: dict, payload: dict) -> ZfEvent:
        assert payload["semantic_result_submit_mode"] == "blocking"
        service = result_submit.SemanticResultSubmitService(
            state_dir=self.state, event_log=self.log, event_writer=self.writer,
        )
        profile = service.registry.profile(payload["output_profile_id"], payload["output_profile_revision"])
        scratch = self.state / payload["result_scratch_ref"]
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text(json.dumps(payload[profile.semantic_field]))
        submitted = service.submit(
            operation_id=payload["operation_id"], use_operation_scratch=True,
            role_instance=child["role_instance"], credential=self.credentials[child["role_instance"]],
        )
        [event] = [row for row in self.log.read_all() if row.id == submitted.canonical_event_id]
        assert event.payload["semantic_submit_admission_event_id"] == submitted.admitted_event_id
        assert event.task_id == dispatch.task_id
        return event

    def complete_reader(self, dispatch: ZfEvent) -> ZfEvent:
        child, payload = self.result(dispatch)
        stage = dispatch.payload["stage_id"]
        # Worker semantics are synthetic; output publication/admission stays real.
        report = {
            "status": "passed", "summary": f"{stage}: local fixture evidence inspected.",
            "findings": [], "recommendation": "approve",
            "outputs": {port["name"]: f"# {stage}\nFixture evidence is local; no remote dependency.\n"
                        for port in payload["workflow_output_ports"]},
        }
        for key in ("workflow_run_id", "stage_id", "generic_workflow_operation", "result_semantics", "workflow_output_ports"):
            report[key] = payload[key]
        payload["report"] = report
        return self.emit(dispatch, child, payload)

    def complete_verify(self, dispatch: ZfEvent, *, read_inputs: bool = True) -> ZfEvent:
        child, payload = self.result(dispatch, read_inputs=read_inputs)
        template = artifact_delivery_success_payload(
            payload, verifier_stage_id=dispatch.payload["stage_id"], verifier_role=child["role_instance"], state_dir=self.state,
        )
        result = template["artifact_delivery_result"]
        for artifact in result["artifacts"]:
            raw = (self.state / artifact["ref"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == artifact["sha256"]
            assert b"Fixture evidence is local; no remote dependency." in raw
            assert artifact["source_ref"] == "synthesize.report"
        refs = [artifact["ref"] for artifact in result["artifacts"]]
        claims = self.body(payload["goal_claim_set_ref"], payload["goal_claim_set_digest"])
        result.update(
            verification_evidence_refs=refs,
            goal_coverage=[{"goal_claim_id": claim["goal_claim_id"], "status": "closed", "supporting_artifact_refs": refs}
                           for claim in claims["claims"]],
            summary="The admitted synthesis contains the local fixture fact.",
        )
        payload.update(template)
        payload["report"] = {"status": "passed", "summary": result["summary"], "findings": [], "recommendation": "approve"}
        return self.emit(dispatch, child, payload)
