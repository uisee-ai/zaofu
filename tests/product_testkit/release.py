"""Release-artifact contract checks independent of a particular language."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import shutil
from typing import Any, Mapping

from tests.testkit.evidence import write_json_atomic
from tests.testkit.command_runner import run_command


class ReleaseArtifactError(ValueError):
    pass


def inspect_artifact(path: Path, *, kind: str) -> dict[str, str | int]:
    if kind not in {"wheel", "sdist", "container", "npm", "binary"}:
        raise ReleaseArtifactError(f"unsupported artifact kind: {kind}")
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise ReleaseArtifactError(f"artifact missing or empty: {path}")
    if kind == "wheel" and not path.name.endswith(".whl"):
        raise ReleaseArtifactError("wheel artifact must end with .whl")
    if kind == "sdist" and not (path.name.endswith(".tar.gz") or path.name.endswith(".zip")):
        raise ReleaseArtifactError("sdist artifact has invalid extension")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"name": path.name, "kind": kind, "size": path.stat().st_size, "digest": digest}


def run_release_artifact_checks(
    artifacts: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    *,
    project_root: Path,
    output_dir: Path,
) -> tuple[dict[str, Any], ...]:
    """Inspect declared release outputs and copy immutable evidence into the run.

    The source path is resolved below ``project_root`` and must be a regular
    file.  A copy is made under ``output_dir/release`` so the resulting
    artifact index never points into a mutable product worktree.
    """

    results: list[dict[str, Any]] = []
    source_base = project_root.resolve()
    release_root = output_dir / "release"
    release_root.mkdir(parents=True, exist_ok=True)
    for spec in artifacts:
        artifact_id = str(spec["id"])
        relative = Path(str(spec["path"]))
        source = (source_base / relative).resolve()
        row: dict[str, Any] = {
            "artifact_id": artifact_id,
            "path": relative.as_posix(),
            "kind": str(spec["kind"]),
            "mandatory": bool(spec.get("mandatory", True)),
            "deterministic": bool(spec.get("deterministic", True)),
            "passed": False,
            "artifact_ref": "",
            "receipt_ref": f"release.{artifact_id}.receipt.json",
        }
        try:
            source.relative_to(source_base)
            record = inspect_artifact(source, kind=str(spec["kind"]))
            destination = release_root / artifact_id / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            # Re-inspect the copied bytes; this catches a concurrent replacement
            # between source validation and evidence capture.
            copied = inspect_artifact(destination, kind=str(spec["kind"]))
            if copied["digest"] != record["digest"]:
                raise ReleaseArtifactError("release artifact changed while captured")
            row.update({
                "passed": True,
                "name": str(record["name"]),
                "size": int(record["size"]),
                "digest": str(record["digest"]),
                "artifact_ref": destination.relative_to(output_dir).as_posix(),
            })
        except (OSError, ReleaseArtifactError, ValueError) as exc:
            row.update({"error": str(exc), "error_type": type(exc).__name__})
        receipt_path = output_dir / str(row["receipt_ref"])
        write_json_atomic(receipt_path, row)
        results.append(row)
    return tuple(results)


def run_release_scenarios(
    scenarios: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    *,
    project_root: Path,
    output_dir: Path,
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Execute install, upgrade and rollback in order for each declaration."""

    results: list[dict[str, Any]] = []
    for spec in scenarios:
        scenario_id = str(spec["id"])
        scenario_dir = output_dir / "release" / "scenarios" / scenario_id
        steps: list[dict[str, Any]] = []
        failed_phase = ""
        for phase in ("install", "upgrade", "rollback"):
            receipt = run_command(
                str(spec[f"{phase}_command"]),
                command_id=f"release.{scenario_id}.{phase}",
                cwd=project_root,
                output_dir=scenario_dir,
                env=env,
                timeout_seconds=float(spec["timeout_seconds"]),
            )
            steps.append({**receipt.__dict__, "passed": receipt.passed})
            if not receipt.passed and not failed_phase:
                failed_phase = phase
                break
        row: dict[str, Any] = {
            "scenario_id": scenario_id,
            "mandatory": bool(spec.get("mandatory", True)),
            "deterministic": bool(spec.get("deterministic", True)),
            "passed": not failed_phase and len(steps) == 3,
            "steps": steps,
            "failed_phase": failed_phase,
        }
        failed_step = steps[-1] if failed_phase else None
        if not failed_phase:
            row["verdict"] = "pass"
        elif failed_step and (failed_step.get("environment_error") or failed_step.get("timed_out")):
            row["verdict"] = "environment_failure"
        else:
            row["verdict"] = "fail"
        if failed_phase:
            row["error"] = str(steps[-1].get("error") or f"{failed_phase} command failed")
        receipt_path = output_dir / f"release.{scenario_id}.receipt.json"
        row["receipt_ref"] = receipt_path.name
        write_json_atomic(receipt_path, row)
        results.append(row)
    return tuple(results)

