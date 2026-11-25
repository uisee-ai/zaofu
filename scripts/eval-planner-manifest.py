"""Paired Planner manifest experiment against frozen, isolated artifact bodies."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import shlex
from statistics import median
import subprocess

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.skills.materialize import materialize_role_skills
from zf.integrations.controller.binding import managed_config
from zf.runtime.evolution_skill import counterbalanced_arms
from zf.runtime.skill_briefing import render_skill_briefing_section
from zf.runtime.workflow_inputs import render_workflow_input_briefing_section

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests/fixtures/skill-loading/planner-manifest.json"
TRANSPORT = runpy.run_path(str(ROOT / "scripts/eval-review-skill.py"))
ARMS = ("full", "index")
POLICY = """Act as the Planner for a single frozen handoff. The Skill index and output contract are fixed.
Read each required JSON body in full with cat. File bytes returned by tools are the read evidence.
The catalog is navigation; listing it never satisfies a required read. Start with inputs/contract.json
and inputs/scan.json. If the contract names a prerequisite kind, look up its file in the manifest
and read that prerequisite. Missing prerequisites block handoff. Never use earlier revisions.
Return exactly a JSON object with decision (ready or blocked), task, acceptance, verify and dependency.
Use the current IDs from the artifacts. Use null for missing dependency. No writes or project commands."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n")


def fixture(project, case):
    inputs = project / "inputs"
    inputs.mkdir(parents=True)
    lookup = case.get("lookup") or case.get("missing")
    contract = {k: case[k] for k in ("task", "acceptance", "verify")}
    contract["prerequisite_kind"] = "release-dependency" if lookup else None
    scan = {
        "superseded_dependency": "task-00",
        "current_dependency": None if lookup else case["dependency"],
    }
    sources = {"inputs/contract.json": contract, "inputs/scan.json": scan}
    extra = "inputs/artifacts/" + sha(case["id"].encode())[:12] + ".json"
    if lookup and not case.get("missing"):
        sources[extra] = {
            "current_dependency": case["dependency"],
            "status": "attested",
        }
    for name, body in sources.items():
        write(project / name, body)
    entries = []
    for index in range(31):
        path = f"inputs/archive/{index:02}.json"
        entry = {"kind": "archived-scan", "ref": path, "revision": index + 1}
        if index == 19 and lookup:
            entry.update(kind="release-dependency", ref=extra)
        elif index < 2:
            entry.update(kind=("contract", "scan")[index], ref=list(sources)[index])
        else:
            write(
                project / path,
                {"status": "superseded", "component": index, "task": case["task"]},
            )
        # Synthetic portable provenance of prior verification obligations. No project data is copied.
        entry["evidence_refs"] = [
            {
                "obligation": f"prior-{index}-{n}",
                "receipt_digest": sha(f"{case['id']}/{index}/{n}".encode()),
            }
            for n in range(9)
        ]
        entries.append(entry)
    manifest = {
        "workflow_run_id": "isolated-" + case["id"],
        "workflow_input_manifest_ref": "inputs/manifest.json",
        "artifact_refs": entries,
        "request_revision": 1,
    }
    write(inputs / "manifest.json", manifest)
    digest = sha((inputs / "manifest.json").read_bytes())
    sections = {
        "full": render_workflow_input_briefing_section(manifest),
        "index": f"## Workflow Input Manifest\n31 entries · frozen at inputs/manifest.json · sha256 {digest}.\nLook up additional refs there when needed.",
    }
    return sources, sections, digest


def cat_paths(command):
    """Return file arguments from literal cat invocations (including chained reads)."""
    try:
        outer = shlex.split(command)
        if len(outer) == 3 and outer[1] in ("-lc", "-c"):
            command = outer[2]
        parts = [shlex.split(piece) for piece in command.split("&&")]
    except ValueError:
        return set()
    if not all(p and p[0] == "cat" for p in parts):
        return set()
    return {
        name.removeprefix("/workspace/").removeprefix("./")
        for p in parts
        for name in p[1:]
    }


