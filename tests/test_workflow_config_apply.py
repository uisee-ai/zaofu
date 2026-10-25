from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from zf.core.events import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.workflow_proposal import build_workflow_proposal
from zf.runtime.workflow_requests import workflow_request_path
from zf.runtime.run_contract import (
    bind_run_contract_workflow_artifacts,
    hydrate_run_effective_config,
)
from zf.runtime.workflow_config_apply import (
    WorkflowConfigApplyError,
    WorkflowConfigApplyService,
)


def _config(path: Path, *, lanes: int) -> None:
    path.write_text(
        f"""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {{name: issue-demo}}
spec:
  lanes: {lanes}
  backend: mock
  issueRef: docs/issue.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {{name: demo}}
spec:
  version: "1.0"
  project: {{name: demo, state_dir: .zf}}
""",
        encoding="utf-8",
    )


def _proposal(tmp_path: Path) -> tuple[dict, dict, dict]:
    state_dir = tmp_path / ".zf"
    base = tmp_path / "zf.yaml"
    candidate = tmp_path / "candidate.yaml"
    _config(base, lanes=1)
    _config(candidate, lanes=2)
    requirement = (
        state_dir
        / "workflow-requests"
        / "req-apply"
        / "requirements"
        / "revision-0001.json"
    )
    requirement.parent.mkdir(parents=True)
    requirement.write_text(
        json.dumps({
            "schema_version": "requirement-spec.v1",
            "request_id": "req-apply",
            "revision": 1,
        }),
        encoding="utf-8",
    )
    import hashlib

    request = {
        "request_id": "req-apply",
        "revision": 1,
        "status": "ready",
        "kind": "issue",
        "requirement_spec_ref": str(requirement),
        "requirement_spec_digest": hashlib.sha256(
            requirement.read_bytes()
        ).hexdigest(),
        "open_questions": [],
    }
    request_path = workflow_request_path(state_dir, "req-apply")
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request), encoding="utf-8")
    proposal, descriptor = build_workflow_proposal(
        state_dir,
        request=request,
        base_config_path=base,
        candidate_config_path=candidate,
        preflight={"status": "GO", "blockers": []},
        flow_kind="issue",
    )
    return proposal, descriptor, dict(proposal["validation_result_ref"])


def _execute(
    service: ControlledActionService,
    payload: dict,
) -> dict:
    return service._execute_action(
        action="workflow-config-apply",
        requested_action="workflow-config-apply",
        payload=payload,
        requested=ZfEvent(
            type="control.action.requested",
            actor="test",
            payload=payload,
        ),
    )


def _apply_payload(
    proposal: dict,
    proposal_ref: dict,
    validation: dict,
) -> dict:
    return {
        "proposal_id": proposal["proposal_id"],
        "proposal_ref": proposal_ref,
        "proposal_digest": proposal["proposal_digest"],
        "validation_result_ref": validation,
        "approval_ref": "owner:approved",
        "idempotency_key": "apply-req-1",
    }


def test_controlled_config_apply_is_cas_bound_and_idempotent(
    tmp_path: Path,
) -> None:
    proposal, proposal_ref, validation = _proposal(tmp_path)
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    service = ControlledActionService(
        state_dir,
        EventWriter(log),
        project_root=tmp_path,
        actor="operator",
    )
    payload = {
        "proposal_id": proposal["proposal_id"],
        "proposal_ref": proposal_ref,
        "proposal_digest": proposal["proposal_digest"],
        "validation_result_ref": validation,
        "approval_ref": "owner:approved",
        "config_ref": str(tmp_path / "zf.yaml"),
        "idempotency_key": "apply-req-1",
    }

    first = _execute(service, payload)
    replay = _execute(service, payload)

    assert first["ok"] is True
    assert replay["ok"] is True
    assert replay["replayed"] is True
    assert "lanes: 2" in (tmp_path / "zf.yaml").read_text(encoding="utf-8")
    types = [event.type for event in log.read_all()]
    assert types.count("workflow.config.change.apply.requested") == 2
    assert types.count("workflow.config.change.applied") == 2


def test_controlled_config_apply_rejects_stale_base_without_writing(
    tmp_path: Path,
) -> None:
    proposal, proposal_ref, validation = _proposal(tmp_path)
    config = tmp_path / "zf.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "\n# drift\n")
    before = config.read_text(encoding="utf-8")
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    service = ControlledActionService(
        state_dir,
        EventWriter(log),
        project_root=tmp_path,
        actor="operator",
    )

    result = _execute(service, {
        "proposal_ref": proposal_ref,
        "proposal_digest": proposal["proposal_digest"],
        "validation_result_ref": validation,
        "approval_ref": "owner:approved",
    })

    assert result["ok"] is False
    assert result["code"] == "base_config_stale"
    assert config.read_text(encoding="utf-8") == before
    assert "workflow.config.change.rejected" in [
        event.type for event in log.read_all()
    ]


