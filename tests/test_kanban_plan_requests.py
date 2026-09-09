from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.runtime.kanban_plan_requests import (
    PLAN_ANSWERED_EVENT,
    PLAN_REQUESTED_EVENT,
    normalize_plan_request_revision,
    pending_kanban_plan_requests,
    plan_requirement_digest,
    plan_request_digest,
    plan_request_id,
    plan_request_gate,
    plan_response_gate,
)
from zf.web.plan_extraction import extract_plan_request
from zf.web.plan_runtime import prepare_headless_plan_draft


ROOT = Path(__file__).resolve().parents[1]


def _request_answer() -> str:
    return """
```json
{
  "plan_request": {
    "revision": 2,
    "header": "Route",
    "question": "How should this requirement proceed?",
    "id": "route",
    "options": [
      {
        "id": "research",
        "label": "Research (Recommended)",
        "description": "Collect external evidence first."
      },
      {
        "id": "channel",
        "label": "Channel",
        "description": "Resolve a product decision with roles."
      }
    ],
    "allow_other": true,
    "reason": "The route changes cost and participants."
  }
}
```
"""


def _channel_setup_answer() -> str:
    return """
```json
{
  "plan_request": {
    "header": "Channel setup",
    "id": "channel-setup",
    "question": "Which collaboration setup should run?",
    "discussion_seed": "Preserve all Task contract refs during migration.",
    "submit_action": "channel-create-and-start",
    "submit_label": "Create & start",
    "options": [
      {
        "id": "quick",
        "label": "Quick change (Recommended)",
        "description": "Focused implementation review.",
        "recommended": true,
        "submit_payload": {
          "template_id": "quick-change",
          "mode": "multi_lens",
          "overrides": {
            "backend": "fake",
            "budget": {"max_rounds": 4}
          }
        }
      },
      {
        "id": "architecture",
        "label": "Architecture review",
        "description": "Broader architecture and security review.",
        "submit_payload": {
          "template_id": "architecture-review",
          "mode": "multi_lens",
          "overrides": {
            "backend": "fake",
            "budget": {"max_rounds": 6}
          }
        }
      }
    ],
    "allow_other": false,
    "reason": "Roles and turn budget change the collaboration cost."
  }
}
```
    """


def _task_workflow_answer() -> str:
    return json.dumps({
        "plan_request": {
            "subject_type": "task_workflow",
            "header": "Workflow route",
            "id": "workflow-route",
            "question": "How should TASK-PLAN run?",
            "options": [
                {
                    "id": "delivery",
                    "label": "PRD delivery (Recommended)",
                    "description": "Use writer and verify lanes.",
                    "recommended": True,
                    "effect": {
                        "mode": "propose",
                        "action": "task-workflow-start",
                        "payload": {
                            "task_id": "TASK-PLAN",
                            "route_id": "delivery:prd:standard",
                            "objective": "Implement the approved Task.",
                            "parameters": {"target_root": "/workspace/project"},
                        },
                    },
                },
                {
                    "id": "research",
                    "label": "Research first",
                    "description": "Collect evidence before delivery.",
                    "effect": {
                        "mode": "propose",
                        "action": "task-workflow-start",
                        "payload": {
                            "task_id": "TASK-PLAN",
                            "route_id": "research:fixed",
                            "objective": "Research the approved Task.",
                            "parameters": {},
                        },
                    },
                },
                {
                    "id": "defer",
                    "label": "No workflow yet",
                    "description": "Keep the Task tracked.",
                    "effect": {"mode": "continue"},
                },
            ],
            "allow_other": True,
        },
    })


def test_dedicated_final_bare_plan_is_normalized() -> None:
    answer = """The owner needs to answer these questions.
```json
{
  "subject_type": "clarification",
  "questions": [
    {
      "id": "coordinate-contract",
      "question": "Which coordinate contract is expected?",
      "options": [
        {"label": "row then column (Recommended)"},
        {"label": "column then row"}
      ]
    }
  ]
}
```"""

    request = extract_plan_request(answer)

    assert request is not None
    assert request["valid"] is True, request["validation_error"]
    assert request["subject_type"] == "clarification"
    assert request["question_id"] == "coordinate-contract"


def test_bare_plan_example_inside_prose_is_not_extracted() -> None:
    answer = """Example only:
```json
{
  "subject_type": "clarification",
  "question": "Which route?",
  "options": ["Direct", "Research"]
}
```
Do not present this example as the current Plan."""

    assert extract_plan_request(answer) is None


def test_channel_task_create_plan_accepts_provider_option_aliases() -> None:
    request = extract_plan_request(
        json.dumps({
            "plan_request": {
                "subject_type": "task_create",
                "header": "Create Task",
                "id": "create-task",
                "question": "Create the exact PRD-bound Task?",
                "options": [
                    {
                        "id": "create",
                        "label": "Create Task (Recommended)",
                        "description": "Prepare the proposal.",
                        "recommended": True,
                        "mode": "propose",
                        "action": "create-task",
                        "payload": {
                            "title": "Deliver the Channel PRD",
                            "objective": "Implement the confirmed requirement.",
                            "acceptance": "Acceptance checks pass.",
                            "acceptance_criteria": ["Run focused tests."],
                            "priority": 3,
                        },
                    },
                    {
                        "id": "continue",
                        "label": "Continue discussion",
                        "description": "Do not create work yet.",
                        "mode": "continue",
                    },
                ],
                "allow_other": False,
            },
        }),
        plan_context={
            "workflow_parameters": {
                "channel_id": "ch-product",
                "thread_id": "main",
                "channel_member_id": "product-pm",
                "leader_revision": 1,
                "prd_revision": 2,
                "source_ref": "channels/ch-product/prd/r2.json",
                "source_digest": "a" * 64,
            },
        },
    )

    assert request is not None
    assert request["valid"] is True, request["validation_error"]
    create, keep_discussing = request["options"]
    assert create["submit_mode"] == "propose"
    assert create["submit_action"] == "create-task"
    assert create["submit_payload"]["title"] == "Deliver the Channel PRD"
    assert create["submit_payload"]["channel_authority"]["prd_revision"] == 2
    assert keep_discussing["submit_mode"] == "continue"
    assert keep_discussing.get("submit_action", "") == ""


