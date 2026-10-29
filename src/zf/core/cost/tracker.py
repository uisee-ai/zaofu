"""Cost tracker — token usage recording and budget enforcement.

Uses active+archive layout (G-COST-1): today's records go in
.zf/cost.jsonl; previous days rotate to .zf/cost/<YYYY-MM-DD>.jsonl
via the shared rotation helper (G-ROT-0).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from zf.core.cost.calculator import calculate_usage_cost
from zf.core.cost.catalog import PricingCatalogStore
from zf.core.state.rotation import list_archives, rotate_if_needed


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


def _float_value(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _datetime_value(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_datetime(entry: Mapping[str, Any]) -> datetime | None:
    occurred = _datetime_value(entry.get("occurred_at"))
    if occurred is not None:
        return occurred
    try:
        return datetime.fromtimestamp(
            float(entry.get("ts") or 0),
            tz=timezone.utc,
        )
    except (OSError, OverflowError, TypeError, ValueError):
        return None


# Legacy coarse rate buckets (USD per 1M tokens). Retained only as an
# explicit-injection escape hatch (``CostTracker(rates=...)``); the default
# pricing path now runs through the cache-aware per-model table in
# ``cost/pricing.py``. Kept for back-compat of any external importer.
DEFAULT_RATES: dict[str, dict[str, float]] = {
    "default": {"input": 3.0, "output": 15.0},
    "opus": {"input": 15.0, "output": 75.0},
    "sonnet": {"input": 3.0, "output": 15.0},
    "haiku": {"input": 0.25, "output": 1.25},
}


@dataclass
class CostSummary:
    role: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_usd: float = 0.0
    estimated_usd: float = 0.0
    provider_reported_usd: float = 0.0
    billed_usd: float = 0.0
    unpriced_entries: int = 0
    partial_entries: int = 0
    entries: int = 0


class CostTracker:
    """Track token usage and costs, enforce budgets."""

    def __init__(
        self,
        cost_path: Path,
        *,
        rates: dict | None = None,
        catalog_store: PricingCatalogStore | None = None,
    ) -> None:
        self.cost_path = cost_path
        # None → default to the cache-aware pricing module. A dict-of-dict
        # (legacy {model: {input, output}}) injected explicitly still wins
        # in record_usage for back-compat / test overrides.
        self.rates = rates
        self.catalog_store = catalog_store or PricingCatalogStore(
            cost_path.parent
        )

    @property
    def _archive_dir(self) -> Path:
        return self.cost_path.parent / self.cost_path.stem

    def record_usage(
        self,
        role: str,
        input_tokens: int,
        output_tokens: int,
        model: str = "default",
        instance_id: str | None = None,
        backend: str = "",
        *,
        cache_creation_tokens: int = 0,
        cache_creation_1h_tokens: int = 0,
        cache_read_tokens: int = 0,
        provider_cost_usd: float | None = None,
        provider: str = "",
        accounting_mode: str = "unknown",
        occurred_at: str = "",
        service_tier: str = "standard",
        request_input_tokens: int | None = None,
        source_event_id: str = "",
        usage_sample_id: str = "",
        usage_semantics: str = "",
        usage_series_id: str = "",
        cumulative_usage: dict[str, int | float] | None = None,
        accounting_baseline: bool = False,
    ) -> float:
        """Record token usage. Returns cost in USD.

        G-INST-6: ``instance_id`` (optional) differentiates replicas of
        the same role type. Absent → defaults to ``role`` for backward
        compatibility so per_role_totals and per_instance_totals return
        the same keys in single-instance deployments.

        1204: ``backend`` (optional) records the adapter kind
        (claude-code / codex / mock) so `summary_by_backend` can split
        spend in mixed-backend configs. Empty string = unknown backend
        (legacy entries pre-1204 read back as "unknown").

        ``cost-record.v2`` never collapses the three different facts:
        catalog estimates, provider-reported runtime cost, and billed cost.
        ``cost_usd`` remains the backward-compatible display value and prefers
        provider-reported cost when available. Unknown model/provider pairs are
        recorded as ``unpriced`` instead of silently using a default rate.
        A legacy injected ``rates`` dict (dict-of-dict) still wins when it
        carries ``model`` — back-compat for explicit overrides.

        ``usage_sample_id`` / ``source_event_id`` make cost.jsonl an
        idempotent projection over events. R37 showed disk-reader snapshots
        can be observed repeatedly; those repeats must remain visible in
        events.jsonl without inflating spend projections or budget gates.
        """
        calculation = calculate_usage_cost(
            catalog_store=self.catalog_store,
            legacy_rates=self.rates,
            provider=provider,
            backend=backend,
            model=model,
            accounting_mode=accounting_mode,
            occurred_at=occurred_at,
            service_tier=service_tier,
            request_input_tokens=request_input_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_creation_1h_tokens=cache_creation_1h_tokens,
            cache_read_tokens=cache_read_tokens,
            provider_cost_usd=provider_cost_usd,
        )

        self.cost_path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(self.cost_path, self._archive_dir)

        dedupe_key = self._dedupe_key(
            source_event_id=source_event_id,
            usage_sample_id=usage_sample_id,
        )
        if dedupe_key and dedupe_key in self._existing_dedupe_keys():
            return 0.0

        entry = {
            "schema_version": "cost-record.v2",
            "role": role,
            "instance_id": instance_id or role,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cache_creation_1h_tokens": cache_creation_1h_tokens,
            "cache_read_tokens": cache_read_tokens,
            "model": model,
            "model_observed": model,
            "provider": calculation.provider,
            "accounting_mode": accounting_mode,
            "cost_usd": float(calculation.display_cost),
            "cost_source": calculation.cost_source,
            "display_cost_kind": calculation.display_kind,
            "cost_status": calculation.cost_status,
            "estimated_cost_usd": (
                _decimal_text(calculation.estimate)
                if calculation.estimate is not None else None
            ),
            "provider_reported_cost_usd": (
                _decimal_text(calculation.provider_reported)
                if calculation.provider_reported is not None else None
            ),
            "billed_cost_usd": None,
            "reconciliation_status": "pending",
            "pricing_formula_version": "deterministic-token-cost.v1",
            "usage_receipt_id": usage_sample_id or source_event_id or None,
            "ts": time.time(),
            "backend": backend,
        }
        if occurred_at:
            entry["occurred_at"] = occurred_at
        if calculation.resolution is not None:
            resolution = calculation.resolution
            entry.update({
                "catalog_version": resolution.catalog_version,
                "pricing_catalog_digest": resolution.catalog_digest,
                "pricing_effective_at": resolution.pricing_effective_at,
                "pricing_rate_key": resolution.rate.rate_key,
                "pricing_precision": resolution.precision,
                "pricing_missing_dimensions": list(
                    resolution.missing_dimensions
                ),
                "estimate_kind": resolution.estimate_kind,
            })
        if source_event_id:
            entry["source_event_id"] = source_event_id
        if usage_sample_id:
            entry["usage_sample_id"] = usage_sample_id
        if dedupe_key:
            entry["dedupe_key"] = dedupe_key
        if usage_semantics:
            entry["usage_semantics"] = usage_semantics
        if usage_series_id:
            entry["usage_series_id"] = usage_series_id
        if cumulative_usage is not None:
            entry["cumulative_usage"] = dict(cumulative_usage)
        if accounting_baseline:
            entry["accounting_baseline"] = True
        with self.cost_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

        return float(calculation.display_cost)

    def record_cumulative_usage(
        self,
        *,
        role: str,
        instance_id: str,
        input_tokens: int,
        output_tokens: int,
        model: str = "default",
        backend: str = "",
        cache_creation_tokens: int = 0,
        cache_creation_1h_tokens: int = 0,
        cache_read_tokens: int = 0,
        provider_cost_usd: float | None = None,
        provider: str = "",
        accounting_mode: str = "unknown",
        occurred_at: str = "",
        service_tier: str = "standard",
        request_input_tokens: int | None = None,
        source_event_id: str = "",
        usage_sample_id: str = "",
        usage_series_id: str = "",
    ) -> float:
        """Record one cumulative provider snapshot as a restart-safe delta."""

        series_id = usage_series_id or ":".join((
            "cumulative",
            str(instance_id or role),
            str(backend or "unknown"),
            str(model or "default"),
        ))
        current: dict[str, int | float] = {
            "input_tokens": max(0, int(input_tokens or 0)),
            "output_tokens": max(0, int(output_tokens or 0)),
            "cache_creation_tokens": max(0, int(cache_creation_tokens or 0)),
            "cache_creation_1h_tokens": max(
                0, int(cache_creation_1h_tokens or 0)
            ),
            "cache_read_tokens": max(0, int(cache_read_tokens or 0)),
        }
        if provider_cost_usd is not None:
            current["provider_cost_usd"] = max(0.0, float(provider_cost_usd))

        entries = self._read_entries()
        previous: dict[str, int | float] = {}
        for entry in reversed(entries):
            if (
                str(entry.get("usage_semantics") or "") == "cumulative"
                and str(entry.get("usage_series_id") or "") == series_id
                and isinstance(entry.get("cumulative_usage"), dict)
            ):
                observed, prior = _datetime_value(occurred_at), _datetime_value(entry.get("occurred_at"))
                if observed is not None and prior is not None and observed < prior:
                    return 0.0  # An older observation is not a provider counter reset.
                previous = dict(entry["cumulative_usage"])
                break

        if not previous and self._is_legacy_series_migration(
            entries,
            instance_id=instance_id,
            backend=backend,
            series_id=series_id,
        ):
            return self.record_usage(
                role=role,
                instance_id=instance_id,
                input_tokens=0,
                output_tokens=0,
                model=model,
                backend=backend,
                cache_creation_tokens=0,
                cache_creation_1h_tokens=0,
                cache_read_tokens=0,
                provider=provider,
                accounting_mode=accounting_mode,
                occurred_at=occurred_at,
                service_tier=service_tier,
                request_input_tokens=request_input_tokens,
                source_event_id=source_event_id,
                usage_sample_id=usage_sample_id,
                usage_semantics="cumulative",
                usage_series_id=series_id,
                cumulative_usage=current,
                accounting_baseline=True,
            )

        token_keys = (
            "input_tokens",
            "output_tokens",
            "cache_creation_tokens",
            "cache_creation_1h_tokens",
            "cache_read_tokens",
        )
        deltas = {
            key: (
                int(current[key])
                if not previous
                or int(current[key]) < int(previous.get(key, 0) or 0)
                else int(current[key]) - int(previous.get(key, 0) or 0)
            )
            for key in token_keys
        }
        provider_delta: float | None = None
        if provider_cost_usd is not None:
            provider_delta = (
                float(current["provider_cost_usd"])
                if not previous
                or float(current["provider_cost_usd"])
                < float(previous.get("provider_cost_usd", 0.0) or 0.0)
                else float(current["provider_cost_usd"])
                - float(previous.get("provider_cost_usd", 0.0) or 0.0)
            )
        if not any(deltas.values()) and not (provider_delta and provider_delta > 0):
            return 0.0
        return self.record_usage(
            role=role,
            instance_id=instance_id,
            input_tokens=deltas["input_tokens"],
            output_tokens=deltas["output_tokens"],
            model=model,
            backend=backend,
            cache_creation_tokens=deltas["cache_creation_tokens"],
            cache_creation_1h_tokens=deltas["cache_creation_1h_tokens"],
            cache_read_tokens=deltas["cache_read_tokens"],
            provider_cost_usd=provider_delta,
            provider=provider,
            accounting_mode=accounting_mode,
            occurred_at=occurred_at,
            service_tier=service_tier,
            request_input_tokens=request_input_tokens,
            source_event_id=source_event_id,
            usage_sample_id=usage_sample_id,
            usage_semantics="cumulative",
            usage_series_id=series_id,
            cumulative_usage=current,
        )

    @staticmethod
    def _is_legacy_series_migration(
        entries: list[dict],
        *,
        instance_id: str,
        backend: str,
        series_id: str,
    ) -> bool:
        """Recognize the one-time disk-reader series-identity upgrade.

        Older ZaoFu releases grouped Codex cumulative snapshots under a
        ``...:default`` series. The first provider-session-scoped snapshot
        contains the whole historical transcript; charging it again would
        make a new Workflow inherit the previous run's token spend.
        """

        prefix = ":".join((
            "disk_reader",
            str(instance_id),
            str(backend or "unknown"),
        )) + ":"
        if not series_id.startswith(prefix) or series_id == prefix + "default":
            return False
        return any(
            str(entry.get("usage_semantics") or "") == "cumulative"
            and str(entry.get("usage_series_id") or "") == prefix + "default"
            and str(entry.get("instance_id") or entry.get("role") or "")
            == instance_id
            and isinstance(entry.get("cumulative_usage"), dict)
            for entry in entries
        )

    @classmethod
    def rebuild_from_events(
        cls,
        events,
        dest_path: Path,
        *,
        rates: dict | None = None,
        role_backends: dict[str, str] | None = None,
    ) -> "CostTracker":
        """K4(I1 可执行性):从 events 重放 agent.usage 重建 cost 投影。

        复用 housekeeping.apply_agent_usage_event(cost 的唯一 runtime
        写路),零逻辑分叉——重建产物与增量写的聚合(per_role/instance/
        backend totals)必须相等。写入 dest_path(新文件);调用方决定
        是否原子替换现役文件。归档目录不在重建范围(events 按日归档
        经 list_archives 读入由调用方拼接)。
        """
        from zf.runtime.housekeeping import apply_agent_usage_event

        tracker = cls(dest_path, rates=rates)
        for event in events:
            if getattr(event, "type", "") != "agent.usage":
                continue
            apply_agent_usage_event(
                tracker, event, role_backends=role_backends,
            )
        return tracker

    def summary_by_backend(
        self, *, last_days: int | None = None,
    ) -> dict[str, CostSummary]:
        """1204-T2: aggregate cost + tokens per backend.

        Entries missing the backend field (written before 1204 shipped)
        are bucketed under "unknown" so legacy data remains visible.
        Rolls up across all roles and instance_ids.
        """
        totals: dict[str, CostSummary] = {}
        for entry in self._read_entries(last_days=last_days):
            if entry.get("accounting_baseline") is True:
                continue
            key = entry.get("backend") or "unknown"
            if key not in totals:
                totals[key] = CostSummary(role=key)
            self._accumulate_summary(totals[key], entry)
        return totals

    def per_role_totals(self, *, last_days: int | None = None) -> dict[str, CostSummary]:
        """Get cost totals grouped by role *type* (aggregates instances).

        last_days=None: aggregate everything (active + all archives).
        last_days=N: today's active + last (N-1) days of archive.
        """
        totals: dict[str, CostSummary] = {}
        for entry in self._read_entries(last_days=last_days):
            if entry.get("accounting_baseline") is True:
                continue
            role = entry["role"]
            if role not in totals:
                totals[role] = CostSummary(role=role)
            self._accumulate_summary(totals[role], entry)
        return totals

    def per_instance_totals(
        self, *, last_days: int | None = None
    ) -> dict[str, CostSummary]:
        """G-INST-6: cost totals grouped by instance_id.

        For single-instance configs this has the same keys as
        per_role_totals. For multi-instance configs it splits dev-1 /
        dev-2 / dev-3 so operators can see which replica is burning
        budget.
        """
        totals: dict[str, CostSummary] = {}
        for entry in self._read_entries(last_days=last_days):
            if entry.get("accounting_baseline") is True:
                continue
            key = entry.get("instance_id") or entry["role"]
            if key not in totals:
                totals[key] = CostSummary(role=key)
            self._accumulate_summary(totals[key], entry)
        return totals

    @staticmethod
    def _accumulate_summary(summary: CostSummary, entry: Mapping[str, Any]) -> None:
        summary.input_tokens += int(entry.get("input_tokens", 0) or 0)
        summary.output_tokens += int(entry.get("output_tokens", 0) or 0)
        summary.cache_creation_tokens += int(
            entry.get("cache_creation_tokens", 0) or 0
        ) + int(entry.get("cache_creation_1h_tokens", 0) or 0)
        summary.cache_read_tokens += int(
            entry.get("cache_read_tokens", 0) or 0
        )
        summary.total_usd += _float_value(entry.get("cost_usd"))
        summary.estimated_usd += _float_value(
            entry.get("estimated_cost_usd")
        )
        summary.provider_reported_usd += _float_value(
            entry.get("provider_reported_cost_usd")
        )
        summary.billed_usd += _float_value(entry.get("billed_cost_usd"))
        status = str(entry.get("cost_status") or "")
        if status == "unpriced":
            summary.unpriced_entries += 1
        elif status == "partial":
            summary.partial_entries += 1
        summary.entries += 1

    def total_usd(self, *, last_days: int | None = None) -> float:
        """Get total cost across all roles."""
        return sum(s.total_usd for s in self.per_role_totals(last_days=last_days).values())

    def usage_records(self) -> list[dict[str, Any]]:
        """Read admitted delta receipts, including archives, without re-accounting."""
        return [entry for entry in self._read_entries()
                if entry.get("accounting_baseline") is not True]

    def usage_totals(
        self,
        *,
        instance_id: str = "",
        last_days: int | None = None,
    ) -> dict[str, Any]:
        """Return one restart-safe token/cost meter snapshot.

        ``instance_id`` narrows the meter to one physical provider role. The
        snapshot is persisted on Run/Operation start events and later used as
        the baseline for active budget enforcement.
        """

        totals: dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_creation_1h_tokens": 0,
            "cache_read_tokens": 0,
            "total_tokens": 0,
            "total_usd": 0.0,
            "estimated_usd": 0.0,
            "provider_reported_usd": 0.0,
            "billed_usd": 0.0,
            "unpriced_entries": 0,
            "partial_entries": 0,
            "entries": 0,
            "catalog_digests": [],
            "pricing_rate_keys": [],
            "latest_usage_ref": "",
            "pricing_formula_version": "deterministic-token-cost.v1",
        }
        catalog_digests: set[str] = set()
        pricing_rate_keys: set[str] = set()
        for entry in self._read_entries(last_days=last_days):
            if entry.get("accounting_baseline") is True:
                continue
            entry_instance = str(
                entry.get("instance_id") or entry.get("role") or ""
            )
            if instance_id and entry_instance != instance_id:
                continue
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_tokens",
                "cache_creation_1h_tokens",
                "cache_read_tokens",
            ):
                totals[key] = int(totals[key]) + int(entry.get(key, 0) or 0)
            totals["total_usd"] = float(totals["total_usd"]) + float(
                entry.get("cost_usd", 0.0) or 0.0
            )
            totals["entries"] = int(totals["entries"]) + 1
            totals["estimated_usd"] = float(totals["estimated_usd"]) + (
                _float_value(entry.get("estimated_cost_usd"))
            )
            totals["provider_reported_usd"] = (
                float(totals["provider_reported_usd"])
                + _float_value(entry.get("provider_reported_cost_usd"))
            )
            totals["billed_usd"] = float(totals["billed_usd"]) + (
                _float_value(entry.get("billed_cost_usd"))
            )
            status = str(entry.get("cost_status") or "")
            if status == "unpriced":
                totals["unpriced_entries"] = (
                    int(totals["unpriced_entries"]) + 1
                )
            elif status == "partial":
                totals["partial_entries"] = (
                    int(totals["partial_entries"]) + 1
                )
            catalog_digest = str(
                entry.get("pricing_catalog_digest")
                or entry.get("catalog_digest")
                or ""
            )
            if catalog_digest:
                catalog_digests.add(catalog_digest)
            rate_key = str(entry.get("pricing_rate_key") or "")
            if rate_key:
                pricing_rate_keys.add(rate_key)
            usage_ref = str(
                entry.get("source_event_id")
                or entry.get("usage_sample_id")
                or entry.get("dedupe_key")
                or ""
            )
            if usage_ref:
                totals["latest_usage_ref"] = usage_ref
        totals["total_tokens"] = sum(
            int(totals[key])
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_tokens",
                "cache_creation_1h_tokens",
                "cache_read_tokens",
            )
        )
        totals["catalog_digests"] = sorted(catalog_digests)
        totals["pricing_rate_keys"] = sorted(pricing_rate_keys)
        return totals

    def precision_summary(
        self, *, last_days: int | None = None
    ) -> dict[str, Any]:
        """Describe which monetary facts back the current projection."""

        totals = self.usage_totals(last_days=last_days)
        catalogs: dict[str, dict[str, str]] = {}
        statuses: dict[str, int] = {}
        for entry in self._read_entries(last_days=last_days):
            if entry.get("accounting_baseline") is True:
                continue
            status = str(entry.get("cost_status") or "legacy")
            statuses[status] = statuses.get(status, 0) + 1
            digest = str(
                entry.get("pricing_catalog_digest")
                or entry.get("catalog_digest")
                or ""
            )
            if digest:
                catalogs[digest] = {
                    "catalog_version": str(
                        entry.get("catalog_version") or ""
                    ),
                    "digest": digest,
                }
        return {
            "schema_version": "cost-precision-summary.v1",
            "display_total_usd": round(float(totals["total_usd"]), 12),
            "estimated_usd": round(float(totals["estimated_usd"]), 12),
            "provider_reported_usd": round(
                float(totals["provider_reported_usd"]), 12
            ),
            "billed_usd": round(float(totals["billed_usd"]), 12),
            "unpriced_entries": int(totals["unpriced_entries"]),
            "partial_entries": int(totals["partial_entries"]),
            "statuses": dict(sorted(statuses.items())),
            "catalogs": list(catalogs.values()),
        }

    def has_unpriced_usage(
        self, *, instance_id: str = "", last_days: int | None = None
    ) -> bool:
        return bool(self.usage_totals(
            instance_id=instance_id,
            last_days=last_days,
        )["unpriced_entries"])

    def estimated_usd_between(self, *, start: str, end: str) -> Decimal:
        """Sum catalog estimates whose observed timestamps fall in a window."""

        start_at = _datetime_value(start)
        end_at = _datetime_value(end)
        if start_at is None or end_at is None or end_at <= start_at:
            raise ValueError("start/end must be an increasing RFC3339 window")
        total = Decimal("0")
        for entry in self._read_entries():
            if entry.get("accounting_baseline") is True:
                continue
            observed = _entry_datetime(entry)
            if observed is None or not (start_at <= observed < end_at):
                continue
            value = entry.get("estimated_cost_usd")
            if value not in (None, ""):
                try:
                    total += Decimal(str(value))
                except InvalidOperation:
                    continue
        return total

    def check_budget(self, budget: float) -> bool:
        """Return True if within budget, False if exceeded."""
        return self.total_usd() <= budget

    def duplicate_report(self) -> dict[str, object]:
        """Return a diagnostic report for duplicate/suspect cost entries."""
        entries = self._read_entries()
        keyed_counts: dict[str, int] = {}
        legacy_counts: dict[str, int] = {}
        missing_dedupe_key = 0
        for entry in entries:
            key = str(entry.get("dedupe_key") or "")
            if not key:
                key = self._dedupe_key(
                    source_event_id=str(entry.get("source_event_id") or ""),
                    usage_sample_id=str(entry.get("usage_sample_id") or ""),
                )
            if key:
                keyed_counts[key] = keyed_counts.get(key, 0) + 1
            else:
                missing_dedupe_key += 1
                legacy = json.dumps(
                    {
                        "role": entry.get("role"),
                        "instance_id": entry.get("instance_id"),
                        "input_tokens": entry.get("input_tokens"),
                        "output_tokens": entry.get("output_tokens"),
                        "cache_creation_tokens": entry.get("cache_creation_tokens", 0),
                        "cache_creation_1h_tokens": entry.get(
                            "cache_creation_1h_tokens", 0
                        ),
                        "cache_read_tokens": entry.get("cache_read_tokens", 0),
                        "model": entry.get("model"),
                        "backend": entry.get("backend"),
                        "cost_source": entry.get("cost_source"),
                        "cost_usd": entry.get("cost_usd"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                legacy_counts[legacy] = legacy_counts.get(legacy, 0) + 1
        duplicate_keys = {k: v for k, v in keyed_counts.items() if v > 1}
        suspect_legacy = {k: v for k, v in legacy_counts.items() if v > 1}
        return {
            "entries": len(entries),
            "dedupe_keys": len(keyed_counts),
            "duplicate_entries": sum(v - 1 for v in duplicate_keys.values()),
            "duplicate_keys": duplicate_keys,
            "missing_dedupe_key": missing_dedupe_key,
            "suspect_legacy_duplicate_entries": sum(
                v - 1 for v in suspect_legacy.values()
            ),
            "suspect_legacy_duplicate_keys": suspect_legacy,
        }

    def daily_totals(self) -> dict[str, dict[str, float]]:
        """Group entries by date (YYYY-MM-DD) and return per-day totals.

        Shape: {"2026-04-14": {"input_tokens": N, "output_tokens": N,
                                "total_usd": N, "entries": N}}
        """
        daily: dict[str, dict[str, float]] = {}

        def _bucket(date_key: str, entries: list[dict]) -> None:
            if not entries:
                return
            bucket = daily.setdefault(
                date_key,
                {"input_tokens": 0, "output_tokens": 0, "total_usd": 0.0, "entries": 0},
            )
            for e in entries:
                if e.get("accounting_baseline") is True:
                    continue
                bucket["input_tokens"] += e.get("input_tokens", 0)
                bucket["output_tokens"] += e.get("output_tokens", 0)
                bucket["total_usd"] += e.get("cost_usd", 0.0)
                bucket["entries"] += 1

        # Archives (one per day, keyed by filename stem)
        for f in list_archives(self._archive_dir, suffix=".jsonl"):
            _bucket(f.stem, self._parse_file(f))
        # Today's active file
        if self.cost_path.exists():
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            _bucket(today, self._parse_file(self.cost_path))
        return daily

    def _parse_file(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        entries: list[dict] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    @staticmethod
    def _dedupe_key(*, source_event_id: str = "", usage_sample_id: str = "") -> str:
        if usage_sample_id:
            return f"usage:{usage_sample_id}"
        if source_event_id:
            return f"event:{source_event_id}"
        return ""

    def _existing_dedupe_keys(self) -> set[str]:
        keys: set[str] = set()
        for entry in self._read_entries():
            key = str(entry.get("dedupe_key") or "")
            if not key:
                key = self._dedupe_key(
                    source_event_id=str(entry.get("source_event_id") or ""),
                    usage_sample_id=str(entry.get("usage_sample_id") or ""),
                )
            if key:
                keys.add(key)
        return keys

    def _read_entries(self, *, last_days: int | None = None) -> list[dict]:
        entries: list[dict] = []
        # Archive files (chronological)
        archive_last_days = (
            last_days - 1 if last_days is not None and last_days > 1 else None
        )
        if last_days != 1:  # skip archives only if caller asked for today only
            for f in list_archives(
                self._archive_dir,
                last_days=archive_last_days,
                suffix=".jsonl",
            ):
                entries.extend(self._parse_file(f))
        # Active file (today)
        entries.extend(self._parse_file(self.cost_path))
        return entries
