from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from zf.runtime.self_issue_session_host import SelfIssueSessionHost
from zf.core.self_issue.markdown import render_publication_markdown
from issue_core import AgentResult
from dataclasses import replace
from zf.core.config.schema import ProjectConfig, SelfIssueConfig, ZfConfig
from zf.web.server import create_app


def test_agent_prompt_collects_template_facts_and_preserves_build_provenance():
    from zf.runtime.self_issue_session_host import _SESSION_SYSTEM_PROMPT

    for required in ('report.environment with os, version,', 'arch and python',
                     'report.project_version', 'report.inspected_source_revision',
                     'report.impact_scope', 'git -C <inspected project> rev-parse HEAD',
                     'continue collecting any', 'Each hypothesis must include hypothesis, confidence, status and support',
                     'Evidence IDs are internal links only'):
        assert required in _SESSION_SYSTEM_PROMPT
    assert 'Do not put this inspected commit in source_code_revision or' in _SESSION_SYSTEM_PROMPT
    assert 'wheel_reported_source_revision unless' in _SESSION_SYSTEM_PROMPT


def test_portable_session_host_runs_and_confirms_without_zaofu_runtime(tmp_path: Path) -> None:
    host = SelfIssueSessionHost(tmp_path / ".state", tmp_path)
    created = host.create_and_run(
        "Refresh fails.\n\nReproduction:\n1. Click Refresh\n\nExpected:\nPage updates",
    )
    assert created["status"] == "ready"
    assert created["report"]["title"]

    completed = host.confirm(created["session_id"], confirmed_by="alice")
    assert completed["session_status"] == "completed"
    assert completed["publication_snapshot_ref"].startswith("sha256:")
    loaded = host.get(created["session_id"])
    assert loaded["status"] == "completed"


def test_confirmed_portable_session_exposes_publication_draft(tmp_path: Path) -> None:
    class Adapter:
        def run(self, *, session, working_set, tools):
            return AgentResult(
                status="ready",
                working_set=replace(
                    working_set,
                    evidences=({"ref": "evidence-1", "kind": "test"},),
                    report={
                        "title": "Refresh is stuck",
                        "bug_description": "Refresh leaves the request pending.",
                        "reproduction_steps": "Click Refresh.",
                        "actual_behavior": "The request remains pending.",
                        "expected_behavior": "The page refreshes.",
                    },
                ),
            )

    host = SelfIssueSessionHost(tmp_path / ".state", tmp_path, agent=Adapter())
    created = host.create_and_run("Refresh is stuck")
    confirmed = host.confirm_and_materialize(created["session_id"])
    assert confirmed["session_status"] == "completed"
    assert confirmed["publication_draft_id"]
    assert confirmed["draft"]["draft_id"] == confirmed["publication_draft_id"]
    restored = host.get(created["session_id"])
    assert restored["draft"]["draft_id"] == confirmed["publication_draft_id"]


def test_ready_session_can_create_draft_and_reanalyze_afterwards(tmp_path: Path) -> None:
    class Adapter:
        def run(self, *, session, working_set, tools):
            return AgentResult(
                status="ready",
                working_set=replace(
                    working_set,
                    report={"title": "first", "actual_behavior": "pending"},
                    evidences=({"ref": "e1", "summary": "first evidence"},),
                ),
            )

    host = SelfIssueSessionHost(tmp_path / ".state", tmp_path, agent=Adapter())
    created = host.create_and_run("refresh fails")
    draft = host.materialize_publication_draft(created["session_id"])
    assert draft["publication_draft_id"]
    rerun = host.reanalyze_and_schedule(created["session_id"], ["actual_behavior"], "再核对 pending")
    assert rerun["session_status"] == "investigating"
    assert host.store.load_working_set(created["session_id"]).workflow["reanalysis_history"]


def test_unpublished_portable_draft_is_refreshed_from_working_set(tmp_path: Path) -> None:
    class Adapter:
        def run(self, *, session, working_set, tools):
            return AgentResult(
                status="ready",
                working_set=replace(
                    working_set,
                    fields={"title": "Initial report", "description": "Initial behavior"},
                    report={"classification": "bug", "component": "initial"},
                ),
            )

    host = SelfIssueSessionHost(tmp_path / ".state", tmp_path, agent=Adapter())
    created = host.create_and_run("The issue")
    confirmed = host.confirm_and_materialize(created["session_id"])
    draft_id = confirmed["publication_draft_id"]
    working = host.store.load_working_set(created["session_id"])
    host.store.save_working_set(
        replace(
            working,
            report={
                "title": "Refreshed report",
                "bug_description": "Refreshed behavior",
                "classification": ["python-packaging", "runtime-dependency"],
                "bounded_scope": ["wheel metadata", "CLI startup"],
                "impact": "Clean installs cannot start the CLI.",
            },
        ),
        expected_revision=working.revision,
    )
    refreshed = host.materialize_publication_draft(created["session_id"])
    assert refreshed["publication_draft_id"] == draft_id
    assert refreshed["draft"]["revision"] == confirmed["draft"]["revision"] + 1
    assert refreshed["draft"]["classification"] == "bug"
    assert refreshed["draft"]["component"] == "wheel metadata / CLI startup"
    assert refreshed["draft"]["actual_behavior"] == "Clean installs cannot start the CLI."


