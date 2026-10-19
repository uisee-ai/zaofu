from pathlib import Path

from zf.web.headless_agent import _CodexRpcClient


def client():
    return _CodexRpcClient([], cwd=Path.cwd(), timeout_s=1, tool_timeout_s=1,
                           env={"REVIEW_TEST": "1"})


def completed(item_id, text, phase="final_answer"):
    return {"method": "item/completed", "params": {"turnId": "turn-1", "item": {
        "type": "agentMessage", "id": item_id, "phase": phase, "text": text}}}


def finish(c, rows):
    for row in rows:
        c._queue.put(row)
    c._queue.put({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    live = []
    reply, _, incomplete, _ = c.wait_turn(on_message=live.append)
    assert not incomplete
    return reply, live


def test_public_answer_items_survive_without_commentary_or_duplicate_completion():
    part = completed("one", "设计\n\n```py\nx = 1\n```", "partial_answer")
    reply, _ = finish(client(), [completed("progress", "正在检查", "commentary"),
                                 part, part, completed("two", "验证\n尾部约束")])
    assert reply == "设计\n\n```py\nx = 1\n```\n\n验证\n尾部约束"


def test_completed_snapshot_corrects_delta_and_preserves_other_item():
    reply, _ = finish(client(), [
        {"method": "item/agentMessage/delta", "params": {"itemId": "one", "delta": "甲😀"}},
        completed("one", "甲😀\n乙", "partial_answer"),
        completed("two", "丙"),
    ])
    assert reply == "甲😀\n乙\n\n丙"


def test_legacy_anonymous_snapshot_does_not_append_to_its_delta():
    reply, _ = finish(client(), [
        {"method": "item/agentMessage/delta", "params": {"delta": "草稿"}},
        {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "完整答案"}}},
    ])
    assert reply == "完整答案"


def test_process_loss_after_acceptance_without_ack_preserves_partial_and_unknown(tmp_path):
    import sys
    from tests.test_web_headless_agent import _fake_codex_script
    from zf.web.headless_agent import CodexHeadlessBackend

    script = _fake_codex_script(tmp_path)
    lines = [line for line in script.read_text().splitlines()
             if "'turnId':'turn-1'" not in line and "turn/completed" not in line]
    script.write_text("\n".join([*lines, "        raise SystemExit(0)", ""]))
    output = CodexHeadlessBackend(command=f"{sys.executable} {script}").run_turn(
        prompt="request", cwd=tmp_path, system_prompt="", thread_id="thread",
        provider_session_id="", on_session_id=lambda _: None, on_message=None, timeout_s=3)
    assert not output.ok
    assert output.telemetry["input_delivery"] == "unknown"
    assert output.provider_session_id == "codex-thread-1"
    assert output.reply == "headless reply"
    assert output.messages[0].content == "headless reply"


def test_explicit_negative_ack_is_not_misclassified_as_unknown_delivery(tmp_path):
    import sys
    from tests.test_web_headless_agent import _fake_codex_script
    from zf.web.headless_agent import CodexHeadlessBackend

    script = _fake_codex_script(tmp_path)
    text = script.read_text().split("    elif req_id and method == 'turn/start':", 1)[0]
    script.write_text(text + "    elif req_id and method == 'turn/start':\n"
                      "        print(json.dumps({'id':req_id,'error':{'code':429,'message':'Too Many Requests'}}), flush=True)\n")
    output = CodexHeadlessBackend(command=f"{sys.executable} {script}").run_turn(
        prompt="request", cwd=tmp_path, system_prompt="", thread_id="thread",
        provider_session_id="", on_session_id=lambda _: None, on_message=None, timeout_s=3)
    assert not output.ok and not output.reply
    assert output.telemetry["input_delivery"] == "settled"
