"""The rendered OA read command must deliver full pinned bodies, not counters."""

import json
import shlex

import pytest

from tests.test_orchestrator_agent_operations import _request, _runtime
from zf.cli.main import main
from zf.runtime.artifact_read_capability import provision_role_artifact_read_credential
from zf.runtime.artifact_read_ledger import required_read_satisfied
from zf.runtime.orchestrator_agent_briefing import build_orchestrator_agent_operation_briefing
from zf.runtime.orchestrator_agent_operations import activate_orchestrator_agent_operation


def _prepared_for_mode(tmp_path, mode):
    if mode == "execution":
        from tests.test_orchestrator_agent_run_plan import _checkpoint, _runtime as plan_runtime

        runtime, trigger, loaded, _, _ = plan_runtime(tmp_path)
        runtime.config.workflow.orchestration.autonomous_actions = ["continue", "rework"]
        _, prepared = _checkpoint(runtime, trigger, loaded)
    else:
        runtime = _runtime(tmp_path)
        provision_role_artifact_read_credential(
            runtime.state_dir, "orchestrator", role_name="orchestrator", provider="mock",
        )
        runtime.config.project.name = "中文🚀"
        runtime.config.workflow.orchestration.shadow_sample_percent = 100
        prepared = _request(runtime, **({"checkpoint": "plan_candidate", "checkpoint_policy": "shadow"}
                                       if mode == "compact" else {}))
    assert prepared.context.input_body["input_mode"] == mode
    return runtime, prepared


def _read_command(runtime, prepared, monkeypatch):
    token = provision_role_artifact_read_credential(
        runtime.state_dir, "orchestrator", role_name="orchestrator", provider="mock",
    )
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
    return shlex.split(command)[1:], briefing


@pytest.mark.parametrize("mode", ["exhaustive", "compact", "execution"])
def test_rendered_required_command_delivers_all_oa_bodies(tmp_path, monkeypatch, capsys, mode):
    from zf.runtime import artifact_read_snapshot as snapshots
    from zf.runtime import artifact_source_versions as versions

    runtime, prepared = _prepared_for_mode(tmp_path, mode)
    args, _ = _read_command(runtime, prepared, monkeypatch)
    manifest = prepared.context.source_manifest
    required = prepared.context.read_policy["required_reads"]
    required_ids = {row["source_id"] for row in required}
    expected = {source["source_id"]: snapshots.selected_text(
        (runtime.state_dir / source["ref"]).read_bytes(), "$",
    ) for source in manifest["sources"] if source["source_id"] in required_ids}
    checked = []
    original_access = snapshots.artifact_access_allowed

    def check_occurrence(scope, **identity):
        checked.append(identity)
        return original_access(scope, **identity)

    monkeypatch.setattr(snapshots, "artifact_access_allowed", check_occurrence)
    monkeypatch.setattr(versions, "read_source_version",
                        lambda *a, **k: pytest.fail("OA page reread complete source after publication"))
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
    assert bodies == expected
    assert len(checked) == pages * len(required)
    assert all(row["role"] == "orchestrator" for row in checked)
    for source in manifest["sources"]:
        if source["source_id"] in required_ids:
            assert source["recovery_ref"]
            assert [row["json_path"] for row in source["read_selections"]] == ["$"]
        else:
            assert "recovery_ref" not in source and "read_selections" not in source
    if mode == "execution":
        assert required_ids == {"checkpoint-input", "execution-context", "effective-config"}
        assert any(source["source_id"].startswith("plan-port-") for source in manifest["sources"]
                   if source["source_id"] not in required_ids)
    ledger = runtime.state_dir / "artifacts/attempts" / prepared.attempt_id / "read-ledger.active.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert all(required_read_satisfied(rows, r) for r in required)


def test_real_oa_briefing_preserves_complete_required_page_envelope(tmp_path, monkeypatch):
    runtime, prepared = _prepared_for_mode(tmp_path, "exhaustive")
    _, briefing = _read_command(runtime, prepared, monkeypatch)
    assert "Return each raw page unchanged" in briefing
    assert "do not filter fields or batch pages into a summary" in briefing


def test_oa_pinned_continuation_still_requires_authenticated_role(tmp_path, monkeypatch, capsys):
    runtime, prepared = _prepared_for_mode(tmp_path, "exhaustive")
    args, _ = _read_command(runtime, prepared, monkeypatch)
    assert main([*args, "--page-bytes", "2048"]) == 0
    cursor = json.loads(capsys.readouterr().out)["next_cursor"]
    assert cursor
    ledger = runtime.state_dir / "artifacts/attempts" / prepared.attempt_id / "read-ledger.active.jsonl"
    before = ledger.read_bytes()
    monkeypatch.setenv("ZF_ROLE_NAME", "impostor")
    assert main([*args, "--page-bytes", "2048", "--cursor", cursor]) == 1
    output = capsys.readouterr()
    assert not output.out and "does not match" in output.err
    assert ledger.read_bytes() == before


def test_legacy_oa_replay_keeps_published_manifest_and_cursor(tmp_path, monkeypatch, capsys):
    from zf.runtime import artifact_read_snapshot as snapshots
    from zf.runtime import artifact_source_versions as versions

    # Model a checkpoint already published by the pre-pin implementation.
    with monkeypatch.context() as old_publisher:
        old_publisher.setattr(versions, "pin_source_versions", lambda *a, **k: None)
        old_publisher.setattr(snapshots, "pin_read_selections", lambda *a, **k: None)
        runtime, prepared = _prepared_for_mode(tmp_path, "exhaustive")
    args, _ = _read_command(runtime, prepared, monkeypatch)
    assert main([*args, "--page-bytes", "2048"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["next_cursor"]
    manifest_ref = prepared.context.source_manifest_ref
    stored = (runtime.state_dir / manifest_ref["ref"]).read_bytes()
    monkeypatch.setattr(versions, "pin_source_versions",
                        lambda *a, **k: pytest.fail("replay republished legacy manifest"))
    replay = _request(runtime)
    assert replay.replay_hit
    assert replay.context.source_manifest_ref == manifest_ref
    assert (runtime.state_dir / manifest_ref["ref"]).read_bytes() == stored
    assert not any(source.get("read_selections") for source in replay.context.source_manifest["sources"])
    assert main([*args, "--page-bytes", "2048", "--cursor", first["next_cursor"]]) == 0
    continued = json.loads(capsys.readouterr().out)
    assert continued["bundle_digest"] == first["bundle_digest"]
    assert continued["items"][0]["page_start"] == first["items"][-1]["page_end"]
