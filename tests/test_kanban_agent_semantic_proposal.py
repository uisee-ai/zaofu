from __future__ import annotations

import json

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.kanban_proposals import proposal_execution_gate
from zf.web.proposal_extraction import extract_action_proposal


def _create_task_answer(*, source_quote: str) -> str:
    return json.dumps(
        {
            "action_proposal": {
                "action": "create-task",
                "intent": {
                    "decision": "propose_action",
                    "source_quote": source_quote,
                },
                "payload": {
                    "title": "实现浏览器 2D 网球小游戏 MVP",
                    "contract": {
                        "behavior": "交付玩家对 AI 的浏览器网球小游戏。",
                        "verification": "运行单元测试、构建和浏览器端到端测试。",
                    },
                },
                "reason": "Agent 判断用户要求把需求登记为 Task。",
            }
        },
        ensure_ascii=False,
    )


@pytest.mark.parametrize(
    ("message", "source_quote"),
    [
        ("docs/prd-tennis-mini-game.md 基于这个创建task", "创建task"),
        ("Please create  task from this PRD.", "create  task"),
        ("Convierte este PRD en una tarea.", "una tarea"),
    ],
)
def test_agent_semantic_intent_is_language_independent(
    message: str,
    source_quote: str,
) -> None:
    proposal = extract_action_proposal(
        _create_task_answer(source_quote=source_quote),
        user_message=message,
        proposal_context={"causation_id": "evt-user"},
    )

    assert proposal is not None
    assert proposal["valid"] is True
    assert proposal["action"] == "create-task"
    assert proposal["payload"]["execution_mode"] == "workflow"
    assert proposal["intent"] == {
        "decision": "propose_action",
        "source_quote": source_quote,
        "source_message_event_id": "evt-user",
    }


def test_explicit_direct_create_task_mode_is_preserved() -> None:
    decoded = json.loads(_create_task_answer(source_quote="立即执行"))
    decoded["action_proposal"]["payload"]["execution_mode"] = "direct"

    proposal = extract_action_proposal(
        json.dumps(decoded, ensure_ascii=False),
        user_message="创建任务并立即执行",
    )

    assert proposal is not None and proposal["valid"] is True
    assert proposal["payload"]["execution_mode"] == "direct"


def test_replan_owner_decision_can_be_proposed_for_web_approval() -> None:
    answer = json.dumps({
        "action_proposal": {
            "action": "replan-approve",
            "payload": {
                "approval_ref": "plan-owner-1",
                "checkpoint_id": "plan-owner-1",
                "proposal_ref": "artifacts/plan-synth/checkpoint.json",
                "eval_ref": "artifacts/plan-synth/eval.json",
                "reason": "Owner selected option A with a 0.5% tolerance.",
            },
            "reason": "Resume the same run through bounded replan.",
        },
    })

    proposal = extract_action_proposal(answer)

    assert proposal is not None and proposal["valid"] is True
    assert proposal["action"] == "replan-approve"
    assert proposal["payload"]["checkpoint_id"] == "plan-owner-1"
    assert proposal["payload"]["reason"].endswith("0.5% tolerance.")


@pytest.mark.parametrize(
    ("intent", "error"),
    [
        (None, "intent is required"),
        (
            {"decision": "respond", "source_quote": "创建task"},
            "intent.decision must be propose_action",
        ),
        (
            {"decision": "propose_action", "source_quote": "创建任务"},
            "intent.source_quote must occur verbatim",
        ),
    ],
)
def test_create_task_intent_contract_fails_closed(
    intent: object,
    error: str,
) -> None:
    decoded = json.loads(_create_task_answer(source_quote="创建task"))
    if intent is None:
        decoded["action_proposal"].pop("intent")
    else:
        decoded["action_proposal"]["intent"] = intent

    proposal = extract_action_proposal(
        json.dumps(decoded, ensure_ascii=False),
        user_message="基于这个创建task",
    )

    assert proposal is not None
    assert proposal["valid"] is False
    assert error in proposal["validation_error"]


def test_invalid_semantic_intent_cannot_pass_the_execution_gate() -> None:
    proposal = extract_action_proposal(
        _create_task_answer(source_quote="创建任务"),
        user_message="只分析现有任务",
    )
    assert proposal is not None and proposal["valid"] is False
    event = ZfEvent(
        id="evt-invalid-intent",
        type="kanban.agent.action.proposed",
        actor="web",
        payload={"proposal": proposal},
    )

    result = proposal_execution_gate(
        [event],
        proposal_event_id=event.id,
        action="create-task",
        execution_payload=proposal["payload"],
    )

    assert result == {"ok": False, "status": "proposal_invalid"}


