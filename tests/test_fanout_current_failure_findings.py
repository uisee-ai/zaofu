"""Historical reader failures remain audit evidence, not current aggregate gaps."""
from types import SimpleNamespace

from zf.core.events import EventLog, EventWriter
from zf.runtime.fanout import FanoutManifestProjector
from zf.runtime.fanout_failure_findings import fanout_failure_findings, fanout_failure_recovery


def test_retried_siblings_only_project_current_failed_attempt(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    writer.emit("fanout.started", payload={"fanout_id": "f", "topology": "fanout_reader"})
    for child in ("product", "tech"):
        writer.emit("fanout.child.dispatched", payload={"fanout_id": "f", "child_id": child, "run_id": child + "-old"})
        writer.emit("fanout.child.failed", payload={"fanout_id": "f", "child_id": child, "run_id": child + "-old",
            "findings": [{"message": child + " stale failure"}],
            "failure_class": "dependency_blocked", "rework_scope": "plan_ports"})
        writer.emit("fanout.child.dispatched", payload={"fanout_id": "f", "child_id": child, "run_id": child + "-new"})
    writer.emit("fanout.child.completed", payload={"fanout_id": "f", "child_id": "product", "run_id": "product-new"})
    writer.emit("fanout.child.failed", payload={"fanout_id": "f", "child_id": "tech", "run_id": "tech-new",
        "findings": [{"message": "current tech failure"}], "failure_kind": "infra"})
    manifest = FanoutManifestProjector(tmp_path).rebuild("f", log.read_all())
    owner = SimpleNamespace(event_log=log, _fanout_child_payloads=lambda _: [])
    before = (tmp_path / "events.jsonl").read_bytes()
    assert {f["message"] for f in fanout_failure_findings(owner, manifest)} == {"current tech failure"}
    assert fanout_failure_recovery(owner, manifest) == {}
    assert (tmp_path / "events.jsonl").read_bytes() == before
