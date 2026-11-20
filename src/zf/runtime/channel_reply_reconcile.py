"""Re-arm an unknown Channel delivery only after an exact native abort receipt."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.channel_dispatch_capacity import channel_dispatch_lock
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_run_owner import provider_run_fields_for_request


def native_abort_receipt(*, session_id: str, project_root: Path, started_at: str,
                         failed_at: str, native_turn_id: str = "") -> dict[str, Any]:
    """Read metadata only; caller cannot supply a transcript path or receipt.

    Older failures lack a turn id. They require exactly one native start inside
    the ledger's dispatch window and no subsequent native turn. A completed
    turn needs result reconciliation, never blind retry.
    """
    try:
        UUID(session_id)
        start, failure = datetime.fromisoformat(started_at), datetime.fromisoformat(failed_at)
    except (ValueError, TypeError) as exc:
        raise ValueError("原回复缺少可核验的 session 或派发时间，保持禁止重试。") from exc
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    matches = list((home / "sessions").glob(f"*/*/*/rollout-*-{session_id}.jsonl"))
    if len(matches) != 1 or not matches[0].resolve().is_relative_to(home / "sessions"):
        raise ValueError("找不到唯一的原生会话回执，保持禁止重试。")
    path = matches[0]
    digest = hashlib.sha256()
    meta: dict = {}
    starts: list[dict] = []
    terminals: dict[str, dict] = {}
    completed: set[str] = set()
    try:
        with path.open("rb") as stream:
            for line in stream:
                digest.update(line)
                row = json.loads(line)
                body = row.get("payload") or {}
                if row.get("type") == "session_meta":
                    meta = body
                if row.get("type") != "event_msg":
                    continue
                if body.get("type") == "task_started":
                    starts.append({"turn_id": body.get("turn_id"), "timestamp": row.get("timestamp")})
                elif body.get("type") in {"turn_aborted", "task_complete", "task_completed"}:
                    if body["type"] in {"task_complete", "task_completed"}:
                        completed.add(str(body.get("turn_id") or ""))
                    terminals[str(body.get("turn_id") or "")] = {
                        "type": body["type"], "timestamp": row.get("timestamp"), "turn_id": body.get("turn_id")}
        if meta.get("id") != session_id or Path(str(meta.get("cwd") or "")).resolve() != project_root.resolve():
            raise ValueError("原生回执的项目或 session 不匹配。")
        candidates = [row for row in starts if start <= datetime.fromisoformat(row["timestamp"]) <= failure]
        if len(candidates) != 1 or not starts or starts[-1] != candidates[0]:
            raise ValueError("无法唯一绑定原生 turn，或会话已有后续 turn；保持禁止重试。")
        turn_id = str(candidates[0].get("turn_id") or "")
        if not turn_id or (native_turn_id and native_turn_id != turn_id):
            raise ValueError("原生 turn 与原回复不匹配。")
        terminal = terminals.get(turn_id, {})
        if turn_id in completed or terminal.get("type") != "turn_aborted":
            raise ValueError("原生 turn 没有确定的中止回执；仍在运行或可能已完成，禁止重复投递。")
        if datetime.fromisoformat(terminal["timestamp"]) < datetime.fromisoformat(candidates[0]["timestamp"]):
            raise ValueError("原生中止回执时间顺序错误。")
    except (OSError, KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("原生回执不可读或不完整；保持禁止重试。") from exc
    return {"provider_session_id": session_id, "native_turn_id": turn_id,
            "native_terminal": terminal, "transcript_sha256": digest.hexdigest()}


def reconcile_channel_reply(service: Any, *, requested: Any, payload: dict) -> dict[str, Any]:
    channel_id = str(payload.get("channel_id") or "")
    request_id = str(payload.get("reconcile_request_id") or "")
    failure_id = str(payload.get("failure_event_id") or "")
    generation = payload.get("run_generation")
    if not request_id or not failure_id or type(generation) is not int or generation < 1:
        raise ValueError("恢复必须绑定原 request、failure event 和 generation。")
    claim = hashlib.sha256(f"{channel_id}:{request_id}".encode()).hexdigest()
    with locked_path(service.state_dir / "locks" / "channel-reply-claims" / claim, timeout_seconds=0.1):
        with channel_dispatch_lock(service.state_dir, channel_id):
            events = service.writer.event_log.read_all()
            channel = project_channel(service.state_dir, channel_id) or {}
            from zf.runtime.channel_owner_authority import channel_owner_authority_error
            if error := channel_owner_authority_error(channel, actor=service.actor, capability="retry_reply"):
                raise ValueError(error)
            replay = next((e for e in reversed(events) if e.type == "channel.agent.reply.requested"
                           and e.payload.get("reconciles_failure_event_id") == failure_id
                           and e.payload.get("channel_id") == channel_id and e.payload.get("request_id") == request_id), None)
            if replay is not None:
                if replay.payload.get("run_generation") != generation + 1:
                    raise ValueError("恢复回执与原 generation 不匹配。")
                return {"event_id": replay.id, "run_generation": replay.payload["run_generation"], "replayed": True}
            row = next((r for r in channel.get("reply_requests", []) if r.get("request_id") == request_id), {})
            if (row.get("status") != "failed" or row.get("failure_status") != "delivery_unknown"
                    or row.get("run_generation", 1) != generation or row.get("event_id") != failure_id):
                raise ValueError("原失败已变化或不属于未知投递；刷新后再操作。")
            if row.get("backend") not in {"codex", "codex-headless", "codex_headless"}:
                raise ValueError("此 Provider 尚无受支持的原生终止核验；保持禁止重试。")
            original = next((e for e in reversed(events) if e.type == "channel.agent.reply.requested"
                             and e.payload.get("channel_id") == channel_id and e.payload.get("request_id") == request_id
                             and e.payload.get("run_generation", 1) == generation), None)
            if original is None:
                raise ValueError("找不到原始投递请求。")
            from zf.runtime.channel_synthesis_lock import retired_synthesis_reply_ids
            if request_id in retired_synthesis_reply_ids(channel, str(row.get("thread_id") or "main")):
                raise ValueError("原综合已被新一轮替代，不能恢复历史请求。")
            message = next((m for m in channel.get("messages", []) if m.get("message_id") == row.get("message_id")), {})
            synthesis_id = (message.get("refs") or {}).get("synthesis_request_id")
            if synthesis_id:
                from zf.runtime.channel_synthesis_currentness import synthesis_request_currentness
                thread_id = str(row.get("thread_id") or "main")
                stale = synthesis_request_currentness(events, channel_id=channel_id, thread_id=thread_id,
                    request_id=synthesis_id, session=(channel.get("discussions") or {}).get(thread_id, {}))
                if stale or any(e.type == "channel.synthesis.proposed" and e.payload.get("channel_id") == channel_id
                                and e.payload.get("request_id") == synthesis_id for e in events):
                    raise ValueError("原综合已过期或已有结果，不能重复生成 PRD。")
            receipt = native_abort_receipt(session_id=str(row.get("provider_session_id") or ""),
                project_root=service.project_root, started_at=str(row.get("started_at") or ""),
                failed_at=str(row.get("ended_at") or row.get("updated_at") or ""))
            descriptor = write_immutable_json_sidecar(service.state_dir, {
                "schema_version": "channel-native-abort-receipt.v1", "channel_id": channel_id,
                "request_id": request_id, "failure_event_id": failure_id, "run_generation": generation,
                "run_id": row.get("run_id"), **receipt,
            }, root="channels/recovery", kind="channel_native_abort_receipt",
               schema_version="channel-native-abort-receipt.v1", created_by="channel-reconcile", source_event_id=requested.id)
            body = {**original.payload, "status": "pending", "run_generation": generation + 1,
                    "reconciles_failure_event_id": failure_id, "native_abort_receipt_ref": descriptor,
                    "reason": "Owner requested retry after verified native abort", "source": service.surface}
            body.update(provider_run_fields_for_request(channel_id, body))
            event = service.writer.emit("channel.agent.reply.requested", actor=service.actor,
                task_id=original.task_id, causation_id=requested.id, correlation_id=channel_id, payload=body)
            return {"event_id": event.id, "run_generation": generation + 1,
                    "native_abort_receipt_ref": descriptor, "replayed": False}
