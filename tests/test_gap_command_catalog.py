"""Gap adapters retain the command contract already accepted by Task Map."""
from copy import deepcopy
import json

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.module_gap_plan import build_gap_task_map_amend
from zf.runtime.task_map import validate_task_map_payload
from zf.runtime.verification_commands import task_contract_verification_commands


def _gap(field="verify_commands"):
    commands = [{"id": "CHECK", "command": "python -m pytest tests/test_files.py",
                 "acceptance_ids": ["AC"], "owner": "task_verify", "tier": "runtime",
                 "deterministic": True, "reusable": True, "timeout_seconds": 180,
                 "rolling_smoke": True}]
    return {"task_id": "GAP", "owner_role": "dev", "claim_paths": ["src/files/**", "tests/**"],
            "acceptance_criteria": [{"id": "AC", "text": "files behave correctly",
                                     "verification_command_ids": ["CHECK"]}],
            "source_refs": ["event:failure"], field: commands}


@pytest.mark.parametrize("field", ["verify_commands", "verification"])
def test_gap_preserves_structured_command_aliases(field):
    gap = _gap(field)
    original = deepcopy(gap)
    amended = build_gap_task_map_amend({"tasks": []}, gap_tasks=[gap], supersedes_task_map_ref="base.json")
    assert validate_task_map_payload(amended, require_task_verification=True).passed
    task = amended["tasks"][0]
    assert task["validation"]["commands"] == original[field]
    assert task["verification"] == [original[field][0]["command"]]
    assert gap == original
    task["validation"]["commands"][0]["acceptance_ids"].append("OTHER")
    assert gap == original


def test_gap_rejects_conflicting_structured_aliases():
    gap = _gap()
    gap["validation"] = {"commands": [{**gap["verify_commands"][0], "id": "OTHER"}]}
    with pytest.raises(ValueError, match="conflicting structured verification commands"):
        build_gap_task_map_amend({"tasks": []}, gap_tasks=[gap], supersedes_task_map_ref="base.json")


def test_unknown_command_reference_is_not_invented():
    gap = _gap()
    gap["acceptance_criteria"][0]["verification_command_ids"] = ["MISSING"]
    amended = build_gap_task_map_amend({"tasks": []}, gap_tasks=[gap], supersedes_task_map_ref="base.json")
    result = validate_task_map_payload(amended, require_task_verification=True)
    assert not result.passed and any("MISSING" in e for e in result.errors)


@pytest.mark.parametrize("local_checklist", [False, True])
def test_real_gap_ready_entry_preserves_commands_in_dispatched_contract(tmp_path, local_checklist):
    from tests.test_module_gap_plan_runtime import _state, _write_base_task_map
    state, log, transport, runtime = _state(tmp_path)
    ref = _write_base_task_map(state)
    gap = _gap()
    if local_checklist:
        gap["validation"] = {"commands": gap.pop("verify_commands")}
        gap["verification"] = {"commands": [gap["validation"]["commands"][0]["command"]]}
    event = ZfEvent(id="gap-ready", type="flow.gap_plan.ready", actor="zf-cli",
                    correlation_id="trace-gap", payload={
        "schema_version": "goal-gap-plan.v1", "pdd_id": "CANGJIE", "feature_id": "CANGJIE",
        "goal_id": "CANGJIE", "goal_kind": "prd", "gap_category": "product_completeness",
        "trace_id": "trace-gap", "task_map_ref": ref, "gap_tasks": [gap],
    })
    runtime.run_once(events=[event])
    runtime.run_once(events=[event])
    events = log.read_all()
    assert not any(e.type == "task_map.amend.failed" for e in events)
    assert len([e for e in events if e.type == "task_map.amended"]) == 1
    task = TaskStore(state / "kanban.json").get("GAP")
    assert task and task.status == "in_progress"
    commands = task_contract_verification_commands(task.contract)
    for key, value in _gap()["verify_commands"][0].items():
        assert commands[0][key] == value
    ready = next(e for e in events if e.type == "task_map.ready")
    amended = json.loads((state / ready.payload["task_map_ref"]).read_text())
    assert validate_task_map_payload(amended, require_task_verification=True).passed
    assert len(transport.sent) == 1


def test_structured_catalog_and_local_string_checklist_are_complementary():
    gap = _gap()
    gap["validation"] = {"commands": gap.pop("verify_commands")}
    gap["verification"] = {"commands": [gap["validation"]["commands"][0]["command"]],
                           "scope": "isolated_branch"}
    original = deepcopy(gap)
    amended = build_gap_task_map_amend({"tasks": []}, gap_tasks=[gap], supersedes_task_map_ref="base.json")
    assert validate_task_map_payload(amended, require_task_verification=True).passed
    assert amended["tasks"][0]["validation"] == original["validation"]
    assert amended["tasks"][0]["verification"] == original["verification"]["commands"]
    assert gap == original
