"""Optional tool-free local planner for one existing implementation worker."""

from __future__ import annotations

import http.client
import json
import time
from pathlib import Path

from zf.core.cost.tracker import CostTracker
from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import (
    canonical_json_sha256,
    write_immutable_json_sidecar,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation

REASONS = ("initial", "repeated_failure", "interface_deviation", "design_choice")
ACTOR = "worker-model-adapter"


def model_profile(config, actor):
    meta = getattr(getattr(config, "workflow", None), "flow_metadata", {}) or {}
    raw = (meta.get("worker_model_planners") or {}).get(actor)
    if not raw or not raw.get("enabled"):
        return {}
    if (
        raw.get("enabled") is not True
        or not isinstance(raw.get("model"), str)
        or not raw["model"].strip()
        or len(raw["model"]) > 100
    ):
        raise ValueError("local planner needs an explicitly enabled model")
    result = {"model": raw["model"]}
    for key, default, limit in (
        ("port", 11434, 65535),
        ("max_calls", 3, 16),
        ("max_output_tokens", 1024, 8192),
        ("timeout_seconds", 60, 600),
    ):
        value = raw.get(key, default)
        if type(value) is not int or not 1 <= value <= limit:
            raise ValueError(f"invalid worker planner {key}")
        result[key] = value
    return result


def freeze_model_profile(runtime, operation_id, payload):
    payload.pop("worker_model_planner", None)
    if payload.get("task_pipeline_stage") != "impl":
        return
    state = Path(runtime.state_dir)
    op = load_workflow_operation(runtime.event_log, operation_id)
    profile = (
        hydrate_sidecar_ref(state, op["request_ref"])
        .payload["request"]
        .get("worker_model_planner", {})
        if op and op.get("request_ref")
        else model_profile(runtime.config, payload.get("role_instance"))
    )
    if profile:
        payload["worker_model_planner"] = profile


def local_plan(profile, packet):
    return local_chat(profile, packet, "Read the bound implementation inputs. Return steps, "
                      "checks and hand-back conditions. You have no tools or approval authority.")


def local_chat(profile, packet, instruction):
    connection = http.client.HTTPConnection(
        "127.0.0.1", profile["port"], timeout=profile["timeout_seconds"]
    )
    data = {
        "model": profile["model"],
        "stream": False,
        "options": {"num_predict": profile["max_output_tokens"]},
        "messages": [
            {
                "role": "system",
                "content": instruction,
            },
            {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
        ],
    }
    try:
        connection.request(
            "POST",
            "/api/chat",
            json.dumps(data).encode(),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"local planner returned HTTP {response.status}")
        raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError("local planner response too large")
        value = json.loads(raw)
        if (
            value.get("done") is not True
            or value.get("model") != profile["model"]
            or value["message"].get("role") != "assistant"
            or value["message"].get("tool_calls")
            or not isinstance(value["message"].get("content"), str)
            or not value["message"]["content"].strip()
            or any(
                type(value.get(k)) is not int or value[k] < 0
                for k in ("prompt_eval_count", "eval_count")
            )
        ):
            raise ValueError(
                "local planner response lacks a bound text and usage receipt"
            )
        return {
            "advice": value["message"]["content"],
            "input_tokens": value["prompt_eval_count"],
            "output_tokens": value["eval_count"],
            "model": value["model"],
        }
    finally:
        connection.close()


def consult(
    state,
    config,
    log,
    writer,
    manifest,
    identity,
    reason,
    refs,
    *,
    authorize,
    invoke=local_plan,
):
    start = time.monotonic()

    def current():
        if authorize() != identity:
            raise ValueError("worker identity changed")
        op = load_workflow_operation(log, identity["operation_id"]) or {}
        if (
            op.get("status") != "running"
            or op.get("active_attempt_id") != manifest["attempt_id"]
            or op.get("role_instance") != identity["actor"]
            or op.get("admitted_call_result_ref")
            or op.get("call_result_admitted_event_id")
        ):
            raise ValueError("implementation attempt is no longer current")
        request = hydrate_sidecar_ref(state, op["request_ref"]).payload["request"]
        profile = request.get("worker_model_planner")
        if (
            not profile
            or request.get("task_pipeline_stage") != "impl"
            or profile != model_profile(config, identity["actor"])
        ):
            raise ValueError(
                "single-model execution: planner capability is unavailable"
            )
        return op, profile, request

    op, profile, request = current()
    if reason not in REASONS or not 1 <= len(set(refs)) == len(refs) <= 8:
        raise ValueError("provide one to eight distinct authorized source selectors")
    if reason == "repeated_failure" and len(refs) < 2:
        raise ValueError("repeated failure hand-back requires both failure receipts")
    from zf.runtime.artifact_read_snapshot import open_selection

    evidence = []
    for source_id, artifact_id, selector in refs:
        selection = open_selection(
            state,
            manifest=manifest,
            identity=identity,
            row={
                "source_id": source_id,
                "artifact_id": artifact_id,
                "json_path": selector,
            },
        )
        if (
            selection.byte_count + sum(len(e["text"].encode()) for e in evidence)
            > 32768
        ):
            raise ValueError("choose smaller frozen selectors for the planner")
        evidence.append(
            {
                "source": source_id,
                "artifact": artifact_id,
                "selector": selector,
                "digest": selection.digest,
                "text": selection.read(),
            }
        )
    packet = {
        "attempt": manifest["attempt_id"],
        "operation": op["operation_id"],
        "request_hash": op["request_hash"],
        "target": request.get("target_commit"),
        "contract_digest": request.get("contract_snapshot_digest"),
        "reason": reason,
        "inputs": evidence,
    }
    key = canonical_json_sha256(packet)

    def emit(kind, **fields):
        return writer.append(
            ZfEvent(
                type=kind,
                actor=ACTOR,
                origin="kernel",
                task_id=manifest.get("task_id"),
                correlation_id=manifest["workflow_run_id"],
                payload={
                    "key": key,
                    "operation_id": op["operation_id"],
                    "attempt_id": manifest["attempt_id"],
                    "worker": identity["actor"],
                    "reason": reason,
                    **fields,
                },
            )
        )

    with locked_path(
        state / "locks" / ("worker-model-" + canonical_json_sha256(op["operation_id"]))
    ):
        current()
        events = [
            e
            for e in log.read_all()
            if e.actor == ACTOR
            and e.origin == "kernel"
            and e.type.startswith("worker.model.")
            and e.payload.get("operation_id") == op["operation_id"]
            and e.payload.get("attempt_id") == manifest["attempt_id"]
        ]
        same = [e for e in events if e.payload["key"] == key]
        returned = next((e for e in same if e.type == "worker.model.returned"), None)
        if returned:
            return hydrate_sidecar_ref(state, returned.payload["result_ref"]).payload
        if same:
            raise ValueError(
                "planner call already claimed; consult its receipt or resume the single-model worker"
            )
        completed = {
            e.payload["key"] for e in events if e.type == "worker.model.returned"
        }
        if any(
            e.type == "worker.model.claimed" and e.payload["key"] not in completed
            for e in events
        ):
            raise ValueError(
                "previous planner outcome needs reconciliation; original executor remains assigned"
            )
        if (
            sum(e.type == "worker.model.claimed" for e in events)
            >= profile["max_calls"]
        ):
            raise ValueError(
                "planner allowance exhausted; continue with the assigned executor"
            )
        from zf.runtime.workflow_budget_status import operation_budget_status

        budget = operation_budget_status(state, config, op["operation_id"])
        limits = budget["dimensions"]
        input_bound = len(json.dumps(packet, ensure_ascii=False).encode()) + 256
        expected = {
            "tokens": input_bound + profile["max_output_tokens"],
            "wall_clock": profile["timeout_seconds"],
            "usage_samples": 1,
        }
        role_budget = next(
            (
                getattr(r, "budget_usd", None)
                for r in getattr(config, "roles", ())
                if r.instance_id == identity["actor"]
            ),
            None,
        )
        if (
            getattr(config, "global_budget_usd", None) is not None
            or role_budget is not None
            or "usd" in limits
            or budget["exceeded"]
            or any(
                k in limits
                and (limits[k]["remaining"] is None or limits[k]["remaining"] < n)
                for k, n in expected.items()
            )
        ):
            raise ValueError("operation budget requires controlled recovery")
        emit(
            "worker.model.claimed",
            model=profile["model"],
            mode="local_text_only",
            max_output_tokens=profile["max_output_tokens"],
        )
    try:
        result = invoke(profile, packet)
    except (OSError, http.client.HTTPException, ValueError, KeyError) as exc:
        emit(
            "worker.model.unavailable",
            owner="run_manager",
            status="outcome_unknown",
            elapsed_ms=round(1000 * (time.monotonic() - start)),
            error_type=type(exc).__name__,
        )
        raise ValueError(
            "local planner unavailable; continue in the original worker"
        ) from exc
    result.update(
        authorizes_execution=False,
        independent_verification_required=True,
        elapsed_ms=round(1000 * (time.monotonic() - start)),
        reason=reason,
        request_hash=op["request_hash"],
    )
    ref = write_immutable_json_sidecar(
        state,
        result,
        root="worker-model-advice",
        kind="worker_model_advice",
        schema_version="worker-model-advice.v1",
        created_by=ACTOR,
    )
    CostTracker(state / "cost.jsonl").record_usage(
        identity["role"],
        result["input_tokens"],
        result["output_tokens"],
        result["model"],
        instance_id=identity["actor"],
        backend="ollama",
        usage_sample_id=key,
    )
    emit(
        "worker.model.returned",
        result_ref=ref,
        model=profile["model"],
        elapsed_ms=result["elapsed_ms"],
    )
    current()
    return result