def test_publication_draft_uses_working_set_fields_and_keeps_triage_boundary(tmp_path: Path) -> None:
    class Adapter:
        def run(self, *, session, working_set, tools):
            return AgentResult(
                status="ready",
                working_set=replace(
                    working_set,
                    fields={
                        "title": "Base wheel misses httpx",
                        "description": "The clean install cannot start the CLI.",
                        "reproduction_steps": ["Install the base wheel", "Run --help"],
                        "actual_behavior": "ImportError before help output.",
                        "expected_behavior": "CLI help exits successfully.",
                        "environment": {"python": "3.12"},
                        "source_revision_reported": "abc123",
                    },
                    gaps=(
                        "Need the target source checkout to locate packaging configuration.",
                        "Rebuild the wheel and run clean-install regression after the fix.",
                    ),
                    report={
                        "classification": "bug",
                        "component": "Python packaging",
                        "external_issue_disposition": "ready_to_fix",
                        "verification_gaps": ["Rebuild the wheel and run clean-install regression after the fix."],
                        "bounded_fix_target": "Update runtime dependency metadata.",
                    },
                ),
                validation={"status": "passed_with_gaps"},
            )

    host = SelfIssueSessionHost(tmp_path / ".state", tmp_path, agent=Adapter())
    created = host.create_and_run("The base wheel cannot start the CLI")
    confirmed = host.confirm_and_materialize(created["session_id"])
    draft = confirmed["draft"]
    assert draft["title"] == "Base wheel misses httpx"
    assert draft["bug_description"] == "The clean install cannot start the CLI."
    assert "Install the base wheel" in draft["reproduction_steps"]
    assert draft["actual_behavior"] == "ImportError before help output."
    assert draft["expected_behavior"] == "CLI help exits successfully."
    assert draft["environment"]["python"] == "3.12"
    assert draft["source_code_revision"] == "abc123"
    assert draft["analysis"]["external_issue_disposition"] == "needs_triage"
    assert draft["analysis"]["investigation_gaps"]
    assert draft["analysis"]["downstream_verification_tasks"]


def test_report_aliases_and_public_body_are_normalized_without_internal_validation_noise() -> None:
    normalized = SelfIssueSessionHost._stage_report_contract(
        {
            "title": "Base wheel misses httpx",
            "bug_description": "Importing the package fails before CLI help can start.",
            "reproduction_steps": ["Create a clean venv", "Install only the wheel", "Run CLI help"],
            "expected_behavior": "Package import and CLI help succeed.",
            "classification": ["python-packaging", "runtime-dependency", "cli-startup"],
            "bounded_scope": ["Python packaging", "wheel metadata", "CLI startup"],
            "impact": "Base-wheel users cannot import the package or start CLI help.",
            "external_issue_disposition": "unverified_candidate",
            "project_version": "当前授权 checkout 的 HEAD：fd643cadb437e22bfde10c281b4855e2aa934293",
            "source_code_revision": "5c5fe055a59999e058b182b90acbc7b9f8c68cb0",
            "investigation_gaps": "Locate the packaging source\nInspect wheel METADATA",
            "downstream_verification_tasks": "Build and clean-install the fixed wheel",
        },
        {},
        (),
    )
    assert normalized["classification"] == "bug"
    assert normalized["component"] == "Python packaging / wheel metadata / CLI startup"
    assert normalized["impact_scope"].startswith("Base-wheel users")
    assert normalized["actual_behavior"].startswith("Base-wheel users")
    assert "project_version" not in normalized
    assert normalized["source_code_revision_inspected"] == "fd643cadb437e22bfde10c281b4855e2aa934293"
    assert normalized["external_issue_disposition"] == "needs_triage"
    assert normalized["investigation_gaps"] == ["Locate the packaging source", "Inspect wheel METADATA"]

    body = render_publication_markdown({
        "bug_description": normalized["bug_description"],
        "reproduction_steps": normalized["reproduction_steps"],
        "expected_behavior": normalized["expected_behavior"],
        "environment": {},
        "source_code_revision": normalized["source_code_revision"],
        "classification": normalized["classification"],
        "severity": "P2",
        "reproduction_status": "unverified",
        "component": normalized["component"],
        "impact_scope": "unknown",
        "analysis": {
            "external_issue_disposition": normalized["external_issue_disposition"],
            "investigation_gaps": normalized["investigation_gaps"],
        },
    }, marker="test")
    assert "1. Create a clean venv" in body
    assert "Operating system:" not in body
    assert "Impact scope:** unknown" not in body
    assert "portable session id" not in body.lower()
    assert "validation" not in body.lower()
    assert "needs_triage" in body


