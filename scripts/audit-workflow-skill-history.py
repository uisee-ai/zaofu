"""Read archived ZaoFu dispatches and Codex tool returns without opening the runtime."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
NAME = r"[a-zA-Z0-9][a-zA-Z0-9_.-]*"
FILE = re.compile(
    r"(?:codex-home|\.codex|\.agents|\.claude|\$\{?CODEX_HOME\}?)/skills/(\{[a-zA-Z0-9_.,-]+\}|"
    + NAME
    + r")/SKILL\.md\b"
)
HEADER = re.compile(r"(?m)^---\s*\nname:\s*[\"']?(" + NAME + r")[\"']?\s*$")
BRIEF = re.compile(
    r"^Read the (?:task|fanout child|operation) briefing at [^\n;]*?/briefings/([^\s;`]+?\.md)\b",
    re.I,
)
READ = re.compile(r"\b(?:cat|sed|head)\s+([^;|&\n]+)")
MANIFEST_PATHS = (
    "workdirs/*/runtime/skills-manifest.json",
    "workdirs/tasks/*/*/*/runtime/skills-manifest.json",
)


def family(role):
    return re.sub(r"-(?:lane-)?\d+$", "", role)


def briefing_key(value):
    return str(value or "").partition("/briefings/")[-1].removeprefix("briefings/")


def read_targets(command):
    return {
        name
        for segment in READ.findall(command)
        for group in FILE.findall(segment)
        for name in group.strip("{}").split(",")
    }


def returned_headers(output):
    # Codex archives wrap JS tool output in up to several JSON encodings.
    plain = re.sub(r"\\+n", "\n", output)
    return set(HEADER.findall(plain))


def transcript(path, role, attempts, briefs):
    active, session, seen, associated, bound = None, None, set(), set(), set()
    pending = {}
    client = model = "unknown"
    environments = set()
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        # Freeze the observed prefix even if the archival source is still growing.
        remaining = path.stat().st_size
        while remaining:
            line = handle.readline(remaining)
            if not line:
                break
            remaining -= len(line)
            sha.update(line)
            if (
                b'"response_item"' not in line
                and b'"session_meta"' not in line
                and b'"turn_context"' not in line
            ):
                continue
            try:
                record = json.loads(line)
            except ValueError:
                if remaining or line.endswith(b"\n"):
                    raise
                continue
            payload = record.get("payload", {})
            kind = payload.get("type")
            if record.get("type") == "session_meta":
                session = payload.get("session_id") or payload.get("id")
                client = payload.get("cli_version") or "unknown"
            elif record.get("type") == "turn_context":
                model = payload.get("model") or "unknown"
            if record.get("type") != "response_item":
                continue
            if kind == "message" and payload.get("role") == "user":
                message = " ".join(
                    x.get("text", "") for x in payload.get("content", [])
                )
                match = BRIEF.match(message)
                if match:
                    key = match.group(1)
                    matches = attempts.get((role, key), [])
                    active = (
                        matches[0][0]
                        if len(matches) == 1 and matches[0] == briefs.get(key)
                        else None
                    )
                    if active:
                        bound.add(active)
                # Additional dispatch-shaped prompts without an exact source end prior attribution.
                elif message.lower().startswith(
                    ("read the ", "active task:", "# agents.md instructions")
                ):
                    active = None
            elif kind in ("custom_tool_call", "function_call"):
                command = payload.get("input") or payload.get("arguments", "")
                if payload.get("name") in (
                    "exec",
                    "exec_command",
                    "functions.exec",
                    "functions.exec_command",
                ):
                    targets = read_targets(command)
                    if targets and payload.get("call_id"):
                        pending[payload["call_id"]] = (targets, active, model)
            elif kind in ("custom_tool_call_output", "function_call_output"):
                call = pending.pop(payload.get("call_id"), None)
                if call:
                    targets, at_call, at_model = call
                    names = targets & returned_headers(str(payload.get("output", "")))
                    seen.update(names)
                    environments.update((name, client, at_model) for name in names)
                    if at_call and active == at_call:
                        associated.update((at_call, name) for name in names)
    return (
        session,
        sha.hexdigest(),
        sorted(seen),
        sorted(associated),
        sorted(bound),
        environments,
    )


def terminal_bindings(state, attempts):
    expected = {a["terminal_event_id"] for a in attempts if a.get("terminal_event_id")}
    actual = {}
    inventory = {}
    unreadable = Counter()
    files = sorted((state / "events").glob("????-??-??.jsonl")) + [
        state / "events.jsonl"
    ]
    for path in files:
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            digest = hashlib.sha256()
            remaining = path.stat().st_size
            while remaining:
                line = handle.readline(remaining)
                if not line:
                    break
                remaining -= len(line)
                digest.update(line)
                try:
                    event = json.loads(line)
                except ValueError:
                    unreadable[str(path.relative_to(state))] += 1
                    continue
                if not isinstance(event, dict):
                    unreadable[str(path.relative_to(state))] += 1
                    continue
                eid = event.get("id")
                if eid not in expected:
                    continue
                payload = event.get("payload", {})
                identity = tuple(
                    payload.get(k)
                    for k in (
                        "attempt_id",
                        "dispatch_id",
                        "operation_id",
                        "workflow_run_id",
                    )
                )
                if eid in actual and actual[eid] != identity:
                    actual[eid] = ()
                else:
                    actual[eid] = identity
            inventory[str(path.relative_to(state))] = digest.hexdigest()
    result = {}
    for a in attempts:
        t = actual.get(a.get("terminal_event_id"))
        expected_tuple = tuple(
            a.get(k) for k in ("attempt_id", "dispatch_id", "operation_id", "run_id")
        )
        result[a["attempt_id"]] = (
            "missing"
            if t is None
            else "matched"
            if all(expected_tuple) and t == expected_tuple
            else "identity_unverified"
        )
    return result, inventory, unreadable


def audit(state):
    # No helper that creates a missing DB or lock file is used here.
    rows = defaultdict(Counter)
    by_role = defaultdict(Counter)
    inventory, ledger, attempts, briefs = {}, {}, defaultdict(list), {}
    versions = defaultdict(set)
    read_environments = defaultdict(set)
    profiles_by_brief = {}
    profile_reads = defaultdict(Counter)
    terminal_reads = defaultdict(Counter)

    def asset(role, name):
        return rows[(family(role), name)]

    attempt_file = state / "task_attempts.json"
    attempt_bytes = attempt_file.read_bytes()
    attempt_rows = list(json.loads(attempt_bytes)["attempts"].values())
    terminals, events_inventory, unreadable = terminal_bindings(state, attempt_rows)
    inventory.update(events_inventory)
    for a in attempt_rows:
        attempt_id, role = a["attempt_id"], a["role"]
        ledger[attempt_id] = a["status"]
        attempts[(role, briefing_key(a.get("briefing_ref")))].append(
            (attempt_id, a.get("dispatch_id"), a.get("operation_id"), a.get("run_id"))
        )
        by_role[family(role)]["attempts_" + a["status"]] += 1
    inventory["task_attempts.json"] = hashlib.sha256(attempt_bytes).hexdigest()
    for pattern in MANIFEST_PATHS:
        for path in sorted(state.glob(pattern)):
            raw = path.read_bytes()
            inventory[str(path.relative_to(state))] = hashlib.sha256(raw).hexdigest()
            m = json.loads(raw)
            role = m["role"]
            by_role[family(role)]["manifests"] += 1
            for item in m["skills"]:
                row = asset(role, item["name"])
                if item.get("status") != "resolved":
                    row["unresolved_manifest"] += 1
                    continue
                row["resolved_manifest"] += 1
                row[
                    "required_manifest" if item.get("auto_inject") else "other_manifest"
                ] += 1
                if item.get("dependency_of"):
                    row["dependency_manifest"] += 1
                if item.get("sha256"):
                    versions[(family(role), item["name"])].add(item["sha256"])
    for path in sorted((state / "briefings").rglob("*.md.metrics.json")):
        raw = path.read_bytes()
        m = json.loads(raw)
        role = m["role"]
        summary = by_role[family(role)]
        doc = path.with_suffix("").with_suffix("")
        contents = doc.read_bytes() if doc.is_file() else b""
        digest = hashlib.sha256(contents).hexdigest() if contents else None
        if not digest or digest != m.get("briefing_sha256"):
            summary["missing_or_changed_brief"] += 1
            continue
        inventory[str(path.relative_to(state))] = hashlib.sha256(raw).hexdigest()
        key = doc.relative_to(state / "briefings").as_posix()
        text = contents.decode()
        identity = (
            text.rpartition("## Runtime TaskAttempt Identity")[2]
            if "## Runtime TaskAttempt Identity" in text
            else ""
        )
        fields = dict(
            re.findall(
                r"(?m)^- (attempt_id|dispatch_id|operation_id|workflow_run_id): `([^`\n]+)`$",
                identity.split("\n## ", 1)[0],
            )
        )
        profiles_by_brief[key] = m.get("output_profile_id") or "unknown"
        briefs[key] = tuple(
            fields.get(k)
            for k in ("attempt_id", "dispatch_id", "operation_id", "workflow_run_id")
        )
        summary["verified_briefs"] += 1
        declared = set()
        for line in text.splitlines():
            if line.startswith("- skills: `"):
                declared.update(
                    x.strip()
                    for x in line.split("`")[1].split(",")
                    if x.strip() != "none" and re.fullmatch(NAME, x.strip())
                )
        indexed = set(m.get("indexed_skills") or [])
        auto = set(m.get("auto_injected_skills") or [])
        summary["zero_index_with_declared"] += int(not indexed and bool(declared))
        for names, field in (
            (declared, "brief_declared"),
            (indexed, "brief_indexed"),
            (auto, "brief_required"),
        ):
            for name in names:
                asset(role, name)[field] += 1
    session_index, linked, bound, seen_ids = {}, set(), set(), set()
    witnesses = []
    for path in sorted(
        state.glob("workdirs/*/codex-home/sessions/*/*/*/rollout-*.jsonl")
    ):
        role = path.relative_to(state).parts[1]
        sid, digest, seen, linked_reads, bound_attempts, environments = transcript(
            path, role, attempts, briefs
        )
        if not sid:
            by_role[family(role)]["unidentified_sessions"] += 1
            continue
        if sid in seen_ids:
            raise ValueError("duplicate session identity, review before combining")
        seen_ids.add(sid)
        session_index[str(path.relative_to(state))] = digest
        by_role[family(role)]["sessions"] += 1
        by_role[family(role)]["sessions_with_skill_read"] += bool(seen)
        for name, client, model in environments:
            read_environments[(family(role), name)].add(f"codex {client} / {model}")
        for name in seen:
            asset(role, name)["read_sessions"] += 1
        witnesses.extend(
            (str(path.relative_to(state)), aid, name, ledger[aid])
            for aid, name in linked_reads
        )
        linked.update((role, aid, name) for aid, name in linked_reads)
        bound.update((role, aid) for aid in bound_attempts)
    for role, aid in bound:
        by_role[family(role)]["bound_attempts"] += 1
    for role, aid in {(role, aid) for role, aid, _ in linked}:
        by_role[family(role)]["bound_read_attempts"] += 1
    profile_by_attempt = {
        ids[0][0]: profiles_by_brief[key]
        for (_, key), ids in attempts.items()
        if len(ids) == 1 and ids[0] == briefs.get(key)
    }
    for role, aid, name in linked:
        asset(role, name)["read_attempts_" + ledger[aid]] += 1
        profile_reads[(family(role), name)][profile_by_attempt[aid]] += 1
        terminal_reads[(family(role), name)][terminals[aid]] += 1
    return {
        "roles": by_role,
        "unreadable_event_records": unreadable,
        "role_skills": [
            dict(
                role=role,
                name=name,
                entry_digests=sorted(versions[(role, name)]),
                terminal_bindings=dict(sorted(terminal_reads[(role, name)].items())),
                read_attempts_by_profile=dict(
                    sorted(profile_reads[(role, name)].items())
                ),
                read_environments=sorted(read_environments[(role, name)]),
                **counts,
            )
            for (role, name), counts in sorted(rows.items())
        ],
        "inventory": inventory | session_index,
        "witnesses": witnesses,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.state), ensure_ascii=False, sort_keys=True, indent=2))
