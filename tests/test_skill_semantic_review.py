import pytest

from zf.runtime.skill_semantic_review import validate_decision


def test_review_requires_each_obligation_and_independent_evidence():
    packet = {"obligations": [{"id": "A"}], "host_facts": {}, "task_outcome": "passed"}
    reply = {
        "items": [
            {
                "id": "A",
                "finding": "met",
                "citations": ["contract"],
                "reason": "Worker reported success",
            }
        ],
        "owner": "observation",
        "action": "Inspect an independent receipt",
        "limitation": "No host receipt",
    }
    with pytest.raises(ValueError):
        validate_decision(packet, reply)
    reply["items"][0]["finding"] = "unresolved"
    assert validate_decision(packet, reply)["status"] == "abstained"
    reply["items"][0]["citations"] = []
    assert validate_decision(packet, reply)["result"] == "unknown"
    from zf.runtime.skill_review_model import _schema

    finding_rule = _schema(packet)["properties"]["items"]["items"]["properties"]["finding"]
    assert finding_rule["enum"] == ["unresolved"]
    reply["items"] = []
    with pytest.raises(ValueError):
        validate_decision(packet, reply)


def test_independent_review_command_budget_retry_replay_and_stale_inputs(tmp_path, monkeypatch, capsys):
    import json
    from zf.cli.main import main
    from zf.core.events.log import EventLog
    from zf.runtime.sidecar_refs import write_sidecar_json
    from zf.runtime import skill_review_batch as batch
    from zf.runtime.skill_review_model import blinded_input
    from zf.runtime.call_result_envelope import canonical_json_sha256
    from zf.runtime.skill_evaluation_projection import query_cards, replay_cards, SkillEvaluationObserver
    from tests.test_skill_attempt_evaluation import dispatch, event, identity

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ZF_STATE_DIR", str(state))
    monkeypatch.delenv("ZF_ROLE_INSTANCE", raising=False)
    log = EventLog(state / "events.jsonl")
    data = identity()
    stage = dispatch(data)
    contract = write_sidecar_json(
        state,
        "artifacts/contract.json",
        {**data, "acceptance_criteria": [{"acceptance_id": "A", "statement": "Restore the job once"}]},
        kind="contract",
        schema_version="c",
        created_by="test",
    )
    stage.payload.update(contract_snapshot_ref=contract["ref"], contract_snapshot_digest=contract["sha256"])
    result = write_sidecar_json(
        state,
        "artifacts/result.json",
        {
            **data,
            "schema_version": "verification-result.v1",
            "requirement_results": [
                {"acceptance_id": "A", "status": "passed", "findings": ["No host output"]}
            ],
        },
        kind="result",
        schema_version="verification-result.v1",
        created_by="test",
    )
    log.append(stage)
    log.append(
        event(
            "workflow.call.result.admitted",
            data,
            admission_status="admitted",
            semantic_verdict="passed",
            control_result_ref=result,
        )
    )
    cli = ["attempt", "skill-review", data["attempt_id"], "--model", "fixture:1"]

    def command(*extra):
        assert main(cli + list(extra)) == 0
        return json.loads(capsys.readouterr().out)

    assert command("--max-calls", "0")["reviews"][0]["pending_reason"] == "budget_exhausted"
    (pending,) = query_cards(state)
    assert pending["review_status"] == "pending"
    assert command("--max-calls", "0")["reviews"][0]["reused"]
    monkeypatch.setattr(
        batch,
        "model_identity",
        lambda name: {
            "backend": "ollama",
            "backend_version": "test",
            "model": name,
            "model_digest": "a" * 64,
        },
    )

    def offline(*_):
        raise ConnectionError("sensitive diagnostic")

    monkeypatch.setattr(batch, "review_once", offline)
    assert command()["reviews"][0]["pending_reason"] == "reviewer_unavailable"
    assert "sensitive diagnostic" not in log.path.read_text()

    def infer(packet, model, timeout):
        assert packet["claims"][0]["findings"] == ["No host output"]
        reply = {
            "items": [
                {
                    "id": "A",
                    "citations": ["contract"],
                    "finding": "unresolved",
                    "reason": "missing receipt",
                }
            ],
            "owner": "evidence_producer",
            "action": "Bind the host receipt.",
            "limitation": "No independent observation.",
        }
        return {
            "decision": validate_decision(packet, reply),
            "session_id": "session-unique",
            "reviewer": model,
            "prompt_digest": batch.review_prompt_digest(packet),
            "model_input_digest": canonical_json_sha256(blinded_input(packet)),
            "inference_options": dict(batch.OPTIONS),
            "input_tokens": 90,
            "output_tokens": 52,
        }

    monkeypatch.setattr(batch, "review_once", infer)
    response = command()
    assert response["calls"] == 1
    assert command()["calls"] == 0
    (reviewed,) = query_cards(state)
    assert reviewed["review_status"] == "abstained"
    assert reviewed["task_outcome"] == "passed"
    assert reviewed["recommendation"]["target"] == "evidence_producer"
    assert reviewed["change_reason"] == "independent_review_updated"
    assert replay_cards(state, tmp_path, None)[0]["review_event_id"] == reviewed["review_event_id"]
    assert len(query_cards(state, history=True)) == 3
    retained = command("--max-calls", "0")["reviews"][0]
    assert retained["pending_reason"] == "budget_exhausted" and retained["retained_status"] == "abstained"
    with monkeypatch.context() as change:
        change.setattr(batch, "OPTIONS", {**batch.OPTIONS, "num_thread": 2})
        assert command()["calls"] == 1
        reviewed = query_cards(state)[0]
    # A worker copy of a review cannot add an authoritative revision.
    forged = next(e for e in reversed(log.read_all()) if e.type == "skill.evaluation.review.recorded")
    forged.origin, forged.id = "worker", "forged"
    log.append(forged)
    observer = SkillEvaluationObserver(state, tmp_path, None)
    observer.poll()
    assert query_cards(state)[0]["revision"] == reviewed["revision"]
    # A late, exactly bound skill invocation changes the frozen base; an old review cannot carry over.
    hook = event("agent.tool.use", data, tool="Skill", input={"skill": "review-method"})
    hook.actor = "reader"
    log.append(hook)
    observer.poll()
    (last,) = query_cards(state)
    assert last["review_status"] == "pending" and "independent_review" not in last
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "reader")
    assert main(cli) == 1


