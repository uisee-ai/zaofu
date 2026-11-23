"""Project-scoped Web terminal lifecycle service."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import time
from typing import Sequence
from uuid import uuid4

from zf.core.config.schema import RuntimeWebTerminalConfig
from zf.web.terminal_backend import (
    HerdrProjectRuntime,
    TERMINAL_PROVIDER_KINDS,
    TerminalAgent,
    TerminalBackend,
    TerminalBridgeSpec,
    TerminalCapability,
    TerminalRuntimeError,
    TerminalSessionRecord,
)
from zf.web.terminal_backend_herdr import HerdrTerminalBackend
from zf.web.terminal_registry import TerminalRegistry, TerminalRegistryError
from zf.web.terminal_usage import TerminalUsageBinding, TerminalUsageService
from zf.integrations.controller.terminal_execution import managed_terminal


_SLOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_title(raw: str, fallback: str) -> str:
    value = "".join(char for char in raw if char.isprintable()).strip()
    return (value or fallback)[:80]


def _validated_title(raw: str) -> str:
    value = _clean_title(raw, "")
    if not value:
        raise TerminalRuntimeError(
            "invalid_terminal_title",
            "terminal title must contain printable characters",
            status_code=422,
        )
    return value


class TerminalService:
    def __init__(
        self,
        *,
        project_id: str,
        project_root: Path,
        state_dir: Path,
        config: RuntimeWebTerminalConfig,
        allowed_providers: Sequence[str],
        backend: TerminalBackend | None = None,
        usage_service: TerminalUsageService | None = None,
        usage_binding_wait_seconds: float = 2.0,
    ) -> None:
        self.project_id = project_id
        self.project_root = Path(project_root).resolve(strict=False)
        self.state_dir = Path(state_dir).resolve(strict=False)
        self.config = config
        self.allowed_providers = tuple(allowed_providers)
        self.registry = TerminalRegistry(
            self.state_dir,
            project_id=project_id,
            project_root=self.project_root,
        )
        self.backend = backend or HerdrTerminalBackend(
            config.herdr_binary,
            minimum_version=config.minimum_herdr_version,
        )
        self.usage = usage_service or TerminalUsageService(state_dir=self.state_dir)
        self.usage_binding_wait_seconds = max(float(usage_binding_wait_seconds), 0.0)
        digest = hashlib.sha256(
            f"{project_id}\0{self.project_root}".encode("utf-8")
        ).hexdigest()[:16]
        self.herdr_session_name = f"zf-{digest}"
        self._capability: tuple[float, TerminalCapability] | None = None

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise TerminalRuntimeError(
                "web_terminal_disabled",
                "runtime.web_terminal.enabled is false",
                status_code=409,
            )

    def capability(self, *, refresh: bool = False) -> TerminalCapability:
        if not self.config.enabled:
            return TerminalCapability(
                available=False,
                binary=self.config.herdr_binary,
                reason="runtime.web_terminal.enabled is false",
            )
        now = time.monotonic()
        if not refresh and self._capability is not None and now - self._capability[0] < 10:
            return self._capability[1]
        capability = self.backend.probe()
        self._capability = (now, capability)
        return capability

    def _require_capability(self) -> TerminalCapability:
        self._require_enabled()
        capability = self.capability()
        if not capability.available:
            raise TerminalRuntimeError(
                "web_terminal_unavailable",
                capability.reason or "Herdr terminal bridge is unavailable",
                status_code=503,
            )
        return capability

    def _records(self, value: dict[str, object]) -> list[dict[str, object]]:
        sessions = value.get("sessions")
        if not isinstance(sessions, list):
            raise TerminalRegistryError("terminal registry sessions must be a list")
        if any(not isinstance(item, dict) for item in sessions):
            raise TerminalRegistryError("terminal registry session must be an object")
        return sessions  # type: ignore[return-value]

    def _runtime(self, value: dict[str, object]) -> dict[str, object]:
        runtime = value.get("runtime")
        if not isinstance(runtime, dict):
            raise TerminalRegistryError("terminal registry runtime must be an object")
        return runtime

    def list_sessions(self) -> dict[str, object]:
        if not self.config.enabled:
            return {
                "schema_version": "terminal-sessions.v1",
                "enabled": False,
                "backend": self.config.backend,
                "allowed_providers": list(self.allowed_providers),
                "allow_takeover": self.config.allow_takeover,
                "capability": self.capability().projection(),
                "sessions": [],
            }
        value = self.registry.read()
        records = [TerminalSessionRecord.from_mapping(item) for item in self._records(value)]
        records.sort(key=lambda item: (item.created_at, item.session_id))
        live = self._live_agents(records)
        records = self._refresh_pending_usage_bindings(records, live)
        return {
            "schema_version": "terminal-sessions.v1",
            "enabled": self.config.enabled,
            "backend": self.config.backend,
            "allowed_providers": list(self.allowed_providers),
            "allow_takeover": self.config.allow_takeover,
            "capability": self.capability().projection(),
            "sessions": [
                {
                    **self.project_session(record),
                    "agent_status": live[record.session_id].status
                    if record.session_id in live
                    else "",
                }
                for record in records
            ],
        }

    def _live_agents(
        self, records: list[TerminalSessionRecord]
    ) -> dict[str, TerminalAgent]:
        active = [record for record in records if record.state == "active"]
        if not active:
            return {}
        try:
            rows = self.backend.list_agents(
                HerdrProjectRuntime(self.herdr_session_name)
            )
        except TerminalRuntimeError:
            return {}
        by_identity = {
            (agent.terminal_id, agent.name, agent.provider): agent for agent in rows
        }
        live = {}
        for record in active:
            agent = by_identity.get(
                (record.terminal_id, record.agent_name, record.provider_kind)
            )
            if agent:
                live[record.session_id] = agent
        return live

    def _refresh_pending_usage_bindings(
        self,
        records: list[TerminalSessionRecord],
        live: dict[str, TerminalAgent],
    ) -> list[TerminalSessionRecord]:
        updates: dict[str, tuple[TerminalSessionRecord, TerminalUsageBinding, str]] = {}
        for index, record in enumerate(records):
            agent = live.get(record.session_id)
            # Keep the first native identity for this terminal generation, even if
            # its transcript arrives later or the hook subsequently reports /new.
            native_id = record.herdr_native_id or (agent.native_id if agent else "")
            if record.provider == "codex" and native_id:
                if record.usage_binding_status == "conflict" or (
                    record.herdr_native_id and record.provider_session_id == native_id
                ):
                    continue
                if (
                    record.provider_session_id
                    and record.provider_session_id != native_id
                ):
                    binding = TerminalUsageBinding(
                        "conflict",
                        reason="Herdr session identity differs from the previously bound Codex session",
                    )
                else:
                    try:
                        binding = self.usage.bind_codex_reference(native_id, self.project_root)
                    except (OSError, ValueError, RuntimeError):
                        continue
                    if record.provider_session_id and binding.status != "bound":
                        # A temporarily unavailable transcript must retain its existing binding.
                        continue
                if binding.status != "pending" or record.herdr_native_id != native_id:
                    updates[record.session_id] = (record, binding, native_id)
                continue
            if (
                record.provider != "codex"
                or record.provider_session_id
                or record.usage_binding_status != "pending"
                or record.usage_binding_started_at_ns <= 0
            ):
                continue
            ended_before_ns = min(
                (
                    candidate.usage_binding_started_at_ns
                    for candidate in records[index + 1 :]
                    if candidate.provider == record.provider
                    and candidate.usage_binding_started_at_ns
                    > record.usage_binding_started_at_ns
                ),
                default=0,
            )
            try:
                binding = self.usage.complete_pending_binding(
                    record,
                    ended_before_ns=ended_before_ns,
                )
            except Exception:
                continue
            if binding.status == "bound":
                updates[record.session_id] = (record, binding, "")
        if not updates:
            return records

        def persist(value: dict[str, object]) -> list[TerminalSessionRecord]:
            for item in self._records(value):
                update = updates.get(str(item.get("session_id") or ""))
                if update is None:
                    continue
                observed, binding, native_id = update
                if observed.provider_session_id != item.get(
                    "provider_session_id", ""
                ) or observed.herdr_native_id != item.get("herdr_native_id", ""):
                    continue
                item["herdr_native_id"] = native_id
                item["provider_session_id"] = binding.provider_session_id
                item["provider_session_path"] = binding.provider_session_path
                item["usage_binding_status"] = binding.status
                item["usage_binding_reason"] = binding.reason
                item["updated_at"] = _now()
            return [
                TerminalSessionRecord.from_mapping(item)
                for item in self._records(value)
            ]

        refreshed = self.registry.mutate(persist)
        refreshed.sort(key=lambda item: (item.created_at, item.session_id))
        return refreshed

    def project_session(self, record: TerminalSessionRecord) -> dict[str, object]:
        """Return a public session projection with best-effort live usage."""

        return record.projection(usage=self._usage_snapshot(record))

    def _usage_snapshot(self, record: TerminalSessionRecord) -> dict[str, object]:
        try:
            return self.usage.snapshot(record)
        except Exception:  # Usage telemetry must never break terminal control.
            return {
                "schema_version": "terminal-usage.v1",
                "status": "unavailable",
                "source": "provider_transcript",
                "provider": record.provider,
                "accounting_mode": "unknown",
                "model": "",
                "models": [],
                "fresh_input_tokens": None,
                "cached_input_tokens": None,
                "cache_creation_input_tokens": None,
                "input_tokens": None,
                "output_tokens": None,
                "reasoning_output_tokens": None,
                "total_tokens": None,
                "cost_usd": None,
                "cost_kind": "unavailable",
                "context_usage_ratio": None,
                "observed_at": "",
                "reason": "terminal usage projection failed",
            }

    def settle_usage(self, session_id: str) -> dict[str, object]:
        """Persist the latest provider counters without affecting workflow cost."""

        record = self.get_session(session_id)
        try:
            return self.usage.settle(record)
        except Exception:  # Usage telemetry must never break terminal control.
            return self._usage_snapshot(record)

    def get_session(self, session_id: str) -> TerminalSessionRecord:
        value = self.registry.read()
        for item in self._records(value):
            if item.get("session_id") == session_id:
                return TerminalSessionRecord.from_mapping(item)
        raise TerminalRuntimeError(
            "terminal_session_not_found",
            f"terminal session {session_id!r} not found",
            status_code=404,
        )

    @managed_terminal
    def create_session(
        self,
        *,
        provider: str,
        slot: str,
        title: str = "",
    ) -> TerminalSessionRecord:
        self._require_capability()
        if provider not in self.allowed_providers:
            raise TerminalRuntimeError(
                "terminal_provider_not_allowed",
                f"provider {provider!r} is not allowed for this project",
                status_code=422,
            )
        provider_kind = TERMINAL_PROVIDER_KINDS.get(provider)
        if provider_kind is None:
            raise TerminalRuntimeError(
                "terminal_provider_unsupported",
                f"provider {provider!r} has no Herdr mapping",
                status_code=422,
            )
        slot = slot.strip()
        if not _SLOT_RE.fullmatch(slot):
            raise TerminalRuntimeError(
                "invalid_terminal_slot",
                "slot must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}",
                status_code=422,
            )

        def create(value: dict[str, object]) -> TerminalSessionRecord:
            sessions = self._records(value)
            same_slot = [item for item in sessions if item.get("slot") == slot]
            active = next((item for item in same_slot if item.get("state") == "active"), None)
            if active is not None:
                if active.get("provider") != provider:
                    raise TerminalRuntimeError(
                        "terminal_slot_conflict",
                        f"slot {slot!r} already runs {active.get('provider')!r}",
                    )
                return TerminalSessionRecord.from_mapping(active)
            active_count = sum(item.get("state") == "active" for item in sessions)
            if active_count >= self.config.max_sessions:
                raise TerminalRuntimeError(
                    "terminal_session_limit",
                    f"project terminal session limit is {self.config.max_sessions}",
                    status_code=429,
                )
            generation = max(
                (int(item.get("generation") or 0) for item in same_slot),
                default=0,
            ) + 1
            session_id = f"term-{uuid4().hex[:16]}"
            agent_name = f"zf{session_id.removeprefix('term-')[:14]}"
            launch = self.usage.prepare_launch(provider, self.project_root)
            runtime = self.backend.ensure_project_runtime(self.herdr_session_name)
            runtime_state = self._runtime(value)
            registered_workspace = str(runtime_state.get("workspace_id") or "")
            resource = self.backend.create_terminal(
                runtime=runtime,
                workspace_id=registered_workspace,
                project_root=self.project_root,
                label=_clean_title(title, f"{provider} · {slot}"),
                agent_name=agent_name,
                provider_kind=provider_kind,
                provider_args=launch.provider_args,
                start_timeout_seconds=self.config.provider_start_timeout_seconds,
            )
            try:
                if resource.native_id and provider == "codex":
                    binding = self.usage.bind_codex_reference(
                        resource.native_id, self.project_root
                    )
                else:
                    binding = self.usage.complete_launch(
                        launch, wait_seconds=self.usage_binding_wait_seconds
                    )
            except Exception:
                binding = TerminalUsageBinding(
                    status="unavailable",
                    reason="provider session binding failed",
                )
            timestamp = _now()
            record = TerminalSessionRecord(
                session_id=session_id,
                slot=slot,
                title=_clean_title(title, f"{provider} · {slot}"),
                provider=provider,
                provider_kind=provider_kind,
                project_id=self.project_id,
                project_root=str(self.project_root),
                state="active",
                generation=generation,
                created_at=timestamp,
                updated_at=timestamp,
                herdr_session=runtime.session_name,
                workspace_id=resource.workspace_id,
                tab_id=resource.tab_id,
                pane_id=resource.pane_id,
                terminal_id=resource.terminal_id,
                herdr_native_id=resource.native_id,
                agent_name=resource.agent_name,
                provider_session_id=binding.provider_session_id,
                provider_session_path=binding.provider_session_path,
                usage_binding_status=binding.status,
                usage_binding_reason=binding.reason,
                usage_binding_started_at_ns=launch.binding_started_at_ns,
            )
            sessions.append(asdict(record))
            runtime_state.update(
                {
                    "backend": "herdr",
                    "herdr_session": runtime.session_name,
                    "workspace_id": resource.workspace_id,
                    "server_pid": runtime.server_pid,
                }
            )
            return record

        return self.registry.mutate(create)

    def rename_session(self, session_id: str, title: str) -> TerminalSessionRecord:
        capability = self._require_capability()
        if not capability.tab_rename:
            raise TerminalRuntimeError(
                "terminal_rename_unavailable",
                "the configured Herdr does not support tab rename",
                status_code=409,
            )
        clean_title = _validated_title(title)

        def rename(value: dict[str, object]) -> TerminalSessionRecord:
            sessions = self._records(value)
            item = next((row for row in sessions if row.get("session_id") == session_id), None)
            if item is None:
                raise TerminalRuntimeError(
                    "terminal_session_not_found",
                    f"terminal session {session_id!r} not found",
                    status_code=404,
                )
            record = TerminalSessionRecord.from_mapping(item)
            if record.state != "active":
                raise TerminalRuntimeError(
                    "terminal_session_not_active",
                    f"terminal session {session_id!r} is not active",
                )
            if record.title == clean_title:
                return record
            self.backend.rename_terminal(
                runtime=HerdrProjectRuntime(session_name=record.herdr_session),
                tab_id=record.tab_id,
                title=clean_title,
            )
            item["title"] = clean_title
            item["updated_at"] = _now()
            return TerminalSessionRecord.from_mapping(item)

        return self.registry.mutate(rename)

    def stop_session(self, session_id: str) -> TerminalSessionRecord:
        self._require_capability()

        def stop(value: dict[str, object]) -> TerminalSessionRecord:
            sessions = self._records(value)
            item = next((row for row in sessions if row.get("session_id") == session_id), None)
            if item is None:
                raise TerminalRuntimeError(
                    "terminal_session_not_found",
                    f"terminal session {session_id!r} not found",
                    status_code=404,
                )
            record = TerminalSessionRecord.from_mapping(item)
            if record.state != "active":
                return record
            runtime = HerdrProjectRuntime(session_name=record.herdr_session)
            self.backend.stop_terminal(runtime=runtime, tab_id=record.tab_id)
            timestamp = _now()
            item["state"] = "stopped"
            item["stopped_at"] = timestamp
            item["updated_at"] = timestamp
            if not any(
                row is not item and row.get("state") == "active" for row in sessions
            ):
                self._runtime(value)["workspace_id"] = ""
            return TerminalSessionRecord.from_mapping(item)

        record = self.registry.mutate(stop)
        self.settle_usage(record.session_id)
        return record

    def record_action_receipt(self, action: str, session_id: str) -> dict[str, object]:
        """Persist a coarse audit receipt without storing terminal content."""

        if action not in {"create", "rename", "stop", "takeover"}:
            raise TerminalRuntimeError(
                "invalid_terminal_action_receipt", f"unsupported receipt action: {action}"
            )

        def record(value: dict[str, object]) -> dict[str, object]:
            if not any(
                item.get("session_id") == session_id for item in self._records(value)
            ):
                raise TerminalRuntimeError(
                    "terminal_session_not_found",
                    f"terminal session {session_id!r} not found",
                    status_code=404,
                )
            receipt = {
                "schema_version": "terminal-action-receipt.v1",
                "receipt_id": f"trc-{uuid4().hex[:16]}",
                "action": action,
                "project_id": self.project_id,
                "session_id": session_id,
                "at": _now(),
            }
            receipts = value.setdefault("receipts", [])
            if not isinstance(receipts, list):
                raise TerminalRegistryError("terminal registry receipts must be a list")
            receipts.append(receipt)
            del receipts[:-200]
            return receipt

        return self.registry.mutate(record)

    def reconcile(self) -> list[TerminalSessionRecord]:
        self._require_capability()

        def reconcile_value(value: dict[str, object]) -> list[TerminalSessionRecord]:
            records: list[TerminalSessionRecord] = []
            for item in self._records(value):
                record = TerminalSessionRecord.from_mapping(item)
                if record.state == "active" and not self.backend.terminal_exists(
                    runtime=HerdrProjectRuntime(session_name=record.herdr_session),
                    tab_id=record.tab_id,
                ):
                    timestamp = _now()
                    item["state"] = "missing"
                    item["updated_at"] = timestamp
                    diagnostics = list(item.get("diagnostics") or [])
                    diagnostics.append("Herdr tab was not found during reconciliation")
                    item["diagnostics"] = diagnostics[-8:]
                    record = TerminalSessionRecord.from_mapping(item)
                records.append(record)
            if not any(record.state == "active" for record in records):
                self._runtime(value)["workspace_id"] = ""
            return records

        records = self.registry.mutate(reconcile_value)
        for record in records:
            try:
                self.usage.settle(record)
            except Exception:
                continue
        return records

    def bridge_spec(
        self,
        session_id: str,
        *,
        mode: str,
        takeover: bool,
        cols: int,
        rows: int,
    ) -> TerminalBridgeSpec:
        capability = self._require_capability()
        if mode not in {"observe", "control"}:
            raise TerminalRuntimeError(
                "invalid_attachment_mode", f"invalid attachment mode: {mode}", status_code=422
            )
        if takeover and (mode != "control" or not self.config.allow_takeover):
            raise TerminalRuntimeError(
                "terminal_takeover_disabled",
                "controller takeover is disabled for this project",
                status_code=403,
            )
        if (
            not isinstance(cols, int)
            or isinstance(cols, bool)
            or not isinstance(rows, int)
            or isinstance(rows, bool)
            or not 1 <= cols <= self.config.max_cols
            or not 1 <= rows <= self.config.max_rows
        ):
            raise TerminalRuntimeError(
                "invalid_terminal_geometry",
                f"geometry exceeds {self.config.max_cols}x{self.config.max_rows}",
                status_code=422,
            )
        record = self.get_session(session_id)
        if record.state != "active":
            raise TerminalRuntimeError(
                "terminal_session_not_active",
                f"terminal session is {record.state}",
                status_code=409,
            )
        spec = self.backend.bridge_spec(
            runtime=HerdrProjectRuntime(session_name=record.herdr_session),
            target=record.terminal_id or record.pane_id,
            mode=mode,
            takeover=takeover,
            cols=cols,
            rows=rows,
        )
        return replace(spec, mouse=mode == "control" and capability.mouse)
