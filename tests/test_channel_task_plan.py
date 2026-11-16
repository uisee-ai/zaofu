from zf.web.channel_task_plan import normalize_channel_task_submit_payload
import pytest


def test_nested_target_binding_preserves_explicit_target_and_defer() -> None:
    from zf.web.channel_task_plan import bind_nested_workflow_target
    payload = {"workflow_plan": {"options": [
        {"route_id": "delivery:prd:standard", "parameters": {"target_root": "approved-target"}},
        {"route_id": "delivery:prd:standard"},
        {"mode": "defer"},
    ]}}
    bound = bind_nested_workflow_target(payload, {"target_root": "known-project"})
    options = bound["workflow_plan"]["options"]
    assert options[0]["parameters"]["target_root"] == "approved-target"
    assert options[1]["parameters"]["target_root"] == "known-project"
    assert options[2] == {"mode": "defer"}
    assert "parameters" not in payload["workflow_plan"]["options"][1]


def _authority() -> dict[str, object]:
    return {
        "channel_id": "ch-prd",
        "thread_id": "main",
        "channel_member_id": "product-pm",
        "leader_revision": 1,
        "prd_revision": 2,
        "source_ref": "channels/ch-prd/prd/r2.json",
        "source_digest": "a" * 64,
    }


def test_channel_task_plan_missing_authority_points_to_trusted_entry() -> None:
    payload, details, error = normalize_channel_task_submit_payload({
        "title": "Deliver the confirmed PRD",
    })

    assert payload == {}
    assert details == {}
    assert "missing authority field(s)" in error
    assert "Channel Details" in error
    assert "Create Task from PRD" in error


def test_channel_task_plan_rejects_prose_scope_entries() -> None:
    payload, details, error = normalize_channel_task_submit_payload({
        "title": "Fix the parser",
        "scope": ["检查并修复 src/legacy/grid-parser.js 及其调用方。"],
        "channel_authority": _authority(),
    })

    assert payload == {}
    assert details == {}
    assert "scope must contain only repo-relative paths or globs" in error


def test_channel_task_plan_accepts_path_scope_entries() -> None:
    payload, _details, error = normalize_channel_task_submit_payload({
        "title": "Fix the parser",
        "scope": ["src/**", "tests/repro-grid-parser.mjs"],
        "channel_authority": _authority(),
    })

    assert error == ""
    assert payload["contract"]["scope"] == [
        "src/**",
        "tests/repro-grid-parser.mjs",
    ]


def test_channel_task_plan_preserves_a_pending_workflow_plan() -> None:
    workflow_plan = {
        "header": "Choose delivery route",
        "options": [
            {
                "id": "delivery",
                "label": "Delivery (Recommended)",
                "recommended": True,
                "route_id": "delivery:prd:standard",
                "objective": "Deliver the confirmed PRD.",
                "parameters": {"target_root": "."},
            },
            {
                "id": "defer",
                "label": "Do not start yet",
                "mode": "defer",
            },
        ],
    }

    payload, _details, error = normalize_channel_task_submit_payload({
        "title": "Deliver the confirmed PRD",
        "channel_authority": _authority(),
        "workflow_plan": workflow_plan,
    })

    assert error == ""
    assert payload["workflow_plan"] == workflow_plan
    assert payload["workflow_plan"] is not workflow_plan


def test_channel_task_plan_rejects_non_mapping_workflow_plan() -> None:
    payload, details, error = normalize_channel_task_submit_payload({
        "title": "Deliver the confirmed PRD",
        "channel_authority": _authority(),
        "workflow_plan": ["not", "a", "mapping"],
    })

    assert payload == {}
    assert details == {}
    assert error == "workflow_plan must be a mapping"


@pytest.mark.parametrize("field", ["objective", "acceptance", "acceptance_criteria", "verification", "verification_tiers"])
def test_approved_task_requires_explicit_local_contract(field):
    raw = {"title": "验证取消", "objective": "验证取消不再派发", "acceptance": "取消后无派发",
           "acceptance_criteria": ["取消后无派发"], "verification": "python -m pytest tests/test_cancel.py",
           "verification_tiers": ["runtime"], "channel_prd_scope": "approved_task",
           "channel_authority": _authority()}
    raw.pop(field)
    assert normalize_channel_task_submit_payload(raw)[2] == f"approved_task requires {field}"


def test_unknown_channel_prd_scope_is_rejected():
    assert "channel_prd_scope must be" in normalize_channel_task_submit_payload({
        "title": "test", "channel_prd_scope": "skip", "channel_authority": _authority(),
    })[2]


@pytest.mark.parametrize("explicit", [None, "selected-project"])
def test_prepare_workflow_carries_trusted_target_to_create_action(explicit):
    from zf.web.channel_task_plan import bind_nested_workflow_target
    raw = {"title": "Prepare the confirmed PRD", "prepare_workflow": True,
           "channel_authority": _authority()}
    if explicit:
        raw["target_root"] = explicit
    bound = bind_nested_workflow_target(raw, {"target_root": "current-project"})
    payload, _, error = normalize_channel_task_submit_payload(bound)
    assert not error
    assert payload["target_root"] == (explicit or "current-project")
    assert payload["prepare_workflow"] is True
    assert "workflow_plan" not in payload
    assert raw.get("target_root") == explicit


@pytest.mark.parametrize("target", [False, {}, ["path"], " "])
def test_channel_task_rejects_invalid_target_root(target):
    from zf.web.channel_task_plan import bind_nested_workflow_target
    bound = bind_nested_workflow_target({
        "title": "Prepare", "channel_authority": _authority(), "target_root": target,
        "prepare_workflow": True,
    }, {"target_root": "current-project"})
    assert normalize_channel_task_submit_payload(bound)[2] == "target_root must be a non-empty string"


def test_flat_channel_constraints_are_preserved_without_inventing_approval():
    for conditions in [[], ["完整 Plan 需 Owner 人工审批后才允许实施", "Python 3.11"]]:
        payload, _, error = normalize_channel_task_submit_payload({
            "title": "Deliver PRD", "channel_authority": _authority(), "constraints": conditions,
        })
        assert not error
        assert payload["contract"]["constraints"] == conditions


@pytest.mark.parametrize("conditions", ["requires approval", {"approval": True}, [{"text": "requires approval"}], [42]])
def test_flat_channel_constraints_reject_unsupported_shapes(conditions):
    payload, _, error = normalize_channel_task_submit_payload({
        "title": "Deliver PRD", "channel_authority": _authority(), "constraints": conditions,
    })
    assert payload == {}
    assert "constraints must be a list of strings" in error
