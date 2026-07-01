"""Tests for candidate-level rework self-healing planning."""

from __future__ import annotations

from types import SimpleNamespace

from zf.runtime.candidate_rework import plan_candidate_rework


def _ev(etype, payload=None, task_id=None, eid="", corr=""):
    return SimpleNamespace(
        type=etype, payload=payload or {}, task_id=task_id, id=eid, correlation_id=corr
    )


def test_candidate_rejection_plans_retrigger_with_feedback():
    events = [
        _ev("review.child.failed", {"child_id": "review-contract", "reason": "bad npm metadata", "trace_id": "t1"}, eid="c1"),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1", "trace_id": "t1"}, eid="r1", corr="t1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    p = plans[0]
    assert p.action == "retrigger"
    assert p.pdd_id == "CJMIN-1"          # derived from target_ref tail
    assert p.attempt == 1
    assert p.source_event_id == "r1"
    assert "review-contract: bad npm metadata" in p.feedback


def test_verify_failed_plans_candidate_retrigger_with_feedback():
    events = [
        _ev("verify.child.failed", {
            "child_id": "verify-lane-4-web-tui",
            "reason": "fixture contract mismatch: tool_id missing",
            "trace_id": "t1",
        }, eid="vc1", corr="t1"),
        _ev("verify.failed", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
        }, eid="vf1", corr="t1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    p = plans[0]
    assert p.action == "retrigger"
    assert p.source_event_type == "verify.failed"
    assert p.source_event_id == "vf1"
    assert "contract_fixture_gap" in p.failure_categories
    assert p.rework_summary["source_event_type"] == "verify.failed"


def test_verify_failed_payload_findings_become_rework_feedback():
    events = [
        _ev("verify.failed", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
            "findings": [
                {
                    "task_id": "CJMIN-GATEWAY-001",
                    "category": "parity_gap",
                    "message": "delivery chunk offsets must be UTF-16 based",
                    "verification_command": "npm test -- gateway",
                },
            ],
        }, eid="vf1", corr="t1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    feedback = plans[0].feedback
    assert feedback == (
        "CJMIN-GATEWAY-001 / parity_gap: "
        "delivery chunk offsets must be UTF-16 based "
        "(verify: npm test -- gateway)",
    )
    assert "parity_gap" in plans[0].failure_categories


def test_verify_failed_parity_gap_preserves_structured_gap_tasks():
    gap_task = {
        "task_id": "CANGJIE-WEB-GAP-001",
        "affinity_tag": "web-tui",
        "owner_role": "dev",
        "allowed_paths": ["web/**", "packages/web-adapter/**"],
        "acceptance_criteria": ["Cangjie WebChat uses Cangjie runtime"],
        "verification": ["npm run test -- web"],
        "source_refs": ["hermes-agent/web"],
    }
    events = [
        _ev("verify.failed", {
            "target_ref": "cand/CANGJIE-1",
            "trace_id": "t1",
            "findings": [
                {
                    "task_id": "CANGJIE-WEB-001",
                    "category": "parity_gap",
                    "message": "Web dashboard still uses a demo bridge",
                    "verification_command": "npm run test -- web",
                    "gap_task": gap_task,
                },
            ],
        }, eid="vf1", corr="t1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    plan = plans[0]
    assert plan.action == "retrigger"
    assert plan.gap_tasks == (gap_task,)
    assert plan.failed_task_ids == ("CANGJIE-WEB-001",)
    assert plan.rework_summary["gap_tasks"] == [gap_task]
    assert "parity_gap" in plan.failure_categories


def test_repeated_verify_contract_gap_replans_to_refresh_contract_freeze():
    events = [
        _ev("verify.failed", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
        }, eid="vf1", corr="t1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "vf1",
            "rework_source": "verify.failed",
            "rework_attempt": 1,
        }, eid="rw1", corr="t1"),
        _ev("verify.child.failed", {
            "child_id": "verify-lane-4-web-tui",
            "reason": (
                "path completion fixture is shape-only; Python reference returns "
                "extra @staged/@url:/@git: items"
            ),
            "trace_id": "t1",
        }, eid="vc2", corr="t1"),
        _ev("verify.failed", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
        }, eid="vf2", corr="t1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    p = plans[0]
    assert p.action == "replan"
    assert p.attempt == 2
    assert p.classification == "contract_freeze_gap"
    assert set(p.failure_categories) >= {"contract_fixture_gap", "parity_gap"}
    assert p.rework_summary["action"] == "replan"


def test_infra_only_verify_failure_does_not_consume_rework_budget():
    events = [
        _ev("fanout.started", {
            "fanout_id": "fanout-verify-real-1",
            "pdd_id": "CJMIN-1",
        }, eid="fs1"),
        _ev("verify.child.failed", {
            "fanout_id": "fanout-verify-real-1",
            "child_id": "verify-lane-4-web-tui",
            "reason": "fixture contract mismatch: tool_id missing",
            "trace_id": "t1",
        }, eid="vc1", corr="t1"),
        _ev("verify.failed", {
            "fanout_id": "fanout-verify-real-1",
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t1",
        }, eid="vf1", corr="t1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "vf1",
            "rework_source": "verify.failed",
            "rework_attempt": 1,
        }, eid="rw1", corr="t1"),
        _ev("fanout.started", {
            "fanout_id": "fanout-verify-infra",
            "pdd_id": "CJMIN-1",
        }, eid="fs2"),
        _ev("fanout.child.failed", {
            "fanout_id": "fanout-verify-infra",
            "child_id": "verify-lane-0-assembly",
            "reason": (
                "refusing to send task to verify-lane-0: pane is not "
                "running an agent process (current_command=bash)"
            ),
            "trace_id": "t2",
        }, eid="ifc1", corr="t2"),
        _ev("verify.failed", {
            "fanout_id": "fanout-verify-infra",
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t2",
        }, eid="vf-infra", corr="t2"),
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CJMIN-1",
            "rework_of": "vf-infra",
            "rework_source": "verify.failed",
            "rework_attempt": 2,
        }, eid="rw-infra", corr="t2"),
        _ev("fanout.started", {
            "fanout_id": "fanout-verify-real-2",
            "pdd_id": "CJMIN-1",
        }, eid="fs3"),
        _ev("verify.child.failed", {
            "fanout_id": "fanout-verify-real-2",
            "child_id": "verify-lane-1-pi-core",
            "reason": "fixture contract mismatch: transcript missing",
            "trace_id": "t3",
        }, eid="vc3", corr="t3"),
        _ev("verify.failed", {
            "fanout_id": "fanout-verify-real-2",
            "target_ref": "cand/CJMIN-1",
            "trace_id": "t3",
        }, eid="vf3", corr="t3"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].source_event_id == "vf3"
    assert plans[0].attempt == 2
    assert plans[0].action == "replan"


def test_already_handled_rejection_is_skipped():
    events = [
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r1"),
        _ev("task_map.ready", {"pdd_id": "CJMIN-1", "rework_of": "r1", "rework_attempt": 1}),
    ]
    assert plan_candidate_rework(events) == []


def test_escalates_at_max_attempts():
    events = [
        _ev("task_map.ready", {"pdd_id": "CJMIN-1", "rework_of": "rA", "rework_attempt": 1}),
        _ev("task_map.ready", {"pdd_id": "CJMIN-1", "rework_of": "rB", "rework_attempt": 2}),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r3"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "escalate"
    assert plans[0].attempt == 3


def test_duplicate_rework_markers_count_once_for_attempt_budget():
    events = [
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r1",
            "rework_source": "review.rejected",
            "rework_attempt": 1,
        }, eid="rw1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r1",
            "rework_source": "review.rejected",
            "rework_attempt": 1,
        }, eid="rw1-dup"),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r2"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].attempt == 2


def test_review_rejected_uses_source_budget_after_runtime_rework_history():
    events = [
        _ev("fanout.child.failed", {
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "reason": "stale_task_map",
            "suggested_action": "use_latest_product_delivery_wave_ready",
        }, eid="stale-1", corr="tr-1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "stale-1",
            "rework_source": "fanout.child.failed",
            "rework_attempt": 1,
        }, eid="rw-stale-1"),
        _ev("integration.failed", {
            "pdd_id": "CJMIN-1",
            "target_ref": "main",
            "trace_id": "tr-1",
        }, eid="int-1", corr="tr-1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "int-1",
            "rework_source": "integration.failed",
            "rework_attempt": 1,
        }, eid="rw-int-1"),
        _ev("review.rejected", {
            "target_ref": "cand/CJMIN-1",
            "trace_id": "tr-1",
        }, eid="review-1", corr="tr-1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].source_event_id == "review-1"
    assert plans[0].attempt == 1


def test_escalation_marker_handles_rejection_idempotently():
    events = [
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r1",
            "rework_source": "review.rejected",
            "rework_attempt": 1,
        }, eid="rw1"),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r2"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r2",
            "rework_source": "review.rejected",
            "rework_attempt": 2,
        }, eid="rw2"),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r3"),
        _ev("human.escalate", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r3",
            "rework_source": "review.rejected",
            "rework_attempt": 3,
        }, eid="esc1"),
    ]

    assert plan_candidate_rework(events, max_attempts=2) == []


