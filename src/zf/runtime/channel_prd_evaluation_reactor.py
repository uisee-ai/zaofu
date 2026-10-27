"""Dispatch independent PRD evaluation outside the Channel member roster."""

from __future__ import annotations

import json
import hashlib
import threading
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.channel_prd_evaluation_recovery import (
    TERMINAL_EVALUATION_EVENTS, evaluation_execution_lock, evaluation_failure,
    fail_interrupted_evaluation,
)
from zf.runtime.channel_attachments import read_channel_attachment
from zf.runtime.channel_owner_sources import is_owner_message
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_prd_quality import required_prd_quality_contract, validate_prd_quality_contract
from zf.runtime.channel_sidecar import hydrate_channel_message_text
from zf.runtime.output_evaluation import EvaluationInput, evaluate_output
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _evaluation_target(body: dict[str, Any]) -> str:
    markdown = body.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("complete canonical PRD Markdown is unavailable")
    synthesis = body.get("synthesis") if isinstance(body.get("synthesis"), dict) else {}
    handoff = {key: value for key, value in synthesis.items() if key != "prd_markdown"}
    return (f"CANONICAL MARKDOWN:\n{markdown}\n\n"
            "CANONICAL STRUCTURED CONTRACT (same PRD revision; full canonical delivery contract):\n"
            f"{json.dumps(handoff, ensure_ascii=False, sort_keys=True)}")


def _version_evidence(host: Any, document: dict[str, Any], events: list[ZfEvent]) -> str:
    descriptor = {"ref": document.get("readiness_ref"),
                  "sha256": document.get("readiness_digest"),
                  "content_type": "application/json"}
    if not descriptor["ref"] or not descriptor["sha256"]:
        raise ValueError("canonical PRD readiness ref/digest is unavailable")
    readiness = hydrate_sidecar_ref(
        Path(host.state_dir), descriptor,
        purpose="independent-prd-readiness-evaluation", actor="output-evaluator",
    ).payload
    if not isinstance(readiness, dict) or any(
        readiness.get(key) != document.get(key)
        for key in ("channel_id", "thread_id", "revision")
    ) or readiness.get("schema_version") != "channel-prd-readiness.v1":
        raise ValueError("canonical PRD readiness version is inconsistent")
    return json.dumps({"readiness_descriptor": descriptor, "readiness": readiness,
                       "channel_membership_events": [
                           {"event_id": row.id, "type": row.type, "actor": row.actor,
                            "ts": row.ts, "causation_id": row.causation_id, "payload": row.payload}
                           for row in host.event_log.read_all()
                           if row.type in {"channel.member.invitation.proposed", "channel.member.invitation.decided",
                                           "channel.member.invited", "channel.member.added", "channel.member.add.rejected",
                                           "channel.member.removed"}
                           and row.correlation_id == document["channel_id"]
                           and str((row.payload or {}).get("thread_id") or document["thread_id"]) == document["thread_id"]
                       ],
                       "version_bound_events": [
                           {"event_id": row.id, "type": row.type, "actor": row.actor,
                            "ts": row.ts, "causation_id": row.causation_id, "payload": row.payload}
                           for row in events
                       ]}, ensure_ascii=False, sort_keys=True)


