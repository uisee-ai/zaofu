"""The operator sees only a consented reply from the currently selected provider turn."""
from dataclasses import replace
from unittest.mock import patch

import pytest
import yaml
from tests.test_stage_team_observations import seed_task_receipts
from tests.test_stage_team_workbench import approve, plan_case
from tests.test_transport_stream_json import FakeResultMessage, _make_fake_query
from tests.test_workflow_registered_controller import assembly
from zf.core.config.loader import load_config
from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.transport import DispatchContext
from zf.runtime.transport_stream_json import StreamJsonTransport
from zf.runtime.workflow_requests import mark_workflow_request

PUBLIC = "Reviewed the current interface contract."


def _submitted_project(case, publish):
    base, state, _, _, writer = case
    documents = list(yaml.safe_load_all(base.read_text()))
    cfg = next(d["spec"] for d in documents if d["kind"] == "ZfConfig")
    cfg.setdefault("observability", {})["workflow_session_excerpts"] = publish
    base.write_text(yaml.safe_dump_all(documents))
    req = plan_case(case)
    approve(case, req)
    mark_workflow_request(state, req, status="submitted", actor="owner", writer=writer, run_id=req)
    return req


def seed_member_session(case, *, publish=True, answer=PUBLIC, private_prompt="private instruction"):
    base, state, _, _, _ = case
    req = _submitted_project(case, publish)
    seed_task_receipts(case, req)
    role = "verify-lane-1"
    context = DispatchContext(instance_id=role, role_name=role, task_id="T-B", operation_id="op-T-B",
        attempt_id="attempt-T-B", lease_id="lease-T-B", dispatch_id="dispatch-T-B", run_id=req, task_pipeline_stage="verify")
    registry = RoleSessionRegistry(state / "stream-sessions.yaml", project_root=str(base.parent))
    sid = str(registry.get_or_create(role))
    stream = StreamJsonTransport(state, registry, cwd=base.parent, workflow_session_excerpts=publish,
        query_fn=_make_fake_query([FakeResultMessage(session_id=sid, result=answer)]))
    stream.spawn(RoleConfig(name=role, instance_id=role, backend="claude-code"), ["claude"], cwd=base.parent)
    with patch("zf.runtime.transport_stream_json.purge_stale_claude_session_lock", return_value={}):
        stream.send_task(role, base, private_prompt, context=context)
    return {"request": req, "run": req, "operation": "op-T-B", "attempt": "attempt-T-B", "instance": role, "task": "T-B"}


def test_no_capture_without_explicit_consent(assembly):
    _, state, _, _, writer = assembly
    seed_member_session(assembly, publish=False)
    close, = [e for e in writer.event_log.read_all() if e.type == "provider.turn.closed"]
    assert "operator_excerpt_ref" not in close.payload
    assert not (state / "artifacts/workflow-turn-excerpts").exists()


@pytest.mark.parametrize("fault", ["foreign_session", "ambiguous_result", "no_final_result", "failed",
                                  "missing_lease", "missing_dispatch", "missing_attempt"])
def test_transport_never_publishes_unsupported_or_unbound_content(tmp_path, fault):
    from tests.test_transport_stream_json import FakeAssistantMessage, FakeTextBlock
    from zf.runtime.stream_turn_lifecycle import run_stream_turn

    ctx = DispatchContext(run_id="r", operation_id="op", attempt_id="a", lease_id="lease", dispatch_id="d", instance_id="member")
    final = FakeResultMessage(result="This content is not released.", session_id="s")
    messages = [FakeAssistantMessage(content=[FakeTextBlock("private working notes")]), final]
    if fault == "foreign_session":
        final.session_id = "other"
    elif fault == "ambiguous_result":
        messages.append(final)
    elif fault == "no_final_result":
        messages.pop()
    elif fault == "failed":
        final.is_error = True
    else:
        field = {"missing_lease":"lease_id", "missing_dispatch":"dispatch_id", "missing_attempt":"attempt_id"}[fault]
        ctx = replace(ctx, **{field:None})
    run_stream_turn(tmp_path, ctx, "s", lambda:(messages,"ok"), publish_excerpt=True)
    assert not (tmp_path/"artifacts/workflow-turn-excerpts").exists()


def test_disclosure_setting_is_default_off_and_requires_boolean(tmp_path):
    from tests.test_workflow_proposal import _config
    from zf.core.config.loader import ConfigError
    base = tmp_path/"zf.yaml"
    _config(base)
    assert load_config(base).observability.workflow_session_excerpts is False
    docs = list(yaml.safe_load_all(base.read_text()))
    cfg = next(doc["spec"] for doc in docs if doc["kind"] == "ZfConfig")
    cfg["observability"] = {"workflow_session_excerpts": "yes"}
    base.write_text(yaml.safe_dump_all(docs))
    with pytest.raises(ConfigError,match="boolean"):
        load_config(base)
