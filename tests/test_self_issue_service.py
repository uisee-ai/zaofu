from __future__ import annotations

import base64
import hashlib
import json
import os
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from zf.core.config.schema import SelfIssueConfig, SelfIssueTargetConfig
from zf.core.events import EventLog, EventWriter
from zf.core.security.secret_provider import SecretKey
from zf.core.self_issue.models import IssueDraft
from zf.integrations.forge.base import (
    AttachmentUploadRequest,
    ForgeResult,
    IssuePublishRequest,
    PublishedIssue,
    UploadedAttachment,
)
from zf.runtime.self_issue_browser_evidence import BrowserCaptureResult
from zf.runtime.self_issue_reproduction_ledger import (
    read_reproduction_ledger,
    record_reproduction_result,
    reproduction_ledger_path,
    reserve_reproduction_attempt,
)
from zf.runtime.self_issue_service import SelfIssueService
from zf.web.self_issue_actions import prepare_self_issue_web_payload


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, str]] = {}

    def put(self, key: SecretKey, value: dict[str, str]) -> None:
        self.values[key.subject] = dict(value)

    def reveal(self, key: SecretKey) -> dict[str, str] | None:
        return self.values.get(key.subject)

    def delete(self, key: SecretKey) -> bool:
        return self.values.pop(key.subject, None) is not None


class FakeProvider:
    name = "gitlab"

    def __init__(self, *, publish_status: str = "published", upload_status: str = "published") -> None:
        self.publish_status = publish_status
        self.upload_status = upload_status
        self.requests: list[IssuePublishRequest] = []
        self.uploads: list[AttachmentUploadRequest] = []
        self.marker_matches: list[PublishedIssue] = []

    def publish(self, request: IssuePublishRequest, *, access_token: str) -> ForgeResult:
        assert access_token == "secret-token"
        self.requests.append(request)
        if self.publish_status == "outcome_unknown":
            return ForgeResult(status="outcome_unknown", reason="response_lost")
        return ForgeResult(status="published", issue=PublishedIssue(
            provider="gitlab", project=request.project, number="17",
            url="https://gitlab.com/a/b/-/issues/17",
        ))

    def upload_attachment(
        self, request: AttachmentUploadRequest, *, access_token: str,
    ) -> ForgeResult:
        assert access_token == "secret-token"
        self.uploads.append(request)
        if self.upload_status == "outcome_unknown":
            return ForgeResult(status="outcome_unknown", reason="response_lost")
        return ForgeResult(status="published", attachment=UploadedAttachment(
            provider="gitlab", project=request.project, filename=request.filename,
            markdown=f"[{request.filename}](/uploads/abc/{request.filename})",
            url=f"https://gitlab.com/a/b/-/uploads/abc/{request.filename}",
            upload_id="upload-1",
        ))

    def find_by_marker(
        self, project: str, marker: str, *, access_token: str,
    ) -> list[PublishedIssue]:
        assert marker and access_token == "secret-token"
        return list(self.marker_matches)


class FakeOAuth:
    def authorization_url(self, **kwargs) -> str:
        return "https://gitlab.com/oauth/authorize?state=" + str(kwargs["state"])

    def exchange(self, **kwargs) -> dict[str, str]:
        assert kwargs["code"] == "auth-code"
        return {
            "access_token": "secret-token", "refresh_token": "refresh-token",
            "scope": "api",
        }

    def refresh(self, **kwargs) -> dict[str, str]:
        return {
            "access_token": "secret-token", "refresh_token": "refresh-token",
            "scope": "api", "expires_at": "2999-01-01T00:00:00+00:00",
        }


class FakeGithubProvider:
    name = "github"

    def __init__(self) -> None:
        self.requests: list[IssuePublishRequest] = []
        self.marker_matches: list[PublishedIssue] = []

    def publish(self, request: IssuePublishRequest, *, access_token: str) -> ForgeResult:
        assert access_token == "github-secret-token"
        self.requests.append(request)
        return ForgeResult(status="published", issue=PublishedIssue(
            provider="github",
            project=request.project,
            number="23",
            url="https://github.com/owner/repo/issues/23",
        ))

    def upload_attachment(
        self, request: AttachmentUploadRequest, *, access_token: str,
    ) -> ForgeResult:
        raise AssertionError("GitHub binary attachment upload must not be called")

    def find_by_marker(
        self, project: str, marker: str, *, access_token: str,
    ) -> list[PublishedIssue]:
        assert access_token == "github-secret-token"
        return list(self.marker_matches)


class FakeGithubOAuth:
    def start(self, *, client_id: str) -> dict[str, str]:
        assert client_id == "Iv-client"
        return {
            "device_code": "device-code-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://github.com/login/device",
            "expires_in": "900",
            "interval": "5",
        }

    def poll(self, *, client_id: str, device_code: str) -> dict[str, str]:
        assert client_id == "Iv-client"
        assert device_code == "device-code-secret"
        return {
            "status": "connected",
            "access_token": "github-secret-token",
            "refresh_token": "github-refresh-token",
            "scope": "issues:write",
        }

    def refresh(self, *, client_id: str, refresh_token: str) -> dict[str, str]:
        return {
            "status": "connected",
            "access_token": "github-secret-token",
            "refresh_token": "github-refresh-token",
            "scope": "issues:write",
        }

def _service(
    tmp_path: Path,
    provider: FakeProvider | None = None,
    *,
    policy: SelfIssueConfig | None = None,
) -> tuple[SelfIssueService, EventLog, MemorySecrets]:
    state = tmp_path / ".state"
    state.mkdir()
    event_log = EventLog(state / "events.jsonl")
    secrets = MemorySecrets()
    service = SelfIssueService(
        state,
        EventWriter(event_log),
        project_root=tmp_path,
        forge_provider=provider or FakeProvider(),
        secret_provider=secrets,
        oauth_client=FakeOAuth(),
        policy=policy,
    )
    return service, event_log, secrets


def _mark_runtime_live(service: SelfIssueService) -> None:
    guard = service.state_dir / "processes" / "watcher.pid.json"
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.write_text(json.dumps({"owner_pid": os.getpid()}), encoding="utf-8")


