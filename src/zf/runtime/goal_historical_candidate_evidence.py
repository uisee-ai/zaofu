"""Pin the prior candidate's admitted report for an execution-time plan revision."""

from collections.abc import Iterator
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_plan_recovery import candidate_reader_binding
from zf.runtime.product_acceptance import validate_product_acceptance_report
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def historical_candidate_reports(
    state: Path,
    events: list[ZfEvent],
    run_id: str,
    generation: str,
    package_digest: str,
) -> list[dict]:
    if not (run_id and generation and package_digest):
        return []
    plan = next(
        (
            e
            for e in events
            if e.type == "task_map.ready"
            and e.origin == "kernel"
            and e.payload.get("workflow_run_id") == run_id
            and e.payload.get("task_map_generation") == generation
            and e.payload.get("plan_artifact_package_digest") == package_digest
        ),
        None,
    )
    if not plan or not plan.payload.get("rework_of"):
        return []
    earlier = events[: events.index(plan)]
    failure = next(
        (e for e in reversed(earlier) if e.id == plan.payload["rework_of"]), None
    )
    if (
        not failure
        or failure.origin != "kernel"
        or failure.type not in {"test.failed", "verify.failed"}
        or failure.payload.get("workflow_run_id") != run_id
        or failure.payload.get("source_commit") != plan.payload.get("source_commit")
    ):
        return []
    earlier = earlier[: earlier.index(failure)]
    operations = failure.payload.get("candidate_verify_operation_ids")
    if not isinstance(operations, list) or not all(
        isinstance(op, str) for op in operations
    ):
        return []
    reports = []
    for op in dict.fromkeys(operations):
        try:
            scope, body = candidate_reader_binding(
                state, earlier, fanout_id=failure.payload["fanout_id"], operation_id=op
            )
            if (
                scope["workflow_run_id"] != run_id
                or scope["task_map_generation"]
                != failure.payload.get("task_map_generation")
                or scope["plan_artifact_package_digest"]
                != failure.payload.get("plan_artifact_package_digest")
                or scope["candidate_event_id"]
                != failure.payload.get("candidate_event_id")
                or scope["source_commit"] != failure.payload["source_commit"]
                or scope["task_map_generation"] == generation
            ):
                continue
            admitted = next(
                e
                for e in reversed(earlier)
                if e.type == "workflow.call.result.admitted"
                and e.payload.get("workflow_run_id") == run_id
                and e.payload.get("operation_id") == op
                and e.payload.get("control_result_ref") == scope["control_result_ref"]
            )
            refs = {
                name: {"ref": body[name + "_ref"], "sha256": body[name + "_digest"]}
                for name in ("product_acceptance_spec", "product_acceptance_report")
            }
            envelope = admitted.payload["envelope_ref"]
            if not isinstance(envelope, dict) or not all(
                d.get("sha256") for d in (*refs.values(), envelope)
            ):
                continue
            admitted_body = hydrate_sidecar_ref(state, envelope).payload
            control = (
                admitted_body.get("control_result")
                if isinstance(admitted_body, dict)
                else None
            )
            if not isinstance(control, dict) or any(
                control.get(k) != scope["control_result_ref"][k]
                for k in ("ref", "sha256")
            ):
                continue
            spec = hydrate_sidecar_ref(state, refs["product_acceptance_spec"]).payload
            report = hydrate_sidecar_ref(
                state, refs["product_acceptance_report"]
            ).payload
            if not isinstance(spec, dict) or not isinstance(report, dict):
                continue
            validate_product_acceptance_report(
                report,
                spec=spec,
                expected={
                    "workflow_run_id": run_id,
                    "task_map_generation": scope["task_map_generation"],
                    "target_commit": scope["source_commit"],
                    "candidate_ref": scope["candidate_ref"],
                    "product_acceptance_spec_ref": refs["product_acceptance_spec"][
                        "ref"
                    ],
                    "product_acceptance_spec_digest": refs["product_acceptance_spec"][
                        "sha256"
                    ],
                },
            )
        except (ValueError, OSError, KeyError, StopIteration):
            continue
        reports.append(
            {
                "admitted_event_id": admitted.id,
                "failure_event_id": failure.id,
                "candidate_event_id": scope["candidate_event_id"],
                "target_commit": scope["source_commit"],
                "task_map_generation": scope["task_map_generation"],
                "plan_artifact_package_digest": scope["plan_artifact_package_digest"],
                "verdict": body["verdict"],
                "envelope": {"ref": envelope["ref"], "sha256": envelope["sha256"]},
                "control_result": {
                    k: scope["control_result_ref"][k] for k in ("ref", "sha256")
                },
                **refs,
            }
        )
    return reports


def _pinned_reports(state: Path, closure: dict) -> list[dict]:
    if not closure.get("closure_fact_digest"):
        return []
    fact = hydrate_sidecar_ref(
        state,
        {
            "ref": closure["closure_fact_ref"],
            "sha256": closure["closure_fact_digest"],
        },
    ).payload
    return (
        fact.get("historical_candidate_reports", []) if isinstance(fact, dict) else []
    )


def historical_judge_sources(state: Path, closure: dict) -> Iterator[dict[str, str]]:
    for index, report in enumerate(_pinned_reports(state, closure), start=1):
        for kind in (
            "envelope",
            "product_acceptance_spec",
            "product_acceptance_report",
        ):
            descriptor = report[kind]
            yield {
                "ref": descriptor["ref"],
                "digest": descriptor["sha256"],
                "source_id": f"historical-candidate-{index}-{kind}",
                "kind": kind,
            }


def pinned_historical_result_refs(
    state: Path, events: list[ZfEvent], closure: dict
) -> set[str]:
    """Accept only admitted predecessor envelopes pinned at this exact Judge dispatch."""
    try:
        reports = historical_candidate_reports(
            state,
            events,
            closure["workflow_run_id"],
            closure["task_map_generation"],
            closure["plan_artifact_package_digest"],
        )
        if not reports or reports != _pinned_reports(state, closure):
            return set()
        return {r["envelope"]["ref"] for r in reports}.intersection(
            closure["input_result_refs"]
        )
    except (OSError, KeyError, ValueError):
        return set()
