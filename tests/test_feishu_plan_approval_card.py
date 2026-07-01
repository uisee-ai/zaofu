"""B17 浅档: plan approval 卡片渲染(doc 93 §7.3)。"""

from __future__ import annotations

from zf.integrations.feishu.plan_approval_card import (
    build_plan_approval_card,
    build_plan_verdict_update,
)

PAYLOAD = {
    "plan_id": "evt-abc", "stage_id": "impl", "task_count": 6,
    "pdd_id": "P-1",
}


def test_requested_card_has_deep_link_and_cli_fallback():
    card = build_plan_approval_card(
        PAYLOAD, web_base_url="http://localhost:8001",
        checklist_warnings=["[X] 无 assembly"],
    )
    text = str(card)
    assert "http://localhost:8001/?page=inbox&plan=evt-abc" in text
    assert "zf plan approve evt-abc" in text  # 断网兜底可见
    assert card["_card_key"] == "plan-approval-evt-abc"
    assert card["header"]["template"] == "orange"  # 有告警


def test_verdict_update_same_card_key_idempotent_target():
    up = build_plan_verdict_update("plan.approved", {"plan_id": "evt-abc"})
    assert up["_card_key"] == "plan-approval-evt-abc"
    assert up["header"]["template"] == "green"
    rj = build_plan_verdict_update(
        "plan.rejected", {"plan_id": "evt-abc", "reason": "缺 assembly"},
    )
    assert "缺 assembly" in str(rj)
    auto = build_plan_verdict_update(
        "plan.approved", {"plan_id": "evt-x", "auto": True},
    )
    assert "自动放行" in str(auto)


def test_sync_sends_then_updates_idempotently(tmp_path):
    import json

    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.events.writer import EventWriter
    from zf.integrations.feishu.plan_approval_card import (
        sync_plan_approval_cards,
    )

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(
        type="plan.approval.requested", actor="zf-cli",
        payload={"plan_id": "evt-p1", "stage_id": "impl", "task_count": 6},
    ))
    sent_cards, updates = [], []
    ledger: dict = {}
    r1 = sync_plan_approval_cards(
        state_dir,
        send_card=lambda c: (sent_cards.append(c), f"msg-{len(sent_cards)}")[1],
        update_card=lambda mid, c: updates.append((mid, c)),
        ledger=ledger,
    )
    assert r1["sent"] == ["evt-p1"] and not updates
    # 重跑幂等:不重发
    r2 = sync_plan_approval_cards(
        state_dir,
        send_card=lambda c: (_ for _ in ()).throw(AssertionError("resend!")),
        update_card=lambda mid, c: updates.append((mid, c)),
        ledger=ledger,
    )
    assert r2["sent"] == []
    # 裁决 → 更新原卡片
    writer.append(ZfEvent(
        type="plan.approved", actor="operator", payload={"plan_id": "evt-p1"},
    ))
    r3 = sync_plan_approval_cards(
        state_dir,
        send_card=lambda c: "never",
        update_card=lambda mid, c: updates.append((mid, c)),
        ledger=ledger,
    )
    assert r3["updated"] == ["evt-p1"]
    assert updates[0][0] == "msg-1"
    # 再跑不重复更新
    r4 = sync_plan_approval_cards(
        state_dir, send_card=lambda c: "never",
        update_card=lambda mid, c: (_ for _ in ()).throw(AssertionError("re-update!")),
        ledger=ledger,
    )
    assert r4["updated"] == []


def _write_event(state_dir, etype, payload):
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.events.writer import EventWriter
    EventWriter(EventLog(state_dir / "events.jsonl")).append(
        ZfEvent(type=etype, actor="zf-cli", payload=payload)
    )


def test_push_sends_interactive_card_persists_ledger_and_is_idempotent(tmp_path):
    # P0.1 production wiring: sidecar pushes a Plan Ready card via the transport,
    # persists an on-disk ledger, and reruns do not resend.
    import json

    from zf.integrations.feishu.plan_approval_card import (
        push_plan_approval_cards_once,
    )
    from zf.integrations.feishu.transport import MockFeishuTransport

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_event(state_dir, "plan.approval.requested",
                 {"plan_id": "evt-p1", "stage_id": "impl", "task_count": 6})
    t = MockFeishuTransport()

    r1 = push_plan_approval_cards_once(
        state_dir, t, receive_id="oc_chat", web_base_url="http://w")
    assert r1["sent"] == ["evt-p1"]
    assert len(t.sent_messages) == 1
    assert t.sent_messages[0].msg_type == "interactive"
    ledger_file = (
        state_dir / "integrations" / "feishu" / "plan_approval_ledger.json"
    )
    assert ledger_file.exists()
    assert json.loads(ledger_file.read_text())[
        "plan-approval-evt-p1"]["state"] == "pending"

    r2 = push_plan_approval_cards_once(
        state_dir, t, receive_id="oc_chat", web_base_url="http://w")
    assert r2["sent"] == [] and len(t.sent_messages) == 1


def test_push_updates_card_on_verdict(tmp_path):
    from zf.integrations.feishu.plan_approval_card import (
        push_plan_approval_cards_once,
    )
    from zf.integrations.feishu.transport import MockFeishuTransport

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_event(state_dir, "plan.approval.requested",
                 {"plan_id": "evt-p1", "stage_id": "impl"})
    t = MockFeishuTransport()
    push_plan_approval_cards_once(state_dir, t, receive_id="oc_chat")
    _write_event(state_dir, "plan.approved", {"plan_id": "evt-p1"})

    r = push_plan_approval_cards_once(state_dir, t, receive_id="oc_chat")
    assert r["updated"] == ["evt-p1"]
    assert len(t.updated_messages) == 1
    assert t.updated_messages[0][0] == "mock-msg-1"  # id mock.send_card returned


def test_transport_send_card_returns_provider_message_id():
    import json

    from zf.integrations.feishu.transport import (
        FeishuHttpTransport,
        FeishuMessage,
        MockFeishuTransport,
    )

    msg = FeishuMessage(chat_id="c", content="{}", msg_type="interactive")
    assert MockFeishuTransport().send_card(msg) == "mock-msg-1"

    class _Resp:
        def __init__(self, body): self._b = body
        def read(self): return json.dumps(self._b).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    http = FeishuHttpTransport(
        tenant_access_token="t",
        request_func=lambda req, timeout=15: _Resp(
            {"data": {"message_id": "om_real"}}),
    )
    assert http.send_card(msg) == "om_real"


def test_inline_tasks_render_scope_for_phone_review():
    # 内容直接 inline:operator 不用开(常不可达的)web 深链就能看清批的是什么
    from zf.integrations.feishu.plan_approval_card import build_plan_approval_card
    card = build_plan_approval_card(
        {"plan_id": "evt-x", "stage_id": "impl", "task_count": 2},
        tasks=[
            {"task_id": "TASK-1", "title": "fix cents", "affinity": "pi-core",
             "paths": ["money.js", "tests/money.test.js"]},
            {"task_id": "TASK-2", "title": "", "affinity": "gateway", "paths": ["b.txt"]},
        ])
    s = __import__("json").dumps(card, ensure_ascii=False)
    assert "TASK-1" in s and "fix cents" in s and "money.js" in s   # task + scope inline
    assert "pi-core" in s and "TASK-2" in s
