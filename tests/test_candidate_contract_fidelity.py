import pytest
from zf.runtime.candidate_verification_authority import _aggregate_contract_snapshot, CandidateVerificationAuthorityError


def aggregate(commands):
    return _aggregate_contract_snapshot([{'task_id': f'T{i}', 'verification_commands': [c]}
        for i, c in enumerate(commands)], workflow_run_id='run', task_id='candidate',
        contract_revision='r1', task_map_generation='g1', base_commit='base', task_ref='candidate',
        candidate_event_id='event', freeze_descriptor={}, child_authorities=[], package_identity={})


def test_same_command_keeps_all_producer_paths_and_nondeterministic_flag():
    command = {'command_id': 'c', 'command_digest': 'a'*64, 'command': 'browser', 'tier': 'e2e'}
    result = aggregate([{**command, 'producer_paths': ['trace.zip']},
                        {**command, 'producer_paths': ['video.webm'], 'deterministic': False}])
    row = result['verification_commands'][0]
    assert row['producer_paths'] == ['trace.zip', 'video.webm']
    assert row['deterministic'] is False
    assert result['verification_tiers'] == ['e2e']


def test_conflicting_command_tiers_cannot_be_silently_coalesced():
    command = {'command_id': 'c', 'command_digest': 'a'*64, 'command': 'browser'}
    with pytest.raises(CandidateVerificationAuthorityError, match='conflicting'):
        aggregate([{**command, 'tier': 'e2e'}, {**command, 'tier': 'integration'}])


def test_mixed_commands_keep_their_original_verification_tiers():
    result = aggregate([
        {'command_id': 'api', 'command_digest': 'a'*64, 'command': 'api-test',
         'tier': 'integration', 'producer_paths': ['api.json']},
        {'command_id': 'browser', 'command_digest': 'b'*64, 'command': 'browser-test',
         'tier': 'e2e', 'producer_paths': ['trace.zip', 'video.webm']},
    ])
    assert result['verification_tier'] == 'integration'  # Candidate stage identity.
    assert [(row['tier'], row['producer_paths']) for row in result['verification_commands']] == [
        ('integration', ['api.json']), ('e2e', ['trace.zip', 'video.webm']),
    ]


def test_independent_frozen_command_ids_keep_each_tier_and_its_criteria():
    from zf.runtime.verification_commands import command_digest

    literal = "python3 hello.py"
    digest = command_digest(literal)
    commands = [
        {
            "command_id": "CMD-CLI",
            "command_digest": digest,
            "command": literal,
            "tier": "runtime",
            "acceptance_ids": ["AC-CLI"],
            "producer_paths": ["runtime.json"],
        },
        {
            "command_id": "CMD-REAL-CLI",
            "command_digest": digest,
            "command": literal,
            "tier": "e2e",
            "acceptance_ids": ["AC-E2E"],
            "producer_paths": ["e2e.json"],
        },
        {
            "command_id": "CMD-TEST",
            "command_digest": "b" * 64,
            "command": "python3 -m unittest",
            "tier": "runtime",
        },
    ]

    def snapshot(records):
        return _aggregate_contract_snapshot(
            [
                {
                    "task_id": "TASK-HELLO-CLI",
                    "verification_commands": records,
                    "acceptance_criteria": [
                        {
                            "acceptance_id": ac,
                            "verification_command_ids": [c["command_id"]],
                        }
                        for c in commands
                        for ac in c.get("acceptance_ids", [])
                    ],
                }
            ],
            workflow_run_id="run",
            task_id="candidate",
            contract_revision="r1",
            task_map_generation="g1",
            base_commit="base",
            task_ref="candidate",
            candidate_event_id="event",
            freeze_descriptor={},
            child_authorities=[],
            package_identity={},
        )

    frozen = snapshot(commands)
    assert frozen == snapshot(list(reversed(commands)))
    by_id = {row["command_id"]: row for row in frozen["verification_commands"]}
    assert len(by_id) == 3
    assert by_id["candidate-cmd-" + "b" * 20]["command"] == "python3 -m unittest"
    for ac in frozen["acceptance_criteria"]:
        assert len(ac["verification_command_ids"]) == 1
        row = by_id[ac["verification_command_ids"][0]]
        assert row["acceptance_ids"] == [ac["acceptance_id"]]
        assert row["command_digest"] == digest
        assert row["producer_paths"] == [row["tier"] + ".json"]


def test_planner_and_critic_preflight_reports_ambiguous_ids_without_losing_obligations(
    tmp_path,
):
    from tests.test_plan_candidate_preflight import _task_map, _source_index
    from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight

    task_map = _task_map()
    original = task_map["tasks"][0]["validation"]["commands"][0]
    task_map["tasks"][0]["validation"]["commands"].append({**original, "tier": "e2e"})
    review = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / "state",
        project_root=tmp_path,
        reports=[
            {
                "report": {
                    "task_map": task_map,
                    "source_index": _source_index(),
                    "source_index_ref": "inline:source-index",
                    "plan_md": "# Plan",
                }
            }
        ],
        manifest={
            "fanout_id": "plan",
            "stage_id": "prd-plan",
            "trigger_payload": {"flow_kind": "prd"},
        },
        metadata={},
        writer_policy={"frozen_candidate_reader": True},
    )
    assert review["status"] == "failed"
    detail = next(
        row for row in review["errors"] if row["code"] == "command_id_duplicate"
    )
    assert all(
        s in detail["message"]
        for s in ("test-command", "runtime", "e2e", "distinct stable command IDs")
    )
