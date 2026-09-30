"""Synthetic Docker protocol double. Never contacts Docker or any HTTP target.

Also runnable as a subprocess; only writes its caller-owned fixture/receipt
paths. A passing result is launcher conformance, never real scan evidence.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any

from tests.testkit.command_runner import CommandReceipt
from tests.testkit.evidence import write_json_atomic


def initial_state(**faults) -> dict[str, Any]:
    return {"containers": {}, "networks": {}, "calls": [], "faults": faults}


def execute(state: dict, argv: list[str]) -> tuple[int, str, str]:
    assert argv[:3] == ["docker", "--host", "unix:///var/run/docker.sock"]
    assert argv[3] == "--config"
    args = argv[5:]
    state["calls"].append(args)
    faults = state["faults"]

    def option(name: str) -> str:
        return args[args.index(name) + 1]

    def output(value: Any) -> tuple[int, str, str]:
        return 0, json.dumps(value), ""

    if args[0] == "info":
        return output({"ServerVersion": "27.0.0" if faults.get("old_engine") else "28.2.0"})
    if args[:2] == ["image", "inspect"]:
        if faults.get("missing_image"):
            return 1, "", "No such image"
        repository, digest = args[-1].split("@")
        repo_digest = repository.split(":")[0] + "@" + digest
        return output([{"Id": "sha256:" + "a" * 64, "Os": "linux", "Architecture": "amd64",
                        "RepoDigests": [] if faults.get("wrong_digest") else [repo_digest]}])
    kind, action = args[:2]
    objects = state["containers" if kind == "container" else "networks"]
    if action == "ls":
        if faults.get("cleanup_daemon_down"):
            return 1, "", "Cannot connect to Docker daemon"
        label, value = option("--filter").removeprefix("label=").split("=", 1)
        rows = []
        for key, row in objects.items():
            labels = row["Config"]["Labels"] if kind == "container" else row["Labels"]
            if labels.get(label) == value:
                rows.append({"ID": key, "Names" if kind == "container" else "Name": row["Name"].lstrip("/")})
        return 0, "\n".join(json.dumps(row) for row in rows), ""
    if action == "create":
        name = option("--name") if kind == "container" else args[-1]
        label, run_id = option("--label").split("=", 1)
        identity = hashlib.sha256(name.encode()).hexdigest()
        if faults.get("name_conflict") and kind == "container":
            return 409, "", "name belongs to another session"
        if kind == "network":
            objects[identity] = {"Id": identity, "Name": name, "Driver": "bridge", "Internal": not faults.get("unsafe_network"),
                                 "Labels": {label: run_id}, "Containers": {}, "Options": {
                                     "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
                                     "com.docker.network.bridge.gateway_mode_ipv6": "isolated"}}
        else:
            mounts = {}
            for index, item in enumerate(args):
                if item == "--mount":
                    parts = dict(part.split("=", 1) for part in args[index + 1].split(",") if "=" in part)
                    mounts[parts["dst"]] = parts["src"]
            scanner = name.endswith("-scanner")
            network = option("--network")
            objects[identity] = {"Id": identity, "Name": "/" + name,
                "Config": {"Labels": {label: run_id}}, "MountMap": mounts, "Access": "",
                "HostConfig": {"NetworkMode": "host" if faults.get("unsafe_container") else network,
                               "PortBindings": {}, "Privileged": False, "ReadonlyRootfs": True},
                "NetworkSettings": {"Networks": {"owned": {"NetworkID": network, "IPAddress": "172.29.0.3" if scanner else "172.29.0.2"}}},
                "State": {"Running": False, "ExitCode": 0, "OOMKilled": False}}
        if faults.get("lost_create_reply") == kind:
            return 1, "", "reply lost after create"
        return 0, identity + "\n", ""
    if action == "inspect":
        row = objects[args[-1]]
        if kind == "network":
            row["Containers"] = {key: {} for key, item in state["containers"].items()
                                 if any(net["NetworkID"] == args[-1] for net in item["NetworkSettings"]["Networks"].values())}
            if faults.get("foreign_member"):
                row["Containers"]["f" * 64] = {}
        return output([row])
    if action == "rm":
        if faults.get("cleanup_rm_failure"):
            return 1, "", "injected cleanup refusal"
        if kind == "network" and (faults.get("foreign_member") or any(
            net["NetworkID"] == args[-1] for item in state["containers"].values()
            for net in item["NetworkSettings"]["Networks"].values()
        )):
            return 1, "", "network has active endpoints"
        objects.pop(args[-1])
        return 0, args[-1], ""
    if action == "stop":
        objects[args[-1]]["State"]["Running"] = False
        return 0, args[-1], ""
    if action == "logs":
        return 0, objects[args[-1]]["Access"], ""
    if action == "exec":
        task_id = args[-1]
        if faults.get("wrong_nonce"):
            return output({"task_id": "another-run", "paths": ["/health", "/api/state"]})
        objects[args[2]]["Access"] += 'INFO "GET /health HTTP/1.1" 200\nINFO "GET /api/state HTTP/1.1" 200\n'
        return output({"task_id": task_id, "paths": ["/health", "/api/state"]})
    if action == "start":
        row = objects[args[-1]]
        row["State"]["Running"] = True
        if "--attach" not in args:
            return 0, args[-1], ""
        if faults.get("interrupt"):
            raise KeyboardInterrupt("synthetic cancellation")
        if faults.get("block_scan"):
            time.sleep(5)
        plan_path = Path(row["MountMap"]["/zap/input"]) / "plan.yaml"
        plan = json.loads(plan_path.read_text())
        origin = plan["jobs"][0]["requests"][0]["url"].removesuffix("/health")
        if faults.get("changed_plan"):
            write_json_atomic(plan_path, {"jobs": []})
        app = next(item for item in objects.values() if item["Name"].endswith("-app"))
        if not faults.get("zero_requests"):
            app["Access"] += 'INFO "GET /health HTTP/1.1" 200\nINFO "GET /api/state HTTP/1.1" 200\n'
        if faults.get("unexpected_request"):
            app["Access"] += 'INFO "POST /api/action HTTP/1.1" 200\n'
        risk = str(faults.get("risk", 0))
        alerts = [] if risk == "0" else [{"pluginid": "10001", "alert": "Synthetic risk", "riskcode": risk,
            "confidence": "2", "instances": [{"uri": origin + "/api/state", "method": "GET"}], "count": "1"}]
        payload = {"@programName": "ZAP", "@version": "synthetic", "@generated": "synthetic-current-run",
                   "site": [{"@name": origin if not faults.get("wrong_report_target") else "http://foreign.invalid:8002",
                             "@host": "172.29.0.2", "@port": "8002", "@ssl": "false", "alerts": alerts}]}
        report_path = Path(row["MountMap"]["/zap/wrk"]) / plan["jobs"][-1]["parameters"]["reportFile"]
        if not faults.get("missing_report"):
            write_json_atomic(report_path, payload)
        row["State"].update(Running=False, ExitCode=faults.get("scan_exit", 0), OOMKilled=bool(faults.get("oom")))
        if faults.get("mutate_canonical"):
            write_json_atomic(Path(app["MountMap"]["/zf-fixture"]) / "runtime-state/kanban.json", {"tampered": True})
        return faults.get("scan_exit", 0), "synthetic AF result\n", ""
    raise AssertionError(f"unexpected fake Docker call: {args}")


class FakeDocker:
    def __init__(self, **faults) -> None:
        self.state = initial_state(**faults)

    def __call__(self, command, *, command_id, cwd, output_dir, timeout_seconds, env):
        code, out, err = execute(self.state, command)
        output_dir.mkdir(parents=True, exist_ok=True)
        stdout, stderr = output_dir / f"{command_id}.stdout.log", output_dir / f"{command_id}.stderr.log"
        stdout.write_text(out)
        stderr.write_text(err)
        receipt = CommandReceipt(command_id, " ".join(command), hashlib.sha256("\0".join(command).encode()).hexdigest(),
                                 str(cwd), code, False, 0.0, str(stdout), str(stderr))
        write_json_atomic(output_dir / f"{command_id}.receipt.json", asdict(receipt))
        return receipt


def main() -> int:
    state_path = Path(sys.argv[1])
    state = json.loads(state_path.read_text())
    code, out, err = execute(state, sys.argv[2:])
    write_json_atomic(state_path, state)
    print(out, end="")
    print(err, end="", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
