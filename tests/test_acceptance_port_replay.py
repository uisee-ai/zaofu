"""Plan package replay uses the same acceptance identity binding as admission."""
from copy import deepcopy

import pytest

from tests.test_plan_acceptance_rework import _prepare, _port
from zf.core.events import EventLog, EventWriter
from zf.runtime.plan_artifact_package import PlanArtifactPackageError, admit_plan_artifact_package_for_payload
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _admit(tmp_path, unbound=False):
    args, previous_event, payload, previous = _prepare(tmp_path)
    spec = deepcopy(hydrate_sidecar_ref(args["state_dir"], _port(previous)).payload)
    if unbound:
        for key in ("schema_version", "workflow_run_id", "flow_kind", "plan_revision", "task_map_generation"):
            spec.pop(key, None)
    payload["plan_ports"] = [{"logical_name": "product_acceptance_spec", "body": spec}]
    log = EventLog(args["state_dir"] / "events.jsonl")
    writer = EventWriter(log)
    writer.append(previous_event)
    first = admit_plan_artifact_package_for_payload(
        **args, payload=payload, events=log.read_all(), event_writer=writer,
    )
    return args, payload, log, writer, first


@pytest.mark.parametrize("unbound", [False, True])
def test_actual_admission_can_replay_original_acceptance_port(tmp_path, unbound):
    args, payload, log, writer, first = _admit(tmp_path, unbound)
    before = len(log.read_all())
    second = admit_plan_artifact_package_for_payload(
        **args, payload=payload, events=log.read_all(), event_writer=writer,
    )
    assert second["plan_artifact_package_ref"] == first["plan_artifact_package_ref"]
    assert second["plan_artifact_package_digest"] == first["plan_artifact_package_digest"]
    assert len(log.read_all()) == before


@pytest.mark.parametrize("change", ["assertion", "mandatory", "owner", "qualification"])
def test_acceptance_semantic_changes_still_require_a_new_generation(tmp_path, change):
    args, payload, log, writer, _ = _admit(tmp_path)
    spec = payload["plan_ports"][0]["body"]
    if change == "assertion":
        spec["user_journeys"][0]["assertions"][0]["statement"] = "Different requirement"
    elif change == "mandatory":
        spec["user_journeys"][0]["mandatory"] = False
    elif change == "owner":
        spec["assembly_owner"] = "Other owner"
    else:
        spec["provider_qualification"]["required_for_goal"] = True
    with pytest.raises(PlanArtifactPackageError, match="differs from the admitted package"):
        admit_plan_artifact_package_for_payload(
            **args, payload=payload, events=log.read_all(), event_writer=writer,
        )