def test_web_github_authorization_is_shared_across_projects_in_one_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_home = tmp_path / "workspace-home"
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    state_a = project_a / ".zf"
    state_b = project_b / ".zf"
    state_a.mkdir(parents=True)
    state_b.mkdir(parents=True)
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(workspace_home))
    payload_a: dict[str, object] = {}
    payload_b: dict[str, object] = {}
    prepare_self_issue_web_payload(
        payload_a, "self-issue-publish", "same-web-session", "project-a", project_a,
    )
    prepare_self_issue_web_payload(
        payload_b, "self-issue-publish", "same-web-session", "project-b", project_b,
    )
    service_a = SelfIssueService(
        state_a, EventWriter(EventLog(state_a / "events.jsonl")), project_root=project_a,
    )
    service_b = SelfIssueService(
        state_b, EventWriter(EventLog(state_b / "events.jsonl")), project_root=project_b,
    )
    key_a = SecretKey(
        user_id=str(payload_a["user_id"]),
        workspace_id=str(payload_a["workspace_id"]),
        provider="github",
        authorization_domain="github.com",
    )
    key_b = SecretKey(
        user_id=str(payload_b["user_id"]),
        workspace_id=str(payload_b["workspace_id"]),
        provider="github",
        authorization_domain="github.com",
    )

    service_a.secrets.put(key_a, {"access_token": "shared-token"})

    assert key_a.subject == key_b.subject
    assert service_b.secrets.reveal(key_b) == {"access_token": "shared-token"}


def _claim_assessment(service: SelfIssueService) -> dict:
    claimed = service.claim_pending_assessment(owner_pid=os.getpid())
    assert claimed is not None
    assert claimed["status"] == "assessment_claimed"
    return claimed


def _answers(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "title": "Save button does nothing",
        "bug_description": "The saved Draft is not restored after refresh.",
        "reproduction_steps": "1. Open Kanban. 2. Save a Draft. 3. Refresh.",
        "expected_behavior": "The Draft remains visible.",
        "attachments_context": "",
        "environment": {"os": "Linux", "version": "24.04"},
        "zaofu_version": "0.0.3",
        "additional_context": "Observed in a local test project.",
    }
    value.update(updates)
    return value


def _assessment(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "self-issue-assessment.v1",
        "classification": "web/ui",
        "severity": "P2",
        "reproduction_status": "reproduced",
        "component": "web/orchestrator",
        "impact_scope": "Users with a saved Self-Issue Draft",
        "confidence": "high",
        "analysis": {
            "observations": ["The restore request returns the Draft."],
            "hypotheses": ["The projection was not applied to the panel."],
            "counter_evidence": [],
            "unknowns": [],
            "code_locations": ["web/src/components/orchestrator/OrchestratorPanel.tsx:1"],
            "duplicate_assessment": "No matching local report was found.",
            "log_findings": [],
        },
        "recommended_next_action": "Add a focused restore regression test.",
    }
    value.update(updates)
    return value


def _promote(service: SelfIssueService, *, answers: dict[str, object] | None = None) -> dict:
    intake = service.capture({
        "description": "Save button does nothing",
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    result = service.submit_intake({
        "intake_id": intake["intake_id"],
        "answers": answers or _answers(),
    })
    assert result["status"] == "draft_collecting_evidence"
    return result["draft"]


def _complete(service: SelfIssueService, draft: dict) -> dict:
    _mark_runtime_live(service)
    requested = service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    assert requested["status"] == "assessment_requested"
    started = _claim_assessment(service)
    result = service.apply_evidence_assessment({
        "draft_id": draft["draft_id"],
        "run_id": started["run_id"],
        "expected_revision": started["expected_revision"],
        "report": _assessment(),
    })
    assert result["status"] == "evidence_completed"
    return result["draft"]


def _put_secret(service: SelfIssueService, secrets: MemorySecrets, draft: dict) -> None:
    key = service._secret_key({}, service.drafts.get(draft["draft_id"]))  # type: ignore[arg-type]
    secrets.put(key, {"access_token": "secret-token", "scope": "api"})


def test_intake_is_pre_draft_persistent_and_required_answers_fail_closed(tmp_path: Path) -> None:
    service, event_log, _ = _service(tmp_path)
    started = service.capture({
        "description": "Seed title",
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })
    intake = started["intake"]

    assert started["status"] == "intake_collecting"
    assert [item["id"] for item in intake["questions"]] == [
        "title", "bug_description", "reproduction_steps", "expected_behavior",
        "attachments_context", "environment", "zaofu_version", "additional_context",
    ]
    question_by_id = {item["id"]: item for item in intake["questions"]}
    assert [
        item["value"] for item in question_by_id["bug_description"]["options"]
    ] == [
        "The task or workflow is stuck",
        "The page is slow, frozen, or not updating",
        "An error message appeared",
        "The result is incorrect or unexpected",
        "I am not sure what failed",
    ]
    assert question_by_id["reproduction_steps"]["options"][-1]["value"] == (
        "I do not know how to reproduce it"
    )
    assert question_by_id["title"]["options"] == []
    assert intake["answers"]["title"] == "Seed title"
    assert not (service.state_dir / "self-issues" / "drafts.json").exists()

    saved = service.save_intake({
        "intake_id": intake["intake_id"], "revision": intake["revision"],
        "current_step": 2, "answers": {"title": "Edited title", "zaofu_version": "0.0.3"},
    })
    restored = service.get({})
    assert saved["status"] == "intake_saved"
    assert restored["intake"]["answers"]["title"] == "Edited title"
    assert restored["intake"]["current_step"] == 2

    incomplete = service.submit_intake({
        "intake_id": intake["intake_id"],
        "answers": {"title": "Edited title", "zaofu_version": "0.0.3"},
    })
    assert incomplete["ok"] is False
    assert incomplete["status"] == "intake_incomplete"
    assert incomplete["missing_question_id"] == "bug_description"
    assert incomplete["reason"] == "This question can not be empty"
    assert "self_issue.intake.started" in [event.type for event in event_log.read_all()]


def test_promoted_draft_captures_exact_source_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = _service(tmp_path)
    monkeypatch.setattr(service, "_git_head", lambda: "abc123-source-revision")

    draft = _promote(service)

    assert draft["source_code_revision"] == "abc123-source-revision"


def test_web_reporter_capture_becomes_local_evidence_and_confirmation_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = SelfIssueConfig(enabled=True, browser_capture_enabled=True)
    service, _, _ = _service(tmp_path, policy=policy)

    def fake_capture(**kwargs):
        path = (
            service.state_dir / "artifacts" / "self-issues" / kwargs["draft_id"]
            / "browser" / kwargs["run_id"] / "playwright-clean-incident.png"
        )
        path.parent.mkdir(parents=True)
        content = _png_with_text_metadata(b"safe")
        path.write_bytes(content)
        return BrowserCaptureResult("captured", "safe local capture", {
            "ref": path.relative_to(service.state_dir).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "byte_count": len(content),
            "content_type": "image/png",
            "capture_source": "playwright",
            "capture_kind": "playwright_clean_reproduction",
        })

    monkeypatch.setattr(
        "zf.runtime.self_issue_service.capture_self_issue_browser_evidence",
        fake_capture,
    )
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
        "reporter_context": {
            "discovered_by": "user", "reported_by": "user",
            "browser_capture": {
                "requested": True, "target": "kanban_board",
                "base_url": "http://127.0.0.1:8002",
            },
        },
    })["intake"]
    draft = service.submit_intake({
        "intake_id": intake["intake_id"], "answers": _answers(),
    })["draft"]
    _mark_runtime_live(service)
    service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    started = _claim_assessment(service)
    completed = service.apply_evidence_assessment({
        "draft_id": draft["draft_id"], "run_id": started["run_id"],
        "expected_revision": started["expected_revision"], "report": _assessment(),
    })

    candidates = completed["draft"]["attachment_refs"]
    screenshot = next(
        item for item in candidates
        if item.get("kind") == "self_issue_public_evidence_screenshot"
    )
    assert screenshot["capture_source"] == "playwright"
    assert screenshot["access_scope"] == {"external_disclosure": False}


