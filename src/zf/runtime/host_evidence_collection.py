"""Collect bounded regular media, retaining mechanical failure diagnostics."""
import base64
import hashlib
from pathlib import Path
import stat


def collect_container_evidence(output: Path, policy) -> tuple[list[dict], dict | None]:
    artifacts, total = [], 0
    relative = ""
    try:
        for artifact in sorted(output.rglob("*")):
            relative = str(artifact.relative_to(output))
            info = artifact.lstat()
            if stat.S_ISDIR(info.st_mode):
                continue
            kind = ("symlink" if stat.S_ISLNK(info.st_mode) else
                    "non_regular" if not stat.S_ISREG(info.st_mode) else
                    "per_file_limit" if info.st_size > policy.max_artifact_bytes else "")
            detail = {"stage": "collect_container_evidence", "path": relative,
                      "size_bytes": info.st_size, "file_mode": stat.S_IFMT(info.st_mode),
                      "per_file_limit_bytes": policy.max_artifact_bytes,
                      "total_limit_bytes": policy.max_total_evidence_bytes}
            if kind:
                return [], {**detail, "kind": kind}
            with artifact.open("rb") as stream:
                data = stream.read(policy.max_artifact_bytes + 1)
            total += len(data)
            if len(data) > policy.max_artifact_bytes or total > policy.max_total_evidence_bytes:
                return [], {**detail, "kind": "per_file_limit" if len(data) > policy.max_artifact_bytes
                            else "total_limit", "size_bytes": len(data), "total_bytes": total}
            artifacts.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(),
                              "base64": base64.b64encode(data).decode()})
    except OSError as exc:
        return [], {"stage": "collect_container_evidence", "path": relative,
                    "kind": "io_error", "errno": exc.errno}
    return artifacts, None
