"""Revalidation consumes frozen queue references, not an agent-supplied plan."""
from unittest.mock import patch

import pytest

from tests.test_run_manager import _config, _state
from tests.test_source_repair_apply import _git, _repair_repositories
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.repair_dispatch import RepairRequest, write_repair_contract
from zf.runtime.run_manager import _execute_repair_closeout_validate, build_repair_merge_queue


def _case(tmp_path, failure_case=""):
    root, worktree, base, commit = _repair_repositories(tmp_path)
    state, log, writer = _state(tmp_path)
    writer.default_origin = "kernel"
    config = _config()
    config.runtime.run_manager.source_repair.enabled = True
    config.runtime.run_manager.source_repair.allow_paths = ["src/zf/**"]
    request = RepairRequest(fingerprint="repair-queue", attempt=0, candidate_id="candidate", candidate_path="",
        repair_task_payload={"contract": {"scope": ["src/zf/**"], "verification_commands": ["git diff --check"]}})
    contract, ref = write_repair_contract(state, request, base_commit=base, created_by="test")
    row = {"queue_id": "repair-queue", "fingerprint": "repair-queue", "candidate_id": "candidate",
        "worktree_path": str(worktree), "base_commit": base, "source_commit": commit,
        "changed_files": ["src/zf/fix.py"], "repair_contract_required": True,
        "repair_contract_ref": ref, "repair_contract_digest": ref["sha256"],
        "verification_plan": [{"command": command} for command in contract["verification_commands"]]
            + [{"command": f"python scripts/dev-verify.py {mode} --base {base}"} for mode in ["plan", "run"]],
    }
    writer.emit("autoresearch.repair.closeout.required", payload=row)
    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager_repair_validation.execute_repair_verification_plan",
        return_value={"ok": False, "status": "failed", "reason": "command_not_allowlisted"},
    ):
        _execute_repair_closeout_validate(state_dir=state, writer=writer,
            action={**row, "action": "repair-closeout-validate", "checkpoint_id": "old-validation",
                    "recovery_case_id": failure_case},
            causation_id="original-closeout")
    failed = next(e for e in log.read_all() if e.type == "run.manager.action.failed")
    payload = {"queue_id": row["queue_id"], "previous_validation_event_id": failed.id,
               "repair_contract_digest": row["repair_contract_digest"]}
    service = ControlledActionService(state, writer, config=config, project_root=tmp_path,
                                      actor="operator", source="operator", surface="cli")
    return root, worktree, log, writer, config, row, failed, payload, service


def _invoke(service, writer, payload):
    requested = writer.emit("operator.intent.created", actor="operator", payload={"action": "repair-closeout-validate"})
    return service.execute(action="repair-closeout-validate", requested_action="repair-closeout-validate",
                           payload=payload, requested=requested)


def test_controlled_revalidation_uses_frozen_plan_and_preserves_old_failure(tmp_path):
    root, worktree, log, writer, _, row, failed, payload, service = _case(tmp_path)
    payload.update(worktree_path="/untrusted", verification_plan=[{"command": "touch injected"}],
                   repair_contract_required=False)
    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager_repair_validation.execute_repair_verification_plan",
        return_value={"ok": True, "status": "passed", "results": []},
    ) as commands:
        result = _invoke(service, writer, payload)
    assert result["ok"], result
    commands.assert_called_once_with(worktree=str(worktree), verification_plan=row["verification_plan"])
    queue = build_repair_merge_queue(log.read_all())["items"][0]
    assert queue["status"] == "closeout_required"
    assert queue["validation"]["status"] == "passed"
    assert any(e.id == failed.id for e in log.read_all())
    new = next(e for e in reversed(log.read_all()) if e.type == "run.manager.action.applied")
    assert new.payload["previous_validation_event_id"] == failed.id
    assert new.payload["validator_digest"]
    assert new.payload["repair_contract_required"] is True


