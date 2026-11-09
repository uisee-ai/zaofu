"""Separate CLI production from observable provider transcript delivery.

Neither a successful write nor a transcript entry proves model comprehension.
Unavailable/truncated transcripts remain unknown; they never imply delivery.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import yaml

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.artifact_output_match import complete_output_observed, observable_outputs


def emit_artifact_output(
    state_dir: Path, output: str, receipts: list[dict[str, Any]], stream: TextIO,
) -> None:
    from zf.runtime.artifact_read_ledger import append_artifact_read

    # Persist the exact body first, but do not claim successful production until
    # the complete write AND flush succeed. A broken pipe leaves no read receipt.
    descriptor = write_immutable_json_sidecar(
        state_dir, {"schema_version": "artifact-output.v1", "body": output},
        root="artifact-outputs", kind="artifact_output",
        schema_version="artifact-output.v1", created_by="artifact-read",
    )
    body = output + "\n"
    if stream.write(body) != len(body):
        raise OSError("artifact output write was incomplete")
    stream.flush()
    for row in receipts:
        append_artifact_read(state_dir, {
            **row, "production_status": "stdout_flushed",
            "provider_delivery_status": "unknown",
            "output_ref": descriptor,
            "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
        })


def observe_artifact_delivery(state_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich sealed evidence from the registry-bound Codex transcript only.

    Hook output is producer-side and cannot establish this observation. Other
    providers stay explicitly unknown until their input surface is observable.
    This is an evidence annotation, not a new result-admission state machine.
    """
    from zf.core.state.role_sessions import RoleSessionRegistry

    try:
        registry = RoleSessionRegistry(state_dir / "role_sessions.yaml", state_dir.parent)
    except (OSError, ValueError, yaml.YAMLError):
        registry = None
    observations: dict[str, list[tuple[str, str, str, str, str]]] = {}
    bodies: dict[str, str] = {}
    result = []
    for original in rows:
        row = dict(original)
        if row.get("production_status") != "stdout_flushed":
            result.append(row)
            continue
        row["provider_observation_scope"] = "historical_tool_output"
        row["current_context_availability"] = "unknown"
        # A previously sealed observation survives session rotation. Its bound
        # immutable witness proves historical delivery, not current memory.
        witness = row.get("provider_observation_ref")
        if row.get("provider_delivery_status") == "observed_in_transcript" and isinstance(witness, dict):
            try:
                observed = hydrate_sidecar_ref(state_dir, witness).payload
                body = hydrate_sidecar_ref(state_dir, row["output_ref"]).payload["body"]
                if (observed["session_id"] == row["provider_session_id"]
                        and observed["call_id"] == row["provider_call_id"]
                        and hashlib.sha256(body.encode()).hexdigest() == row["output_sha256"]
                        and observed["output"] in _observable_outputs(observed.get("raw_output", observed["output"]))
                        and complete_output_observed(body, observed["output"])):
                    result.append(row)
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                pass
        row["provider_delivery_status"] = "unknown"
        for key in ("provider_observation_ref", "provider_session_id", "provider_call_id"):
            row.pop(key, None)
        actor = str(row.get("consumer_actor") or "")
        if actor not in observations:
            observations[actor] = []
            try:
                path = registry.get_path(actor) if registry else None
                if path and row.get("consumer_provider") == "codex":
                    observations[actor] = _codex_outputs(path, str(registry.get(actor) or ""))
            except (OSError, ValueError):
                pass
        try:
            descriptor = row["output_ref"]
            digest = row["output_sha256"]
            if digest not in bodies:
                body = hydrate_sidecar_ref(state_dir, descriptor).payload["body"]
                if hashlib.sha256(body.encode()).hexdigest() != digest:
                    raise ValueError("artifact output digest mismatch")
                bodies[digest] = body
            for session_id, call_id, timestamp, text, raw_text in observations[actor]:
                if datetime.fromisoformat(timestamp) < datetime.fromisoformat(row["read_at"]):
                    continue
                # Tool wrappers add timing/exit metadata. Require the entire
                # exact output at line boundaries, never a prefix or summary.
                if complete_output_observed(bodies[digest], text):
                    observed = write_immutable_json_sidecar(
                        state_dir, {"schema_version": "provider-output-observation.v1",
                                    "session_id": session_id, "call_id": call_id,
                                    "timestamp": timestamp, "output": text, "raw_output": raw_text},
                        root="provider-output-observations", kind="provider_output_observation",
                        schema_version="provider-output-observation.v1", created_by="read-ledger-seal",
                    )
                    row.update(provider_delivery_status="observed_in_transcript",
                               provider_session_id=session_id, provider_call_id=call_id,
                               provider_observation_ref=observed)
                    break
        except (OSError, ValueError, KeyError, TypeError):
            pass
        result.append(row)
    return result


def _observable_outputs(output: str) -> list[str]:
    return observable_outputs(output)


def _codex_outputs(path: Path, expected_session_id: str) -> list[tuple[str, str, str, str, str]]:
    outputs = []
    session_id = ""
    try:
        with path.open() as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue  # The active transcript may end in a partial line.
                if not isinstance(entry, dict):
                    continue
                payload = entry.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                if entry.get("type") == "session_meta":
                    session_id = str(payload.get("id") or "")
                if (session_id and session_id == expected_session_id and entry.get("timestamp") and entry.get("type") == "response_item"
                        and payload.get("type") in {"function_call_output", "custom_tool_call_output"}
                        and payload.get("call_id")):
                    output = payload.get("output")
                    if isinstance(output, list):
                        output = "\n".join(block["text"] for block in output
                                           if isinstance(block, dict)
                                           and block.get("type") == "input_text"
                                           and isinstance(block.get("text"), str))
                    if isinstance(output, str):
                        outputs.extend((session_id, payload["call_id"], entry["timestamp"], text, output)
                                       for text in _observable_outputs(output))
    except OSError:
        return []
    return outputs
