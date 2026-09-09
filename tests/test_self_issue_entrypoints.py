from __future__ import annotations

import json
from pathlib import Path

import httpx

from zf.cli.main import main
from zf.core.config.schema import ProjectConfig, SelfIssueConfig, ZfConfig
from zf.web.server import create_app


def _project(tmp_path: Path, *, locked: bool = False) -> Path:
    state = tmp_path / ".state"
    state.mkdir()
    config = [
        "version: '1.0'", "project:", "  name: test", "  state_dir: .state",
    ]
    if locked:
        config.extend((
            "self_issue:", "  enabled: true", "  target_locked: true",
            "  provider: gitlab", "  target_project: owner/central",
        ))
    (tmp_path / "zf.yaml").write_text("\n".join(config) + "\n", encoding="utf-8")
    return state


def _answers() -> dict[str, object]:
    return {
        "title": "CLI title", "bug_description": "CLI observed a failure.",
        "reproduction_steps": "Run zf and open Kanban.", "expected_behavior": "Kanban opens.",
        "attachments_context": "", "environment": {"os": "Linux", "version": "24.04"},
        "zaofu_version": "0.0.3", "additional_context": "",
    }


def test_cli_report_starts_the_same_persistent_pre_draft_intake(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    state = _project(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert main(["issue", "report", "CLI title", "--non-interactive"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["action"] == "self-issue-capture"
    assert result["status"] == "intake_collecting"
    assert result["intake"]["answers"]["title"] == "CLI title"
    assert (state / "self-issues" / "intakes.json").is_file()
    assert not (state / "self-issues" / "drafts.json").exists()


def test_cli_publication_commands_expose_provider_batch_arguments(
    monkeypatch,
) -> None:
    observed: list[dict[str, object]] = []

    def fake_run(args) -> int:
        observed.append(vars(args))
        return 0

    monkeypatch.setattr("zf.cli.issue._run_publication_action", fake_run)

    assert main(["issue", "preview", "sid-1", "--provider", "both"]) == 0
    assert main([
        "issue", "confirm", "pubb-1", "--payload-digest", "abc",
    ]) == 0
    assert main([
        "issue", "publish", "pubb-1", "--confirmation-id", "confirm-1",
    ]) == 0
    assert observed[0]["publication_mode"] == "both"
    assert observed[1]["payload_digest"] == "abc"
    assert observed[2]["confirmation_id"] == "confirm-1"


def test_cli_answer_promotes_the_intake_and_uses_the_evidence_action(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["issue", "report", "CLI title", "--non-interactive"]) == 0
    intake = json.loads(capsys.readouterr().out)["intake"]
    answer_file = tmp_path / "answers.json"
    answer_file.write_text(json.dumps({"answers": _answers()}), encoding="utf-8")
    assert main([
        "issue", "answer", intake["intake_id"], "--answers-file", str(answer_file),
    ]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "evidence_waiting_for_runtime"
    assert result["draft"]["assessment_status"] == "waiting_for_runtime"
    assert result["draft"]["title"] == "CLI title"


def test_cli_report_redacts_request_and_obeys_the_locked_target(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    state = _project(tmp_path, locked=True)
    monkeypatch.chdir(tmp_path)
    assert main([
        "issue", "report", "failure TOKEN=private-value", "--non-interactive",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["intake"]["target_binding"]["project"] == "owner/central"
    assert "private-value" not in (state / "events.jsonl").read_text(encoding="utf-8")

    assert main([
        "issue", "report", "redirect", "--target-project", "other/repo", "--non-interactive",
    ]) == 1
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["status"] == "invalid_self_issue_request"


async def test_web_slash_action_persists_answers_and_promotes_to_canonical_draft(
    tmp_path: Path, monkeypatch,
) -> None:
    state = _project(tmp_path)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    app = create_app(
        state,
        config=ZfConfig(project=ProjectConfig(name="test", state_dir=".state")),
        project_root=tmp_path,
    )
    headers = {"X-ZF-Web-Token": "test-token"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/api/actions/self-issue-capture", headers=headers,
            json={"description": "Web title", "target_binding": {"provider": "gitlab", "project": "a/b"}},
        )
        intake = started.json()["intake"]
        saved = await client.post(
            "/api/actions/self-issue-intake-save", headers=headers,
            json={
                "intake_id": intake["intake_id"], "revision": intake["revision"],
                "answers": _answers(), "current_step": 7,
            },
        )
        events_before_read = (state / "events.jsonl").read_text(encoding="utf-8")
        restored = await client.post(
            "/api/actions/self-issue-get", headers=headers, json={},
        )
        events_after_read = (state / "events.jsonl").read_text(encoding="utf-8")
        submitted = await client.post(
            "/api/actions/self-issue-intake-submit", headers=headers,
            json={"intake_id": intake["intake_id"], "answers": _answers()},
        )

    assert started.status_code == saved.status_code == restored.status_code == 200
    assert started.json()["status"] == "intake_collecting"
    browser_capture = started.json()["intake"]["reporter_context"]["browser_capture"]
    assert browser_capture["requested"] is True
    assert browser_capture["target"] == "kanban_board"
    assert browser_capture["base_url"] == "http://test/"
    assert browser_capture["project_id"]
    assert events_after_read == events_before_read
    assert restored.json()["intake"]["answers"]["bug_description"] == "CLI observed a failure."
    assert submitted.json()["status"] == "draft_collecting_evidence"
    assert json.loads((state / "self-issues" / "drafts.json").read_text())[0]["title"] == "CLI title"


async def test_web_external_issue_actions_receive_the_authenticated_user_binding(
    tmp_path: Path, monkeypatch,
) -> None:
    state = _project(tmp_path)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    prepared: list[tuple[str, str, str]] = []

    def record_binding(
        payload: dict[str, object],
        action: str,
        identity: str,
        project_id: str,
        project_root: Path,
        *,
        web_base_url: str = "",
    ) -> None:
        prepared.append((action, identity, project_id))

    monkeypatch.setattr(
        "zf.web.server.prepare_self_issue_web_payload",
        record_binding,
    )
    app = create_app(
        state,
        config=ZfConfig(project=ProjectConfig(name="test", state_dir=".state")),
        project_root=tmp_path,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/actions/external-issue-needs-info-submit",
            headers={"X-ZF-Web-Token": "test-token"},
            json={"task_id": "ISSUE-1", "response": "details"},
        )

    assert len(prepared) == 1
    action, identity, project_id = prepared[0]
    assert action == "external-issue-needs-info-submit"
    assert identity == "test-token"
    assert project_id


async def test_web_evidence_start_waits_for_runtime_without_scheduling_an_agent(
    tmp_path: Path, monkeypatch,
) -> None:
    state = _project(tmp_path)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    scheduled: list[dict] = []

    def fake_run(**kwargs):
        scheduled.append(kwargs)
        return {"ok": True, "status": "evidence_completed"}

    monkeypatch.setattr(
        "zf.runtime.self_issue_assessment_executor.run_self_issue_assessment",
        fake_run,
    )
    config = ZfConfig(project=ProjectConfig(name="test", state_dir=".state"))
    app = create_app(state, config=config, project_root=tmp_path)
    headers = {"X-ZF-Web-Token": "test-token"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = (await client.post(
            "/api/actions/self-issue-capture", headers=headers,
            json={"target_binding": {"provider": "gitlab", "project": "a/b"}},
        )).json()
        promoted = (await client.post(
            "/api/actions/self-issue-intake-submit", headers=headers,
            json={"intake_id": started["intake"]["intake_id"], "answers": _answers()},
        )).json()
        evidence = (await client.post(
            "/api/actions/self-issue-evidence-start", headers=headers,
            json={
                "draft_id": promoted["draft"]["draft_id"],
                "revision": promoted["draft"]["revision"],
                "backend": "codex-headless",
            },
        )).json()

    assert evidence["status"] == "evidence_waiting_for_runtime"
    assert evidence["scheduled"] is False
    assert evidence["draft"]["runtime_status"] in {"stopped", "unknown"}
    assert evidence["draft"]["assessment_status"] == "waiting_for_runtime"
    assert scheduled == []


async def test_web_locked_target_is_central_and_not_user_editable(
    tmp_path: Path, monkeypatch,
) -> None:
    state = _project(tmp_path)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=".state"),
        self_issue=SelfIssueConfig(
            enabled=True, target_locked=True, provider="gitlab", target_project="owner/central",
        ),
    )
    app = create_app(state, config=config, project_root=tmp_path)
    headers = {"X-ZF-Web-Token": "test-token"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = await client.post(
            "/api/actions/self-issue-capture", headers=headers, json={},
        )
        rejected = await client.post(
            "/api/actions/self-issue-capture", headers=headers,
            json={"target_binding": {"provider": "gitlab", "project": "other/repo"}},
        )
    assert accepted.json()["intake"]["target_binding"]["project"] == "owner/central"
    assert rejected.status_code == 422
    assert rejected.json()["status"] == "invalid_self_issue_request"