def test_playwright_capture_waits_for_orchestrator_web_target_approval(
    tmp_path: Path, monkeypatch,
) -> None:
    service, _, _ = _service(tmp_path)
    calls: list[object] = []

    def fake_capture(**kwargs):
        calls.append(kwargs)
        return BrowserCaptureResult("captured", "unexpected")

    monkeypatch.setattr(
        "zf.runtime.self_issue_service.capture_self_issue_browser_evidence",
        fake_capture,
    )
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
        "reporter_context": {
            "discovered_by": "user", "reported_by": "user",
            "browser_capture": {
                "requested": True, "target": "kanban_board",
                "base_url": "http://127.0.0.1:8002",
            },
        },
    })["intake"]
    draft = service.submit_intake({
        "intake_id": intake["intake_id"], "answers": _answers(),
    })["draft"]
    _mark_runtime_live(service)
    service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    started = _claim_assessment(service)
    service.apply_evidence_assessment({
        "draft_id": draft["draft_id"], "run_id": started["run_id"],
        "expected_revision": started["expected_revision"],
        "report": _assessment(classification="runtime", component="runtime/worker"),
    })

    assert calls == []


def test_intake_attachment_confirmation_follows_required_answer_validation(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    service.add_intake_attachment({
        "intake_id": intake["intake_id"],
        "filename": "screen.png",
        "content_type": "image/png",
        "content_base64": base64.b64encode(_png_with_text_metadata(b"safe")).decode(),
    })

    missing_required = service.submit_intake({
        "intake_id": intake["intake_id"],
        "answers": {"title": "Attachment validation"},
    })
    missing_confirmation = service.submit_intake({
        "intake_id": intake["intake_id"],
        "answers": _answers(),
    })
    promoted = service.submit_intake({
        "intake_id": intake["intake_id"],
        "answers": _answers(),
        "attachment_disclosure_confirmed": True,
    })

    assert missing_required["status"] == "intake_incomplete"
    assert missing_required["missing_question_id"] == "bug_description"
    assert missing_confirmation["status"] == "attachment_disclosure_required"
    assert missing_confirmation["missing_question_id"] == "attachments_context"
    assert promoted["status"] == "draft_collecting_evidence"


def test_intake_cancel_physically_deletes_record_and_local_answers(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    answer_path = service.state_dir / "artifacts" / "self-issue-intakes" / intake["intake_id"]
    assert answer_path.is_dir()

    dismissed = service.dismiss_intake({"intake_id": intake["intake_id"]})

    assert dismissed["status"] == "intake_cancelled"
    assert service.intakes.get(intake["intake_id"]) is None
    assert not answer_path.exists()


def test_legacy_diagnosis_fields_are_not_accepted_by_the_new_draft_schema() -> None:
    value = {
        "draft_id": "sid-1", "subject_scope": "zaofu", "incident_fingerprint": "abc",
        "title": "title", "summary": "summary", "target_binding": {},
        "diagnostic_status": "completed",
    }
    with pytest.raises(TypeError):
        IssueDraft.from_dict(value)


def test_legacy_draft_rows_are_ignored_without_migrating_them(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    store_path = service.state_dir / "self-issues" / "drafts.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps([{
        "draft_id": "legacy", "incident_fingerprint": "partially-migrated",
        "clarification_digest": "old-schema",
        "title": "Historical diagnosis Draft",
    }]), encoding="utf-8")

    draft = _promote(service)

    assert service.get({})["draft"]["draft_id"] == draft["draft_id"]
    rows = json.loads(store_path.read_text(encoding="utf-8"))
    assert rows[0]["draft_id"] == "legacy"
    assert rows[-1]["incident_fingerprint"]


