"""Optional FUSE adapter for a dedicated, owner-configured Linux worker cgroup."""

from __future__ import annotations

import errno
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from zf.runtime.artifact_native_delivery import HOST_SCHEMA, NATIVE_CODEX_VERSION, open_call
from zf.runtime.artifact_read_ledger import append_artifact_read
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


def mounted_scope(config, actor, *, cgroups=Path("/sys/fs/cgroup")):
    from zf.core.config.artifact_worker_cgroups import artifact_worker_cgroups

    groups = artifact_worker_cgroups(config)
    value = groups.get(actor, "") if isinstance(groups, dict) else ""
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"/[\w.@:/-]+", value)
        or Path(value).as_posix() != value
        or ".." in Path(value).parts
        or value.endswith("/")
        or any(
            k != actor
            and (
                str(v) == value
                or str(v).startswith(value + "/")
                or value.startswith(str(v) + "/")
            )
            for k, v in groups.items()
        )
    ):
        raise ValueError(
            "native inputs require a dedicated worker cgroup; use artifact read"
        )
    path = cgroups / value[1:]
    if not (path / "cgroup.procs").is_file() or path.resolve() != path:
        raise ValueError("configured worker cgroup is unavailable")
    return value


def peer_identity(pid, group):
    proc = Path("/proc") / str(pid)
    if (proc / "cgroup").read_text().splitlines() != ["0::" + group]:
        raise PermissionError("caller is outside the configured worker cgroup")
    return (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]


def _slice(name):
    match = re.fullmatch(r"(inputs/[\w-]+\.txt)\.chars-(\d{1,12})-(\d{1,12})", name)
    if match:
        return match[1], (int(match[2]), int(match[3]))
    return name, None


class MountAccess:
    def __init__(self, state, view, path, group, check, version):
        self.state, self.view, self.path = state, view, path
        self.group, self.check, self.version = group, check, version
        self.transcript_cache = {}

    def authorize(self, pid):
        epoch = self.check()
        return epoch, peer_identity(pid, self.group)

    def open(self, name, pid):
        epoch, peer = self.authorize(pid)
        base, bounds = _slice(name)
        data, row = self.view.read(base, bounds)
        proof = None
        if row is not None and self.version == NATIVE_CODEX_VERSION:
            try:
                proof = open_call(
                    self.state,
                    self.view.identity["actor"],
                    str(self.path / name),
                    pid,
                    self.transcript_cache,
                )
            except (OSError, ValueError, TypeError, KeyError):
                pass
        handle = dict(
            pid=pid,
            peer=peer,
            epoch=epoch,
            data=data,
            row=row,
            proof=proof,
            contiguous=0,
        )
        if proof and not data:
            self.read(handle, 0, 0)
        return handle

    def read(self, handle, offset, size):
        if self.authorize(handle["pid"]) != (handle["epoch"], handle["peer"]):
            raise PermissionError("native read lease changed")
        data = handle["data"][offset : offset + size]
        if offset <= handle["contiguous"]:
            handle["contiguous"] = max(handle["contiguous"], offset + len(data))
        # Record only once, at the last actual read, while peer and grant are live.
        if handle["proof"] and handle["contiguous"] == len(handle["data"]):
            row, proof = handle["row"], handle["proof"]
            host = {
                **row,
                **proof,
                "schema_version": HOST_SCHEMA,
                "peer_identity": handle["peer"],
                "credential_generation": handle["epoch"],
                "tool_version": self.version,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            ref = write_immutable_json_sidecar(
                self.state,
                host,
                root="native-input-reads",
                kind="mounted_file_read",
                schema_version=HOST_SCHEMA,
                created_by="input-mount",
            )
            append_artifact_read(
                self.state,
                {
                    **row,
                    "native_host_ref": ref,
                    "production_status": "mounted_file_read",
                    "provider_delivery_status": "unknown",
                },
            )
            handle["proof"] = None
        return data


def serve_mount(access):
    import subprocess
    from threading import Event, Thread

    try:
        import fusepy as fuse  # Debian exposes this name.
    except ImportError:
        try:
            import fuse
        except (ImportError, OSError) as exc:
            raise ValueError(
                "native mount requires fusepy and libfuse2; use artifact read"
            ) from exc
    stopped = Event()

    def guarded(fn, *args):
        try:
            return fn(*args)
        except FileNotFoundError as exc:
            raise fuse.FuseOSError(errno.ENOENT) from exc
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise fuse.FuseOSError(errno.EACCES) from exc

    def revoke():
        while not stopped.wait(0.5):
            try:
                access.check()
            except (ValueError, OSError, KeyError):
                subprocess.run(
                    ["fusermount", "-u", str(access.path)],
                    capture_output=True,
                    check=False,
                )
                return

    class Operations(fuse.Operations):
        def __init__(self):
            self.handles = {}
            self.sequence = 0

        def init(self, path):
            print(
                str(access.path / "INPUTS.md"),
                str(access.path / "RESUME.md"),
                sep="\n",
                flush=True,
            )
            Thread(target=revoke, daemon=True).start()

        def getattr(self, path, fh=None):
            guarded(access.authorize, fuse.fuse_get_context()[2])
            name = path.lstrip("/")
            if name in {"", "inputs"}:
                mode, size = stat.S_IFDIR | 0o500, 0
            else:
                base, bounds = _slice(name)
                if (
                    base not in {"INPUTS.md", "RESUME.md"}
                    and base not in access.view.files
                ):
                    raise fuse.FuseOSError(errno.ENOENT)
                size = (
                    len(guarded(access.view.read, base, bounds)[0])
                    if bounds
                    else guarded(access.view.size, name)
                )
                mode = stat.S_IFREG | 0o400
            return dict(
                st_mode=mode,
                st_nlink=1,
                st_uid=os.getuid(),
                st_gid=os.getgid(),
                st_size=size,
            )

        def readdir(self, path, fh):
            guarded(access.authorize, fuse.fuse_get_context()[2])
            if path == "/":
                return [".", "..", "INPUTS.md", "RESUME.md", "inputs"]
            if path == "/inputs":
                return [".", "..", *(Path(n).name for n in access.view.files)]
            raise fuse.FuseOSError(errno.ENOTDIR)

        def open(self, path, flags):
            if flags & os.O_ACCMODE != os.O_RDONLY:
                raise fuse.FuseOSError(errno.EROFS)
            pid = fuse.fuse_get_context()[2]
            handle = guarded(access.open, path.lstrip("/"), pid)
            self.sequence += 1
            self.handles[self.sequence] = handle
            return self.sequence

        def read(self, path, size, offset, fh):
            # The kernel reports the actual caller of every read, including a transferred descriptor.
            handle = self.handles[fh]
            pid = fuse.fuse_get_context()[2]
            if pid != handle["pid"]:
                raise fuse.FuseOSError(errno.EACCES)
            return guarded(access.read, handle, offset, size)

        def release(self, path, fh):
            self.handles.pop(fh, None)
            return 0

    try:
        fuse.FUSE(
            Operations(),
            str(access.path),
            foreground=True,
            nothreads=True,
            ro=True,
            direct_io=True,
            attr_timeout=0,
            entry_timeout=0,
            negative_timeout=0,
            fsname="zf-inputs",
        )
    finally:
        stopped.set()
