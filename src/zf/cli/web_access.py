"""Select the Dashboard access policy before binding its listener."""

import getpass
import os
from pathlib import Path
from urllib.parse import urlencode

from zf.core.workspace.registry import workspace_home
from zf.core.workspace.web_account import LocalAccount

_LEGACY_KEYS = ("ZF_WEB_ACTION_TOKEN", "ZF_WEB_PASSCODE", "ZF_WEB_TRUSTED_SESSION")


def setup_account(*, reset: bool) -> None:
    from zf.integrations.controller.binding import managed_config
    if managed_config() is not None:
        raise RuntimeError("Managed workspaces use the Controller identity provider")
    account = LocalAccount()
    if account.path.exists() and not reset:
        raise RuntimeError("Account already configured. Use --reset-password for recovery")
    password = getpass.getpass("New password (empty selects zaofu): ") or "zaofu"
    if password != (getpass.getpass("Confirm password: ") or "zaofu"):
        raise ValueError("Passwords do not match")
    account.initialize(password, reset=reset)
    print("Local account configured. Restart all Dashboard processes for this workspace home.")


def configure_web_access(*, host: str, read_only: bool = False) -> LocalAccount | None:
    from zf.integrations.controller.binding import managed_config
    if managed_config() is not None:
        for key in _LEGACY_KEYS:
            os.environ.pop(key, None)
        return None

    account = LocalAccount()
    token_path = Path(os.environ.get("ZF_WEB_ACTION_TOKEN_FILE") or workspace_home() / "web-action-token").expanduser()
    if token_path.is_symlink():
        raise RuntimeError("Refusing symbolic link at web token path")
    explicit = (any(os.environ.get(key, "").strip() for key in _LEGACY_KEYS[:2])
                or os.environ.get("ZF_WEB_TRUSTED_SESSION", "").strip().lower() in {"1", "true", "yes", "local_trusted"})
    if account.path.exists() or account.path.is_symlink() or not (explicit or token_path.exists()):
        account.initialize()
        for key in _LEGACY_KEYS:
            os.environ.pop(key, None)
        return account
    if read_only:
        for key in _LEGACY_KEYS:
            os.environ.pop(key, None)
    elif not explicit:
        token = token_path.read_text().strip()
        if not token:
            raise RuntimeError("Empty legacy token file. Use zf web --setup-account to migrate")
        os.environ["ZF_WEB_ACTION_TOKEN"] = token
    return None


def dashboard_launch_url(host: str, port: int) -> str:
    authority = f"[{host}]" if ":" in host and not host.startswith("[") else host
    url = f"http://{authority}:{port}/"
    token = os.environ.get("ZF_WEB_ACTION_TOKEN", "").strip()
    if token:
        url += "#" + urlencode({"zf-action-token": token})
    return url
