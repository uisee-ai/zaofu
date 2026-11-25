"""Join historical observations to the selected project roles and the current repository Skill bodies."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import runpy
import sys
from tempfile import TemporaryDirectory

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.skills import materialize_role_skills

ROOT = Path(__file__).resolve().parents[1]
family = runpy.run_path(str(ROOT / "scripts/audit-workflow-skill-history.py"))["family"]
COLUMNS = (
    "cohort role skill current_mode current_source current_sha256 current_static_score "
    "historic_entry_match resolved_manifest required_manifest dependency_manifest brief_declared "
    "brief_indexed brief_required read_sessions read_attempts_succeeded read_attempts_failed "
    "read_attempts_superseded read_attempts_other historic_read_environments witnessed_output_profiles terminal_binding_counts"
).split()


def project_policy(config_path):
    """Compile the chosen roles, then resolve their current methods in a disposable directory."""
    if config_path.suffix in (".json", ".snapshot"):
        snapshot = json.loads(config_path.read_text())
        if snapshot["schema"] != "skill-role-config.v1":
            raise ValueError("unsupported role snapshot")
        roles = [RoleConfig(**fields) for fields in snapshot["roles"]]
    else:
        roles = load_config(config_path).roles
    policy, checked = {}, set()
    with TemporaryDirectory(prefix="zf-skill-config-") as tmp:
        for role in roles:
            group = family(role.instance_id)
            key = (
                group,
                role.backend,
                tuple(role.skills),
                tuple(sorted(role.skill_loading.items())),
            )
            if key in checked or not role.skills:
                continue
            checked.add(key)
            # Use the repository catalog rather than mutable historic Skill directories.
            result = materialize_role_skills(
                config=ZfConfig(roles=[role]),
                project_root=ROOT,
                state_dir=Path(tmp),
                provider_skill_root=Path(tmp) / f"package-{len(checked)}",
                role=role,
            )
            for entry in result.skills:
                name = entry.name
                value = policy.setdefault((group, name), [set(), set(), entry.sha256])
                if entry.sha256 != value[2]:
                    raise ValueError("inconsistent current method body")
                value[0].add(
                    ("required" if entry.auto_inject else "on_demand")
                    if entry.status == "resolved"
                    else entry.status
                )
                value[1].add("direct" if name in role.skills else "dependency")
    return policy


def quality_scores(files):
    scores = {}
    for path in files:
        report = json.loads(path.read_text())
        for check in report["results"]:
            for detail in check.get("success_details", []):
                name = re.fullmatch(r"\[([^\]]+)\] quality_score", detail["check"])
                if name:
                    scores[name[1]] = detail["metadata"]["overall_score"]
    return scores


def comparison(cohort, data, policy, scores):
    history = {(r["role"], r["name"]): r for r in data["role_skills"]}
    unavailable = policy is None
    policy = policy or {}
    for role, name in sorted(history.keys() | policy.keys()):
        old = history.get((role, name), {})
        modes, origins, sha = policy.get((role, name), (set(), set(), None))
        if not sha:
            # A method can be observed in history even when that role no longer requests it.
            path = next(
                (
                    p
                    for base in ("skills", "yoke")
                    if (p := ROOT / base / name / "SKILL.md").is_file()
                ),
                None,
            )
            sha = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
        digests = set(old.get("entry_digests", []))
        match = (
            "unknown"
            if not digests or not sha
            else (
                ("all" if digests == {sha} else "some")
                if sha in digests
                else "different"
            )
        )
        numbers = {k: old.get(k, 0) for k in COLUMNS[8:-4]}
        known = {
            "read_attempts_succeeded",
            "read_attempts_failed",
            "read_attempts_superseded",
        }
        numbers["read_attempts_other"] = sum(
            v
            for k, v in old.items()
            if k.startswith("read_attempts_") and k not in known and isinstance(v, int)
        )
        yield dict(
            cohort=cohort,
            role=role,
            skill=name,
            current_mode="|".join(sorted(modes))
            or ("unknown_config" if unavailable else "not_listed"),
            current_source="|".join(sorted(origins)),
            current_sha256=sha or "",
            current_static_score=scores.get(name, ""),
            historic_entry_match=match,
            terminal_binding_counts=json.dumps(
                old.get("terminal_bindings", {}), separators=(",", ":"), sort_keys=True
            ),
            witnessed_output_profiles=json.dumps(
                old.get("read_attempts_by_profile", {}),
                separators=(",", ":"),
                sort_keys=True,
            ),
            historic_read_environments=" | ".join(old.get("read_environments", [])),
            **numbers,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export-roles",
        type=Path,
        help="Export the role fields using the config’s own compiler",
    )
    parser.add_argument(
        "--cohort",
        nargs=3,
        action="append",
        metavar=("LABEL", "AUDIT_JSON", "PROJECT_CONFIG"),
    )
    parser.add_argument("--quality", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.export_roles:
        config = load_config(args.export_roles)
        print(
            json.dumps(
                {
                    "schema": "skill-role-config.v1",
                    "config_sha256": hashlib.sha256(
                        args.export_roles.read_bytes()
                    ).hexdigest(),
                    "roles": [
                        {
                            "name": r.name,
                            "instance_id": r.instance_id,
                            "backend": r.backend,
                            "skills": r.skills,
                            "skill_loading": getattr(r, "skill_loading", {}),
                        }
                        for r in config.roles
                    ],
                },
                indent=2,
            )
        )
        sys.exit(0)
    if not args.cohort:
        parser.error("provide --cohort or --export-roles")
    writer = csv.DictWriter(sys.stdout, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    scores = quality_scores(args.quality)
    for label, archive, config in args.cohort:
        try:
            policy = project_policy(Path(config))
        except ConfigError as exc:
            print(f"{label}: current config unavailable ({exc})", file=sys.stderr)
            policy = None
        writer.writerows(
            comparison(label, json.loads(Path(archive).read_text()), policy, scores)
        )
