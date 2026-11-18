"""A targeted re-review must bind the complete change to prior observed inputs."""
from hashlib import sha256

import pytest

from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_text_change import text_change


@pytest.mark.parametrize("case", ["addition", "deletion", "replace", "first_review", "other_run",
                                  "different_port", "unread", "inline_unread", "human_resolution",
                                  "different_package", "different_request", "different_preview", "oversized"])
def test_markdown_delta_requires_the_same_case_and_a_completed_read(tmp_path, case):
    def save(name, body, source_id):
        ref = write_immutable_json_sidecar(tmp_path, body, root="fixtures", kind=name,
                                          schema_version="fixture.v1", created_by="test")
        return {**ref, "source_id": source_id, "artifact_id": name, "allowed_paths": ["$"]}

    prefix, suffix = ("原有前文\n" * 20, "验收交接\n" * 20)
    old = "" if case == "addition" else "旧恢复规则\n"
    new = "" if case == "deletion" else "新恢复规则\n"
    if case == "oversized":
        new *= 9000
    structural = save("tasks.json", {"tasks": ["check"]}, "child-artifact-tasks")
    context = {"schema_version": "plan-rework-context.v1", "rework_of": "reject"}
    if case == "human_resolution":
        context["human_resolution"] = {"choice": "amend"}

    def version(name, text):
        filename = "artifacts/" + name + ".md"
        raw = text.encode()
        (tmp_path / filename).write_bytes(raw)
        doc = {"ref": filename, "sha256": sha256(raw).hexdigest(), "source_id": "child-artifact-doc",
               "artifact_id": "plan.md", "allowed_paths": ["$"]}
        report = {"plan_md": text, "plan_artifact_ref": filename, "plan_ports": [{"body": [1]}]}
        return doc, report, save("report.json", report, "child-result-planner")

    a, _, ar = version("prior", prefix + old + suffix)
    b, report, br = version("current", prefix + new + suffix)
    prior_rows = [dict(source_id=s["source_id"], artifact_id=s["artifact_id"], artifact_sha256=s["sha256"],
                       json_path=p, returned_bytes=100, status="read") for s, p in ((a, "$"), (ar, "$.plan_ports"))]
    observed = [row for row in prior_rows if not (case == "unread" and row["json_path"] == "$"
                                                  or case == "inline_unread" and row["json_path"] == "$.plan_ports")]
    previous = {"sources": [a, ar, structural], "_critic_required_reads": prior_rows,
                "_critic_observed_reads": observed}
    current_structure = {**structural, "sha256": "0" * 64} if case == "different_port" else structural
    trigger = ZfEvent(type="prd.plan.requested", origin="kernel", payload={"rework_of": "reject"})
    events = [ZfEvent(id="reject", type="plan.rejected", actor="operator",
                      payload={"workflow_run_id": "other" if case == "other_run" else "run", "plan_id": "p",
                               "request_event_id": "other" if case == "different_request" else "request"}),
              ZfEvent(id="request", type="plan.approval.requested", origin="kernel", payload={"workflow_run_id": "run",
                      "plan_id": "p", "plan_artifact_digest": "0" * 64 if case == "different_preview" else a["sha256"],
                      "plan_artifact_package_digest": "bad" if case == "different_package" else "pkg"})]
    patch = text_change(tmp_path, sources=[b, br, current_structure, save("context", context, "plan-rework-context")],
                        previous=previous, report=report, trigger=trigger, events=[] if case == "first_review" else events,
                        run="run", package_digest="pkg")
    if case not in {"addition", "deletion", "replace", "inline_unread"}:
        assert patch is None
        return
    assert (patch["before"], patch["after"], patch["start_line"]) == (old, new, 21)
    assert (patch["prior_sha256"], patch["current_sha256"]) == (a["sha256"], b["sha256"])
    assert patch["previously_read_inline_ports"] is (case != "inline_unread")
