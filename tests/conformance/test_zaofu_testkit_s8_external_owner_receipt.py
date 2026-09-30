"""Real watcher delivery to a separate loopback HTTP receiver, not real Feishu."""

import json
import os
import socket
from urllib.parse import urlparse

import pytest

from tests.testkit.general_route import GeneralRoute
from tests.testkit.issue_route import isolate_issue_processes
from tests.testkit.owner_http_receiver import TOKEN, owner_http_receiver, read_receiver
from zf.integrations.feishu.transport import FeishuHttpTransport
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


pytestmark = pytest.mark.host


@pytest.mark.parametrize("reject", [False, True])
def test_kernel_terminal_watcher_http_receiver_receipt(tmp_path, monkeypatch, reject):
    with owner_http_receiver(reject=reject) as (url, receiver_pid):
        assert receiver_pid != os.getpid()
        connect = socket.socket.connect
        create_connection = socket.create_connection
        forbidden = isolate_issue_processes(tmp_path, monkeypatch)
        address = ("127.0.0.1", urlparse(url).port)

        def loopback_only(sock, destination):
            assert destination == address, f"unexpected network destination: {destination}"
            return connect(sock, destination)

        monkeypatch.setattr(socket.socket, "connect", loopback_only)
        monkeypatch.setattr(socket, "create_connection", create_connection)
        for name in tuple(os.environ):
            if name.startswith(("FEISHU_", "ZF_OWNER_VISIBLE_")) or name.lower().endswith("_proxy"):
                monkeypatch.delenv(name, raising=False)
        for name, value in {
            "FEISHU_BASE_URL": url + "/open-apis", "FEISHU_TENANT_ACCESS_TOKEN": TOKEN,
            "FEISHU_APP_ID": "testkit-app", "FEISHU_APP_SECRET": "testkit-public-secret",
            "ZF_OWNER_VISIBLE_CHAT": "oc_testkit_receiver",
            "ZF_OWNER_VISIBLE_RECEIVE_ID_TYPE": "chat_id", "ZF_ALLOW_LIVE_FEISHU_IN_TESTS": "1",
        }.items():
            monkeypatch.setenv(name, value)
        route = GeneralRoute(tmp_path, monkeypatch)
        with route.evidence.guard("owner_http_receiver", {"reject": reject}):
            route.complete_reader(route.start())
            for stage in ("collect-1", "collect-2", "synthesize"):
                route.complete_reader(route.dispatch(stage))
            route.complete_verify(route.dispatch("verify"))
            for _ in range(12):
                route.tick()
                if route.events("run.goal.completed"):
                    break
            [terminal] = route.events("run.goal.completed")
            ticks = TickServiceState()
            run_standard_tick_services(route.runtime, state=ticks, now=1.0)
            [owner] = [event for event in route.events("owner.visible_message.requested")
                       if event.payload.get("terminal_event_id") == terminal.id]
            received = read_receiver(url + "/testkit/received")
            delivered = route.events("owner.visible_message.delivered")
            if reject:
                assert not received["messages"]
                assert len(received["attempts"]) == 2
                assert not delivered
                [failed] = route.events("owner.visible_message.failed")
                assert failed.payload["source_event_id"] == owner.id
            else:
                [receipt] = delivered
                [message] = received["messages"]
                assert receipt.payload["source_event_id"] == owner.id
                assert receipt.correlation_id == owner.correlation_id == terminal.payload["run_id"]
                assert receipt.payload["feishu_message_id"] == message["message_id"]
                assert receipt.payload["receive_id"] == message["chat_id"] == "oc_testkit_receiver"
                card = json.loads(message["body"]["content"])
                body = card["elements"][0]["text"]["content"]
                assert owner.payload["summary"] in body
                assert len(received["attempts"]) == 1
                readback = FeishuHttpTransport().list_recent("oc_testkit_receiver")
                assert [row["message_id"] for row in readback] == [message["message_id"]]
            route.reopen()
            for _ in range(3):
                route.tick()
                run_standard_tick_services(route.runtime, state=ticks, now=2.0)
            assert read_receiver(url + "/testkit/received") == received
            assert [event.id for event in route.events("run.goal.completed")] == [terminal.id]
            assert not forbidden
