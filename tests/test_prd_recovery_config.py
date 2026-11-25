"""Verify public recovery settings at the project config boundary."""

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.workflow.flow_metadata import flow_metadata_for


def compiled(tmp_path, prd, *, issue=False, workflow=None):
    def doc(kind, spec):
        return {"apiVersion": "zaofu.dev/v1", "kind": kind, "spec": spec}

    docs = [doc("IssueFlow", {"lanes": 1})] if issue else []
    docs += [
        doc("PrdFlow", {"lanes": 1, **prd}),
        doc("ZfConfig", {"project": {"name": "test"}, "workflow": workflow or {}}),
    ]
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump_all(docs))
    return load_config(path)


@pytest.mark.parametrize("actor", ["planner", "verify-lane-0"])
def test_prd_profiles_pin_real_instance_bindings(tmp_path, actor):
    from zf.core.config.artifact_worker_cgroups import artifact_worker_cgroups

    cfg = compiled(
        tmp_path,
        {"milestoneHandoff": True, "artifactWorkerCgroups": {actor: "/test/worker"}},
    )
    assert flow_metadata_for(cfg, "prd")["milestone_handoff"] is True
    assert artifact_worker_cgroups(cfg) == {actor: "/test/worker"}


def test_prd_host_grants_and_milestones_stay_scoped(tmp_path):
    from zf.core.config.artifact_worker_cgroups import artifact_worker_cgroups

    cfg = compiled(
        tmp_path,
        {
            "milestoneHandoff": True,
            "artifactWorkerCgroups": {"verify-lane-0": "/test/worker"},
        },
        issue=True,
    )
    assert flow_metadata_for(cfg, "prd")["milestone_handoff"] is True
    assert "milestone_handoff" not in flow_metadata_for(cfg, "issue")
    assert not flow_metadata_for(cfg, "issue").get("artifact_worker_cgroups")
    assert artifact_worker_cgroups(cfg) == {"prd-verify-lane-0": "/test/worker"}


def test_private_project_grants_survive_template_defaults(tmp_path):
    from zf.core.config.artifact_worker_cgroups import artifact_worker_cgroups

    cfg = compiled(
        tmp_path,
        {"milestoneHandoff": True},
        workflow={
            "_flow_metadata": {
                "milestone_handoff": False,
                "artifact_worker_cgroups": {"planner": "/test/private"},
            }
        },
    )
    assert flow_metadata_for(cfg)["milestone_handoff"] is False
    assert artifact_worker_cgroups(cfg) == {"planner": "/test/private"}


@pytest.mark.parametrize(
    "spec",
    [
        {"milestoneHandoff": "yes"},
        {"milestoneHandoff": True, "milestone_handoff": False},
        {"artifactWorkerCgroups": {}, "artifact_worker_cgroups": {}},
        {"artifactWorkerCgroups": None},
        {"artifactWorkerCgroups": {"nonexistent": "/test/missing"}},
        {"artifactWorkerCgroups": {"verify-lane-0": "relative/path"}},
        {"artifactWorkerCgroups": {"verify-lane-0": "/../escape"}},
        {"artifactWorkerCgroups": {"verify-lane-0": "/"}},
        {
            "artifactWorkerCgroups": {
                "verify-lane-0": "/test/worker",
                "dev-lane-0": "/test/worker",
            }
        },
        {"artifactWorkerCgroups": {"planner": "/test", "dev-lane-0": "/test/worker"}},
    ],
)
def test_invalid_or_shared_configuration_is_refused(tmp_path, spec):
    with pytest.raises(ConfigError, match=r"(artifactWorkerCgroups|milestoneHandoff)"):
        compiled(tmp_path, spec)


def test_cgroup_uniqueness_applies_across_flow_families(tmp_path):
    with pytest.raises(ConfigError, match="overlapping worker scopes"):
        compiled(
            tmp_path,
            {"artifactWorkerCgroups": {"verify-lane-0": "/test/one"}},
            issue=True,
            workflow={
                "_flow_metadata_by_kind": {
                    "issue": {
                        "artifact_worker_cgroups": {
                            "issue-fix-lane-0": "/test/one/child"
                        },
                    }
                }
            },
        )
