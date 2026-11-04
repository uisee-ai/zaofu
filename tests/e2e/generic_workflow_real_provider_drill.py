#!/usr/bin/env python3
"""Run the Generic Workflow complex closure with one real Verify provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import signal
import shutil
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zf.runtime.artifact_delivery_result import CLAIM_STATUSES, VERDICTS

from tests.e2e.test_generic_workflow_complex_mock_e2e import (
    run_generic_workflow_complex_scenario,
)
from tests.e2e.oa_multiflow_mock_pilot import source_identity
from tests.e2e.oa_provider_ab_pilot import provider_usage
from tests.e2e.thin_judge_goal_closure_provider_drill import (
    _invoke_claude,
    _invoke_codex,
)


_TEMP_PREFIX = "zf-generic-workflow-provider-"


class _DrillTerminated(RuntimeError):
    def __init__(self, signum: int) -> None:
        super().__init__(f"real-provider drill terminated by signal {signum}")
        self.signum = signum


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one isolated real-provider Verify turn inside the Generic "
            "Workflow replan/closure scenario."
        ),
    )
    parser.add_argument(
        "--backend",
        required=True,
        choices=("claude-code", "codex"),
    )
    parser.add_argument("--confirm-real", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument(
        "--reasoning-effort",
        choices=("minimal", "low", "medium", "high", "xhigh"),
        default="medium",
    )
    return parser


def _shape_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(value),
            "properties": {
                str(key): _shape_schema(item)
                for key, item in value.items()
            },
        }
    if isinstance(value, list):
        if not value:
            return {
                "type": "array",
                "minItems": 0,
                "maxItems": 0,
                "items": {"type": "string"},
            }
        return {
            "type": "array",
            "minItems": len(value),
            "maxItems": len(value),
            "items": _shape_schema(value[0]),
        }
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if value is None:
        return {"type": "null"}
    return {"type": "string"}


def _descriptor_path(state_dir: Path, descriptor: Mapping[str, Any]) -> Path:
    ref = Path(str(descriptor.get("ref") or ""))
    return ref if ref.is_absolute() else state_dir / ref


def _provider_verifier(
    *,
    backend: str,
    timeout_seconds: int,
    model: str,
    reasoning_effort: str,
    audit_holder: dict[str, Any],
):
    def verify(
        project_root: Path,
        state_dir: Path,
        sources: Mapping[str, Any],
        expected: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        source_paths = {
            name: _descriptor_path(state_dir, descriptor)
            for name, descriptor in sources.items()
            if isinstance(descriptor, Mapping)
        }
        verification_contract = (
            state_dir / "provider-verification-contract.json"
        )
        verification_contract.write_text(
            json.dumps(
                {"artifact_delivery_result": expected},
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        source_paths["verification_contract"] = verification_contract
        inline_inputs: list[dict[str, Any]] = []
        if backend == "codex":
            for name, path in sorted(source_paths.items()):
                content = path.read_text(encoding="utf-8")
                encoded = content.encode("utf-8")
                if len(encoded) > 128 * 1024:
                    raise AssertionError(
                        f"provider input exceeds inline limit: {path}",
                    )
                inline_inputs.append({
                    "name": name,
                    "path": str(path),
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                    "byte_count": len(encoded),
                    "content": content,
                })
        read_instruction = (
            "Use the Read tool once for every path listed above before "
            "answering."
            if backend == "claude-code"
            else (
                "The canonical input bytes are delivered inline below. Do not "
                "invoke shell commands or file tools."
            )
        )
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["artifact_delivery_result"],
            "properties": {
                "artifact_delivery_result": _shape_schema(expected),
            },
        }
        fields = schema["properties"]["artifact_delivery_result"]["properties"]
        fields["verdict"]["enum"] = sorted(VERDICTS)
        fields["recommended_action"]["enum"] = ["complete", "gap_plan", "replan", "human", "hold"]
        fields["open_gap_refs"] = {"type": "array", "items": {"type": "string"}}
        fields["goal_coverage"]["items"]["properties"]["status"]["enum"] = sorted(CLAIM_STATUSES)
        prompt = "\n".join([
            "You are the independent read-only verifier for a ZaoFu Generic "
            "Workflow provider drill.",
            "Read every canonical input and the pinned verification contract "
            "before answering:",
            *[
                f"- {name}: {path}"
                for name, path in sorted(source_paths.items())
            ],
            read_instruction,
            "Confirm that the report closes the mandatory claim using two "
            "independent evidence families.",
            "Do not modify files and do not run tests, builds, package "
            "commands, or git mutations.",
            "Return only the artifact_delivery_result required by the supplied "
            "JSON schema. The verification contract pins runtime identity.",
            "For passed use complete with no gaps; for rejected use gap_plan or replan "
            "with gap refs; for blocked use human or hold.",
            *(
                [
                    "INLINE_CANONICAL_INPUTS:",
                    json.dumps(
                        inline_inputs,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ]
                if inline_inputs
                else []
            ),
        ])
        if backend == "claude-code":
            output, audit = _invoke_claude(
                project_root=project_root,
                state_dir=state_dir,
                prompt=prompt,
                schema=schema,
                timeout=timeout_seconds,
            )
        else:
            output, audit = _invoke_codex(
                project_root=project_root,
                state_dir=state_dir,
                prompt=prompt,
                schema=schema,
                timeout=timeout_seconds,
                skip_git_repo_check=True,
                require_read_commands=False,
                model=model,
                reasoning_effort=reasoning_effort,
            )
        result = output.get("artifact_delivery_result")
        if not isinstance(result, Mapping):
            raise AssertionError(
                "provider did not return artifact_delivery_result",
            )
        safe_audit = {
            key: value
            for key, value in audit.items()
            if key not in {"raw_stdout", "stderr"}
        }
        safe_audit["provider_result"] = dict(result)
        safe_audit["verification_contract"] = dict(expected)
        raw_rows = [
            json.loads(line)
            for line in str(audit.get("raw_stdout") or "").splitlines()
            if line.strip()
        ]
        safe_audit["usage"] = provider_usage(raw_rows)
        safe_audit["prompt_sha256"] = hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest()
        if backend == "codex":
            commands = [
                str(command)
                for command in safe_audit.get("commands") or []
            ]
            if commands or safe_audit.get("file_changes"):
                raise AssertionError(
                    "Codex inline-context Verify performed an external "
                    f"side effect: commands={commands}, "
                    f"file_changes={safe_audit.get('file_changes')}",
                )
            safe_audit["context_delivery"] = {
                "mode": "inline_canonical_inputs",
                "inputs": [
                    {
                        key: item[key]
                        for key in (
                            "name",
                            "path",
                            "sha256",
                            "byte_count",
                        )
                    }
                    for item in inline_inputs
                ],
            }
        else:
            read_paths = {
                str(path)
                for path in safe_audit.get("read_paths") or []
            }
            missing_reads = [
                str(path)
                for path in source_paths.values()
                if str(path) not in read_paths
            ]
            if missing_reads:
                raise AssertionError(
                    f"Claude did not read required provider inputs: "
                    f"{missing_reads}",
                )
        audit_holder.update(safe_audit)
        (state_dir / "provider-audit.json").write_text(
            json.dumps(safe_audit, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (state_dir / "provider-raw.jsonl").write_text(
            str(audit.get("raw_stdout") or ""),
            encoding="utf-8",
        )
        return dict(result)

    return verify


def _cleanup(root: Path) -> None:
    resolved = root.resolve()
    if resolved.parent != Path("/tmp") or not resolved.name.startswith(
        _TEMP_PREFIX
    ):
        raise RuntimeError(f"refusing to clean unsafe provider root: {resolved}")
    shutil.rmtree(resolved)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm_real:
        raise SystemExit("pass --confirm-real to invoke a real provider")
    root = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX, dir="/tmp"))
    audit: dict[str, Any] = {}
    started = time.monotonic()
    identity = source_identity(_REPO_ROOT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _terminate(signum, _frame) -> None:  # noqa: ANN001
        raise _DrillTerminated(int(signum))

    signal.signal(signal.SIGTERM, _terminate)
    try:
        # This scenario exercises the kernel in-process; only Verify is real.
        with patch(
            "zf.runtime.workflow_runtime_start.ensure_project_runtime",
            return_value={"ok": True, "status": "running", "pid": 123, "launched": False},
        ):
            result = run_generic_workflow_complex_scenario(
                root,
                provider_verifier=_provider_verifier(
                    backend=args.backend,
                    timeout_seconds=args.timeout_seconds,
                    model=args.model,
                    reasoning_effort=args.reasoning_effort,
                    audit_holder=audit,
                ),
                provider_backend=args.backend,
            )
        result.update({
            "schema_version": "generic-workflow-real-provider-drill.v1",
            "status": "passed",
            "execution_mode": "hybrid_real_provider",
            "control_plane": "mock",
            "source_identity": identity,
            "backend": args.backend,
            "model": args.model if args.backend == "codex" else "",
            "reasoning_effort": (
                args.reasoning_effort if args.backend == "codex" else ""
            ),
            "provider_session_id": audit.get("provider_session_id", ""),
            "prompt_sha256": audit.get("prompt_sha256", ""),
            "usage": audit.get("usage", {}),
            "duration_seconds": round(time.monotonic() - started, 3),
            "budget": {
                "provider_turns": 1,
                "timeout_seconds": args.timeout_seconds,
            },
            "provider_audit": audit,
            "oa": dict(result.get("oa") or {}),
            "temporary_root": str(root),
            "cleaned": True,
        })
        return result
    except Exception:
        if audit:
            print(json.dumps({
                "status": "failed",
                "execution_mode": "hybrid_real_provider",
                "control_plane": "mock",
                "provider_session_id": audit.get("provider_session_id", ""),
                "provider_result": audit.get("provider_result", {}),
                "usage": audit.get("usage", {}),
            }, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        raise
    finally:
        try:
            _cleanup(root)
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)


def main() -> int:
    result = run(_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