def _request_item(host: Any, event: ZfEvent) -> EvaluationInput:
    payload = event.payload or {}
    channel_id = str(payload["channel_id"])
    thread_id = str(payload["thread_id"])
    channel = project_channel(host.state_dir, channel_id) or {}
    prd = next((row for row in reversed(channel.get("syntheses") or [])
                if str(row.get("thread_id") or "main") == thread_id
                and row.get("artifact_ref") and row.get("artifact_digest")), None)
    if not prd or any((
        prd.get("prd_revision") != payload.get("prd_revision"),
        prd.get("artifact_ref") != payload.get("artifact_ref"),
        str(prd.get("artifact_digest") or "").removeprefix("sha256:")
        != str(payload.get("artifact_digest") or "").removeprefix("sha256:"),
    )):
        raise ValueError("canonical PRD version changed before evaluation")
    document = hydrate_sidecar_ref(
        Path(host.state_dir),
        {"ref": prd["artifact_ref"], "sha256": prd["artifact_digest"],
         "content_type": "application/json"},
        purpose="independent-prd-evaluation", actor="output-evaluator",
    ).payload
    body = document.get("body") if isinstance(document, dict) else {}
    synthesis = body.get("synthesis") if isinstance(body, dict) else {}
    events = host.event_log.read_all()
    synthesis_request_id = next((str(row.payload.get("request_id") or "") for row in events
                                 if row.id == prd.get("event_id")
                                 and row.type == "channel.synthesis.proposed"), "")
    quality_error = validate_prd_quality_contract(
        synthesis if isinstance(synthesis, dict) else {},
        required_version=required_prd_quality_contract(
            events, channel_id=channel_id, thread_id=thread_id, request_id=synthesis_request_id,
        ),
    )
    if quality_error:
        raise ValueError(f"canonical PRD quality contract invalid: {quality_error}")
    target = _evaluation_target(body if isinstance(body, dict) else {})
    if any(document.get(key) != value for key, value in {
        "channel_id": channel_id, "thread_id": thread_id,
        "revision": payload.get("prd_revision"),
    }.items()):
        raise ValueError("canonical PRD document identity is inconsistent")
    version_events = [row for row in events if row.type in {
        "channel.consensus.proposed", "channel.consensus.signed",
        "channel.consensus.blocked", "channel.consensus.reached",
    } and row.correlation_id == channel_id
        and str((row.payload or {}).get("thread_id") or "main") == thread_id
        and (row.payload or {}).get("artifact_ref") == prd["artifact_ref"]
        and str((row.payload or {}).get("artifact_digest") or "").removeprefix("sha256:")
        == str(prd["artifact_digest"]).removeprefix("sha256:")]
    evidence = json.loads(_version_evidence(host, document, version_events))
    evidence["digest_binding"] = {
        "canonical_artifact": {"ref": prd["artifact_ref"],
                               "sha256": str(prd["artifact_digest"]).removeprefix("sha256:"),
                               "digest_scope": "Raw canonical artifact file bytes; validated before extraction."},
        "evaluation_target": {"sha256": hashlib.sha256(target.encode("utf-8")).hexdigest(),
                              "digest_scope": "Frozen target_text UTF-8 bytes; manifest.target.sha256."},
        "relationship": "target_text is extracted Markdown plus the structured synthesis contract. "
                        "Its digest differs from the raw JSON artifact digest by construction. "
                        "Version-bound consensus events sign the canonical_artifact digest.",
    }
    owner = str(channel.get("owner_actor_ref") or "")
    # Bind conversation inputs to the current synthesis, never to later chat.
    proposed_index = next((i for i, row in enumerate(events) if row.id == prd.get("event_id")), -1)
    if proposed_index < 0:
        raise ValueError("canonical PRD publication event is unavailable")
    owner_message_ids = {
        str((row.payload or {}).get("message_id") or "")
        for row in events[:proposed_index + 1]
        if is_owner_message(row, channel_id=channel_id, thread_id=thread_id, owner=owner)
    }
    coverage = synthesis.get("source_coverage") if isinstance(synthesis, dict) else None
    if isinstance(coverage, list):
        bound_ids = {str(row.get("message_id") or "") for row in coverage
                     if isinstance(row, dict) and row.get("kind") in {"requirement", "owner_input"}}
        owner_message_ids &= bound_ids
        messages_by_id = {str(row.get("message_id") or ""): row for row in channel.get("messages") or []
                          if isinstance(row, dict)}
        for row in coverage:
            if not isinstance(row, dict) or row.get("kind") not in {"requirement", "owner_input"}:
                continue
            message_id = str(row.get("message_id") or "")
            if message_id not in owner_message_ids:
                continue
            message = messages_by_id.get(message_id) or {}
            descriptor = (message.get("refs") or {}).get("message_body") or {}
            actual_ref = str(descriptor.get("ref") or message.get("body_ref") or "")
            actual_digest = str(descriptor.get("sha256") or message.get("body_sha256") or "")
            if not actual_ref:
                content = hydrate_channel_message_text(Path(host.state_dir), message, strict=True)
                actual_ref = f"event:{message.get('event_id') or message_id}#inline-message"
                actual_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if (not row.get("message_body_ref") or not row.get("message_body_digest")
                    or row.get("message_body_ref") != actual_ref
                    or str(row.get("message_body_digest")).removeprefix("sha256:") != actual_digest.removeprefix("sha256:")
                    or (row.get("event_id") and row.get("event_id") != message.get("event_id"))):
                raise ValueError(f"version-bound owner message identity mismatch: {message_id}")
        requirement_id = next((str(row.get("message_id") or "") for row in coverage
                               if isinstance(row, dict) and row.get("kind") == "requirement"
                               and str(row.get("message_id") or "") in owner_message_ids), "")
    else:
        # Older PRDs lack a manifest. Recover the discussion anchor that
        # existed at publication instead of using today's restarted session.
        requirement_id = next((str((row.payload or {}).get("requirement_message_id") or "")
                               for row in reversed(events[:proposed_index + 1])
                               if row.type in {"channel.discussion.started", "channel.discussion.continued"}
                               and row.correlation_id == channel_id
                               and str((row.payload or {}).get("thread_id") or "main") == thread_id
                               and (row.payload or {}).get("requirement_message_id")), "")
        published_user_ids = {str((row.payload or {}).get("message_id") or "")
                              for row in events[:proposed_index + 1]
                              if row.type == "channel.message.posted" and row.correlation_id == channel_id
                              and str((row.payload or {}).get("thread_id") or "main") == thread_id
                              and (row.payload or {}).get("role") == "user"}
        if requirement_id not in published_user_ids:
            requirement_id = ""
    bound_attachments = {(str(row.get("message_id") or ""), str(row.get("message_body_ref") or ""),
                          str(row.get("message_body_digest") or "").removeprefix("sha256:"))
                         for row in coverage or [] if isinstance(row, dict) and row.get("kind") == "channel_attachment"}

    def check_attachment_binding(message_id: str, attachment: dict[str, Any]) -> None:
        if isinstance(coverage, list) and (message_id, str(attachment.get("uri") or ""),
                str(attachment.get("hash") or "").removeprefix("sha256:")) not in bound_attachments:
            raise ValueError(f"version-bound owner attachment mismatch: {message_id}")
    source_messages = [
        row for row in channel.get("messages") or []
        if isinstance(row, dict)
        and str(row.get("thread_id") or "main") == thread_id
        and ((bool(requirement_id) and str(row.get("message_id") or "") == requirement_id)
             or (str(row.get("message_id") or "") in owner_message_ids
                 and row.get("role") == "user"
                 and isinstance(row.get("refs"), dict)
                 and row["refs"].get("evaluation_source_document") is True))
    ]
    source_parts = []
    source_identity = []
    # File-to-PRD intake need not create a discussion. Trust only the snapshot
    # bound to this canonical version's durable synthesis request, not author refs.
    selected = next((row.payload.get("prd_source_file") for row in events
                     if synthesis_request_id and row.type == "channel.synthesis.requested"
                     and row.payload.get("request_id") == synthesis_request_id
                     and row.payload.get("channel_id") == channel_id
                     and str(row.payload.get("thread_id") or "main") == thread_id), None)
    if selected is not None:
        if (not isinstance(selected, dict) or selected.get("kind") != "channel_prd_source"
                or not selected.get("sha256")
                or selected.get("access_scope") != {"channel_id": channel_id, "thread_id": thread_id}):
            raise ValueError("original requirement file snapshot scope is inconsistent")
        content = hydrate_sidecar_ref(Path(host.state_dir), selected,
                                      purpose="independent-prd-source-evaluation",
                                      actor="output-evaluator").payload
        if not isinstance(content, str) or not content.strip():
            raise ValueError("original requirement file snapshot is empty")
        source_parts.append(f"[selected source file {selected['ref']}; sha256 {selected['sha256']}]\n{content}")
        source_identity.append({"file_ref": selected["ref"], "digest": selected["sha256"]})
    elif not source_messages or not requirement_id:
        raise ValueError("original requirement source is unavailable")
    for message in source_messages:
        message_id = str(message["message_id"])
        content = hydrate_channel_message_text(Path(host.state_dir), message, strict=True)
        descriptor = (message.get("refs") or {}).get("message_body") or {}
        digest = str(descriptor.get("sha256") or "")
        if not digest or not content.strip():
            raise ValueError("original requirement source body is incomplete")
        source_parts.append(f"[channel message {message_id}; body sha256 {digest}]\n{content}")
        source_identity.append({"message_id": message_id, "digest": digest})
        # Original requirement attachments belong to the source stage. Ordinary
        # conversation/author attachments must not leak the target into it.
        for attachment in (message.get("refs") or {}).get("attachments") or []:
            mime = str(attachment.get("mime") or attachment.get("type") or "")
            if not (mime.startswith("text/") or mime == "application/json"):
                continue
            check_attachment_binding(message_id, attachment)
            body, data = read_channel_attachment(
                Path(host.state_dir), attachment, channel_id=channel_id, thread_id=thread_id,
            )
            text = data.decode("utf-8")
            if not text.strip():
                raise ValueError("original requirement attachment body is empty")
            source_parts.append(
                f"[channel source attachment {body['name']}; message {message_id}; "
                f"sha256 {body['hash']}]\n{text}"
            )
            source_identity.append({"message_id": message_id, "digest": digest,
                                    "attachment_ref": attachment["uri"],
                                    "attachment_digest": body["hash"]})
    source = "\n\n".join(source_parts)
    source_ids = {row["message_id"] for row in source_identity if "message_id" in row}
    source_ref = "channel-source-bundle:sha256:" + hashlib.sha256(
        json.dumps(source_identity, sort_keys=True).encode("utf-8")
    ).hexdigest()
    decisions = [
        {"kind": "question_resolution", "event_id": row.id,
         "actor": row.actor, "payload": row.payload}
        for row in events[:proposed_index + 1]
        if row.type == "channel.question.resolved"
        and row.correlation_id == channel_id
        and str((row.payload or {}).get("thread_id") or "main") == thread_id
    ]
    for message in channel.get("messages") or []:
        if not isinstance(message, dict) or str(message.get("thread_id") or "main") != thread_id:
            continue
        message_id = str(message.get("message_id") or "")
        if message_id not in owner_message_ids or message_id in source_ids or message.get("role") != "user":
            continue
        attachments = []
        for attachment in (message.get("refs") or {}).get("attachments") or []:
            mime = str(attachment.get("mime") or attachment.get("type") or "")
            if not (mime.startswith("text/") or mime == "application/json"):
                continue
            check_attachment_binding(message_id, attachment)
            body, data = read_channel_attachment(Path(host.state_dir), attachment,
                channel_id=channel_id, thread_id=thread_id)
            text = data.decode("utf-8")
            if not text.strip():
                raise ValueError("owner conversation attachment body is empty")
            attachments.append({"ref": attachment["uri"], "name": body["name"],
                                "digest": body["hash"], "text": text})
        decisions.append({
            "kind": "owner_message", "message_id": message_id,
            "body_digest": str(((message.get("refs") or {}).get("message_body") or {}).get("sha256") or ""),
            "text": hydrate_channel_message_text(Path(host.state_dir), message, strict=True),
            "attachments": attachments,
        })
    decisions.extend({"kind": "channel_membership_event", **row}
                     for row in evidence["channel_membership_events"])
    role = str(payload.get("role") or "full")
    return EvaluationInput(
        kind="prd_output", objective=(
            "评估本版 PRD 文档对完整原始需求和有效 Owner 决定的忠实度、完整性、"
            "内部一致性及可验收性。区分原文要求写进交付文档的内容与本次平台操作/"
            "验收指令；后者保留在范围说明，不要求产品 PRD 逐条复述建群、点击或"
            "评估动作。审批与权限等适用约束仍须准确表达。成员签字不证明内容质量。"
            "本次不审计产品实施或全部平台运行事实，不要求当前评估报告在自身生成前"
            "证明已经完成；产品验证如未执行须如实记录，不能由测试计划推导通过。"
        ),
        source_text=source, decision_text=json.dumps(decisions, ensure_ascii=False),
        target_text=target, scope=role,
        evidence_text=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        risk_focus=("权限", "状态与跨资源一致性", "边界与验收设计") if role == "focused" else (),
        source_ref=source_ref,
        target_ref=str(prd["artifact_ref"]),
        target_revision=int(prd["prd_revision"]),
        context={"channel_id": channel_id, "thread_id": thread_id},
    )


