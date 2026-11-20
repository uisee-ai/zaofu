"""The standalone installation's private account and revocable browser sessions."""

import hashlib
import json
import os
import stat
import secrets
import time
from pathlib import Path

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.workspace.registry import workspace_home


def _password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768,
                          r=8, p=1, maxmem=64 << 20).hex()


def session_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LocalAccount:
    username = "zaofu"

    def __init__(self, home: Path | None = None):
        self.path = (home or workspace_home()) / "web-account.json"

    def read(self) -> dict:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, encoding="utf-8") as stream:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise ValueError("unsafe account file")
                data = json.load(stream)
            if (data["version"] != 1 or len(bytes.fromhex(data["salt"])) != 16
                    or len(bytes.fromhex(data["password_hash"])) != 64
                    or not isinstance(data["sessions"], dict)):
                raise ValueError("invalid account")
            return data
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Cannot read private account. Use zf web --reset-password locally.") from exc

    def initialize(self, password: str = "zaofu", *, reset: bool = False) -> None:
        with locked_path(self.path):
            if self.path.is_symlink():
                raise RuntimeError("Refusing symbolic link at the account path")
            if self.path.exists() and not reset:
                self.read()
                return
            self._replace_password(password)

    def _replace_password(self, password: str) -> None:
        if not password or len(password.encode()) > 1024:
            raise ValueError("Password must contain between 1 and 1024 bytes")
        salt = secrets.token_hex(16)
        data = {"version": 1, "salt": salt, "password_hash": _password_hash(password, salt),
                "sessions": {}}
        atomic_write_text(self.path, json.dumps(data))

    def login(self, username: str, password: str, ttl: int) -> str | None:
        if len(password.encode()) > 1024:
            return None
        with locked_path(self.path):
            data = self.read()
            matches = secrets.compare_digest(data["password_hash"], _password_hash(password, data["salt"]))
            if not matches or username != self.username:
                return None
            now = time.time()
            sessions = {k: end for k, end in data["sessions"].items() if end > now}
            token = secrets.token_urlsafe(32)
            sessions[session_key(token)] = now + ttl
            data["sessions"] = sessions
            atomic_write_text(self.path, json.dumps(data))
            return token

    def valid(self, token: str) -> bool:
        return bool(token and self.read()["sessions"].get(session_key(token), 0) > time.time())

    def logout(self, token: str) -> None:
        with locked_path(self.path):
            data = self.read()
            if data["sessions"].pop(session_key(token), None) is not None:
                atomic_write_text(self.path, json.dumps(data))

    def change_password(self, token: str, current: str, new: str) -> bool:
        with locked_path(self.path):
            data = self.read()
            if (data["sessions"].get(session_key(token), 0) <= time.time()
                    or len(current.encode()) > 1024
                    or not secrets.compare_digest(_password_hash(current, data["salt"]), data["password_hash"])):
                return False
            self._replace_password(new)
            return True
