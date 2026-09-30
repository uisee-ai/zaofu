"""Concurrent isolation compares verified config content, not request provenance."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.run_contract import write_run_contract_snapshot
from zf.runtime.run_isolation import concurrent_isolation_blocker


def _invoke(state, run, config):
    ref = write_immutable_json_sidecar(
        state, {"schema_version": "effective-config-snapshot.v1", "request_id": run, "config": config},
        root="test-configs", kind="effective_config_snapshot", schema_version="effective-config-snapshot.v1",
        created_by="test",
    )
    contract = write_run_contract_snapshot(state, {"config": {
        "effective_snapshot_ref": ref, "effective_snapshot_digest": ref["sha256"],
    }})
    return ZfEvent(type="workflow.invoke.requested", task_id=f"TASK-{run}", payload={
        "workflow_run_id": run, "request_id": run, "scope": [f"src/{run}"],
        "effective_config_ref": ref, "effective_config_digest": ref["sha256"],
        "run_contract_ref": contract["ref"], "run_contract_digest": contract["contract_digest"],
    })


@pytest.mark.parametrize("fault", ["", "different_config", "missing_snapshot", "tampered_snapshot",
                                  "wrong_effective_digest", "wrong_effective_ref", "empty_ref",
                                  "null_ref", "wrong_schema", "non_mapping_config"])
@pytest.mark.parametrize("fault_on", ["active", "new"])
def test_concurrent_config_content_and_binding_checks(tmp_path, fault, fault_on):
    config = {"project": {"name": "shared"}, "workflow": {"run_admission": {"mode": "concurrent"}}}
    first, second = [_invoke(tmp_path, name, config) for name in ("A", "B")]
    assert first.payload["effective_config_digest"] != second.payload["effective_config_digest"]
    victim = first if fault_on == "active" else second
    body = victim.payload
    ref = body["effective_config_ref"]
    if fault == "different_config":
        different = deepcopy(config)
        different["project"]["name"] = "other"
        replacement = _invoke(tmp_path, body["workflow_run_id"], different)
        victim.payload = replacement.payload
    elif fault == "missing_snapshot":
        (tmp_path / ref["ref"]).unlink()
    elif fault == "tampered_snapshot":
        (tmp_path / ref["ref"]).write_text("{}")
    elif fault == "wrong_effective_digest":
        body["effective_config_digest"] = "0" * 64
    elif fault == "wrong_effective_ref":
        body["effective_config_ref"] = deepcopy(second.payload["effective_config_ref"] if fault_on == "active"
                                               else first.payload["effective_config_ref"])
    elif fault in {"empty_ref", "null_ref"}:
        body["effective_config_ref"] = {} if fault == "empty_ref" else None
    elif fault in {"wrong_schema", "non_mapping_config"}:
        snapshot = {"schema_version": "effective-config-snapshot.v1", "config": config}
        snapshot["schema_version" if fault == "wrong_schema" else "config"] = []
        bad_ref = write_immutable_json_sidecar(
            tmp_path, snapshot, root="test-configs", kind="effective_config_snapshot",
            schema_version="effective-config-snapshot.v1", created_by="test",
        )
        body.update(effective_config_ref=bad_ref, effective_config_digest=bad_ref["sha256"])
    runtime = SimpleNamespace(state_dir=tmp_path, config=SimpleNamespace(
        runtime=SimpleNamespace(workdirs=SimpleNamespace(enabled=True, mode="worktree")),
    ))
    reason = concurrent_isolation_blocker(
        runtime, second, active_run_ids=["A"], events=[first, second],
        run_id_for=lambda event: event.payload.get("workflow_run_id", ""),
    )
    expected = ("concurrent effective config digest mismatch" if fault == "different_config" else
                "concurrent effective config binding invalid" if fault else "")
    assert reason == expected


@pytest.mark.parametrize("drift", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_binding_and_digest_only_admission_facts_agree_or_fail_closed(tmp_path, drift, reverse):
    config = {"project": {"name": "shared"}}
    first, second = [_invoke(tmp_path, run, config) for run in ("A", "B")]
    binding = ZfEvent(type="config.run_contract.request_bound", payload={
        "workflow_run_id": "A", "effective_config_ref": first.payload["effective_config_ref"],
    })
    admitted = ZfEvent(type="run.admission.admitted", payload={
        "workflow_run_id": "A",
        "effective_config_digest": "unknown-config" if drift else first.payload["effective_config_digest"],
    })
    rows = [binding, first, admitted]
    runtime = SimpleNamespace(state_dir=tmp_path, config=SimpleNamespace(
        runtime=SimpleNamespace(workdirs=SimpleNamespace(enabled=True, mode="worktree")),
    ))
    reason = concurrent_isolation_blocker(
        runtime, second, active_run_ids=["A"], events=list(reversed(rows)) if reverse else rows,
        run_id_for=lambda event: event.payload.get("workflow_run_id", ""),
    )
    assert reason == ("concurrent isolation found config identity divergence for active Run A" if drift else "")
