"""Recover missing tool telemetry from the current registry-bound native session."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yaml

from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


def observe_completed_tools(state_dir: Path, role: str, pending: dict, closed) -> list[dict]:
    """Witness exact call/result pairs; absent or ambiguous evidence stays pending.

    A controller restart can attach its telemetry tailer at EOF between use and
    result. This observes historical completion, never replays a tool or claims
    that its result passed a product gate or remains in the model's context.
    """
    if not pending or closed.payload.get("backend") != "codex":
        return []
    try:
        registry = RoleSessionRegistry(state_dir / "role_sessions.yaml", state_dir.parent)
        path, session_id = registry.get_path(role), str(registry.get(role) or "")
        if path is None or not session_id:
            return []
        calls, outputs = {}, {}
        with path.open() as stream:
            first = json.loads(stream.readline())
            if (first.get("type") != "session_meta"
                    or first.get("payload", {}).get("id") != session_id):
                return []
            for line in stream:
                if not line.endswith("\n"):
                    break
                row = json.loads(line)
                if not isinstance(row, dict):
                    continue
                if row.get("type") == "session_meta":
                    return []  # Mixed/rotated identity cannot witness this call.
                payload = row.get("payload")
                if row.get("type") != "response_item" or not isinstance(payload, dict):
                    continue
                call_id = payload.get("call_id")
                if call_id not in pending:
                    continue
                if payload.get("type") == "function_call":
                    calls.setdefault(call_id, []).append(payload)
                elif payload.get("type") == "function_call_output" and call_id in calls:
                    outputs.setdefault(call_id, []).append(row)
        current = RoleSessionRegistry(registry.path, state_dir.parent)
        if str(current.get(role) or "") != session_id or current.get_path(role) != path:
            return []
        observations = []
        for call_id, use in pending.items():
            if len(calls.get(call_id, [])) != 1 or len(outputs.get(call_id, [])) != 1:
                continue
            call, result = calls[call_id][0], outputs[call_id][0]
            arguments = call.get("arguments")
            if (call.get("name") != use.payload.get("tool") or not isinstance(arguments, str)
                    or arguments[:2000] != use.payload.get("input")
                    or "output" not in result["payload"]):
                continue
            if not (datetime.fromisoformat(use.ts) <= datetime.fromisoformat(result["timestamp"])
                    <= datetime.fromisoformat(closed.ts)):
                continue
            witness = write_immutable_json_sidecar(state_dir, {
                "schema_version": "provider-tool-completion-observation.v1",
                "session_id": session_id, "role_instance": role,
                "tool_use_event_id": use.id, "closed_event_id": closed.id,
                "call": call, "result": result,
            }, root="provider-tool-observations", kind="provider_tool_observation",
                schema_version="provider-tool-completion-observation.v1", created_by="reader-recovery")
            observations.append({"tool_use_id": call_id, "observation_ref": witness})
        return observations
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        return []
