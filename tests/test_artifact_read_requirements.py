"""Task contract obligations must survive canonical required-read selection."""

import json

import pytest

from tests.test_artifact_read_capability import _grant
from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.artifact_query.handoff import CanonicalHandoffResolver
from zf.runtime.artifact_read_ledger import (
    canonical_required_reads,
    read_attempt_artifact,
    required_read_satisfied,
)
from zf.runtime.artifact_read_requirements import required_reads_for_attempt
from zf.runtime.task_contract_snapshot import (
    build_task_contract_snapshot,
    current_task_contract_identity,
    write_task_contract_snapshot,
)


@pytest.mark.parametrize("profile", ["implementation", "task-verify", "candidate-verify"])
def test_task_contract_obligations_cannot_be_satisfied_by_ac_and_commands_only(
    tmp_path, monkeypatch, capsys, profile,
):
    state = tmp_path / "custom-state"
    state.mkdir()
    task = Task(id="T1", title="Deliver evidence", status="in_progress", contract=TaskContract(
        acceptance_criteria=["AC-1"], verification="pytest focused",
        scope=["feature/", "evidence/"],
        evidence_contract={"task_map_generation": "G1",
                           "required": ["result.json", "state-before-after.json"],
                           "base_path": "evidence/", "pass_rule": "完整证据" * 700},
    ))
    TaskStore(state / "kanban.json").add(task)
    snapshot = build_task_contract_snapshot(
        task, workflow_run_id="run", task_map_generation_id="G1",
        base_commit="base", task_ref="task/T1",
    )
    descriptor = write_task_contract_snapshot(state, snapshot)
    # Use the real dispatch resolver; the other profiles share this contract source.
    manifest, _ = CanonicalHandoffResolver(
        state_dir=state, project_root=tmp_path, config=None,
    ).resolve_payload(
        payload={**current_task_contract_identity(task), "output_profile_id": "implementation",
                 "base_commit": "base", "task_ref": "task/T1", "task_map_generation": "G1",
                 "contract_snapshot_ref": descriptor["ref"],
                 "contract_snapshot_digest": descriptor["sha256"]},
        workflow_run_id="run", task_id="T1", attempt_id="attempt", dispatch_id="attempt",
    )
    requirements = canonical_required_reads(manifest, output_profile_id=profile)
    contract = next(s for s in manifest["sources"] if s["source_id"] == "contract")
    old_reads = [read_attempt_artifact(
        state, manifest=manifest, source_id="contract", artifact_id=contract["artifact_id"],
        json_path=path,
    )["read"] for path in ("$.acceptance_criteria", "$.verification_commands")]
    assert not all(required_read_satisfied(old_reads, r) for r in requirements)

    # New declarations must not rewrite an already pinned attempt's policy.
    old_requirements = [{**requirements[0], "json_path": path} for path in (
        "$.acceptance_criteria", "$.verification_commands",
    )]
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(type="task.dispatched", actor="orchestrator", payload={
        "attempt_id": "old-attempt", "required_reads": old_requirements,
    }))
    assert required_reads_for_attempt(log.read_all(), "old-attempt") == old_requirements
    assert all(required_read_satisfied(old_reads, r) for r in old_requirements)
    _grant(state, monkeypatch, manifest=manifest)
    writer.append(ZfEvent(type="task.dispatched", actor="orchestrator", payload={
        "attempt_id": "attempt", "required_reads": requirements,
    }))
    cursor, content, pages = "", "", 0
    ledger_path = state / "artifacts/attempts/attempt/read-ledger.active.jsonl"
    while True:
        assert main(["artifact", "read", "--attempt", "attempt", "--required",
                     "--cursor", cursor, "--page-bytes", "2048", "--state-dir", str(state)]) == 0
        page = json.loads(capsys.readouterr().out)
        content += "".join(row["content"] for row in page["items"])
        cursor = page["next_cursor"]
        pages += 1
        ledger = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        if not cursor:
            break
        assert not all(required_read_satisfied(ledger, r) for r in requirements)
    assert pages > 1
    assert json.loads(content)["evidence_contract"] == snapshot["evidence_contract"]
    assert json.loads(content)["allowed_paths"] == snapshot["allowed_paths"]
    assert all(required_read_satisfied(ledger, r) for r in requirements)