def test_channel_setup_continue_effect_does_not_inherit_top_level_action() -> None:
    request = extract_plan_request(
        json.dumps({
            "plan_request": {
                "subject_type": "channel_setup",
                "header": "Channel Setup",
                "id": "channel-setup",
                "question": "Create and start a Channel?",
                "discussion_seed": (
                    "Add an integer add function with pytest coverage."
                ),
                "submit_action": "channel-create-and-start",
                "submit_label": "Create and start",
                "allow_other": False,
                "options": [
                    {
                        "id": "create",
                        "label": "Create and start (Recommended)",
                        "description": "Start one focused conversation.",
                        "recommended": True,
                        "submit_payload": {
                            "template_id": "prd-clarification",
                            "name": "feishu-add-function-e2e",
                            "mode": "conversation",
                            "overrides": {
                                "budget": {"max_rounds": 1},
                            },
                        },
                    },
                    {
                        "id": "cancel",
                        "label": "Do not create a Channel",
                        "description": "Keep the current conversation.",
                        "effect": {"mode": "continue"},
                    },
                ],
            },
        }),
    )

    assert request is not None
    assert request["valid"] is True, request["validation_error"]
    create, cancel = request["options"]
    assert create["submit_payload"]["mode"] == "conversation"
    assert create["submit_details"]["product_mode"] == "conversation"
    assert cancel["submit_mode"] == "continue"
    assert "submit_action" not in cancel


def test_channel_task_create_plan_binds_selected_canonical_prd() -> None:
    artifact_ref = "channels/ch-product/prd/r2.json"
    artifact_digest = "a" * 64
    request = extract_plan_request(
        json.dumps({
            "plan_request": {
                "subject_type": "task_create",
                "channel_prd_ref": artifact_ref,
                "channel_prd_digest": artifact_digest,
                "channel_prd_intent": {
                    "decision": "bind_channel_prd",
                    "source_quote": "Create the Task from the confirmed PRD.",
                },
                "header": "Create Task",
                "id": "create-task",
                "question": "Create the exact PRD-bound Task?",
                "options": [
                    {
                        "id": "create",
                        "label": "Create Task (Recommended)",
                        "description": "Prepare the exact proposal.",
                        "recommended": True,
                        "effect": {
                            "mode": "propose",
                            "action": "create-task",
                            "payload": {
                                "title": "Deliver the Channel PRD",
                                "objective": "Implement the confirmed requirement.",
                            },
                        },
                    },
                    {
                        "id": "continue",
                        "label": "Continue discussion",
                        "description": "Do not create work yet.",
                        "effect": {"mode": "continue"},
                    },
                ],
                "allow_other": False,
            },
        }),
        plan_context={
            "user_semantic_context": (
                "Create the Task from the confirmed PRD."
            ),
            "canonical_channel_prds": {
                "items": [{
                    "channel_id": "ch-product",
                    "thread_id": "main",
                    "channel_member_id": "product-pm",
                    "leader_revision": 1,
                    "prd_revision": 2,
                    "artifact_ref": artifact_ref,
                    "artifact_digest": artifact_digest,
                }],
            },
        },
    )

    assert request is not None
    assert request["valid"] is True, request["validation_error"]
    assert request["channel_prd_intent"] == {
        "decision": "bind_channel_prd",
        "source_quote": "Create the Task from the confirmed PRD.",
    }
    create = request["options"][0]
    assert create["submit_payload"]["channel_authority"] == {
        "channel_id": "ch-product",
        "thread_id": "main",
        "channel_member_id": "product-pm",
        "leader_revision": 1,
        "prd_revision": 2,
        "source_ref": artifact_ref,
        "source_digest": artifact_digest,
    }


@pytest.mark.parametrize(
    ("intent", "semantic_context", "expected_error"),
    [
        (
            None,
            "Fix the unrelated row and column parser issue.",
            "Channel PRD selection requires channel_prd_intent",
        ),
        (
            {
                "decision": "bind_channel_prd",
                "source_quote": "Create from the confirmed PRD.",
            },
            "Fix the unrelated row and column parser issue.",
            "must occur verbatim in the user semantic context",
        ),
    ],
)
def test_channel_task_create_plan_requires_explicit_prd_handoff_intent(
    intent: dict[str, str] | None,
    semantic_context: str,
    expected_error: str,
) -> None:
    artifact_ref = "channels/ch-product/prd/r2.json"
    artifact_digest = "a" * 64
    raw_plan: dict[str, object] = {
        "subject_type": "task_create",
        "channel_prd_ref": artifact_ref,
        "channel_prd_digest": artifact_digest,
        "question": "Create this Task?",
        "options": [
            {
                "label": "Create Task (Recommended)",
                "description": "Prepare the proposal.",
                "effect": {
                    "mode": "propose",
                    "action": "create-task",
                    "payload": {"title": "Fix the parser"},
                },
            },
            {
                "label": "Continue discussion",
                "description": "Do not create work yet.",
                "effect": {"mode": "continue"},
            },
        ],
        "allow_other": False,
    }
    if intent is not None:
        raw_plan["channel_prd_intent"] = intent

    request = extract_plan_request(
        json.dumps({"plan_request": raw_plan}),
        plan_context={
            "user_semantic_context": semantic_context,
            "canonical_channel_prds": {
                "items": [{
                    "channel_id": "ch-product",
                    "thread_id": "main",
                    "channel_member_id": "product-pm",
                    "leader_revision": 1,
                    "prd_revision": 2,
                    "artifact_ref": artifact_ref,
                    "artifact_digest": artifact_digest,
                }],
            },
        },
    )

    assert request is not None
    assert request["valid"] is False
    assert expected_error in request["validation_error"]


