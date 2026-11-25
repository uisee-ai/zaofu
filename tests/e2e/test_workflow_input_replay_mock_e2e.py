"""Replay a real frozen contract on isolated ledgers and synthetic public tool returns."""

import io
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from zf.cli.artifact_required_read import prepare_required_read
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime import artifact_mount
from zf.runtime.artifact_mount import MountAccess
from zf.runtime.artifact_output_delivery import emit_artifact_output
from zf.runtime.artifact_read_ledger import seal_read_ledger
from zf.runtime.artifact_read_ledger_validation import validate_read_rows
from zf.runtime.artifact_text_page import parse_artifact_page
from zf.runtime.artifact_workdir_view import AttemptFiles
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def copy_descriptor(source, state, desc):
    ref = desc["ref"]
    path = (source / ref).resolve()
    if (
        not path.is_relative_to((source / "artifacts").resolve())
        or Path(ref).is_absolute()
        or ".." in Path(ref).parts
    ):
        raise ValueError("fixture reference outside the published artifacts")
    target = state / ref
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return target


def copy_sources(source, state, value):
    if isinstance(value, dict):
        if isinstance(value.get("ref"), str) and value.get("sha256"):
            copy_descriptor(source, state, value)
        for child in value.values():
            copy_sources(source, state, child)
    elif isinstance(value, list):
        for child in value:
            copy_sources(source, state, child)


@pytest.mark.parametrize("mode", ["A", "B", "C"])
def test_frozen_8x8_input_contract_twice_interrupted(tmp_path, monkeypatch, mode):
    folder, attempt = (
        os.getenv("ZF_8X8_FIXTURE_STATE"),
        os.getenv("ZF_8X8_FIXTURE_ATTEMPT"),
    )
    if not (folder and attempt):
        pytest.skip("recorded 8x8 input corpus is opt-in")
    source, state = Path(folder), tmp_path / "state"
    with (source / "events.jsonl").open() as stream:
        approved = next(
            item["payload"]
            for line in stream
            if (item := json.loads(line))["type"] == "workflow.operation.requested"
            and item["payload"].get("active_attempt_id") == attempt
        )
    request = hydrate_sidecar_ref(source, approved["request_ref"]).payload["request"]
    policy = hydrate_sidecar_ref(
        source, request["input_consumption_policy_ref"]
    ).payload
    manifest_ref = policy["delivery_evidence"]["source_manifest_ref"]
    copy_descriptor(source, state, manifest_ref)
    manifest = hydrate_sidecar_ref(state, manifest_ref).payload
    copy_sources(source, state, manifest["sources"])
    rows = policy["required_reads"]
    assert len(rows) == 40
    ident = {
        "role": request["role_instance"],
        "actor": request["role_instance"],
        "provider": "codex",
        "purpose": manifest["read_purpose"],
        "operation_id": approved["operation_id"],
        "attempt_id": attempt,
    }
    sid, transcript = str(uuid4()), state / "mock-provider.jsonl"

    def put(value):
        with transcript.open("a") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")

    put({"type": "session_meta", "payload": {"id": sid}})
    RoleSessionRegistry(state / "role_sessions.yaml", tmp_path).bind_codex_session(
        ident["actor"], sid, session_path=transcript
    )
    view = AttemptFiles(
        state, manifest, ident, rows, lambda: {"operation_id": ident["operation_id"]}
    )
    calls = 0

    def response(call_id, text):
        put(
            {
                "type": "response_item",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": text,
                },
            }
        )

    started = time.monotonic()
    if mode != "C":
        cursor = None
        while True:
            output, receipts = prepare_required_read(
                state,
                manifest=manifest,
                rows=rows,
                identity=ident,
                cursor=cursor,
                output_format="json" if mode == "A" else "text",
                page_bytes=8192 if mode == "A" else 32768,
            )
            emit_artifact_output(state, output, receipts, io.StringIO())
            calls += 1
            response(f"call-{calls}", output)
            cursor = parse_artifact_page(output)["next_cursor"]
            if not cursor:
                break
    else:
        root = tmp_path / "virtual-mount"
        access = MountAccess(state, view, root, "/isolated-mock", lambda: 1, "0.171.0")
        monkeypatch.setattr(
            artifact_mount, "peer_identity", lambda pid, group: "mock-start"
        )
        for required in rows:
            name = next(
                path
                for path, (r, _) in view.files.items()
                if all(
                    r[k] == required.get(k, "$")
                    for k in (
                        "source_id",
                        "artifact_id",
                        "json_path",
                        "artifact_sha256",
                    )
                )
            )
            total = len(view.read(name)[0].decode())
            for start in range(0, max(1, total), 8192):
                calls += 1
                displayed = (
                    name
                    if total <= 8192
                    else f"{name}.chars-{start}-{min(total, start + 8192)}"
                )
                call_id = f"call-{calls}"
                call = {
                    "type": "response_item",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": call_id,
                        "arguments": json.dumps(
                            {"cmd": "/usr/bin/cat " + str(root / displayed)}
                        ),
                    },
                }
                put(call)
                monkeypatch.setattr(
                    artifact_mount,
                    "open_call",
                    lambda *_, c=call, f=displayed: {
                        "session_id": sid,
                        "call": c,
                        "actual_path": str(root / f),
                        "peer_pid": 42,
                    },
                )
                handle = access.open(displayed, 42)
                text = access.read(handle, 0, len(handle["data"])).decode()
                response(
                    call_id,
                    json.dumps(
                        {"chunk_id": call_id, "exit_code": 0, "output": text},
                        ensure_ascii=False,
                    ),
                )
    reading_ms = round((time.monotonic() - started) * 1000)
    for _ in range(2):
        sealed = seal_read_ledger(state, attempt)
        ledger = [
            json.loads(line)
            for line in hydrate_sidecar_ref(state, sealed).payload.splitlines()
        ]
        assert validate_read_rows(state, policy=policy, rows=ledger) == []
        other = {
            **policy,
            "delivery_evidence": {
                **policy["delivery_evidence"],
                "consumer_actor": "different-lane",
            },
        }
        assert validate_read_rows(state, policy=other, rows=ledger)
        resumed = json.loads(
            AttemptFiles(state, manifest, ident, rows, lambda: {})
            .read("RESUME.md")[0]
            .split(b"\n", 2)[2]
        )
        host = resumed["host_evidence"]
        assert (host["total"], host["available"], len(host["gaps"])) == (19, 18, 1)
        assert (
            host["gaps"][0]["receipt_id"]
            and host["gaps"][0]["source_operation_id"]
            and not host["authorizes_retry"]
        )
        assert host["gaps"][0]["declared_paths"] and host["gaps"][0]["media_refs"]
        assert not (state / "events.jsonl").exists()
    print(
        json.dumps(
            {
                "mode": mode,
                "calls": calls,
                "mock_read_ms": reading_ms,
                "required": len(rows),
                "source_manifest_sha256": manifest_ref["sha256"],
                "new_host_executions": 0,
            }
        )
    )