def test_stratified_sampling_and_negative_release():
    from zf.runtime.skill_review_batch import select_cards
    from tests.test_skill_attempt_evaluation import identity

    cards = [
        {**identity(str(i)), "task_outcome": outcome, "critical_failures": critical}
        for i, (outcome, critical) in enumerate(
            [("passed", ["target"]), ("passed", []), ("passed", []), ("rejected", [])]
        )
    ]
    sample = select_cards(cards, 10)
    assert [r for _, r in sample] == ["critical_evidence_conflict", "sample_released", "sample_withheld"]
    assert sample == select_cards(list(reversed(cards)), 10)
    packet = {"obligations": [{"id": "A"}], "task_outcome": "passed", "host_facts": {"H1": {}}}
    reply = {
        "items": [
            {
                "id": "A",
                "finding": "violated",
                "citations": ["contract", "H1"],
                "reason": "counterexample",
            }
        ],
        "owner": "skill",
        "action": "Use a negative input",
        "limitation": "Single input",
    }
    verdict = validate_decision(packet, reply)
    assert verdict["result"] == "fail" and verdict["critical_false_release"]
    reply["items"][0]["citations"] = ["H2"]
    with pytest.raises(ValueError):
        validate_decision(packet, reply)


def test_model_receives_only_frozen_text_in_a_fresh_tool_free_request(monkeypatch):
    import json
    from zf.runtime import skill_review_model as model

    identity = {"model": "fixture:1", "model_digest": "a" * 64}
    packet = {"obligations": [{"id": "A"}], "task_outcome": "passed", "host_facts": {"H1": {}}}
    reply = {
        "items": [{"id": "A", "finding": "violated", "citations": ["H1"], "reason": "Observed failure"}],
        "owner": "evidence_producer",
        "action": "Inspect the recorded failure",
        "limitation": "Single observation",
    }
    calls = []

    def request(route, payload, timeout):
        calls.append(payload)
        assert route == "chat" and timeout == 7
        assert "tools" not in payload
        assert [r["role"] for r in payload["messages"]] == ["system", "user"]
        visible = json.loads(payload["messages"][1]["content"])
        assert visible == {"obligations": packet["obligations"], "host_facts": packet["host_facts"]}
        assert payload["options"]["num_predict"] == model.OUTPUT_TOKENS
        return {
            "model": "fixture:1",
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 50,
            "eval_count": 30,
            "message": {"role": "assistant", "content": json.dumps(reply)},
        }

    monkeypatch.setattr(model, "_request", request)
    monkeypatch.setattr(model, "model_identity", lambda _: identity)
    first, second = [model.review_once(packet, identity, 7) for _ in range(2)]
    assert first["session_id"] != second["session_id"]
    assert first["prompt_digest"] == second["prompt_digest"]
    from zf.runtime.skill_attempt_evaluation import apply_review

    card = {
        "task_outcome": "passed",
        "evidence": {"contract": "c", "control_result": "r"},
        "criteria": [
            {"id": "binding", "critical": False, "result": "pass", "source": "mechanical"},
            {"id": "business", "critical": True, "result": "unknown", "source": "independent_review"},
        ],
    }
    apply_review(
        card, {**first, "status": "reviewed"}, {**packet, "host_facts": {"H1": {"event_id": "host-kernel"}}}
    )
    assert card["task_outcome"] == "passed" and card["critical_failures"] == ["business"]
    assert card["score"]["denominator"] == 2 and card["score"]["percent"] == 50
    assert card["criteria"][1]["evidence_refs"] == ["host-kernel"]
    monkeypatch.setattr(model, "model_identity", lambda _: {**identity, "model_digest": "b" * 64})
    with pytest.raises(ValueError, match="changed"):
        model.review_once(packet, identity, 7)