def test_action_proposal_must_be_the_dedicated_final_envelope() -> None:
    answer = (
        "下面只是格式示例：\n"
        f"```json\n{_create_task_answer(source_quote='创建task')}\n```\n"
        "请不要执行这个示例。"
    )

    assert (
        extract_action_proposal(
            answer,
            user_message="请解释 action proposal 格式，不要创建task",
        )
        is None
    )

    final_answer = (
        "我会把该需求整理为待确认 Task。\n"
        f"```json\n{_create_task_answer(source_quote='创建task')}\n```"
    )
    proposal = extract_action_proposal(
        final_answer,
        user_message="基于这个创建task",
    )
    assert proposal is not None and proposal["valid"] is True


def test_final_action_envelope_can_follow_an_unrelated_code_block() -> None:
    answer = (
        "建议先运行：\n"
        "```bash\nnpm test\n```\n"
        "确认后可创建 Task。\n"
        f"```json\n{_create_task_answer(source_quote='创建 Task')}\n```"
    )

    proposal = extract_action_proposal(
        answer,
        user_message="请创建 Task 并保留测试命令",
    )

    assert proposal is not None and proposal["valid"] is True


def test_bare_action_object_is_not_an_agent_proposal() -> None:
    answer = json.dumps(
        {
            "action": "create-task",
            "intent": {
                "decision": "propose_action",
                "source_quote": "创建task",
            },
            "payload": {"title": "不应提取"},
        },
        ensure_ascii=False,
    )

    assert extract_action_proposal(answer, user_message="创建task") is None


def test_final_fenced_bare_action_is_normalized_as_a_proposal() -> None:
    answer = """已完成只读核对，以下仅提出待批准登记。
```json
{
  "action": "create-task",
  "intent": {
    "decision": "propose_action",
    "source_quote": "提出一个待批准的 create-task action proposal"
  },
  "payload": {
    "title": "修复非对称坐标解析",
    "execution_mode": "workflow",
    "scope": ["src/grid-parser.js", "tests/grid-parser.test.js"],
    "contract": {
      "behavior": "按 row,column 顺序解析。",
      "verification": "npm test",
      "spec_skip_reason": "这是边界明确的缺陷修复，没有上游产品规格。"
    }
  },
  "reason": "仅登记 workflow-managed Task。"
}
```"""

    proposal = extract_action_proposal(
        answer,
        user_message="请提出一个待批准的 create-task action proposal",
    )

    assert proposal is not None and proposal["valid"] is True
    assert proposal["payload"]["execution_mode"] == "workflow"
    assert proposal["payload"]["contract"]["scope"] == [
        "src/grid-parser.js",
        "tests/grid-parser.test.js",
    ]
    assert proposal["payload"]["contract"]["verification_tiers"] == ["runtime"]
    assert "scope" not in proposal["payload"]


def test_scoped_workflow_task_requires_source_precedence_before_approval() -> None:
    decoded = json.loads(_create_task_answer(source_quote="创建task"))
    decoded["action_proposal"]["payload"]["workflow_plan"] = {
        "question": "如何执行这个 Task？",
        "options": [],
    }
    decoded["action_proposal"]["payload"]["contract"]["scope"] = [
        "src/grid-parser.js",
    ]

    proposal = extract_action_proposal(
        json.dumps(decoded, ensure_ascii=False),
        user_message="基于这个创建task",
    )

    assert proposal is not None
    assert proposal["valid"] is False
    assert (
        proposal["validation_error"]
        == "scoped workflow Task requires a source contract ref or "
        "contract.spec_skip_reason"
    )


@pytest.mark.parametrize(
    ("missing_field", "validation_error"),
    [
        ("behavior", "contract.behavior is required"),
        ("verification", "contract.verification is required"),
    ],
)
def test_workflow_plan_requires_complete_task_contract_before_approval(
    missing_field: str,
    validation_error: str,
) -> None:
    decoded = json.loads(_create_task_answer(source_quote="创建task"))
    decoded["action_proposal"]["payload"]["workflow_plan"] = {
        "question": "如何执行这个 Task？",
        "options": [],
    }
    decoded["action_proposal"]["payload"]["contract"].pop(missing_field)

    proposal = extract_action_proposal(
        json.dumps(decoded, ensure_ascii=False),
        user_message="基于这个创建task",
    )

    assert proposal is not None
    assert proposal["valid"] is False
    assert validation_error in proposal["validation_error"]


