"""doc 79 Tier3: Feishu token invalidate + re-mint on 99991663.

R12 ran 8.5h; the tenant_access_token minted at boot expired (~2h) and
``_tenant_token`` kept returning the cached-stale token → 16x HTTP 400
99991663 "Invalid access token". The fix invalidates the cached token on a
token-invalid response and re-mints once.
"""

from __future__ import annotations

import io
import urllib.error

from zf.integrations.feishu.transport import (
    FeishuHttpTransport,
    FeishuMessage,
    is_token_invalid_code,
)


def test_token_invalid_code_detection():
    assert is_token_invalid_code(99991663) is True   # invalid access token
    assert is_token_invalid_code(99991668) is True   # expired
    assert is_token_invalid_code(0) is False
    assert is_token_invalid_code(230001) is False


def _resp(body: str):
    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body.encode("utf-8")

    return _R()


def _http_error(url: str, code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url, code, "err", {}, io.BytesIO(body.encode("utf-8"))
    )


def test_send_remints_on_stale_token_and_succeeds():
    # Sequence the transport should walk:
    #   1) mint  → T1
    #   2) send  → 400 / 99991663 (T1 went stale)
    #   3) re-mint (cache invalidated) → T2
    #   4) send  → ok
    script = [
        ("ok", '{"code":0,"tenant_access_token":"T1","expire":7200}'),
        ("err", 400, '{"code":99991663,"msg":"Invalid access token"}'),
        ("ok", '{"code":0,"tenant_access_token":"T2","expire":7200}'),
        ("ok", '{"code":0,"msg":"ok"}'),
    ]
    steps = iter(script)
    seen = []

    def fake_request(request, timeout=15):
        kind, *rest = next(steps)
        seen.append(request.full_url)
        if kind == "ok":
            return _resp(rest[0])
        raise _http_error(request.full_url, rest[0], rest[1])

    t = FeishuHttpTransport(
        base_url="https://x/open-apis",
        app_id="app",
        app_secret="secret",
        request_func=fake_request,
    )
    assert t.send_message(FeishuMessage(chat_id="oc_1", content="hi")) is True
    # re-mint happened: token rotated to T2, and four HTTP calls were made.
    assert t.tenant_access_token == "T2"
    assert len(seen) == 4


def test_send_does_not_loop_forever_on_persistent_token_failure():
    # If re-mint still yields a token the server rejects, fail after one retry
    # (no infinite loop) — raises, surfaced as a delivery failure upstream.
    script = [
        ("ok", '{"code":0,"tenant_access_token":"T1"}'),
        ("err", 400, '{"code":99991663,"msg":"Invalid access token"}'),
        ("ok", '{"code":0,"tenant_access_token":"T2"}'),
        ("err", 400, '{"code":99991663,"msg":"Invalid access token"}'),
    ]
    steps = iter(script)

    def fake_request(request, timeout=15):
        kind, *rest = next(steps)
        if kind == "ok":
            return _resp(rest[0])
        raise _http_error(request.full_url, rest[0], rest[1])

    t = FeishuHttpTransport(
        base_url="https://x/open-apis",
        app_id="app",
        app_secret="secret",
        request_func=fake_request,
    )
    import pytest

    from zf.integrations.feishu.transport import FeishuTransportError

    with pytest.raises(FeishuTransportError):
        t.send_message(FeishuMessage(chat_id="oc_1", content="hi"))
