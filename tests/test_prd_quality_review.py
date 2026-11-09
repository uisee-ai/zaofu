"""Frozen PRD quality criteria supplement, rather than replace, source coverage."""
import json
import uuid
from dataclasses import replace

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.output_evaluation import EvaluationInput, evaluate_output
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from tests.test_output_evaluation import FakeBackend


def test_source_coverage_alone_cannot_pass_channel_prd_quality(tmp_path):
    backend = FakeBackend(omit_quality=True)
    result = evaluate_output(
        state_dir=tmp_path, writer=EventWriter(EventLog(tmp_path / "events.jsonl")),
        item=EvaluationInput(kind="prd_output", objective="完整 PRD 质量评估",
                             source_text="保留权限", target_text="保留权限",
                             context={"channel_id": "ch-quality"}),
        request_id=str(uuid.uuid4()), backend=backend,
    )
    assert result["verdict"] == "INDETERMINATE"
    assert "report_ref" not in result
    assert len(backend.calls) == 4


def test_prd_companion_survives_human_readable_export():
    from zf.runtime.channel_prd_document import render_prd_document

    companion = {
        "source_baseline": [{"version": "v1.3", "status": "unverified", "ref": "source:old"}],
        "acceptance_data_plan": [{"acceptance_ids": ["AC-1"], "dataset": "pending owner approval"}],
        "handoff": [{"artifact": "task_map.json", "status": "planned", "producer": "Planner"}],
    }
    markdown = render_prd_document({"prd_markdown": "# Product", "implementation_companion": companion},
                                   channel_id="ch", thread_id="main", source_refs=[])
    assert "v1.3" in markdown
    assert "unverified" in markdown
    assert "pending owner approval" in markdown
    assert "task_map.json" in markdown


@pytest.mark.parametrize("quality_result", ["PASS", "FAIL", "INDETERMINATE"])
def test_quality_method_is_frozen_and_separate_from_source_and_product(tmp_path, quality_result):
    class ReviewedBackend(FakeBackend):
        def run_turn(self, **kwargs):
            turn = super().run_turn(**kwargs)
            if len(self.calls) >= 3:
                report = json.loads(turn.reply)
                next(row for row in report["matrix"] if row["criterion_id"] == "PRD-Q1")["result"] = quality_result
                return replace(turn, reply=json.dumps(report))
            return turn

    backend = ReviewedBackend()
    request_id = str(uuid.uuid4())
    item = EvaluationInput(kind="prd_output", objective="质量评估", source_text="原始要求：权限隔离",
                           target_text="TARGET-UNIQUE-TEXT", context={"channel_id": "ch"})
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    result = evaluate_output(state_dir=tmp_path, writer=writer, item=item, request_id=request_id, backend=backend)

    def read(name):
        return hydrate_sidecar_ref(tmp_path, {"ref": result[f"{name}_ref"],
                                             "sha256": result[f"{name}_digest"],
                                             "content_type": "application/json"}).payload

    manifest = read("manifest")
    report = read("report")
    bundle = read("input")
    inventory = read("inventory")
    assert inventory["criteria"][0]["criterion_id"] == "R1"
    assert len(inventory["criteria"]) == 1
    assert bundle["manifest"] == manifest
    assert report["review_method_digest"] == manifest["review_method_digest"]
    assert {row["criterion_id"] for row in report["quality_checklist"]} == {
        row["criterion_id"] for row in manifest["review_method"]["criteria"]}
    assert report["document_quality"] == quality_result
    assert report["source_fidelity"] == "PASS"
    assert report["verdict"] == quality_result
    assert report["product_verification"] == "未执行"
    assert report["planning_readiness"]["verdict"] == "ready"
    assert "TARGET-UNIQUE-TEXT" not in backend.calls[0]["prompt"]
    assert "TARGET-UNIQUE-TEXT" not in backend.calls[1]["prompt"]
    assert all(manifest["review_method_digest"] in call["prompt"] for call in backend.calls)
    assert writer.event_log.read_all()[-1].type == "output.evaluation.completed"