def test_run_contract_hydrates_proposal_effective_config(tmp_path: Path) -> None:
    proposal, proposal_ref, _ = _proposal(tmp_path)
    contract = bind_run_contract_workflow_artifacts(
        {
            "schema_version": "run-contract.v1",
            "workflow": {},
            "config": {},
            "contract_digest": "",
        },
        proposal_ref=proposal_ref,
        proposal_digest=proposal["proposal_digest"],
        effective_config_ref=proposal["effective_config_ref"],
    )

    effective = hydrate_run_effective_config(
        tmp_path / ".zf",
        contract,
    )

    assert effective["project"]["name"] == "demo"
    assert contract["config"]["effective_snapshot_digest"] == proposal[
        "effective_config_ref"
    ]["sha256"]


def test_controlled_config_apply_rejects_superseded_proposal(
    tmp_path: Path,
) -> None:
    proposal, proposal_ref, validation = _proposal(tmp_path)
    request_path = workflow_request_path(tmp_path / ".zf", "req-apply")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["revision"] = 2
    request_path.write_text(json.dumps(request), encoding="utf-8")
    before = (tmp_path / "zf.yaml").read_bytes()
    log = EventLog(tmp_path / ".zf" / "events.jsonl")
    service = ControlledActionService(
        tmp_path / ".zf",
        EventWriter(log),
        project_root=tmp_path,
        actor="operator",
    )

    result = _execute(service, {
        "proposal_ref": proposal_ref,
        "proposal_digest": proposal["proposal_digest"],
        "validation_result_ref": validation,
        "approval_ref": "owner:approved",
    })

    assert result["ok"] is False
    assert result["code"] == "proposal_superseded"
    assert (tmp_path / "zf.yaml").read_bytes() == before


def test_controlled_config_apply_emits_failed_on_unexpected_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    proposal, proposal_ref, validation = _proposal(tmp_path)
    from zf.runtime.workflow_config_apply import WorkflowConfigApplyService

    def fail(*args, **kwargs):
        raise RuntimeError("disk service unavailable")

    monkeypatch.setattr(WorkflowConfigApplyService, "apply", fail)
    log = EventLog(tmp_path / ".zf" / "events.jsonl")
    service = ControlledActionService(
        tmp_path / ".zf",
        EventWriter(log),
        project_root=tmp_path,
        actor="operator",
    )

    result = _execute(service, {
        "proposal_ref": proposal_ref,
        "proposal_digest": proposal["proposal_digest"],
        "validation_result_ref": validation,
        "approval_ref": "owner:approved",
    })

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["code"] == "config_apply_internal_failure"
    assert "workflow.config.change.failed" in [
        event.type for event in log.read_all()
    ]


def test_controlled_config_apply_rejects_unbound_validation_result(
    tmp_path: Path,
) -> None:
    proposal, proposal_ref, _validation = _proposal(tmp_path)
    fake = tmp_path / ".zf" / "fake-validation.json"
    fake.write_text('{"status": "PASS"}\n', encoding="utf-8")
    import hashlib

    log = EventLog(tmp_path / ".zf" / "events.jsonl")
    service = ControlledActionService(
        tmp_path / ".zf",
        EventWriter(log),
        project_root=tmp_path,
        actor="operator",
    )

    result = _execute(service, {
        "proposal_ref": proposal_ref,
        "proposal_digest": proposal["proposal_digest"],
        "validation_result_ref": {
            "ref": str(fake),
            "sha256": hashlib.sha256(fake.read_bytes()).hexdigest(),
        },
        "approval_ref": "owner:approved",
    })

    assert result["ok"] is False
    assert result["code"] == "validation_result_mismatch"
    assert "lanes: 1" in (tmp_path / "zf.yaml").read_text(encoding="utf-8")


def test_config_apply_serializes_competing_base_cas(
    tmp_path: Path,
    monkeypatch,
) -> None:
    proposal, proposal_ref, validation = _proposal(tmp_path)
    from zf.runtime import workflow_config_apply as module
    from zf.runtime.workflow_config_apply import (
        WorkflowConfigApplyError,
        WorkflowConfigApplyService,
    )

    config = tmp_path / "zf.yaml"
    entered = threading.Event()
    release = threading.Event()
    original_write = module.atomic_write_text

    def slow_first_config_write(path, content):
        if Path(path) == config and not entered.is_set():
            entered.set()
            assert release.wait(timeout=5)
        return original_write(path, content)

    monkeypatch.setattr(module, "atomic_write_text", slow_first_config_write)

    def apply(key: str) -> str:
        service = WorkflowConfigApplyService(
            state_dir=tmp_path / ".zf",
            project_root=tmp_path,
        )
        try:
            service.apply({
                "proposal_ref": proposal_ref,
                "proposal_digest": proposal["proposal_digest"],
                "validation_result_ref": validation,
                "approval_ref": "owner:approved",
                "idempotency_key": key,
            })
        except WorkflowConfigApplyError as exc:
            return exc.code
        return "applied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(apply, "apply-a")
        assert entered.wait(timeout=5)
        second = pool.submit(apply, "apply-b")
        release.set()
        outcomes = {first.result(timeout=5), second.result(timeout=5)}

    assert outcomes == {"applied", "base_config_stale"}
    assert "lanes: 2" in config.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("missing_key", "code"),
    [
        ("approval_ref", "approval_required"),
        ("validation_result_ref", "validation_result_mismatch"),
    ],
)
def test_config_apply_requires_approval_and_exact_validation(
    tmp_path: Path,
    missing_key: str,
    code: str,
) -> None:
    proposal, proposal_ref, validation = _proposal(tmp_path)
    payload = _apply_payload(proposal, proposal_ref, validation)
    payload.pop(missing_key)
    before = (tmp_path / "zf.yaml").read_bytes()
    service = WorkflowConfigApplyService(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
    )

    with pytest.raises(WorkflowConfigApplyError) as raised:
        service.apply(payload)

    assert raised.value.code == code
    assert (tmp_path / "zf.yaml").read_bytes() == before


