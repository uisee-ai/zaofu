import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from tests.test_artifact_read_snapshot import IDENTITY, published
from tests.test_input_delivery_admission import _operation, _semantic
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime import artifact_mount
from zf.runtime.artifact_mount import MountAccess
from zf.runtime.artifact_read_ledger import active_read_ledger_path, seal_read_ledger
from zf.runtime.artifact_read_ledger_validation import validate_read_rows
from zf.runtime.artifact_workdir_view import AttemptFiles
from zf.runtime.result_submit import ResultSubmitError
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def test_exact_files_acl_resume_utf8_and_no_checkout_changes(tmp_path):
    manifest, required, _ = published(
        tmp_path, aliases=True, body={"shared": "中\r\n文", "tasks": [""] * 8}
    )
    view = AttemptFiles(
        tmp_path,
        manifest,
        {**IDENTITY, "operation_id": "op"},
        required,
        lambda: {"step": "verify"},
    )
    assert len(view.files) == 2
    assert "alias" not in view.read("INPUTS.md")[0].decode()
    assert b'"step": "verify"' in view.read("RESUME.md")[0]
    assert {view.read(p)[0] for p in view.files} == {"中\r\n文".encode(), b""}
    with pytest.raises(FileNotFoundError):
        view.read("inputs/../../private")
    assert not active_read_ledger_path(tmp_path, "a").exists()


def test_revocation_denies_already_open_file(tmp_path, monkeypatch):
    manifest, required, _ = published(tmp_path)
    view = AttemptFiles(
        tmp_path, manifest, {**IDENTITY, "operation_id": "op"}, required, lambda: {}
    )
    epoch = [1]
    monkeypatch.setattr(artifact_mount, "peer_identity", lambda p, g: "pid-start")
    access = MountAccess(
        tmp_path, view, tmp_path / "mount", "/lane", lambda: epoch[0], ""
    )
    handle = access.open(next(iter(view.files)), 44)
    assert access.read(handle, 0, 1)
    epoch[0] += 1
    with pytest.raises(PermissionError):
        access.read(handle, 1, 120)


