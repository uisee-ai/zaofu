"""Refactor v3 default compiler path with real admission and scripted semantics."""

from pathlib import Path

import pytest

from tests.testkit.model_evidence import ModelEvidence
from tests.testkit.prd_route import PrdRoute
from zf.core.events import ZfEvent


class RefactorRoute(PrdRoute):
    delivery_task_id = "TASK-REFACTOR-ANSWER"
    artifact_namespace = "testkit-refactor"

    def flow_spec(self) -> dict | None:
        return None

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root.mkdir()
        self.source_root = root.parent / "source"
        self.source_root.mkdir()
        (self.source_root / "answer.txt").write_text("fixed\n")
        self.root = root
        super().__init__(root, monkeypatch, flow_kind="refactor", flow_spec=self.flow_spec())
        self.evidence = ModelEvidence(
            root=self.state, model_name="official-refactor-route", source=Path(__file__),
            reference=lambda: {"base": self.base, "invoke": self.invoke.to_json() if self.invoke else None},
            settings="refactor-flow/v3 defaults; scripted semantics; local Git/probes; 12 ticks per stage",
            max_files=2048, max_bytes=32 * 1024 * 1024,
        )

    def plan_to_writer(self, *, reopen_after_plan: bool = False) -> ZfEvent:
        self.start()
        return self.continue_to_writer(reopen_after_plan=reopen_after_plan)

    def continue_to_writer(self, *, reopen_after_plan: bool = False) -> ZfEvent:
        scan = next(stage for stage in self.config.workflow.stages if stage.id == "flow-scan")
        assert scan.roles == ["scan-contract", "scan-runtime", "scan-verification"]
        for role in scan.roles:
            self.complete_reader(self.dispatch("flow-scan", role), extra={
                "coverage_matrix": [{"area": "core", "status": "covered", "source_ref": "answer.txt"}],
            })
        self.complete_reader(self.dispatch("flow-plan"), plan=True)
        if reopen_after_plan:
            self.reopen()
        self.complete_critic()
        writer = self.dispatch("flow-lanes-impl")
        assert writer.payload["task_id"] == self.delivery_task_id
        assert self.events("plan.artifact_package.admitted")
        return writer

    def plan_result(self) -> dict:
        plan = super().plan_result()
        [review] = self.events("zaofu.refactor.review.ready")
        plan.update(
            review_artifact_ref=review.payload["review_artifact_ref"],
            refactor_plan_md="# Refactor plan\nRework the answer implementation within its declared scope.\n",
            plan_intent="bounded fixture refactor", gates=[self.command], risk_register=[], backlog_candidates=[],
        )
        return plan