def test_integration_failure_plans_bounded_rework():
    # integration.failed is candidate-level (task_id=None): the kernel sweep must
    # own its recovery (bounded, same pdd) instead of an agent re-route that
    # mints an incrementing pdd each cycle.
    events = [
        _ev("integration.failed", {"target_ref": "cand/CJMIN-1", "pdd_id": "CJMIN-1"}, eid="i1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].pdd_id == "CJMIN-1"


def test_integration_failure_resolves_pdd_from_fanout_started():
    # Real writer fanout failures report target_ref="main"; that is the branch,
    # not the candidate PDD. Recovery must follow fanout_id back to the started
    # manifest identity so task_map.ready is re-emitted for the original PDD.
    events = [
        _ev("fanout.started", {
            "fanout_id": "fanout-impl-1",
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "target_ref": "main",
        }, eid="fs1", corr="tr-1"),
        _ev("integration.failed", {
            "fanout_id": "fanout-impl-1",
            "target_ref": "main",
            "trace_id": "tr-1",
        }, eid="i1", corr="tr-1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].pdd_id == "CJMIN-1"


def test_wrong_pdd_rework_does_not_mark_integration_failure_handled():
    # A previous bad recovery emitted task_map.ready{pdd_id=main,rework_of=i1}.
    # That must not suppress the corrected rework for the fanout's real PDD.
    events = [
        _ev("fanout.started", {
            "fanout_id": "fanout-impl-1",
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "target_ref": "main",
        }, eid="fs1", corr="tr-1"),
        _ev("integration.failed", {
            "fanout_id": "fanout-impl-1",
            "target_ref": "main",
            "trace_id": "tr-1",
        }, eid="i1", corr="tr-1"),
        _ev("task_map.ready", {
            "pdd_id": "main",
            "rework_of": "i1",
            "rework_attempt": 1,
        }, eid="bad-rework", corr="tr-1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].pdd_id == "CJMIN-1"


def test_stale_task_map_child_failure_plans_retrigger():
    events = [
        _ev("fanout.child.failed", {
            "fanout_id": "fanout-impl-1",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "reason": "stale_task_map",
            "suggested_action": "use_latest_product_delivery_wave_ready",
        }, eid="f1", corr="tr-1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].pdd_id == "CJMIN-1"
    assert plans[0].source_event_type == "fanout.child.failed"
    assert "dev-lane-0-CJMIN-PI-CORE-001: stale_task_map" in plans[0].feedback


def test_latest_handled_rejection_suppresses_older_same_pdd():
    events = [
        _ev("fanout.child.failed", {
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "reason": "stale_task_map",
        }, eid="f1", corr="tr-1"),
        _ev("fanout.child.failed", {
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "reason": "stale_task_map",
        }, eid="f2", corr="tr-1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "f2",
            "rework_attempt": 1,
        }, eid="rw1", corr="tr-1"),
    ]

    assert plan_candidate_rework(events, max_attempts=2) == []


def test_stale_task_map_uses_runtime_recovery_attempt_budget():
    events = [
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "r1",
            "rework_source": "review.rejected",
            "rework_attempt": 1,
        }, eid="rw1"),
        _ev("test.failed", {"target_ref": "cand/CJMIN-1"}, eid="t1"),
        _ev("task_map.ready", {
            "pdd_id": "CJMIN-1",
            "rework_of": "t1",
            "rework_source": "test.failed",
            "rework_attempt": 2,
        }, eid="rw2"),
        _ev("fanout.child.failed", {
            "pdd_id": "CJMIN-1",
            "trace_id": "tr-1",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "reason": "stale_task_map",
            "suggested_action": "use_latest_product_delivery_wave_ready",
        }, eid="f1", corr="tr-1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].attempt == 1


def test_task_level_rejection_ignored():
    # rejection WITH a task_id is the per-task path, not candidate-level
    events = [_ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, task_id="T-1", eid="r1")]
    assert plan_candidate_rework(events) == []


def test_multiple_rejections_same_pdd_plan_once():
    # two review.rejected for the same candidate must yield ONE rework,
    # not double-trigger the writer fanout.
    events = [
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r1"),
        _ev("review.rejected", {"target_ref": "cand/CJMIN-1"}, eid="r2"),
    ]
    plans = plan_candidate_rework(events)
    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].source_event_id == "r2"  # latest rejection


# --- doc 78 W2: plan-level failures plan a replan, not a re-implement ---

def test_integration_conflict_plans_replan_not_retrigger():
    # A cherry-pick conflict is a plan-level (decomposition) error: re-implementing
    # the same task_map repeats it. The plan must re-decompose, not re-implement.
    events = [
        _ev("integration.failed", {
            "target_ref": "cand/CJMIN-1", "pdd_id": "CJMIN-1",
            "status": "conflict", "conflict_files": ["packages/state/db.ts"],
        }, eid="i1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"


def test_design_marker_review_rejection_plans_replan():
    events = [
        _ev("review.rejected", {
            "target_ref": "cand/CJMIN-1",
            "reason": "the architecture/contract decomposition is wrong",
        }, eid="r1"),
    ]
    plans = plan_candidate_rework(events)
    assert plans[0].action == "replan"


def test_candidate_conflict_plans_replan():
    # The actual cherry-pick conflict event (candidate.conflict, task_id=None)
    # must be recovered by the sweep and routed to replan, not ignored.
    events = [
        _ev("candidate.conflict", {
            "target_ref": "cand/CJMIN-1", "pdd_id": "CJMIN-1",
            "status": "conflict", "conflict_files": ["packages/state/db.ts"],
        }, eid="cc1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"


def test_integration_authoritative_verification_scope_gap_plans_replan():
    events = [
        _ev("integration.failed", {
            "target_ref": "main",
            "pdd_id": "CJMIN-R37",
            "trace_id": "tr-r37",
            "findings": [
                {
                    "child_id": "dev-lane-3-CJMIN-PROVIDERS-FUNCTION-CALLING-001",
                    "reason": "authoritative_verification_unrunnable_inside_allowed_scope",
                    "summary": (
                        "Focused provider checks pass, but authoritative "
                        "verification requires workspace/package files outside "
                        "this slice's allowed_paths."
                    ),
                }
            ],
        }, eid="int-scope", corr="tr-r37"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)

    assert len(plans) == 1
    assert plans[0].action == "replan"
    assert plans[0].classification == "design_issue"
    assert plans[0].source_event_type == "integration.failed"


def test_replan_escalates_at_max_attempts():
    # The dangerous loop: replan events advance the attempt counter, so a
    # decomposition that cannot be fixed must ESCALATE at the cap, not emit yet
    # another replan forever. Guards the if/elif precedence (escalate before
    # the replan classification branch).
    events = [
        _ev("orchestrator.replan_requested", {"pdd_id": "CJMIN-1", "rework_of": "x1", "rework_attempt": 1}),
        _ev("orchestrator.replan_requested", {"pdd_id": "CJMIN-1", "rework_of": "x2", "rework_attempt": 2}),
        _ev("candidate.conflict", {"target_ref": "cand/CJMIN-1", "pdd_id": "CJMIN-1", "status": "conflict"}, eid="cc3"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "escalate"
    assert plans[0].attempt == 3


def test_replan_requested_marks_rejection_handled():
    # Once a replan has been requested for a rejection it must not re-fire each
    # tick; orchestrator.replan_requested{rework_of} is a handling marker and
    # advances the attempt counter (so the cap still bounds it).
    events = [
        _ev("integration.failed", {
            "target_ref": "cand/CJMIN-1", "pdd_id": "CJMIN-1", "status": "conflict",
        }, eid="i1"),
        _ev("orchestrator.replan_requested", {"pdd_id": "CJMIN-1", "rework_of": "i1", "rework_attempt": 1}),
    ]
    assert plan_candidate_rework(events) == []


# --- R28 (doc 93 §1/§5): admission/W1 机械拒 → 自动回 synth -------------------

def _replan_config(enabled=True, trigger="zaofu.refactor.review.ready"):
    return SimpleNamespace(
        workflow=SimpleNamespace(
            admission_replan=SimpleNamespace(
                enabled=enabled, resynth_trigger=trigger
            )
        )
    )


_ADMISSION_REASON = (
    "lane_pipeline admission rejected task_map: assembly task "
    "'CJMIN-ASSEMBLY-001' is not present in the task_map; no task owns root"
)


def test_admission_cancel_plans_replan_when_enabled():
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1",
            "trace_id": "t1",
            "reason": _ADMISSION_REASON,
        }, eid="cx1", corr="t1"),
    ]
    plans = plan_candidate_rework(events, config=_replan_config())
    assert len(plans) == 1
    p = plans[0]
    assert p.action == "replan"          # 回 synth 重拆,绝不 retrigger 同张被拒 map
    assert p.pdd_id == "CJMIN-1"
    assert p.source_event_id == "cx1"
    assert p.source_event_type == "fanout.cancelled"
    assert any("admission:" in f for f in p.feedback)


def test_admission_cancel_ignored_when_disabled_default():
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1", "trace_id": "t1", "reason": _ADMISSION_REASON,
        }, eid="cx1", corr="t1"),
    ]
    # 缺省(无 config / 开关关)= 现状 no_action,不产 plan(零回归)
    assert plan_candidate_rework(events) == []
    assert plan_candidate_rework(events, config=_replan_config(enabled=False)) == []


def test_non_contract_cancel_not_replanned():
    # stale/dedup 类取消不在白名单 —— 即便开关开也不当重拆(否则误把 task 状态
    # 类失败送去 synth)。
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1", "trace_id": "t1", "reason": "stale_task_map",
        }, eid="cx1", corr="t1"),
    ]
    assert plan_candidate_rework(events, config=_replan_config()) == []


def test_w1_overlap_cancel_plans_replan():
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1", "trace_id": "t1",
            "reason": "writer fanout task_map has overlapping allowed paths "
                      "'docs/cj-min/**' and 'docs/cj-min/state-config.md'",
        }, eid="cx1", corr="t1"),
    ]
    plans = plan_candidate_rework(events, config=_replan_config())
    assert len(plans) == 1 and plans[0].action == "replan"


