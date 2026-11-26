"""Continuation Readers receive retained media before their first dispatch."""
import base64
import hashlib
import json

import pytest

from tests.test_candidate_continuation import source, _action
from tests.test_host_evidence_media import PNG
from tests.test_run_admission import _Transport
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.sidecar_refs import write_sidecar_json


@pytest.mark.parametrize('corrupt', [False, True])
@pytest.mark.parametrize('size_allowed', [False, True])
def test_initial_continuation_delivers_verified_media_without_probe(source, corrupt, size_allowed):
    seeded, service, run_id, candidate = source
    if not size_allowed:
        seeded.config.runtime.host_verification.max_artifact_bytes = 1
    target = candidate.payload['candidate_head_commit']
    evidence = write_sidecar_json(seeded.state_dir, 'artifacts/old-browser.json', {
        'image': 'bound-image', 'target_commit': target, 'files': [{
            'path': 'desktop.png', 'sha256': '0' * 64 if corrupt else hashlib.sha256(PNG).hexdigest(),
            'base64': base64.b64encode(PNG).decode(),
        }]}, kind='container_evidence', schema_version='container-evidence.v1', created_by='run-manager')
    receipt = write_sidecar_json(seeded.state_dir, 'artifacts/old-receipt.json', {
        'workflow_run_id': run_id, 'target_commit': target, 'command_digest': 'browser-command',
        'execution_origin': 'preauthorized_container', 'source_stable': True,
        'image': 'bound-image', 'evidence_ref': evidence,
    }, kind='task_host_receipt', schema_version='task-host-receipt.v1', created_by='run-manager')
    seeded.event_writer.append(ZfEvent(type='runtime.action.attempt.completed', origin='kernel',
        actor='run-manager', payload={'workflow_run_id': run_id, 'action': 'task-host-assist',
                                     'kind': 'verify', 'receipt_ref': receipt}))
    preview = _action(service, run_id, candidate, dry_run=True)
    assert preview['ok'], preview
    accepted = _action(service, run_id, candidate,
        reviewed_digest=preview['manifest']['manifest_digest'], approval_ref='owner:continue')
    assert accepted['ok'], accepted
    invoke = next(e for e in seeded.event_log.read_all() if e.id == accepted['event_id'])
    runtime = Orchestrator(seeded.state_dir, seeded.config, _Transport(), project_root=seeded.project_root)
    if corrupt or not size_allowed:
        with pytest.raises(ValueError, match='digest|limit'):
            runtime._on_workflow_invoke_requested(invoke)
    else:
        runtime._on_workflow_invoke_requested(invoke)
    events = [e for e in seeded.event_log.read_all()
              if e.payload.get('workflow_run_id') == accepted['workflow_run_id']]
    candidates = [e for e in events if e.type == 'candidate.ready']
    if corrupt or not size_allowed:
        assert not candidates
        return
    indices = list((seeded.state_dir / 'artifacts/container-media').glob('*/index.json'))
    assert len(indices) == 1, 'First Reader must receive image paths without a failed round first'
    index = json.loads(indices[0].read_text())
    assert (seeded.state_dir / index['files'][0]['ref']).read_bytes() == PNG
    assert str(indices[0]) in candidates[0].payload['operator_feedback'][0]
    assert not any(e.type in {'test.passed', 'runtime.action.attempt.started'} for e in events)


def test_continuation_retains_latest_host_observation_per_obligation(source):
    runtime, service, run_id, candidate = source
    for idx, (command_id, tier) in enumerate([('one', 'runtime'), ('two', 'e2e'), ('one', 'runtime')]):
        receipt = write_sidecar_json(runtime.state_dir, f"artifacts/occurrence-{idx}.json", {
            "workflow_run_id": run_id, "target_commit": candidate.payload["candidate_head_commit"],
            "command_digest": "shared-argv", "command_id": command_id,
            "manifest": {"command": {"command_id": command_id, "tier": tier}},
            "exit_code": 1 if idx == 2 else 0}, kind="task_host_receipt",
            schema_version="task-host-receipt.v1", created_by="run-manager")
        runtime.event_writer.append(ZfEvent(type="runtime.action.attempt.completed", origin="kernel",
            id=f"occurrence-{idx}", payload={"workflow_run_id": run_id, "action": "task-host-assist",
                                             "kind": "verify", "receipt_ref": receipt}))
    preview = _action(service, run_id, candidate, dry_run=True)
    assert preview["ok"], preview
    assert {r["source_event_id"] for r in preview["manifest"]["execution_evidence"]} == {
        "occurrence-1", "occurrence-2"}