def react_channel_prd_evaluation_requested(host: Any, event: ZfEvent) -> None:
    payload = event.payload or {}
    request_id = str(payload.get("request_id") or "")
    if not request_id:
        return
    if any(row.type == "output.evaluation.completed" and (row.payload or {}).get("request_id") == request_id
           for row in host.event_log.read_all()):
        _publish_feedback(host, event)
        return
    fail_interrupted_evaluation(host.state_dir, host.event_writer, event)
    lock = evaluation_execution_lock(host.state_dir, request_id)
    try:
        lock.__enter__()
    except TimeoutError:
        return
    try:
        if any((row.payload or {}).get("request_id") == request_id and row.type in {
            "channel.prd_evaluation.dispatched", "output.evaluation.started",
            "output.evaluation.completed", "output.evaluation.failed",
        } for row in host.event_log.read_all()):
            lock.__exit__(None, None, None)
            return
        dispatched = host.event_writer.emit(
            "channel.prd_evaluation.dispatched", actor="orchestrator-reactor",
            causation_id=event.id, correlation_id=event.correlation_id,
            payload={"request_id": request_id, "channel_id": payload.get("channel_id"),
                     "thread_id": payload.get("thread_id"), "role": payload.get("role")})
    except BaseException:
        lock.__exit__(None, None, None)
        raise

    def run() -> None:
        try:
            item = _request_item(host, event)
            evaluate_output(state_dir=Path(host.state_dir), writer=host.event_writer,
                            item=item, request_id=request_id,
                            causation_id=dispatched.id)
        except Exception as exc:
            evaluation_failure(host.event_writer, event, reason=str(exc), causation_id=dispatched.id)
            return
        finally:
            lock.__exit__(None, None, None)
        # Feedback failure must never overwrite an already completed verdict.
        _publish_feedback(host, event)

    try:
        threading.Thread(target=run, name=f"zf-prd-evaluation-{request_id[:8]}",
                         daemon=True).start()
    except Exception as exc:
        try:
            evaluation_failure(host.event_writer, event, reason=str(exc), causation_id=dispatched.id)
        finally:
            lock.__exit__(None, None, None)


def reconcile_channel_prd_evaluations(host: Any) -> None:
    events = host.event_log.read_all()
    terminal = {row.payload.get("request_id") for row in events
                if row.type in TERMINAL_EVALUATION_EVENTS}
    for event in events:
        if (event.type == "channel.prd_evaluation.requested"
                and event.payload.get("request_id") not in terminal):
            react_channel_prd_evaluation_requested(host, event)


def _publish_feedback(host: Any, event: ZfEvent) -> None:
    from zf.runtime.channel_evaluation_feedback import publish_evaluation_feedback

    try:
        publish_evaluation_feedback(host, event)
    except Exception as exc:
        host.event_writer.emit(
            "channel.prd_evaluation.feedback.failed", actor="orchestrator-reactor",
            causation_id=event.id, correlation_id=event.correlation_id,
            payload={"request_id": (event.payload or {}).get("request_id"), "reason": str(exc)[:500]})
