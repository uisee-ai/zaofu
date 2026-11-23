"""Worker placement exercises the normal tmux launch and exclusive host ownership."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

import pytest

from zf.core.config.loader import load_config
from zf.runtime import artifact_worker_placement as placement
from zf.runtime.transport import make_transport


def test_only_the_explicit_owned_scope_wraps_the_launch(tmp_path, monkeypatch):
    state = tmp_path / "custom-state"
    monkeypatch.setattr(placement, "_app_slice", lambda: "/test/app.slice")
    monkeypatch.setattr(placement.shutil, "which", lambda _: "/usr/bin/systemd-run")
    expected = placement.managed_worker_cgroup(tmp_path, state, "reader")
    assert (
        placement.worker_scope_prefix(tmp_path, state, "reader", expected)[-1] == "--"
    )
    for wrong_root, wrong_state, wrong_actor in [
        (tmp_path / "other", state, "reader"),
        (tmp_path, state / "other", "reader"),
        (tmp_path, state, "other"),
    ]:
        with pytest.raises(ValueError, match="does not match"):
            placement.worker_scope_prefix(
                wrong_root, wrong_state, wrong_actor, expected
            )
    with pytest.raises(ValueError, match="outside"):
        placement.worker_scope_prefix(
            tmp_path, state, "reader", expected.replace("/test/", "/other/")
        )

    def unavailable():
        raise FileNotFoundError("missing user systemd")

    monkeypatch.setattr(placement, "_app_slice", unavailable)
    assert placement.worker_scope_prefix(tmp_path, state, "reader", expected) == []
    assert (
        placement.worker_scope_prefix(
            tmp_path, state, "reader", "/external/reader.scope"
        )
        == []
    )


@pytest.mark.host
@pytest.mark.skipif(
    os.getenv("ZF_TEST_WORKER_PLACEMENT") != "1",
    reason="explicit isolated tmux and systemd user scope",
)
def test_tmux_places_only_the_bound_worker_and_refuses_a_duplicate(
    tmp_path, monkeypatch
):
    from zf.runtime.artifact_mount import mounted_scope, peer_identity

    state = tmp_path / "custom-state"
    actor = "planner"
    expected = placement.managed_worker_cgroup(tmp_path, state, actor)
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "zf.yaml"
    config.write_text(f"""apiVersion: zaofu.dev/v1
kind: PrdFlow
spec:
  lanes: 1
  artifactWorkerCgroups:
    planner: {expected}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  project: {{name: placement, state_dir: custom-state}}
  session: {{tmux_session: scope-pilot}}
""")
    cfg = load_config(config)
    role = next(r for r in cfg.roles if r.instance_id == actor)
    with TemporaryDirectory(prefix="zf-worker-placement-") as socket_dir:
        monkeypatch.setenv("TMUX_TMPDIR", socket_dir)
        monkeypatch.delenv("TMUX", raising=False)
        transport = make_transport(cfg).for_role(actor)
        observed = tmp_path / "pid"
        prefix = placement.worker_scope_prefix(tmp_path, state, actor, expected)
        transport.init()
        try:
            transport.spawn(
                role,
                [
                    sys.executable,
                    "-c",
                    "import os,time; from pathlib import Path; Path('pid').write_text(str(os.getpid())); time.sleep(40)",
                ],
                cwd=tmp_path,
            )
            deadline = time.monotonic() + 15
            while not observed.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert observed.is_file(), transport.tmux.capture_pane(actor)
            pid = int(observed.read_text())
            assert peer_identity(pid, mounted_scope(cfg, actor))
            other = next(r for r in cfg.roles if r.instance_id == "dev-lane-0")
            other_result = tmp_path / "other-cgroup"
            transport.spawn(
                other,
                ["sh", "-c", "cat /proc/self/cgroup > other-cgroup"],
                cwd=tmp_path,
            )
            until = time.monotonic() + 5
            while not other_result.exists() and time.monotonic() < until:
                time.sleep(0.05)
            assert "0::" in other_result.read_text()
            assert expected not in other_result.read_text()
            second = tmp_path / "second"
            with pytest.raises(RuntimeError, match="already occupied"):
                transport.spawn(role, ["touch", str(second)], cwd=tmp_path)
            # A racing caller that computed its launch before the scope was
            # acquired still fails the user manager's atomic creation check.
            duplicate = [*prefix, "touch", str(second)]
            assert (
                subprocess.run(duplicate, capture_output=True, timeout=8).returncode
                != 0
            )
            assert not second.exists()
        finally:
            transport.shutdown()
            # A provider may survive a terminal disconnect. Stop only the
            # transient unit derived from this test's unique tmp_path binding.
            subprocess.run(
                ["systemctl", "--user", "stop", Path(expected).name],
                capture_output=True,
                timeout=8,
            )