def test_channel_task_create_plan_rejects_stale_canonical_prd_selector() -> None:
    answer = json.dumps({
        "plan_request": {
            "subject_type": "task_create",
            "channel_prd_ref": "channels/ch-product/prd/r1.json",
            "channel_prd_digest": "stale",
            "header": "Create Task",
            "question": "Create the exact PRD-bound Task?",
            "options": [
                {
                    "label": "Create Task (Recommended)",
                    "description": "Prepare the proposal.",
                    "effect": {
                        "mode": "propose",
                        "action": "create-task",
                        "payload": {"title": "Deliver the PRD"},
                    },
                },
                {
                    "label": "Continue discussion",
                    "description": "Keep discussing.",
                    "effect": {"mode": "continue"},
                },
            ],
            "allow_other": False,
        },
    })

    request = extract_plan_request(
        answer,
        plan_context={"canonical_channel_prds": {"items": []}},
    )

    assert request is not None
    assert request["valid"] is False
    assert "does not match exactly one current canonical artifact" in (
        request["validation_error"]
    )


def _requested(event_id: str = "evt-plan") -> ZfEvent:
    request = extract_plan_request(
        _request_answer(),
        plan_context={
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "turn_id": "turn-1",
        },
    )
    assert request is not None
    return ZfEvent(
        id=event_id,
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        payload={"request": request},
    )


def test_plan_request_normalizes_stable_identity_and_options() -> None:
    first = extract_plan_request(
        _request_answer(),
        plan_context={
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "turn_id": "turn-1",
        },
    )
    second = extract_plan_request(
        _request_answer(),
        plan_context={
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "turn_id": "turn-2",
        },
    )

    assert first is not None and second is not None
    assert first["request_id"] == second["request_id"]
    assert first["request_digest"] == second["request_digest"]
    assert first["revision"] == 2
    assert first["valid"] is True
    assert [item["id"] for item in first["options"]] == [
        "research",
        "channel",
    ]
    assert first["options"][0]["recommended"] is True


def test_single_question_v3_aliases_preserve_v2_identity_and_digest() -> None:
    request = extract_plan_request(
        _request_answer(),
        plan_context={
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "originating_message_event_id": "evt-origin",
        },
    )

    assert request is not None
    legacy = {**request}
    legacy.pop("questions")
    assert plan_request_id(request) == plan_request_id(legacy)
    assert plan_request_digest(request) == plan_request_digest(legacy)


def test_plan_request_identity_tracks_origin_while_digest_tracks_semantics() -> None:
    base_context = {
        "project_id": "project-a",
        "conversation_id": "kanban:project-a",
        "thread_key": "main",
        "originating_message_event_id": "evt-origin-a",
    }
    first = extract_plan_request(_request_answer(), plan_context=base_context)
    revised = extract_plan_request(
        _request_answer().replace(
            "Collect external evidence first.",
            "Collect repository and external evidence first.",
        ),
        plan_context=base_context,
    )
    separate = extract_plan_request(
        _request_answer(),
        plan_context={
            **base_context,
            "originating_message_event_id": "evt-origin-b",
        },
    )

    assert first is not None and revised is not None and separate is not None
    assert revised["request_id"] == first["request_id"]
    assert revised["request_digest"] != first["request_digest"]
    assert separate["request_id"] != first["request_id"]


def test_headless_plan_binds_the_contiguous_requirement_event_set() -> None:
    old = ZfEvent(
        id="evt-old",
        type="user.message",
        actor="web",
        payload={
            "message": "An unrelated completed request.",
            "project_id": "zaofu",
            "conversation_id": "kanban:zaofu",
            "thread_key": "main",
        },
    )
    boundary = ZfEvent(
        id="evt-task",
        type="task.created",
        actor="web",
        task_id="TASK-OLD",
    )
    requirement = ZfEvent(
        id="evt-requirement",
        type="user.message",
        actor="web",
        payload={
            "message": "Preserve all Task contract refs during migration.",
            "project_id": "zaofu",
            "conversation_id": "kanban:zaofu",
            "thread_key": "main",
        },
    )
    continuation = ZfEvent(
        id="evt-continuation",
        type="user.message",
        actor="web",
        payload={
            "message": "基于上面的需求创建 Channel。",
            "project_id": "zaofu",
            "conversation_id": "kanban:zaofu",
            "thread_key": "main",
        },
    )

    draft, proposal = prepare_headless_plan_draft(
        [old, boundary, requirement, continuation],
        answer=_channel_setup_answer(),
        action_proposal=None,
        project_id="zaofu",
        conversation_id="kanban:zaofu",
        thread_key="main",
        fallback_thread_id="main",
        turn_id="turn-channel",
        backend="codex-headless",
        provider_session_id="session-1",
        originating_message_event_id=continuation.id,
        task_id=None,
        correlation_id="trace-channel",
    )

    assert proposal is None
    assert draft is not None
    assert draft.request["originating_message_event_ids"] == [
        requirement.id,
        continuation.id,
    ]
    assert draft.request["requirement_digest"] == plan_requirement_digest([
        (requirement.id, requirement.payload["message"]),
        (continuation.id, continuation.payload["message"]),
    ])


