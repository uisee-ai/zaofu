"""Explicit native tier: paused old impl, Given new map, real recovery/candidate."""

import pytest

from tests.conformance.test_zaofu_testkit_s5_native_claude_pipeline import _exercise_native_pipeline
from tests.testkit.native_map_replacement import NativeMapReplacement, install_scheduler_observer


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_native_old_impl_map_replacement_reaches_candidate(monkeypatch, record_property):
    def factory(runtime, tmux, record):
        observer = NativeMapReplacement(runtime, tmux, record)
        install_scheduler_observer(monkeypatch, observer)
        return observer

    _exercise_native_pipeline(monkeypatch, record_property, recovery_factory=factory)


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_native_old_verify_map_replacement_reaches_candidate(monkeypatch, record_property):
    def factory(runtime, tmux, record):
        observer = NativeMapReplacement(runtime, tmux, record, old_stage="verify")
        install_scheduler_observer(monkeypatch, observer)
        return observer

    _exercise_native_pipeline(monkeypatch, record_property, recovery_factory=factory)
