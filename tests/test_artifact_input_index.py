import copy
import json

import pytest
from fastapi.testclient import TestClient

from tests.test_artifact_read_capability import _grant
from tests.test_artifact_read_snapshot import IDENTITY, published
from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.artifact_input_index import input_index, read_cost
from zf.runtime.artifact_query import ArtifactQueryService
from zf.runtime.artifact_read_ledger import active_read_ledger_path
from zf.web.server import create_app


def test_cli_query_web_share_pinned_scoped_index_without_read_receipts(tmp_path, monkeypatch, capsys):
    state = tmp_path / "custom-state"
    manifest, required, _ = published(state)
    _grant(state, monkeypatch, manifest=manifest)
    EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(type="task.dispatched", actor="orchestrator", task_id="T7",
        payload={"dispatch_id": "a", "required_reads": required}))
    assert main(["artifact", "index", "--attempt", "a", "--state-dir", str(state)]) == 0
    cli = json.loads(capsys.readouterr().out)
    query = ArtifactQueryService(state_dir=state, project_root=tmp_path)
    view = query.attempt_inputs("a", context=query.context())
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.get("/api/attempts/a/inputs")
    assert response.status_code == 200
    web = response.json()
    assert cli["items"] == view["items"] == web["items"]
    assert cli["task_id"] == "T7"
    assert [i["json_path"] for i in cli["items"]] == ["$.shared", "$.tasks[7]"]
    assert all(i["required"] and i["read_status"] == "missing" for i in cli["items"])
    assert sum(i["selected_bytes"] for i in cli["items"]) < cli["items"][0]["source_bytes"] / 20
    assert not active_read_ledger_path(state, "a").exists()
    assert cli["provider_tokens"] is None and cli["body_read_evidence"] is False
    assert client.get("/api/attempts/a/inputs?limit=0").status_code == 400
    monkeypatch.setenv("ZF_ROLE_NAME", "verify")
    assert main(["artifact", "index", "--attempt", "a", "--state-dir", str(state)]) == 1
    assert "task-input" not in capsys.readouterr().out


def test_filter_permissions_before_counts_and_same_digest_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /workspace/zf run zf")
    manifest, rows, _ = published(tmp_path, aliases=True)
    result = input_index(manifest=manifest, required=rows, identity=IDENTITY, reads=[])
    assert result["items"][0]["read_command"].startswith("uv --project /workspace/zf run zf artifact read ")
    assert result["total"] == 2
    assert "alias" not in json.dumps(result)
    assert input_index(manifest=manifest, required=rows, identity=IDENTITY, reads=[], query="alias")["total"] == 0
    narrowed = input_index(manifest=manifest, required=rows, identity=IDENTITY, reads=[], query="tasks[7]")
    assert narrowed["total"] == 1
    assert narrowed["items"][0]["reason"] == "required_by_attempt_policy"


def test_navigation_pagination_binds_manifest_query_and_identity(tmp_path):
    manifest, required, _ = published(tmp_path)
    page = input_index(manifest=manifest, required=required, identity=IDENTITY, reads=[], limit=1)
    assert len(json.dumps(page, ensure_ascii=False).encode()) < 8192
    next_page = input_index(manifest=manifest, required=required, identity=IDENTITY, reads=[], cursor=page["next_cursor"])
    assert len(next_page["items"]) == 1 and next_page["next_cursor"] is None
    for override in ({"query": "tasks"}, {"required": []}, {"identity": {**IDENTITY, "role": "verify"}},
                     {"manifest": {**manifest, "task_id": "T8"}}):
        args = {"manifest": manifest, "required": required, "identity": IDENTITY, "reads": [], **override}
        with pytest.raises(ValueError, match="stale"):
            input_index(**args, cursor=page["next_cursor"])


def test_repeated_cost_counts_deliveries_not_replicated_ledger_rows():
    row = {"delivery_id": "one", "output_sha256": "o", "read_at": "1", "returned_bytes": 12,
           "selected_sha256": "s", "page_start": 0, "page_end": 4}
    second = {**row, "delivery_id": "two", "read_at": "2"}
    cost = read_cost([row, copy.deepcopy(row), second])
    assert cost["produced_body_bytes"] == 24
    assert cost["repeated_exact_range_bytes"] == 12
    assert cost["read_count"] == 2
    assert cost["current_memory"] == cost["provider_delivery"] == "unknown"


def test_project_input_route_uses_resolved_project_and_cannot_fall_back(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import zf.web.server as server
    from fastapi import HTTPException

    first, second = tmp_path / "first", tmp_path / "second"
    published(first)
    published(second, body={"shared": "second project", "tasks": [{} for _ in range(8)]})
    def resolve(project_id, **kwargs):
        if project_id != "second":
            raise HTTPException(404, "unknown project")
        return SimpleNamespace(state_dir=second, project_root=tmp_path, config=None)
    monkeypatch.setattr(server, "_resolve_api_project", resolve)
    client = TestClient(server.create_app(first, project_root=tmp_path))
    scoped = client.get("/api/projects/second/attempts/a/inputs")
    local = client.get("/api/attempts/a/inputs")
    assert scoped.status_code == 200
    assert scoped.json()["manifest_sha256"] != local.json()["manifest_sha256"]
    assert scoped.json()["items"][0]["selected_bytes"] == len("second project")
    assert client.get("/api/projects/unknown/attempts/a/inputs").status_code == 404


def test_reader_policy_is_resolved_from_operation_sidecar_not_marked_optional(tmp_path):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    manifest, required, _ = published(tmp_path)
    query = ArtifactQueryService(state_dir=tmp_path, project_root=tmp_path.parent)
    unknown = query.attempt_inputs("a", context=query.context())
    assert unknown["required_policy_status"] == "unknown"
    assert all(item["required"] is None for item in unknown["items"])
    policy = write_immutable_json_sidecar(tmp_path, {"attempt_id": "a", "required_reads": required},
        root="policies", kind="policy", schema_version="input-consumption-policy.v1", created_by="kernel")
    request = write_immutable_json_sidecar(tmp_path, {"request": {"input_consumption_policy_ref": policy}},
        root="requests", kind="request", schema_version="request.v1", created_by="kernel")
    EventWriter(EventLog(tmp_path / "events.jsonl")).append(ZfEvent(type="workflow.operation.requested",
        payload={"operation_id": "op", "active_attempt_id": "a", "request_ref": request}))
    resolved = query.attempt_inputs("a", context=query.context())
    assert resolved["required_policy_status"] == "available"
    assert all(item["required"] for item in resolved["items"])
