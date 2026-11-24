"""Measure visibility of a long Planner Skill and bounded chapter handoffs in disposable Codex sessions."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
import json
from pathlib import Path
import re
import runpy
import secrets
import shlex
import shutil
import subprocess

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.skills.materialize import materialize_role_skills
from zf.integrations.controller.binding import managed_config
from zf.runtime.evolution_skill import counterbalanced_arms

ROOT = Path(__file__).resolve().parents[1]
TRANSPORT = runpy.run_path(str(ROOT / "scripts/eval-review-skill.py"))
NAME = "zf-plan-task-map-contract"
PATH = Path(".agents/skills") / NAME / "SKILL.md"
SOURCE_SHA = "fdeea143d4fd40ef768d67cdc29b1e806ee7b80a2bc3f0f58c87cd260973fc5d"
# Line ranges refer to this exact frozen source. Chapter files are siblings of the
# entry so that all original references and scripts resolve identically.
CHAPTERS = {
    "ports-and-supplements": 164,
    "incremental-scope": 268,
    "source-and-result": 490,
    "task-map-shape": 573,
    "assembly-and-recovery": 869,
    "decision-map": 993,
    "acceptance-and-review": 1096,
}
MARKS = {"opening": 29, "provenance": 494, "review": 1160}
CASES = {
    "visibility": (
        "Report all three receipts in the order opening, provenance, review. "
        "Use null for any missing value. Return a JSON array only.",
        None,
    ),
    "source": (
        "A release task has a plural list of sources. It has no primary task anchors, "
        "and its source index maps only a sibling task. Decide whether to hand it off. "
        "Return a JSON object with decision (ready or revise), action "
        "(retain_plural or bind_task_source), and the provenance receipt from the Skill.",
        {"decision": "revise", "action": "bind_task_source"},
    ),
    "review": (
        "A complete plan has unique, version-bound source IDs per AC. Compiled "
        "plan_review.mode=none. Its handoff says that no independent Critic was scheduled. "
        "Decide whether that handoff accurately describes the review. Return a JSON object "
        "with decision (ready or revise), action (retain_disclosure or claim_approval), "
        "and the review receipt from the Skill.",
        {"decision": "ready", "action": "retain_disclosure"},
    ),
}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def instrument(body, codes, chapters, layout="references"):
    lines = body.splitlines(keepends=True)
    for key, n in MARKS.items():
        lines[n - 1] += f"\nBenchmark {key} receipt = {codes[key]}\n"
    if not chapters:
        return {"SKILL.md": "".join(lines)}
    starts = list(CHAPTERS.values()) + [len(lines) + 1]
    files = {
        f"{name}.md": "".join(lines[start - 1 : end - 1])
        for name, start, end in zip(CHAPTERS, starts, starts[1:])
    }
    if layout == "references":
        files = {
            "references/" + name: content.replace("](references/", "](").replace(
                "](../zf-", "](../../zf-"
            )
            for name, content in files.items()
        }
    index = ["\n## Contract chapters\n\n"]
    for file, content in files.items():
        headings = [
            line.lstrip("# ")
            for line in content.splitlines()
            if line.startswith(("## ", "### "))
        ]
        index.append(f"- [{'; '.join(headings)}]({file})\n")
    files["SKILL.md"] = "".join(lines[: starts[0] - 1] + index)
    return files


def verified_commands(trace, project):
    found = []
    for event in map(json.loads, trace.read_text().splitlines()):
        item = event.get("item", {})
        if (
            event.get("type") != "item.completed"
            or item.get("type") != "command_execution"
        ):
            continue
        cmd = shlex.split(item["command"])
        if len(cmd) == 3 and cmd[1] in ("-lc", "-c"):
            cmd = shlex.split(cmd[2])
        allowed = (len(cmd) == 2 and cmd[0] == "cat") or (
            len(cmd) == 4
            and cmd[:2] == ["sed", "-n"]
            and re.fullmatch(r"\d+(,\d+)?p", cmd[2])
        )
        path = Path(cmd[-1].removeprefix("/workspace/")) if cmd else None
        if (
            not allowed
            or path.parent not in (PATH.parent, PATH.parent / "references")
            or not (project / path).is_file()
            or item.get("exit_code") != 0
        ):
            found.append((None, "", cmd))
        else:
            found.append((str(path), item.get("aggregated_output", ""), cmd))
    return found


def trial(job, args, codes):
    case, rep, arm = job
    values = codes[f"{case}-{rep}"]
    directory = args.output / f"{case}-{rep}-{arm}"
    project = directory / "project"
    shutil.copytree(args.output / "template", project)
    entry = project / PATH
    files = instrument(
        entry.read_text(),
        values,
        case != "visibility" and arm == "sections",
        args.layout,
    )
    for path, body in files.items():
        (entry.parent / path).write_text(body)
    prompt = f"Read the Planner method at {PATH}. Work read-only. "
    if case == "visibility" and arm == "whole":
        prompt += f"Make exactly one exec_command: cat {PATH}. Use the tool's default output limit. "
    elif case == "visibility":
        # Ranges cover neighborhoods, so the two diagnostic arms differ only by the read operation.
        prompt += (
            "Run three separate exec_command calls with default output limits, using "
            + ", ".join(
                f"sed -n '{a},{b}p' {PATH}"
                for a, b in [(22, 40), (487, 515), (1147, 1180)]
            )
            + ". "
        )
    else:
        prompt += (
            "Begin with a single literal cat of the entry using the tool's default output limit. "
            "Follow up as needed with individual cat or sed -n line-range commands using "
            "literal paths. Use the JSON key receipt for the requested receipt. "
        )
    prompt += CASES[case][0]
    if case == "visibility" and args.layout == "references":
        prompt += " Use three scalar strings or nulls as array elements."
    (directory / "prompt.txt").write_text(prompt)
    TRANSPORT["run_one"](directory, prompt, args)
    print(case, rep, arm, flush=True)


def publish(root):
    registry = json.loads((root / "registry.json").read_text())
    observations = []
    for pair, codes in registry["receipts"].items():
        case, rep = pair.rsplit("-", 1)
        for arm in ("whole", "sections"):
            d = root / f"{pair}-{arm}"
            result = json.loads((d / "result.json").read_text())
            trace = d / "trace.jsonl"
            if hashlib.sha256(trace.read_bytes()).hexdigest() != result["trace_sha256"]:
                raise ValueError("trace digest mismatch")
            data = TRANSPORT["collect_trace"](trace)
            project = d / "project"
            template = root / "template"
            expected_files = TRANSPORT["snapshot"](template)
            body = (template / PATH).read_bytes()
            if hashlib.sha256(body).hexdigest() != registry["source_sha256"]:
                raise ValueError("source digest mismatch")
            parts = instrument(
                body.decode(),
                codes,
                case != "visibility" and arm == "sections",
                registry.get("layout", "siblings"),
            )
            for name, part in parts.items():
                expected_files[str(PATH.parent / name)] = hashlib.sha256(
                    part.encode()
                ).hexdigest()
            if (
                TRANSPORT["snapshot"](project) != expected_files
                or TRANSPORT["digest"](expected_files) != result["before_digest"]
            ):
                raise ValueError("fixture drift")
            try:
                answer = json.loads(data["answers"][-1])
            except (ValueError, IndexError):
                answer = None
            cmds = verified_commands(trace, project)
            paths = [p for p, _, _ in cmds]
            schema_exact = None
            if case != "visibility":
                required = ["provenance" if case == "source" else "review"]
                expected = CASES[case][1]
                decision = (
                    isinstance(answer, dict)
                    and {k: answer.get(k) for k in expected} == expected
                )
                recalled = (
                    required
                    if isinstance(answer, dict)
                    and answer.get("receipt") == codes[required[0]]
                    and set(answer) == {*expected, "receipt"}
                    else []
                )
            else:
                required = list(MARKS)
                decision = None
                schema_exact = (
                    isinstance(answer, list)
                    and len(answer) == 3
                    and all(isinstance(x, str) or x is None for x in answer)
                )
                recalled = [
                    k
                    for i, k in enumerate(required)
                    if isinstance(answer, list)
                    and len(answer) == 3
                    and (answer[i] == codes[k] or answer[i] == {k: codes[k]})
                ]
            witnessed = [
                k
                for k in required
                if any(
                    f"Benchmark {k} receipt = {codes[k]}" in body for _, body, _ in cmds
                )
            ]
            valid = bool(
                cmds
                and all(paths)
                and paths[0] == str(PATH)
                and data["turn_completed"]
                and not data["errors"]
                and not result["returncode"]
                and not result["changed_paths"]
                and not result["timed_out"]
            )
            if case == "visibility":
                expected_cmds = (
                    [["cat"]]
                    if arm == "whole"
                    else [
                        ["sed", "-n", s] for s in ("22,40p", "487,515p", "1147,1180p")
                    ]
                )
                valid = (
                    valid
                    and [cmd[:-1] for _, _, cmd in cmds] == expected_cmds
                    and all(p == str(PATH) for p in paths)
                )
            elif cmds:
                valid = valid and cmds[0][2][0] == "cat"
            observations.append(
                dict(
                    case=case,
                    repeat=int(rep),
                    arm=arm,
                    valid=valid,
                    witnessed=witnessed,
                    recalled=recalled,
                    handoff_correct=decision,
                    required=required,
                    commands=len(cmds),
                    verified_return_bytes=[
                        len(body.encode()) if path else None for path, body, _ in cmds
                    ],
                    prompt_sha256=hashlib.sha256(
                        (d / "prompt.txt").read_bytes()
                    ).hexdigest(),
                    usage=data["usage"],
                    schema_exact=schema_exact,
                    trace_sha256=result["trace_sha256"],
                )
            )
    return {k: v for k, v in registry.items() if k != "receipts"} | {
        "observations": observations
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument(
        "--layout", choices=("siblings", "references"), default="references"
    )
    parser.add_argument("--jobs", type=int, choices=range(1, 5), default=3)
    args = parser.parse_args()
    if args.replay:
        print(json.dumps(publish(args.output), ensure_ascii=False, indent=2))
        return
    if managed_config() is not None:
        parser.error("standalone runner required")
    args.model, args.reasoning_effort, args.timeout = "gpt-6-luna", "high", 150
    args.auth_file = Path.home() / ".codex/auth.json"
    source = ROOT / "skills" / NAME / "SKILL.md"
    if (
        not args.auth_file.is_file()
        or hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA
    ):
        parser.error("frozen source or runner credential unavailable")
    args.docker_image = subprocess.check_output(
        ["docker", "image", "inspect", "mcp/playwright:latest", "--format", "{{.Id}}"],
        text=True,
    ).strip()
    args.output.mkdir(parents=True, exist_ok=False)
    project = args.output / "template"
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    role = RoleConfig(name="planner", backend="codex", skills=[NAME])
    materialize_role_skills(
        config=ZfConfig(roles=[role]),
        role=role,
        project_root=project,
        state_dir=args.output / "state",
        provider_skill_root=project / ".agents/skills",
        skill_overrides={NAME: source},
    )
    if (project / PATH).read_bytes() != source.read_bytes():
        raise ValueError("materialization mismatch")
    codes = {
        f"{c}-{i}": {k: secrets.token_hex(8) for k in MARKS}
        for c in CASES
        for i in (1, 2)
    }
    registry = dict(
        layout=args.layout,
        source_sha256=SOURCE_SHA,
        model=args.model,
        client=subprocess.check_output(["codex", "--version"], text=True).strip(),
        effort=args.reasoning_effort,
        image=args.docker_image,
        receipts=codes,
        scope="Single-Skill visibility probe and two bounded handoff decisions; eligibility for a wider pilot only.",
        gate="All four candidate handoffs correct with witnessed and recalled receipts; lower total input on both cases.",
    )
    save(args.output / "registry.json", registry)
    jobs = [
        (c, i, a)
        for c in CASES
        for i in (1, 2)
        for a in counterbalanced_arms(("whole", "sections"), i)
    ]
    with ThreadPoolExecutor(args.jobs) as pool:
        list(pool.map(partial(trial, args=args, codes=codes), jobs))
    save(args.output / "report.json", publish(args.output))


if __name__ == "__main__":
    main()