def observed_bodies(trace, project, sources, missing_ref=None):
    expected = {name: (project / name).read_text().strip() for name in sources}
    seen = set()
    for line in trace.read_text().splitlines():
        event = json.loads(line)
        item = event.get("item", {})
        paths = cat_paths(item.get("command", ""))
        if (
            missing_ref in paths
            and event.get("type") == "item.completed"
            and item.get("type") == "command_execution"
            and missing_ref in (item.get("aggregated_output") or "")
            and "No such file" in (item.get("aggregated_output") or "")
            and item.get("exit_code") != 0
        ):
            seen.add(missing_ref)
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "command_execution"
            and item.get("exit_code") == 0
        ):
            returned = item.get("aggregated_output") or ""
            seen.update(
                name
                for name, body in expected.items()
                if name in paths and body in returned
            )
    return sorted(seen)


def score(case, answer):
    blocked = bool(case.get("missing"))
    expected = {key: case[key] for key in ("task", "acceptance", "verify")}
    expected.update(
        decision="blocked" if blocked else "ready",
        dependency=None if blocked else case["dependency"],
    )
    return answer == expected, bool(
        blocked and isinstance(answer, dict) and answer.get("decision") == "ready"
    )


def trial(job, *, args, suite, common):
    case, rep, arm = job
    directory = args.output / "runs" / f"{case['id']}-{rep}-{arm}"
    project = directory / "project"
    shutil.copytree(args.output / "template", project)
    sources, sections, digest = fixture(project, case)
    prompt = common + "\n" + sections[arm]
    (directory / "prompt.txt").write_text(prompt)
    result = TRANSPORT["run_one"](directory, prompt, args)
    try:
        answer = json.loads(result["answers"][-1])
    except (IndexError, ValueError):
        answer = None
    correct, false_ready = score(case, answer)
    missing_ref = (
        "inputs/artifacts/" + sha(case["id"].encode())[:12] + ".json"
        if case.get("missing")
        else None
    )
    required = set(sources)
    if missing_ref:
        required.add(missing_ref)
    seen = observed_bodies(directory / "trace.jsonl", project, sources, missing_ref)
    row = {
        "case": case["id"],
        "split": case["split"],
        "repeat": rep,
        "arm": arm,
        "answer": answer,
        "complete": bool(
            result["turn_completed"]
            and not result["errors"]
            and not result["returncode"]
            and not result["timed_out"]
            and not result["changed_paths"]
        ),
        "handoff_correct": correct,
        "false_ready": false_ready,
        "required": sorted(required),
        "observed": seen,
        "missing_reads": sorted(required - set(seen)),
        "manifest_digest": digest,
        "workspace_digest": result["before_digest"],
        "prompt_digest": sha(prompt.encode()),
        "trace_digest": result["trace_sha256"],
        "section_bytes": len(sections[arm].encode()),
        "usage": result["usage"],
        "seconds": result["seconds"],
    }
    write(directory / "observation.json", row)
    print(case["id"], rep, arm, row["complete"], flush=True)
    return row


