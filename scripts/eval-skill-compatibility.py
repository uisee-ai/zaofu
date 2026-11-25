"""Run NVIDIA's advisory harness checks on the actual isolated role materialization."""

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from zf.core.config.loader import load_config
from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.skills.materialize import materialize_role_skills

ROOT = Path(__file__).resolve().parents[1]


def audit(args, config, role, backend, project_root):
    if not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9_.-]*", role.instance_id):
        raise ValueError("invalid role instance path component")
    destination = args.output / role.instance_id / backend
    destination.mkdir(parents=True)
    material = materialize_role_skills(
        config=config,
        role=replace(role, backend=backend),
        project_root=project_root,
        state_dir=destination / "state",
    )
    if not material.skills:
        return []
    client = {"codex": "codex", "claude-code": "claude"}[backend]
    version = subprocess.check_output([client, "--version"], text=True).strip()
    match = re.search(r"\d+\.\d+\.\d+", version)
    if not match:
        raise ValueError(f"unknown {client} version")
    raw = destination / "harness.json"
    env = dict(os.environ)
    resolved = {s.name for s in material.skills if s.status == "resolved"}
    reports = {}
    if resolved:
        roots = {
            path.parent
            for path in material.materialized_paths_under(project_root).values()
        }
        if len(roots) != 1:
            raise ValueError("expected a single native Skill root")
        root = roots.pop()
        if backend == "codex":
            env["CODEX_HOME"] = str(root.parent)
        with (destination / "check.log").open("w") as out:
            subprocess.run(
                [
                    args.evaluator,
                    "harness-check",
                    str(root),
                    "--harness",
                    backend,
                    "--harness-version",
                    f"{backend}={match[0]}",
                    "--json",
                    str(raw),
                ],
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                check=True,
            )
        reports = {
            Path(p).name: rows[0]
            for p, rows in json.loads(raw.read_text())["harness_compatibility"].items()
        }
    return [
        {
            "role": role.name,
            "name": s.name,
            "digest": s.sha256,
            "client": version,
            "backend": backend,
            "materialization": s.status,
            "static_status": reports.get(s.name, {}).get("status", "unmeasured"),
            "verified_version": reports.get(s.name, {}).get("verified_version"),
            "rules": [
                f.get("rule_id", f.get("id"))
                for f in reports.get(s.name, {}).get("findings", [])
            ],
            "runtime_read": "unmeasured",
        }
        for s in material.skills
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--changed-skill",
        action="append",
        default=[],
        help="Also check a changed method outside the pilot roles",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Project zf.yaml; by default examine the frozen pilot roles",
    )
    parser.add_argument(
        "--role", action="append", help="Configured role name or instance"
    )
    parser.add_argument("--evaluator", default="skillevaluator")
    args = parser.parse_args()
    args.output = (
        args.output.resolve()
        if args.output
        else Path(tempfile.mkdtemp(prefix="zf-harness-check-")) / "results"
    )
    args.output.mkdir(parents=True, exist_ok=False)
    if args.config:
        config = load_config(args.config)
        project = args.config.resolve().parent
        roles = config.roles
    else:
        root = ROOT / "tests/fixtures/skill-loading"
        v = json.loads((root / "task-verify-v2.json").read_text())
        roles = [
            RoleConfig(
                name="planner",
                skills=json.loads((root / "planner-manifest.json").read_text())[
                    "skills"
                ],
            ),
            RoleConfig(name="verify", skills=v["support"] + [v["skill"]]),
        ]
        config, project = ZfConfig(roles=roles), ROOT
    covered = {name for r in roles for name in r.skills}
    for n, name in enumerate(sorted(set(args.changed_skill) - covered)):
        if not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9_.-]*", name):
            parser.error("invalid changed Skill name")
        roles = [*roles, RoleConfig(name=f"changed-{n}", skills=[name])]
    actual = [
        r
        for r in roles
        if not args.role or {r.name, r.instance_id}.intersection(args.role)
    ]
    if not actual:
        parser.error("no matching roles")
    rows = []
    for r in actual:
        for backend in ("codex", "claude-code"):
            rows.extend(audit(args, config, r, backend, project))
    version = subprocess.check_output([args.evaluator, "--version"], text=True).strip()
    (args.output / "report.json").write_text(
        json.dumps({"tool": version, "rows": rows}, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"{len(rows)} static predictions: {args.output / 'report.json'}")
    for row in rows:
        if row["static_status"] != "loads":
            print(
                row["role"],
                row["backend"],
                row["name"],
                row["static_status"],
                ", ".join(row["rules"]),
            )


if __name__ == "__main__":
    main()