def test_action_bound_channel_plan_materializes_exact_member_and_round_summary() -> None:
    request = extract_plan_request(
        _channel_setup_answer(),
        plan_context={
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "originating_message_event_id": "evt-origin",
        },
    )

    assert request is not None
    assert request["valid"] is True
    assert request["submit_action"] == "channel-create-and-start"
    assert request["discussion_seed"] == (
        "Preserve all Task contract refs during migration."
    )
    assert request["submit_label"] == "Create & start"
    assert request["allow_other"] is False
    quick = request["options"][0]
    submit_payload = quick["submit_payload"]
    assert {
        key: value
        for key, value in submit_payload.items()
        if key != "expected_profile_selection_digest"
    } == {
        "template_id": "quick-change",
        "mode": "multi_lens",
        "overrides": {
            "backend": "fake",
            "budget": {"max_rounds": 4},
        },
    }
    assert len(submit_payload["expected_profile_selection_digest"]) == 64
    assert quick["submit_details"]["member_count"] == 3
    assert quick["submit_details"]["mode"] == "multi_lens"
    assert quick["submit_details"]["engine_mode"] == (
        "fanout_then_synthesis"
    )
    assert quick["submit_details"]["routing_strategy"] == (
        "blind_fanout_then_synthesis"
    )
    assert quick["submit_details"]["first_pass_reply_count"] == 3
    assert [
        member["role"] for member in quick["submit_details"]["members"]
    ] == ["tech_leader", "dev_reviewer", "qa_analyst"]
    assert quick["submit_details"]["max_rounds"] == 4
    assert quick["submit_details"]["round_policy"] == "explicit_cap"
    assert len(quick["submit_details"]["profile_selection_digest"]) == 64
    assert len(quick["submit_details"]["materialization_digest"]) == 64

    source = ZfEvent(
        id="evt-channel-plan",
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        payload={"request": request},
    )
    gate = plan_response_gate(
        [source],
        request_event_id=source.id,
        request_id=request["request_id"],
        revision=request["revision"],
        question_id="",
        option_id="",
        answer="",
        answers=[{
            "question_id": request["question_id"],
            "option_id": "quick",
            "answer": "forged",
        }],
    )
    assert gate["ok"] is True
    assert gate["question_id"] == request["question_id"]
    assert gate["submit_action"] == "channel-create-and-start"
    assert gate["submit_payload"] == quick["submit_payload"]


def test_channel_setup_plan_requires_explicit_mode_at_extract_and_apply() -> None:
    missing_mode = extract_plan_request(
        _channel_setup_answer().replace(
            '          "mode": "multi_lens",\n',
            "",
            1,
        )
    )
    assert missing_mode is not None
    assert missing_mode["valid"] is False
    assert "submit_payload.mode is required" in missing_mode["validation_error"]

    request = extract_plan_request(_channel_setup_answer())
    assert request is not None and request["valid"] is True
    stale_request = deepcopy(request)
    stale_request["questions"][0]["options"][0]["submit_payload"].pop(
        "mode"
    )
    source = ZfEvent(
        id="evt-stale-channel-plan",
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        payload={"request": stale_request},
    )

    gate = plan_response_gate(
        [source],
        request_event_id=source.id,
        request_id=stale_request["request_id"],
        revision=stale_request["revision"],
        question_id=stale_request["question_id"],
        option_id="quick",
        answer="Quick change (Recommended)",
    )

    assert gate == {
        "ok": False,
        "status": "plan_channel_mode_required",
    }

    answered = ZfEvent(
        id="evt-stale-channel-plan-answer",
        type=PLAN_ANSWERED_EVENT,
        actor="web",
        payload={
            "request_event_id": source.id,
            "request_id": stale_request["request_id"],
            "revision": stale_request["revision"],
            "question_id": stale_request["question_id"],
            "option_id": "quick",
            "answer": "Quick change (Recommended)",
            "answers": [{
                "question_id": stale_request["question_id"],
                "option_id": "quick",
                "answer": "Quick change (Recommended)",
            }],
        },
    )
    duplicate = plan_response_gate(
        [source, answered],
        request_event_id=source.id,
        request_id=stale_request["request_id"],
        revision=stale_request["revision"],
        question_id=stale_request["question_id"],
        option_id="quick",
        answer="Quick change (Recommended)",
    )
    assert duplicate["ok"] is True
    assert duplicate["status"] == "already_answered"


def test_channel_plan_defaults_missing_presentation_header() -> None:
    request = extract_plan_request(
        _channel_setup_answer().replace(
            '    "header": "Channel setup",\n',
            "",
        ),
        plan_context={
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
        },
    )

    assert request is not None
    assert request["valid"] is True
    assert request["validation_error"] == ""
    assert request["header"] == "Channel setup"
    assert request["questions"][0]["header"] == "Channel setup"


def test_action_bound_plan_rejects_other_and_hidden_payload_fields() -> None:
    other = extract_plan_request(
        _channel_setup_answer().replace(
            '"allow_other": false',
            '"allow_other": true',
        )
    )
    hidden = extract_plan_request(
        _channel_setup_answer().replace(
            '"template_id": "quick-change",',
            '"template_id": "quick-change", "message": "hidden",',
            1,
        )
    )

    assert other is not None and hidden is not None
    assert other["valid"] is False
    assert "allow_other" in other["validation_error"]
    assert hidden["valid"] is False
    assert "unsupported submit_payload field" in hidden["validation_error"]


