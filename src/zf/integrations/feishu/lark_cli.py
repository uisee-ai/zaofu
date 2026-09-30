"""Safe lark-cli adapter for Feishu document and Base projections."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from zf.integrations.feishu.transport import FeishuHttpTransport, FeishuTransportError


_MIN_VERSION = (1, 0, 47)
# ``im +chat-members-list`` is the readback operation that makes project-group
# membership activation fail closed.  It landed in lark-cli v1.0.64; the older
# general projection surface remains supported from v1.0.47.
_MIN_PROJECT_GROUP_VERSION = (1, 0, 64)
_ALLOWED_COMMANDS = {
    ("docs", "+create"),
    ("docs", "+update"),
    ("base", "+base-create"),
    ("base", "+table-create"),
    ("base", "+field-list"),
    ("base", "+field-create"),
    ("base", "+view-list"),
    ("base", "+view-create"),
    ("base", "+view-set-visible-fields"),
    ("base", "+view-set-filter"),
    ("base", "+view-set-group"),
    ("base", "+view-set-sort"),
    ("base", "+view-set-timebar"),
    ("base", "+record-search"),
    ("base", "+record-get"),
    ("base", "+record-upsert"),
    ("base", "+record-batch-create"),
    ("base", "+record-batch-update"),
    # Project Feishu group provisioning is intentionally limited to this small,
    # auditable IM surface.  Do not turn the runner into a generic CLI proxy.
    ("im", "+chat-create"),
    ("im", "+chat-members-list"),
    ("im", "chat.members", "create"),
}
_IDEMPOTENT_NO_OP_COMMANDS = {
    ("base", "+view-set-visible-fields"),
    ("base", "+view-set-filter"),
    ("base", "+view-set-group"),
    ("base", "+view-set-sort"),
    ("base", "+view-set-timebar"),
}
_NO_OPERATION_CODE = 800070003
_SECRET_ENV_KEYS = {
    "FEISHU_APP_SECRET",
    "FEISHU_TENANT_ACCESS_TOKEN",
    "LARKSUITE_CLI_APP_SECRET",
    "LARKSUITE_CLI_TENANT_ACCESS_TOKEN",
}
_FEISHU_ENV_MAP = {
    "FEISHU_APP_ID": "LARKSUITE_CLI_APP_ID",
    "FEISHU_APP_SECRET": "LARKSUITE_CLI_APP_SECRET",
    "FEISHU_TENANT_ACCESS_TOKEN": "LARKSUITE_CLI_TENANT_ACCESS_TOKEN",
}
_MINTED_TOKEN_TTL_SECONDS = 100 * 60


@dataclass(frozen=True)
class LarkCliResult:
    payload: dict[str, Any]
    argv: tuple[str, ...]


class LarkCliRunner:
    """Execute a fixed lark-cli command surface without shell interpolation."""

    def __init__(
        self,
        *,
        executable: str = "lark-cli",
        identity: str = "bot",
        prefer_profile: bool = False,
        timeout_seconds: float = 60.0,
        environ: Mapping[str, str] | None = None,
        check_version: bool = True,
        tenant_token_provider: Callable[[str, str], str] | None = None,
    ) -> None:
        if identity not in {"bot", "user"}:
            raise ValueError("identity must be bot or user")
        self.executable = executable
        self.identity = identity
        # Some projections intentionally use the bot configured in lark-cli's
        # profile.  This matters when the process environment also contains
        # FEISHU_APP_ID/FEISHU_APP_SECRET for a different product bot.
        self.prefer_profile = bool(prefer_profile)
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.environ = dict(os.environ if environ is None else environ)
        self._tenant_token_provider = (
            tenant_token_provider or self._mint_tenant_access_token
        )
        self._minted_tenant_token = ""
        self._minted_tenant_token_at = 0.0
        self.version: tuple[int, int, int] | None = None
        self._resolved = (
            shutil.which(executable) if os.path.sep not in executable else executable
        )
        if not self._resolved:
            raise FeishuTransportError(f"lark-cli executable not found: {executable}")
        if check_version:
            self._check_version()

    def run(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
    ) -> LarkCliResult:
        command_tuple = tuple(str(value) for value in command)
        if not _command_is_allowed(command_tuple):
            raise FeishuTransportError(
                f"lark-cli command is not allowed: {' '.join(command_tuple[:3])}"
            )
        argv = [
            self._resolved,
            *command_tuple,
            "--as",
            self.identity,
            "--format",
            "json",
        ]
        try:
            completed = subprocess.run(
                argv,
                input=input_text,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
                env=self._child_env(require_token=True),
            )
        except subprocess.TimeoutExpired as exc:
            raise FeishuTransportError(
                f"lark-cli timed out after {self.timeout_seconds:g}s"
            ) from exc
        except OSError as exc:
            raise FeishuTransportError(f"lark-cli execution failed: {exc}") from exc
        if completed.returncode != 0:
            error_payload = _json_object_from_output(
                completed.stderr or completed.stdout
            )
            error = (
                error_payload.get("error")
                if isinstance(error_payload.get("error"), dict)
                else {}
            )
            if (
                command_tuple[:2] in _IDEMPOTENT_NO_OP_COMMANDS
                and error.get("code") == _NO_OPERATION_CODE
            ):
                return LarkCliResult(
                    payload={"ok": True, "no_op": True},
                    argv=tuple(argv),
                )
            detail = self._redact((completed.stderr or completed.stdout).strip())
            raise FeishuTransportError(
                f"lark-cli exited {completed.returncode}: {detail[:800]}"
            )
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            detail = self._redact((completed.stdout or "").strip())
            raise FeishuTransportError(
                f"lark-cli returned invalid JSON: {detail[:400]}"
            ) from exc
        if not isinstance(payload, dict):
            raise FeishuTransportError("lark-cli JSON response must be an object")
        return LarkCliResult(payload=payload, argv=tuple(argv))

    def _check_version(self) -> None:
        try:
            completed = subprocess.run(
                [self._resolved, "--version"],
                text=True,
                capture_output=True,
                check=False,
                timeout=min(self.timeout_seconds, 10.0),
                env=self._child_env(require_token=False),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FeishuTransportError(
                f"unable to inspect lark-cli version: {exc}"
            ) from exc
        text = f"{completed.stdout}\n{completed.stderr}"
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
        if completed.returncode != 0 or match is None:
            raise FeishuTransportError("unable to parse lark-cli version")
        version = tuple(int(part) for part in match.groups())
        self.version = version
        if version < _MIN_VERSION:
            required = ".".join(str(part) for part in _MIN_VERSION)
            found = ".".join(str(part) for part in version)
            raise FeishuTransportError(
                f"lark-cli {found} is unsupported; require >= {required}"
            )

    def require_minimum_version(
        self,
        required: tuple[int, int, int],
        *,
        capability: str,
    ) -> None:
        """Fail closed when an opt-in adapter needs a newer CLI feature.

        ``check_version=False`` is an explicit test/offline escape hatch, so
        callers using it retain an unknown version rather than a fabricated
        compatibility claim.
        """
        if self.version is None or self.version >= required:
            return
        found = ".".join(str(part) for part in self.version)
        minimum = ".".join(str(part) for part in required)
        raise FeishuTransportError(
            f"lark-cli {found} lacks {capability}; require >= {minimum}"
        )

    def _child_env(self, *, require_token: bool) -> dict[str, str]:
        env = dict(self.environ)
        if self.identity == "user":
            # User OAuth belongs to the CLI profile, not the project's bot App.
            for key in (*_FEISHU_ENV_MAP, *_FEISHU_ENV_MAP.values()):
                env.pop(key, None)
            env["LARKSUITE_CLI_DEFAULT_AS"] = "user"
            env["LARKSUITE_CLI_BRAND"] = "feishu"
            return env
        if self.prefer_profile:
            # Let lark-cli resolve the selected identity from its profile.  Do
            # not leak another application's credentials into this request.
            for key in (*_FEISHU_ENV_MAP, *_FEISHU_ENV_MAP.values()):
                env.pop(key, None)
            env["LARKSUITE_CLI_BRAND"] = "feishu"
            env["LARKSUITE_CLI_DEFAULT_AS"] = "bot"
            return env
        for source, target in _FEISHU_ENV_MAP.items():
            value = env.get(source, "").strip()
            if value:
                env[target] = value
        if require_token and not env.get("LARKSUITE_CLI_TENANT_ACCESS_TOKEN", "").strip():
            app_id = env.get("LARKSUITE_CLI_APP_ID", "").strip()
            app_secret = env.get("LARKSUITE_CLI_APP_SECRET", "").strip()
            if app_id and app_secret:
                now = time.monotonic()
                if (
                    not self._minted_tenant_token
                    or now - self._minted_tenant_token_at
                    >= _MINTED_TOKEN_TTL_SECONDS
                ):
                    self._minted_tenant_token = self._tenant_token_provider(
                        app_id,
                        app_secret,
                    )
                    self._minted_tenant_token_at = now
                env["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] = (
                    self._minted_tenant_token
                )
        env["LARKSUITE_CLI_BRAND"] = "feishu"
        env["LARKSUITE_CLI_DEFAULT_AS"] = "bot"
        return env

    def _redact(self, text: str) -> str:
        redacted = text
        env = self._child_env(require_token=False)
        for key in _SECRET_ENV_KEYS:
            secret = env.get(key, "")
            if secret:
                redacted = redacted.replace(secret, "***")
        return redacted

    @staticmethod
    def _mint_tenant_access_token(app_id: str, app_secret: str) -> str:
        return FeishuHttpTransport(
            app_id=app_id,
            app_secret=app_secret,
        ).resolve_tenant_access_token()


def _json_object_from_output(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            payload, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _command_is_allowed(command: tuple[str, ...]) -> bool:
    """Return true only for a registered fixed command prefix.

    Most shortcuts are two words, while the generated IM member-create API is
    three.  Prefix matching permits flags after the exact command but never an
    arbitrary `lark-cli im ...` operation.
    """
    return any(
        len(command) >= len(allowed) and command[: len(allowed)] == allowed
        for allowed in _ALLOWED_COMMANDS
    )


class LarkCliDocumentClient:
    def __init__(self, runner: LarkCliRunner | None = None) -> None:
        self.runner = runner or LarkCliRunner()

    def create_document(
        self,
        *,
        title: str,
        folder_token: str = "",
        content: str = "",
    ) -> dict[str, Any]:
        if not title.strip():
            raise ValueError("document title is required")
        command = [
            "docs",
            "+create",
            "--api-version",
            "v2",
            "--doc-format",
            "markdown",
            "--content",
            "-",
        ]
        if folder_token.strip():
            command.extend(["--parent-token", folder_token.strip()])
        body = re.sub(r"(?m)^# ", "## ", content or "")
        body = body or f"## {title.strip()}\n"
        document_content = (
            f"<title>{html.escape(title.strip(), quote=False)}</title>\n"
            f"{body}"
        )
        payload = self.runner.run(
            command,
            input_text=document_content,
        ).payload
        document = _first_mapping(payload, "document") or payload
        document_id = _first_string(document, "document_id", "doc_token", "token")
        if not document_id:
            raise FeishuTransportError(
                "lark-cli document create response has no document_id"
            )
        return {
            **document,
            "document_id": document_id,
            "title": str(document.get("title") or title.strip()),
        }

    def append_markdown(self, document_id: str, markdown: str) -> dict[str, Any]:
        if not document_id.strip():
            raise ValueError("document_id is required")
        append_content = re.sub(r"(?m)^# ", "## ", markdown)
        payload = self.runner.run(
            [
                "docs",
                "+update",
                "--api-version",
                "v2",
                "--doc",
                document_id.strip(),
                "--command",
                "append",
                "--doc-format",
                "markdown",
                "--content",
                "-",
            ],
            input_text=append_content,
        ).payload
        blocks = _first_int(payload, "blocks", "block_count", "inserted")
        return {
            "document_id": document_id.strip(),
            "blocks": blocks
            if blocks is not None
            else max(1, append_content.count("\n## ") + 1),
        }


class LarkCliChatAdminClient:
    """Narrow, argv-only adapter for a project collaboration group lifecycle.

    It deliberately owns only create/list/add operations.  It cannot remove
    people, discover arbitrary chats, or execute an opaque command supplied by
    a caller.  The binding service verifies every desired member after a write
    before it marks a project binding active.
    """

    def __init__(self, runner: LarkCliRunner | None = None) -> None:
        self.runner = runner or LarkCliRunner()
        if isinstance(self.runner, LarkCliRunner):
            self.runner.require_minimum_version(
                _MIN_PROJECT_GROUP_VERSION,
                capability="im +chat-members-list required for project-group verification",
            )

    def create_group(
        self,
        *,
        name: str,
        owner_open_id: str,
        bot_app_ids: Sequence[str],
        provisioner_app_id: str,
    ) -> dict[str, Any]:
        group_name = _required(name, "group name")
        owner = _required(owner_open_id, "owner_open_id")
        normalized_bots = _unique_values(bot_app_ids)
        if len(normalized_bots) > 5:
            raise ValueError("at most five bot app ids can be invited at creation")
        command = [
            "im",
            "+chat-create",
            "--name",
            group_name,
            "--owner",
            owner,
            "--set-bot-manager",
        ]
        # The acting bot is already in the chat.  Passing it again is rejected
        # by some tenants, so only invite the other configured product bots.
        invited_bots = [
            app_id for app_id in normalized_bots if app_id != provisioner_app_id
        ]
        if invited_bots:
            command.extend(["--bots", ",".join(invited_bots)])
        payload = self.runner.run(command).payload
        chat = _first_mapping(payload, "chat") or _first_mapping(payload, "data")
        chat_id = _first_string(chat or payload, "chat_id", "open_chat_id")
        if not chat_id:
            raise FeishuTransportError(
                "lark-cli chat create response has no chat_id"
            )
        return {
            **chat,
            "chat_id": chat_id,
            "name": str((chat or payload).get("name") or group_name),
            "owner_open_id": str((chat or payload).get("owner_id") or owner),
        }

    def list_members(self, chat_id: str) -> dict[str, set[str]]:
        chat = _required(chat_id, "chat_id")
        payload = self.runner.run(
            ["im", "+chat-members-list", "--chat-id", chat, "--page-all"]
        ).payload
        users = _member_ids(payload, bucket="users")
        bots = _member_ids(payload, bucket="bots")
        return {"users": users, "bots": bots}

    def add_members(
        self,
        chat_id: str,
        *,
        user_open_ids: Sequence[str] = (),
        bot_app_ids: Sequence[str] = (),
    ) -> dict[str, list[str]]:
        chat = _required(chat_id, "chat_id")
        users = _unique_values(user_open_ids)
        bots = _unique_values(bot_app_ids)
        added = {"users": [], "bots": []}
        if users:
            self._create_members(chat, users, member_id_type="open_id")
            added["users"] = users
        if bots:
            self._create_members(chat, bots, member_id_type="app_id")
            added["bots"] = bots
        return added

    def ensure_members(
        self,
        chat_id: str,
        *,
        owner_open_id: str,
        bot_app_ids: Sequence[str],
    ) -> dict[str, Any]:
        desired_owner = _required(owner_open_id, "owner_open_id")
        desired_bots = _unique_values(bot_app_ids)
        before = self.list_members(chat_id)
        missing_users = (
            [desired_owner] if desired_owner not in before["users"] else []
        )
        missing_bots = [app_id for app_id in desired_bots if app_id not in before["bots"]]
        added = self.add_members(
            chat_id,
            user_open_ids=missing_users,
            bot_app_ids=missing_bots,
        )
        after = self.list_members(chat_id)
        still_missing_users = (
            [desired_owner] if desired_owner not in after["users"] else []
        )
        still_missing_bots = [
            app_id for app_id in desired_bots if app_id not in after["bots"]
        ]
        return {
            "members": after,
            "added": added,
            "missing_users": still_missing_users,
            "missing_bots": still_missing_bots,
            "verified": not still_missing_users and not still_missing_bots,
        }

    def _create_members(
        self,
        chat_id: str,
        ids: Sequence[str],
        *,
        member_id_type: str,
    ) -> None:
        self.runner.run(
            [
                "im",
                "chat.members",
                "create",
                "--params",
                json.dumps(
                    {
                        "chat_id": chat_id,
                        "member_id_type": member_id_type,
                        "succeed_type": 1,
                    },
                    separators=(",", ":"),
                ),
                "--data",
                json.dumps({"id_list": list(ids)}, separators=(",", ":")),
            ]
        )


class LarkCliBitableClient:
    def __init__(self, runner: LarkCliRunner | None = None) -> None:
        self.runner = runner or LarkCliRunner()

    def create_base(
        self,
        *,
        name: str,
        folder_token: str = "",
        time_zone: str = "",
    ) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("base name is required")
        command = ["base", "+base-create", "--name", name.strip()]
        if folder_token.strip():
            command.extend(["--folder-token", folder_token.strip()])
        if time_zone.strip():
            command.extend(["--time-zone", time_zone.strip()])
        payload = self.runner.run(command).payload
        base = _first_mapping(payload, "base", "app") or payload
        token = _first_string(base, "app_token", "base_token", "token")
        if not token:
            raise FeishuTransportError(
                "lark-cli Base create response has no base token"
            )
        return {
            **base,
            "app_token": token,
            "base_token": token,
            "name": str(base.get("name") or name.strip()),
        }

    def create_table(self, app_token: str, *, name: str) -> dict[str, Any]:
        _required(app_token, "app_token")
        _required(name, "table name")
        payload = self.runner.run(
            [
                "base",
                "+table-create",
                "--base-token",
                app_token.strip(),
                "--name",
                name.strip(),
            ]
        ).payload
        table = _first_mapping(payload, "table") or payload
        table_id = _first_string(table, "table_id", "id")
        if not table_id:
            raise FeishuTransportError("lark-cli table create response has no table_id")
        return {
            **table,
            "table_id": table_id,
            "name": str(table.get("name") or name.strip()),
        }

    def ensure_fields(
        self,
        app_token: str,
        table_id: str,
        field_specs: list[dict[str, object]],
    ) -> dict[str, Any]:
        existing = self._list_named_items(
            "field",
            app_token,
            table_id,
            name_keys=("field_name", "name"),
        )
        created: list[str] = []
        for spec in field_specs:
            name = str(spec.get("field_name") or "").strip()
            if not name or name in existing:
                continue
            self.runner.run(
                [
                    "base",
                    "+field-create",
                    "--base-token",
                    app_token.strip(),
                    "--table-id",
                    table_id.strip(),
                    "--json",
                    json.dumps(
                        _field_json(spec), ensure_ascii=False, separators=(",", ":")
                    ),
                ]
            )
            existing.add(name)
            created.append(name)
        return {"existing": sorted(existing - set(created)), "created": created}

    def ensure_views(
        self,
        app_token: str,
        table_id: str,
        view_specs: list[dict[str, str]],
    ) -> dict[str, Any]:
        existing = self._list_named_items(
            "view",
            app_token,
            table_id,
            name_keys=("view_name", "name"),
        )
        created: list[str] = []
        for spec in view_specs:
            name = str(spec.get("view_name") or "").strip()
            if not name or name in existing:
                continue
            self.runner.run(
                [
                    "base",
                    "+view-create",
                    "--base-token",
                    app_token.strip(),
                    "--table-id",
                    table_id.strip(),
                    "--json",
                    json.dumps(
                        {"name": name, "type": str(spec.get("view_type") or "grid")},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ]
            )
            existing.add(name)
            created.append(name)
        return {"existing": sorted(existing - set(created)), "created": created}

    def ensure_view_layouts(
        self,
        app_token: str,
        table_id: str,
        layout_specs: list[dict[str, object]],
    ) -> dict[str, Any]:
        existing = self._list_named_items(
            "view",
            app_token,
            table_id,
            name_keys=("view_name", "name"),
        )
        view_ids = self._list_view_ids(app_token, table_id)
        configured: list[str] = []
        skipped: list[str] = []
        for spec in layout_specs:
            name = str(spec.get("view_name") or "").strip()
            if not name or name not in existing:
                if name:
                    skipped.append(name)
                continue
            applied = 0
            for key, command in (
                ("visible_fields", "+view-set-visible-fields"),
                ("filter_config", "+view-set-filter"),
                ("group_config", "+view-set-group"),
                ("sort_config", "+view-set-sort"),
                ("timebar", "+view-set-timebar"),
            ):
                value = spec.get(key)
                if not isinstance(value, (list, dict)):
                    continue
                body = {key: value} if isinstance(value, list) else value
                self.runner.run(
                    [
                        "base",
                        command,
                        "--base-token",
                        app_token.strip(),
                        "--table-id",
                        table_id.strip(),
                        "--view-id",
                        view_ids.get(name, name),
                        "--json",
                        json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                    ]
                )
                applied += 1
            if applied:
                configured.append(name)
        return {"configured": configured, "skipped": skipped}

    def _list_view_ids(self, app_token: str, table_id: str) -> dict[str, str]:
        payload = self.runner.run([
            "base", "+view-list", "--base-token", app_token.strip(),
            "--table-id", table_id.strip(), "--limit", "200",
        ]).payload
        result: dict[str, str] = {}
        for item in _all_mappings(payload):
            name = _first_string(item, "view_name", "name")
            view_id = _first_string(item, "view_id", "id")
            if name and view_id:
                result[name] = view_id
        return result

    def find_record_id(
        self,
        app_token: str,
        table_id: str,
        *,
        key_field: str,
        key_value: str,
    ) -> str:
        _required(key_field, "key_field")
        _required(key_value, "key_value")
        payload = self.runner.run(
            [
                "base",
                "+record-search",
                "--base-token",
                app_token.strip(),
                "--table-id",
                table_id.strip(),
                "--keyword",
                key_value,
                "--search-field",
                key_field,
                "--field-id",
                key_field,
                "--limit",
                "10",
            ]
        ).payload
        matches = _exact_record_matches(payload, key_field, key_value)
        if len(matches) > 1:
            raise FeishuTransportError(
                f"multiple Feishu records match {key_field}={key_value!r}"
            )
        return matches[0] if matches else ""

    def create_record(
        self, app_token: str, table_id: str, fields: dict[str, Any]
    ) -> str:
        return self._upsert_record(app_token, table_id, fields=fields)

    def batch_create_records(self, app_token: str, table_id: str, records: list[dict[str, Any]]) -> list[str]:
        payload = self.runner.run([
            "base", "+record-batch-create", "--base-token", app_token.strip(),
            "--table-id", table_id.strip(), "--json",
            json.dumps({"create_records": records}, ensure_ascii=False, separators=(",", ":")),
        ]).payload
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        values = data.get("record_id_list") if isinstance(data, dict) else []
        return [str(value) for value in values] if isinstance(values, list) else []

    def batch_update_records(self, app_token: str, table_id: str, records: dict[str, dict[str, Any]]) -> None:
        self.runner.run([
            "base", "+record-batch-update", "--base-token", app_token.strip(),
            "--table-id", table_id.strip(), "--json",
            json.dumps({"update_records": records}, ensure_ascii=False, separators=(",", ":")),
        ])

    def update_record(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> str:
        record_id = _required(record_id, "record_id")
        payload = self.runner.run(
            [
                "base",
                "+record-get",
                "--base-token",
                app_token.strip(),
                "--table-id",
                table_id.strip(),
                "--record-id",
                record_id,
                "--field-id",
                next(iter(fields), ""),
            ]
        ).payload
        data = payload.get("data")
        missing = data.get("record_not_found") if isinstance(data, dict) else []
        if isinstance(missing, list) and record_id in {
            str(value) for value in missing
        }:
            raise FeishuTransportError("record has been deleted")
        return self._upsert_record(
            app_token,
            table_id,
            record_id=record_id,
            fields=fields,
        )

    def _upsert_record(
        self,
        app_token: str,
        table_id: str,
        *,
        fields: dict[str, Any],
        record_id: str = "",
    ) -> str:
        command = [
            "base",
            "+record-upsert",
            "--base-token",
            app_token.strip(),
            "--table-id",
            table_id.strip(),
            "--json",
            json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
        ]
        if record_id.strip():
            command.extend(["--record-id", record_id.strip()])
        payload = self.runner.run(command).payload
        found = _first_string(payload, "record_id", "id")
        if not found:
            record = _first_mapping(payload, "record")
            found = _first_string(record, "record_id", "id")
        if not found:
            found = _first_list_string(payload, "record_id_list")
        if not found:
            found = record_id.strip()
        if not found:
            raise FeishuTransportError(
                "lark-cli record upsert response has no record_id"
            )
        return found

    def _list_named_items(
        self,
        kind: str,
        app_token: str,
        table_id: str,
        *,
        name_keys: tuple[str, ...],
    ) -> set[str]:
        payload = self.runner.run(
            [
                "base",
                f"+{kind}-list",
                "--base-token",
                app_token.strip(),
                "--table-id",
                table_id.strip(),
                "--limit",
                "200",
            ]
        ).payload
        names: set[str] = set()
        for item in _all_mappings(payload):
            value = _first_string(item, *name_keys)
            if value:
                names.add(value)
        return names


def _field_json(spec: dict[str, object]) -> dict[str, Any]:
    name = str(spec.get("field_name") or "").strip()
    field_type = int(spec.get("type") or 1)
    if field_type == 3:
        options = []
        prop = spec.get("property")
        if isinstance(prop, dict):
            for item in prop.get("options") or []:
                if isinstance(item, dict) and str(item.get("name") or "").strip():
                    options.append({"name": str(item["name"])})
        return {"name": name, "type": "select", "multiple": False, "options": options}
    if field_type == 5:
        return {"name": name, "type": "datetime"}
    return {"name": name, "type": "text"}


def _exact_record_matches(
    payload: dict[str, Any],
    key_field: str,
    key_value: str,
) -> list[str]:
    matches: list[str] = []
    data = payload.get("data")
    if isinstance(data, dict):
        fields = data.get("fields")
        record_ids = data.get("record_id_list")
        rows = data.get("data")
        if (
            isinstance(fields, list)
            and isinstance(record_ids, list)
            and isinstance(rows, list)
        ):
            try:
                index = [str(value) for value in fields].index(key_field)
            except ValueError:
                index = -1
            if index >= 0:
                for record_id, row in zip(record_ids, rows, strict=False):
                    if (
                        isinstance(row, list)
                        and index < len(row)
                        and str(row[index]) == key_value
                    ):
                        matches.append(str(record_id))
    for item in _all_mappings(payload):
        fields = item.get("fields")
        if isinstance(fields, dict) and str(fields.get(key_field)) == key_value:
            record_id = _first_string(item, "record_id", "id")
            if record_id:
                matches.append(record_id)
    return list(dict.fromkeys(matches))


def _first_mapping(payload: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return dict(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, dict):
                return dict(value)
    return {}


def _all_mappings(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_all_mappings(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_all_mappings(child))
    return found


def _first_string(payload: Mapping[str, Any], *keys: str) -> str:
    for mapping in _all_mappings(payload):
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _first_int(payload: Mapping[str, Any], *keys: str) -> int | None:
    for mapping in _all_mappings(payload):
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def _first_list_string(payload: Mapping[str, Any], *keys: str) -> str:
    for mapping in _all_mappings(payload):
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        return item.strip()
    return ""


def _unique_values(values: Sequence[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique


def _member_ids(payload: Mapping[str, Any], *, bucket: str) -> set[str]:
    """Extract member IDs from lark-cli's separate users/bots result buckets."""
    keys = (
        ("app_id", "member_id", "id")
        if bucket == "bots"
        else ("member_id", "open_id", "id")
    )
    values: set[str] = set()
    for mapping in _all_mappings(payload):
        members = mapping.get(bucket)
        if not isinstance(members, list):
            continue
        for item in members:
            if isinstance(item, str) and item.strip():
                values.add(item.strip())
                continue
            if not isinstance(item, dict):
                continue
            for key in keys:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    values.add(value.strip())
                    break
    return values


def _required(value: str, label: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{label} is required")
    return result
