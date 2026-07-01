"""zf bridge — external bridge runtimes."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import EventSigningConfigError, event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.openclaw_feishu_bridge import (
    bridge_status,
    push_openclaw_feishu_bridge_once,
    resolve_openclaw_feishu_bridge_binding,
)
from zf.runtime.openclaw_feishu_inbound_watch import (
    handle_openclaw_feishu_inbound_payloads,
    load_inbound_payloads_from_file,
    scan_openclaw_feishu_payload_dir_once,
    watch_openclaw_feishu_payload_dir,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("bridge", help="Run external bridge integrations")
    parser.set_defaults(func=_run_root)
    bridge_sub = parser.add_subparsers(dest="bridge_command")

    openclaw_feishu = bridge_sub.add_parser(
        "openclaw-feishu",
        help="[DEPRECATED] Relay ZaoFu→Feishu via OpenClaw — use direct "
             "`zf feishu push` / `zf feishu bridge --watch` instead",
    )
    openclaw_feishu.set_defaults(func=_run_openclaw_feishu_root)
    ocf_sub = openclaw_feishu.add_subparsers(dest="openclaw_feishu_command")

    push = ocf_sub.add_parser("push", help="Push outbound ZaoFu channel messages")
    _add_common_args(push)
    push.add_argument("--once", action="store_true", help="Run one scan and exit")
    push.add_argument("--watch", action="store_true", help="Continuously scan for new messages")
    push.add_argument("--interval", type=float, default=2.0)
    push.set_defaults(func=run_openclaw_feishu_push)

    status = ocf_sub.add_parser("status", help="Show bridge delivery counters")
    _add_common_args(status)
    status.set_defaults(func=run_openclaw_feishu_status)

    inbound = ocf_sub.add_parser("inbound", help="Apply inbound Feishu/OpenClaw payload")
    _add_common_args(inbound)
    inbound.add_argument(
        "--payload-file",
        default="",
        help="JSON payload file from OpenClaw/Feishu, or '-' for stdin",
    )
    inbound.add_argument("--watch", action="store_true", help="Watch a payload spool directory")
    inbound.add_argument(
        "--payload-dir",
        default="",
        help="Directory containing OpenClaw/Feishu JSON or JSONL payload files",
    )
    inbound.add_argument(
        "--glob",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Payload file glob for --watch; default: *.json and *.jsonl",
    )
    inbound.add_argument("--archive-dir", default="", help="Processed file archive directory")
    inbound.add_argument("--failed-dir", default="", help="Failed file archive directory")
    inbound.add_argument("--keep-files", action="store_true", help="Do not move payload files")
    inbound.add_argument("--interval", type=float, default=2.0)
    inbound.add_argument(
        "--max-iterations",
        type=int,
        default=0,
        help="Stop --watch after N scans; 0 means forever",
    )
    inbound.add_argument("--serve", action="store_true", help="Serve an HTTP POST inbound endpoint")
    inbound.add_argument("--host", default="127.0.0.1")
    inbound.add_argument("--port", type=int, default=8788)
    inbound.add_argument(
        "--token-env",
        default="",
        help="Environment variable containing HTTP inbound token",
    )
    inbound.add_argument(
        "--allowed-chat-id",
        action="append",
        default=[],
        metavar="CHAT_ID",
        help="Additional Feishu source chat id accepted by inbound payloads",
    )
    inbound.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="Allow unauthenticated HTTP inbound requests; for local smoke only",
    )
    inbound.set_defaults(func=run_openclaw_feishu_inbound)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--binding", default="", help="Bridge binding id from zf.yaml")
    parser.add_argument("--channel", default="", help="Override ZaoFu channel id")
    parser.add_argument("--target", default="", help="Override Feishu target, e.g. chat:oc_xxx")
    parser.add_argument("--provider-binding", default="", help="Override OpenClaw provider binding")
    parser.add_argument("--json", action="store_true", help="Emit JSON")


def _run_root(_args: argparse.Namespace) -> int:
    print("Usage: zf bridge {openclaw-feishu} ...", file=sys.stderr)
    return 1


def _run_openclaw_feishu_root(_args: argparse.Namespace) -> int:
    print("Usage: zf bridge openclaw-feishu {push,status,inbound} ...", file=sys.stderr)
    return 1


_DEPRECATION = (
    "[DEPRECATED] `zf bridge openclaw-feishu` relays ZaoFu→Feishu through the "
    "OpenClaw gateway. It is an optional sidecar, NOT on the live alert path "
    "(the orchestrator never calls it). Alerts/cards already deliver DIRECTLY "
    "via FeishuHttpTransport — use `zf feishu push --watch` (outbound) and "
    "`zf feishu bridge --watch` (inbound). This relay will be removed."
)


def _warn_deprecated() -> None:
    print(_DEPRECATION, file=sys.stderr)


def run_openclaw_feishu_push(args: argparse.Namespace) -> int:
    _warn_deprecated()
    if not getattr(args, "once", False) and not getattr(args, "watch", False):
        args.once = True
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=True,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if context.config is None:
        print("Error: zf.yaml is required for OpenClaw Feishu bridge", file=sys.stderr)
        return 1
    try:
        event_log = event_log_from_project(context.state_dir, config=context.config)
    except EventSigningConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    writer = EventWriter(event_log)

    def run_once() -> dict[str, object]:
        result = push_openclaw_feishu_bridge_once(
            event_log=event_log,
            writer=writer,
            config=context.config,
            bridge_binding_id=getattr(args, "binding", ""),
            channel_id=getattr(args, "channel", ""),
            target=getattr(args, "target", ""),
            provider_binding_id=getattr(args, "provider_binding", ""),
        )
        return {
            "ok": result.ok,
            "status": result.status,
            "reason": result.reason,
            "considered": result.considered,
            "sent": result.sent,
            "skipped": result.skipped,
            "failed": result.failed,
            "delivered_event_ids": result.delivered_event_ids or [],
            "failed_event_ids": result.failed_event_ids or [],
        }

    try:
        if getattr(args, "watch", False):
            while True:
                payload = run_once()
                _print_push_result(payload, json_output=getattr(args, "json", False))
                time.sleep(max(float(getattr(args, "interval", 2.0)), 0.5))
        payload = run_once()
    except KeyboardInterrupt:
        return 130
    finally:
        event_log.close()
    _print_push_result(payload, json_output=getattr(args, "json", False))
    return 0 if payload.get("ok") else 2


def run_openclaw_feishu_status(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=True,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        event_log = event_log_from_project(context.state_dir, config=context.config)
    except EventSigningConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    payload = bridge_status(event_log, bridge_id=getattr(args, "binding", ""))
    event_log.close()
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            "OpenClaw Feishu bridge: "
            f"requested={payload['send_requested']} "
            f"delivered={payload['delivered']} failed={payload['failed']} "
            f"inbound_received={payload['inbound_received']} "
            f"inbound_rejected={payload['inbound_rejected']} "
            f"loop_skipped={payload['loop_skipped']}"
        )
    return 0


def run_openclaw_feishu_inbound(args: argparse.Namespace) -> int:
    _warn_deprecated()
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=True,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "serve", False):
        return _serve_openclaw_feishu_inbound(context, args)
    try:
        event_log = event_log_from_project(context.state_dir, config=context.config)
    except EventSigningConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    writer = EventWriter(event_log)
    try:
        if getattr(args, "watch", False):
            return _run_openclaw_feishu_inbound_watch(context, event_log, writer, args)
        payload_file = str(getattr(args, "payload_file", "") or "").strip()
        if not payload_file:
            print(
                "Error: --payload-file is required unless --watch or --serve is used",
                file=sys.stderr,
            )
            return 1
        batch = handle_openclaw_feishu_inbound_payloads(
            state_dir=context.state_dir,
            event_log=event_log,
            writer=writer,
            config=context.config,
            payloads=_load_inbound_payloads(payload_file),
            bridge_binding_id=getattr(args, "binding", ""),
            channel_id=getattr(args, "channel", ""),
            target=getattr(args, "target", ""),
            provider_binding_id=getattr(args, "provider_binding", ""),
            allowed_chat_ids=_allowed_chat_ids_arg(args),
            project_root=context.project_root,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        event_log.close()
    output = _batch_result_payload(batch)
    _print_inbound_batch_result(output, json_output=getattr(args, "json", False))
    return 0 if output.get("ok") else 2


def _run_openclaw_feishu_inbound_watch(
    context: Any,
    event_log: Any,
    writer: EventWriter,
    args: argparse.Namespace,
) -> int:
    payload_dir = _inbound_payload_dir(context, args)
    if not payload_dir:
        print(
            "Error: --payload-dir or integrations.openclaw_feishu_bridge.*.inbound.payload_dir is required for --watch",
            file=sys.stderr,
        )
        return 1
    archive_dir = _spool_dir_arg(args, "archive_dir", payload_dir / ".processed")
    failed_dir = _spool_dir_arg(args, "failed_dir", payload_dir / ".failed")
    patterns = list(getattr(args, "glob", []) or []) or ["*.json", "*.jsonl"]
    max_iterations = max(int(getattr(args, "max_iterations", 0) or 0), 0)
    common = {
        "payload_dir": payload_dir,
        "archive_dir": archive_dir,
        "failed_dir": failed_dir,
        "state_dir": context.state_dir,
        "event_log": event_log,
        "writer": writer,
        "config": context.config,
        "bridge_binding_id": getattr(args, "binding", ""),
        "channel_id": getattr(args, "channel", ""),
        "target": getattr(args, "target", ""),
        "provider_binding_id": getattr(args, "provider_binding", ""),
        "allowed_chat_ids": _allowed_chat_ids_arg(args),
        "project_root": context.project_root,
        "patterns": patterns,
        "keep_files": bool(getattr(args, "keep_files", False)),
    }
    if max_iterations == 1:
        result = scan_openclaw_feishu_payload_dir_once(**common)
        payload = _spool_result_payload(result)
        _print_inbound_spool_result(payload, json_output=getattr(args, "json", False))
        return 0 if payload.get("ok") else 2
    try:
        results = watch_openclaw_feishu_payload_dir(
            **common,
            interval_seconds=float(getattr(args, "interval", 2.0)),
            max_iterations=max_iterations,
        )
    except KeyboardInterrupt:
        return 130
    ok = all(item.ok for item in results)
    output = {
        "ok": ok,
        "status": "completed" if ok else "failed",
        "iterations": len(results),
        "results": [_spool_result_payload(item) for item in results],
    }
    if getattr(args, "json", False):
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        for item in output["results"]:
            _print_inbound_spool_result(item, json_output=False)
    return 0 if ok else 2


def _serve_openclaw_feishu_inbound(context: Any, args: argparse.Namespace) -> int:
    token_result = _inbound_server_token(context.config, args)
    if isinstance(token_result, str):
        token = token_result
    else:
        print(f"Error: {token_result['reason']}", file=sys.stderr)
        return 1

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            if self.path in {"/health", "/healthz", "/readyz"}:
                _send_json(self, 200, {"ok": True, "status": "ready"})
                return
            _send_json(self, 404, {"ok": False, "reason": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            if token and not _authorized(self.headers, token):
                _send_json(self, 401, {"ok": False, "reason": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > 2_000_000:
                _send_json(self, 413, {"ok": False, "reason": "payload too large"})
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
                result = _handle_inbound_payloads_for_context(
                    context=context,
                    args=args,
                    payloads=_coerce_json_payloads(payload),
                )
            except (json.JSONDecodeError, ValueError) as exc:
                _send_json(self, 400, {"ok": False, "reason": str(exc)})
                return
            except EventSigningConfigError as exc:
                _send_json(self, 500, {"ok": False, "reason": str(exc)})
                return
            _send_json(self, 200 if result.get("ok") else 202, result)

        def log_message(self, format: str, *args_: object) -> None:
            return

    server = ThreadingHTTPServer(
        (str(getattr(args, "host", "127.0.0.1")), int(getattr(args, "port", 8788))),
        Handler,
    )
    print(
        "OpenClaw Feishu inbound server listening on "
        f"http://{getattr(args, 'host', '127.0.0.1')}:{getattr(args, 'port', 8788)}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped OpenClaw Feishu inbound server.")
    finally:
        server.server_close()
    return 0


def _handle_inbound_payloads_for_context(
    *,
    context: Any,
    args: argparse.Namespace,
    payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    event_log = event_log_from_project(context.state_dir, config=context.config)
    writer = EventWriter(event_log)
    try:
        result = handle_openclaw_feishu_inbound_payloads(
            state_dir=context.state_dir,
            event_log=event_log,
            writer=writer,
            config=context.config,
            payloads=payloads,
            bridge_binding_id=getattr(args, "binding", ""),
            channel_id=getattr(args, "channel", ""),
            target=getattr(args, "target", ""),
            provider_binding_id=getattr(args, "provider_binding", ""),
            allowed_chat_ids=_allowed_chat_ids_arg(args),
            project_root=context.project_root,
        )
        return _batch_result_payload(result)
    finally:
        event_log.close()


def _print_push_result(payload: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    suffix = f" reason={payload['reason']}" if payload.get("reason") else ""
    print(
        "OpenClaw Feishu bridge push: "
        f"status={payload['status']} considered={payload['considered']} "
        f"sent={payload['sent']} skipped={payload['skipped']} "
        f"failed={payload['failed']}{suffix}"
    )


def _load_json_payload(path: str) -> object:
    if path == "-":
        return json.loads(sys.stdin.read())
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_inbound_payloads(path: str) -> list[dict[str, Any]]:
    if path == "-":
        return _coerce_json_payloads(json.loads(sys.stdin.read()))
    return load_inbound_payloads_from_file(Path(path))


def _coerce_json_payloads(payload: Any) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, list) else [payload]
    payloads: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"payload item {index} must be a JSON object")
        payloads.append(item)
    return payloads


def _allowed_chat_ids_arg(args: argparse.Namespace) -> list[str]:
    return [
        str(item).strip()
        for item in getattr(args, "allowed_chat_id", []) or []
        if str(item).strip()
    ]


def _batch_result_payload(result: Any) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "status": result.status,
        "count": result.count,
        "received": result.received,
        "posted": result.posted,
        "rejected": result.rejected,
        "skipped": result.skipped,
        "failed": result.failed,
        "results": result.results,
    }


def _spool_result_payload(result: Any) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "status": result.status,
        "considered": result.considered,
        "processed": result.processed,
        "failed": result.failed,
        "received": result.received,
        "posted": result.posted,
        "rejected": result.rejected,
        "skipped": result.skipped,
        "files": result.files,
    }


def _print_inbound_batch_result(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(
        "OpenClaw Feishu bridge inbound: "
        f"count={payload['count']} ok={str(payload['ok']).lower()} "
        f"received={payload['received']} posted={payload['posted']} "
        f"rejected={payload['rejected']} skipped={payload['skipped']} "
        f"failed={payload['failed']}"
    )


def _print_inbound_spool_result(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(
        "OpenClaw Feishu bridge inbound watch: "
        f"considered={payload['considered']} processed={payload['processed']} "
        f"failed={payload['failed']} received={payload['received']} "
        f"posted={payload['posted']} rejected={payload['rejected']} "
        f"skipped={payload['skipped']}"
    )


def _inbound_payload_dir(context: Any, args: argparse.Namespace) -> Path | None:
    raw = str(getattr(args, "payload_dir", "") or "").strip()
    if raw:
        return Path(raw)
    binding, _error = resolve_openclaw_feishu_bridge_binding(
        context.config,
        bridge_binding_id=getattr(args, "binding", ""),
        channel_id=getattr(args, "channel", ""),
        target=getattr(args, "target", ""),
        provider_binding_id=getattr(args, "provider_binding", ""),
        require_outbound=False,
    )
    raw = str(getattr(binding.inbound, "payload_dir", "") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = context.project_root / path
    return path


def _spool_dir_arg(args: argparse.Namespace, name: str, default: Path) -> Path:
    raw = str(getattr(args, name, "") or "").strip()
    return Path(raw) if raw else default


def _inbound_server_token(config: Any, args: argparse.Namespace) -> str | dict[str, str]:
    if bool(getattr(args, "allow_unauthenticated", False)):
        return ""
    token_env = str(getattr(args, "token_env", "") or "").strip()
    if not token_env:
        binding, _error = resolve_openclaw_feishu_bridge_binding(
            config,
            bridge_binding_id=getattr(args, "binding", ""),
            channel_id=getattr(args, "channel", ""),
            target=getattr(args, "target", ""),
            provider_binding_id=getattr(args, "provider_binding", ""),
            require_outbound=False,
        )
        token_env = str(
            getattr(binding.inbound, "server_token_env", "")
            or "ZF_OPENCLAW_FEISHU_INBOUND_TOKEN"
        ).strip()
    token = os.environ.get(token_env, "").strip()
    if not token:
        return {
            "reason": (
                f"{token_env} is not set; use --token-env or "
                "--allow-unauthenticated for local smoke"
            )
        }
    return token


def _authorized(headers: Any, token: str) -> bool:
    auth = str(headers.get("Authorization") or "").strip()
    if auth == f"Bearer {token}":
        return True
    return str(headers.get("X-ZF-Bridge-Token") or "").strip() == token


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