@pytest.mark.parametrize(
    "outcome",
    [
        "complete",
        "other-call",
        "truncated",
        "missing",
        "ranges",
        "mixed-cli",
        "corrupt-witness",
    ],
)
@pytest.mark.parametrize("version", ["0.171.0", "0.172.0"])
def test_host_open_public_call_and_actual_admission(tmp_path, monkeypatch, outcome, version):
    runtime, payload, prepared, service, token = _operation(tmp_path)
    state = runtime.state_dir
    manifest = hydrate_sidecar_ref(state, payload["attempt_source_manifest"]).payload
    view = AttemptFiles(
        state,
        manifest,
        {**IDENTITY, "operation_id": prepared.operation_id},
        payload["required_reads"],
        lambda: {},
    )
    mount = tmp_path / "mock-worker-mount"
    access = MountAccess(state, view, mount, "/dedicated", lambda: 7, version)
    monkeypatch.setattr(
        artifact_mount, "peer_identity", lambda pid, group: "verified-mock-peer"
    )
    sid = str(uuid4())
    transcript = state / f"{sid}.jsonl"

    def put(value):
        with transcript.open("a") as stream:
            stream.write(json.dumps(value) + "\n")

    put({"type": "session_meta", "payload": {"id": sid}})
    RoleSessionRegistry(
        state / "role_sessions.yaml", runtime.project_root
    ).bind_codex_session("dev-1", sid, session_path=transcript)
    base = next(iter(view.files))
    total = len(view.read(base)[0].decode())
    spans = [(0, 7), (7, total)] if outcome == "ranges" else [(0, total)]
    for start, end in spans:
        name = base if len(spans) == 1 else f"{base}.chars-{start}-{end}"
        path, call_id = str(mount / name), f"call-{start}"
        call = {
            "type": "response_item",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": call_id,
                "arguments": json.dumps({"cmd": "/usr/bin/cat " + path}),
            },
        }
        put(call)
        monkeypatch.setattr(
            artifact_mount,
            "open_call",
            lambda *args: {
                "session_id": sid,
                "call": call,
                "actual_path": path,
                "peer_pid": 401,
            },
        )
        handle = access.open(name, 401)
        shown = access.read(handle, 0, 65536).decode()
        if outcome == "mixed-cli":
            shown = (
                "Warning: truncated output (original token count: 41)\nTotal output lines: 1\n\n"
                + shown[:5]
                + "…10 tokens truncated…"
                + shown[-5:]
            )
        if outcome != "missing":
            put(
                {
                    "type": "response_item",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "unrelated" if outcome == "other-call" else call_id,
                        "output": "Chunk ID: 0001\nWall time: 0.01 seconds\nProcess exited with code 0\nOutput:\n"
                        + (shown[:-1] if outcome == "truncated" else shown),
                    },
                }
            )
    if outcome == "mixed-cli":
        import io
        from zf.runtime.artifact_output_delivery import emit_artifact_output
        from zf.runtime.artifact_read_ledger import read_attempt_artifact
        from zf.runtime.input_delivery_recovery import input_delivery_recovery

        host = [
            json.loads(line)
            for line in active_read_ledger_path(state, manifest["attempt_id"])
            .read_text()
            .splitlines()
        ]
        gap = input_delivery_recovery(
            state,
            runtime.event_log.read_all(),
            view.identity,
            manifest["attempt_id"],
            host,
        )
        assert gap["missing"][0]["next_missing_range"] == [5, total - 5]
        row = view.files[base][0]
        result = read_attempt_artifact(
            state,
            manifest=manifest,
            **row,
            **IDENTITY,
            page_start=5,
            page_size=total - 10,
            record=False,
        )
        part = {**result["read"], "operation_id": prepared.operation_id}
        raw = json.dumps(
            {
                "content": result["content"],
                **{k: part[k] for k in ("page_start", "page_end", "page_total")},
            }
        )
        emit_artifact_output(state, raw, [part], io.StringIO())
        put(
            {
                "type": "response_item",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "type": "function_call_output",
                    "call_id": "cli-supplement",
                    "output": raw,
                },
            }
        )
    sealed = seal_read_ledger(state, manifest["attempt_id"])
    rows = [
        json.loads(line)
        for line in hydrate_sidecar_ref(state, sealed).payload.splitlines()
    ]
    assert any(r["production_status"] == "mounted_file_read" for r in rows)
    if outcome == "corrupt-witness":
        (state / rows[0]["native_proof_ref"]["ref"]).write_text("{}")
    valid = outcome in {"complete", "ranges", "mixed-cli"}
    assert (
        bool(
            validate_read_rows(
                state, policy=payload["input_consumption_policy"], rows=rows
            )
        )
        is not valid
    )
    if valid:
        assert service.submit(
            operation_id=prepared.operation_id,
            semantic_result=_semantic(),
            role_instance="dev-1",
            credential=token,
        ).admitted_event_id
    elif outcome != "corrupt-witness":
        with pytest.raises(ResultSubmitError):
            service.submit(
                operation_id=prepared.operation_id,
                semantic_result=_semantic(),
                role_instance="dev-1",
                credential=token,
            )


def configured_runtime(tmp_path, monkeypatch, *, backend="codex", mapped=True):
    from tests.test_result_submit import _runtime
    from zf.core.config.schema import RoleConfig

    runtime = _runtime(tmp_path)
    runtime.config.roles = [
        RoleConfig(name="dev", instance_id="dev-1", backend=backend)
    ]
    groups = {"dev-1": "/dedicated"} if mapped else {}
    runtime.config.workflow.flow_metadata["artifact_worker_cgroups"] = groups
    monkeypatch.setattr(
        artifact_mount, "mounted_scope", lambda config, actor: groups[actor]
    )
    return runtime


