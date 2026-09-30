from __future__ import annotations

import json

from issue_core import (
    InMemoryIssueSessionStore,
    IssueSessionWorker,
    ProviderResponse,
    ReadOnlyInvestigationTools,
    ToolCallingIssueAgent,
)


class ToolThenReportProvider:
    name = "fake-tool-provider"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                ok=True,
                tool_calls=({"name": "search", "arguments": {"pattern": "pending", "path": "src"}},),
            )
        assert request.context["observations"][0]["output"] == ["src/app.py"]
        return ProviderResponse(ok=True, text=json.dumps({
            "status": "ready",
            "fields": {"title": "Refresh request remains pending"},
            "field_provenance": {"title": {"source": "inferred", "confidence": 0.9}},
            "claims": [{"kind": "reported", "value": "Refresh does not update"}],
            "evidences": [{"kind": "code_search", "ref": "src/app.py"}],
            "gaps": [],
            "hypotheses": [],
            "conflicts": [],
            "report": {
                "title": "Refresh request remains pending",
                "actual_behavior": "The request remains pending",
            },
        }))


def test_tool_calling_agent_collects_information_then_composes_report(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("state = 'pending'\n", encoding="utf-8")
    store = InMemoryIssueSessionStore()
    session = store.create("Refresh does not update")
    provider = ToolThenReportProvider()
    agent = ToolCallingIssueAgent(provider)
    tools = ReadOnlyInvestigationTools(tmp_path, allowed_paths=("src",))

    result = IssueSessionWorker(store).run(session.session_id, agent, tools)
    assert result.status == "ready_for_confirmation"
    assert provider.calls == 2
    assert result.last_result is not None
    assert result.last_result.working_set.report["title"] == "Refresh request remains pending"
    assert result.last_result.working_set.evidences[0]["ref"] == "src/app.py"


def test_tool_calling_agent_does_not_crash_on_non_mapping_sections(tmp_path) -> None:
    class MalformedProvider:
        name = "malformed"

        def complete(self, request):
            return ProviderResponse(ok=True, text=json.dumps({
                "status": "ready",
                "fields": "bad-fields",
                "field_provenance": "bad-provenance",
                "report": "bad-report",
                "validation": "bad-validation",
            }))

    store = InMemoryIssueSessionStore()
    session = store.create("Import fails")
    result = IssueSessionWorker(store).run(
        session.session_id,
        ToolCallingIssueAgent(MalformedProvider()),
        ReadOnlyInvestigationTools(tmp_path),
    )
    assert result.status == "failed"
    assert "mapping report" in (result.last_result.reason if result.last_result else "")
