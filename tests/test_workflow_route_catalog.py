from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from zf.core.config.loader import load_config
from zf.core.config.schema import FanoutChildConfig
from zf.runtime.workflow_route_catalog import (
    delivery_route_contracts_for_kind,
    resolve_workflow_route,
    workflow_route_catalog,
)


ROOT = Path(__file__).resolve().parents[1]


def test_root_catalog_projects_delivery_and_registered_research_routes() -> None:
    config = load_config(ROOT / "zf.yaml")

    first = workflow_route_catalog(config)
    second = workflow_route_catalog(config)

    assert first == second
    assert first["config_digest"].startswith("sha256:")
    routes = {
        route["route_id"]: route
        for route in first["routes"]
    }
    delivery = routes["delivery:prd:standard"]
    assert delivery["entry_pattern_id"] == "prd-scan"
    assert delivery["lane_count"] == 2
    assert delivery["topology"] == "multi_lane"
    assert delivery["required_parameters"] == ["target_root"]
    assert set(delivery["writer_roles"]) == {"dev-lane-0", "dev-lane-1"}
    assert {"verify-lane-0", "verify-lane-1"} <= set(
        delivery["verify_roles"]
    )

    research = routes["research:fixed"]
    assert research["topology"] == "fanout_reader"
    assert research["lane_count"] == 0
    assert research["writer_roles"] == []
    assert research["roles"] == [
        "source_researcher",
        "product_analyst",
        "technical_analyst",
        "risk_critic",
        "synthesizer",
    ]
    adaptive = routes["research:adaptive-pilot"]
    assert adaptive["template_id"] == "research-adaptive.pilot.v1"
    assert adaptive["entry_pattern_id"] == "research-adaptive"
    assert adaptive["roles"] == ["research_root"]
    assert adaptive["writer_roles"] == []
    assert adaptive["rollout"] == "opt_in_pilot"


def test_refactor_catalog_exposes_request_policy_parameters():
    config = load_config(ROOT / "examples/prod/controller/refactor-lane-v3.yaml")
    route = resolve_workflow_route(config, "delivery:refactor:standard")
    assert route["required_parameters"] == ["source_root", "target_root"]


def test_issue_catalog_does_not_require_repository_parameters():
    config = load_config(ROOT / "examples/prod/controller/issue-fanout-v3.yaml")
    route = resolve_workflow_route(config, "delivery:issue:standard")
    assert route["required_parameters"] == []


def test_catalog_hides_registered_research_stage_with_wrong_role_contract() -> None:
    config = load_config(ROOT / "zf.yaml")
    stage = next(
        item
        for item in config.workflow.stages
        if item.id == "research-fanout"
    )
    stage.children = [
        FanoutChildConfig(role_instance="research-source"),
        FanoutChildConfig(role_instance="research-product"),
        FanoutChildConfig(role_instance="research-technical"),
        FanoutChildConfig(role_instance="research-risk-critic"),
    ]

    route_ids = {
        route["route_id"]
        for route in workflow_route_catalog(config)["routes"]
    }

    assert "research:fixed" not in route_ids
    assert "general:research-fanout" not in route_ids
    assert "research:adaptive-pilot" in route_ids


def test_catalog_only_exposes_registered_reader_general_entries() -> None:
    config = SimpleNamespace(
        roles=[
            SimpleNamespace(
                name="reviewer",
                instance_id="reviewer",
                role_kind="reader",
            ),
            SimpleNamespace(
                name="writer",
                instance_id="writer",
                role_kind="writer",
            ),
        ],
        workflow=SimpleNamespace(
            kind_routes={},
            affinity_lanes={},
            stages=[
                SimpleNamespace(
                    id="architecture-review",
                    trigger="workflow.invoke.requested",
                    topology="fanout_reader",
                    roles=["reviewer"],
                    flow_kind="",
                ),
                SimpleNamespace(
                    id="internal-review",
                    trigger="review.requested",
                    topology="fanout_reader",
                    roles=["reviewer"],
                    flow_kind="",
                ),
                SimpleNamespace(
                    id="unsafe-general-writer",
                    trigger="workflow.invoke.requested",
                    topology="fanout_writer",
                    roles=["writer"],
                    flow_kind="",
                ),
            ],
        ),
    )

    catalog = workflow_route_catalog(config)

    assert [route["route_id"] for route in catalog["routes"]] == [
        "general:architecture-review",
    ]
    assert resolve_workflow_route(
        config,
        "general:architecture-review",
        expected_config_digest=catalog["config_digest"],
    ) is not None
    assert resolve_workflow_route(
        config,
        "general:architecture-review",
        expected_config_digest="sha256:stale",
    ) is None


