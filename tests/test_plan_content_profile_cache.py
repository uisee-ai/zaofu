"""A new server must not reuse an old inferred-complete Plan projection."""

from zf.core.events.model import ZfEvent
from zf.web.projections import workflow_workbench_cache as cache
from tests.test_workflow_workbench_cache import oracle, submit, url
from tests.test_workflow_preview_review import setup  # noqa: F401


def test_old_complete_claim_cache_cannot_survive_projection_update(setup, monkeypatch):
    client, service, binding = setup
    submit(service, binding)
    plan = service.project_root / "docs/content-plan.md"
    plan.write_text("# Reviewable plan\n\n## PRD\nScope\n## SDD\nContracts pending\n")
    service.writer.append(ZfEvent(type="plan.approval.requested", payload={
        "workflow_run_id": "run-a", "pdd_id": "TASK-42", "plan_id": "PLAN-CONTENT",
        "plan_artifact_ref": str(plan),
    }))
    build = cache.build_sdlc_graph

    def old_graph(inputs):
        graph = build(inputs)
        graph["plans"][0]["content_profile"] = "derived_plan_full"
        return graph

    with monkeypatch.context() as old:
        old.setattr(cache, "CACHE_VERSION", "workflow-workbench.v6")
        old.setattr(cache, "build_sdlc_graph", old_graph)
        stale = client.get(url(binding))
    assert stale.json()["plans"][0]["content_profile"] == "derived_plan_full"
    latest = client.get(url(binding), headers={"If-None-Match": stale.headers["etag"]})
    assert latest.status_code == 200
    assert latest.headers["etag"] != stale.headers["etag"]
    assert latest.json()["plans"][0]["content_profile"] == "derived_plan"
    assert latest.json() == oracle(client, service, binding)
    assert client.get(url(binding), headers={"If-None-Match": latest.headers["etag"]}).status_code == 304
    direct = client.get("/api/projects/default/plans/PLAN-CONTENT/preview")
    assert direct.status_code == 200
    assert direct.json()["content_profile"] == "derived_plan"
    assert direct.json()["status"] == "pending"