@pytest.mark.parametrize("version", ["0.171.0", "0.172.0"])
def test_registered_workspace_cli_binds_current_grant_rollout_and_rotation(
    tmp_path, monkeypatch, capsys, version
):
    from types import SimpleNamespace
    from tests.test_artifact_read_capability import _grant
    from zf.cli import artifact_workspace
    from zf.cli.main import main
    from zf.runtime.artifact_read_capability import (
        provision_role_artifact_read_credential,
    )

    runtime = configured_runtime(tmp_path, monkeypatch)
    runtime, payload, prepared, _, _ = _operation(tmp_path, runtime=runtime)
    state = runtime.state_dir
    manifest = hydrate_sidecar_ref(state, payload["attempt_source_manifest"]).payload
    _grant(state, monkeypatch, manifest=manifest, operation_id=prepared.operation_id)
    cfg = tmp_path / "zf.yaml"
    cfg.write_text("project: test")
    ctx = SimpleNamespace(
        config=runtime.config,
        state_dir=state,
        project_root=runtime.project_root,
        config_path=cfg,
    )
    monkeypatch.setattr(artifact_workspace, "resolve_project_context", lambda **kw: ctx)
    monkeypatch.setattr(artifact_workspace, "mounted_scope", lambda *args: "/dedicated")
    monkeypatch.setattr(artifact_workspace, "peer_identity", lambda *args: "mock-pid")
    sid, rollout = str(uuid4()), state / "rollout.jsonl"
    rollout.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": sid}}) + "\n"
    )
    RoleSessionRegistry(
        state / "role_sessions.yaml", ctx.project_root
    ).bind_codex_session("dev-1", sid, session_path=rollout)
    served = []
    replacement_credential = None

    def serve(access):
        nonlocal replacement_credential
        served.append(access.path)
        assert access.check() == 1
        assert "source_id" in access.view.read("INPUTS.md")[0].decode()
        assert manifest["attempt_id"] in access.view.read("RESUME.md")[0].decode()
        replacement_credential = provision_role_artifact_read_credential(
            state, "dev-1", role_name="dev", provider="codex"
        )
        with pytest.raises(ValueError, match="lease expired"):
            access.check()

    monkeypatch.setattr(artifact_workspace, "serve_mount", serve)
    argv = [
        "artifact",
        "workspace",
        "--attempt",
        manifest["attempt_id"],
        "--state-dir",
        str(state),
    ]
    monkeypatch.setattr(artifact_workspace.shutil, "which", lambda _: "/bin/codex")
    monkeypatch.setattr(
        artifact_workspace.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="codex-cli 0.170.0"),
    )
    assert main(argv) == 1 and not served and "version" in capsys.readouterr().err
    monkeypatch.setattr(artifact_workspace.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout="codex-cli 0.173.0",
    ))
    assert main(argv) == 1 and not served and "version" in capsys.readouterr().err
    monkeypatch.setattr(
        artifact_workspace.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=f"codex-cli {version}"),
    )
    monkeypatch.setattr(artifact_workspace, "mounted_scope", lambda *args: "/other")
    assert (
        main(argv) == 1 and not served and "original request" in capsys.readouterr().err
    )
    monkeypatch.setattr(artifact_workspace, "mounted_scope", lambda *args: "/dedicated")
    assert main(argv) == 0 and len(served) == 1 and not served[0].exists()
    assert main(argv) == 1 and "credential" in capsys.readouterr().err
    # A new process gets the replacement credential; its missing session remains a separate denial.
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN_FILE", str(replacement_credential))
    rollout.unlink()
    assert main(argv) == 1 and len(served) == 1
    assert "session" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("backend", "mapped", "expected"),
    [
        ("codex", True, True),
        ("codex", False, False),
        ("claude-code", True, False),
    ],
)
def test_first_dispatch_freezes_file_preferences_across_replay(
    tmp_path, monkeypatch, backend, mapped, expected
):
    from zf.runtime.artifact_read_ledger import render_attempt_source_briefing
    from zf.runtime.call_result_runtime import prepare_call_operation
    from zf.runtime.reader_respawn_continuation import reader_continuation_guidance
    from zf.runtime.workflow_operation import load_workflow_operation

    runtime = configured_runtime(tmp_path, monkeypatch, backend=backend, mapped=mapped)
    runtime, payload, first, _, _ = _operation(tmp_path, runtime=runtime)
    operator = load_workflow_operation(runtime.event_log, first.operation_id)
    request = hydrate_sidecar_ref(runtime.state_dir, operator["request_ref"]).payload[
        "request"
    ]
    assert request.get("native_input_cgroup", "") == ("/dedicated" if expected else "")

    def check_prompt(native):
        rendered = render_attempt_source_briefing(payload, state_dir=runtime.state_dir)
        assert ("Preferred artifact input" in rendered) == native
        assert ("Preferred deduplicated read" in rendered) != native
        assert ("CLI fallback" in rendered) == native
        guidance = reader_continuation_guidance(
            attempt_id=first.attempt_id,
            backend=backend,
            cli="zf",
            runtime=runtime,
            actor="dev-1",
            operation_id=first.operation_id,
        )
        assert ("Open the mounted RESUME.md first" in guidance) == native

    check_prompt(expected)
    # A missing scope, a changed path or a newly authorized worker must never
    # change the already published request, even with a spoofed transport preference.
    runtime.config.workflow.flow_metadata["artifact_worker_cgroups"]["dev-1"] = (
        "/changed"
    )
    payload["native_input_view"] = True
    retry = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_writer_child",
        operation_key="dev-1-T1",
        stage_id="impl",
        task_id="T1",
        dispatch_id="attempt-1",
    )
    assert retry.request_hash == first.request_hash
    check_prompt(False)
    monkeypatch.setattr(
        artifact_mount,
        "mounted_scope",
        lambda *args: (_ for _ in ()).throw(ValueError("missing scope")),
    )
    assert "artifact workspace" not in reader_continuation_guidance(
        attempt_id=first.attempt_id,
        backend=backend,
        cli="zf",
        runtime=runtime,
        actor="dev-1",
        operation_id=first.operation_id,
    )


