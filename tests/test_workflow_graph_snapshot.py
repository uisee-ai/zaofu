from zf.core.config.schema import RoleConfig, WorkflowConfig, WorkflowStageConfig, ZfConfig
from zf.runtime.workflow_graph_snapshot import compile_proposal_graph
import pytest


@pytest.mark.parametrize('family,kind', [('IssueFlow', 'issue'), ('PrdFlow', 'prd'), ('RefactorFlow', 'refactor'), ('Workflow', 'workflow')])
def test_graph_uses_explicit_flow_kind_instead_of_stage_name_prefix(family, kind):
    other = 'prd' if kind == 'issue' else 'issue'
    config = ZfConfig(roles=[
        RoleConfig(name="product-reader", instance_id="product-reader", flow_kind=kind),
        RoleConfig(name="issue-reader", instance_id="issue-reader", flow_kind=other),
    ], workflow=WorkflowConfig(stages=[
        WorkflowStageConfig(id="customer-defined-stage", flow_kind=kind, roles=["product-reader"], trigger="product.requested"),
        WorkflowStageConfig(id="other-stage", flow_kind=other, roles=["issue-reader"], trigger="issue.requested"),
    ]))
    graph = compile_proposal_graph(config, family)
    ids = {n["node_id"] for n in graph["nodes"]}
    assert "stage:customer-defined-stage" in ids and "role:product-reader" in ids
    assert "stage:other-stage" not in ids and "role:issue-reader" not in ids
    assert "event:product.requested" in ids
    assert len(config.workflow.stages) == 2 and len(config.roles) == 2
