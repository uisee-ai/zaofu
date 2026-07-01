"""Deterministic self-eval runner."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from zf.core.self_eval.contract import (
    SelfEvalCommand,
    SelfEvalContract,
    SelfEvalContractError,
    load_self_eval_contract,
    split_local_command,
)


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: str
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class SelfEvalRunResult:
    status: str
    score: float | None
    output_dir: Path
    iterations_path: Path
    summary_path: Path
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "passed"


def run_self_eval(
    contract_path: Path,
    *,
    output_dir: Path | None = None,
    cwd: Path | None = None,
) -> SelfEvalRunResult:
    """Run a deterministic self-eval contract and write result artifacts."""
    contract = load_self_eval_contract(contract_path)
    out_dir = output_dir or Path(contract.output.dir)
    if not out_dir.is_absolute() and cwd is not None:
        out_dir = cwd / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    iterations_path = out_dir / contract.output.iterations
    summary_path = out_dir / contract.output.summary

    command_results: list[CommandResult] = []
    guard_status = "passed"
    status = "failed"
    score: float | None = None
    reason = ""

    for guard in contract.guards:
        result = _run_command(guard, cwd=cwd)
        command_results.append(result)
        if result.exit_code != 0:
            guard_status = "failed"
            reason = f"guard {guard.name!r} failed with exit code {result.exit_code}"
            _write_outputs(
                contract,
                iterations_path,
                summary_path,
                status=status,
                score=score,
                guard_status=guard_status,
                reason=reason,
                command_results=command_results,
            )
            return SelfEvalRunResult(status, score, out_dir, iterations_path, summary_path, reason)

    verify_result = _run_command(contract.verify, cwd=cwd)
    command_results.append(verify_result)
    if verify_result.exit_code != 0:
        reason = f"verify failed with exit code {verify_result.exit_code}"
    else:
        try:
            score = _extract_score(contract, verify_result.stdout)
            status = "passed"
            reason = "ok"
        except SelfEvalContractError as exc:
            reason = "; ".join(exc.errors)

    _write_outputs(
        contract,
        iterations_path,
        summary_path,
        status=status,
        score=score,
        guard_status=guard_status,
        reason=reason,
        command_results=command_results,
    )
    return SelfEvalRunResult(status, score, out_dir, iterations_path, summary_path, reason)


def _run_command(command: SelfEvalCommand, *, cwd: Path | None) -> CommandResult:
    env_overrides, argv = split_local_command(command.command)
    env = os.environ.copy()
    env.update(env_overrides)
    completed = subprocess.run(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )
    return CommandResult(
        name=command.name,
        command=command.command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _extract_score(contract: SelfEvalContract, stdout: str) -> float:
    match = re.search(contract.metric.pattern, stdout)
    if match is None:
        raise SelfEvalContractError([
            f"missing numeric metric evidence for {contract.metric.name!r}",
        ])
    if "score" in match.groupdict():
        raw = match.group("score")
    elif match.lastindex:
        raw = match.group(1)
    else:
        raw = match.group(0)
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise SelfEvalContractError([
            f"metric {contract.metric.name!r} is not numeric: {raw!r}",
        ]) from exc


def _write_outputs(
    contract: SelfEvalContract,
    iterations_path: Path,
    summary_path: Path,
    *,
    status: str,
    score: float | None,
    guard_status: str,
    reason: str,
    command_results: list[CommandResult],
) -> None:
    score_text = "" if score is None else str(score)
    iterations_path.write_text(
        "iteration\tscore\tguard\tstatus\tdescription\n"
        f"1\t{score_text}\t{guard_status}\t{status}\t{_tsv(reason)}\n",
        encoding="utf-8",
    )
    lines = [
        "# Self-Eval Summary",
        "",
        f"- Goal: {contract.goal}",
        f"- Metric: {contract.metric.name} ({contract.metric.direction})",
        f"- Status: {status}",
        f"- Score: {score_text or 'n/a'}",
        f"- Reason: {reason}",
        "",
        "## Commands",
    ]
    for result in command_results:
        lines.extend([
            "",
            f"### {result.name}",
            "",
            f"- Command: `{result.command}`",
            f"- Exit code: {result.exit_code}",
            "",
            "stdout:",
            "```text",
            _clip(result.stdout),
            "```",
            "",
            "stderr:",
            "```text",
            _clip(result.stderr),
            "```",
        ])
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clip(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text.rstrip()
    return text[:limit].rstrip() + "\n...[truncated]"


def _tsv(text: str) -> str:
    return text.replace("\t", " ").replace("\n", " ").strip()
