import os
from types import SimpleNamespace

import httpx2
import pytest

from tests.test_controller_managed import managed as managed, workspace as workspace
from zf.integrations.controller.binding import ManagedDenied
from zf.integrations.controller.execution import process_identity
from zf.integrations.controller.ledger import Ledger
from zf.integrations.controller.project_runtime import managed_runtime


@pytest.fixture
def stale_project(managed, monkeypatch):
    config, peer, context, credentials = managed
    monkeypatch.chdir(context.project_root)
    key = "project:" + context.config.observability.controller.project_id
    ledger = Ledger(config.state_dir)
    # Same PID/start ticks may occur in two different Pod PID namespaces.
    ledger.insert(
        key,
        "project",
        "starting",
        {
            "project_id": key[8:],
            "lease": {"id": "old-project"},
            "owner": process_identity(os.getpid()),
            "pid": os.getpid(),
        },
    )
    proof = {
        "protocol": "zfc.control.v1",
        "workspace_id": config.workspace_id,
        "deployment_id": credentials["deployment_id"],
        "generation": credentials["generation"],
        "project_id": key[8:],
        "lease_id": "old-project",
        "status": "released",
        "lease_deployment_id": "fenced-old-deployment",
        "physical_fence": True,
    }
    old = peer.handle

    def handle(request):
        if request.method == "GET" and request.url.path.endswith(
            "/execution-leases/old-project"
        ):
            assert request.url.params["project_id"] == key[8:]
            return httpx2.Response(200, json=proof, request=request)
        return old(request)

    monkeypatch.setattr(
        "zf.integrations.controller.workspace_transport.WorkspaceTransport.request",
        _delegate_requests(handle),
    )
    return ledger, key, proof


def _delegate_requests(handle):
    from zf.integrations.controller.workspace_transport import WorkspaceTransport

    original = WorkspaceTransport.request

    def request(self, method, path, body=None):
        if method == "GET" and "/execution-leases/old-project?" in path:
            response = handle(
                httpx2.Request(method, "https://controller.example.test" + path)
            )
            response.raise_for_status()
            return response.json()
        return original(self, method, path, body)

    return request


def test_real_runtime_admission_retires_fenced_old_slot_and_acquires_fresh_lease(
    managed, stale_project
):
    _, peer, _, _ = managed
    ledger, key, _ = stale_project
    seen = []

    @managed_runtime
    def runtime(args):
        seen.append(ledger.get(key)["body"]["lease"]["id"])
        return 0

    assert runtime(SimpleNamespace()) == 0
    assert seen == ["lease-0"] and len(peer.leases) == 1
    assert ledger.get(key)["status"] == "unloaded"


@pytest.mark.parametrize(
    "change", ["held", "generation", "project", "deployment", "lease", "no_fence"]
)
def test_runtime_stays_closed_on_unbound_or_nonterminal_proof(
    managed, stale_project, change
):
    _, peer, _, _ = managed
    ledger, key, proof = stale_project
    if change == "held":
        proof["status"] = "held"
    elif change == "generation":
        proof["generation"] += 1
    elif change == "project":
        proof["project_id"] = "foreign"
    elif change == "deployment":
        proof["deployment_id"] = "foreign"
    elif change == "lease":
        proof["lease_id"] = "foreign"
    else:
        proof["physical_fence"] = False

    @managed_runtime
    def runtime(args):
        pytest.fail("unproven runtime was started")

    with pytest.raises(ManagedDenied, match="unresolved"):
        runtime(SimpleNamespace())
    assert not peer.leases and ledger.get(key)["status"] == "starting"


def test_remote_proof_cannot_overwrite_newer_local_project_lease(
    managed, stale_project, monkeypatch
):
    from zf.integrations.controller.project_lease import retire_released

    config, _, context, credentials = managed
    ledger, key, proof = stale_project
    newer = {"lease": {"id": "newer-lease"}}

    def query(*args):
        ledger.put(key, "starting", newer)
        return proof

    assert not retire_released(
        ledger,
        key,
        SimpleNamespace(request=query),
        {"version": 2},
        credentials,
        context.state_dir,
    )
    assert ledger.get(key)["body"] == newer


def test_controller_project_load_uses_same_recovery_before_spawning(managed, stale_project, monkeypatch):
    import json
    import time
    from zf.core.events import EventWriter
    from zf.core.events.factory import event_log_from_project
    from zf.integrations.controller.workspace import synchronize_workspace
    from zf.runtime import controller_actions

    config, peer, context, _ = managed
    ledger, key, _ = stale_project
    writer = EventWriter(event_log_from_project(context.state_dir, config=context.config))
    guard = context.state_dir / 'processes/watcher.pid.json'
    spawned = []
    def start(command, **kwargs):
        spawned.append(kwargs['env']['ZF_CONTROLLER_PROJECT_LEASE'])
        guard.parent.mkdir(parents=True, exist_ok=True)
        guard.write_text(json.dumps({'owner_pid': os.getpid()}))
        writer.emit('loop.started', actor='fixture', payload={})
        return SimpleNamespace(pid=os.getpid(), poll=lambda: None)
    monkeypatch.setattr(controller_actions.subprocess, 'Popen', start)
    peer.commands = [{'id': 'recover-project', 'workspace_id': config.workspace_id,
        'deployment_id': peer.deployment_id, 'generation': peer.generation, 'policy_version': 2,
        'expires_at': int(time.time()) + 60, 'user_id': 'owner', 'session_id': 'login',
        'payload': {'action': 'project.load', 'project_id': key[8:]}}]
    try:
        synchronize_workspace(config)
        synchronize_workspace(config)
        assert spawned == ['lease-0']
        assert ledger.get('recover-project')['body']['result']['effect'] == 'watcher_started'
        assert ledger.get(key)['status'] == 'loaded'
    finally:
        writer.event_log.close()
