"""B-COST-02 step2: claude-code usage-capture miss probe.

When ``session_path`` returns None for a claude-code worker whose uuid is
already known, the orchestrator must (after debounce) emit
``cost.usage.capture_miss`` so a silent 0-usage capture leaves a signal in
events.jsonl. Codex (date-bucketed, cwd-independent) and the early-boot
empty-uuid window must NOT trip it.
"""

from __future__ import annotations

from types import SimpleNamespace

from zf.runtime.lifecycle_observation import (
    LifecycleObservationMixin,
    _USAGE_CAPTURE_MISS_THRESHOLD,
)


class _CapturingWriter:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)


class _Host(LifecycleObservationMixin):
    def __init__(self):
        self._usage_capture_misses = {}
        self.event_writer = _CapturingWriter()


def _role(backend="claude-code", instance_id="dev-1"):
    return SimpleNamespace(backend=backend, instance_id=instance_id)


def _misses(host):
    return [e for e in host.event_writer.events if e.type == "cost.usage.capture_miss"]


def test_emits_once_after_threshold_consecutive_misses():
    host = _Host()
    role = _role()
    for _ in range(_USAGE_CAPTURE_MISS_THRESHOLD - 1):
        host._note_usage_capture_miss(role, "/wt/.zf-A/proj", "uuid-1")
        assert _misses(host) == []  # debounced, not yet
    host._note_usage_capture_miss(role, "/wt/.zf-A/proj", "uuid-1")
    evts = _misses(host)
    assert len(evts) == 1
    p = evts[0].payload
    assert p["session_id"] == "uuid-1"
    assert p["consecutive_misses"] == _USAGE_CAPTURE_MISS_THRESHOLD
    assert p["escaped_project_dir"] == "-wt--zf-A-proj"
    assert evts[0].actor == "dev-1"


def test_does_not_re_emit_after_threshold():
    host = _Host()
    role = _role()
    for _ in range(_USAGE_CAPTURE_MISS_THRESHOLD + 4):
        host._note_usage_capture_miss(role, "/wt/proj", "uuid-1")
    assert len(_misses(host)) == 1  # single signal, no spam


def test_empty_session_id_is_early_boot_not_a_miss():
    host = _Host()
    role = _role()
    for _ in range(_USAGE_CAPTURE_MISS_THRESHOLD + 2):
        host._note_usage_capture_miss(role, "/wt/proj", "")
    assert _misses(host) == []
    assert host._usage_capture_misses == {}


def test_codex_is_not_path_sensitive():
    host = _Host()
    role = _role(backend="codex", instance_id="worker-1")
    for _ in range(_USAGE_CAPTURE_MISS_THRESHOLD + 2):
        host._note_usage_capture_miss(role, "/wt/proj", "uuid-1")
    assert _misses(host) == []
