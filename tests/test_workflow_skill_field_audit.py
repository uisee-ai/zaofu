"""Prevent false task attribution and receipts in retrospective Skill evaluations."""

import json
from pathlib import Path
import runpy


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
AUDIT = runpy.run_path(str(SCRIPTS / "audit-workflow-skill-history.py"))
PROBE = runpy.run_path(str(SCRIPTS / "eval-planner-contract-read.py"))
EXPORT = runpy.run_path(str(SCRIPTS / "compare-workflow-skill-history.py"))


def session(path, rows):
    path.write_text(
        "\n".join(json.dumps({"type": "response_item", "payload": v}) for v in rows)
        + "\n"
    )


def test_return_must_match_native_path_call_and_current_attempt(tmp_path):
    trace = tmp_path / "session.jsonl"
    key = "task-pipeline/T1/verify.md"
    ids = ("attempt", "dispatch", "operation", "run")
    prompt = {
        "type": "message",
        "role": "user",
        "content": [
            {"text": f"Read the task briefing at /state/briefings/{key}; follow it."}
        ],
    }
    call = {
        "type": "function_call",
        "name": "exec_command",
        "call_id": "x",
        "arguments": json.dumps(
            {"cmd": "cat /r/codex-home/skills/{alpha,beta}/SKILL.md"}
        ),
    }
    output = {
        "type": "function_call_output",
        "call_id": "x",
        "output": "---\nname: beta\n---\nbody",
    }

    def observe(items, bindings=(ids,), identity=ids):
        session(trace, items)
        return AUDIT["transcript"](
            trace, "verify-lane-1", {("verify-lane-1", key): bindings}, {key: identity}
        )[2:4]

    assert observe([prompt, call, output]) == (["beta"], [("attempt", "beta")])
    assert observe([prompt, call, {**output, "call_id": "wrong"}]) == ([], [])
    assert observe([prompt, call, output], (ids, ids)) == (["beta"], [])
    assert observe([prompt, call, output], identity=("other", *ids[1:])) == (
        ["beta"],
        [],
    )
    assert observe(
        [
            prompt,
            call,
            {
                **prompt,
                "content": [{"text": "Read the task briefing at /s/briefings/new.md"}],
            },
            output,
        ]
    ) == (["beta"], [])
    assert observe(
        [
            prompt,
            {
                **call,
                "arguments": json.dumps({"cmd": "cat /repo/skills/beta/SKILL.md"}),
            },
            output,
        ]
    ) == ([], [])


def test_unavailable_config_and_different_body_are_explicit():
    old = {
        "role_skills": [
            {
                "name": "test-only",
                "role": "verify",
                "entry_digests": ["old"],
                "read_sessions": 1,
            }
        ]
    }
    (missing,) = EXPORT["comparison"]("build", old, None, {})
    assert (missing["current_mode"], missing["historic_entry_match"]) == (
        "unknown_config",
        "unknown",
    )
    (row,) = EXPORT["comparison"](
        "build", old, {("verify", "test-only"): ({"on_demand"}, {"direct"}, "new")}, {}
    )
    assert row["historic_entry_match"] == "different"
    assert row["read_sessions"] == 1 and row["read_attempts_succeeded"] == 0


def test_split_keeps_all_source_sections_and_resolves_original_links():
    root = SCRIPTS.parent / "skills" / PROBE["NAME"]
    text = (root / "SKILL.md").read_text()
    codes = {k: "read-" + k for k in PROBE["MARKS"]}
    whole = PROBE["instrument"](text, codes, False)["SKILL.md"]
    for layout in ["siblings", "references"]:
        parts = PROBE["instrument"](text, codes, True, layout)
        entry = parts.pop("SKILL.md")
        joined = entry.partition("## Contract chapters")[0].rstrip() + "\n\n"
        for path, chunk in parts.items():
            assert f"]({path})" in entry
            if layout == "references":
                chunk = chunk.replace("](../../zf-", "](../zf-")
                for ref in (root / "references").iterdir():
                    chunk = chunk.replace(f"]({ref.name})", f"](references/{ref.name})")
            joined += chunk
        assert joined == whole


def test_terminal_dispatch_alias_is_not_an_attempt_receipt(tmp_path):
    row = dict(
        attempt_id="a",
        dispatch_id="d",
        operation_id="o",
        run_id="r",
        terminal_event_id="e",
    )
    path = tmp_path / "events.jsonl"
    event = {
        "id": "e",
        "payload": {
            "dispatch_id": "d",
            "operation_id": "o",
            "workflow_run_id": "r",
            "attempt_id": "d",
        },
    }
    path.write_text(json.dumps(event) + "\n")
    assert AUDIT["terminal_bindings"](tmp_path, [row])[0] == {
        "a": "identity_unverified"
    }
    event["payload"]["attempt_id"] = "a"
    path.write_text(json.dumps(event) + "\n")
    assert AUDIT["terminal_bindings"](tmp_path, [row])[0] == {"a": "matched"}
