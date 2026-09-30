#!/usr/bin/env python3
"""Prepare and audit the isolated four-flow Kanban browser E2E."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.task.store import TaskStore
from zf.runtime.channel_prd_context import canonical_channel_prd_context
from zf.runtime.channel_projection import project_channel
from zf.runtime.workflow_route_catalog import workflow_route_catalog


GENERAL_ROLES = (
    "general-scoper",
    "general-collector-a",
    "general-collector-b",
    "general-synthesizer",
    "general-verifier",
)
FLOW_ROUTES = {
    "prd": "delivery:prd:default",
    "issue": "delivery:issue:default",
    "refactor": "delivery:refactor:default",
    "general": "general:scope",
}
FLOW_MARKERS = {
    "prd": "FOURFLOW_TASK_PRD",
    "issue": "FOURFLOW_TASK_ISSUE",
    "refactor": "FOURFLOW_TASK_REFACTOR",
    "general": "FOURFLOW_TASK_GENERAL",
}


def summarize_flow_run(events: list[Any], invoke: Any) -> dict[str, Any]:
    """Summarize admission and terminal truth without upgrading an invoke."""
    payload = invoke.payload if hasattr(invoke, "payload") else {}
    payload = payload if isinstance(payload, dict) else {}
    run_id = str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or payload.get("request_id")
        or ""
    ).strip()

    def belongs(event: Any) -> bool:
        event_payload = event.payload if hasattr(event, "payload") else {}
        event_payload = event_payload if isinstance(event_payload, dict) else {}
        identities = {
            str(event_payload[key]).strip()
            for key in ("workflow_run_id", "run_id")
            if event_payload.get(key)
        }
        # A trace/request alias must not override an explicit conflicting run.
        source = str(event_payload.get("source_event_id") or "")
        return bool(run_id and identities == {run_id} and (
            not source or not event.type.startswith("run.admission.") or source == invoke.id
        ))

    position = next((i for i, event in enumerate(events) if event.id == invoke.id), None)
    observed = events[position + 1:] if position is not None else []

    admission = next(
        (
            event
            for event in reversed(observed)
            if belongs(event)
            and str(getattr(event, "type", "") or "")
            in {
                "run.admission.admitted",
                "run.admission.queued",
                "run.admission.rejected",
            }
        ),
        None,
    )
    terminal = next(
        (
            event
            for event in reversed(observed)
            if belongs(event)
            and str(getattr(event, "type", "") or "")
            in {
                "run.goal.completed",
                "run.goal.blocked",
                "run.completed",
                "run.failed",
                "run.cancelled",
                "run.abandoned",
            }
        ),
        None,
    )
    admission_type = str(getattr(admission, "type", "") or "")
    admission_status = {
        "run.admission.admitted": "admitted",
        "run.admission.queued": "queued",
        "run.admission.rejected": "rejected",
    }.get(admission_type, "missing")
    terminal_type = str(getattr(terminal, "type", "") or "")
    terminal_status = (
        "completed" if terminal_type in {"run.goal.completed", "run.completed"}
        else "blocked" if terminal_type == "run.goal.blocked"
        else "failed" if terminal_type in {"run.failed", "run.cancelled", "run.abandoned"}
        else "missing"
    )
    return {
        "run_id": run_id,
        "admission_status": admission_status,
        "admission_event_id": str(getattr(admission, "id", "") or ""),
        "terminal_status": terminal_status,
        "terminal_event_id": str(getattr(terminal, "id", "") or ""),
        "terminal_event_observed": terminal is not None,
    }


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(
        list(args),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _prepare(project_root: Path, source_root: Path) -> None:
    project_root = project_root.resolve()
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "README.md").write_text(
        "# Legacy source\n\nMinimal refactor source fixture.\n",
        encoding="utf-8",
    )
    _run("git", "init", "--initial-branch=main", cwd=source_root)
    _run("git", "config", "user.name", "ZaoFu Four Flow E2E", cwd=source_root)
    _run(
        "git",
        "config",
        "user.email",
        "four-flow-e2e@localhost",
        cwd=source_root,
    )
    _run("git", "add", "README.md", cwd=source_root)
    _run(
        "git",
        "commit",
        "-m",
        "chore: initialize refactor source",
        cwd=source_root,
    )

    config_path = project_root / "zf.yaml"
    documents = list(yaml.safe_load_all(config_path.read_text(encoding="utf-8")))
    for document in documents:
        if not isinstance(document, dict):
            continue
        kind = str(document.get("kind") or "")
        spec = document.setdefault("spec", {})
        if kind == "IssueFlow":
            spec["issueRef"] = "README.md"
        elif kind == "PrdFlow":
            spec["prdRef"] = "README.md"
            spec["targetRoot"] = str(project_root)
        elif kind == "RefactorFlow":
            spec["objectiveRef"] = "README.md"
            spec["sourceRoot"] = str(source_root.resolve())
            spec["targetRoot"] = str(project_root)
            spec["environmentPolicy"] = "isolated_mock_e2e"
        elif kind == "ZfConfig":
            project = spec.setdefault("project", {})
            project["state_dir"] = ".zf"
            roles = spec.setdefault("roles", [])
            existing = {
                str(role.get("name") or "")
                for role in roles
                if isinstance(role, dict)
            }
            for role_name in GENERAL_ROLES:
                if role_name in existing:
                    continue
                roles.append({
                    "name": role_name,
                    "instance_id": role_name,
                    "backend": "mock",
                    "role_kind": "reader",
                    "permission_mode": "default",
                    "transport": "tmux",
                })
            workflow = spec.setdefault("workflow", {})
            profiles = workflow.setdefault("execution_profiles", {})
            profiles.setdefault("direct-v1", {"strategy": "direct"})
    config_path.write_text(
        yaml.safe_dump_all(
            documents,
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (project_root / ".gitignore").write_text(
        "\n".join((
            ".zf/",
            "docs/intake/",
            "artifacts/",
            "",
        )),
        encoding="utf-8",
    )


def _contains_action(event: Any, action: str) -> bool:
    return action in json.dumps(
        event.payload if isinstance(event.payload, dict) else {},
        ensure_ascii=False,
        sort_keys=True,
    )


def _report(
    *,
    project_root: Path,
    state_dir: Path,
    channel_id: str,
    workflow_request_id: str,
) -> None:
    config = load_config(project_root / "zf.yaml")
    catalog = workflow_route_catalog(config)
    routes = list(catalog["routes"])
    route_ids = [str(route["route_id"]) for route in routes]
    assert len(route_ids) == len(set(route_ids)), (
        f"duplicate workflow routes: {route_ids}"
    )
    for route_id in FLOW_ROUTES.values():
        assert route_id in route_ids, f"missing workflow route: {route_id}"
    route_by_id = {
        str(route["route_id"]): dict(route)
        for route in routes
    }

    channel = project_channel(state_dir, channel_id)
    assert isinstance(channel, dict), f"Channel not found: {channel_id}"
    member_ids = {
        str(member.get("member_id") or "")
        for member in channel.get("members") or []
        if isinstance(member, dict)
    }
    assert member_ids == {
        "product_pm",
        "arch",
        "critic",
        "synthesizer",
    }, member_ids
    discussion = channel.get("discussion") or {}
    assert discussion.get("max_rounds") == 2, discussion
    assert discussion.get("mode") == "multi_lens", discussion
    assert discussion.get("engine_mode") == "fanout_then_synthesis", discussion

    prd_context = canonical_channel_prd_context(state_dir)
    matching_prds = [
        item
        for item in prd_context["items"]
        if item["channel_id"] == channel_id
    ]
    assert len(matching_prds) == 1, matching_prds
    canonical_prd = matching_prds[0]
    artifact_ref = str(canonical_prd["artifact_ref"])
    artifact_digest = str(canonical_prd["artifact_digest"])
    prd_path = state_dir / artifact_ref
    prd_payload = json.loads(prd_path.read_text(encoding="utf-8"))
    prd_body = (
        prd_payload.get("body")
        if isinstance(prd_payload.get("body"), dict)
        else {}
    )
    spec_ref = str(prd_body.get("spec_path") or "")
    spec_digest = str(prd_body.get("spec_digest") or "")
    assert spec_ref and spec_digest, prd_body

    events = EventLog(state_dir / "events.jsonl").read_all()
    event_order = {
        event.id: index
        for index, event in enumerate(events, start=1)
    }
    workflow_proposed = next(
        event
        for event in events
        if event.type == "workflow.request.proposed"
        and (
            event.correlation_id == workflow_request_id
            or event.payload.get("request_id") == workflow_request_id
        )
    )
    workflow_proposal_digest = str(
        workflow_proposed.payload.get("proposal_digest") or ""
    )
    assert workflow_proposal_digest
    config_applied = [
        event
        for event in events
        if event.type == "workflow.config.change.applied"
        and event.payload.get("proposal_digest")
        == workflow_proposal_digest
    ]
    assert len(config_applied) == 1, config_applied
    premature_submit = [
        event
        for event in events
        if event.type == "workflow.submit.accepted"
        and (
            event.correlation_id == workflow_request_id
            or event.payload.get("request_id") == workflow_request_id
        )
    ]
    assert not premature_submit, premature_submit

    consensus = next(
        event
        for event in events
        if event.type == "channel.consensus.reached"
        and event.payload.get("channel_id") == channel_id
    )
    task_store = TaskStore(state_dir / "kanban.json")
    tasks = task_store.list_all_with_archive()
    flow_results: dict[str, Any] = {}
    for flow_kind, marker in FLOW_MARKERS.items():
        matched_tasks = [
            task for task in tasks
            if marker in task.title
        ]
        assert len(matched_tasks) == 1, (
            f"{flow_kind} task mismatch: "
            f"{[task.title for task in matched_tasks]}"
        )
        task = matched_tasks[0]
        contract = asdict(task.contract)
        assert contract["spec_ref"] == spec_ref
        assert contract["source_ref"] == artifact_ref
        assert contract["product_contract_ref"] == artifact_ref
        assert artifact_ref in contract["handoff_artifacts"]
        assert (
            contract["evidence_contract"]["channel_prd_digest"]
            == artifact_digest
        )
        assert contract["evidence_contract"]["spec_digest"] == spec_digest
        assert contract["verification"] == "test -f README.md"
        assert contract["acceptance_criteria"]

        created = next(
            event
            for event in events
            if event.type == "task.created" and event.task_id == task.id
        )
        plan_requested = next(
            event
            for event in events
            if event.type == "kanban.agent.plan.requested"
            and event.task_id == task.id
        )
        plan_answered = next(
            event
            for event in events
            if event.type == "kanban.agent.plan.answered"
            and event.task_id == task.id
        )
        workflow_start_proposal = next(
            event
            for event in events
            if event.type == "operator.action.proposed"
            and event.task_id == task.id
            and _contains_action(event, "workflow-start")
        )
        workflow_start_request = next(
            event
            for event in events
            if event.type == "web.action.requested"
            and event.task_id == task.id
            and str(event.payload.get("action") or "") == "workflow-start"
        )
        invoke = next(
            event
            for event in events
            if event.type == "workflow.invoke.requested"
            and event.task_id == task.id
        )
        expected_pattern = str(
            route_by_id[FLOW_ROUTES[flow_kind]]["entry_pattern_id"]
        )
        assert invoke.payload["pattern_id"] == expected_pattern
        assert (
            invoke.payload["source_refs"]["channel_prd_digest"]
            == artifact_digest
        )
        manifest_ref = Path(
            str(invoke.payload["workflow_input_manifest_ref"])
        )
        if not manifest_ref.is_absolute():
            manifest_ref = state_dir / manifest_ref
        assert manifest_ref.exists(), manifest_ref
        ordered_events = {
            "consensus": (consensus.id, event_order[consensus.id]),
            "task_created": (created.id, event_order[created.id]),
            "plan_requested": (
                plan_requested.id,
                event_order[plan_requested.id],
            ),
            "workflow_proposed": (
                workflow_start_proposal.id,
                event_order[workflow_start_proposal.id],
            ),
            "plan_answered": (
                plan_answered.id,
                event_order[plan_answered.id],
            ),
            "workflow_requested": (
                workflow_start_request.id,
                event_order[workflow_start_request.id],
            ),
            "workflow_invoked": (invoke.id, event_order[invoke.id]),
        }
        observed_order = [item[1] for item in ordered_events.values()]
        assert observed_order == sorted(observed_order), ordered_events
        flow_results[flow_kind] = {
            "task_id": task.id,
            "route_id": FLOW_ROUTES[flow_kind],
            "pattern_id": expected_pattern,
            "invoke_event_id": invoke.id,
        }

    print(json.dumps({
        "ok": True,
        "channel": {
            "channel_id": channel_id,
            "members": sorted(member_ids),
            "max_rounds": discussion["max_rounds"],
            "consensus_event_id": consensus.id,
            "artifact_ref": artifact_ref,
            "artifact_digest": artifact_digest,
        },
        "dynamic_workflow": {
            "request_id": workflow_request_id,
            "config_applied_event_id": config_applied[0].id,
            "route_id": FLOW_ROUTES["general"],
            "submitted_during_install": False,
        },
        "flows": flow_results,
        "event_count": len(events),
    }, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--project-root", type=Path, required=True)
    prepare.add_argument("--source-root", type=Path, required=True)
    report = subparsers.add_parser("report")
    report.add_argument("--project-root", type=Path, required=True)
    report.add_argument("--state-dir", type=Path, required=True)
    report.add_argument("--channel-id", required=True)
    report.add_argument("--workflow-request-id", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "prepare":
        _prepare(args.project_root, args.source_root)
    else:
        _report(
            project_root=args.project_root.resolve(),
            state_dir=args.state_dir.resolve(),
            channel_id=args.channel_id,
            workflow_request_id=args.workflow_request_id,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
