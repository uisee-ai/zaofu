"""Provider-native usage projection for interactive Web Terminal sessions.

The mutable tab title is presentation only. Accounting is keyed by the stable
ZaoFu TerminalSession generation plus the provider-native session identity.
Raw prompts and terminal bytes are never read; only structured usage metadata
from provider transcript files is consumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
import time
from typing import Any, Iterable
from uuid import uuid4

from zf.core.cost.calculator import calculate_usage_cost
from zf.core.cost.catalog import PricingCatalogStore
from zf.core.cost.tracker import CostTracker
from zf.core.state.locks import locked_path
from zf.runtime.provider_usage import canonical_usage_tokens
from zf.web.terminal_backend import TerminalSessionRecord


TERMINAL_COST_FILENAME = "terminal-cost.jsonl"
_QUALIFIED_PROVIDERS = frozenset({"claude-code", "codex"})
_CODEX_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)
_CODEX_SHELL_ID_RE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.",
    re.IGNORECASE,
)
_BINDING_TIMESTAMP_SLOP_NS = 1_000_000_000


@dataclass(frozen=True)
class TerminalUsageLaunch:
    provider: str
    project_root: Path
    binding_started_at_ns: int = 0
    provider_session_id: str = ""
    provider_args: tuple[str, ...] = ()
    paths_before: frozenset[str] = frozenset()


@dataclass(frozen=True)
class TerminalUsageBinding:
    status: str
    provider_session_id: str = ""
    provider_session_path: str = ""
    reason: str = ""


@dataclass
class _UsageBucket:
    model: str
    fresh_input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    occurred_at: str = ""

    def add(self, receipt: dict[str, Any], *, occurred_at: str) -> None:
        self.fresh_input_tokens += int(receipt["fresh_input_tokens"])
        self.cache_read_tokens += int(receipt["cache_read_input_tokens"])
        self.cache_creation_tokens += int(receipt["cache_creation_input_tokens"])
        self.output_tokens += int(receipt["output_tokens"])
        self.reasoning_output_tokens += int(receipt["reasoning_output_tokens"])
        if occurred_at:
            self.occurred_at = occurred_at

    @property
    def input_tokens(self) -> int:
        return (
            self.fresh_input_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class _UsageSnapshot:
    status: str
    source: str
    provider: str
    accounting_mode: str = "unknown"
    buckets: dict[str, _UsageBucket] = field(default_factory=dict)
    context_usage_ratio: float | None = None
    observed_at: str = ""
    reason: str = ""


class TerminalUsageService:
    """Resolve, read, price, and idempotently settle one terminal generation."""

    def __init__(
        self,
        *,
        state_dir: Path,
        claude_projects_root: Path | None = None,
        codex_sessions_root: Path | None = None,
        codex_shell_snapshots_root: Path | None = None,
    ) -> None:
        self.state_dir = Path(state_dir).resolve(strict=False)
        claude_config_root = Path(
            os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
        )
        codex_config_root = Path(
            os.environ.get("CODEX_HOME") or Path.home() / ".codex"
        )
        self.claude_projects_root = Path(
            claude_projects_root or claude_config_root / "projects"
        ).resolve(strict=False)
        self.codex_sessions_root = Path(
            codex_sessions_root or codex_config_root / "sessions"
        ).resolve(strict=False)
        self.codex_shell_snapshots_root = Path(
            codex_shell_snapshots_root or codex_config_root / "shell_snapshots"
        ).resolve(strict=False)
        self.cost_path = self.state_dir / TERMINAL_COST_FILENAME
        self._cache: dict[tuple[str, str, int, int], _UsageSnapshot] = {}
        self._lock = RLock()

    def prepare_launch(self, provider: str, project_root: Path) -> TerminalUsageLaunch:
        provider = str(provider or "").strip()
        root = Path(project_root).resolve(strict=False)
        binding_started_at_ns = time.time_ns()
        if provider == "claude-code":
            provider_session_id = str(uuid4())
            return TerminalUsageLaunch(
                provider=provider,
                project_root=root,
                binding_started_at_ns=binding_started_at_ns,
                provider_session_id=provider_session_id,
                provider_args=("--session-id", provider_session_id),
            )
        if provider == "codex":
            return TerminalUsageLaunch(
                provider=provider,
                project_root=root,
                binding_started_at_ns=binding_started_at_ns,
                paths_before=frozenset(
                    str(path) for path in self._codex_binding_paths()
                ),
            )
        return TerminalUsageLaunch(
            provider=provider,
            project_root=root,
            binding_started_at_ns=binding_started_at_ns,
        )

    def complete_launch(
        self,
        launch: TerminalUsageLaunch,
        *,
        wait_seconds: float = 2.0,
    ) -> TerminalUsageBinding:
        if launch.provider == "claude-code":
            escaped = (
                "-" + str(launch.project_root).lstrip("/").replace(
                    "/", "-"
                ).replace(".", "-").replace("_", "-")
            )
            path = self.claude_projects_root / escaped / (
                f"{launch.provider_session_id}.jsonl"
            )
            return TerminalUsageBinding(
                status="bound",
                provider_session_id=launch.provider_session_id,
                provider_session_path=str(path),
            )
        if launch.provider != "codex":
            return TerminalUsageBinding(
                status="unsupported",
                reason="provider usage reader is not qualified",
            )

        deadline = time.monotonic() + max(wait_seconds, 0.0)
        candidates: list[Path] = []
        while True:
            candidates = sorted(
                path
                for path in self._codex_binding_paths()
                if str(path) not in launch.paths_before
            )
            candidates = self._codex_binding_candidates(
                candidates,
                project_root=launch.project_root,
                started_at_ns=launch.binding_started_at_ns,
                allow_shell_snapshots=True,
            )
            discovered = self._codex_candidate_ids(candidates)
            if len(discovered) == 1 or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        discovered = self._codex_candidate_ids(candidates)
        if len(discovered) != 1:
            return TerminalUsageBinding(
                status="unavailable" if discovered else "pending",
                reason=(
                    "provider session binding was ambiguous"
                    if discovered
                    else "provider session binding awaits provider metadata"
                ),
            )
        native_id, bound_paths = next(iter(discovered.items()))
        rollout = next(
            (path for path in bound_paths if path.suffix == ".jsonl"),
            None,
        )
        return TerminalUsageBinding(
            status="bound",
            provider_session_id=native_id,
            provider_session_path=str(rollout) if rollout is not None else "",
        )

    def complete_pending_binding(
        self,
        record: TerminalSessionRecord,
        *,
        ended_before_ns: int = 0,
    ) -> TerminalUsageBinding:
        """Resolve a Codex transcript created after browser-side startup gates."""

        started_at_ns = int(record.usage_binding_started_at_ns or 0)
        if record.provider != "codex" or started_at_ns <= 0:
            return TerminalUsageBinding(
                status="unavailable",
                reason="provider session binding has no launch boundary",
            )
        candidates = self._codex_binding_candidates(
            self._codex_paths(),
            project_root=Path(record.project_root),
            started_at_ns=started_at_ns,
            ended_before_ns=ended_before_ns,
        )
        discovered = self._codex_candidate_ids(candidates)
        if not discovered:
            return TerminalUsageBinding(
                status="pending",
                reason="provider session binding awaits provider metadata",
            )
        if len(discovered) != 1:
            return TerminalUsageBinding(
                status="unavailable",
                reason="provider session binding was ambiguous",
            )
        native_id, bound_paths = next(iter(discovered.items()))
        rollout = next(
            (path for path in bound_paths if path.suffix == ".jsonl"),
            None,
        )
        return TerminalUsageBinding(
            status="bound",
            provider_session_id=native_id,
            provider_session_path=str(rollout) if rollout is not None else "",
        )

    def snapshot(self, record: TerminalSessionRecord) -> dict[str, object]:
        internal = self._read_snapshot(record)
        if internal.status not in {"observed", "awaiting_usage"}:
            fallback = self._ledger_snapshot(record)
            if fallback is not None:
                return fallback
        return self._project(internal)

    def settle(self, record: TerminalSessionRecord) -> dict[str, object]:
        internal = self._read_snapshot(record)
        if internal.status != "observed":
            return self.snapshot(record)
        instance_id = self.instance_id(record)
        provider_session_id = record.provider_session_id
        tracker = CostTracker(self.cost_path)
        with self._lock, locked_path(self.cost_path):
            for model, bucket in sorted(internal.buckets.items()):
                series_id = ":".join(
                    (
                        "terminal",
                        record.project_id,
                        record.session_id,
                        f"g{record.generation}",
                        provider_session_id,
                        model,
                    )
                )
                sample_id = self._sample_id(series_id, bucket)
                tracker.record_cumulative_usage(
                    role="web-terminal",
                    instance_id=instance_id,
                    input_tokens=bucket.fresh_input_tokens,
                    output_tokens=bucket.output_tokens,
                    model=model,
                    backend=record.provider,
                    cache_creation_tokens=bucket.cache_creation_tokens,
                    cache_read_tokens=bucket.cache_read_tokens,
                    provider=internal.provider,
                    accounting_mode=internal.accounting_mode,
                    occurred_at=bucket.occurred_at or internal.observed_at,
                    usage_sample_id=sample_id,
                    usage_series_id=series_id,
                )
        return self._project(internal)

    @staticmethod
    def instance_id(record: TerminalSessionRecord) -> str:
        return (
            f"terminal:{record.project_id}:{record.session_id}:g{record.generation}"
        )

    def _read_snapshot(self, record: TerminalSessionRecord) -> _UsageSnapshot:
        if record.provider not in _QUALIFIED_PROVIDERS:
            return _UsageSnapshot(
                status="unsupported",
                source="provider_transcript",
                provider=record.provider,
                reason="provider usage reader is not qualified",
            )
        if not record.provider_session_id:
            return _UsageSnapshot(
                status=(
                    "awaiting_usage"
                    if record.usage_binding_status == "pending"
                    else "unavailable"
                ),
                source="provider_transcript",
                provider=record.provider,
                reason=(
                    record.usage_binding_reason
                    or "terminal predates provider session binding"
                ),
            )
        path = self._resolve_transcript(record)
        if path is None:
            return _UsageSnapshot(
                status="awaiting_usage",
                source="provider_transcript",
                provider=record.provider,
                reason="provider transcript is not available yet",
            )
        try:
            stat = path.stat()
        except OSError:
            return _UsageSnapshot(
                status="awaiting_usage",
                source="provider_transcript",
                provider=record.provider,
                reason="provider transcript is not available yet",
            )
        cache_key = (record.provider, str(path), stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        snapshot = (
            self._read_claude(path)
            if record.provider == "claude-code"
            else self._read_codex(path, expected_root=Path(record.project_root))
        )
        with self._lock:
            self._cache = {
                key: value
                for key, value in self._cache.items()
                if key[0] != record.provider or key[1] != str(path)
            }
            self._cache[cache_key] = snapshot
        return snapshot

    def _resolve_transcript(self, record: TerminalSessionRecord) -> Path | None:
        root = (
            self.claude_projects_root
            if record.provider == "claude-code"
            else self.codex_sessions_root
        )
        raw = str(record.provider_session_path or "").strip()
        if raw:
            path = Path(raw).resolve(strict=False)
            if self._under(path, root) and path.exists():
                return path
        if record.provider == "claude-code":
            matches = sorted(
                self.claude_projects_root.glob(
                    f"*/{record.provider_session_id}.jsonl"
                )
            )
        else:
            matches = sorted(
                self.codex_sessions_root.glob(
                    f"*/*/*/rollout-*-{record.provider_session_id}.jsonl"
                )
            )
        return matches[0].resolve(strict=False) if len(matches) == 1 else None

    def _read_claude(self, path: Path) -> _UsageSnapshot:
        messages: dict[str, tuple[str, dict[str, Any], str]] = {}
        for index, obj in enumerate(self._jsonl(path)):
            if obj.get("type") != "assistant":
                continue
            message = obj.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                continue
            identity = str(
                message.get("id")
                or obj.get("requestId")
                or obj.get("request_id")
                or f"line-{index}"
            )
            messages[identity] = (
                str(message.get("model") or "unknown"),
                dict(message["usage"]),
                str(obj.get("timestamp") or ""),
            )
        buckets: dict[str, _UsageBucket] = {}
        latest_model = ""
        latest_receipt: dict[str, Any] | None = None
        observed_at = ""
        for model, raw, timestamp in messages.values():
            receipt = canonical_usage_tokens(
                raw,
                backend="claude-code",
                input_semantics="fresh_plus_cache",
            )
            if int(receipt["total_tokens"]) <= 0:
                continue
            buckets.setdefault(model, _UsageBucket(model=model)).add(
                receipt,
                occurred_at=timestamp,
            )
            if not observed_at or timestamp >= observed_at:
                observed_at = timestamp
                latest_model = model
                latest_receipt = receipt
        if not buckets:
            return _UsageSnapshot(
                status="awaiting_usage",
                source="provider_transcript",
                provider="anthropic",
                reason="provider transcript has no usage receipt yet",
            )
        window = self._claude_window(latest_model)
        ratio = (
            int(latest_receipt["combined_input_tokens"]) / window
            if latest_receipt is not None and window > 0
            else None
        )
        return _UsageSnapshot(
            status="observed",
            source="provider_transcript",
            provider="anthropic",
            buckets=buckets,
            context_usage_ratio=ratio,
            observed_at=observed_at,
        )

    def _read_codex(self, path: Path, *, expected_root: Path) -> _UsageSnapshot:
        buckets: dict[str, _UsageBucket] = {}
        previous: dict[str, int] = {}
        seen_incremental: set[str] = set()
        current_model = "unknown"
        provider = "openai"
        accounting_mode = "unknown"
        observed_at = ""
        context_ratio: float | None = None
        for obj in self._jsonl(path):
            kind = obj.get("type")
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            if kind == "session_meta":
                cwd = str(payload.get("cwd") or "")
                if cwd and Path(cwd).resolve(strict=False) != expected_root.resolve(
                    strict=False
                ):
                    return _UsageSnapshot(
                        status="unavailable",
                        source="provider_transcript",
                        provider="codex",
                        reason="provider transcript Project identity mismatch",
                    )
                provider = str(payload.get("model_provider") or provider)
                continue
            if kind == "turn_context":
                current_model = str(payload.get("model") or current_model)
                continue
            if kind != "event_msg" or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            raw_total = info.get("total_token_usage")
            raw_last = info.get("last_token_usage")
            timestamp = str(obj.get("timestamp") or "")
            model = str(info.get("model") or current_model or "unknown")
            if isinstance(raw_total, dict):
                receipt = canonical_usage_tokens(
                    raw_total,
                    backend="codex",
                    input_semantics="combined_includes_cache",
                )
                current = self._receipt_counters(receipt)
                delta = {
                    key: (
                        value
                        if value < previous.get(key, 0)
                        else value - previous.get(key, 0)
                    )
                    for key, value in current.items()
                }
                previous = current
                self._add_counters(buckets, model, delta, timestamp)
            elif isinstance(raw_last, dict):
                marker = hashlib.sha256(
                    json.dumps(
                        {"timestamp": timestamp, "usage": raw_last},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if marker not in seen_incremental:
                    seen_incremental.add(marker)
                    receipt = canonical_usage_tokens(
                        raw_last,
                        backend="codex",
                        input_semantics="combined_includes_cache",
                    )
                    self._add_counters(
                        buckets,
                        model,
                        self._receipt_counters(receipt),
                        timestamp,
                    )
            window = int(info.get("model_context_window") or 0)
            if isinstance(raw_last, dict) and window > 0:
                context_ratio = max(int(raw_last.get("input_tokens") or 0), 0) / window
            rate_limits = payload.get("rate_limits")
            if isinstance(rate_limits, dict) and str(rate_limits.get("plan_type") or ""):
                accounting_mode = "subscription"
            if timestamp:
                observed_at = timestamp
        buckets = {
            model: bucket
            for model, bucket in buckets.items()
            if bucket.total_tokens > 0
        }
        if not buckets:
            return _UsageSnapshot(
                status="awaiting_usage",
                source="provider_transcript",
                provider=provider,
                accounting_mode=accounting_mode,
                reason="provider transcript has no usage receipt yet",
            )
        return _UsageSnapshot(
            status="observed",
            source="provider_transcript",
            provider=provider,
            accounting_mode=accounting_mode,
            buckets=buckets,
            context_usage_ratio=context_ratio,
            observed_at=observed_at,
        )

    def _project(self, snapshot: _UsageSnapshot) -> dict[str, object]:
        buckets = list(snapshot.buckets.values())
        fresh = sum(item.fresh_input_tokens for item in buckets)
        cached = sum(item.cache_read_tokens for item in buckets)
        cache_creation = sum(item.cache_creation_tokens for item in buckets)
        output = sum(item.output_tokens for item in buckets)
        reasoning = sum(item.reasoning_output_tokens for item in buckets)
        models = sorted(snapshot.buckets)
        cost = Decimal("0")
        unpriced = 0
        partial = 0
        catalog = PricingCatalogStore(self.state_dir)
        for bucket in buckets:
            calculation = calculate_usage_cost(
                catalog_store=catalog,
                legacy_rates=None,
                provider=snapshot.provider,
                backend=(
                    "claude-code" if snapshot.provider == "anthropic" else "codex"
                ),
                model=bucket.model,
                accounting_mode=snapshot.accounting_mode,
                occurred_at=bucket.occurred_at or snapshot.observed_at,
                service_tier="standard",
                request_input_tokens=None,
                input_tokens=bucket.fresh_input_tokens,
                output_tokens=bucket.output_tokens,
                cache_creation_tokens=bucket.cache_creation_tokens,
                cache_creation_1h_tokens=0,
                cache_read_tokens=bucket.cache_read_tokens,
                provider_cost_usd=None,
            )
            if calculation.estimate is None:
                unpriced += 1
            else:
                cost += calculation.estimate
                if calculation.cost_status == "partial":
                    partial += 1
        observed = snapshot.status == "observed"
        cost_usd: float | None = None if not observed or unpriced == len(buckets) else float(cost)
        cost_kind = (
            "unavailable"
            if not observed
            else "unpriced"
            if unpriced == len(buckets)
            else "partial_estimate"
            if unpriced or partial
            else "estimated"
        )
        total_input = fresh + cached + cache_creation
        return {
            "schema_version": "terminal-usage.v1",
            "status": snapshot.status,
            "source": snapshot.source,
            "provider": snapshot.provider,
            "accounting_mode": snapshot.accounting_mode,
            "model": models[0] if len(models) == 1 else f"{len(models)} models" if models else "",
            "models": models,
            "fresh_input_tokens": fresh if observed else None,
            "cached_input_tokens": cached if observed else None,
            "cache_creation_input_tokens": cache_creation if observed else None,
            "input_tokens": total_input if observed else None,
            "output_tokens": output if observed else None,
            "reasoning_output_tokens": reasoning if observed else None,
            "total_tokens": total_input + output if observed else None,
            "cost_usd": cost_usd,
            "cost_kind": cost_kind,
            "context_usage_ratio": snapshot.context_usage_ratio,
            "observed_at": snapshot.observed_at,
            "reason": snapshot.reason,
        }

    def _ledger_snapshot(self, record: TerminalSessionRecord) -> dict[str, object] | None:
        if not self.cost_path.exists():
            return None
        with self._lock, locked_path(self.cost_path):
            totals = CostTracker(self.cost_path).usage_totals(
                instance_id=self.instance_id(record)
            )
        if int(totals["entries"]) <= 0:
            return None
        unpriced = int(totals["unpriced_entries"])
        partial = int(totals["partial_entries"])
        entries = int(totals["entries"])
        fresh = int(totals["input_tokens"])
        cached = int(totals["cache_read_tokens"])
        cache_creation = int(totals["cache_creation_tokens"])
        output = int(totals["output_tokens"])
        return {
            "schema_version": "terminal-usage.v1",
            "status": "observed",
            "source": "terminal_cost_ledger",
            "provider": record.provider,
            "accounting_mode": "unknown",
            "model": "",
            "models": [],
            "fresh_input_tokens": fresh,
            "cached_input_tokens": cached,
            "cache_creation_input_tokens": cache_creation,
            "input_tokens": fresh + cached + cache_creation,
            "output_tokens": output,
            "reasoning_output_tokens": None,
            "total_tokens": fresh + cached + cache_creation + output,
            "cost_usd": None if unpriced == entries else float(totals["total_usd"]),
            "cost_kind": (
                "unpriced"
                if unpriced == entries
                else "partial_estimate"
                if unpriced or partial
                else "estimated"
            ),
            "context_usage_ratio": None,
            "observed_at": "",
            "reason": "provider transcript unavailable; showing settled projection",
        }

    def _codex_paths(self) -> set[Path]:
        return set(self.codex_sessions_root.glob("*/*/*/rollout-*.jsonl"))

    def _codex_binding_paths(self) -> set[Path]:
        return self._codex_paths() | set(
            self.codex_shell_snapshots_root.glob("*.sh")
        )

    def _codex_binding_candidates(
        self,
        paths: Iterable[Path],
        *,
        project_root: Path,
        started_at_ns: int,
        ended_before_ns: int = 0,
        allow_shell_snapshots: bool = False,
    ) -> list[Path]:
        candidates: list[Path] = []
        for path in paths:
            if allow_shell_snapshots and path.suffix == ".sh":
                try:
                    modified_ns = path.stat().st_mtime_ns
                except OSError:
                    continue
                if modified_ns + _BINDING_TIMESTAMP_SLOP_NS >= started_at_ns:
                    candidates.append(path)
                continue
            session_started_at_ns = self._codex_session_started_at_ns(path)
            if session_started_at_ns <= 0:
                continue
            if (
                session_started_at_ns + _BINDING_TIMESTAMP_SLOP_NS
                < started_at_ns
            ):
                continue
            if ended_before_ns > 0 and session_started_at_ns >= ended_before_ns:
                continue
            if self._codex_project_matches(path, project_root):
                candidates.append(path)
        return candidates

    @staticmethod
    def _codex_project_matches(path: Path, expected_root: Path) -> bool:
        for obj in TerminalUsageService._jsonl(path):
            if obj.get("type") != "session_meta" or not isinstance(
                obj.get("payload"), dict
            ):
                continue
            cwd = str(obj["payload"].get("cwd") or "")
            return bool(cwd) and Path(cwd).resolve(
                strict=False
            ) == expected_root.resolve(strict=False)
        return False

    @staticmethod
    def _codex_session_started_at_ns(path: Path) -> int:
        for obj in TerminalUsageService._jsonl(path):
            if obj.get("type") != "session_meta" or not isinstance(
                obj.get("payload"), dict
            ):
                continue
            raw = str(
                obj["payload"].get("timestamp")
                or obj.get("timestamp")
                or ""
            ).strip()
            if not raw:
                return 0
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return 0
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1_000_000_000)
        return 0

    @staticmethod
    def _codex_candidate_ids(paths: Iterable[Path]) -> dict[str, list[Path]]:
        discovered: dict[str, list[Path]] = {}
        for path in paths:
            native_id = TerminalUsageService._codex_session_id(path)
            if native_id:
                discovered.setdefault(native_id, []).append(path)
        return discovered

    @staticmethod
    def _codex_session_id(path: Path) -> str:
        shell_match = _CODEX_SHELL_ID_RE.search(path.name)
        if shell_match:
            return shell_match.group(1)
        for obj in TerminalUsageService._jsonl(path):
            if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                value = str(obj["payload"].get("id") or "")
                if value:
                    return value
                break
        match = _CODEX_ID_RE.search(path.name)
        return match.group(1) if match else ""

    @staticmethod
    def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            return
        with handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value

    @staticmethod
    def _receipt_counters(receipt: dict[str, Any]) -> dict[str, int]:
        return {
            "fresh_input_tokens": int(receipt["fresh_input_tokens"]),
            "cache_read_input_tokens": int(receipt["cache_read_input_tokens"]),
            "cache_creation_input_tokens": int(receipt["cache_creation_input_tokens"]),
            "output_tokens": int(receipt["output_tokens"]),
            "reasoning_output_tokens": int(receipt["reasoning_output_tokens"]),
        }

    @staticmethod
    def _add_counters(
        buckets: dict[str, _UsageBucket],
        model: str,
        counters: dict[str, int],
        timestamp: str,
    ) -> None:
        receipt = {
            "fresh_input_tokens": counters["fresh_input_tokens"],
            "cache_read_input_tokens": counters["cache_read_input_tokens"],
            "cache_creation_input_tokens": counters["cache_creation_input_tokens"],
            "output_tokens": counters["output_tokens"],
            "reasoning_output_tokens": counters["reasoning_output_tokens"],
        }
        buckets.setdefault(model, _UsageBucket(model=model)).add(
            receipt,
            occurred_at=timestamp,
        )

    @staticmethod
    def _sample_id(series_id: str, bucket: _UsageBucket) -> str:
        value = {
            "series_id": series_id,
            "fresh_input_tokens": bucket.fresh_input_tokens,
            "cache_read_tokens": bucket.cache_read_tokens,
            "cache_creation_tokens": bucket.cache_creation_tokens,
            "output_tokens": bucket.output_tokens,
            "reasoning_output_tokens": bucket.reasoning_output_tokens,
        }
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _claude_window(model: str) -> int:
        if model.startswith(("claude-opus-4", "claude-sonnet-4")):
            return 1_000_000
        return 200_000

    @staticmethod
    def _under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True


__all__ = [
    "TERMINAL_COST_FILENAME",
    "TerminalUsageBinding",
    "TerminalUsageLaunch",
    "TerminalUsageService",
]