def test_catalog_does_not_duplicate_delivery_route_for_kind_alias() -> None:
    canonical_route = SimpleNamespace(
        alias="",
        default_tier="default",
        pattern_id="prd-scan",
        tier_routes={},
    )
    config = SimpleNamespace(
        roles=[
            SimpleNamespace(
                name="planner",
                instance_id="planner",
                role_kind="reader",
            ),
        ],
        workflow=SimpleNamespace(
            kind_routes={
                "feat": SimpleNamespace(alias="prd"),
                "prd": canonical_route,
            },
            affinity_lanes={},
            dag=SimpleNamespace(external_triggers=["prd.requested"]),
            stages=[
                SimpleNamespace(
                    id="prd-scan",
                    trigger="prd.requested",
                    topology="fanout_reader",
                    roles=["planner"],
                    flow_kind="prd",
                ),
            ],
        ),
    )

    route_ids = [
        route["route_id"]
        for route in workflow_route_catalog(config)["routes"]
    ]

    assert route_ids == ["delivery:prd:default"]


def test_catalog_hides_blocking_delivery_writer_entry() -> None:
    config = SimpleNamespace(
        roles=[SimpleNamespace(
            name="writer",
            instance_id="writer",
            role_kind="writer",
        )],
        workflow=SimpleNamespace(
            kind_routes={
                "issue": SimpleNamespace(
                    alias="",
                    default_tier="default",
                    pattern_id="issue-lanes-impl",
                    tier_routes={},
                ),
            },
            flow_metadata_by_kind={"issue": {}},
            flow_metadata={},
            affinity_lanes={},
            dag=SimpleNamespace(external_triggers=["issue.requested"]),
            stages=[SimpleNamespace(
                id="issue-lanes-impl",
                trigger="task_map.ready",
                topology="fanout_writer_scoped",
                roles=["writer"],
                flow_kind="issue",
            )],
        ),
    )

    contracts = delivery_route_contracts_for_kind(config, "issue")

    assert len(contracts) == 1
    assert contracts[0]["ok"] is False
    assert "fanout_reader" in contracts[0]["error"]
    assert workflow_route_catalog(config)["routes"] == []


def test_catalog_exposes_light_writer_entry_through_light_adapter() -> None:
    config = SimpleNamespace(
        roles=[SimpleNamespace(
            name="writer",
            instance_id="writer",
            role_kind="writer",
        )],
        workflow=SimpleNamespace(
            kind_routes={
                "issue": SimpleNamespace(
                    alias="",
                    default_tier="default",
                    pattern_id="issue-lanes-impl",
                    tier_routes={},
                ),
            },
            flow_metadata_by_kind={
                "issue": {
                    "topology": "light",
                    "light_entry_trigger": "issue.requested",
                },
            },
            flow_metadata={},
            affinity_lanes={},
            dag=SimpleNamespace(
                external_triggers=["issue.requested", "task_map.ready"],
            ),
            stages=[SimpleNamespace(
                id="issue-lanes-impl",
                trigger="task_map.ready",
                topology="fanout_writer_scoped",
                roles=["writer"],
                flow_kind="issue",
            )],
        ),
    )

    route = workflow_route_catalog(config)["routes"][0]

    assert route["entry_class"] == "light_adapter"
    assert route["entry_trigger"] == "issue.requested"
    assert route["start_adapter"] == "light_delivery_request_submit"
