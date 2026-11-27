"""Correlate a host-observed read of one mounted file with its public Codex tool call."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
from datetime import datetime
from pathlib import Path
from uuid import UUID

from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

HOST_SCHEMA = "mounted-file-read.v1"
PROOF_SCHEMA = "mounted-file-tool-return.v1"
NATIVE_CODEX_VERSIONS = ("0.171.0", "0.172.0")


def session_rows(state, actor, cache=None):
    registry = RoleSessionRegistry(state / "role_sessions.yaml", state.parent)
    sid, path = str(registry.get(actor) or ""), registry.get_path(actor)
    if not path or str(UUID(sid)) != sid:
        raise ValueError("no bound native session")
    cache = cache if cache is not None else {}
    with path.open("rb") as stream:
        st = os.fstat(stream.fileno())
        header = json.loads(stream.readline())
        if header.get("type") != "session_meta" or header["payload"]["id"] != sid:
            raise ValueError("native transcript identity changed")
        key = (actor, sid, path, st.st_dev, st.st_ino)
        offset, rows, ids = cache.get(key, (stream.tell(), [], set()))
        if offset > st.st_size:
            raise ValueError("native transcript was truncated")
        stream.seek(offset)
        for line in stream:
            if not line.endswith(b"\n"):
                break
            row = json.loads(line)
            if row.get("type") == "session_meta":
                raise ValueError("mixed native session identity")
            payload = row.get("payload")
            if row.get("type") == "response_item" and isinstance(payload, dict):
                call_id = payload.get("call_id")
                if (
                    payload.get("name") in {"exec_command", "functions.exec_command"}
                    and call_id
                ):
                    args = json.loads(payload.get("arguments") or "{}")
                    if str(args.get("cmd", "")).startswith("/usr/bin/cat "):
                        ids.add(call_id)
                if call_id in ids:
                    rows.append(row)
            offset = stream.tell()
        cache[key] = (offset, rows, ids)
    current = RoleSessionRegistry(state / "role_sessions.yaml", state.parent)
    if str(current.get(actor) or "") != sid or current.get_path(actor) != path:
        raise ValueError("native session changed during observation")
    return sid, rows


def matching_call(payload, filename):
    if payload.get("type") != "function_call" or payload.get("name") not in {
        "exec_command",
        "functions.exec_command",
    }:
        return False
    args = json.loads(payload["arguments"])
    # One literal absolute file and fixed tool; reject compound shell or redirected reads.
    return args.get("cmd") == "/usr/bin/cat " + shlex.quote(filename) and bool(
        payload.get("call_id")
    )


def open_call(state, actor, filename, pid, cache=None):
    proc = Path("/proc") / str(pid)
    argv = (proc / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
    if os.readlink(proc / "exe") != "/usr/bin/cat" or argv != [
        b"/usr/bin/cat",
        os.fsencode(filename),
    ]:
        return None
    binary = shutil.which("codex")
    if not binary:
        return None
    expected = Path(binary).resolve()
    seen = set()
    ancestor = pid
    while ancestor > 1 and ancestor not in seen:
        seen.add(ancestor)
        entry = Path("/proc") / str(ancestor)
        if (entry / "exe").resolve() == expected:
            break
        ancestor = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[1])
    else:
        return None
    sid, rows = session_rows(state, actor, cache)
    completed = {
        r["payload"].get("call_id")
        for r in rows
        if r["payload"].get("type") == "function_call_output"
    }
    calls = [
        r
        for r in rows
        if r["payload"].get("call_id") not in completed
        and matching_call(r["payload"], filename)
    ]
    if len(calls) != 1:
        return None
    return {
        "session_id": sid,
        "call": calls[0],
        "actual_path": filename,
        "peer_pid": pid,
    }


def _intervals(raw, body):
    if not isinstance(raw, str):
        return []
    try:
        packet = json.loads(raw)
    except ValueError:
        packet = None
    if (
        isinstance(packet, dict)
        and packet.get("chunk_id")
        and packet.get("exit_code") == 0
    ):
        output = packet.get("output")
    else:
        match = re.fullmatch(
            r"Chunk ID: [\w-]+\nWall time: [\d.]+ seconds\nProcess exited with code 0\n"
            r"(?:Original token count: \d+\n)?(?:Final output:|Output:)\n(.*)",
            raw,
            re.S,
        )
        output = match[1] if match else None
    if output == body:
        return [(0, len(body))]
    if not isinstance(output, str):
        return []
    truncated = re.fullmatch(
        r"Warning: truncated output \(original token count: \d+\)\nTotal output lines: \d+\n\n(.*)",
        output,
        re.S,
    )
    if not truncated:
        return []
    parts = re.split(r"…\d+ tokens truncated…", truncated[1])
    if (
        len(parts) != 2
        or not body.startswith(parts[0])
        or not body.endswith(parts[1])
        or len(parts[0]) + len(parts[1]) >= len(body)
    ):
        return []
    return [
        (a, b)
        for a, b in ((0, len(parts[0])), (len(body) - len(parts[1]), len(body)))
        if a < b
    ]


def verified_native(state: Path, row: dict, text: str, *, ephemeral=False):
    host = hydrate_sidecar_ref(state, row["native_host_ref"]).payload
    proof = row.get("_native_proof") if ephemeral else None
    proof = proof or hydrate_sidecar_ref(state, row["native_proof_ref"]).payload
    keys = (
        "workflow_run_id",
        "task_id",
        "dispatch_id",
        "attempt_id",
        "operation_id",
        "consumer_actor",
        "source_id",
        "artifact_id",
        "artifact_sha256",
        "occurrence_id",
        "json_path",
        "selected_sha256",
        "selected_bytes",
        "page_start",
        "page_end",
        "page_total",
        "returned_bytes",
    )
    if (
        host["schema_version"] != HOST_SCHEMA
        or proof["schema_version"] != PROOF_SCHEMA
        or any(host[k] != row[k] for k in keys)
        or proof["host_ref"] != row["native_host_ref"]
        or not host["credential_generation"]
        or not host["peer_identity"]
        or host["tool_version"] not in NATIVE_CODEX_VERSIONS
        or row["production_status"] != "mounted_file_read"
        or row.get("provider_delivery_status") != "observed_in_transcript"
    ):
        return []
    sid, call, result = host["session_id"], host["call"], proof["result"]
    start, end = row["page_start"], row["page_end"]
    valid = (
        str(UUID(sid)) == sid == row["provider_session_id"] == proof["session_id"]
        and matching_call(call["payload"], host["actual_path"])
        and result["payload"]["type"] == "function_call_output"
        and call["payload"]["call_id"]
        == result["payload"]["call_id"]
        == row["provider_call_id"]
        and datetime.fromisoformat(call["timestamp"])
        <= datetime.fromisoformat(host["finished_at"])
        <= datetime.fromisoformat(result["timestamp"])
        and type(start) is int
        and type(end) is int
        and 0 <= start <= end <= len(text) == row["page_total"]
        and hashlib.sha256(text.encode()).hexdigest() == row["selected_sha256"]
        and len(text.encode()) == row["selected_bytes"]
        and len(text[start:end].encode()) == row["returned_bytes"]
    )
    if not valid:
        return []
    return [
        {
            **row,
            "page_start": start + a,
            "page_end": start + b,
            "returned_bytes": len(text[start + a : start + b].encode()),
        }
        for a, b in _intervals(result["payload"]["output"], text[start:end])
    ]


def observe_native(state, row, persist, cache):
    if row.get("native_proof_ref"):
        return row
    row = {**row, "provider_delivery_status": "unknown"}
    row.pop("_native_proof", None)
    try:
        host = hydrate_sidecar_ref(state, row["native_host_ref"]).payload
        sid, rows = session_rows(state, row["consumer_actor"], cache)
        if sid != host["session_id"]:
            return row
        call_id = host["call"]["payload"]["call_id"]
        pair = [r for r in rows if r["payload"].get("call_id") == call_id]
        if (
            len(pair) != 2
            or pair[0] != host["call"]
            or pair[1]["payload"].get("type") != "function_call_output"
        ):
            return row
        proof = {
            "schema_version": PROOF_SCHEMA,
            "host_ref": row["native_host_ref"],
            "session_id": sid,
            "result": pair[1],
        }
        if persist:
            row["native_proof_ref"] = write_immutable_json_sidecar(
                state,
                proof,
                root="native-input-observations",
                kind="native_input_observation",
                schema_version=PROOF_SCHEMA,
                created_by="read-ledger-seal",
            )
        else:
            row["_native_proof"] = proof
        row.update(
            provider_session_id=sid,
            provider_call_id=call_id,
            provider_delivery_status="observed_in_transcript",
        )
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return row
