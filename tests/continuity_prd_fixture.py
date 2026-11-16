"""Build the isolated real-PRD qualification from the shipping v5 controller.

This only prepares configuration; it never approves or starts a workflow.
"""
from pathlib import Path

import yaml

from zf.core.config.loader import load_config
from zf.core.config.render import renderable_config_to_primitive


def configuration(root: Path, *, harness: Path) -> list[dict]:
    source = harness / "zf-prd-v5.yaml"
    documents = list(yaml.safe_load_all(source.read_text()))
    flow, controller = documents
    spec = flow["spec"]
    spec.update(lanes=2, prdRef="docs/prd/TODO.md", targetRoot=str(root))
    spec["roleDefaults"].update(
        model="gpt-6.1-terra", model_reasoning_effort="high",
        contextWindowTokens=200000,
        provider_session={"sandbox": "workspace-write", "native_delegation": False},
    )
    pipeline = spec["taskPipeline"]
    pipeline["maxActiveTaskPipelines"] = 4
    pipeline["backpressure"]["maxUnverifiedTasks"] = 2
    for stage, prefix in (("impl", "dev"), ("verify", "verify")):
        pipeline["pools"][stage].update(
            capacity=2, roleInstances=[f"{prefix}-lane-{i}" for i in range(2)],
        )
    cfg = controller["spec"]
    cfg["project"] = {
        "name": root.name, "state_dir": ".zf",
        # The fixture uses Node's built-in tests and the installed TS compiler.
        "scripts": {"setup": "node --version && node_modules/.bin/tsc --version"},
    }
    cfg["session"] = {"tmux_session": root.name}
    cfg["profile_sources"] = [str(harness / p) for p in cfg["profile_sources"]]
    cfg["skill_sources"][0]["path"] = str(harness / "skills")
    cfg["workflow"]["plan_approval"] = True
    cfg["orchestrator"]["model"] = "gpt-6.1-terra"
    cfg["quality_gates"]["static"]["required_checks"] = [
        "npm test", "npm run build",
    ]
    # The baseline exercises Kernel/WRC and the configured OA. Resident repair
    # agents are qualified separately with explicit fault injection.
    cfg["runtime"]["run_manager"]["resident_agent"] = {"enabled": False}
    cfg["runtime"]["autoresearch_resident"] = {"enabled": False}
    cfg["runtime"]["workdirs"]["provision_paths"] = ["node_modules"]
    for role in cfg["roles"]:
        role.update(model="gpt-6.1-terra", model_reasoning_effort="high")
        role["provider_session"] = {
            "sandbox": "workspace-write", "native_delegation": False,
        }
    # Named controller readers do not consume lane lifecycleByStage defaults.
    # Preserve their workdirs while rotating native context on each dispatch.
    for role in renderable_config_to_primitive(load_config(source))["roles"]:
        if role["flow_kind"] == "prd" and role["role_kind"] == "reader":
            if role["instance_id"].startswith("verify-lane-"):
                continue
            role.update({
                "model": "gpt-6.1-terra", "model_reasoning_effort": "high",
                "context_window_tokens": 200000,
                "provider_session": {"sandbox": "workspace-write", "native_delegation": False},
                "lifecycle": {"mode": "on_demand", "idle_seconds": 600,
                              "reader_context_scope": "dispatch"},
            })
            cfg["roles"].append(role)
    spec["roleDefaults"]["lifecycleByStage"] = {
        "verify": {"mode": "on_demand", "idle_seconds": 600,
                   "reader_context_scope": "dispatch"},
    }
    return documents
