from __future__ import annotations

import pytest

from issue_core import JsonProviderAgent, ProviderRequest, ProviderResponse


class FakeProvider:
    name = "fake"

    def __init__(self, response):
        self.response = response

    def complete(self, request):
        assert request.timeout_seconds == 30
        assert "read_file" in request.allowed_tools
        return self.response


def test_provider_adapter_parses_json_without_host_dependency() -> None:
    agent = JsonProviderAgent(FakeProvider(ProviderResponse(ok=True, text='{"title":"x"}')))
    value = agent.complete_json(ProviderRequest("system", "user", {}, ("read_file",), 30))
    assert value == {"title": "x"}


def test_provider_adapter_rejects_provider_failure_or_non_object() -> None:
        with pytest.raises(RuntimeError):
            JsonProviderAgent(FakeProvider(ProviderResponse(ok=False, error="offline"))).complete_json(
            ProviderRequest("", "", {}, ("read_file",), 30)
        )
        with pytest.raises(ValueError):
            JsonProviderAgent(FakeProvider(ProviderResponse(ok=True, text="[]"))).complete_json(
            ProviderRequest("", "", {}, ("read_file",), 30)
        )
