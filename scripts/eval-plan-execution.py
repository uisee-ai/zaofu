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
    for path in source.rglob("*"):
        if path.is_symlink() or any(p in {".git", ".agents", ".codex", ".claude"}
                                    for p in path.relative_to(source).parts):
            raise ValueError(f"reserved or symlink input: {path.relative_to(source)}")
    shutil.copytree(source, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--candidate-facts", type=Path)
    parser.add_argument("--companion-skills", type=Path,
                        help="Identical missing dependencies for both arms; cannot override git skills")
    parser.add_argument("--model", required=True)
    parser.add_argument("--docker-image", required=True)
    parser.add_argument("--auth-file", type=Path, default=Path.home() / ".codex/auth.json")
    parser.add_argument("--repetitions", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--timeout", type=int, default=600,
                        help="Wall-clock seconds; 0 waits for completion without a deadline")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or args.timeout < 0:
        parser.error("output must be new and timeout must be non-negative")
    args.timeout = args.timeout or None
    prompt = (args.inputs / "prompt.md").read_text()
    if not args.prepare_only and (not args.auth_file.is_file() or not shutil.which("rg")):
        parser.error("real evaluation requires local auth and rg")
    args.docker_image = subprocess.check_output(
        ["docker", "image", "inspect", args.docker_image, "--format", "{{.Id}}"], text=True,
    ).strip()
    helper = runner()
    args.output.mkdir(parents=True)
    manifest = {
        "schema_version": "plan-execution-eval.v1",
        "model": args.model, "reasoning_effort": "provider_default_same_for_both_arms",
        "codex_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "isolation": args.docker_image, "timeout_seconds": args.timeout,
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
    facts = args.candidate_facts.read_bytes() if args.candidate_facts else None
    manifest["candidate_facts_sha256"] = hashlib.sha256(facts).hexdigest() if facts else None
    manifest["companion_digest"] = (
        helper.digest(helper.snapshot(args.companion_skills)) if args.companion_skills else None
    )
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    failed = False
    for repeat in range(args.repetitions):
        for arm in (("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")):
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
                record.update({k: result[k] for k in ("seconds", "returncode", "timed_out", "usage", "turn_completed", "changed_paths", "trace_sha256")})
                failed |= bool(result["returncode"] or result["timed_out"] or not result["turn_completed"] or result["changed_paths"])
            manifest["runs"].append(record)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(record), flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