def test_admission_replan_handled_by_marker():
    # orchestrator.replan_requested(rework_of=被拒 cancel)= handled 标记,
    # sweep 不再每 tick 重发。
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1", "trace_id": "t1", "reason": _ADMISSION_REASON,
        }, eid="cx1", corr="t1"),
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CJMIN-1", "rework_of": "cx1",
            "rework_source": "fanout.cancelled", "rework_attempt": 1,
        }, eid="rq1", corr="t1"),
    ]
    assert plan_candidate_rework(events, config=_replan_config()) == []


def test_admission_replan_escalates_at_cap():
    events = [
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CJMIN-1", "rework_of": "a",
            "rework_source": "fanout.cancelled", "rework_attempt": 1,
        }, eid="rq1"),
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CJMIN-1", "rework_of": "b",
            "rework_source": "fanout.cancelled", "rework_attempt": 2,
        }, eid="rq2"),
        _ev("fanout.cancelled", {
            "pdd_id": "CJMIN-1", "trace_id": "t1", "reason": _ADMISSION_REASON,
        }, eid="cx3", corr="t1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2, config=_replan_config())
    assert len(plans) == 1 and plans[0].action == "escalate"


def test_clean_module_parity_closure_supersedes_stale_w1_cancel():
    events = [
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CANGJIE-R3",
            "rework_of": "cx1",
            "rework_source": "fanout.cancelled",
            "rework_attempt": 1,
        }, eid="rq1", corr="trace-r3"),
        _ev("orchestrator.replan_requested", {
            "pdd_id": "CANGJIE-R3",
            "rework_of": "cx2",
            "rework_source": "fanout.cancelled",
            "rework_attempt": 2,
        }, eid="rq2", corr="trace-r3"),
        _ev("fanout.cancelled", {
            "pdd_id": "CANGJIE-R3",
            "trace_id": "trace-r3",
            "candidate_ref": "cand/CANGJIE-R3",
            "reason": (
                "writer fanout task_map has overlapping allowed paths "
                "'packages/core/**' and 'packages/core/src/agent-loop.ts'"
            ),
        }, eid="cx3", corr="trace-r3"),
        _ev("cangjie.module.parity.scan.completed", {
            "pdd_id": "CANGJIE-R3",
            "trace_id": "trace-r3",
            "candidate_ref": "cand/CANGJIE-R3",
            "open_p0_p1_gap_count": 0,
        }, eid="parity-clean", corr="trace-r3"),
    ]

    assert plan_candidate_rework(
        events,
        max_attempts=2,
        config=_replan_config(),
    ) == []


def test_clean_module_parity_closure_does_not_supersede_other_pdd_cancel():
    events = [
        _ev("fanout.cancelled", {
            "pdd_id": "CANGJIE-R3",
            "trace_id": "trace-r3",
            "candidate_ref": "cand/CANGJIE-R3",
            "reason": (
                "writer fanout task_map has overlapping allowed paths "
                "'packages/core/**' and 'packages/core/src/agent-loop.ts'"
            ),
        }, eid="cx3", corr="trace-r3"),
        _ev("cangjie.module.parity.scan.completed", {
            "pdd_id": "CANGJIE-R4",
            "trace_id": "trace-r4",
            "candidate_ref": "cand/CANGJIE-R4",
            "open_p0_p1_gap_count": 0,
        }, eid="parity-other", corr="trace-r4"),
    ]

    plans = plan_candidate_rework(
        events,
        max_attempts=2,
        config=_replan_config(),
    )

    assert len(plans) == 1
    assert plans[0].action == "replan"
