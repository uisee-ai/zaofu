"""The human decision register must not broaden the controlled approval action."""
import hashlib
import json

from zf.runtime.operator_plan_preview import build_plan_preview
from zf.runtime.plan_review_decisions import decision_counts, read_decision_review
from tests.plan_human_review_fixture import add_human_review_fixture
from tests.test_operator_plan_preview import _artifacts
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter


def test_register_categories_do_not_treat_future_questions_as_current_blockers():
    items = [
        {"status": "pending_decision", "blocking": False},
        {"status": "pending", "blocking": True},
        {"status": "authorized_with_task_obligations", "blocking": False},
        {"status": "startup_authorized_measurement_pending"},
        {"status": "resolved"}, {"status": "unknown-custom-state"},
    ]
    assert decision_counts(items) == dict(total=6, pending=2, blocking=1, follow_up=2, resolved=1, unknown=1)


def test_bound_register_honors_digest_and_reports_empty_register(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"owner_decision_items": []}))
    port = {"name": "task_map", "ref": "map.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    result = read_decision_review(tmp_path, project_root=tmp_path, artifacts=[port], action_items=[])
    assert result["available"] and result["counts"]["total"] == 0
    path.write_text(json.dumps({"owner_decision_items": [{"decision_id": "forged"}]}))
    result = read_decision_review(tmp_path, project_root=tmp_path, artifacts=[port], action_items=[])
    assert result["error"] and not result["available"] and result["items"] == []


def test_action_questions_are_not_misreported_as_the_full_register(tmp_path):
    questions = [{"decision_id": "Q-APPROVE", "status": "pending"}]
    result = read_decision_review(tmp_path, project_root=tmp_path, artifacts=[], action_items=questions)
    assert not result["available"]
    assert result["items"] == questions
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"owner_decision_items": ["invalid", questions[0]]}))
    port = {"name": "task_map", "ref": "map.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    result = read_decision_review(tmp_path, project_root=tmp_path, artifacts=[port], action_items=questions)
    assert not result["available"] and result["error"]


def test_real_preview_reads_task_map_decisions_without_changing_action_questions(tmp_path):
    state, _, _ = _artifacts(tmp_path)
    events = EventLog(state / "events.jsonl")
    writer = EventWriter(events)
    add_human_review_fixture(state, writer)
    preview = build_plan_preview(state, events.read_all(), plan_id="evt-preview-review", project_root=state.parent)
    assert preview["owner_decision_items"] == []
    assert preview["decision_count"] == 0
    assert preview["decision_review"]["counts"]["pending"] >= 1
    assert preview["decision_review"]["items"][0]["decision_id"] == "Q1"
    section = next(s for s in preview["review_packet"]["sections"] if s["id"] == "decisions")
    assert section["status"] == "ready"
    assert section["counts"] == preview["decision_review"]["counts"]
    assert section["counts"]["follow_up"] == 1
    assert section["counts"]["blocking"] == 0
    assert "确认库存来源" in preview["review_dossier_markdown"]
    assert preview["actions"]["approve"] == "plan-approve"


def test_impact_description_does_not_override_explicit_nonblocking_register(tmp_path):
    from tests.test_plan_review_packet import _packet
    from zf.runtime.plan_review_packet import build_review_packet

    _, kwargs = _packet(tmp_path, ["R1"], ["R1"])
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps({"decisions": [{
        "decision_id": "Q-FUTURE", "status": "pending", "blocking": False,
        "blocking_impact": "Only needed before the later reporting stage",
    }]}))
    kwargs["artifacts"].append({
        "name": "decision_register", "ref": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
    packet = build_review_packet(tmp_path, **kwargs, markdown="# Plan", preview_bundle={})
    section = next(s for s in packet["sections"] if s["id"] == "decisions")
    assert section["status"] == "ready"
    assert section["counts"] == {"pending": 1, "blocking": 0}
