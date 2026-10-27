"""Independent output evaluation freezes inputs before exposing the target."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from zf.core.events import EventLog, EventWriter
from zf.runtime.output_evaluation import EvaluationInput, evaluate_output
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.web.headless_agent import HeadlessTurnResult


class FakeBackend:
    backend_id = "fake-readonly"

    def __init__(self, *, malformed: bool = False, matrix_mode: str = "normal") -> None:
        self.calls: list[dict] = []
        self.malformed = malformed
        self.matrix_mode = matrix_mode

    def available(self) -> bool:
        return True

    def run_turn(self, **kwargs) -> HeadlessTurnResult:
        self.calls.append(kwargs)
        kwargs["on_session_id"]("provider-fresh-1")
        if len(self.calls) == 1:
            criteria = [] if "SOURCE:\n\n" in kwargs["prompt"] else [
                {"criterion_id": "R1", "source_location": "line 1",
                 "expected": "Must preserve permission", "importance": "P1",
                 "verification": "compare output"}]
            reply = ("not-json" if self.malformed else json.dumps({
                "criteria": criteria,
                "read_scope": ["line 1"],
                "unread_scope": [], "rubric": {"PASS": "R1 retained"},
            }))
        elif len(self.calls) == 2:
            reply = json.dumps({"decision_deltas": [], "read_scope": [], "unread_scope": []})
        else:
            row = {"criterion_id": "R1", "result": "PASS", "target_location": "line 1"}
            if len(self.calls) == 3 and self.matrix_mode == "conclusion_first":
                row = {"criterion_id": "R1", "conclusion": "PASS", "target_location": "line 1"}
            elif self.matrix_mode == "missing_always":
                row = {"criterion_id": "R2", "result": "PASS", "target_location": "line 1"}
            elif self.matrix_mode == "fail_matrix":
                row["result"] = "FAIL"
            reply = json.dumps({"verdict": "PASS", "matrix": [row],
                                "findings": [], "coverage": {"read": ["line 1"]},
                                "product_verification": "未执行"})
        return HeadlessTurnResult(ok=True, status="completed", backend=self.backend_id,
                                  thread_id=kwargs["thread_id"], provider_session_id="provider-fresh-1",
                                  reply=reply, messages=[], usage={})


def _run(tmp_path: Path, *, kind: str = "prd_output", source: str = "原始要求：必须保留权限。", malformed: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    backend = FakeBackend(malformed=malformed)
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    item = EvaluationInput(kind=kind, objective="评估输出", source_text=source,
                           target_text="最终产物：必须保留权限。")
    result = evaluate_output(state_dir=tmp_path, writer=writer, item=item,
                             request_id=str(uuid.uuid4()), backend=backend)
    return result, backend, writer


def test_source_inventory_is_frozen_before_target_and_report_is_bound(tmp_path):
    result, backend, writer = _run(tmp_path)
    assert result["verdict"] == "PASS"
    assert len(backend.calls) == 3
    assert "最终产物" not in backend.calls[0]["prompt"]
    assert "最终产物" not in backend.calls[1]["prompt"]
    assert "最终产物" in backend.calls[2]["prompt"]
    assert "FROZEN_SOURCE_INVENTORY" in backend.calls[1]["prompt"]
    assert "FROZEN_DECISION_DELTAS" in backend.calls[2]["prompt"]
    assert all(call["permission_profile"] == "read_only" for call in backend.calls)
    assert backend.calls[0]["provider_session_id"] == ""
    assert backend.calls[1]["provider_session_id"] == "provider-fresh-1"
    assert backend.calls[2]["provider_session_id"] == "provider-fresh-1"
    assert backend.calls[0]["cwd"] == backend.calls[1]["cwd"] == backend.calls[2]["cwd"]
    assert not backend.calls[0]["cwd"].exists()
    events = writer.event_log.read_all()
    assert [row.type for row in events] == [
        "output.evaluation.started", "output.evaluation.inventory.frozen",
        "output.evaluation.decisions.frozen",
        "output.evaluation.completed"]
    report = hydrate_sidecar_ref(tmp_path, {"ref": result["report_ref"],
                                             "sha256": result["report_digest"],
                                             "content_type": "application/json"}).payload
    assert report["inventory_ref"] == result["inventory_ref"]
    assert report["product_verification"] == "未执行"


def test_missing_source_cannot_pass_prd_or_markdown(tmp_path):
    for kind in ("prd_output", "markdown_output"):
        result, _, _ = _run(tmp_path / kind, kind=kind, source="")
        assert result["verdict"] == "INDETERMINATE"


def test_prompt_design_without_case_evidence_cannot_claim_effectiveness(tmp_path):
    result, _, _ = _run(tmp_path, kind="prompt_design")
    assert result["verdict"] == "INDETERMINATE"


def test_malformed_inventory_never_sees_target(tmp_path):
    result, backend, writer = _run(tmp_path, malformed=True)
    assert result["verdict"] == "INDETERMINATE"
    assert len(backend.calls) == 2  # one bounded contract repair, still no target
    assert writer.event_log.read_all()[-1].type == "output.evaluation.failed"


def test_matrix_field_mismatch_gets_one_bounded_repair(tmp_path):
    backend = FakeBackend(matrix_mode="conclusion_first")
    result = evaluate_output(
        state_dir=tmp_path, writer=EventWriter(EventLog(tmp_path / "events.jsonl")),
        item=EvaluationInput(kind="prd_output", objective="评估输出",
                             source_text="必须保留权限", target_text="保留权限"),
        request_id=str(uuid.uuid4()), backend=backend)
    assert result["verdict"] == "PASS"
    assert len(backend.calls) == 4
    assert "target-repair" in result["stage_traces"]


def test_matrix_must_cover_each_frozen_source_criterion(tmp_path):
    backend = FakeBackend(matrix_mode="missing_always")
    result = evaluate_output(
        state_dir=tmp_path, writer=EventWriter(EventLog(tmp_path / "events.jsonl")),
        item=EvaluationInput(kind="prd_output", objective="评估输出",
                             source_text="必须保留权限", target_text="保留权限"),
        request_id=str(uuid.uuid4()), backend=backend)
    assert result["verdict"] == "INDETERMINATE"
    assert len(backend.calls) == 4
    assert "report_ref" not in result


def test_matrix_covers_new_requirements_identified_from_owner_chat():
    from zf.runtime.output_evaluation import _valid_matrix

    inventory = {"criteria": [{"criterion_id": "R1"}]}
    decisions = {"decision_deltas": [{"criterion_id": "R2", "source": "owner-message"}]}
    report = {"matrix": [{"criterion_id": "R1", "result": "PASS", "target_location": "section 1"}]}
    assert not _valid_matrix(report, inventory, decisions)
    report["matrix"].append({"criterion_id": "R2", "result": "PASS", "target_location": "section 2"})
    assert _valid_matrix(report, inventory, decisions)


def test_owner_added_requirement_cannot_be_dropped_from_final_evaluation(tmp_path):
    from dataclasses import replace

    class AdditionalRequirementBackend(FakeBackend):
        def run_turn(self, **kwargs):
            turn = super().run_turn(**kwargs)
            if len(self.calls) == 2:
                return replace(turn, reply=json.dumps({"decision_deltas": [{"criterion_id": "R2",
                    "source": "owner-message", "expected": "support undo"}], "read_scope": [], "unread_scope": []}))
            return turn

    backend = AdditionalRequirementBackend()
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    item = EvaluationInput(kind="prd_output", objective="评估输出", source_text="必须保留权限",
                           decision_text="Owner 另外要求支持撤销", target_text="保留权限，但没有撤销",
                           context={"channel_id": "ch", "thread_id": "main"})
    assert item.manifest()["method_version"].endswith(".v3")
    result = evaluate_output(state_dir=tmp_path, writer=writer, item=item,
                             request_id=str(uuid.uuid4()), backend=backend)
    assert result["verdict"] == "INDETERMINATE"
    assert "report_ref" not in result
    assert "没有撤销" not in backend.calls[0]["prompt"]
    assert "没有撤销" not in backend.calls[1]["prompt"]


def test_channel_decision_without_requirement_identity_does_not_reach_target(tmp_path):
    from dataclasses import replace

    class MissingDecisionIdentity(FakeBackend):
        def run_turn(self, **kwargs):
            turn = super().run_turn(**kwargs)
            if len(self.calls) in {2, 3}:
                return replace(turn, reply=json.dumps({"decision_deltas": [{"expected": "support undo"}],
                    "read_scope": [], "unread_scope": []}))
            return turn

    backend = MissingDecisionIdentity()
    item = EvaluationInput(kind="prd_output", objective="评估输出", source_text="必须保留权限",
        decision_text="另外要求支持撤销", target_text="SECRET-TARGET", context={"channel_id": "ch"})
    result = evaluate_output(state_dir=tmp_path, writer=EventWriter(EventLog(tmp_path / "events.jsonl")),
                             item=item, request_id=str(uuid.uuid4()), backend=backend)
    assert result["verdict"] == "INDETERMINATE"
    assert all("SECRET-TARGET" not in call["prompt"] for call in backend.calls)


def test_report_cannot_pass_with_failing_matrix_row(tmp_path):
    backend = FakeBackend(matrix_mode="fail_matrix")
    result = evaluate_output(
        state_dir=tmp_path, writer=EventWriter(EventLog(tmp_path / "events.jsonl")),
        item=EvaluationInput(kind="prd_output", objective="评估输出",
                             source_text="必须保留权限", target_text="保留权限"),
        request_id=str(uuid.uuid4()), backend=backend)
    assert result["verdict"] == "FAIL"
