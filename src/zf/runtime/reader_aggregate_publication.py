"""Exact generic aggregate publication, including reviewed rebuild crash replay."""
from dataclasses import asdict

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def publication_payload(*, manifest, artifact_payload, final_status, publish_event,
                        rebuild_request, rebuild_of, recovery_generation,
                        failure_recovery, failure_findings, aggregate_payload):
    fanout_id = manifest['fanout_id']
    trace_id = str(manifest.get('trace_id') or '')
    stage_id = str(manifest.get('stage_id') or '')
    pdd_id = str(manifest.get('pdd_id') or '')
    feature_id = str(manifest.get('feature_id') or '')
    children = [child for child in manifest.get('children', []) if isinstance(child, dict)]
    publish_payload = {
        "fanout_id": fanout_id,
        "trace_id": trace_id,
        "stage_id": stage_id,
        "pdd_id": pdd_id,
        "feature_id": feature_id,
        "status": final_status,
        "target_ref": manifest.get("target_ref", ""),
        "child_count": len(children),
        **artifact_payload,
    }
    if rebuild_request is not None:
        publish_payload.update({
            "rebuild_of": rebuild_of,
            "rebuild_request_event_id": rebuild_request.id,
            "recovery_generation": recovery_generation,
        })
    if final_status == "failed":
        publish_payload.update(failure_recovery)
        publish_payload.setdefault("findings", failure_findings)
        publish_payload.setdefault(
            "failed_children",
            aggregate_payload.get("failed_children", []),
        )
        # R5: judge.failed reached the operator with an empty reason.
        # The aggregate knows which children failed and what they
        # reported — say so instead of shipping "".
        if not str(publish_payload.get("reason") or "").strip():
            failed_children = publish_payload.get("failed_children") or []
            first_message = ""
            if failure_findings and isinstance(failure_findings[0], dict):
                first_message = str(
                    failure_findings[0].get("message") or "",
                )[:160]
            groups = publish_payload.get("reader_outcomes") or {}
            if groups:
                counts = ", ".join(f"{outcome}={len(children)}" for outcome, children in sorted(groups.items()))
                first_message = f"{counts}. {first_message}" if first_message else counts
            publish_payload["reason"] = (
                f"{len(failed_children)}/{len(children)} children failed"
                + (f": {first_message}" if first_message else "")
            )
        # publish_event is config-driven (the stage's failure_event may
        # name any contract event, e.g. lane.stage.failed). Forge the
        # contract's required keys present — value may be empty, same
        # rule as the lane-pipeline emitter — else a blocking
        # discriminator replaces the failure with discriminator.failed
        # and rework never sees it (R6: verify-timeout wedged 25min).
        from zf.core.config.schema_profiles import union_required_keys

        target_ref = str(publish_payload.get("target_ref") or "")
        for key in union_required_keys(publish_event):
            if key == "task_id" and target_ref.startswith("task/"):
                publish_payload.setdefault(
                    "task_id", target_ref.split("/", 1)[1],
                )
            else:
                publish_payload.setdefault(key, "")
    return publish_payload


def freeze_publication(runtime, event):
    return write_immutable_json_sidecar(runtime.state_dir, asdict(event),
        root='fanout-rebuild-publications', kind='fanout_rebuild_publication',
        schema_version='fanout-rebuild-publication.v1', created_by='kernel-aggregate',
        source_event_id=event.causation_id)


def publish_prepared(runtime, aggregate, events=None):
    descriptor = aggregate.payload.get('rebuild_publication_ref') or {}
    body = hydrate_sidecar_ref(runtime.state_dir, descriptor)
    if not body.ok or not isinstance(body.payload, dict):
        raise ValueError('rebuild publication sidecar is unavailable')
    event = ZfEvent.from_dict(body.payload)
    expected_type = aggregate.payload.get('success_event') or aggregate.payload.get('failure_event')
    if (aggregate.origin != 'kernel' or event.origin != 'kernel'
            or event.type != expected_type or event.causation_id != aggregate.id
            or event.correlation_id != aggregate.correlation_id
            or any(event.payload.get(key) != aggregate.payload.get(key) for key in (
                'fanout_id', 'rebuild_of', 'rebuild_request_event_id', 'recovery_generation',
                'workflow_run_id', 'run_id', 'trace_id'))):
        raise ValueError('rebuild publication identity differs from its aggregate')
    for existing in (events if events is not None else runtime.event_log.read_all()):
        if existing.id == event.id:
            if existing.origin != 'kernel' or existing.type != event.type or existing.payload != event.payload:
                raise ValueError('rebuild publication ID is already bound to another body')
            return existing
        if (existing.origin == 'kernel' and existing.type == 'discriminator.failed'
                and existing.payload.get('blocked_event_id') == event.id
                and existing.payload.get('blocked_event_type') == event.type
                and existing.payload.get('blocked_event_payload') == event.payload):
            return existing
    return runtime.event_writer.append(event)