def test_report_environment_is_normalized_to_string_map() -> None:
    normalized = SelfIssueSessionHost._stage_report_contract(
        {"environment": "Ubuntu 24.04 / Python 3.12 / x86_64"}, {}, (),
    )
    assert isinstance(normalized["environment"], dict)
    assert normalized["environment"]["details"].startswith("Ubuntu 24.04")


def test_typed_provenance_gap_is_not_routed_to_fix_by_clean_install_keyword():
    gap = {'field': 'clean_install_origin', 'kind': 'provenance_limitation',
           'blocking': False, 'description': 'clean-install test origin unknown'}
    report = SelfIssueSessionHost._stage_report_contract({}, {}, [gap])
    assert report['investigation_gaps'] == [gap]
    assert report['downstream_verification_tasks'] == []


def test_report_values_survive_placeholder_field_projection() -> None:
    normalized = SelfIssueSessionHost._stage_report_contract(
        {
            "classification": "bug",
            "component": "Python packaging / CLI import chain",
            "impact": "Base-wheel users cannot start CLI help.",
            "bounded_scope": ["wheel metadata", "CLI startup"],
        },
        {
            "classification": "unknown",
            "component": "unknown",
            "impact_scope": "unknown",
        },
        (),
    )
    assert normalized["classification"] == "bug"
    assert normalized["component"] == "Python packaging / CLI import chain"
    assert normalized["impact_scope"] == "Base-wheel users cannot start CLI help."


def test_portable_session_host_asks_and_resumes_same_session(tmp_path: Path) -> None:
    host = SelfIssueSessionHost(tmp_path / ".state", tmp_path)
    created = host.create_and_run("Refresh fails")
    assert created["status"] == "ask_user"
    answered = host.answer(created["session_id"], "Click Refresh after opening the page; expected it updates")
    assert answered["session_id"] == created["session_id"]
    assert answered["revision"] > created["revision"]


def test_cancelled_session_is_terminal_and_cannot_be_confirmed(tmp_path: Path) -> None:
    host = SelfIssueSessionHost(tmp_path / ".state", tmp_path)
    created = host.create_and_run("Refresh fails")
    cancelled = host.cancel(created["session_id"])
    assert cancelled["session_status"] == "cancelled"
    try:
        host.confirm(created["session_id"])
    except ValueError as exc:
        assert "terminal" in str(exc) or "ready" in str(exc)
    else:
        raise AssertionError("cancelled session must not be confirmable")


def test_host_accepts_a_provider_or_agent_adapter_without_runtime(tmp_path: Path) -> None:
    class Adapter:
        def run(self, *, session, working_set, tools):
            return AgentResult(
                status="ready",
                working_set=replace(
                    working_set,
                    report={"title": "Adapter-owned report"},
                ),
            )

    host = SelfIssueSessionHost(tmp_path / ".state", tmp_path, agent=Adapter())
    created = host.create_and_run("raw report")
    assert created["status"] == "ready"
    assert created["report"]["title"] == "Adapter-owned report"


async def test_web_capture_can_select_portable_session_path(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / ".state"
    state.mkdir()
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    app = create_app(
        state,
        config=ZfConfig(project=ProjectConfig(name="test", state_dir=".state"), self_issue=SelfIssueConfig(enabled=True)),
        project_root=tmp_path,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/actions/self-issue-capture",
            headers={"X-ZF-Web-Token": "test-token"},
            json={
                "description": "Refresh fails. Reproduction: click Refresh. Expected: page updates",
                "direct_report": True,
                "portable_session": True,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["session_id"].startswith("issue-session-")
        assert payload["status"] in {"investigating", "ready", "ask_user"}

        if payload["status"] == "investigating":
            for _ in range(40):
                await asyncio.sleep(0.05)
                observed = await client.post(
                    "/api/actions/self-issue-session-get",
                    headers={"X-ZF-Web-Token": "test-token"},
                    json={"session_id": payload["session_id"]},
                )
                status = observed.json().get("status")
                if status in {"ready", "ask_user", "failed"}:
                    payload = observed.json()
                    break

        if payload["status"] == "ready":
            confirmed = await client.post(
                "/api/actions/self-issue-session-confirm",
                headers={"X-ZF-Web-Token": "test-token"},
                json={"session_id": payload["session_id"], "confirmed_by": "web-user"},
            )
            assert confirmed.status_code == 200
            assert confirmed.json()["session_status"] == "completed"
