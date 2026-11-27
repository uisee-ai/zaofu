"""Discover and query an opted-in board using only the worker's actual attempt."""

import json
import shlex
from types import SimpleNamespace

from zf.cli import artifact_board
from zf.cli.main import build_parser
from zf.core.events import EventLog
from zf.runtime.artifact_read_capability import (
    bind_attempt_artifact_read_capability,
    provision_role_artifact_read_credential,
)
from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, write_attempt_source_manifest
from zf.runtime.mailbox_briefing import run_board_hint

from tests.test_workflow_board import seed


def test_worker_can_discover_roster_only_with_current_authority(tmp_path, monkeypatch, capsys):
    board = dict(enabled=True, generation="g", members=["reader", "peer"], groups={"read": ["reader"]})
    workflow = SimpleNamespace(
        flow_metadata_by_kind={"prd": {"run_board_policy": {"enabled": True}}, "issue": {}},
        flow_metadata={"run_boards": {"r": board}},
    )
    state = tmp_path / "test project"
    config = SimpleNamespace(workflow=workflow)
    ctx = SimpleNamespace(config=config, state_dir=state, event_log=EventLog(state / "events.jsonl"))
    payload = dict(flow_kind="prd", workflow_run_id="r", attempt_id="reader")
    assert not run_board_hint(config, state, {**payload, "flow_kind": "issue"})
    assert not run_board_hint(config, state, {**payload, "workflow_run_id": ""})
    assert not run_board_hint(config, state, {**payload, "attempt_id": ""})
    command = run_board_hint(config, state, payload).split("`")[1]
    invocation = shlex.split(command)
    args = build_parser().parse_args(invocation[invocation.index("artifact"):])
    assert args.state_dir == str(state) and args.attempt == "reader" and args.roster
    monkeypatch.setattr(artifact_board, "resolve_project_context", lambda **_: ctx)
    monkeypatch.setattr(artifact_board, "event_log_from_project", lambda *a, **kw: ctx.event_log)
    identity = seed(ctx, "reader")
    manifest = build_attempt_source_manifest(
        workflow_run_id="r", task_id="task", attempt_id="reader", dispatch_id="reader", sources=[]
    )
    write_attempt_source_manifest(state, manifest)
    token = provision_role_artifact_read_credential(state, "reader", role_name="reader", provider="mock")
    bind_attempt_artifact_read_capability(
        state, operation_id=identity["operation_id"], attempt_id="reader", role_instance="reader", manifest=manifest,
    )
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "reader")
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN", token.read_text().strip())
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out) == {k: board[k] for k in ("generation", "members", "groups")}
    board["members"].remove("reader")
    assert args.func(args) == 1
    assert not capsys.readouterr().out
