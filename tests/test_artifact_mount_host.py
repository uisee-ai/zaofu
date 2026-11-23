"""Optional Linux FUSE smoke with mock worker inputs in a dedicated transient cgroup."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_artifact_read_snapshot import IDENTITY, published
from zf.runtime.artifact_mount import MountAccess, mounted_scope, serve_mount
from zf.runtime.artifact_workdir_view import AttemptFiles


@pytest.mark.skipif(
    os.getenv("ZF_TEST_INPUT_MOUNT") != "1", reason="explicit isolated Linux mount tier"
)
def test_real_kernel_revokes_open_descriptor_and_removes_mount(tmp_path):
    import multiprocessing
    import subprocess

    from importlib.util import find_spec

    pytest.importorskip("fusepy" if find_spec("fusepy") else "fuse")
    group = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    assert "zf-input-mock-" in group and group.endswith(".scope"), (
        "use a dedicated systemd-run --user --scope"
    )
    cfg = SimpleNamespace(
        workflow=SimpleNamespace(
            flow_metadata={"artifact_worker_cgroups": {"dev-1": group}}
        )
    )
    assert mounted_scope(cfg, "dev-1") == group
    state, path = tmp_path / "state", tmp_path / "mount"
    path.mkdir()
    manifest, required, _ = published(state)
    view = AttemptFiles(
        state,
        manifest,
        {**IDENTITY, "operation_id": "mock-op"},
        required,
        lambda: {"next_action": "assess_context_gaps_then_continue"},
    )
    name = next(iter(view.files))
    revoked = multiprocessing.Event()

    def check():
        if revoked.is_set():
            raise ValueError("revoked")
        return 1

    access = MountAccess(state, view, path, group, check, "")
    daemon = multiprocessing.get_context("fork").Process(
        target=serve_mount, args=(access,)
    )
    daemon.start()
    try:
        deadline = time.monotonic() + 10
        while (
            not (path / "INPUTS.md").exists()
            and daemon.is_alive()
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert daemon.is_alive() and (path / "INPUTS.md").is_file()
        assert name in (path / "INPUTS.md").read_text()
        assert "next_action" in (path / "RESUME.md").read_text()
        assert (path / name).read_bytes() == view.read(name)[0]
        with open(path / name, "rb", buffering=0) as handle:
            assert handle.read(1)
            pid = os.fork()
            if pid == 0:
                try:
                    os.read(handle.fileno(), 1)
                except PermissionError:
                    os._exit(0)
                os._exit(1)
            assert os.waitpid(pid, 0)[1] == 0
            revoked.set()
            with pytest.raises(OSError):
                handle.read(1)
        daemon.join(6)
        assert daemon.exitcode == 0 and not os.path.ismount(path)
    finally:
        if daemon.is_alive():
            daemon.terminate()
            daemon.join(5)
        if os.path.ismount(path):
            subprocess.run(["fusermount3", "-u", str(path)], check=True)
