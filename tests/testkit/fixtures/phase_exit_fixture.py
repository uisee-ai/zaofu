"""Explicitly selected subprocess fixture; never part of default discovery."""

import os
import signal

import pytest


def abort() -> None:
    mode = os.environ["ZF_TESTKIT_FIXTURE_EXIT_MODE"]
    if mode == "exit":
        os._exit(42)
    os.kill(os.getpid(), getattr(signal, mode))
    raise AssertionError("fixture termination did not occur")


@pytest.fixture
def interrupted_phase():
    phase = os.environ["ZF_TESTKIT_FIXTURE_EXIT_PHASE"]
    if phase == "setup":
        abort()
    yield
    if phase == "teardown":
        abort()


def test_previous_pass():
    assert True


def test_previous_failure():
    assert False, "original ordinary failure before abrupt exit"


def test_interrupted(interrupted_phase):
    if os.environ["ZF_TESTKIT_FIXTURE_EXIT_PHASE"] == "call":
        abort()


def test_never_reached():
    raise AssertionError("must not execute after process termination")
