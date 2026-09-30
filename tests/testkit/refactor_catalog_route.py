"""Deploy the full catalog before driving the existing two-lane Kernel route."""

from copy import deepcopy
from pathlib import Path
import shutil

import yaml

from tests.e2e.test_orchestrator_agent_semantic_control_mock_e2e import _adopt_decision
from tests.e2e.generic_workflow_complex_support import _RecordingTransport
from tests.testkit.issue_route import git
from tests.testkit.refactor_two_lane_route import TwoLaneStrongRefactorRoute
from zf.core.profile.apply import materialize_flow_assets
from zf.core.profile.flows import flow_path
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.orchestrator_agent_operations import prepared_operation_from_checkpoint_event
from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential


class ReadyRecordingTransport(_RecordingTransport):
    """Already-live provider substitute; never start a host process."""

    def wait_ready(self, role_name, pattern, timeout):
        return self.is_alive(role_name)


class CatalogRefactorRoute(TwoLaneStrongRefactorRoute):
    request_backend = "codex"

    def __init__(self, root, monkeypatch):
        super().__init__(root, monkeypatch)
        self.catalog_results: dict[str, str] = {}
        source = flow_path("refactor-lane-v3-codex")
        assert source is not None
        path = root / "zf.yaml"
        shutil.copy2(source, path)
        self.catalog_assets = materialize_flow_assets("refactor-lane-v3-codex", root, config_path=path)
        docs = list(yaml.safe_load_all(path.read_text()))
        self.catalog_documents = deepcopy(docs)
        flow, config = docs
        flow["spec"].update(objectiveRef="docs/intake/bug.md", parityScope=["core"],
                             sourceRoot=str(self.source_root), targetRoot=str(root))
        spec = config["spec"]
        spec["project"]["state_dir"] = "custom-state"
        spec["runtime"]["git"]["remote_policy"] = "local_only"
        path.write_text(yaml.safe_dump_all(docs, sort_keys=False))
        git(root, "add", "--", "zf.yaml", "common/profiles.yaml", "skills")
        git(root, "commit", "-q", "-m", "fixture complete catalog deployment", "--",
            "zf.yaml", "common/profiles.yaml", "skills")
        self.base = git(root, "rev-parse", "HEAD")
        self.reopen()
        token_path = provision_role_submit_credential(self.state, "orchestrator")
        self.credentials["orchestrator"] = token_path.read_text().strip()
        self.evidence.model_name = "official-refactor-catalog-route"
        self.evidence.source = Path(__file__).resolve()
        self.evidence.settings = "full catalog/profile/skills deployment; recording transport; two-lane local delivery"

    def reopen(self):
        super().reopen()
        self.transport = ReadyRecordingTransport()
        self.runtime.transport = self.transport

    def tick(self):
        # The real transport changes an admitted operation from ``requested``
        # to ``running`` during the same tick that dispatches it.  Capture the
        # durable prepared context before that transition; reconstructing from
        # the request event after the tick is intentionally fail-closed.
        prepared_before_tick = {}
        for requested in self.events("orchestrator.semantic.checkpoint.requested"):
            operation_id = requested.payload["operation_id"]
            if operation_id in self.catalog_results:
                continue
            prepared = prepared_operation_from_checkpoint_event(self.runtime, requested)
            if prepared is not None:
                prepared_before_tick[operation_id] = prepared
        super().tick()
        for requested in self.events("orchestrator.semantic.checkpoint.requested"):
            operation_id = requested.payload["operation_id"]
            if operation_id in self.catalog_results:
                continue
            deliveries = [*self.deliveries, *self.transport.sent]
            if not any(role == "orchestrator" and operation_id in str(briefing)
                       for role, briefing, _, _ in deliveries):
                continue
            prepared = prepared_before_tick.get(operation_id)
            assert prepared is not None
            for source in prepared.context.source_manifest["sources"]:
                read_attempt_artifact(
                    self.state, manifest=prepared.context.source_manifest,
                    source_id=source["source_id"], artifact_id=source["artifact_id"],
                    actor="orchestrator", role="orchestrator", provider="mock",
                )
            result = SemanticResultSubmitService(
                state_dir=self.state, event_log=self.log, event_writer=self.writer,
            ).submit(operation_id=operation_id, semantic_result=_adopt_decision(prepared),
                     role_instance="orchestrator", credential=self.credentials["orchestrator"])
            self.catalog_results[operation_id] = result.canonical_event_id
