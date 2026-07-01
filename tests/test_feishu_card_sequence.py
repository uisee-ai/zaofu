"""feishu-stream B2: update_card sequence param (CardKit streaming ordering)."""

from __future__ import annotations

import json

from zf.integrations.feishu.transport import FeishuHttpTransport, MockFeishuTransport


def test_mock_records_sequence():
    t = MockFeishuTransport()
    t.update_card("om_1", {"x": 1}, sequence=3)
    assert t.updated_sequences == [("om_1", 3)]
    assert t.updated_messages[-1][0] == "om_1"


def test_mock_default_sequence_zero():
    t = MockFeishuTransport()
    t.update_card("om_1", {"x": 1})  # non-streaming caller, no sequence
    assert t.updated_sequences == [("om_1", 0)]


def test_http_includes_sequence_when_set():
    seen = {}

    class _Resp:
        def read(self): return b'{"code":0}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _req(req, timeout=15):
        seen["body"] = json.loads(req.data.decode())
        return _Resp()

    http = FeishuHttpTransport(tenant_access_token="t", request_func=_req)
    http.update_card("om_1", {"x": 1}, sequence=5)
    assert seen["body"].get("sequence") == 5


def test_http_omits_sequence_when_zero():
    seen = {}

    class _Resp:
        def read(self): return b'{"code":0}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    http = FeishuHttpTransport(
        tenant_access_token="t",
        request_func=lambda req, timeout=15: (seen.__setitem__("body", json.loads(req.data.decode())), _Resp())[1])
    http.update_card("om_1", {"x": 1})  # default 0
    assert "sequence" not in seen["body"]