@pytest.mark.parametrize("fault", ["stale_failure", "digest", "disabled", "scope", "paused", "human", "base_drift"])
def test_revalidation_rejects_changed_authority_or_identity(tmp_path, fault):
    root, _, log, writer, config, _, _, payload, service = _case(tmp_path)
    if fault == "stale_failure": payload["previous_validation_event_id"] = "foreign"
    elif fault == "digest": payload["repair_contract_digest"] = "foreign"
    elif fault == "disabled": config.runtime.run_manager.source_repair.enabled = False
    elif fault == "scope": config.runtime.run_manager.source_repair.allow_paths = ["tests/**"]
    elif fault == "paused": writer.emit("dispatch.paused", actor="operator")
    elif fault == "human": writer.emit("human.escalate", payload={"decision_token": "owner", "reason": "owner input"})
    else:
        (root / "other.txt").write_text("new source baseline\n")
        _git(root, "add", "other.txt"); _git(root, "commit", "-qm", "fix: new source baseline")
    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager_repair_validation.execute_repair_verification_plan",
    ) as commands:
        result = _invoke(service, writer, payload)
    assert not result["ok"]
    commands.assert_not_called()
    assert build_repair_merge_queue(log.read_all())["items"][0]["validation"]["status"] == "failed"


def test_new_failure_id_does_not_reset_same_validator_revalidation_allowance(tmp_path):
    root, _, log, writer, _, _, _, payload, service = _case(tmp_path)
    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager_repair_validation.execute_repair_verification_plan",
        return_value={"ok": False, "status": "failed", "reason": "command_failed"},
    ) as commands:
        assert not _invoke(service, writer, payload)["ok"]
        failed = next(e for e in reversed(log.read_all()) if e.type == "run.manager.action.failed")
        payload["previous_validation_event_id"] = failed.id
        assert not _invoke(service, writer, payload)["ok"]
    assert commands.call_count == 1


def test_bound_resident_recommendation_rebuilds_and_executes_same_verification(tmp_path):
    from zf.runtime.run_manager import run_manager_tick

    root, _, log, writer, config, row, _, payload, service = _case(tmp_path)
    writer.emit("run.manager.resident.prompted", payload={"request_id": "diagnose-1", "instance_id": "run-manager"})
    writer.emit("run.manager.action.effect.pending", payload={
        "request_id": "diagnose-1", "action": "resident-agent-reprompt",
        "continuation_expected_events": ["run.manager.agent.recommendation"],
    })
    advice = writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={
        **payload, "request_id": "diagnose-1", "recommended_route": "controlled_action",
        "action": "repair-closeout-validate", "verification_plan": [{"command": "touch injected"}],
    })
    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager_repair_validation.execute_repair_verification_plan",
        return_value={"ok": True, "status": "passed", "results": []},
    ) as commands:
        for _ in range(2):
            run_manager_tick(state_dir=service.state_dir, writer=writer, config=config,
                             project_root=tmp_path, spawn_repairs=False)
    assert commands.call_count == 1
    assert commands.call_args.kwargs["verification_plan"] == row["verification_plan"]
    assert any(e.type == "run.manager.agent.recommendation.consumed" and e.payload.get("source_event_id") == advice.id
               for e in log.read_all())
    assert build_repair_merge_queue(log.read_all())["items"][0]["validation"]["status"] == "passed"


