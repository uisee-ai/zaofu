"""Bind native evaluation evidence to the existing member review protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.state.locks import locked_path
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.runtime.channel_synthesis_currentness import current_synthesis_consensus
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref, write_sidecar_json


def evaluation_feedback_documents(state_dir: Path, refs: dict[str, Any]):
    """Verify the complete report, its frozen input identity, and PRD binding."""
    if not (refs.get("evaluation_report") or {}).get("sha256"):
        raise ValueError("evaluation report digest is required")
    report = hydrate_sidecar_ref(state_dir, refs.get("evaluation_report") or {})
    body = report.payload
    if not isinstance(body, dict) or body.get("request_id") != refs.get("evaluation_request_id"):
        raise ValueError("evaluation report request mismatch")
    if not body.get("manifest_digest"):
        raise ValueError("evaluation manifest digest is required")
    manifest = hydrate_sidecar_ref(state_dir, {
        "ref": body.get("manifest_ref"), "sha256": body.get("manifest_digest"),
    })
    frozen = manifest.payload
    if not isinstance(frozen, dict) or any(
        (frozen.get("context") or {}).get(key) != refs.get(key)
        for key in ("channel_id", "thread_id")
    ) or any((frozen.get("target") or {}).get(key) != refs.get(expected)
             for key, expected in (("ref", "artifact_ref"), ("revision", "prd_revision"))):
        raise ValueError("evaluation report target mismatch")
    return report, manifest


def publish_evaluation_feedback(host: Any, request: Any) -> None:
    """Dispatch findings once; semantics remain with an existing required signer."""
    payload = request.payload or {}
    request_id = str(payload.get("request_id") or "")
    channel_id = str(payload.get("channel_id") or "")
    thread_id = str(payload.get("thread_id") or "main")
    with locked_path(Path(host.state_dir) / "locks" / f"channel-evaluation-feedback-{request_id}.lock"):
        events = host.event_log.read_all()
        if any(row.type in {"channel.prd_evaluation.feedback.reviewed", "channel.prd_evaluation.feedback.skipped"}
               and (row.payload or {}).get("request_id") == request_id for row in events):
            return
        terminal = next((row for row in reversed(events)
                         if row.type == "output.evaluation.completed"
                         and (row.payload or {}).get("request_id") == request_id), None)
        if terminal is None:
            return
        result = terminal.payload or {}
        refs = {key: payload.get(key) for key in (
            "channel_id", "thread_id", "prd_revision", "artifact_ref", "artifact_digest")}
        refs.update(evaluation_request_id=request_id, evaluation_report={
            "ref": result.get("report_ref"), "sha256": result.get("report_digest"),
        })
        report, _ = evaluation_feedback_documents(Path(host.state_dir), refs)
        channel = project_channel(host.state_dir, channel_id) or {}
        if channel.get("discussion", {}).get("mode") == "libre_discuss":
            return  # Preserve the readable evaluation; revisions remain an explicit user action.
        consensus = current_synthesis_consensus(channel, thread_id)
        session = (channel.get("discussions") or {}).get(thread_id) or {}
        reason = ""
        if (session.get("state") == "idle" and session.get("closed_event_id")) or consensus.get("human_confirmed"):
            reason = "owner_confirmed_or_closed"
        elif any(consensus.get(key) != payload.get(key) for key in ("artifact_ref", "artifact_digest")):
            reason = "stale_prd"
        elif not report.payload.get("findings") and report.payload.get("verdict") == "PASS":
            reason = "no_findings"
        members = [row for row in channel.get("members") or []
                   if row.get("member_id") in (consensus.get("required_signers") or [])
                   and row.get("member_id") != consensus.get("proposed_by")
                   and row.get("status") != "removed"]
        members.sort(key=lambda row: row.get("channel_role") != "critic")
        if not members and not reason:
            reason = "no_required_reviewer"
        if reason:
            host.event_writer.emit("channel.prd_evaluation.feedback.skipped", actor="orchestrator-reactor",
                                   causation_id=terminal.id, correlation_id=channel_id,
                                   payload={**payload, "reason": reason})
            return
        reviewer = str(members[0]["member_id"])
        feedback = next((row for row in events if row.type == "channel.prd_evaluation.feedback.requested"
                         and (row.payload or {}).get("request_id") == request_id), None)
        if feedback is None:
            feedback = host.event_writer.emit(
                "channel.prd_evaluation.feedback.requested", actor="orchestrator-reactor",
                causation_id=terminal.id, correlation_id=channel_id,
                payload={**payload, "member_id": reviewer, "evaluation_report": refs["evaluation_report"]},
            )
        reviewer = str(feedback.payload["member_id"])
        refs.update(consensus_review_id=f"creview-eval-{feedback.id}", evaluation_feedback_id=feedback.id)
        message = next((row for row in events if row.type == "channel.message.posted"
                        and (row.payload.get("refs") or {}).get("evaluation_feedback_id") == feedback.id), None)
        if message is None:
            message = host.event_writer.emit(
                "channel.message.posted", actor="orchestrator-reactor", correlation_id=channel_id,
                causation_id=feedback.id,
                payload=channel_message_event_payload(Path(host.state_dir), {
                    "channel_id": channel_id, "thread_id": thread_id,
                    "message_id": f"msg-eval-{feedback.id}", "member_id": "operator",
                    "role": "user", "source": "runtime", "mentions": [reviewer], "refs": refs,
                    "text": f"@{reviewer} 核实独立评估的完整报告和原始需求，逐项记录处置理由。"
                            "已明确需求的文档缺陷走 revision_request；未决产品取舍走 owner_decision。"
                            "评估发现是待核实证据，不自动成为产品需求；保留误报、范围外和建议的说明。",
                }, created_by="channel-evaluation-feedback:runtime", source_event_id=feedback.id),
            )
    from zf.runtime.channel_synthesis_reactor import _route_runtime_message

    _route_runtime_message(host, feedback, message)


def validate_feedback_reply(*, state_dir: Path, writer: Any, refs: dict[str, Any],
                            review: dict[str, Any], channel_id: str, thread_id: str,
                            member_id: str) -> str:
    """Check authorization and complete finding dispositions, without scoring them."""
    channel = project_channel(state_dir, channel_id) or {}
    consensus = current_synthesis_consensus(channel, thread_id)
    session = (channel.get("discussions") or {}).get(thread_id) or {}
    if consensus.get("human_confirmed") or (session.get("state") == "idle" and session.get("closed_event_id")):
        return "evaluation_feedback_target_closed"
    feedback_id = str(refs.get("evaluation_feedback_id") or "")
    requested = next((row for row in writer.event_log.read_all()
                      if row.id == feedback_id and row.type == "channel.prd_evaluation.feedback.requested"), None)
    if requested is None or requested.actor != "orchestrator-reactor":
        return "invalid_evaluation_feedback"
    payload = requested.payload or {}
    if refs.get("consensus_review_id") != f"creview-eval-{feedback_id}":
        return "invalid_evaluation_feedback_review_id"
    terminal = next((row for row in writer.event_log.read_all()
                     if row.id == requested.causation_id and row.type == "output.evaluation.completed"), None)
    if terminal is None or any((terminal.payload or {}).get(key) != value for key, value in {
        "request_id": refs.get("evaluation_request_id"),
        "report_ref": (refs.get("evaluation_report") or {}).get("ref"),
        "report_digest": (refs.get("evaluation_report") or {}).get("sha256"),
    }.items()):
        return "invalid_evaluation_feedback_causation"
    if any(payload.get(key) != value for key, value in {
        "channel_id": channel_id, "thread_id": thread_id, "member_id": member_id,
        "artifact_ref": refs.get("artifact_ref"), "artifact_digest": refs.get("artifact_digest"),
        "prd_revision": refs.get("prd_revision"), "request_id": refs.get("evaluation_request_id"),
        "evaluation_report": refs.get("evaluation_report"),
    }.items()):
        return "invalid_evaluation_feedback_scope"
    try:
        report, _ = evaluation_feedback_documents(state_dir, refs)
    except (SidecarRefError, ValueError, OSError):
        return "invalid_evaluation_feedback_evidence"
    dispositions = review.get("finding_dispositions")
    if not isinstance(dispositions, list):
        return "evaluation_feedback_requires_finding_dispositions"
    expected = set(range(len(report.payload.get("findings") or [])))
    indexes = [row.get("finding_index") for row in dispositions if isinstance(row, dict)]
    if (any(type(index) is not int for index in indexes) or len(indexes) != len(dispositions)
            or len(indexes) != len(set(indexes)) or set(indexes) != expected):
        return "evaluation_feedback_incomplete_finding_dispositions"
    for row in dispositions:
        if row.get("disposition") not in {"revision_request", "owner_decision", "evidence_needed",
                                         "out_of_scope", "not_reproduced", "suggestion"} or not str(row.get("reason") or "").strip():
            return "invalid_evaluation_finding_disposition"
    return ""


def record_feedback_reply(*, state_dir: Path, writer: Any, refs: dict[str, Any],
                          review: dict[str, Any], reply_event_id: str, member_id: str) -> None:
    feedback_id = str(refs["evaluation_feedback_id"])
    descriptor = write_sidecar_json(
        state_dir, f"artifacts/channel-evaluation-feedback/{feedback_id}/{reply_event_id}.json",
        {"feedback_id": feedback_id, "evaluation_request_id": refs["evaluation_request_id"],
         "evaluation_report": refs["evaluation_report"], "artifact_ref": refs["artifact_ref"],
         "artifact_digest": refs["artifact_digest"], "review": review},
        kind="evaluation_feedback_review", schema_version="channel-evaluation-feedback.v1",
        created_by=member_id, source_event_id=reply_event_id, required=True,
    )
    writer.emit("channel.prd_evaluation.feedback.reviewed", actor=member_id,
                causation_id=reply_event_id, correlation_id=refs["channel_id"],
                payload={"request_id": refs["evaluation_request_id"], "feedback_id": feedback_id,
                         "channel_id": refs["channel_id"], "thread_id": refs["thread_id"],
                         "review": descriptor})
