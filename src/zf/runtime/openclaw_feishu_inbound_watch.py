"""Watch/apply OpenClaw Feishu inbound payload spools.

The watcher is intentionally file/HTTP-payload based. ZaoFu should not assume
which OpenClaw connector shape is deployed; external sidecars may write JSON
payloads to a spool directory or POST them to the CLI server, while this module
keeps the deterministic normalization path centralized.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.openclaw_feishu_inbound import (
    OpenClawFeishuInboundResult,
    handle_openclaw_feishu_inbound_payload,
)


@dataclass(frozen=True)
class OpenClawFeishuInboundBatchResult:
    ok: bool
    status: str
    count: int = 0
    received: int = 0
    posted: int = 0
    rejected: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class OpenClawFeishuInboundSpoolResult:
    ok: bool
    status: str
    considered: int = 0
    processed: int = 0
    failed: int = 0
    received: int = 0
    posted: int = 0
    rejected: int = 0
    skipped: int = 0
    files: list[dict[str, Any]] = field(default_factory=list)


def load_inbound_payloads_from_file(path: Path) -> list[dict[str, Any]]:
    """Load one JSON/JSONL file into payload dicts."""
    if str(path) == "-":
        raise ValueError("stdin is handled by CLI")
    raw = Path(path).read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        payloads: list[dict[str, Any]] = []
        for lineno, line in enumerate(raw.splitlines(), start=1):
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSONL: {exc}") from exc
            payloads.extend(_coerce_payload_items(parsed, source=f"{path}:{lineno}"))
        return payloads
    parsed = json.loads(raw)
    return _coerce_payload_items(parsed, source=str(path))


def handle_openclaw_feishu_inbound_payloads(
    *,
    state_dir: Path,
    event_log: EventLog,
    writer: EventWriter,
    config: ZfConfig | None,
    payloads: list[dict[str, Any]],
    bridge_binding_id: str = "",
    channel_id: str = "",
    target: str = "",
    provider_binding_id: str = "",
    allowed_chat_ids: list[str] | None = None,
    project_root: Path | None = None,
) -> OpenClawFeishuInboundBatchResult:
    results: list[dict[str, Any]] = []
    received = posted = rejected = skipped = failed = 0
    for payload in payloads:
        result = handle_openclaw_feishu_inbound_payload(
            state_dir=state_dir,
            event_log=event_log,
            writer=writer,
            config=config,
            payload=payload,
            bridge_binding_id=bridge_binding_id,
            channel_id=channel_id,
            target=target,
            provider_binding_id=provider_binding_id,
            allowed_chat_ids=allowed_chat_ids,
            project_root=project_root,
        )
        results.append(_result_payload(result))
        received += result.received
        posted += result.posted
        rejected += result.rejected
        skipped += result.skipped
        if not result.ok and result.status == "config_error":
            failed += 1
    ok = failed == 0
    return OpenClawFeishuInboundBatchResult(
        ok=ok,
        status="completed" if ok else "failed",
        count=len(payloads),
        received=received,
        posted=posted,
        rejected=rejected,
        skipped=skipped,
        failed=failed,
        results=results,
    )


def process_openclaw_feishu_payload_file(
    *,
    path: Path,
    state_dir: Path,
    event_log: EventLog,
    writer: EventWriter,
    config: ZfConfig | None,
    bridge_binding_id: str = "",
    channel_id: str = "",
    target: str = "",
    provider_binding_id: str = "",
    allowed_chat_ids: list[str] | None = None,
    project_root: Path | None = None,
) -> OpenClawFeishuInboundBatchResult:
    payloads = load_inbound_payloads_from_file(path)
    return handle_openclaw_feishu_inbound_payloads(
        state_dir=state_dir,
        event_log=event_log,
        writer=writer,
        config=config,
        payloads=payloads,
        bridge_binding_id=bridge_binding_id,
        channel_id=channel_id,
        target=target,
        provider_binding_id=provider_binding_id,
        allowed_chat_ids=allowed_chat_ids,
        project_root=project_root,
    )


def scan_openclaw_feishu_payload_dir_once(
    *,
    payload_dir: Path,
    archive_dir: Path,
    failed_dir: Path,
    state_dir: Path,
    event_log: EventLog,
    writer: EventWriter,
    config: ZfConfig | None,
    bridge_binding_id: str = "",
    channel_id: str = "",
    target: str = "",
    provider_binding_id: str = "",
    allowed_chat_ids: list[str] | None = None,
    project_root: Path | None = None,
    patterns: list[str] | None = None,
    keep_files: bool = False,
) -> OpenClawFeishuInboundSpoolResult:
    payload_dir = Path(payload_dir)
    patterns = patterns or ["*.json", "*.jsonl"]
    files = _payload_files(payload_dir, patterns=patterns)
    archive_dir.mkdir(parents=True, exist_ok=True)
    failed_dir.mkdir(parents=True, exist_ok=True)

    processed = failed = received = posted = rejected = skipped = 0
    file_results: list[dict[str, Any]] = []
    for path in files:
        try:
            batch = process_openclaw_feishu_payload_file(
                path=path,
                state_dir=state_dir,
                event_log=event_log,
                writer=writer,
                config=config,
                bridge_binding_id=bridge_binding_id,
                channel_id=channel_id,
                target=target,
                provider_binding_id=provider_binding_id,
                allowed_chat_ids=allowed_chat_ids,
                project_root=project_root,
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            failed += 1
            dest = "" if keep_files else str(_move_to(path, failed_dir))
            file_results.append({
                "file": str(path),
                "status": "failed",
                "reason": str(exc),
                "moved_to": dest,
            })
            continue

        received += batch.received
        posted += batch.posted
        rejected += batch.rejected
        skipped += batch.skipped
        if batch.ok:
            processed += 1
            dest = "" if keep_files else str(_move_to(path, archive_dir))
            file_results.append({
                "file": str(path),
                "status": "processed",
                "count": batch.count,
                "received": batch.received,
                "posted": batch.posted,
                "rejected": batch.rejected,
                "skipped": batch.skipped,
                "moved_to": dest,
            })
            continue
        failed += 1
        dest = "" if keep_files else str(_move_to(path, failed_dir))
        file_results.append({
            "file": str(path),
            "status": "failed",
            "count": batch.count,
            "reason": "one or more payloads failed before event emission",
            "moved_to": dest,
        })

    ok = failed == 0
    return OpenClawFeishuInboundSpoolResult(
        ok=ok,
        status="completed" if ok else "failed",
        considered=len(files),
        processed=processed,
        failed=failed,
        received=received,
        posted=posted,
        rejected=rejected,
        skipped=skipped,
        files=file_results,
    )


def watch_openclaw_feishu_payload_dir(
    *,
    payload_dir: Path,
    archive_dir: Path,
    failed_dir: Path,
    state_dir: Path,
    event_log: EventLog,
    writer: EventWriter,
    config: ZfConfig | None,
    interval_seconds: float = 2.0,
    max_iterations: int = 0,
    bridge_binding_id: str = "",
    channel_id: str = "",
    target: str = "",
    provider_binding_id: str = "",
    allowed_chat_ids: list[str] | None = None,
    project_root: Path | None = None,
    patterns: list[str] | None = None,
    keep_files: bool = False,
) -> list[OpenClawFeishuInboundSpoolResult]:
    results: list[OpenClawFeishuInboundSpoolResult] = []
    iteration = 0
    while True:
        iteration += 1
        results.append(scan_openclaw_feishu_payload_dir_once(
            payload_dir=payload_dir,
            archive_dir=archive_dir,
            failed_dir=failed_dir,
            state_dir=state_dir,
            event_log=event_log,
            writer=writer,
            config=config,
            bridge_binding_id=bridge_binding_id,
            channel_id=channel_id,
            target=target,
            provider_binding_id=provider_binding_id,
            allowed_chat_ids=allowed_chat_ids,
            project_root=project_root,
            patterns=patterns,
            keep_files=keep_files,
        ))
        if max_iterations and iteration >= max_iterations:
            return results
        time.sleep(max(float(interval_seconds), 0.5))


def _coerce_payload_items(value: Any, *, source: str) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else [value]
    payloads: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{source}: payload item {index} must be a JSON object")
        payloads.append(item)
    return payloads


def _payload_files(payload_dir: Path, *, patterns: list[str]) -> list[Path]:
    if not payload_dir.exists():
        return []
    selected: dict[str, Path] = {}
    for pattern in patterns:
        for path in payload_dir.glob(pattern):
            if not path.is_file():
                continue
            if _is_internal_spool_file(path):
                continue
            selected[str(path)] = path
    return [selected[key] for key in sorted(selected)]


def _is_internal_spool_file(path: Path) -> bool:
    return any(part in {".processed", ".failed"} for part in path.parts)


def _move_to(path: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / path.name
    if dest.exists():
        stem = path.stem
        suffix = path.suffix
        for index in range(1, 10_000):
            candidate = directory / f"{stem}.{index}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
    path.replace(dest)
    return dest


def _result_payload(result: OpenClawFeishuInboundResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "status": result.status,
        "reason": result.reason,
        "received": result.received,
        "posted": result.posted,
        "rejected": result.rejected,
        "skipped": result.skipped,
        "event_id": result.event_id,
        "message_event_id": result.message_event_id,
    }
