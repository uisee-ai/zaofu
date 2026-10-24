#!/usr/bin/env python3
"""Evaluate main-agent decisions; never apply proposals or start workflows."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from typing import Any

from zf.core.config.loader import load_config
from zf.runtime.workflow_route_catalog import workflow_route_catalog
from zf.web.plan_extraction import extract_plan_request
from zf.web.proposal_extraction import action_json_candidates, extract_action_proposal


def score(case: dict, reply: str, *, catalog: dict, config: Any = None) -> dict:
    """Compare decisions with authored labels, not keywords in user messages."""
    errors: list[str] = []
    action = extract_action_proposal(reply, user_message=case["message"])
    plan = extract_plan_request(reply, config=config)
    if action and plan:
        errors.append("action and clarification cannot be emitted together")
    decision = "answer"
    options: list[dict] = []
    if action:
        decision = str(action.get("action"))
        if not action.get("valid"):
            errors.append("invalid action proposal")
        workflow = (action.get("payload") or {}).get("workflow_plan") or {}
        options = workflow.get("options") or []
        if case.get("forbid_workflow_plan") and workflow:
            errors.append("tracking-only request unexpectedly contains workflow planning")
    elif plan:
        decision = "clarify"
        if not plan.get("valid"):
            errors.append("invalid clarification request")
    else:
        # A malformed final machine envelope must not score as a plain answer.
        for candidate in action_json_candidates(reply):
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                errors.append("malformed final JSON")
                continue
            if isinstance(value, dict) and any(k in value for k in ("action", "action_proposal", "plan_request")):
                errors.append("unaccepted final envelope")
    if not reply.strip():
        errors.append("empty answer")
    if decision not in case["expected_decisions"]:
        errors.append(f"unexpected decision: {decision}")
    routes = {row["route_id"]: row for row in catalog.get("routes", [])}
    executable = [o for o in options if o.get("route_id")]
    if any(o["route_id"] not in routes for o in executable):
        errors.append("unregistered route")
    expected = case.get("expected_route_kinds") or []
    if expected:
        recommended = [o for o in executable if o.get("recommended") is True]
        if len(recommended) != 1 or routes.get(recommended[0]["route_id"], {}).get("kind") not in expected:
            errors.append("missing or incorrect recommended route")
    return {"case_id": case["id"], "decision": decision, "passed": not errors, "errors": errors}


def evaluate(cases: list[dict], *, agent: Any, backend: str, catalog: dict, config: Any = None) -> list[dict]:
    rows = []
    for case in cases:
        available = {r.get("kind") for r in catalog.get("routes", [])}
        if case.get("expected_route_kinds") and not available.intersection(case["expected_route_kinds"]):
            rows.append({"case_id": case["id"], "passed": False, "status": "catalog_ineligible"})
            continue
        turns = []
        for message in [*case.get("prior_messages", []), case["message"]]:
            result = agent.run_turn(
                backend=backend, message=message, scope="project",
                thread_key=f"intake-eval:{case['id']}", permission_profile="read_only",
                context={"workflow_route_catalog": catalog, "project_id": "intake-eval"},
            )
            turns.append({"message": message, "reply": result.reply, "status": result.status})
            if not result.ok:
                break
        row = score(case, result.reply, catalog=catalog, config=config) if result.ok else {
            "case_id": case["id"], "passed": False, "errors": [str(getattr(result, "error", "provider failed"))],
        }
        telemetry = getattr(result, "telemetry", {}) or {}
        row.update(status=result.status, reply=result.reply, turns=turns, usage=getattr(result, "usage", {}),
                   model=telemetry.get("model", "unreported"),
                   provider_session_id=getattr(result, "provider_session_id", ""))
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=Path(__file__).resolve().parents[1] / "tests/fixtures/intake-routing/cases.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=["codex-headless", "claude-headless"])
    parser.add_argument("--responses", type=Path, help="Replay recorded {case_id: reply}; never treated as a live model run")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if bool(args.backend) == bool(args.responses):
        parser.error("choose exactly one of --backend and --responses")
    root = args.project_root.resolve()
    config = load_config(root / "zf.yaml")
    catalog = workflow_route_catalog(config)
    data = args.cases.read_bytes()
    cases = json.loads(data)["cases"]
    if args.case:
        unknown = set(args.case) - {c["id"] for c in cases}
        if unknown:
            parser.error(f"unknown cases: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in args.case]
    if args.responses:
        replies = json.loads(args.responses.read_text())
        rows = []
        for case in cases:
            reply = str(replies.get(case["id"], ""))
            row = score(case, reply, catalog=catalog, config=config)
            row.update(reply=reply, status="recorded", model="unreported")
            rows.append(row)
    else:
        from zf.web.headless_agent import KanbanHeadlessAgent

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with tempfile.TemporaryDirectory(prefix=f"zf-intake-eval-{stamp}-") as temp:
            agent = KanbanHeadlessAgent(state_dir=Path(temp), project_root=root, timeout_s=args.timeout)
            rows = evaluate(cases, agent=agent, backend=args.backend, catalog=catalog, config=config)
    report = {
        "schema_version": "intake-routing-eval-result.v1",
        "mode": "live_provider" if args.backend else "recorded_responses",
        "backend": args.backend, "cases_sha256": hashlib.sha256(data).hexdigest(),
        "catalog": catalog, "cases": rows,
        "passed": bool(rows) and all(r["passed"] for r in rows),
        "limitations": "Scores declared decisions and routes; answer substance and product delivery need independent review.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "cases": len(rows), "mode": report["mode"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