def test_config_apply_rejects_unknown_field_and_wrong_project(
    tmp_path: Path,
) -> None:
    proposal, proposal_ref, validation = _proposal(tmp_path)
    payload = _apply_payload(proposal, proposal_ref, validation)
    service = WorkflowConfigApplyService(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
    )
    before = (tmp_path / "zf.yaml").read_bytes()

    with pytest.raises(WorkflowConfigApplyError) as unknown:
        service.apply({**payload, "shell": "rm -rf ."})
    assert unknown.value.code == "apply_payload_unknown_field"

    other_root = tmp_path / "other-project"
    other_root.mkdir()
    (other_root / "zf.yaml").write_bytes(before)
    other_service = WorkflowConfigApplyService(
        state_dir=tmp_path / ".zf",
        project_root=other_root,
    )
    with pytest.raises(WorkflowConfigApplyError) as wrong_project:
        other_service.apply(payload)
    assert wrong_project.value.code == "project_identity_mismatch"
    assert (tmp_path / "zf.yaml").read_bytes() == before
    assert (other_root / "zf.yaml").read_bytes() == before


@pytest.mark.parametrize(
    "unsafe_ref",
    ["config", "candidate", "validation"],
)
def test_config_apply_rejects_symlink_inputs_without_writing(
    tmp_path: Path,
    unsafe_ref: str,
) -> None:
    proposal, proposal_ref, validation = _proposal(tmp_path)
    payload = _apply_payload(proposal, proposal_ref, validation)
    config = tmp_path / "zf.yaml"
    before = config.read_bytes()

    if unsafe_ref == "config":
        target = tmp_path / "outside-config.yaml"
        target.write_bytes(before)
        config.unlink()
        config.symlink_to(target)
        expected_code = "config_path_unsafe"
    elif unsafe_ref == "candidate":
        candidate = Path(proposal["private_config_candidate_ref"])
        target = tmp_path / "outside-candidate.yaml"
        target.write_bytes(candidate.read_bytes())
        candidate.unlink()
        candidate.symlink_to(target)
        expected_code = "candidate_path_unsafe"
    else:
        validation_path = (
            tmp_path / ".zf" / str(validation["ref"])
        )
        target = tmp_path / "outside-validation.json"
        target.write_bytes(validation_path.read_bytes())
        validation_path.unlink()
        validation_path.symlink_to(target)
        expected_code = "validation_result_unsafe"

    service = WorkflowConfigApplyService(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
    )
    with pytest.raises(WorkflowConfigApplyError) as raised:
        service.apply(payload)

    assert raised.value.code == expected_code
    if unsafe_ref == "config":
        assert target.read_bytes() == before
    else:
        assert config.read_bytes() == before


def test_config_apply_secret_scan_rejects_inline_credentials() -> None:
    from zf.runtime.workflow_config_apply import _validate_no_inline_secrets

    with pytest.raises(WorkflowConfigApplyError) as raised:
        _validate_no_inline_secrets(
            "version: '1.0'\n"
            "project: {name: demo}\n"
            "provider_token: literal-secret\n"
        )

    assert raised.value.code == "candidate_inline_secret"
    _validate_no_inline_secrets(
        "version: '1.0'\n"
        "project: {name: demo}\n"
        "token_env: OPENAI_API_KEY\n"
    )


def test_secret_scan_does_not_read_task_skill_names_as_api_keys():
    from zf.runtime.workflow_config_apply import _validate_no_inline_secrets
    _validate_no_inline_secrets('skills: [zf-plan-task-map-contract, zf-harness-task-capsule-contract]')
    for content in ('skills: [sk-abcdefghijklmnop]', 'value: Bearer sk-abcdefghijklmnop',
                    'url: https://example.test/?key=sk-abcdefghijklmnop'):
        with pytest.raises(WorkflowConfigApplyError) as error:
            _validate_no_inline_secrets(content)
        assert error.value.code == 'candidate_inline_secret'
