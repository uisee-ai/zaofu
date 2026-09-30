"""S6 regression probes for CLI flow initialization and routing.

These probes intentionally retain currently failing product behavior as
strict xfails.  The underlying assertions are the existing CLI-flow contract
tests; the wrappers make the failures visible in the TestKit registry without
changing production behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-024: project init does not preserve specialized IssueFlow/"
        "RefactorFlow kind in the generated config"
    ),
)
@pytest.mark.parametrize(
    "case_name",
    [
        "test_project_init_creates_flow_project",
        "test_project_init_defaults_to_multi_kind_without_ignition",
        "test_project_init_refactor_requires_source_and_git",
    ],
)
def test_project_init_preserves_specialized_flow_kind(
    case_name: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests import test_cli_flow

    getattr(test_cli_flow, case_name)(tmp_path, capsys, monkeypatch)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-025: project init --apply with a clear Issue request returns "
        "STOP instead of accepted"
    ),
)
def test_project_init_apply_accepts_clear_request(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_cli_flow import test_project_init_clear_request_only_ignites_with_apply

    test_project_init_clear_request_only_ignites_with_apply(tmp_path, capsys, monkeypatch)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-026: multi-kind workflow submissions return STOP and do not "
        "route issue/prd/refactor independently"
    ),
)
def test_multi_kind_submit_routes_without_stop(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_cli_flow import test_multi_kind_project_submits_each_kind_without_route_crosstalk

    test_multi_kind_project_submits_each_kind_without_route_crosstalk(
        tmp_path, capsys, monkeypatch
    )
