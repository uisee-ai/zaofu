"""The rendered OA read command must deliver full pinned bodies, not counters."""

import json
import shlex

from tests.test_orchestrator_agent_operations import _request, _runtime
from zf.cli.main import main
from zf.runtime.artifact_read_capability import provision_role_artifact_read_credential
from zf.runtime.artifact_read_ledger import required_read_satisfied
from zf.runtime.orchestrator_agent_briefing import build_orchestrator_agent_operation_briefing
from zf.runtime.orchestrator_agent_operations import activate_orchestrator_agent_operation


def test_rendered_required_command_delivers_all_oa_bodies(tmp_path, monkeypatch, capsys):
    runtime = _runtime(tmp_path)
    token = provision_role_artifact_read_credential(
        runtime.state_dir, "orchestrator", role_name="orchestrator", provider="mock",
    )
    prepared = _request(runtime)
    activate_orchestrator_agent_operation(
        runtime, prepared, dispatch_id="dispatch-1", causation_id="evt-run-start",
    )
    for name, value in {
        "ZF_ROLE_INSTANCE": "orchestrator", "ZF_ROLE_NAME": "orchestrator",
        "ZF_ROLE_BACKEND": "mock", "ZF_ARTIFACT_READ_TOKEN_FILE": str(token),
        "ZF_ARTIFACT_PURPOSE": prepared.context.source_manifest["read_purpose"],
    }.items():
        monkeypatch.setenv(name, value)
    briefing = build_orchestrator_agent_operation_briefing(
        state_dir=runtime.state_dir, prepared=prepared,
    )
    command = next((line for line in briefing.splitlines()
                    if line.startswith("zf artifact read ") and "--required" in line), "")
    assert command, "OA needs an executable required-body paging entrypoint"
    args = shlex.split(command)[1:]
    cursor, bodies, pages = "", {}, 0
    while True:
        assert main([*args, "--page-bytes", "2048", "--cursor", cursor]) == 0
        page = json.loads(capsys.readouterr().out)
        for row in page["items"]:
            bodies[row["source_id"]] = bodies.get(row["source_id"], "") + row["content"]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages > 1
    manifest = prepared.context.source_manifest
    required = prepared.context.read_policy["required_reads"]
    assert set(bodies) == {r["source_id"] for r in required}
    for source in manifest["sources"]:
        if source["source_id"] in bodies:
            assert json.loads(bodies[source["source_id"]]) == json.loads(
                (runtime.state_dir / source["ref"]).read_text(),
            )
    ledger = runtime.state_dir / "artifacts/attempts" / prepared.attempt_id / "read-ledger.active.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert all(required_read_satisfied(rows, r) for r in required)
