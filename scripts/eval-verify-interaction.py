"""Measure two co-loaded Verify methods independently and together on one frozen suite."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json
from pathlib import Path
import runpy
import shutil
import subprocess

from skill_loading_comparison import score_submission
from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.skills.materialize import materialize_role_skills
from zf.integrations.controller.binding import managed_config
from zf.runtime.evolution_skill import counterbalanced_arms
from zf.runtime.skill_briefing import render_skill_briefing_section

ROOT = Path(__file__).resolve().parents[1]
BASE = runpy.run_path(str(ROOT / "scripts/eval-task-verify-loading.py"))
SUITE = ROOT / "tests/fixtures/skill-loading/task-verify-v2.json"
PAIR = ("zf-workflow-adaptation-boundary", "zf-project-adapter-matrix-enrichment")
GROUPS = {"neither": (), "a": PAIR[:1], "b": PAIR[1:], "both": PAIR}
write = BASE["write"]


def prepare(args, suite):
    prompts, inventories = {}, {}
    for arm, added in GROUPS.items():
        project = args.output / "templates" / arm / "project"
        project.mkdir(parents=True)
        enabled = [
            n
            for n in suite["support"] + [suite["skill"]]
            if n not in PAIR or n in added
        ]
        paths = {
            n: next(
                root / n / "SKILL.md"
                for root in (ROOT / "skills", ROOT / "yoke")
                if (root / n / "SKILL.md").is_file()
            )
            for n in enabled
        }
        role = RoleConfig(name="verify", backend="codex", skills=enabled)
        pack = materialize_role_skills(
            project_root=project,
            state_dir=project.parent / "state",
            role=role,
            config=ZfConfig(roles=[role]),
            provider_skill_root=project / ".agents/skills",
            skill_overrides=paths,
        )
        seen = {entry.name for entry in pack.skills if entry.status == "resolved"}
        if seen != set(enabled) or any(e.status != "resolved" for e in pack.skills):
            raise ValueError("unexpected real materialized set or mode")
        prompts[arm] = "\n".join(render_skill_briefing_section(role, list(pack.skills)))
        inventories[arm] = BASE["TRANSPORT"]["snapshot"](project / ".agents/skills")
        subprocess.run(["git", "init", "-q", str(project)], check=True)
    support = [
        {p: v for p, v in items.items() if p.split("/")[0] not in PAIR}
        for items in inventories.values()
    ]
    if not all(s == support[0] for s in support):
        raise ValueError("support method drift")
    return prompts, inventories


def trial(job, *, args, suite, prompts):
    case, rep, arm = job
    row = BASE["run_trial"](job, args=args, suite=suite, prompts=prompts)
    path = args.output / "runs" / f"{case['id']}-{rep}-{arm}"
    row["reads"] = {n: BASE["witnessed_read"](path / "trace.jsonl", n) for n in PAIR}
    row.update(score_submission(row["answer"], case["expected"]))
    write(path / "observation.json", row)
    return row


def summarize(suite, rows):
    result = {}
    for split in ("selection", "test"):
        sample = [r for r in rows if r["split"] == split]
        size = (
            sum(c["split"] == split for c in suite["cases"])
            * suite["policy"]["replicates"]
        )
        keys = {(r["case"], r["repeat"]) for r in sample}
        by_key = {(r["case"], r["repeat"], r["arm"]): r for r in sample}
        if len(by_key) != len(sample):
            raise ValueError("duplicate trial")
        paired = [
            k
            for k in sorted(keys)
            if all(by_key.get((*k, a), {}).get("complete") for a in GROUPS)
        ]
        arms = {}
        for arm in GROUPS:
            group = [by_key[(*k, arm)] for k in paired]
            arms[arm] = {
                "correct": sum(r["correct"] for r in group),
                "false_release": sum(r["false_release"] for r in group),
                "verdict_correct": sum(r["verdict_correct"] for r in group),
                "reads": {n: sum(r["reads"][n] for r in group) for n in PAIR},
                "positive_reads": {
                    n: sum(r["reads"][n] and r["route"] != "forbidden" for r in group)
                    for n in PAIR
                },
                "mean_seconds": round(sum(r["seconds"] for r in group) / len(group), 2)
                if group
                else None,
            }
            for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
                arms[arm][field] = (
                    sum(r["usage"][field] for r in group)
                    if group and all(type(r["usage"].get(field)) is int for r in group)
                    else None
                )
        witnessed = all(
            arms[a]["positive_reads"][n] > 0
            for a, names in GROUPS.items()
            for n in names
        )
        clean = all(
            not arms[a]["reads"][n]
            for a, names in GROUPS.items()
            for n in PAIR
            if n not in names
        )
        eligible = bool(paired) and len(paired) == size and witnessed and clean
        delta = (
            arms["both"]["correct"]
            - arms["a"]["correct"]
            - arms["b"]["correct"]
            + arms["neither"]["correct"]
        )
        result[split] = {
            "expected_pairs": size,
            "matched_pairs": len(paired),
            "arms": arms,
            "activation_confirmed": witnessed,
            "absent_arms_clean": clean,
            "interaction_pp": round(100 * delta / len(paired), 2) if eligible else None,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, choices=(1, 2, 3, 4), default=2)
    parser.add_argument(
        "--auth-file", type=Path, default=Path.home() / ".codex/auth.json"
    )
    args = parser.parse_args()
    if managed_config() is not None or not args.auth_file.is_file():
        parser.error("standalone authenticated runner required")
    args.output.mkdir(parents=True, exist_ok=False)
    args.docker_image = subprocess.check_output(
        ["docker", "image", "inspect", "mcp/playwright:latest", "--format", "{{.Id}}"],
        text=True,
    ).strip()
    args.model, args.reasoning_effort, args.timeout = "gpt-6-luna", "high", 150
    suite = json.loads(SUITE.read_text())
    prompts, inventories = prepare(args, suite)
    manifest = {
        "schema_version": "verify-interaction.v1",
        "methods": PAIR,
        "model": args.model,
        "effort": args.reasoning_effort,
        "image": args.docker_image,
        "client": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "suite_digest": BASE["file_hash"](SUITE),
        "timeout": args.timeout,
        "inventory_digests": {
            a: BASE["TRANSPORT"]["digest"](i) for a, i in inventories.items()
        },
        "rule": "held-out complete quartet, confirmed positive reads in each applicable arm, zero contamination; manual decision",
    }
    write(args.output / "frozen.json", {"suite": suite, "identity": manifest})
    jobs = [
        (case, rep, arm)
        for case in suite["cases"]
        for rep in range(1, suite["policy"]["replicates"] + 1)
        for arm in counterbalanced_arms(tuple(GROUPS), rep)
    ]
    with ThreadPoolExecutor(args.jobs) as pool:
        rows = list(
            pool.map(partial(trial, args=args, suite=suite, prompts=prompts), jobs)
        )
    write(
        args.output / "report.json",
        {**manifest, "splits": summarize(suite, rows), "observations": rows},
    )


if __name__ == "__main__":
    main()
