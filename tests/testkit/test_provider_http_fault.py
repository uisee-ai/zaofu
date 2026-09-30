import http.client
import json
from urllib.parse import urlsplit

from tests.testkit.provider_http_fault import ProviderHttpFault, isolated_provider_env


def test_local_server_errors_then_success_without_payload_evidence():
    with ProviderHttpFault(["rate_limit", "quota"], fallback="success") as server:
        for status in (429, 400, 200):
            connection = http.client.HTTPConnection("127.0.0.1", urlsplit(server.url).port, timeout=2)
            connection.request("POST", "/v1/messages?beta=true", b"private-test-body",
                               {"Authorization": "Bearer private-test-token"})
            response = connection.getresponse()
            assert response.status == status
            body = response.read()
            if status == 429:
                assert response.getheader("Retry-After") == "1"
                assert json.loads(body)["error"]["type"] == "rate_limit_error"
            elif status == 400:
                assert "credit balance" in json.loads(body)["error"]["message"]
            else:
                assert b"event: message_stop" in body
            connection.close()
        assert [r["status"] for r in server.records] == [429, 400, 200]
        assert "private-test" not in json.dumps(server.records)
        assert all(r["request_bytes"] == 17 for r in server.records)


def test_server_rejects_unknown_routes_and_oversized_requests():
    with ProviderHttpFault([], fallback="quota") as server:
        for path, headers, expected in (("/elsewhere", {}, 404),
                                        ("/v1/messages", {"Content-Length": "2000001"}, 413)):
            conn = http.client.HTTPConnection("127.0.0.1", urlsplit(server.url).port, timeout=2)
            conn.request("POST", path, b"", headers)
            response = conn.getresponse()
            assert response.status == expected
            response.read()
            conn.close()
        assert not server.records


def test_provider_environment_uses_only_local_fake_credentials(tmp_path):
    env = isolated_provider_env(tmp_path, "http://127.0.0.1:12345")
    assert env["ANTHROPIC_API_KEY"] == "testkit-not-a-real-key"
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "provider-config")
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert env["NO_PROXY"] == "127.0.0.1,localhost"
