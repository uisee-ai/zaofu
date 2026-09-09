from zf.web.channel_task_plan import normalize_channel_task_submit_payload


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