def report(suite, registry, rows):
    policy = suite["policy"]
    eligible = True
    splits = {}
    for split in ("selection", "test"):
        cases = [c["id"] for c in suite["cases"] if c["split"] == split]
        keys = [(c, rep) for c in cases for rep in range(1, policy["repeats"] + 1)]
        by_key = {
            (r["case"], r["repeat"], r["arm"]): r for r in rows if r["split"] == split
        }
        if len(by_key) != sum(r["split"] == split for r in rows):
            raise ValueError("duplicate trial")
        paired = []
        for key in keys:
            pair = [by_key.get((*key, arm)) for arm in ARMS]
            if all(r and r["complete"] for r in pair):
                if (
                    pair[0]["workspace_digest"] != pair[1]["workspace_digest"]
                    or pair[0]["manifest_digest"] != pair[1]["manifest_digest"]
                ):
                    raise ValueError("paired fixtures differ")
                paired.append(key)
        arms = {}
        for arm in ARMS:
            sample = [by_key[(*key, arm)] for key in paired]
            arms[arm] = {
                "handoff_correct": sum(r["handoff_correct"] for r in sample),
                "false_ready": sum(r["false_ready"] for r in sample),
                "missing_reads": sum(len(r["missing_reads"]) for r in sample),
                "observed_reads": sum(len(r["observed"]) for r in sample),
                "required_reads": sum(len(r["required"]) for r in sample),
                "median_seconds": median(r["seconds"] for r in sample)
                if sample
                else None,
            }
            for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
                arms[arm][field] = (
                    sum(r["usage"][field] for r in sample)
                    if sample
                    and all(type(r["usage"].get(field)) is int for r in sample)
                    else None
                )
        a, b = arms["full"], arms["index"]
        savings = (
            100 * (a["input_tokens"] - b["input_tokens"]) / a["input_tokens"]
            if a["input_tokens"] and b["input_tokens"] is not None
            else None
        )
        ok = (
            len(paired) == len(keys)
            and bool(keys)
            and savings is not None
            and savings >= policy["minimum_input_saving_percent"]
            and all(
                len(keys) - r["handoff_correct"] <= policy["max_handoff_errors"]
                and r["false_ready"] <= policy["max_false_ready"]
                and r["missing_reads"] <= policy["max_missing_reads"]
                for r in arms.values()
            )
        )
        eligible = eligible and ok
        splits[split] = {
            "expected_pairs": len(keys),
            "matched_pairs": len(paired),
            "arms": arms,
            "input_saving_percent": round(savings, 2) if savings is not None else None,
            "qualified": ok,
        }
    return {
        **registry,
        "policy": policy,
        "splits": splits,
        "decision": "bounded_workflow_trial" if eligible else "retain_full",
        "observations": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, choices=(1, 2, 3, 4), default=2)
    parser.add_argument(
        "--auth-file", type=Path, default=Path.home() / ".codex/auth.json"
    )
    parser.add_argument("--docker-image", default="mcp/playwright:latest")
    args = parser.parse_args()
    if managed_config() is not None or not args.auth_file.is_file():
        parser.error("standalone authenticated runner required")
    args.output.mkdir(parents=True, exist_ok=False)
    suite = json.loads(SUITE.read_text())
    args.model, args.reasoning_effort, args.timeout = (
        suite["model"],
        suite["effort"],
        150,
    )
    args.docker_image = subprocess.check_output(
        ["docker", "image", "inspect", args.docker_image, "--format", "{{.Id}}"],
        text=True,
    ).strip()
    template = args.output / "template"
    template.mkdir()
    subprocess.run(["git", "init", "-q", str(template)], check=True)
    role = RoleConfig(name="planner", backend="codex", skills=suite["skills"])
    methods = {
        name: next(
            root / name / "SKILL.md"
            for root in (ROOT / "skills", ROOT / "yoke")
            if (root / name / "SKILL.md").exists()
        )
        for name in role.skills
    }
    material = materialize_role_skills(
        config=ZfConfig(roles=[role]),
        role=role,
        project_root=template,
        state_dir=args.output / "state",
        provider_skill_root=template / ".agents/skills",
        skill_overrides=methods,
    )
    if any(e.status != "resolved" for e in material.skills) or not set(
        role.skills
    ) <= set(e.name for e in material.skills):
        raise ValueError("incomplete planner method pack")
    common = (
        POLICY
        + "\n"
        + "\n".join(render_skill_briefing_section(role, list(material.skills)))
    )
    registry = {
        "schema_version": "planner-manifest-comparison.v1",
        "client": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "model": args.model,
        "effort": args.reasoning_effort,
        "image": args.docker_image,
        "common_prompt_digest": sha(common.encode()),
        "suite_digest": sha(SUITE.read_bytes()),
        "skills_digest": TRANSPORT["digest"](
            TRANSPORT["snapshot"](template / ".agents/skills")
        ),
        "harness_digest": sha(Path(__file__).read_bytes()),
        "timeout": args.timeout,
    }
    write(args.output / "frozen.json", {"suite": suite, "identity": registry})
    jobs = [
        (case, rep, arm)
        for case in suite["cases"]
        for rep in range(1, suite["policy"]["repeats"] + 1)
        for arm in counterbalanced_arms(ARMS, rep)
    ]
    with ThreadPoolExecutor(args.jobs) as pool:
        rows = list(
            pool.map(partial(trial, args=args, suite=suite, common=common), jobs)
        )
    write(args.output / "report.json", report(suite, registry, rows))


if __name__ == "__main__":
    main()
