"""S2 process-crash recovery for the canonical non-task stores."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


def _paths(root: Path, kind: str) -> tuple[Path, Path]:
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    if kind == "feature":
        return state / "feature_list.json", state / "feature_list.json"
    if kind == "session":
        return state / "session.yaml", state / "session.yaml"
    if kind == "role":
        return state / "role_sessions.yaml", state / "role_sessions.yaml"
    raise AssertionError(f"unknown store kind: {kind}")


def _seed(root: Path, kind: str) -> Path:
    path, _ = _paths(root, kind)
    if kind == "feature":
        FeatureStore(path).add(Feature(id="F-S2-SEED", title="seed"))
    elif kind == "session":
        SessionStore(path).create(project_root=str(root))
    else:
        RoleSessionRegistry(path, project_root=str(root)).get_or_create("dev")
    return path


def _apply_update(root: Path, kind: str) -> None:
    path, _ = _paths(root, kind)
    if kind == "feature":
        FeatureStore(path).add(Feature(id="F-S2-NEW", title="after-restart"))
    elif kind == "session":
        SessionStore(path).update(runtime_state="active")
    else:
        RoleSessionRegistry(path, project_root=str(root)).get_or_create("review")


def _child(root: Path, kind: str) -> None:
    if os.environ.get("ZF_TESTKIT_STORE_REPLACE_CRASH") == "1":
        from zf.core.state import atomic_io

        atomic_io.os.replace = lambda _source, _destination: os._exit(97)
    _apply_update(root, kind)


def _run_child(root: Path, kind: str, *, crash: bool) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ZF_TESTKIT_STORE_REPLACE_CRASH"] = "1" if crash else "0"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.conformance.test_zaofu_testkit_s2_store_crash",
            "--child",
            kind,
            str(root),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


@pytest.mark.parametrize("kind", ["feature", "session", "role"])
def test_store_replace_crash_preserves_snapshot_and_restart_recovers(
    tmp_path: Path,
    request,
    kind: str,
) -> None:
    attach_runtime_case_evidence(
        request, tmp_path / "state",
        given=f"{kind} canonical store has a valid seed snapshot",
        when="crash exactly at atomic replace, clean residue, then restart and update",
        then="old snapshot survives the crash and the normal retry is applied",
        forbidden=["partial canonical overwrite", "lost seed state", "duplicate recovery update"],
        inputs={"store_kind": kind},
    )
    path = _seed(tmp_path, kind)
    crashed = _run_child(tmp_path, kind, crash=True)
    assert crashed.returncode == 97, crashed.stderr
    assert path.exists()
    if kind == "feature":
        assert [item["title"] for item in json.loads(path.read_text())] == ["seed"]
    elif kind == "session":
        assert yaml.safe_load(path.read_text())["runtime_state"] == "created"
    else:
        assert set(yaml.safe_load(path.read_text())["roles"]) == {"dev"}
    # A hard process exit bypasses atomic_write_text's exception cleanup.  The
    # canonical snapshot must remain valid; TestKit removes this expected
    # crash residue before exercising the normal retry path.
    orphans = list(path.parent.glob(path.name + ".*.tmp"))
    assert orphans
    for orphan in orphans:
        orphan.unlink()

    recovered = _run_child(tmp_path, kind, crash=False)
    assert recovered.returncode == 0, recovered.stderr
    if kind == "feature":
        assert [item.title for item in FeatureStore(path).list_all()] == [
            "seed",
            "after-restart",
        ]
    elif kind == "session":
        assert SessionStore(path).load().runtime_state == "active"
    else:
        assert set(yaml.safe_load(path.read_text())["roles"]) == {"dev", "review"}


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--child":
    _child(Path(sys.argv[3]), sys.argv[2])
