"""Evaluate a role's actual Skill briefing with three counterbalanced, read-only Codex arms."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
import json
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.skills.materialize import materialize_role_skills
from zf.runtime.evolution_contracts import stable_digest
from zf.runtime.evolution_skill import counterbalanced_arms
from zf.runtime.evolution_skill_eval import build_skill_treatment_identity, compare_skill_treatment_identities
from zf.runtime.skill_briefing import render_skill_briefing_section

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests/fixtures/skill-loading/task-verify.json"
# Use the same read-only container, TLS trust and native usage capture as the zf-cr evaluator.
TRANSPORT = runpy.run_path(str(ROOT / "scripts/eval-review-skill.py"))
ARMS = ("raw", "current", "candidate")
MODES = dict(zip(ARMS, ("absent", "required", "on_demand")))
PROTOCOL = """You are the read-only Task Verify worker. All task evidence is supplied below.
Use the enabled Skill index as your briefing. Skill files are under .agents/skills/NAME/SKILL.md.
Use shell reads for matching method documents only. Do not run project commands or modify files.
Submit exactly one JSON object with verdict (release, withhold, outside), action (accept,
request_evidence, return_to_planner, no_verification), receipt (the supporting receipt ID
or none for absent/inapplicable evidence). Decide from the frozen contract. Do not repair a package."""


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def prepare(args, suite):
    state = args.output
    inputs = state / "inputs"
    inputs.mkdir(parents=True)
    names = [*suite["support"], suite["skill"]]
    for name in names:
        source = next(
            (
                root / name
                for root in (ROOT / "skills", ROOT / "yoke")
                if (root / name / "SKILL.md").is_file()
            ),
            None,
        )
        if source is None:
            raise ValueError(f"missing enabled skill {name}")
        shutil.copytree(source, inputs / name)
    image = subprocess.check_output(
        ["docker", "image", "inspect", args.docker_image, "--format", "{{.Id}}"], text=True
    ).strip()
    args.docker_image = image
    version = subprocess.check_output(["codex", "--version"], text=True).strip()
    support = {n: TRANSPORT["snapshot"](inputs / n) for n in suite["support"]}
    target = suite["skill"]
    # Only project/ is mounted into the solver's container. References and graders stay on the host.
    prompts, identities, entries_by_arm = {}, {}, {}
    common = {
        k: stable_digest(v)
        for k, v in {
            "runtime_commit_digest": {
                p: file_hash(ROOT / p)
                for p in (
                    "src/zf/core/skills/materialize.py",
                    "src/zf/core/skills/provenance.py",
                    "src/zf/runtime/skill_briefing.py",
                    "scripts/eval-task-verify-loading.py",
                    "scripts/eval-review-skill.py",
                )
            },
            "provider_digest": version,
            "model_digest": [args.model, args.reasoning_effort],
            "support_skill_inventory_digest": support,
            "role_profile_digest": "Task Verify",
            "briefing_digest": PROTOCOL,
            "prompt_digest": [c["prompt"] for c in suite["cases"]],
            "workspace_fixture_digest": [suite["cases"], support],
            "tool_policy_digest": "read supplied method documents",
            "sandbox_policy_digest": image,
            "network_policy_digest": "existing read-only evaluation container, host networking",
            "budget_digest": [args.timeout, suite["policy"]],
            "eval_suite_generation_digest": suite,
        }.items()
    }
    targets = {}
    for arm in ARMS:
        project = state / "templates" / arm / "project"
        project.mkdir(parents=True)
        enabled = list(suite["support"]) if arm == "raw" else names
        role = RoleConfig(
            name="verify",
            backend="codex",
            skills=enabled,
            skill_loading={target: "on_demand"} if arm == "candidate" else {},
        )
        material = materialize_role_skills(
            config=ZfConfig(roles=[role]),
            project_root=project,
            state_dir=project.parent / "state",
            role=role,
            provider_skill_root=project / ".agents/skills",
            skill_overrides={name: inputs / name / "SKILL.md" for name in enabled},
        )
        actual = {entry.name: entry for entry in material.skills}
        if set(actual) != set(enabled) or any(entry.status != "resolved" for entry in actual.values()):
            raise ValueError("materialized inventory differs from the frozen role")
        for name in enabled:
            if TRANSPORT["snapshot"](project / ".agents/skills" / name) != TRANSPORT["snapshot"](
                inputs / name
            ):
                raise ValueError("provider method files differ from frozen inputs")
        if arm != "raw" and (
            actual[target].auto_inject != (arm == "current")
            or actual[target].load_on_demand != (arm == "candidate")
        ):
            raise ValueError("target mode differs from the declared treatment")
        prompts[arm] = "\n".join(render_skill_briefing_section(role, list(actual.values())))
        write(project.parent / "manifest.json", material.to_payload())
        entries_by_arm[arm] = file_hash(project.parent / "manifest.json")
        digest = file_hash(inputs / target / "SKILL.md") if arm != "raw" else ""
        targets[arm] = {
            "name": target,
            "available": arm != "raw",
            "loading_mode": MODES[arm],
            "version": digest,
            "digest": digest,
            "materialized_path_digest": stable_digest(f".agents/skills/{target}") if digest else "",
        }
        identities[arm] = build_skill_treatment_identity(
            arm=arm, target_skill=targets[arm], common_identity=common, evaluation_purpose="natural_lift"
        )
        subprocess.run(["git", "init", "-q", str(project)], check=True)
    comparison = compare_skill_treatment_identities(list(identities.values()))
    if not comparison["comparable"]:
        raise ValueError(comparison["reason"])
    manifest = {
        "skill": target,
        "skill_digest": targets["current"]["digest"],
        "model": args.model,
        "effort": args.reasoning_effort,
        "client": version,
        "image": image,
        "suite_digest": stable_digest(suite),
        "treatments": identities,
        "manifest_digests": entries_by_arm,
        "comparison": comparison,
        "policy": suite["policy"],
    }
    write(state / "manifest.json", manifest)
    write(state / "cases.json", suite)
    return prompts, manifest


def witnessed_read(path, target):
    needle = f".agents/skills/{target}/SKILL.md"
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item", {})
        if (
            event.get("type") != "item.completed"
            or item.get("type") != "command_execution"
            or item.get("exit_code") != 0
            or not isinstance(item.get("aggregated_output"), str)
        ):
            continue
        # Require the actual returned frontmatter as well as a command targeting this file.
        if f"name: {target}" not in item["aggregated_output"].splitlines():
            continue
        command = item.get("command", "")
        try:
            words = shlex.split(command)
        except ValueError:
            continue
        if len(words) >= 3 and words[1] in ("-c", "-lc"):
            command = words[2]
        from zf.runtime.skill_invocation_projection import _tool_reads_target
        import re

        targets = {needle, "./" + needle, "/workspace/" + needle}
        if any(
            _tool_reads_target("bash", {"command": part.strip()}, targets)
            for part in re.split(r"&&|\|\||[;|\n]", command)
        ):
            return True
    return False


def run_trial(job, *, args, suite, prompts):
    case, repeat, arm = job
    trial = args.output / "runs" / f"{case['id']}-{repeat}-{arm}"
    shutil.copytree(args.output / "templates" / arm / "project", trial / "project")
    prompt = PROTOCOL + "\n\n" + prompts[arm] + "\n\nTask:\n" + case["prompt"]
    (trial / "prompt.txt").write_text(prompt)
    result = TRANSPORT["run_one"](trial, prompt, args)
    try:
        final = json.loads(result["answers"][-1].strip())
    except (ValueError, IndexError):
        final = None
    complete = (
        result["turn_completed"]
        and not result["errors"]
        and not result["returncode"]
        and not result["changed_paths"]
        and not result["timed_out"]
    )
    usage = result["usage"]
    row = {
        "case": case["id"],
        "split": case["split"],
        "repeat": repeat,
        "arm": arm,
        "route": case["route"],
        "complete": bool(complete),
        "answer": final,
        "reference": case["expected"],
        "seconds": result["seconds"],
        "read": witnessed_read(trial / "trace.jsonl", suite["skill"]),
        "usage": usage,
        "timed_out": result["timed_out"],
        "trace_digest": result["trace_sha256"],
        "prompt_digest": file_hash(trial / "prompt.txt"),
    }
    write(trial / "observation.json", row)
    print(case["id"], repeat, arm, "completed" if complete else "incomplete", flush=True)
    return row


def main():
    from zf.integrations.controller.binding import managed_config
    from skill_loading_comparison import loading_report

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="high")
    parser.add_argument("--docker-image", default="mcp/playwright:latest")
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--jobs", type=int, choices=[1, 2, 3, 4], default=2)
    parser.add_argument("--auth-file", type=Path, default=Path.home() / ".codex/auth.json")
    args = parser.parse_args()
    if managed_config() is not None or not args.auth_file.is_file() or not 1 <= args.timeout <= 600:
        parser.error(
            "requires a standalone installation, local Codex authentication and a 1–600 second timeout"
        )
    args.output.mkdir(parents=True, exist_ok=False)
    suite = json.loads(SUITE.read_text())
    prompts, manifest = prepare(args, suite)
    jobs = [
        (case, rep, arm)
        for case in suite["cases"]
        for rep in range(1, suite["policy"]["replicates"] + 1)
        for arm in counterbalanced_arms(ARMS, rep)
    ]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows = list(pool.map(partial(run_trial, args=args, suite=suite, prompts=prompts), jobs))
    write(args.output / "report.json", loading_report(manifest, suite, rows))


if __name__ == "__main__":
    main()
