"""Recover authored reader handoffs from the admitted occurrence, never scratch."""

from typing import Any

from zf.runtime.call_result_envelope import hydrate_call_result_envelope
from zf.runtime.call_result_runtime import hydrate_admitted_control_result


def admitted_fanout_report(runtime: Any, descriptor: dict) -> dict:
    control = hydrate_admitted_control_result(runtime.state_dir, descriptor)
    if control.get("schema_version") != "verification-result.v1":
        return control
    envelope = hydrate_call_result_envelope(runtime.state_dir, descriptor)
    identity = envelope.get("identity") or {}
    source_ids = envelope.get("source_event_ids") or []
    # Only the exact occurrence bound by the immutable admitted envelope may
    # restore semantic fields omitted from the compact verification contract.
    for event in runtime.event_log.read_all():
        if event.id not in source_ids:
            continue
        payload = event.payload or {}
        if any(
            identity.get(key) and payload.get(key) != identity[key]
            for key in ("operation_id", "request_hash", "fanout_id", "child_id")
        ):
            continue
        report = payload.get("report")
        if isinstance(report, dict):
            return {
                **report,
                **control,
                "status": "passed" if control.get("verdict") == "passed" else "failed",
            }
    return control
