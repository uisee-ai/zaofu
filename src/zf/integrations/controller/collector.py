"""Whitelist projection from existing durable events, never current-role attribution guesses."""

import hashlib
import json
import re
import time
from dataclasses import replace
from datetime import datetime, timezone

from zf.integrations.controller.outbox import Outbox


def identifier(value):
    text = str(value or "")
    return text if re.fullmatch(r"[\w.:/@+-]{1,160}", text) else None


def stable(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class Collector:
    def __init__(self, state_dir, config):
        self.state_dir = state_dir
        self.config = config
        self.cloud = config.observability.controller
        self.outbox = Outbox(state_dir, self.cloud)

    def __call__(self, event):
        timestamp = (event.payload or {}).get('usage_timestamp') if event.type in {'agent.usage', 'controller.usage.receipt'} else None
        binding = self.outbox.binding_at(timestamp or event.ts)
        if binding is None:
            # Pre-enrollment history has no proven deployment/mode binding. Do not relabel it.
            return
        data = event.payload or {}
        version = int(datetime.fromisoformat(event.ts.replace('Z', '+00:00')).timestamp() * 1_000_000)
        attribution = {"project_id": self.cloud.project_id,
            "session_id": identifier(data.get("thread_id") or data.get("session_id")),
            "operation_id": identifier(data.get("operation_id")),
            "run_id": identifier(data.get("workflow_run_id") or data.get("run_id")),
            "task_id": identifier(event.task_id or data.get("task_id")),
            "attempt_id": identifier(data.get("active_attempt_id") or data.get("attempt_id")),
            "agent_id": identifier(event.actor)}
        if event.type in {'agent.usage', 'controller.usage.receipt'}:
            if not data.get('_controller_accounted'):
                self._usage(event, attribution, binding)
        elif (event.type.startswith("agent.session.run.") and event.type.rsplit('.', 1)[-1] in {"started", "completed", "failed", "cancelled"}) or event.type in {"controller.execution.started", "controller.execution.finished"}:
            phase = event.type.rsplit('.', 1)[-1]
            if (phase in {'completed', 'failed', 'cancelled'} and data.get('provider_started') is not False
                    and not (data.get('usage') or {}).get('_controller_accounted')):
                from zf.integrations.controller.health import capture
                capture(self.state_dir, binding['deployment'], self.cloud.project_id, binding['mode'],
                    identifier(data.get('provider') or data.get('backend')), status=phase,
                    error=data.get('error'), observed_at=datetime.fromisoformat(event.ts).timestamp(), sample_id=event.id)
            if phase == "finished":
                phase = "completed" if data.get("status") in {"completed", "not_started"} else "cancelled" if data.get("status") in {"cancelled", "stopped"} else "failed"
            session_id = identifier(data.get("thread_id") or data.get("provider_session_id"))
            operation_id = identifier(data.get("operation_id") or data.get("run_id"))
            # Native terminal lifecycle belongs to TerminalRegistry. A later execution
            # receipt must not overwrite its stopped state with reusable-Session idle.
            if session_id and data.get("source") != "terminal":
                source = data.get("source")
                if source not in {"workflow", "kanban", "channel", "operator", "helper"}:
                    source = "unknown"
                # A completed turn leaves its reusable Session idle; it is not a terminal Session.
                self.outbox.enqueue("session:" + event.id, event.ts, {**attribution, "kind": "session",
                    "session_id": session_id, "operation_id": operation_id, "version": version, "source": source,
                    "status": "active" if phase == "started" else "idle", "provider": identifier(data.get("provider") or data.get("backend")),
                    "model": identifier(data.get("model")), "provider_session_id": identifier(data.get("provider_session_id")),
                    "started_at": event.ts if phase == "started" else None, "ended_at": None}, origin_id=binding['deployment'])
            if operation_id:
                self.outbox.enqueue("operation:" + event.id, event.ts, {**attribution, "session_id": session_id,
                    "kind": "operation", "operation_id": operation_id, "version": version,
                    "status": "running" if phase == "started" else phase,
                    "started_at": event.ts if phase == "started" else None,
                    "ended_at": event.ts if phase != "started" else None}, origin_id=binding['deployment'])
            if (phase in {'completed', 'failed', 'cancelled'} and operation_id
                    and data.get('provider_started') is not False
                    and not (data.get('usage') or {}).get('_controller_accounted')):
                from zf.runtime.provider_usage import normalize_provider_usage

                accounting = normalize_provider_usage(data.get('usage'), backend=str(data.get('backend') or data.get('provider') or ''))
                self._usage(replace(event, payload={**data, 'usage': accounting['turn'],
                    'usage_sample_id': 'session-run:' + operation_id}),
                    {**attribution, 'session_id': session_id, 'operation_id': operation_id}, binding)
        elif event.type.startswith("operator.session.") and data.get("session_id"):
            phase = event.type.rsplit('.', 1)[-1]
            if phase in {"started", "stopped", "failed"}:
                self.outbox.enqueue("session:" + event.id, event.ts, {**attribution, "kind": "session",
                    "session_id": identifier(data["session_id"]), "version": version, "source": "operator", "status": phase,
                    "provider": identifier(data.get("backend")), "provider_session_id": identifier(data.get("provider_session_id")),
                    "started_at": event.ts if phase == "started" else None, "ended_at": event.ts if phase != "started" else None}, origin_id=binding['deployment'])
        elif event.type in {"workflow.operation.started", "workflow.operation.settled", "workflow.operation.failed", "workflow.operation.cancelled", "workflow.operation.interrupted"} and attribution["operation_id"]:
            phase = event.type.rsplit('.', 1)[-1]
            self.outbox.enqueue("operation:" + event.id, event.ts, {**attribution, "kind": "operation", "version": version,
                "status": {"started": "running", "settled": "completed", "interrupted": "failed"}.get(phase, phase),
                "started_at": event.ts if phase == "started" else None, "ended_at": event.ts if phase != "started" else None}, origin_id=binding['deployment'])

    def _usage(self, event, attribution, binding):
        from zf.runtime.provider_usage import canonical_usage_tokens

        data = event.payload or {}
        if event.type == 'agent.usage' and data.get('source') == 'disk_reader':
            from zf.integrations.controller.binding import managed_config
            managed = managed_config()
            if managed:
                from zf.integrations.controller.ledger import Ledger
                occurred = datetime.fromisoformat(str(data.get('usage_timestamp') or event.ts).replace('Z', '+00:00')).timestamp()
                if Ledger(managed.state_dir).covers_usage(self.cloud.project_id, event.actor,
                        data.get('provider_session_id') or data.get('session_id'), occurred):
                    return
        raw = data.get("usage") if isinstance(data.get('usage'), dict) else {}
        backend = str(data.get("backend") or "")
        canonical = canonical_usage_tokens(raw, backend=backend, input_semantics=str(data.get("input_semantics") or ""))
        # Reuse the same non-overlapping counters that feed cost-record.v2.
        tokens = {"input_tokens": canonical["fresh_input_tokens"], "output_tokens": canonical["output_tokens"],
                  "cache_read_tokens": canonical["cache_read_input_tokens"], "cache_creation_tokens": canonical["cache_creation_input_tokens"],
                  "reasoning_tokens": canonical["reasoning_output_tokens"]}
        present = (any(key in raw for key in ('input_tokens', 'inputTokens'))
                   and any(key in raw for key in ('output_tokens', 'outputTokens')))
        counter_keys = {'input_tokens', 'inputTokens', 'output_tokens', 'outputTokens',
            'cache_read_input_tokens', 'cached_input_tokens', 'cache_creation_input_tokens',
            'reasoning_output_tokens', 'reasoning_tokens', 'cachedInputTokens', 'reasoningTokens'}
        present = present and all(type(value) is int and 0 <= value <= 10**15 for key, value in raw.items() if key in counter_keys)
        present = present and all(type(value) is int and 0 <= value <= 10**15 for value in tokens.values())
        present = present and tokens['reasoning_tokens'] <= tokens['output_tokens']
        mode = binding['mode']
        accounting = data.get("accounting_mode", "unknown")
        if accounting not in {"api", "subscription", "enterprise", "unknown"}:
            accounting = "unknown"
        sample = str(data.get("usage_sample_id") or event.id)
        revision = data.get('usage_revision', 1) if event.type == 'controller.usage.receipt' else 1
        if type(revision) is not int or not 1 <= revision <= 10**9:
            raise ValueError('invalid Controller usage revision')
        series = stable(self.cloud.project_id + ':' + str(data['usage_series_id'])) if data.get("usage_series_id") else None
        cumulative = data.get("usage_semantics") == "cumulative"
        source_started = data.get('usage_series_started_at')
        try:
            started = datetime.fromisoformat(str(source_started).replace('Z', '+00:00'))
            observed = datetime.fromisoformat(str(data.get('usage_timestamp') or event.ts).replace('Z', '+00:00'))
            known_new_series = (started.tzinfo is not None and observed.tzinfo is not None
                and binding['started'] <= started.timestamp() <= observed.timestamp() <= datetime.fromisoformat(event.ts).timestamp())
        except (TypeError, ValueError):
            known_new_series = False
        if cumulative and not series:
            # Do not invent a session boundary from role or time proximity.
            present = False
        if data.get("usage_scope") == "inclusive":
            # Parent aggregate receipts need an explicit covered-sample contract; never sum with children.
            present = False
        request_id = identifier(data.get("source_request_id"))
        namespace = identifier(data.get("request_namespace"))
        if not (request_id and namespace):
            request_id = namespace = None
        payload = {**attribution, "kind": "usage", "record_id": stable(self.cloud.project_id + ':' + sample), "revision": revision,
            "provider": identifier(data.get("provider")) or identifier(backend) or "unknown",
            "model": identifier(data.get("model")) or "unknown", "mode": mode, "accounting_mode": accounting,
            "source": "runtime", "source_request_id": request_id, "request_namespace": namespace,
            "quality": "reported" if present else "missing",
            "completeness": ("complete" if data.get('usage_completeness', 'complete') == 'complete'
                             else "partial") if present else "pending",
            "tokens": tokens if present else None, "semantics": "cumulative" if cumulative and series else "delta",
            "series_id": series, "input_semantics": "fresh_plus_cache",
            "raw_counters": {key: value for key, value in raw.items() if key in {
                "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "cached_input_tokens",
                "reasoning_output_tokens"} and type(value) is int and 0 <= value <= 10**15},
            "provider_session_id": identifier(data.get("provider_session_id") or data.get("session_id"))}
        if data.get('budget_reservation_id'):
            payload['budget_reservation_id'] = identifier(data['budget_reservation_id'])
        if data.get('price_version'):
            payload['price_version'] = identifier(data['price_version'])
        for field in ('estimated_cost_usd', 'provider_reported_cost_usd'):
            value = data.get(field)
            if isinstance(value, str) and re.fullmatch(r'\d{1,12}(\.\d{1,12})?', value):
                payload[field] = value
        key = "usage:" + sample + (':revision:' + str(revision) if revision > 1 else '')
        self.outbox.enqueue(key, str(data.get("usage_timestamp") or event.ts), payload,
                            sample_series=series if cumulative and series else "", origin_id=binding['deployment'],
                            baseline_first=not known_new_series)

    def project(self):
        payload = {"kind": "project", "project_id": self.cloud.project_id, "version": 1,
                   "name": (self.config.project.name or self.cloud.project_id)[:160],
                   "status": "registered", "kernel_status": "unknown", "configured_agents": len(self.config.roles)}
        from zf.runtime.workflow_runtime_start import _ready_owner, _runtime_owner
        if _ready_owner(self.state_dir):
            payload['kernel_status'] = 'ready'
        elif not _runtime_owner(self.state_dir):
            payload['kernel_status'] = 'stopped'
        key = "project:" + self.cloud.deployment_id + ':' + stable(json.dumps(payload, sort_keys=True))
        payload['version'] = time.time_ns()
        self.outbox.snapshot(key, now(), payload)

    def heartbeat(self):
        import zf
        status = self.outbox.status()
        timestamp = now()
        self.outbox.enqueue("heartbeat:" + self.cloud.deployment_id + ':' + timestamp, timestamp, {
            "kind": "heartbeat", "version": time.time_ns(), "project_id": self.cloud.project_id,
            "coverage_since": status['coverage_since'], "connector_version": "1", "runtime_version": zf.__version__,
            "pending_records": status["pending_records"], "pending_bytes": status["pending_bytes"],
            "oldest_pending_at": datetime.fromtimestamp(status["oldest_pending"], timezone.utc).isoformat() if status["oldest_pending"] else None,
            "applied_generation": 0, "kernel_status": "unknown", "coverage": "zf_event_ledger"})