def test_bare_action_example_inside_prose_is_not_extracted() -> None:
    answer = """Example only:
```json
{
  "action": "create-task",
  "intent": {"decision": "propose_action", "source_quote": "创建task"},
  "payload": {"title": "Do not create"}
}
```
Do not present this example for approval."""

    assert extract_action_proposal(answer, user_message="解释如何创建task") is None


@pytest.mark.parametrize("behavior", [None, "", "既有行为必须保留"])
def test_workflow_proposal_normalizes_objective_before_approval(behavior) -> None:
    decoded = json.loads(_create_task_answer(source_quote="创建task"))
    payload = decoded["action_proposal"]["payload"]
    payload["workflow_plan"] = {"question": "如何执行？", "options": []}
    contract = payload["contract"]
    contract.pop("behavior")
    if behavior is not None:
        contract["behavior"] = behavior
    contract["objective"] = "按全文修复步长；覆盖零和负数，不重构。"
    proposal = extract_action_proposal(
        json.dumps(decoded, ensure_ascii=False), user_message="创建task",
    )
    assert proposal is not None and proposal["valid"] is True
    normalized = proposal["payload"]
    assert normalized["contract"]["behavior"] == (
        behavior or contract["objective"]
    )
    assert "objective" not in normalized["contract"]
    assert contract["objective"] == "按全文修复步长；覆盖零和负数，不重构。"
    if behavior:
        assert contract["objective"] in normalized["notes"]


@pytest.mark.parametrize("objective", [None, "", [], {"behavior": "invented"}])
def test_workflow_proposal_rejects_nontext_objective(objective) -> None:
    decoded = json.loads(_create_task_answer(source_quote="创建task"))
    payload = decoded["action_proposal"]["payload"]
    payload["workflow_plan"] = {"question": "如何执行？", "options": []}
    payload["contract"].pop("behavior")
    payload["contract"]["objective"] = objective
    proposal = extract_action_proposal(
        json.dumps(decoded, ensure_ascii=False), user_message="创建task",
    )
    assert proposal is not None and proposal["valid"] is False
    assert "contract.behavior is required" in proposal["validation_error"]


def test_idea_to_product_uses_the_same_semantic_intent_contract() -> None:
    answer = json.dumps(
        {
            "action_proposal": {
                "action": "idea-to-product",
                "intent": {
                    "decision": "propose_action",
                    "source_quote": "做成产品",
                },
                "payload": {"objective": "把浏览器网球游戏做成产品"},
            }
        },
        ensure_ascii=False,
    )

    proposal = extract_action_proposal(
        answer,
        user_message="把浏览器网球游戏做成产品",
        proposal_context={"causation_id": "evt-idea"},
    )

    assert proposal is not None and proposal["valid"] is True
    assert proposal["intent"]["source_message_event_id"] == "evt-idea"


def test_test1_contract_shapes_materialize_without_python_repr() -> None:
    answer = json.dumps(
        {
            "action_proposal": {
                "action": "create-task",
                "intent": {
                    "decision": "propose_action",
                    "source_quote": "创建task",
                },
                "payload": {
                    "title": "实现浏览器 2D 网球小游戏 MVP",
                    "contract": {
                        "spec_ref": "docs/prd-tennis-mini-game.md",
                        "behavior": [
                            "使用 TypeScript 与 2D Canvas。",
                            "支持玩家对 AI 的完整对局。",
                        ],
                        "verification": {
                            "command": "npm test && npm run build",
                            "evidence": "浏览器完整对局证据",
                        },
                        "acceptance": [
                            "Chrome 中可完成对局",
                            "AC-01 至 AC-09 均有证据",
                        ],
                    },
                },
            }
        },
        ensure_ascii=False,
    )

    proposal = extract_action_proposal(answer, user_message="基于这个创建task")

    assert proposal is not None and proposal["valid"] is True
    contract = proposal["payload"]["contract"]
    assert contract["behavior"] == (
        "使用 TypeScript 与 2D Canvas。\n支持玩家对 AI 的完整对局。"
    )
    assert contract["verification"] == (
        "command: npm test && npm run build\n"
        "evidence: 浏览器完整对局证据"
    )
    assert contract["acceptance"] == (
        "Chrome 中可完成对局\nAC-01 至 AC-09 均有证据"
    )
    assert contract["acceptance_criteria"] == [
        "Chrome 中可完成对局",
        "AC-01 至 AC-09 均有证据",
    ]
    assert "['" not in json.dumps(contract, ensure_ascii=False)
    assert "{'command'" not in json.dumps(contract, ensure_ascii=False)