def test_delayed_host_placement_keeps_first_permission_without_preferring_unavailable_mount(
    tmp_path, monkeypatch
):
    from zf.runtime.artifact_read_ledger import render_attempt_source_briefing
    from zf.runtime.artifact_workdir_view import recovery_hint

    runtime = configured_runtime(tmp_path, monkeypatch)

    def unavailable(*_):
        raise ValueError("scope has not started")

    monkeypatch.setattr(artifact_mount, "mounted_scope", unavailable)
    _, payload, first, _, _ = _operation(tmp_path, runtime=runtime)
    assert "Preferred deduplicated read" in render_attempt_source_briefing(payload)
    assert not recovery_hint(runtime, "dev-1", first.attempt_id, first.operation_id)
    monkeypatch.setattr(artifact_mount, "mounted_scope", lambda *_: "/dedicated")
    assert "RESUME.md" in recovery_hint(
        runtime, "dev-1", first.attempt_id, first.operation_id
    )
    assert not recovery_hint(runtime, "dev-1", "different-attempt", first.operation_id)


def test_worker_mount_refusal_uses_cli_fallback_error(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from zf.runtime.artifact_mount import serve_mount

    def mount_refused(*args, **kwargs):
        raise RuntimeError(1)  # fusepy when /dev/fuse is unavailable in this worker

    monkeypatch.setitem(sys.modules, "fusepy", SimpleNamespace(Operations=object, FUSE=mount_refused))
    with pytest.raises(OSError, match=r"FUSE mount failed in this worker environment \(exit 1\)"):
        serve_mount(SimpleNamespace(path=tmp_path))