def test_task_workflow_plan_normalizes_option_effects_and_route_details() -> None:
    config = load_config(ROOT / "zf.yaml")
    request = extract_plan_request(
        _task_workflow_answer(),
        plan_context={
            "project_id": "zaofu",
            "task_id": "TASK-PLAN",
            "task_contract_digest": "sha256:task-binding",
            "conversation_id": "kanban:zaofu",
            "thread_key": "main",
            "workflow_parameters": {
                "channel_id": "ch-prd",
                "thread_id": "main",
                "source_ref": "channel-artifacts/ch-prd/prd.md",
                "source_refs": {
                    "channel_id": "ch-prd",
                    "channel_prd_digest": "sha256:canonical",
                },
                "artifact_refs": [{
                    "kind": "channel_prd",
                    "ref": "channel-artifacts/ch-prd/prd.md",
                    "digest": "sha256:canonical",
                }],
            },
        },
        config=config,
    )

    assert request is not None
    assert request["valid"] is True
    assert request["subject_type"] == "task_workflow"
    assert request["task_id"] == "TASK-PLAN"
    assert request["task_contract_digest"] == "sha256:task-binding"
    delivery = request["options"][0]
    assert delivery["submit_mode"] == "propose"
    assert delivery["submit_action"] == "workflow-start"
    assert delivery["submit_payload"]["task_contract_digest"] == (
        "sha256:task-binding"
    )
    assert delivery["submit_payload"]["config_digest"] == (
        request["config_digest"]
    )
    assert delivery["submit_payload"]["parameters"]["channel_id"] == "ch-prd"
    assert delivery["submit_payload"]["parameters"]["target_root"] == (
        "/workspace/project"
    )
    assert delivery["submit_payload"]["parameters"]["source_refs"][
        "channel_prd_digest"
    ] == "sha256:canonical"
    assert delivery["submit_payload"]["parameters"]["artifact_refs"] == [{
        "kind": "channel_prd",
        "ref": "channel-artifacts/ch-prd/prd.md",
        "digest": "sha256:canonical",
    }]
    assert delivery["submit_details"]["lane_count"] == 2

    source = ZfEvent(
        id="evt-workflow-plan",
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        task_id="TASK-PLAN",
        payload={"request": request},
    )
    gate = plan_response_gate(
        [source],
        request_event_id=source.id,
        request_id=request["request_id"],
        revision=request["revision"],
        question_id=request["question_id"],
        option_id="research",
        answer="forged",
    )
    assert gate["ok"] is True
    assert gate["submit_mode"] == "propose"
    assert gate["submit_action"] == "workflow-start"
    assert gate["submit_payload"]["route_id"] == "research:fixed"


def test_headless_plan_can_bind_an_existing_task_outside_task_panel() -> None:
    config = load_config(ROOT / "zf.yaml")

    draft, proposal = prepare_headless_plan_draft(
        [],
        answer=_task_workflow_answer(),
        action_proposal=None,
        project_id="zaofu",
        conversation_id="kanban:zaofu",
        thread_key="kanban:zaofu",
        fallback_thread_id="kanban:zaofu",
        turn_id="turn-workflow-existing-task",
        backend="codex-headless",
        provider_session_id="session-1",
        originating_message_event_id="evt-workflow-request",
        task_id=None,
        task_binding_digests={
            "TASK-PLAN": "sha256:canonical-task-binding",
        },
        correlation_id="trace-workflow-existing-task",
        config=config,
    )

    assert proposal is None
    assert draft is not None
    assert draft.request["valid"] is True, draft.request["validation_error"]
    assert draft.request["task_id"] == "TASK-PLAN"
    assert draft.request["task_contract_digest"] == (
        "sha256:canonical-task-binding"
    )
    assert draft.request["options"][0]["submit_payload"][
        "task_contract_digest"
    ] == "sha256:canonical-task-binding"


def test_task_workflow_plan_rejects_incomplete_canonical_task_contract() -> None:
    config = load_config(ROOT / "zf.yaml")

    draft, proposal = prepare_headless_plan_draft(
        [],
        answer=_task_workflow_answer(),
        action_proposal=None,
        project_id="zaofu",
        conversation_id="kanban:zaofu",
        thread_key="kanban:zaofu",
        fallback_thread_id="kanban:zaofu",
        turn_id="turn-workflow-invalid-task",
        backend="codex-headless",
        provider_session_id="session-1",
        originating_message_event_id="evt-workflow-request",
        task_id=None,
        task_binding_digests={
            "TASK-PLAN": "sha256:canonical-task-binding",
        },
        task_contract_errors={
            "TASK-PLAN": [
                "TASK-PLAN: contract.verification_tiers must not be empty",
                "TASK-PLAN: contract.spec_skip_reason is required",
            ],
        },
        correlation_id="trace-workflow-invalid-task",
        config=config,
    )

    assert proposal is None
    assert draft is not None
    assert draft.request["valid"] is False
    assert "workflow Task contract is incomplete" in (
        draft.request["validation_error"]
    )
    assert "verification_tiers must not be empty" in (
        draft.request["validation_error"]
    )


def test_agent_task_workflow_defer_choice_normalizes_to_continue() -> None:
    config = load_config(ROOT / "zf.yaml")
    answer = json.loads(_task_workflow_answer())
    answer["plan_request"]["options"][2]["effect"]["mode"] = "defer"

    request = extract_plan_request(
        json.dumps(answer),
        plan_context={
            "task_id": "TASK-PLAN",
            "task_contract_digest": "sha256:task-binding",
        },
        config=config,
    )

    assert request is not None
    assert request["valid"] is True, request["validation_error"]
    assert request["options"][2]["submit_mode"] == "continue"
    assert "submit_action" not in request["options"][2]


