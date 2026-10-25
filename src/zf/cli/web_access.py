"""Writable dashboard defaults at the operator CLI boundary."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from urllib.parse import urlencode

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.workspace.registry import workspace_home


def configure_web_access(*, host: str, read_only: bool = False) -> None:
    keys = ("ZF_WEB_ACTION_TOKEN", "ZF_WEB_PASSCODE", "ZF_WEB_TRUSTED_SESSION")
    if read_only:
        for key in keys:
            os.environ.pop(key, None)
        return
    # Disabling trusted access selects authentication, not a read-only server.
    trusted = os.environ.get("ZF_WEB_TRUSTED_SESSION", "").strip().lower()
    if any(os.environ.get(key, "").strip() for key in keys[:2]) or trusted in {"1", "true", "yes", "local_trusted"}:
        return
    if not trusted and host.strip().lower() in {"localhost", "127.0.0.1", "::1", "[::1]"}:
        os.environ["ZF_WEB_TRUSTED_SESSION"] = "1"
        return
    token_path = Path(os.environ.get("ZF_WEB_ACTION_TOKEN_FILE") or workspace_home() / "web-action-token").expanduser()
    with locked_path(token_path):
        if token_path.is_symlink():
            raise RuntimeError(f"web action token file must not be a symlink: {token_path}")
        token = token_path.read_text().strip() if token_path.exists() else ""
        if not token:
            token = secrets.token_hex(32)
            atomic_write_text(token_path, token + "\n")
        token_path.chmod(0o600)
    os.environ["ZF_WEB_ACTION_TOKEN"] = token


def dashboard_launch_url(host: str, port: int) -> str:
    authority = f"[{host}]" if ":" in host and not host.startswith("[") else host
    url = f"http://{authority}:{port}/"
    token = os.environ.get("ZF_WEB_ACTION_TOKEN", "").strip()
    if token:
        # Fragments never reach HTTP request logs or Referer headers.
        url += "#" + urlencode({"zf-action-token": token})
    return url