def test_revalidation_preserves_bound_diagnosis_case_gate(tmp_path):
    from zf.runtime.run_manager import run_manager_tick

    root, _, log, writer, config, _, _, payload, service = _case(tmp_path)
    writer.emit("run.manager.resident.prompted", payload={"request_id": "diagnose-2", "instance_id": "run-manager"})
    writer.emit("run.manager.action.effect.pending", payload={
        "request_id": "diagnose-2", "recovery_case_id": "case-diagnosis",
        "action": "resident-agent-reprompt",
        "continuation_expected_events": ["run.manager.agent.recommendation"],
    })
    writer.emit("run.manager.action.blocked", payload={"recovery_case_id": "case-diagnosis"})
    from zf.runtime.recovery_case import recovery_action_admission
    assert recovery_action_admission(log.read_all(), {"recovery_case_id": "case-diagnosis"})["reason"] == "case_blocked"
    writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={
        **payload, "request_id": "diagnose-2", "recommended_route": "controlled_action",
        "action": "repair-closeout-validate",
    })
    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager_repair_validation.execute_repair_verification_plan",
        return_value={"ok": True, "status": "passed", "results": []},
    ) as commands:
        run_manager_tick(state_dir=service.state_dir, writer=writer, config=config,
                         project_root=tmp_path, spawn_repairs=False)
    commands.assert_not_called()
    assert build_repair_merge_queue(log.read_all())["items"][0]["validation"]["status"] == "failed"
    assert any(e.payload.get("reason") == "recovery_case:case_blocked" for e in log.read_all())


@pytest.mark.parametrize("requested", [False, True])
def test_unbound_case_cannot_replace_original_failure_case(tmp_path, requested):
    from zf.runtime.run_manager import run_manager_tick

    root, _, log, writer, config, _, _, payload, service = _case(tmp_path, failure_case="case-original")
    writer.emit("run.manager.action.blocked", payload={"recovery_case_id": "case-original"})
    if requested:
        writer.emit("run.manager.resident.prompted", payload={"request_id": "diagnose-3", "instance_id": "run-manager"})
        writer.emit("run.manager.action.effect.pending", payload={
            "request_id": "diagnose-3", "action": "resident-agent-reprompt",
            "continuation_expected_events": ["run.manager.agent.recommendation"],
        })
        payload["request_id"] = "diagnose-3"
    writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={
        **payload, "recovery_case_id": "case-bypass", "recommended_route": "controlled_action",
        "action": "repair-closeout-validate",
    })
    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager_repair_validation.execute_repair_verification_plan",
        return_value={"ok": True, "status": "passed", "results": []},
    ) as commands:
        run_manager_tick(state_dir=service.state_dir, writer=writer, config=config,
                         project_root=tmp_path, spawn_repairs=False)
    commands.assert_not_called()
    assert build_repair_merge_queue(log.read_all())["items"][0]["validation"]["status"] == "failed"


@pytest.mark.parametrize("mutation", ["source_commit", "repair_contract_digest", "previous_validation_event_id",
                                      "validator_digest", "missing_tested", "worker", "merged", "discarded"])
def test_unbound_success_does_not_reopen_queue(tmp_path, mutation):
    from copy import deepcopy
    from zf.core.events import ZfEvent

    root, _, log, writer, _, _, _, payload, service = _case(tmp_path)
    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager_repair_validation.execute_repair_verification_plan",
        return_value={"ok": True, "status": "passed", "results": []},
    ):
        assert _invoke(service, writer, payload)["ok"]
    events = log.read_all()
    index = next(i for i, e in enumerate(events) if e.type == "run.manager.action.applied")
    body = deepcopy(events[index].payload)
    prefix = events[:index]
    origin = "kernel"
    if mutation in {"merged", "discarded"}:
        prefix.append(ZfEvent(type="run.manager.repair.merge." + mutation, origin="kernel",
                             payload={"queue_id": "repair-queue"}))
    elif mutation == "worker": origin = "worker"
    elif mutation == "missing_tested": body["validation_result"].pop("tested_candidate")
    else: body[mutation] = "foreign"
    prefix.append(ZfEvent(type="run.manager.action.applied", origin=origin, payload=body))
    queue = build_repair_merge_queue(prefix)["items"][0]
    assert queue["status"] == (mutation if mutation in {"merged", "discarded"} else "needs_review")
    assert queue["validation"]["status"] == "failed"
