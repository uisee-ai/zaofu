"""The Verify decision can omit unselectable roles while sharing all original policy."""
import json

import pytest

from tests.test_oa_verify_predecessor_release import setup_run
from tests.test_orchestrator_agent_required_delivery import _read_command
from zf.cli.main import main
from zf.core.config.schema import RoleConfig
from zf.runtime.oa_verify_role_config import pin_verify_role_config
from zf.runtime.orchestrator_agent_operations import prepared_operation_from_checkpoint_event
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


def _prepare(tmp_path, monkeypatch, permission="default"):
    runtime, task, _, _, _, sweep, _, _ = setup_run(tmp_path, monkeypatch)
    runtime.task_store.update(task.id, active_dispatch_id="")
    fee_reader = RoleConfig(name="fee-reader", instance_id="fee-reader", role_kind="reader", backend="mock",
                            skills=["fee-audit"], allowed_tools=["Read"], permission_mode=permission)
    runtime.config.roles.append(fee_reader)
    policy = runtime.config.workflow.flow_metadata["task_pipeline"]
    policy["pools"]["verify"]["role_instances"].append("fee-reader")
    event, = sweep()
    operation = prepared_operation_from_checkpoint_event(runtime, event)
    context = operation.context
    sources = {s["source_id"]: s for s in context.source_manifest["sources"]}
    return runtime, operation, sources


def test_signed_config_keeps_every_shared_setting_and_exact_reader_permissions(tmp_path, monkeypatch, capsys):
    runtime, operation, sources = _prepare(tmp_path, monkeypatch)
    context = operation.context
    actual = hydrate_sidecar_ref(runtime.state_dir, sources["verify-role-config"]).payload
    full = hydrate_sidecar_ref(runtime.state_dir, sources["effective-config"]).payload
    assert actual["effective_config_ref"] == {k: context.effective_config_ref[k] for k in ("ref", "sha256")}
    seats = context.input_body["checkpoint_context"]["verify_assignment_seats"]
    candidates = [r for r in full["config"]["roles"] if r["instance_id"] in seats]
    assert actual["eligible_reader_seats"] == sorted(seats) == ["fee-reader", "reader"]
    assert actual["config"] == {**full["config"], "roles": candidates}
    fee = next(r for r in candidates if r["instance_id"] == "fee-reader")
    assert (fee["allowed_tools"], fee["skills"], fee["permission_mode"]) == (["Read"], ["fee-audit"], "default")
    required = {s["source_id"] for s in context.read_policy["required_reads"]}
    assert {"verify-role-config", "checkpoint-input", "current-task-contract", "execution-context"} == required
    _read_command(runtime, operation, monkeypatch)
    full_source = sources["effective-config"]
    content = []
    offset = 0
    while True:
        assert main(["artifact", "read", "--attempt", operation.attempt_id, "--state-dir", str(runtime.state_dir),
                     "--source", full_source["source_id"], "--artifact", full_source["artifact_id"],
                     "--page-start", str(offset)]) == 0
        page = json.loads(capsys.readouterr().out)
        assert page["page_start"] == offset < page["page_end"]
        content.append(page["content"])
        offset = page["page_end"]
        if offset == page["page_total"]:
            break
    assert json.loads("".join(content)) == full


def test_changed_role_permissions_change_the_frozen_decision_input(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    with monkeypatch.context() as first_patch:
        old_runtime, _, old = _prepare(first, first_patch, permission="default")
    new_runtime, _, new = _prepare(second, monkeypatch, permission="plan")
    for key in ("effective-config", "verify-role-config"):
        assert old[key]["sha256"] != new[key]["sha256"]
    cfg = hydrate_sidecar_ref(new_runtime.state_dir, new["verify-role-config"]).payload["config"]
    assert next(role["permission_mode"] for role in cfg["roles"] if role["instance_id"] == "fee-reader") == "plan"
    with pytest.raises(SidecarRefError):
        hydrate_sidecar_ref(old_runtime.state_dir, {**old["verify-role-config"], "sha256": new["verify-role-config"]["sha256"]})


@pytest.mark.parametrize("override", [
    {"verify_specialist_patterns": {"fee-reader": ["audit"]}},
    {"verify_assignment_seats": ["fee-reader"]}, {"verify_assignment_seats": ["reader", "reader"]},
    {"verify_assignment_revision": "other-revision"}, {"task_map_generation": "superseded-map"},
    {"verify_assignment_task": "other"}, {"effective_config_ref": {"ref": "explicit"}},
    {"verify_assignment_contract_ref": {}},
])
def test_unproved_eligibility_and_specialist_work_keep_full_config(tmp_path, monkeypatch, override):
    runtime, operation, sources = _prepare(tmp_path, monkeypatch)
    ctx = operation.context
    payload = {
        **ctx.input_body["checkpoint_context"], "task_map_generation": ctx.input_body["identity"]["task_map_generation"],
        "verify_assignment_contract_ref": sources["current-task-contract"], **override,
    }
    assert pin_verify_role_config(runtime, operation.workflow_run_id, payload, ctx.effective_config_ref) == {}


def test_resuming_a_signed_full_read_never_replaces_its_frozen_input(tmp_path, monkeypatch):
    runtime, task, _, _, _, sweep, _, _ = setup_run(tmp_path, monkeypatch)
    runtime.task_store.update(task.id, active_dispatch_id="")
    with monkeypatch.context() as legacy:
        legacy.setattr("zf.runtime.orchestrator_agent_context.pin_verify_role_config", lambda *args: {})
        original, = sweep()
    old = prepared_operation_from_checkpoint_event(runtime, original)
    assert "effective-config" in {r["source_id"] for r in old.context.read_policy["required_reads"]}
    replay, = sweep()
    current = prepared_operation_from_checkpoint_event(runtime, replay)
    assert original.id == replay.id
    assert current.request_hash == old.request_hash
    assert current.context == old.context


def test_registered_experts_keep_full_config_mandatory_in_the_actual_checkpoint(tmp_path, monkeypatch):
    from zf.runtime import task_verify_specialists

    monkeypatch.setattr(task_verify_specialists, "specialist_catalog", lambda config: {"fee-reader": ["audit"]})
    _, operation, sources = _prepare(tmp_path, monkeypatch)
    assert "verify-role-config" not in sources
    assert "effective-config" in {s["source_id"] for s in operation.context.read_policy["required_reads"]}