def test_partial_obligations_do_not_generate_an_overall_verdict(tmp_path):
    from types import SimpleNamespace
    from zf.runtime.sidecar_refs import write_sidecar_json
    from zf.runtime.skill_semantic_review import review_packet
    from tests.test_skill_attempt_evaluation import event

    data = {
        "acceptance_criteria": [
            {"acceptance_id": "A", "statement": "visible"},
            {
                "acceptance_id": "PRIVATE-ID",
                "statement": "private text",
                "access_scope": {"roles": ["owner"]},
            },
        ]
    }
    ref = write_sidecar_json(
        tmp_path, "artifacts/contract.json", data, kind="contract", schema_version="v1", created_by="test"
    )
    key = ("r", "op", "a", "d")
    card = {
        "contract_digest": ref["sha256"],
        "evidence": {"contract": ref["ref"], "control_result": None},
        "task_outcome": "passed",
        "target_commit": "abc",
        "criteria": [{"id": "archived_probe_claims_match", "evidence_refs": []}],
    }
    h = SimpleNamespace(state=tmp_path, terminal={key: event("terminal", {})}, missing={})
    packet = review_packet(h, key, card)
    assert not packet["obligations"] and "PRIVATE-ID" not in str(packet) and "private text" not in str(packet)


def test_review_packet_rechecks_cited_host_fact_against_producer(tmp_path, monkeypatch):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.host_verification import reconcile_host_verification
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.skill_attempt_evaluation import EvaluationHistory
    from zf.runtime.skill_semantic_review import review_packet
    from zf.runtime.task_execution_evidence import capture_execution_evidence
    from zf.runtime.workflow_operation import reduce_workflow_operations
    from tests.test_task_host_verification_handoff import audit_case

    service, _, _ = audit_case(tmp_path, monkeypatch)
    assert reconcile_host_verification(service) == 1
    events = service.writer.event_log.read_all()
    source = reduce_workflow_operations(events)["verify-1"]
    state = service.state_dir
    index = hydrate_sidecar_ref(state, capture_execution_evidence(state, source, events)).payload
    ref = index["entries"][0]["ref"]
    fact = hydrate_sidecar_ref(state, ref).payload
    key = ("run-1", "successor-verify", "attempt", "dispatch")
    history = EvaluationHistory(state, tmp_path)
    for ev in events:
        history.consume(ev)
    history.terminal[key] = events[-1]
    criterion = {"id": "archived_probe_claims_match", "evidence_refs": [ref["ref"], ref["ref"]]}
    card = {
        "contract_digest": None,
        "evidence": {"contract": None, "control_result": None},
        "task_id": "T1",
        "target_commit": fact["target_commit"],
        "task_outcome": "passed",
        "criteria": [criterion],
    }

    def observations():
        return review_packet(history, key, card)["host_facts"]

    assert list(observations()) == ["H1"]
    assert observations()["H1"]["output_excerpt"] == "verified\n"
    card["task_id"] = "different-task"
    assert not observations()
    card["task_id"] = "T1"
    switched = write_immutable_json_sidecar(
        state,
        {**fact, "output": "substituted output"},
        root="test/facts",
        kind="test",
        schema_version="test.v1",
        created_by="test",
    )
    criterion["evidence_refs"] = [switched["ref"]]
    assert not observations()


@pytest.mark.parametrize(
    ("outcome", "findings", "expected"),
    [
        ("blocked", ["met", "met"], "fail"),
        ("blocked", ["met", "unresolved"], "unknown"),
        ("blocked", ["violated", "unresolved"], "pass"),
        ("passed", ["met", "unresolved"], "unknown"),
        ("passed", ["violated", "unresolved"], "fail"),
        ("abstained", ["met", "met"], "unknown"),
    ],
)
def test_mixed_obligations_respect_polarity(outcome, findings, expected):
    packet = {"task_outcome": outcome, "obligations": [{"id": "A"}, {"id": "B"}], "host_facts": {"H1": {}}}
    items = [
        {"id": i, "finding": finding, "citations": ["H1"], "reason": "recorded observation"}
        for i, finding in zip(("A", "B"), findings)
    ]
    reply = {"items": items, "owner": "none", "action": "Inspect the record", "limitation": "One sample"}
    assert validate_decision(packet, reply)["result"] == expected


def test_role_restricted_probe_is_skipped_before_evidence_access():
    from zf.runtime.skill_attempt_evidence import probe_agreement

    probe = {
        "evidence_refs": ["artifacts/task-handoffs/tool-output/" + "0" * 64 + ".json"],
        "access_scope": {"roles": ["project-owner"]},
    }
    assert probe_agreement(None, {}, {"probe_receipts": [probe]}) == []