def test_agent_task_workflow_plan_rejects_missing_route_parameters() -> None:
    config = load_config(ROOT / "zf.yaml")
    answer = json.loads(_task_workflow_answer())
    del answer["plan_request"]["options"][0]["effect"]["payload"][
        "parameters"
    ]["target_root"]

    request = extract_plan_request(
        json.dumps(answer),
        plan_context={
            "task_id": "TASK-PLAN",
            "task_contract_digest": "sha256:task-binding",
        },
        config=config,
    )

    assert request is not None
    assert request["valid"] is False
    assert "missing executable parameter(s): target_root" in (
        request["validation_error"]
    )


def test_agent_task_workflow_plan_uses_trusted_context_route_parameters() -> None:
    config = load_config(ROOT / "zf.yaml")
    answer = json.loads(_task_workflow_answer())
    del answer["plan_request"]["options"][0]["effect"]["payload"][
        "parameters"
    ]["target_root"]

    request = extract_plan_request(
        json.dumps(answer),
        plan_context={
            "task_id": "TASK-PLAN",
            "task_contract_digest": "sha256:task-binding",
            "workflow_parameters": {
                "target_root": "/trusted/project",
            },
        },
        config=config,
    )

    assert request is not None
    assert request["valid"] is True, request["validation_error"]
    assert request["options"][0]["submit_payload"]["parameters"][
        "target_root"
    ] == "/trusted/project"


def test_invalid_workflow_field_preserves_known_context_for_repair() -> None:
    config = load_config(ROOT / "zf.yaml")
    answer = json.loads(_task_workflow_answer())
    parameters = answer["plan_request"]["options"][0]["effect"]["payload"][
        "parameters"
    ]
    parameters["channel_consensus_event_id"] = "evt-consensus"
    parameters["invented_provider_field"] = "must-not-survive"

    request = extract_plan_request(
        json.dumps(answer),
        plan_context={
            "task_id": "TASK-PLAN",
            "task_contract_digest": "sha256:task-binding",
            "workflow_parameters": {
                "channel_id": "ch-plan",
                "channel_member_id": "leader-1",
                "leader_revision": 2,
                "target_root": "/trusted/project",
            },
        },
        config=config,
    )

    assert request is not None
    assert request["valid"] is False
    assert "invented_provider_field" in request["validation_error"]
    payload = request["options"][0]["submit_payload"]
    assert payload["parameters"]["channel_id"] == "ch-plan"
    assert payload["parameters"]["channel_member_id"] == "leader-1"
    assert payload["parameters"]["target_root"] == "/trusted/project"
    assert payload["parameters"]["consensus_event_id"] == "evt-consensus"
    assert "invented_provider_field" not in payload["parameters"]


def test_plan_subject_boundaries_keep_channel_and_workflow_orthogonal() -> None:
    config = load_config(ROOT / "zf.yaml")
    context = {
        "task_id": "TASK-PLAN",
        "task_contract_digest": "sha256:task-binding",
    }
    workflow_with_channel = extract_plan_request(
        _task_workflow_answer().replace(
            '"action": "task-workflow-start"',
            '"action": "channel-create-and-start"',
            1,
        ),
        plan_context=context,
        config=config,
    )
    channel_with_workflow = extract_plan_request(
        _channel_setup_answer().replace(
            '"submit_action": "channel-create-and-start"',
            '"subject_type": "channel_setup", '
            '"submit_action": "task-workflow-start"',
            1,
        ),
        plan_context=context,
        config=config,
    )

    assert workflow_with_channel is not None
    assert workflow_with_channel["valid"] is False
    assert "task_workflow only allows" in (
        workflow_with_channel["validation_error"]
    )
    assert channel_with_workflow is not None
    assert channel_with_workflow["valid"] is False
    assert "channel_setup only allows" in (
        channel_with_workflow["validation_error"]
    )


def test_plan_request_revision_is_mechanically_monotonic() -> None:
    source = _requested("evt-plan-r2")
    request = source.payload["request"]
    repeated = normalize_plan_request_revision([source], {**request, "revision": 1})
    changed = normalize_plan_request_revision(
        [source],
        {
            **request,
            "revision": 1,
            "request_digest": "changed-digest",
        },
    )

    assert repeated["revision"] == 2
    assert changed["revision"] == 3


def test_plan_request_accepts_one_to_three_clarification_questions() -> None:
    request = extract_plan_request(
        """
        {"plan_request":{"header":"Delivery inputs","questions":[
          {"id":"scope","header":"Scope","question":"Which scope?","options":[
            {"id":"focused","label":"Focused","description":"Small scope."},
            {"id":"broad","label":"Broad","recommended":true,"description":"Full scope."}
          ]},
          {"id":"evidence","header":"Evidence","question":"Which evidence?","options":[
            {"id":"tests","label":"Tests","recommended":true},
            {"id":"tests-and-browser","label":"Tests and browser"}
          ]}
        ]}}
        """
    )

    assert request is not None
    assert request["valid"] is True
    assert request["header"] == "Delivery inputs"
    assert request["questions"][0]["header"] == "Scope"
    assert [item["id"] for item in request["questions"]] == [
        "scope",
        "evidence",
    ]
    assert request["questions"][0]["options"][0]["id"] == "broad"
    assert request["questions"][0]["options"][0]["recommended"] is True
    assert request["question_id"] == "scope"
    assert request["options"] == request["questions"][0]["options"]
    assert plan_request_digest({
        **request,
        "header": "Other delivery inputs",
    }) != request["request_digest"]


