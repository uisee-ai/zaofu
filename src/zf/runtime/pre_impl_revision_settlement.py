"""Fence an in-flight OA Plan revision when its reviewed parent changes."""
from __future__ import annotations

from zf.core.events.log import EventLedgerAdvanced
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_pre_impl_revision import (
    is_pre_impl_revision_trigger, pre_impl_revision_stale_reason,
)


class RevisionSettlementDeferred(ValueError):
    """Stop this settlement tick. The existing idle sweep retries pending publications."""


class RevisionNoLongerCurrent(RevisionSettlementDeferred):
    """The owner or a successor has already settled this revision's parent."""


def _is_revision_manifest(manifest: dict) -> bool:
    payload = manifest.get("trigger_payload") or {}
    return bool(manifest.get("trigger_event_id")
                and payload.get("revision_trigger_event_id") == manifest["trigger_event_id"]
                and payload.get("replan_classification") == "oa_pre_impl_revision")


def _check(runtime, manifest, events, *, synth: ZfEvent | None):
    trigger = next((e for e in events if e.id == manifest["trigger_event_id"]), None)
    if not trigger or not is_pre_impl_revision_trigger(trigger):
        raise RevisionNoLongerCurrent("revision_trigger_missing")
    if synth is not None:
        # A crash can leave this exact admitted Critic result between package
        # admission and task_map.ready. Ignore only its own transition while
        # validating the parent. A later independent admission remains visible.
        own_transitions = {
            e.id for e in events
            if e.origin == "kernel" and e.causation_id == synth.id
            and e.payload.get("workflow_run_id") == trigger.payload["workflow_run_id"]
            and ((e.type == "plan.artifact_package.admitted"
                  and e.payload.get("supersedes_package_digest") == trigger.payload["plan_artifact_package_digest"])
                 or (e.type == "plan.artifact_package.superseded"
                     and e.payload.get("package_digest") == trigger.payload["plan_artifact_package_digest"]))
        }
        if own_transitions:
            events = [e for e in events if e.id not in own_transitions]
    reason = pre_impl_revision_stale_reason(runtime, trigger, manifest["stage_id"], events=events)
    if reason in {"run_paused", "dispatch_paused"}:
        # Existing pause semantics accept already-running results. New Critic
        # dispatch remains paused and is picked up by the normal aggregate sweep.
        return synth is not None
    if reason:
        runtime._cancel_superseded_fanout_manifest(
            fanout_id=manifest["fanout_id"], manifest=manifest, reason=reason,
            superseded_by="", source="oa_pre_impl_revision_currentness",
        )
        raise RevisionNoLongerCurrent(reason)
    return True


def revision_can_continue(runtime, manifest: dict, *, synth: ZfEvent | None = None) -> bool:
    if not _is_revision_manifest(manifest):
        return True
    if (manifest.get("aggregate") or {}).get("status") in {"completed", "failed", "timed_out", "cancelled"}:
        return False
    try:
        return _check(runtime, manifest, runtime.event_log.read_all(), synth=synth)
    except RevisionNoLongerCurrent:
        return False


def revision_settlement_writer(runtime, manifest: dict, synth: ZfEvent):
    if not _is_revision_manifest(manifest):
        return None
    return RevisionSettlementWriter(runtime, manifest, synth)


class RevisionSettlementWriter:
    """Publish only while the same source and Owner authorization are current."""

    def __init__(self, runtime, manifest, synth):
        self.runtime = runtime
        self.manifest = manifest
        self.synth = synth

    def append(self, event: ZfEvent):
        for _ in range(3):
            offset = self.runtime.event_log.current_offset()
            events = self.runtime.event_log.read_all()
            _check(self.runtime, self.manifest, events, synth=self.synth)
            already_published = next((e for e in events if e.id == event.id), None)
            if event.type in {"fanout.aggregate.started", "fanout.aggregate.completed",
                              "goal.claim_set.pinned", "plan.artifact_package.admitted", "plan.artifact_package.superseded"}:
                prior = next((e for e in events if e.type == event.type and e.origin == "kernel"
                              and e.causation_id == event.causation_id), None)
                if prior:
                    metadata = {"revision_publication_ref", "revision_publication_id"}
                    if ({k: v for k, v in prior.payload.items() if k not in metadata}
                            != {k: v for k, v in event.payload.items() if k not in metadata}):
                        raise ValueError("Plan revision settlement conflicts with its earlier publication")
                    return prior
            if already_published:
                if (already_published.type != event.type or already_published.payload != event.payload
                        or already_published.origin != "kernel" or already_published.causation_id != event.causation_id):
                    raise ValueError("OA revision publication identity already belongs to another event")
                return already_published
            try:
                return self.runtime.event_writer.append(event, expected_offset=offset,
                    allow_intervening=frozenset({"worker.heartbeat", "agent.text"}))
            except EventLedgerAdvanced:
                # Recheck state-changing occurrences under a fresh offset.
                continue
        raise RevisionSettlementDeferred("revision settlement deferred after ledger contention")

def resume_revision_publications(runtime, events, pending):
    """Recover only a reviewed revision's frozen result left between aggregate and publication."""
    by_id = {e.id: e for e in events}
    blocked = {e.payload.get("blocked_event_id") for e in events if e.type == "discriminator.failed"}
    cancelled = {e.payload.get("fanout_id") for e in events if e.type == "fanout.cancelled"}
    from zf.runtime.reader_aggregate_publication import publish_prepared

    for aggregate in pending:
        p = aggregate.payload
        if p["revision_publication_id"] in by_id or p["revision_publication_id"] in blocked or p["fanout_id"] in cancelled:
            continue
        synth = by_id.get(aggregate.causation_id)
        if not synth or synth.type != "fanout.synth.completed" or synth.payload.get("fanout_id") != p["fanout_id"]:
            continue
        manifest = runtime._fanout_manifest(p["fanout_id"])
        if not manifest or not _is_revision_manifest(manifest) or aggregate.causation_id != p.get("synth_event_id"):
            continue
        try:
            publish_prepared(runtime, aggregate, events, revision_writer=revision_settlement_writer(runtime, manifest, synth))
            return True
        except RevisionSettlementDeferred:
            continue
    return False
