"""Completion gates consume each Run's pinned contract, never another submit's."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.goal_completion_gate import _evaluate_active_claims
from zf.runtime.run_contract import write_run_contract, write_run_contract_snapshot


@pytest.mark.parametrize("bad_binding", ["", "missing", "digest", "tampered", "ref_only", "digest_only"])
def test_completion_resolves_contract_per_claim_and_isolates_invalid_binding(tmp_path, monkeypatch, bad_binding):
    state = tmp_path / "custom-state"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    runtime = SimpleNamespace(state_dir=state, event_writer=writer, config=SimpleNamespace(workflow=None))
    contracts = [{
        "workflow": {"completion_profile": "artifact_delivery", "proposal_digest": name},
        "protocols": {"workflow_operation": {"required_operation_ids": [f"op-{name}"]},
                      "goal_closure": {"delivery_policy": policy}},
    } for name, policy in (("A", "report_only"), ("B", "ship_candidate"))]
    claims = []
    for name, contract in zip(("A", "B"), contracts, strict=True):
        descriptor = write_run_contract_snapshot(state, contract)
        claims.append(ZfEvent(type="run.goal.completion.claimed", payload={
            "run_id": name, "workflow_run_id": name, "claim_id": f"claim-{name}",
            "run_contract_ref": descriptor["ref"], "run_contract_digest": descriptor["contract_digest"],
        }))
    # A later request rotates this compatibility pointer before A completes.
    write_run_contract(state, contracts[1])
    if bad_binding == "missing":
        (state / claims[0].payload["run_contract_ref"]).unlink()
    elif bad_binding == "digest":
        claims[0].payload["run_contract_digest"] = "0" * 64
    elif bad_binding == "tampered":
        (state / claims[0].payload["run_contract_ref"]).write_text("{}")
    elif bad_binding == "ref_only":
        claims[0].payload.pop("run_contract_digest")
    elif bad_binding == "digest_only":
        claims[0].payload.pop("run_contract_ref")
    calls = []

    def gate(events, **kwargs):
        calls.append(deepcopy(kwargs))
        return None

    monkeypatch.setattr("zf.runtime.run_manager.run_goal_completion_gate_event", gate)
    _evaluate_active_claims(runtime, claims)
    expected = contracts[1:] if bad_binding else contracts
    assert [call["run_contract"] for call in calls] == expected
    assert [call["required_operation_ids"] for call in calls] == [
        contract["protocols"]["workflow_operation"]["required_operation_ids"] for contract in expected
    ]
    assert [call["delivery_policy"] for call in calls] == [
        contract["protocols"]["goal_closure"]["delivery_policy"] for contract in expected
    ]
    assert log.read_all() == []