def test_plan_discussion_revision_keeps_the_original_request_identity() -> None:
    source = _requested("evt-plan-discussion-source")
    source_request = source.payload["request"]
    source_request["originating_message_event_id"] = "evt-original"
    source_request["originating_message_event_ids"] = ["evt-original"]
    source_request["requirement_digest"] = "sha256:original"
    discussion_message = ZfEvent(
        id="evt-plan-discussion-message",
        type="user.message",
        actor="web",
        payload={
            "message": "Could the recommendation include repository evidence?",
            "request": {
                "plan_discussion": {
                    "request_event_id": source.id,
                    "request_id": source_request["request_id"],
                    "revision": source_request["revision"],
                },
            },
        },
    )

    draft, proposal = prepare_headless_plan_draft(
        [source, discussion_message],
        answer=_request_answer().replace(
            "Collect external evidence first.",
            "Collect repository and external evidence first.",
        ),
        action_proposal=None,
        project_id="project-a",
        conversation_id="kanban:project-a",
        thread_key="main",
        fallback_thread_id="main",
        turn_id="turn-discussion-revision",
        backend="claude-headless",
        provider_session_id="session-1",
        originating_message_event_id=discussion_message.id,
        task_id=None,
        correlation_id="trace-discussion",
    )

    assert proposal is None
    assert draft is not None
    assert draft.request["request_id"] == source_request["request_id"]
    assert draft.request["revision"] == source_request["revision"] + 1
    assert draft.request["originating_message_event_id"] == "evt-original"
    assert draft.request["originating_message_event_ids"] == ["evt-original"]
    assert draft.request["requirement_digest"] == "sha256:original"


def test_plan_discussion_can_replace_invalid_draft_with_valid_revision() -> None:
    source = _requested("evt-invalid-plan-source")
    source_request = source.payload["request"]
    source_request["valid"] = False
    source_request["validation_error"] = "unsupported workflow parameter"
    discussion_message = ZfEvent(
        id="evt-invalid-plan-repair-message",
        type="user.message",
        actor="web",
        payload={
            "message": "Remove unsupported workflow parameters.",
            "request": {
                "plan_discussion": {
                    "request_event_id": source.id,
                    "request_id": source_request["request_id"],
                    "revision": source_request["revision"],
                },
            },
        },
    )

    draft, proposal = prepare_headless_plan_draft(
        [source, discussion_message],
        answer=_request_answer(),
        action_proposal=None,
        project_id="project-a",
        conversation_id="kanban:project-a",
        thread_key="main",
        fallback_thread_id="main",
        turn_id="turn-invalid-plan-repair",
        backend="claude-headless",
        provider_session_id="session-1",
        originating_message_event_id=discussion_message.id,
        task_id=None,
        correlation_id="trace-invalid-plan-repair",
    )

    assert proposal is None
    assert draft is not None
    assert draft.request["request_id"] == source_request["request_id"]
    assert draft.request["revision"] == source_request["revision"] + 1
    assert draft.request["valid"] is True
    assert draft.request["validation_error"] == ""


def test_multi_question_plan_rejects_actions_and_invalid_option_count() -> None:
    action_bound = extract_plan_request(
        """
        {"plan_request":{"questions":[
          {"id":"scope","header":"Scope","question":"Which scope?","options":[
            {"id":"focused","label":"Focused"},
            {"id":"broad","label":"Broad"}
          ]},
          {"id":"route","header":"Route","question":"Which route?","options":[
            {"id":"research","label":"Research","effect":{
              "mode":"propose","action":"workflow-start","payload":{}
            }},
            {"id":"delivery","label":"Delivery"}
          ]}
        ]}}
        """
    )
    invalid_options = extract_plan_request(
        """
        {"plan_request":{"questions":[
          {"id":"a","header":"A","question":"First?","options":[{"label":"One"}]},
          {"id":"b","header":"B","question":"Second?","options":[{"label":"Two"}]}
        ]}}
        """
    )

    assert action_bound is not None
    assert action_bound["valid"] is False
    assert "cannot bind an action" in action_bound["validation_error"]
    assert invalid_options is not None
    assert invalid_options["valid"] is False
    assert "two or three options" in invalid_options["validation_error"]


def test_pending_plan_request_resolves_only_for_bound_answer() -> None:
    source = _requested()
    pending = pending_kanban_plan_requests([source])
    assert [item["request_event_id"] for item in pending] == ["evt-plan"]

    unrelated = ZfEvent(
        type=PLAN_ANSWERED_EVENT,
        actor="web",
        payload={
            "request_event_id": "evt-other",
            "request_id": "plan-other",
        },
    )
    assert len(pending_kanban_plan_requests([source, unrelated])) == 1

    answered = ZfEvent(
        type=PLAN_ANSWERED_EVENT,
        actor="web",
        payload={
            "request_event_id": source.id,
            "request_id": source.payload["request"]["request_id"],
        },
    )
    assert pending_kanban_plan_requests([source, answered]) == []


def test_pending_plan_request_excludes_invalid_agent_output() -> None:
    source = _requested()
    source.payload["request"]["valid"] = False
    source.payload["request"]["validation_error"] = "mutually exclusive output"

    assert pending_kanban_plan_requests([source]) == []


def test_invalid_current_plan_is_discussable_but_not_answerable() -> None:
    source = _requested("evt-plan-invalid-discussion")
    request = source.payload["request"]
    request["valid"] = False
    request["validation_error"] = "unsupported workflow parameter"
    identity = {
        "request_event_id": source.id,
        "request_id": request["request_id"],
        "revision": request["revision"],
    }

    assert plan_request_gate([source], **identity) == {
        "ok": False,
        "status": "plan_request_invalid",
    }
    discussion = plan_request_gate(
        [source],
        **identity,
        require_valid=False,
    )
    assert discussion["ok"] is True
    assert discussion["request"]["valid"] is False
    assert discussion["request"]["validation_error"] == (
        "unsupported workflow parameter"
    )