def test_promoted_intake_retries_return_the_existing_draft(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    submitted = service.submit_intake({
        "intake_id": intake["intake_id"], "answers": _answers(),
    })

    for retried in (
        service.submit_intake({
            "intake_id": intake["intake_id"], "answers": _answers(),
        }),
        service.save_intake({
            "intake_id": intake["intake_id"], "answers": _answers(),
            "current_step": 7,
        }),
        service.dismiss_intake({"intake_id": intake["intake_id"]}),
    ):
        assert retried["status"] == "intake_already_submitted"
        assert retried["draft"]["draft_id"] == submitted["draft"]["draft_id"]


def test_interrupted_submitted_intake_is_restored_and_can_finish(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    record = service.intakes.get(intake["intake_id"])
    assert record is not None
    record.status = "submitted"
    service.intakes.save(record)

    restored = service.get({})
    recovered = service.submit_intake({
        "intake_id": intake["intake_id"], "answers": _answers(),
    })

    assert restored["status"] == "intake_collecting"
    assert restored["intake"]["intake_id"] == intake["intake_id"]
    assert recovered["status"] == "draft_collecting_evidence"


def test_reporter_owned_evidence_ref_is_verified_and_promoted(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    evidence = service.state_dir / "artifacts" / "worker" / "failure.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"error":"bounded"}\n', encoding="utf-8")
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    started = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
        "reporter_context": {"discovered_by": "planner", "reported_by": "planner"},
        "evidence_refs": [{
            "ref": "artifacts/worker/failure.json", "sha256": digest,
            "kind": "worker_failure", "content_type": "application/json",
        }],
    })
    draft = service.submit_intake({
        "intake_id": started["intake"]["intake_id"], "answers": _answers(),
    })["draft"]
    assert draft["evidence_refs"][0]["sha256"] == digest
    assert draft["reporter_context"]["reported_by"] == "planner"

    evidence.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        service.capture({
            "target_binding": {"provider": "gitlab", "project": "a/b"},
            "evidence_refs": [{"ref": "artifacts/worker/failure.json", "sha256": digest}],
        })


def test_incident_dedup_updates_same_reporter_but_never_crosses_reporters(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)

    def report(user_id: str) -> dict:
        intake = service.capture({
            "user_id": user_id,
            "target_binding": {"provider": "gitlab", "project": "a/b"},
        })["intake"]
        return service.submit_intake({
            "intake_id": intake["intake_id"], "answers": _answers(),
        })["draft"]

    first = report("public-user-a")
    repeated = report("public-user-a")
    other_user = report("public-user-b")
    assert repeated["draft_id"] == first["draft_id"]
    assert repeated["occurrence_count"] == 2
    assert other_user["draft_id"] != first["draft_id"]
    assert other_user["incident_fingerprint"] != first["incident_fingerprint"]


def test_evidence_assessment_interrupt_resume_and_revision_conflict(tmp_path: Path) -> None:
    service, event_log, _ = _service(tmp_path)
    draft = _promote(service)
    _mark_runtime_live(service)
    service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    started = _claim_assessment(service)
    ledger = reproduction_ledger_path(
        service.state_dir,
        draft_id=draft["draft_id"],
        run_id=started["run_id"],
    )
    reserved = reserve_reproduction_attempt(
        ledger, target="subject:tests/test_web.py::test_snapshot",
    )
    record_reproduction_result(
        ledger,
        attempt=int(reserved["attempt"]),
        target="subject:tests/test_web.py::test_snapshot",
        status="failed",
    )
    interrupted = service.interrupt_evidence({"draft_id": draft["draft_id"]})
    late = service.apply_evidence_assessment({
        "draft_id": draft["draft_id"], "run_id": started["run_id"],
        "expected_revision": started["expected_revision"], "report": _assessment(),
    })
    resumed = service.resume_evidence({"draft_id": draft["draft_id"]})
    resumed = _claim_assessment(service)
    completed = service.apply_evidence_assessment({
        "draft_id": draft["draft_id"], "run_id": resumed["run_id"],
        "expected_revision": resumed["expected_revision"], "report": _assessment(),
    })

    assert interrupted["status"] == "evidence_interrupted"
    assert late["status"] == "evidence_conflict"
    assert resumed["status"] == "assessment_claimed"
    assert resumed["run_id"] == started["run_id"]
    assert len(read_reproduction_ledger(ledger)["attempts"]) == 1
    assert completed["draft"]["classification"] == "web/ui"
    assert completed["draft"]["component"] == "web/orchestrator"
    assert completed["draft"]["evidence_activity"]["status"] == "completed"
    types = [event.type for event in event_log.read_all()]
    assert "self_issue.evidence.interrupted" in types
    assert "self_issue.evidence.resumed" in types
    assert "self_issue.assessment.completed" in types


def test_interrupted_evidence_restart_creates_a_fresh_reproduction_budget(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)
    draft = _promote(service)
    _mark_runtime_live(service)
    started = service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    old_ledger = reproduction_ledger_path(
        service.state_dir,
        draft_id=draft["draft_id"],
        run_id=started["run_id"],
    )
    reserve_reproduction_attempt(old_ledger, target="subject:tests/a.py")
    interrupted = service.interrupt_evidence({"draft_id": draft["draft_id"]})

    restarted = service.start_evidence({
        "draft_id": draft["draft_id"],
        "revision": interrupted["draft"]["revision"],
        "force": True,
    })
    new_ledger = reproduction_ledger_path(
        service.state_dir,
        draft_id=draft["draft_id"],
        run_id=restarted["run_id"],
    )

    assert restarted["run_id"] != started["run_id"]
    assert read_reproduction_ledger(old_ledger)["attempts"][0]["status"] == (
        "outcome_unknown"
    )
    assert read_reproduction_ledger(new_ledger)["attempts"] == []


def test_evidence_input_contains_redacted_log_excerpts_and_screenshot_inventory(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)
    logs = service.state_dir / "logs"
    logs.mkdir()
    (logs / "runtime.log").write_text(
        "[]\n{}\nERROR timeout TOKEN=private-runtime-token\n[]\n", encoding="utf-8",
    )
    shots = service.state_dir / "artifacts" / "playwright"
    shots.mkdir(parents=True)
    screenshot = shots / "failure.png"
    screenshot.write_bytes(_png_with_text_metadata(b"metadata"))
    draft = _promote(service)
    _mark_runtime_live(service)
    service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    started = _claim_assessment(service)
    input_path = service.state_dir / started["input_ref"]["ref"]
    evidence_input = json.loads(input_path.read_text(encoding="utf-8"))
    mechanical = evidence_input["mechanical_evidence"]
    assert mechanical["log_excerpts"][0]["path"] == "logs/runtime.log"
    assert "private-runtime-token" not in mechanical["log_excerpts"][0]["redacted_tail"]
    assert mechanical["screenshot_refs"][0]["capture_source"] == "playwright"

    completed = service.apply_evidence_assessment({
        "draft_id": draft["draft_id"],
        "run_id": started["run_id"],
        "expected_revision": started["expected_revision"],
        "report": _assessment(),
    })["draft"]
    public_refs = [
        item for item in completed["attachment_refs"]
        if str(item.get("kind", "")).startswith("self_issue_public_evidence_")
    ]
    assert {item["kind"] for item in public_refs} == {
        "self_issue_public_evidence_summary",
        "self_issue_public_evidence_screenshot",
    }
    summary_ref = next(
        item for item in public_refs
        if item["kind"] == "self_issue_public_evidence_summary"
    )
    summary = (service.state_dir / summary_ref["ref"]).read_text(encoding="utf-8")
    assert "Redacted log excerpts" in summary
    assert "Semantically related error log locations" in summary
    assert "No exception or error log location semantically related" in summary
    assert "\n[]\n" not in summary
    assert "\n{}\n" not in summary
    assert "private-runtime-token" not in summary
    screenshot_ref = next(
        item for item in public_refs
        if item["kind"] == "self_issue_public_evidence_screenshot"
    )
    assert b"metadata" not in (service.state_dir / screenshot_ref["ref"]).read_bytes()


def test_semantically_selected_log_location_is_rendered_in_public_evidence(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)
    logs = service.state_dir / "logs"
    logs.mkdir()
    (logs / "web.log").write_text(
        "warning: slow web request GET /snapshot/light took 14300ms\n",
        encoding="utf-8",
    )
    draft = _promote(service, answers=_answers(
        bug_description="The Kanban page takes a long time to render.",
    ))
    _mark_runtime_live(service)
    service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    started = _claim_assessment(service)
    evidence_input = json.loads(
        (service.state_dir / started["input_ref"]["ref"]).read_text(encoding="utf-8"),
    )
    candidate = evidence_input["mechanical_evidence"]["log_error_candidates"][0]
    assessment = _assessment()
    analysis = dict(assessment["analysis"])  # type: ignore[arg-type]
    analysis["log_findings"] = [{
        "candidate_id": candidate["candidate_id"],
        "relation": "supports",
        "confidence": "high",
        "reason": "The measured slow snapshot request matches the reported render delay.",
    }]
    assessment["analysis"] = analysis

    completed = service.apply_evidence_assessment({
        "draft_id": draft["draft_id"],
        "run_id": started["run_id"],
        "expected_revision": started["expected_revision"],
        "report": assessment,
    })["draft"]
    summary_ref = next(
        item for item in completed["attachment_refs"]
        if item["kind"] == "self_issue_public_evidence_summary"
    )
    summary = (service.state_dir / summary_ref["ref"]).read_text(encoding="utf-8")

    assert "`logs/web.log:1`" in summary
    assert "**Relationship:** supports" in summary
    assert "slow web request GET /snapshot/light" in summary
    assert "matches the reported render delay" in summary


def test_unknown_semantic_log_candidate_fails_closed(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    draft = _promote(service)
    _mark_runtime_live(service)
    service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    started = _claim_assessment(service)
    assessment = _assessment()
    analysis = dict(assessment["analysis"])  # type: ignore[arg-type]
    analysis["log_findings"] = [{
        "candidate_id": "logc-not-issued",
        "relation": "supports",
        "confidence": "high",
        "reason": "Fabricated evidence reference.",
    }]
    assessment["analysis"] = analysis

    with pytest.raises(ValueError, match="unknown log candidate"):
        service.apply_evidence_assessment({
            "draft_id": draft["draft_id"],
            "run_id": started["run_id"],
            "expected_revision": started["expected_revision"],
            "report": assessment,
        })


def test_sanitized_runtime_evidence_is_uploaded_and_rendered_as_clickable_links(
    tmp_path: Path,
) -> None:
    provider = FakeProvider()
    service, _, secrets = _service(tmp_path, provider)
    logs = service.state_dir / "logs"
    logs.mkdir()
    (logs / "runtime.log").write_text("ERROR request stalled\n", encoding="utf-8")
    shots = service.state_dir / "artifacts" / "playwright"
    shots.mkdir(parents=True)
    (shots / "failure.png").write_bytes(_png_with_text_metadata(b"metadata"))

    draft = _complete(service, _promote(service))
    preparation = service.attachment_preview({"draft_id": draft["draft_id"]})
    assert {item["kind"] for item in preparation["attachments"]} == {
        "self_issue_public_evidence_summary",
        "self_issue_public_evidence_screenshot",
    }
    assert preparation["preview"]["title"]
    assert preparation["preview"]["body"]
    confirmed = service.attachment_confirm({
        "preparation_id": preparation["preparation_id"],
        "manifest_digest": preparation["manifest_digest"],
    })
    _put_secret(service, secrets, draft)
    prepared = service.attachment_prepare({
        "preparation_id": preparation["preparation_id"],
        "confirmation_id": confirmed["confirmation_id"],
    })
    body = service.preview({"draft_id": draft["draft_id"]})["preview"]["body"]

    assert prepared["status"] == "attachments_prepared"
    assert len(provider.uploads) == 2
    assert "[incident-evidence-summary.md](<https://gitlab.com/a/b/-/uploads/abc/incident-evidence-summary.md>)" in body
    assert "![playwright-incident-1.png](<https://gitlab.com/a/b/-/uploads/abc/playwright-incident-1.png>)" in body
    assert "](/uploads/" not in body
    assert "artifacts/self-issues/" not in body


def test_markdown_preview_is_exact_snapshot_and_publication_is_idempotent(tmp_path: Path) -> None:
    provider = FakeProvider()
    service, _, secrets = _service(tmp_path, provider)
    service._git_head = lambda: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"  # type: ignore[method-assign]
    draft = _complete(service, _promote(service, answers=_answers(
        additional_context="Bearer private-token must not leave the machine",
    )))
    preview = service.preview({"draft_id": draft["draft_id"]})
    body = preview["preview"]["body"]
    assert body.startswith("## Describe the bug")
    assert "{\"" not in body
    assert "private-token" not in body
    assert "## To reproduce" in body
    assert "- **Impact scope:** Users with a saved Self-Issue Draft" in body
    assert "- **Assessment confidence:** high" in body
    assert "- **ZaoFu source commit:** 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" in body
    confirmed = service.confirm({
        "intent_id": preview["intent_id"], "payload_digest": preview["payload_digest"],
    })
    _put_secret(service, secrets, draft)
    published = service.publish({
        "intent_id": preview["intent_id"], "confirmation_id": confirmed["confirmation_id"],
    })
    repeated = service.publish({
        "intent_id": preview["intent_id"], "confirmation_id": confirmed["confirmation_id"],
    })
    assert published["status"] == repeated["status"] == "published"
    assert published["preview"] == preview["preview"]
    assert len(provider.requests) == 1
    assert provider.requests[0].body == body
    restored = service.get({"draft_id": draft["draft_id"]})["draft"]
    assert restored["preview"] == preview["preview"]
    assert service.update({
        "draft_id": draft["draft_id"], "revision": restored["revision"],
        "title": "must not change",
    })["status"] == "published_immutable"
    assert service.start_evidence({
        "draft_id": draft["draft_id"], "revision": restored["revision"], "force": True,
    })["status"] == "published_immutable"


def test_historical_published_ref_does_not_lock_a_restarted_draft(tmp_path: Path) -> None:
    provider = FakeProvider()
    service, _, secrets = _service(tmp_path, provider)
    draft = _complete(service, _promote(service))
    preview = service.preview({"draft_id": draft["draft_id"]})
    confirmed = service.confirm({
        "intent_id": preview["intent_id"],
        "payload_digest": preview["payload_digest"],
    })
    _put_secret(service, secrets, draft)
    published = service.publish({
        "intent_id": preview["intent_id"],
        "confirmation_id": confirmed["confirmation_id"],
    })
    assert published["status"] == "published"

    stored = service.drafts.get(draft["draft_id"])
    assert stored is not None
    assert stored.published_issue_ref
    stored.publication_state = "draft"
    service.drafts.save(stored)

    updated = service.update({
        "draft_id": stored.draft_id,
        "revision": stored.revision,
        "title": "A new report revision after Restart",
    })
    assert updated["status"] == "draft_updated"
    assert updated["draft"]["published_issue_ref"] == stored.published_issue_ref

    _mark_runtime_live(service)
    restarted = service.start_evidence({
        "draft_id": stored.draft_id,
        "revision": updated["draft"]["revision"],
        "force": True,
    })
    assert restarted["status"] == "assessment_requested"
    assert restarted["draft"]["publication_state"] == "draft"
    assert restarted["draft"]["published_issue_ref"] == stored.published_issue_ref
    assert restarted["draft"]["evidence_run_id"] != stored.evidence_run_id
    assert len(restarted["draft"]["evidence_refs"]) > len(stored.evidence_refs)

    claimed = _claim_assessment(service)
    completed = service.apply_evidence_assessment({
        "draft_id": stored.draft_id,
        "run_id": claimed["run_id"],
        "expected_revision": claimed["expected_revision"],
        "report": _assessment(),
    })
    assert completed["status"] == "evidence_completed"
    next_preview = service.preview({"draft_id": stored.draft_id})
    assert next_preview["status"] == "previewed"
    assert next_preview["intent_id"] != preview["intent_id"]


def test_unchanged_draft_update_keeps_revision_and_preview_batch(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    draft = _complete(service, _promote(service))
    preview = service.preview({"draft_id": draft["draft_id"]})

    unchanged = service.update({
        "draft_id": draft["draft_id"],
        "revision": draft["revision"],
        "title": draft["title"],
    })

    assert unchanged["status"] == "draft_unchanged"
    assert unchanged["draft"]["revision"] == draft["revision"]
    assert unchanged["draft"]["batch_id"] == preview["batch_id"]
    assert unchanged["draft"]["publication_batch"]["status"] == "previewed"
    assert preview["draft_revision"] == draft["revision"]


def test_markdown_preview_keeps_empty_user_fields_and_normalizes_soft_line_breaks(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)
    draft = _complete(service, _promote(service, answers=_answers(
        bug_description="The board waits even though the\nruntime is still active.",
        expected_behavior="",
        attachments_context="",
        environment={"os": "", "version": ""},
        additional_context="",
    )))

    body = service.preview({"draft_id": draft["draft_id"]})["preview"]["body"]

    assert "The board waits even though the runtime is still active." in body
    assert "## Expected behavior\n\n(User did not provide this information.)" in body
    assert "## Attachment context\n\n(User did not provide this information.)" in body
    assert "- **Operating system:** (User did not provide this information.)" in body
    assert "## Additional context\n\n(User did not provide this information.)" in body


def test_interrupted_evidence_can_be_previewed_with_an_explicit_gap(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    draft = _promote(service)
    _mark_runtime_live(service)
    service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    interrupted = service.interrupt_evidence({"draft_id": draft["draft_id"]})

    preview = service.preview({"draft_id": draft["draft_id"]})

    assert interrupted["status"] == "evidence_interrupted"
    assert preview["status"] == "previewed"
    assert (
        "Not collected because the user interrupted evidence collection."
        in preview["preview"]["body"]
    )
    assert "## Evidence references" not in preview["preview"]["body"]


def test_failed_evidence_can_be_previewed_as_not_collected(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    draft = _promote(service)
    _mark_runtime_live(service)
    service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    started = _claim_assessment(service)
    failed = service.fail_evidence({
        "draft_id": draft["draft_id"], "run_id": started["run_id"],
        "reason": "no safe incident evidence was available",
    })

    preview = service.preview({"draft_id": draft["draft_id"]})

    assert failed["status"] == "evidence_failed"
    assert preview["status"] == "previewed"
    assert "No incident evidence was collected." in preview["preview"]["body"]


def test_draft_edit_invalidates_the_confirmed_publication_snapshot(tmp_path: Path) -> None:
    service, _, secrets = _service(tmp_path)
    draft = _complete(service, _promote(service))
    preview = service.preview({"draft_id": draft["draft_id"]})
    confirmed = service.confirm({
        "intent_id": preview["intent_id"], "payload_digest": preview["payload_digest"],
    })
    updated = service.update({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
        "title": "Changed after confirmation",
    })
    _put_secret(service, secrets, updated["draft"])
    result = service.publish({
        "intent_id": preview["intent_id"], "confirmation_id": confirmed["confirmation_id"],
    })
    assert result["status"] == "stale_confirmation"


def test_outcome_unknown_locks_and_recovers_by_stable_marker(tmp_path: Path) -> None:
    provider = FakeProvider(publish_status="outcome_unknown")
    service, _, secrets = _service(tmp_path, provider)
    draft = _complete(service, _promote(service))
    preview = service.preview({"draft_id": draft["draft_id"]})
    confirmed = service.confirm({
        "intent_id": preview["intent_id"], "payload_digest": preview["payload_digest"],
    })
    _put_secret(service, secrets, draft)
    unknown = service.publish({
        "intent_id": preview["intent_id"], "confirmation_id": confirmed["confirmation_id"],
    })
    assert unknown["status"] == "outcome_unknown"
    assert service.preview({"draft_id": draft["draft_id"]})["status"] == "outcome_unknown"
    provider.marker_matches = [PublishedIssue(
        "gitlab", "a/b", "17", "https://gitlab.com/a/b/-/issues/17",
    )]
    recovered = service.recover({"intent_id": preview["intent_id"]})
    assert recovered["status"] == "published"


def test_oauth_callback_resumes_only_the_exact_confirmed_publication(tmp_path: Path) -> None:
    provider = FakeProvider()
    service, _, _ = _service(tmp_path, provider)
    draft = _complete(service, _promote(service))
    preview = service.preview({"draft_id": draft["draft_id"]})
    confirmed = service.confirm({
        "intent_id": preview["intent_id"], "payload_digest": preview["payload_digest"],
    })
    assert service.publish({
        "intent_id": preview["intent_id"], "confirmation_id": confirmed["confirmation_id"],
    })["status"] == "authorization_required"
    started = service.oauth_start({
        "draft_id": draft["draft_id"], "intent_id": preview["intent_id"],
        "confirmation_id": confirmed["confirmation_id"], "client_id": "public-client",
        "redirect_uri": "http://127.0.0.1:8002/callback", "session_id": "browser-session",
    })
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
    callback = service.oauth_callback({
        "state": state, "code": "auth-code", "session_id": "browser-session",
    })
    assert callback["status"] == "published"
    assert callback["resumed_publication"] is True
    assert len(provider.requests) == 1
    with pytest.raises(ValueError, match="consumed"):
        service.oauth_callback({
            "state": state, "code": "auth-code", "session_id": "browser-session",
        })


def _png_with_text_metadata(secret: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return len(data).to_bytes(4, "big") + kind + data + crc.to_bytes(4, "big")

    return b"\x89PNG\r\n\x1a\n" + b"".join((
        chunk(b"IHDR", b"\0\0\0\1\0\0\0\1\x08\x02\0\0\0"),
        chunk(b"tEXt", b"token\0" + secret),
        chunk(b"IDAT", zlib.compress(b"\0\0\0\0")),
        chunk(b"IEND", b""),
    ))


def test_attachment_is_sanitized_then_requires_separate_confirmation(tmp_path: Path) -> None:
    provider = FakeProvider()
    service, _, secrets = _service(tmp_path, provider)
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    added = service.add_intake_attachment({
        "intake_id": intake["intake_id"], "filename": "screen.png",
        "content_type": "image/png",
        "content_base64": base64.b64encode(_png_with_text_metadata(b"secret-token")).decode(),
    })
    descriptor = added["intake"]["attachments"][0]
    assert descriptor["redaction_applied"] is True
    stored = next((service.state_dir / "artifacts" / "self-issue-intakes").rglob("*.png"))
    assert b"secret-token" not in stored.read_bytes()
    draft = _complete(service, service.submit_intake({
        "intake_id": intake["intake_id"], "answers": _answers(),
        "attachment_disclosure_confirmed": True,
    })["draft"])
    blocked = service.preview({"draft_id": draft["draft_id"]})
    assert blocked["status"] == "attachment_preparation_required"
    preparation = service.attachment_preview({"draft_id": draft["draft_id"]})
    confirmed = service.attachment_confirm({
        "preparation_id": preparation["preparation_id"],
        "manifest_digest": preparation["manifest_digest"],
    })
    _put_secret(service, secrets, draft)
    prepared = service.attachment_prepare({
        "preparation_id": preparation["preparation_id"],
        "confirmation_id": confirmed["confirmation_id"],
    })
    assert prepared["status"] == "attachments_prepared"
    assert len(provider.uploads) == 1
    final_preview = service.preview({"draft_id": draft["draft_id"]})
    assert "screen.png" in final_preview["preview"]["body"]


def test_unicode_attachment_filename_keeps_extension_and_has_local_preview(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    added = service.add_intake_attachment({
        "intake_id": intake["intake_id"], "filename": "事故截图 现场.png",
        "content_type": "image/png",
        "content_base64": base64.b64encode(_png_with_text_metadata(b"safe")).decode(),
    })
    descriptor = added["intake"]["attachments"][0]
    assert descriptor["filename"] == "事故截图 现场.png"
    draft = _complete(service, service.submit_intake({
        "intake_id": intake["intake_id"], "answers": _answers(),
        "attachment_disclosure_confirmed": True,
    })["draft"])

    preparation = service.attachment_preview({"draft_id": draft["draft_id"]})
    local = preparation["attachments"][0]
    assert local["filename"] == "事故截图 现场.png"
    assert Path(local["local_path"]).is_file()
    path, content_type, filename = service.local_attachment_file(
        draft_id=draft["draft_id"], digest=local["sha256"],
    )
    assert path == Path(local["local_path"])
    assert content_type == "image/png"
    assert filename == "事故截图 现场.png"


def test_attachment_unknown_requires_evidenced_manual_resolution(tmp_path: Path) -> None:
    provider = FakeProvider(upload_status="outcome_unknown")
    service, _, secrets = _service(tmp_path, provider)
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    added = service.add_intake_attachment({
        "intake_id": intake["intake_id"], "filename": "safe.log",
        "content_type": "text/plain",
        "content_base64": base64.b64encode(b"safe log\n").decode(),
    })
    draft = _complete(service, service.submit_intake({
        "intake_id": intake["intake_id"], "answers": _answers(),
        "attachment_disclosure_confirmed": True,
    })["draft"])
    preparation = service.attachment_preview({"draft_id": draft["draft_id"]})
    confirmed = service.attachment_confirm({
        "preparation_id": preparation["preparation_id"],
        "manifest_digest": preparation["manifest_digest"],
    })
    _put_secret(service, secrets, draft)
    unknown = service.attachment_prepare({
        "preparation_id": preparation["preparation_id"],
        "confirmation_id": confirmed["confirmation_id"],
    })
    assert unknown["status"] == "attachment_outcome_unknown"
    with pytest.raises(ValueError, match="evidence_refs"):
        service.resolve_attachment_unknown({
            "preparation_id": preparation["preparation_id"],
            "decision": "not_prepared", "evidence_refs": [],
        })
    resolved = service.resolve_attachment_unknown({
        "preparation_id": preparation["preparation_id"],
        "decision": "not_prepared", "evidence_refs": ["operator-check-1"],
    })
    assert resolved["status"] == "attachment_prepare_failed"


def test_oauth_callback_resumes_the_confirmed_attachment_preparation(tmp_path: Path) -> None:
    provider = FakeProvider()
    service, _, _ = _service(tmp_path, provider)
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    service.add_intake_attachment({
        "intake_id": intake["intake_id"], "filename": "safe.log",
        "content_type": "text/plain",
        "content_base64": base64.b64encode(b"safe evidence\n").decode(),
    })
    draft = _complete(service, service.submit_intake({
        "intake_id": intake["intake_id"], "answers": _answers(),
        "attachment_disclosure_confirmed": True,
    })["draft"])
    preparation = service.attachment_preview({"draft_id": draft["draft_id"]})
    confirmed = service.attachment_confirm({
        "preparation_id": preparation["preparation_id"],
        "manifest_digest": preparation["manifest_digest"],
    })
    assert service.attachment_prepare({
        "preparation_id": preparation["preparation_id"],
        "confirmation_id": confirmed["confirmation_id"],
    })["status"] == "authorization_required"
    started = service.oauth_start({
        "draft_id": draft["draft_id"], "preparation_id": preparation["preparation_id"],
        "confirmation_id": confirmed["confirmation_id"], "client_id": "public-client",
        "redirect_uri": "http://127.0.0.1:8002/callback", "session_id": "browser-session",
    })
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
    callback = service.oauth_callback({
        "state": state, "code": "auth-code", "session_id": "browser-session",
    })
    assert callback["status"] == "attachments_prepared"
    assert callback["resumed_publication"] is True
    assert len(provider.uploads) == 1


def test_locked_target_is_applied_before_intake_and_cannot_be_redirected(tmp_path: Path) -> None:
    policy = SelfIssueConfig(
        enabled=True, target_locked=True, provider="gitlab",
        target_project="owner/central", authorization_domain="gitlab.com",
    )
    service, _, _ = _service(tmp_path, policy=policy)
    intake = service.capture({"target_binding": {}})["intake"]
    assert intake["target_binding"] == {"provider": "gitlab", "project": "owner/central"}
    with pytest.raises(ValueError, match="locked"):
        service.capture({
            "target_binding": {"provider": "gitlab", "project": "attacker/repo"},
        })


def _dual_provider_service(
    tmp_path: Path,
) -> tuple[
    SelfIssueService, MemorySecrets, FakeProvider, FakeGithubProvider,
]:
    state = tmp_path / ".state"
    state.mkdir()
    secrets = MemorySecrets()
    gitlab = FakeProvider()
    github = FakeGithubProvider()
    policy = SelfIssueConfig(
        enabled=True,
        provider="gitlab",
        authorization_domain="gitlab.com",
        target_project="a/b",
        target_locked=True,
        oauth_client_id="gitlab-client",
        oauth_redirect_uri="http://127.0.0.1:8002/",
        targets={
            "gitlab": SelfIssueTargetConfig(
                provider="gitlab",
                authorization_domain="gitlab.com",
                project="a/b",
                oauth_client_id="gitlab-client",
                oauth_redirect_uri="http://127.0.0.1:8002/",
                auth_mode="oauth_pkce",
            ),
            "github": SelfIssueTargetConfig(
                provider="github",
                authorization_domain="github.com",
                project="owner/repo",
                oauth_client_id="Iv-client",
                auth_mode="device_flow",
            ),
        },
        default_publication_mode="gitlab",
    )
    service = SelfIssueService(
        state,
        EventWriter(EventLog(state / "events.jsonl")),
        project_root=tmp_path,
        forge_provider=gitlab,
        forge_providers={"github": github},
        secret_provider=secrets,
        oauth_client=FakeOAuth(),
        github_oauth_client=FakeGithubOAuth(),
        policy=policy,
    )
    return service, secrets, gitlab, github


def _dual_provider_draft(service: SelfIssueService) -> dict:
    intake = service.capture({"target_binding": {}})["intake"]
    promoted = service.submit_intake({
        "intake_id": intake["intake_id"],
        "answers": _answers(),
    })["draft"]
    return _complete(service, promoted)


def test_publication_batch_can_publish_one_snapshot_to_both_providers(
    tmp_path: Path,
) -> None:
    service, secrets, gitlab, github = _dual_provider_service(tmp_path)
    draft = _dual_provider_draft(service)
    preview = service.preview({
        "draft_id": draft["draft_id"],
        "publication_mode": "both",
    })

    assert preview["publication_mode"] == "both"
    assert set(preview["previews"]) == {"gitlab", "github"}
    assert "## Describe the bug" in preview["previews"]["gitlab"]["body"]
    assert "## Describe the bug" in preview["previews"]["github"]["body"]
    assert preview["previews"]["gitlab"]["body"] != preview["previews"]["github"]["body"]
    confirmed = service.confirm({
        "batch_id": preview["batch_id"],
        "payload_digest": preview["payload_digest"],
    })
    stored = service.drafts.get(draft["draft_id"])
    assert stored is not None
    secrets.put(
        service._secret_key({}, stored, provider="gitlab"),
        {"access_token": "secret-token", "scope": "api"},
    )
    secrets.put(
        service._secret_key({}, stored, provider="github"),
        {"access_token": "github-secret-token", "scope": "issues:write"},
    )

    published = service.publish({
        "batch_id": preview["batch_id"],
        "confirmation_id": confirmed["confirmation_id"],
    })
    repeated = service.publish({
        "batch_id": preview["batch_id"],
        "confirmation_id": confirmed["confirmation_id"],
    })

    assert published["status"] == repeated["status"] == "published"
    assert set(published["issues"]) == {"gitlab", "github"}
    assert len(gitlab.requests) == len(github.requests) == 1


def test_github_device_flow_resumes_exact_confirmed_batch_once(tmp_path: Path) -> None:
    service, _, _, github = _dual_provider_service(tmp_path)
    draft = _dual_provider_draft(service)
    preview = service.preview({
        "draft_id": draft["draft_id"],
        "publication_mode": "github",
    })
    confirmed = service.confirm({
        "batch_id": preview["batch_id"],
        "payload_digest": preview["payload_digest"],
    })
    assert service.publish({
        "batch_id": preview["batch_id"],
        "confirmation_id": confirmed["confirmation_id"],
    })["status"] == "authorization_required"

    started = service.github_device_start({
        "draft_id": draft["draft_id"],
        "batch_id": preview["batch_id"],
        "confirmation_id": confirmed["confirmation_id"],
        "session_id": "browser-session",
    })
    assert started["user_code"] == "ABCD-EFGH"
    connected = service.github_device_poll({
        "transaction_id": started["transaction_id"],
        "session_id": "browser-session",
    })

    assert connected["status"] == "published"
    assert connected["resumed_publication"] is True
    assert len(github.requests) == 1
    with pytest.raises(ValueError, match="consumed"):
        service.github_device_poll({
            "transaction_id": started["transaction_id"],
            "session_id": "browser-session",
        })
    transaction_text = (
        service.state_dir / "self-issues" / "github-device-transactions.json"
    ).read_text(encoding="utf-8")
    assert "github-secret-token" not in transaction_text


def test_dual_provider_authorization_resumes_one_batch_without_early_publish(
    tmp_path: Path,
) -> None:
    service, _, gitlab, github = _dual_provider_service(tmp_path)
    draft = _dual_provider_draft(service)
    preview = service.preview({
        "draft_id": draft["draft_id"],
        "publication_mode": "both",
    })
    confirmed = service.confirm({
        "batch_id": preview["batch_id"],
        "payload_digest": preview["payload_digest"],
    })
    blocked = service.publish({
        "batch_id": preview["batch_id"],
        "confirmation_id": confirmed["confirmation_id"],
    })
    assert blocked["provider"] == "gitlab"
    oauth = service.oauth_start({
        "draft_id": draft["draft_id"],
        "batch_id": preview["batch_id"],
        "confirmation_id": confirmed["confirmation_id"],
        "session_id": "browser-session",
    })
    state = parse_qs(urlparse(oauth["authorization_url"]).query)["state"][0]

    github_required = service.oauth_callback({
        "state": state,
        "code": "auth-code",
        "session_id": "browser-session",
    })

    assert github_required["status"] == "authorization_required"
    assert github_required["provider"] == "github"
    assert gitlab.requests == github.requests == []
    device = service.github_device_start({
        "draft_id": draft["draft_id"],
        "batch_id": preview["batch_id"],
        "confirmation_id": confirmed["confirmation_id"],
        "session_id": "browser-session",
    })
    published = service.github_device_poll({
        "transaction_id": device["transaction_id"],
        "session_id": "browser-session",
    })
    assert published["status"] == "published"
    assert len(gitlab.requests) == len(github.requests) == 1


def test_github_only_preview_omits_binary_attachments_without_blocking(
    tmp_path: Path,
) -> None:
    service, _, _, github = _dual_provider_service(tmp_path)
    intake = service.capture({"target_binding": {}})["intake"]
    service.add_intake_attachment({
        "intake_id": intake["intake_id"],
        "filename": "现场截图.png",
        "content_type": "image/png",
        "content_base64": base64.b64encode(_png_with_text_metadata(b"safe")).decode(),
    })
    draft = _complete(service, service.submit_intake({
        "intake_id": intake["intake_id"],
        "answers": _answers(),
        "attachment_disclosure_confirmed": True,
    })["draft"])

    preview = service.preview({
        "draft_id": draft["draft_id"],
        "publication_mode": "github",
    })
    attachment_result = service.attachment_preview({
        "draft_id": draft["draft_id"],
        "publication_mode": "github",
    })

    assert preview["status"] == "previewed"
    assert "Binary attachments are not included" in preview["preview"]["body"]
    assert attachment_result["status"] == "attachments_omitted_for_github"
    assert github.requests == []


def test_draft_close_physically_deletes_draft_intents_and_artifacts(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    draft = _complete(service, _promote(service))
    service.preview({"draft_id": draft["draft_id"]})
    artifact_root = service.state_dir / "artifacts" / "self-issues" / draft["draft_id"]
    assert artifact_root.is_dir()
    dismissed = service.dismiss({"draft_id": draft["draft_id"]})
    assert dismissed["status"] == "draft_dismissed"
    assert service.drafts.get(draft["draft_id"]) is None
    assert not service.intents.for_draft(draft["draft_id"])
    assert not artifact_root.exists()
