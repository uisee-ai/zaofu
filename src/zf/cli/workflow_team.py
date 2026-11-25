"""Operator read-only client for the dashboard's versioned stage team projection."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("team", help="Inspect approved stage work, attempts and handoffs")
    parser.add_argument("--request", required=True)
    parser.add_argument("--project", default="default")
    parser.add_argument("--base-url", default=os.environ.get("ZF_WEB_BASE_URL", "http://127.0.0.1:8001"))
    parser.add_argument("--as-of", type=int, default=0, help="EventLog cursor")
    parser.add_argument("--generation", default="", help="Require this exact dashboard snapshot")
    parser.add_argument("--stage", default="")
    parser.add_argument("--task", default="")
    parser.add_argument("--member", default="", help="Obligation, proposed role or bound instance")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.set_defaults(func=run)


def read_stage_teams(args):
    query = urlencode({"as_of": args.as_of})
    url = (args.base_url.rstrip("/") + "/api/projects/" + quote(args.project, safe="")
           + "/workflow-requests/" + quote(args.request, safe="") + "/graph")
    if not args.generation:
        with urlopen(Request(f"{url}/structure?{query}"), timeout=30) as response:
            generation = json.load(response)["generation"]
    else:
        generation = args.generation
    request = Request(f"{url}/execution?{query}", headers={"X-ZF-Structure-Generation": generation})
    with urlopen(request, timeout=30) as response:
        graph = json.load(response)
    if graph.get("generation") != generation or graph["scope"]["request_id"] != args.request:
        raise ValueError("Workbench snapshot binding changed")
    rows = []
    for node in graph["nodes"]:
        team = node["data"].get("team")
        stage = node["data"].get("stage_id")
        if not team or args.stage and stage != args.stage:
            continue
        if (args.stage or args.member or args.task) and team.get("partial"):
            if args.task and not any(args.task in m["task_ids"] for m in team["members"]):
                continue
            if args.member and not any(args.member in {m["instance"], m["obligation_id"], m["role"]} for m in team["members"]):
                continue
            detail = Request(f"{url}/node?{query}&" + urlencode({"node_id": node["id"]}),
                             headers={"X-ZF-Structure-Generation": generation})
            with urlopen(detail, timeout=30) as response:
                selected = json.load(response)
            if selected["scope"] != graph["scope"] or selected["node"]["id"] != node["id"]:
                raise ValueError("Stage details do not match the requested snapshot")
            team = selected["node"]["data"]["team"]
        members = [m for m in team["members"] if (not args.task or args.task in m["task_ids"])
                   and (not args.member or args.member in {m["obligation_id"], m["instance"], m["role"]})]
        if not members and (args.task or args.member):
            continue
        rows.append({"stage": stage, "stage_status": node.get("status"), **team, "members": members})
    return {"scope": graph["scope"], "generation": generation, "stages": rows}


def run(args) -> int:
    try:
        value = read_stage_teams(args)
    except HTTPError as exc:
        print(f"Workbench request failed (HTTP {exc.code})", file=sys.stderr)
        return 1
    except (OSError, URLError, KeyError, ValueError) as exc:
        print(f"Workbench request failed ({exc})", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    print(f"{args.request} | {value['scope'].get('run_id') or 'not started'} | {value['generation']}")
    for stage in value["stages"]:
        denominator = stage["required_total"] if stage["required_total"] is not None else "unknown"
        print(f"{stage['stage']} | {stage['stage_status']} | admitted {stage['admitted_count']}/{denominator}")
        if stage.get("optional_count"):
            print(f"  optional opinions admitted {stage['optional_admitted_count']}/{stage['optional_count']}")
        for mode, progress in stage.get("task_progress", {}).items():
            print(f"  {mode} | tasks {progress['admitted']}/{progress['total']}")
        for decision in stage.get("decisions", []):
            print(f"  {decision['event_type']} | {decision['actor']} | {decision['event_id']}")
        for receipt in stage.get("unbound_deliveries", []):
            print(f"  delivery outside current binding | {receipt['event_type']} | {receipt['event_id']}")
        for member in stage["members"]:
            status = member["status"] + (" (reused)" if member.get("reused") else "")
            label = member["obligation_id"] + (" (optional)" if member.get("required") is False else "")
            print(f"  {label} | {member['role']} | {status} | "
                  f"{member['instance'] or 'instance unassigned'} | {', '.join(member['task_ids'])}")
            if args.stage or args.member or args.task:
                print(f"    owner {member['owner']} | last observed {member['last_event_at']} | {member['source_event_id']}")
                if member.get("reused"):
                    print(f"    reused {member['original_work_unit_id']} | parent {member['source_operation_id']} | "
                          f"original admission {member['source_result_event_id']}")
                if member.get("expected_output"):
                    print(f"    deliverable {member['expected_output']} | {member.get('recipient') or 'recipient unknown'}")
                for h in member.get("attempt_history", []):
                    print(f"    {h['time']} | {h['instance']} | {h['attempt_id']} | {h['event_type']}")
                if member.get("evidence_ref"):
                    print(f"    {member['evidence_ref']['ref']} | {member['evidence_ref']['sha256']}")
            for msg in member.get("messages", []):
                print(f"    {msg['sender']} -> {msg['recipient']} | {msg['posted']} | "
                      f"seen {msg['seen'] or '-'} | consumed {msg['consumed'] or '-'}")
    return 0