def test_new_plan_revision_is_not_resolved_by_old_revision_answer() -> None:
    source = _requested("evt-plan-r1")
    request = source.payload["request"]
    answered = ZfEvent(
        type=PLAN_ANSWERED_EVENT,
        actor="web",
        payload={
            "request_event_id": source.id,
            "request_id": request["request_id"],
            "revision": 2,
        },
    )
    revised_request = {**request, "revision": 3, "turn_id": "turn-3"}
    revised = ZfEvent(
        id="evt-plan-r2",
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        payload={"request": revised_request},
    )

    pending = pending_kanban_plan_requests([source, answered, revised])

    assert [item["request_event_id"] for item in pending] == ["evt-plan-r2"]


def test_plan_response_gate_canonicalizes_option_and_dedupes() -> None:
    source = _requested()
    request = source.payload["request"]
    gate = plan_response_gate(
        [source],
        request_event_id=source.id,
        request_id=request["request_id"],
        revision=2,
        question_id="route",
        option_id="research",
        answer="forged display text",
    )

    assert gate["ok"] is True
    assert gate["status"] == "ready"
    assert gate["answer"] == "Research (Recommended)"

    answered = ZfEvent(
        id="evt-answer",
        type=PLAN_ANSWERED_EVENT,
        actor="web",
        payload={
            "request_event_id": source.id,
            "request_id": request["request_id"],
            "revision": 2,
            "question_id": "route",
            "option_id": "research",
            "answer": "Research (Recommended)",
        },
    )
    duplicate = plan_response_gate(
        [source, answered],
        request_event_id=source.id,
        request_id=request["request_id"],
        revision=2,
        question_id="route",
        option_id="research",
        answer="Research (Recommended)",
    )
    conflict = plan_response_gate(
        [source, answered],
        request_event_id=source.id,
        request_id=request["request_id"],
        revision=2,
        question_id="route",
        option_id="channel",
        answer="Channel",
    )

    assert duplicate["ok"] is True
    assert duplicate["status"] == "already_answered"
    assert conflict["ok"] is False
    assert conflict["status"] == "plan_request_already_answered"


def test_plan_response_gate_requires_other_text() -> None:
    source = _requested()
    request = source.payload["request"]

    rejected = plan_response_gate(
        [source],
        request_event_id=source.id,
        request_id=request["request_id"],
        revision=2,
        question_id="route",
        option_id="other",
        answer="",
    )
    accepted = plan_response_gate(
        [source],
        request_event_id=source.id,
        request_id=request["request_id"],
        revision=2,
        question_id="route",
        option_id="other",
        answer="Create a task directly",
    )

    assert rejected["status"] == "plan_answer_required"
    assert accepted["ok"] is True
    assert accepted["answer"] == "Create a task directly"


def test_plan_response_gate_answers_multi_question_plan_atomically() -> None:
    request = extract_plan_request(
        """
        {"plan_request":{"questions":[
          {"id":"scope","header":"Scope","question":"Which scope?","options":[
            {"id":"focused","label":"Focused","recommended":true},
            {"id":"broad","label":"Broad"}
          ]},
          {"id":"evidence","header":"Evidence","question":"Which evidence?","options":[
            {"id":"tests","label":"Tests","recommended":true},
            {"id":"browser","label":"Browser"}
          ],"allow_other":true}
        ]}}
        """,
        plan_context={
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
        },
    )
    assert request is not None and request["valid"] is True
    source = ZfEvent(
        id="evt-multi-plan",
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        payload={"request": request},
    )
    partial = plan_response_gate(
        [source],
        request_event_id=source.id,
        request_id=request["request_id"],
        revision=request["revision"],
        question_id="scope",
        option_id="focused",
        answer="Focused",
    )
    complete = plan_response_gate(
        [source],
        request_event_id=source.id,
        request_id=request["request_id"],
        revision=request["revision"],
        question_id="scope",
        option_id="focused",
        answer="forged",
        answers=[
            {
                "question_id": "scope",
                "option_id": "focused",
                "answer": "forged",
            },
            {
                "question_id": "evidence",
                "option_id": "other",
                "answer": "Tests plus a mobile browser run",
            },
        ],
    )

    assert partial["ok"] is False
    assert partial["status"] == "plan_answers_incomplete"
    assert complete["ok"] is True
    assert complete["submit_mode"] == "continue"
    assert complete["submit_action"] == ""
    assert complete["answers"] == [
        {
            "question_id": "scope",
            "option_id": "focused",
            "answer": "Focused",
        },
        {
            "question_id": "evidence",
            "option_id": "other",
            "answer": "Tests plus a mobile browser run",
        },
    ]


def test_plan_response_gate_rejects_superseded_revision() -> None:
    source = _requested("evt-plan-r2")
    request = source.payload["request"]
    revised = ZfEvent(
        id="evt-plan-r3",
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        payload={
            "request": {
                **request,
                "revision": 3,
                "request_digest": "digest-revision-3",
            },
        },
    )

    gate = plan_response_gate(
        [source, revised],
        request_event_id=source.id,
        request_id=request["request_id"],
        revision=2,
        question_id="route",
        option_id="research",
        answer="Research",
    )

    assert gate["ok"] is False
    assert gate["status"] == "plan_request_superseded"
    assert gate["latest_revision"] == 3
