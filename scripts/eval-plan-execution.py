#!/usr/bin/env python3
"""Paired real-provider Plan review; preserves evidence, never grants approval.

Inputs: a directory containing prompt.md and project/. The provider sees only
the project, a frozen skill tree and optional candidate execution facts.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def runner():
    spec = importlib.util.spec_from_file_location("review_eval", ROOT / "scripts/eval-review-skill.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def freeze_skills(ref: str, destination: Path) -> str:
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", ref + "^{commit}"], cwd=ROOT, text=True,
    ).strip()
    for prefix in ("yoke", "skills"):
        names = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", commit, "--", prefix + "/"], cwd=ROOT, text=True,
        ).splitlines()
        if prefix == "skills" and not names:
            raise ValueError("commit contains no skills")
        for name in names:
            path = destination / Path(name).relative_to(prefix)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT))
    return commit


def copy_inputs(source: Path, target: Path) -> None:
    # Inputs must not inject a second instruction tree or follow host symlinks.
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"input must be a real directory, not a symlink: {source}")
    for path in source.rglob("*"):
        if path.is_symlink() or any(p in {".git", ".agents", ".codex", ".claude"}
                                    for p in path.relative_to(source).parts):
            raise ValueError(f"reserved or symlink input: {path.relative_to(source)}")
    shutil.copytree(source, target)


def overlay_variant(source: Path, target: Path) -> dict[str, str]:
    """Replace explicitly supplied method text in an isolated candidate copy."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError("variant must be a real directory")
    files = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_symlink() or any(p.startswith(".") for p in relative.parts):
            raise ValueError(f"unsafe variant path: {relative}")
        if path.is_file():
            if path.suffix not in {".md", ".json"} or not (target / relative).is_file():
                raise ValueError(f"variant must replace existing method text: {relative}")
            files.append((path, relative))
    if not files:
        raise ValueError("variant contains no method replacements")
    changes = {}
    for path, relative in files:
        body = path.read_bytes()
        (target / relative).write_bytes(body)
        changes[relative.as_posix()] = hashlib.sha256(body).hexdigest()
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--variant", type=Path,
                        help="Add an arm with explicit method-text replacements over candidate")
    parser.add_argument("--include-bare", action="store_true",
                        help="Add a no-method arm; container permissions and case stay identical")
    parser.add_argument("--max-provider-calls", type=int, default=12)
    parser.add_argument("--candidate-facts", type=Path)
    parser.add_argument("--companion-skills", type=Path,
                        help="Identical missing dependencies for both arms; cannot override git skills")
    parser.add_argument("--model", required=True)
    parser.add_argument("--docker-image", help="Existing local image; required for real runs")
    parser.add_argument("--auth-file", type=Path, default=Path.home() / ".codex/auth.json")
    parser.add_argument("--repetitions", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--timeout", type=int, default=600,
                        help="Wall-clock seconds; 0 waits for completion without a deadline")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or args.timeout < 0:
        parser.error("output must be new and timeout must be non-negative")
    args.timeout = args.timeout or None
    arms = ["baseline", "candidate"]
    if args.variant:
        arms.append("variant")
    if args.include_bare:
        arms.append("bare")
    if not 1 <= args.max_provider_calls <= 12 or len(arms) * args.repetitions > args.max_provider_calls:
        parser.error("requested runs exceed max-provider-calls (1..12)")
    if args.candidate_facts and (args.variant or args.include_bare):
        parser.error("method-contribution arms require identical facts; omit candidate-facts")
    if (args.inputs / "prompt.md").is_symlink():
        parser.error("prompt must not be a symlink")
    prompt = (args.inputs / "prompt.md").read_text()
    if not args.prepare_only:
        if not args.docker_image or not args.auth_file.is_file() or not shutil.which("rg"):
            parser.error("real evaluation requires a Docker image, local auth and rg")
        args.docker_image = subprocess.check_output(
            ["docker", "image", "inspect", args.docker_image, "--format", "{{.Id}}"], text=True,
        ).strip()
    helper = runner()
    args.output.mkdir(parents=True)
    manifest = {
        "schema_version": "plan-execution-eval.v1",
        "model": args.model, "reasoning_effort": "provider_default_same_for_both_arms",
        "codex_version": (None if args.prepare_only else subprocess.check_output(
            ["codex", "--version"], text=True).strip()),
        "prepared_only": args.prepare_only,
        "isolation": None if args.prepare_only else args.docker_image,
        "requested_image": args.docker_image, "timeout_seconds": args.timeout,
        "max_provider_calls": args.max_provider_calls, "arms": arms,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "input_digest": helper.digest(helper.snapshot(args.inputs)),
        "repetitions": args.repetitions, "commits": {}, "bundle_digests": {}, "runs": [],
        "limitations": ["Semantic review is manual; no automatic improvement verdict.",
                        "Review experiment is not workflow dispatch or product build.",
                        "Same model and inputs do not guarantee identical sampling or cache state."],
    }
    for arm in ("baseline", "candidate"):
        bundle = args.output / "bundles" / arm
        manifest["commits"][arm] = freeze_skills(getattr(args, arm), bundle)
        if args.companion_skills:
            for companion in sorted(args.companion_skills.iterdir()):
                if not companion.is_dir() or companion.is_symlink():
                    raise ValueError("companion skills must be real directories")
                if (bundle / companion.name).exists():
                    raise ValueError(f"companion overrides versioned skill: {companion.name}")
                copy_inputs(companion, bundle / companion.name)
        manifest["bundle_digests"][arm] = helper.digest(helper.snapshot(bundle))
    if args.variant:
        bundle = args.output / "bundles/variant"
        shutil.copytree(args.output / "bundles/candidate", bundle)
        manifest["variant_replacements"] = overlay_variant(args.variant, bundle)
        manifest["bundle_digests"]["variant"] = helper.digest(helper.snapshot(bundle))
    if args.include_bare:
        (args.output / "bundles/bare").mkdir()
        manifest["bundle_digests"]["bare"] = helper.digest({})
    facts = args.candidate_facts.read_bytes() if args.candidate_facts else None
    manifest["candidate_facts_sha256"] = hashlib.sha256(facts).hexdigest() if facts else None
    manifest["companion_digest"] = (
        helper.digest(helper.snapshot(args.companion_skills)) if args.companion_skills else None
    )
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    failed = False
    for repeat in range(args.repetitions):
        for arm in (arms if repeat % 2 == 0 else list(reversed(arms))):
            run_dir = args.output / f"{repeat + 1}-{arm}"
            project = run_dir / "project"
            copy_inputs(args.inputs / "project", project)
            shutil.copytree(args.output / "bundles" / arm, project / ".agents/skills")
            if arm == "candidate" and facts:
                (project / "execution-review.json").write_bytes(facts)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            (run_dir / "prompt.txt").write_text(prompt)
            record = {"id": run_dir.name, "arm": arm, "repeat": repeat + 1}
            if not args.prepare_only:
                result = helper.run_one(run_dir, prompt, args)
                record.update({k: result[k] for k in ("seconds", "returncode", "timed_out", "usage", "turn_completed", "changed_paths", "trace_sha256", "errors")})
                failed |= bool(result["returncode"] or result["timed_out"] or not result["turn_completed"] or result["changed_paths"] or result["errors"])
            manifest["runs"].append(record)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(record), flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
