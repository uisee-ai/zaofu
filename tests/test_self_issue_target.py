from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ExternalIssueIngressConfig, SelfIssueConfig, ZfConfig
from zf.core.config.self_issue_policy import (
    inject_self_issue_policy,
    inherit_workspace_self_issue_config,
)
from zf.runtime.self_issue_target import (
    SELF_ISSUE_TARGET_ROOT,
    resolve_self_issue_target_root,
    zaofu_v7_root,
)


def _locked_policy() -> SelfIssueConfig:
    return SelfIssueConfig(
        enabled=True,
        target_locked=True,
        target_project="owner/zaofu-self-issue",
        ingress=ExternalIssueIngressConfig(target_root="."),
    )


def test_locked_publication_target_keeps_current_project_source_root(tmp_path: Path) -> None:
    assert resolve_self_issue_target_root(tmp_path, _locked_policy()) == tmp_path.resolve()


def test_explicit_zaofu_source_mode_targets_running_checkout(tmp_path: Path) -> None:
    policy = SelfIssueConfig(
        enabled=True,
        target_locked=True,
        target_project="owner/zaofu-self-issue",
        source_mode="zaofu_self_issue",
    )
    assert resolve_self_issue_target_root(tmp_path, policy) == zaofu_v7_root()


def test_unconfigured_self_issue_keeps_isolated_caller_root(tmp_path: Path) -> None:
    assert resolve_self_issue_target_root(tmp_path, SelfIssueConfig()) == tmp_path.resolve()


def test_workspace_inheritance_and_project_init_use_symbolic_zaofu_root() -> None:
    workspace = ZfConfig(self_issue=_locked_policy())
    project = ZfConfig(self_issue=SelfIssueConfig())
    inherited = inherit_workspace_self_issue_config(project, workspace)
    assert inherited is not None
    assert inherited.self_issue.ingress.target_root == SELF_ISSUE_TARGET_ROOT

    documents = [{"kind": "ZfConfig", "spec": {"project": {"name": "child"}}}]
    generated = inject_self_issue_policy(documents, _locked_policy())
    config = next(document for document in generated if document.get("kind") == "ZfConfig")
    assert config["spec"]["self_issue"]["ingress"]["target_root"] == (
        SELF_ISSUE_TARGET_ROOT
    )


def test_project_init_reconciles_stale_managed_external_issue_workflow() -> None:
    documents = [
        {
            "apiVersion": "zaofu.dev/v1",
            "kind": "Workflow",
            "metadata": {"name": "external-issue-triage/v1"},
            "spec": {
                "entry": "external-issue-triage",
                "tasks": [{
                    "name": "external-issue-triage",
                    "criteria": {"instructions": ["otherwise report needs_info"]},
                }],
            },
        },
        {"kind": "ZfConfig", "spec": {"project": {"name": "child"}}},
    ]

    generated = inject_self_issue_policy(documents, _locked_policy())

    workflows = [item for item in generated if item.get("kind") == "Workflow"]
    assert len(workflows) == 1
    instructions = workflows[0]["spec"]["tasks"][0]["criteria"]["instructions"]
    assert any("Minimize user dependence" in item for item in instructions)
    assert any("unverified_candidate" in item for item in instructions)
    assert any("MUST use ready_to_fix" in item for item in instructions)
    assert all("otherwise report needs_info" not in item for item in instructions)
