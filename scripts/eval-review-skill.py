#!/usr/bin/env python3
"""Bounded, isolated Codex A/B runs for zf-cr; semantic verdicts require review.

Results contain transcripts and metrics, never an automatic semantic pass.
Use --prepare-only for offline fixture/package validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests/fixtures/review_skill_eval/cases.json"


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def prepare(directory: Path, files: dict[str, str], bundle: Path) -> None:
    for relative in files:
        path = Path(relative)
        if (path.is_absolute() or not path.parts or ".." in path.parts
                or path.parts[0] in (".git", ".agents")):
            raise ValueError(f"Unsafe or reserved fixture path: {relative}")
    directory.mkdir(parents=True)
    for relative, content in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    shutil.copytree(bundle, directory / ".agents/skills/zf-cr")
    # Give both arms identical history; no task solutions or grading rubrics here.
    subprocess.run(["git", "init", "-q", str(directory)], check=True)
    paths = list(files) + [".agents/skills/zf-cr"]
    subprocess.run(["git", "-C", str(directory), "add", "--", *paths], check=True)
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-10-20T00:00:00Z",
           "GIT_COMMITTER_DATE": "2026-10-20T00:00:00Z"}
    subprocess.run(
        ["git", "-C", str(directory), "-c", "user.name=Skill Eval",
         "-c", "user.email=eval@example.invalid", "commit", "-qm", "test: fixture"],
        check=True, env=env,
    )


def collect_trace(path: Path) -> dict:
    usage, commands, answers, errors = {}, [], [], []
    completed = False
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            errors.append("invalid_jsonl")
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
            completed = True
        if event.get("type") in ("error", "turn.failed"):
            errors.append(event.get("type"))
        if event.get("type") != "item.completed":
            continue
        item = event.get("item", {})
        if item.get("type") == "error":
            errors.append(item.get("message", "item.error"))
        if item.get("type") == "command_execution":
            commands.append({
                "command": item.get("command"),
                "exit_code": item.get("exit_code"),
            })
        if item.get("type") == "agent_message":
            answers.append(item.get("text", ""))
    return {
        "turn_completed": completed, "usage": usage, "commands": commands,
        "answers": answers, "errors": errors,
        # A hint for human trajectory inspection, not proof of invocation.
        "skill_read_command_seen": any(
            "zf-cr" in str(c["command"]) and "SKILL.md" in str(c["command"])
            for c in commands
        ),
    }


def run_one(run_dir: Path, prompt: str, args: argparse.Namespace) -> dict:
    project = run_dir / "project"
    container_name = "zf-review-eval-" + digest(str(run_dir))[:12]
    command = [
        "codex", "exec", "--ephemeral", "--ignore-user-config",
        "-s", "read-only", "-c", 'approval_policy="never"',
        "-m", args.model, "--json", "-C", str(project),
        "--output-last-message", str(run_dir / "answer.txt"), "-",
    ]
    if args.docker_image:
        # The enclosing container enforces read-only inputs/rootfs. No host
        # checkout, daemon socket or writable host directory is exposed.
        command = [
            "docker", "run", "--rm", "-i", "--name", container_name,
            "--read-only", "--cap-drop=ALL", "--cap-add=DAC_OVERRIDE",
            "--security-opt=no-new-privileges",
            "--memory=1g", "--cpus=1", "--network=host",
            "--tmpfs", "/tmp:rw,nosuid,size=128m",
            "--tmpfs", "/root/.codex:rw,nosuid,size=128m",
            "-v", f"{Path(shutil.which('codex')).resolve().parent}:/opt/codex:ro",
            "-v", f"{Path(shutil.which('rg')).resolve()}:/usr/local/bin/rg:ro",
            "-v", f"{args.auth_file.resolve()}:/root/.codex/auth.json:ro",
            "-v", f"{project.resolve()}:/workspace:ro",
            "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory",
            "-e", "GIT_CONFIG_VALUE_0=/workspace",
        ]
        for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "NO_PROXY"):
            if key in os.environ:
                command.extend(["-e", key])
        command.extend([
            "--workdir", "/workspace", "--entrypoint", "/opt/codex/codex",
            args.docker_image, "exec", "--ephemeral", "--ignore-user-config",
            "-s", "danger-full-access", "-c", 'approval_policy="never"',
            "-c", 'web_search="disabled"', "-m", args.model, "--json", "-",
        ])
    before = snapshot(project)
    started = time.monotonic()
    timed_out = False
    with (run_dir / "trace.jsonl").open("w") as out, (
        run_dir / "stderr.txt"
    ).open("w") as err:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=out, stderr=err,
            text=True, start_new_session=True,
        )
        try:
            process.communicate(prompt, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        finally:
            if args.docker_image:
                subprocess.run(["docker", "rm", "-f", container_name],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    after = snapshot(project)
    result = {
        "command": command, "seconds": round(time.monotonic() - started, 3),
        "returncode": process.returncode, "timed_out": timed_out,
        "changed_paths": sorted(k for k in before.keys() | after.keys()
                                if before.get(k) != after.get(k)),
        "before_digest": digest(before), "after_digest": digest(after),
        "trace_sha256": hashlib.sha256((run_dir / "trace.jsonl").read_bytes()).hexdigest(),
        **collect_trace(run_dir / "trace.jsonl"),
    }
    (run_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    if args.docker_image:
        (run_dir / "answer.txt").write_text("\n\n".join(result["answers"]) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, default=ROOT / "skills/zf-cr")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--repetitions", type=int, default=3, choices=range(1, 4))
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--routing", action="store_true")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--docker-image", help="Existing local image with bash, git and Python")
    parser.add_argument("--auth-file", type=Path, default=Path.home() / ".codex/auth.json")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output must be a new directory; previous evidence is immutable")
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be 1..600 seconds")
    for bundle in (args.baseline, args.candidate):
        if not (bundle / "SKILL.md").is_file():
            parser.error(f"Missing skill entry: {bundle}")
    if args.docker_image:
        if not args.auth_file.is_file() or not shutil.which("rg"):
            parser.error("Docker evaluation requires a local auth file and rg")
        args.docker_image = subprocess.check_output(
            ["docker", "image", "inspect", args.docker_image, "--format", "{{.Id}}"],
            text=True,
        ).strip()
    elif not args.prepare_only:
        subprocess.run(
            ["codex", "sandbox", "-c", 'sandbox_mode="read-only"',
             "--", "/bin/echo", "review eval sandbox preflight"], check=True,
        )
    spec = json.loads(args.cases.read_text())
    cases = spec["routing"] if args.routing else spec["cases"]
    if args.case:
        if set(args.case) - {c["id"] for c in cases}:
            parser.error("Unknown case id")
        cases = [c for c in cases if c["id"] in args.case]
    args.output.mkdir(parents=True)
    # Freeze complete packages before any invocation, including nested resources.
    for arm in ("baseline", "candidate"):
        shutil.copytree(getattr(args, arm), args.output / "bundles" / arm)
    manifest = {
        "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "model": args.model, "routing": args.routing,
        "codex_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "repetitions": args.repetitions, "timeout": args.timeout,
        "isolation": args.docker_image or "codex-read-only",
        "bundle_digests": {a: digest(snapshot(args.output / "bundles" / a))
                           for a in ("baseline", "candidate")},
        "grading": "pending independent semantic/trajectory review; no automatic pass",
        "runs": [],
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    for case in cases:
        files = {**spec["common_files"], **(
            spec["cases"][-1]["files"] if args.routing else case["files"]
        )}
        for repeat in range(args.repetitions):
            # Alternate which arm runs first to reduce warm-cache/time ordering bias.
            arms = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
            for arm in arms:
                run_id = f"{case['id']}-{repeat + 1}-{arm}"
                run_dir = args.output / run_id
                prepare(run_dir / "project", files, args.output / "bundles" / arm)
                prompt = case["prompt"]
                if not args.routing:
                    prompt = "Use $zf-cr at .agents/skills/zf-cr/SKILL.md.\n" + prompt
                (run_dir / "prompt.txt").write_text(prompt)
                record = {"id": run_id, "case": case["id"], "arm": arm, "repeat": repeat + 1}
                if not args.prepare_only:
                    result = run_one(run_dir, prompt, args)
                    record.update({k: result[k] for k in (
                        "returncode", "seconds", "timed_out", "changed_paths",
                        "usage", "turn_completed", "trace_sha256",
                    )})
                manifest["runs"].append(record)
                (args.output / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
                )
                print(json.dumps(record, ensure_ascii=False), flush=True)
                if not args.prepare_only and (
                    result["returncode"] != 0 or result["timed_out"]
                    or result["changed_paths"]
                    or not result["turn_completed"]
                    or (result["commands"] and all(
                        c["exit_code"] != 0 for c in result["commands"]
                    ))
                ):
                    raise SystemExit(f"Execution failed; retained evidence in {run_dir}")


if __name__ == "__main__":
    main()
