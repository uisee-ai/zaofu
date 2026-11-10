"""Scripted model boundary for PRD UI journeys; never edits runtime stores."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import uuid

from zf.runtime.channel_reply_prompt import fake_channel_reply_text


def field(prompt: str, key: str, default: str = "") -> str:
    return next((line[len(key) + 2:] for line in prompt.splitlines() if line.startswith(key + ": ")), default)


def base_prd() -> dict:
    text = fake_channel_reply_text({"member_id": "synthesizer"}, {
        "message_id": "ui-requirement", "text": "Preserve README.md",
        "refs": {"synthesis_request_id": "scripted-provider"},
    })
    return json.loads(text[text.index('{"channel_synthesis"'):])["channel_synthesis"]


def reply(prompt: str, cwd: Path) -> str | dict:
    if "workflow-dsl-editor.v1" in prompt:
        ctx = json.loads(prompt.split("当前上下文：\n", 1)[1])
        changes = [{"field": key, "replacement": True} for key in ctx["fields"]
                   if ".workflow.plan_approval" in key]
        assert len(changes) == 1, "missing plan approval field"
        return {"answer": "生成完整 Plan 后等待 Owner 审批。", "changes": changes}
    if "Use the zf-workflow-synthesis method." in prompt:
        contract, _ = json.JSONDecoder().raw_decode(prompt.split("Contract:\n", 1)[1].strip())
        return {
            "schema_version": "workflow-synthesis-result.v1",
            **{key: contract[key] for key in ("request_id", "request_revision", "requirement_ref", "requirement_digest")},
            "selected_flow_family": "PrdFlow",
            "short_flow_spec": {"flow_family": "PrdFlow", "purpose": "Implement the confirmed PRD using PRD v5", "parameters": {}},
            "decision_rationale": "The owner explicitly requested the registered PRD v5 controller.",
            "assumptions": [], "open_questions": [], "requested_roles": [], "requested_skills": [],
            "requested_profiles": [], "risk_hints": [],
        }
    message = prompt.rsplit("\nUser message:\n", 1)[-1]
    if "UI_PRD_DISCUSS" in message:
        return {"plan_request": {"subject_type": "clarification", "questions": [
            {"header": "需求确认", "id": key, "question": question, "allow_other": True,
             "options": [{"id": "yes", "label": "确认", "recommended": True, "description": "确认需求"},
                         {"id": "discuss", "label": "继续讨论", "description": "补充需求"}]}
            for key, question in [("missing", "README.md 缺失时如何报错？"), ("endpoint", "本轮是否只规划，实施等待审批？")]]}}
    if "UI_PRD_CONFIRMED" in message:
        return "已确认 README.md 缺失时报错并停止；本轮生成完整 PRD 和 Plan，实施等待审批。"
    if "UI_PRD_ANSWER" in message:
        path = cwd / "docs/prd/ui-requirement.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(base_prd()["prd_markdown"], encoding="utf-8")
        return "UI_PRD_WRITTEN：已生成 docs/prd/ui-requirement.md，请审阅。缺失文件需明确报错；本轮只规划，实施等待审批。"
    if "UI_PRD_CREATE" in message:
        source = "docs/prd/ui-requirement.md"
        return {"action_proposal": {"action": "create-task", "intent": {"decision": "propose_action", "source_quote": "请创建一个 Task 并 prepare_workflow"}, "payload": {
            "title": "UI PRD v5 planning", "objective": "Preserve README.md; report missing files; generate the full Plan and wait for owner approval.",
            "execution_mode": "workflow", "prepare_workflow": True, "target_root": str(cwd),
            "contract": {"behavior": "Preserve README.md and report a missing file.", "scope": ["README.md", "tests/**"],
                         "acceptance": "README.md stays readable; absence produces an explicit error.",
                         "verification": "test -f README.md", "verification_tiers": ["runtime"],
                         "source_ref": source, "handoff_artifacts": [source]},
        }}}
    if "UI_CHANNEL_SETUP" in message:
        return {"plan_request": {"subject_type": "channel_setup", "header": "PRD Group",
            "id": "ui-prd-group", "question": "创建需求讨论 Group？", "discussion_seed": "Preserve README.md and report missing files.",
            "submit_action": "channel-create-and-start", "submit_label": "Create Group", "allow_other": False,
            "options": [{"id": "prd", "label": "PRD Group", "recommended": True,
                         "description": "产品、架构、质疑和综合角色参与；先自然讨论。",
                         "submit_payload": {"template_id": "prd-clarification", "channel_id": "ui-prd-group",
                                            "name": "UI PRD Group", "mode": "conversation",
                                            "overrides": {"backend": "fake", "budget": {"max_rounds": 1},
                                                          "role_overrides": {"security_reviewer": {"enabled": False}}}}},
                        {"id": "cancel", "label": "稍后创建", "description": "暂不创建", "submit_payload": {"template_id": "quick-change", "mode": "conversation"}}]}}
    if "UI_CHANNEL_CREATE" in message:
        from tests.e2e.scripts.four_flow_fake_headless import _canonical_prd, _prompt_context
        os.environ["ZF_FOUR_FLOW_CHANNEL_ID"] = "ch-ui-prd-group"
        prd = _canonical_prd(_prompt_context(prompt))
        return {"plan_request": {
            "subject_type": "task_create", "channel_prd_ref": prd["artifact_ref"],
            "channel_prd_digest": prd["artifact_digest"],
            "channel_prd_intent": {"decision": "bind_channel_prd", "source_quote": "UI_CHANNEL_CREATE create a Task"},
            "header": "Create PRD Task", "question_id": "ui-task-create",
            "question": "Create a Task from the confirmed Channel PRD?",
            "options": [
                {"id": "create", "label": "Create Task", "recommended": True,
                 "description": "Prepare PRD v5 Workflow Preview; Owner approves separately.",
                 "effect": {"mode": "propose", "action": "create-task", "payload": {
                     "title": "Channel UI PRD v5 planning", "prepare_workflow": True,
                     "objective": "Generate the complete README Plan and wait for owner approval.",
                     "priority": 2, "scope": ["README.md", "tests/**"],
                     "acceptance": "README.md stays readable; absence produces an explicit error.",
                     "acceptance_criteria": ["README.md stays readable; absence produces an explicit error."],
                     "verification": "test -r README.md", "verification_tiers": ["runtime"],
                     "explicit_non_goals": [], "skills_required": []}}},
                {"id": "continue", "label": "Continue discussion", "description": "Keep discussing.",
                 "effect": {"mode": "continue"}}],
            "allow_other": False, "reason": "Task creation requires an explicit owner decision."}}

    raise ValueError("unrecognized provider turn")


def main() -> None:
    if "--version" in sys.argv:
        print("codex-cli 0.0.0-ui-fake"); return
    if any(arg in sys.argv for arg in ("login", "auth")):
        print("Logged in using isolated fake provider"); return
    if "app-server" not in sys.argv:
        print("Fake planning provider ready ›", flush=True)
        for line in sys.stdin:
            print("›", flush=True)
        return
    cwd = Path.cwd()
    thread = "ui-fake-" + uuid.uuid4().hex
    def emit(value):
        print(json.dumps(value, ensure_ascii=False), flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        method, ident, params = message.get("method"), message.get("id"), message.get("params", {})
        if ident is None:
            continue
        result = {}
        if method in {"thread/start", "thread/resume"}:
            cwd = Path(params.get("cwd") or cwd)
            result = {"threadId": params.get("threadId") or thread, "model": "ui-fake", "reasoningEffort": "low"}
        elif method == "model/list":
            result = {"data": [], "nextCursor": None}
        elif method == "turn/start":
            prompt = "\n".join(item.get("text", "") for item in params.get("input", []))
            audit = Path(os.environ["ZF_UI_RUN_ROOT"]) / "provider-prompts"
            audit.mkdir(exist_ok=True)
            (audit / (uuid.uuid4().hex + ".txt")).write_text(prompt)
            try:
                answer = reply(prompt, cwd)
                if isinstance(answer, dict):
                    answer = json.dumps(answer, ensure_ascii=False)
            except Exception as exc:
                answer = f"UI_FAKE_PROVIDER_ERROR: {type(exc).__name__}: {exc}"
            emit({"jsonrpc": "2.0", "id": ident, "result": {"turnId": uuid.uuid4().hex}})
            emit({"jsonrpc": "2.0", "method": "item/completed", "params": {"item": {"type": "agentMessage", "text": answer}}})
            emit({"jsonrpc": "2.0", "method": "turn/completed", "params": {"turn": {"status": "completed"}}})
            continue
        emit({"jsonrpc": "2.0", "id": ident, "result": result})

if __name__ == "__main__":
    main()