def test_author_and_consensus_prompts_carry_quality_method_without_bound_skills():
    from zf.runtime.channel_reply_prompt import channel_reply_response_contract

    synthesis = channel_reply_response_contract({}, {}, {"refs": {"synthesis_request_id": "r1"}})
    for field in ("source_baseline", "review_coverage", "acceptance_data_plan", "prd_quality_checklist"):
        assert field in synthesis
    assert "state the architecture boundary explicitly: separate control plane" not in synthesis
    review = channel_reply_response_contract({}, {}, {"refs": {"consensus_review_id": "c1"}})
    assert "actual role" in review and "Senior development" in review


@pytest.mark.parametrize("stage", ["source", "decisions"])
def test_product_inventory_cannot_replace_a_method_criterion(tmp_path, stage):
    class CollisionBackend(FakeBackend):
        def run_turn(self, **kwargs):
            turn = super().run_turn(**kwargs)
            body = json.loads(turn.reply)
            if stage == "source" and len(self.calls) == 1:
                body["criteria"][0]["criterion_id"] = "PRD-Q1"
            if stage == "decisions" and len(self.calls) == 2:
                body["decision_deltas"] = [{"criterion_id": ["PRD-Q1"], "validity": "authorized"}]
            return replace(turn, reply=json.dumps(body))

    backend = CollisionBackend()
    result = evaluate_output(
        state_dir=tmp_path, writer=EventWriter(EventLog(tmp_path / "events.jsonl")),
        item=EvaluationInput(kind="prd_output", objective="质量评估", source_text="保留权限",
                             target_text="TARGET-MUST-NOT-BE-REVEALED", context={"channel_id": "ch"}),
        request_id=str(uuid.uuid4()), backend=backend,
    )
    assert result["verdict"] == "INDETERMINATE"
    assert "reserved PRD quality criterion" in result["reason"]
    assert all("TARGET-MUST-NOT-BE-REVEALED" not in row["prompt"] for row in backend.calls)


def test_unread_owner_evidence_cannot_receive_source_fidelity_pass(tmp_path):
    class UnreadDecisionBackend(FakeBackend):
        def run_turn(self, **kwargs):
            turn = super().run_turn(**kwargs)
            if len(self.calls) == 2:
                body = json.loads(turn.reply)
                body["unread_scope"] = ["owner-message-2"]
                return replace(turn, reply=json.dumps(body))
            return turn

    result = evaluate_output(
        state_dir=tmp_path, writer=EventWriter(EventLog(tmp_path / "events.jsonl")),
        item=EvaluationInput(kind="prd_output", objective="质量评估", source_text="保留权限",
                             target_text="保留权限", context={"channel_id": "ch"}),
        request_id=str(uuid.uuid4()), backend=UnreadDecisionBackend(),
    )
    report = hydrate_sidecar_ref(tmp_path, {"ref": result["report_ref"], "sha256": result["report_digest"],
                                          "content_type": "application/json"}).payload
    assert result["verdict"] == "INDETERMINATE"
    assert report["source_fidelity"] == "INDETERMINATE"


@pytest.mark.parametrize("mode", ["conversation", "multi_lens"])
@pytest.mark.parametrize("disable_reviewers", [False, True])
def test_prd_template_materializes_independent_reviewers_in_both_modes(tmp_path, mode, disable_reviewers):
    from tests.test_channel_templates import _execute, _runtime
    from zf.runtime.channel_projection import project_channel

    state, _, writer, service = _runtime(tmp_path)
    overrides = {"backend": "fake", "discussion_mode": mode}
    if disable_reviewers:
        overrides["role_overrides"] = {role: {"enabled": False}
                                        for role in ("dev_reviewer", "security_reviewer")}
    result = _execute(service, writer, "channel-create-from-template", {
        "channel_id": "ch-quality", "template_id": "prd-clarification", "overrides": overrides})
    assert result["ok"], result
    channel = project_channel(state, "ch-quality")
    assert channel["discussion"]["mode"] == mode
    members = {row["member_id"]: row for row in channel["members"]}
    for role in ("dev_reviewer", "security_reviewer"):
        if disable_reviewers:
            assert role not in members
        else:
            assert members[role]["channel_role"] == role
            assert members[role]["permission_profile"] == "read_only"
            assert members[role]["member_id"] != channel["discussion"]["synthesizer"]
