import hashlib
import json
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import (
    active_ledger_attempt_id,
    build_attempt_source_manifest,
    build_input_consumption_policy,
    canonical_required_reads,
    live_attempt_ids,
    materialize_attempt_source_ref,
    read_attempt_artifact,
    render_attempt_source_briefing,
    seal_read_ledger,
    validate_required_reads,
)


def test_stage_profiles_declare_only_their_canonical_required_inputs() -> None:
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1",
        task_id="T1",
        attempt_id="attempt-1",
        dispatch_id="dispatch-1",
        sources=[{
            "source_id": source_id,
            "artifact_id": f"{source_id}.json",
            "ref": f"artifacts/{source_id}.json",
            "sha256": digest,
        } for source_id, digest in (
            ("contract", "a" * 64),
            ("target", "b" * 64),
            ("impl-self-check", "c" * 64),
            ("notes", "d" * 64),
        )],
    )

    impl = canonical_required_reads(
        manifest,
        output_profile_id="implementation",
    )
    assert [(row["source_id"], row["json_path"]) for row in impl] == [
        ("contract", "$.acceptance_criteria"),
        ("contract", "$.verification_commands"),
    ]
    verify = canonical_required_reads(
        manifest,
        output_profile_id="candidate-verify",
    )
    assert {(row["source_id"], row["json_path"]) for row in verify} == {
        ("contract", "$.acceptance_criteria"),
        ("contract", "$.verification_commands"),
        ("target", "$"),
        ("impl-self-check", "$"),
    }
    assert all(row["source_id"] != "notes" for row in verify)


def test_impl_and_verify_require_every_injected_plan_port() -> None:
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-ports",
        task_id="T-ports",
        attempt_id="attempt-ports",
        dispatch_id="dispatch-ports",
        sources=[
            {
                "source_id": "contract",
                "artifact_id": "contract.json",
                "ref": "artifacts/contract.json",
                "sha256": "a" * 64,
            },
            {
                "source_id": "target",
                "artifact_id": "target.json",
                "ref": "artifacts/target.json",
                "sha256": "b" * 64,
            },
            {
                "source_id": "impl-self-check",
                "artifact_id": "self-check.json",
                "ref": "artifacts/self-check.json",
                "sha256": "c" * 64,
            },
            {
                "source_id": "plan-port-acceptance_matrix",
                "artifact_id": "acceptance_matrix",
                "ref": "artifacts/acceptance-matrix.json",
                "sha256": "d" * 64,
            },
            {
                "source_id": "plan-port-test_matrix",
                "artifact_id": "test_matrix",
                "ref": "artifacts/test-matrix.json",
                "sha256": "e" * 64,
            },
        ],
    )

    impl = canonical_required_reads(
        manifest,
        output_profile_id="implementation",
    )
    verify = canonical_required_reads(
        manifest,
        output_profile_id="candidate-verify",
    )

    expected_ports = {
        ("plan-port-acceptance_matrix", "$"),
        ("plan-port-test_matrix", "$"),
    }
    assert expected_ports <= {
        (row["source_id"], row["json_path"]) for row in impl
    }
    assert expected_ports <= {
        (row["source_id"], row["json_path"]) for row in verify
    }


def test_attempt_source_briefing_renders_literal_runtime_cli(monkeypatch) -> None:
    command = "uv --project /workspace/zaofu run zf"
    monkeypatch.setenv("ZF_CLI_CMD", command)

    briefing = render_attempt_source_briefing({
        "attempt_source_manifest_ref": "artifacts/attempts/a/source.json",
        "attempt_id": "attempt-1",
    })

    assert f"`{command} artifact list --attempt <attempt-id>`" in briefing
    assert "Execute one literal CLI command per tool call" in briefing
    assert "loops, pipes, redirections" in briefing


def test_required_read_records_and_seals_attempt_ledger(tmp_path: Path) -> None:
    artifact = tmp_path / "artifacts" / "inputs" / "task-map.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"tasks": [{"task_id": "T1"}]}), encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1",
        task_id="T1",
        attempt_id="attempt-1",
        dispatch_id="dispatch-1",
        sources=[{
            "source_id": "plan",
            "artifact_id": "task-map",
            "kind": "task_map",
            "ref": "artifacts/inputs/task-map.json",
            "sha256": digest,
            "allowed_paths": ["$.tasks"],
        }],
    )
    result = read_attempt_artifact(
        tmp_path,
        manifest=manifest,
        source_id="plan",
        artifact_id="task-map",
        json_path="$.tasks",
        max_items=10,
        max_chars=1000,
    )
    assert "T1" in result["content"]
    descriptor = seal_read_ledger(tmp_path, "attempt-1")
    assert descriptor["ref"].endswith(".jsonl")
    assert not (tmp_path / "artifacts/attempts/attempt-1/read-ledger.active.jsonl").exists()

    policy = build_input_consumption_policy(
        workflow_run_id="run-1",
        attempt_id="attempt-1",
        required_reads=[{
            "source_id": "plan",
            "artifact_id": "task-map",
            "artifact_sha256": digest,
            "json_path": "$.tasks",
            "min_returned_bytes": 1,
            "max_items": 10,
            "max_chars": 1000,
        }],
    )
    assert validate_required_reads(
        tmp_path,
        policy=policy,
        ledger_descriptor=descriptor,
    ) == []


def test_required_read_identity_is_exact_and_optional_sources_are_optional(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "artifacts" / "inputs"
    inputs.mkdir(parents=True)
    contract = inputs / "contract.json"
    contract.write_text(json.dumps({
        "acceptance_criteria": [{"acceptance_id": "AC-1"}],
        "verification_commands": ["pytest -q"],
    }))
    optional = inputs / "notes.json"
    optional.write_text(json.dumps({"notes": ["context only"]}))
    contract_digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    optional_digest = hashlib.sha256(optional.read_bytes()).hexdigest()
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1",
        task_id="T1",
        attempt_id="attempt-contract",
        dispatch_id="dispatch-contract",
        sources=[
            {
                "source_id": "contract",
                "artifact_id": "contract.json",
                "kind": "task_contract_snapshot",
                "ref": "artifacts/inputs/contract.json",
                "sha256": contract_digest,
                "allowed_paths": ["$.acceptance_criteria", "$.verification_commands"],
            },
            {
                "source_id": "notes",
                "artifact_id": "notes.json",
                "kind": "context",
                "ref": "artifacts/inputs/notes.json",
                "sha256": optional_digest,
                "allowed_paths": ["$"],
            },
        ],
    )
    read_attempt_artifact(
        tmp_path,
        manifest=manifest,
        source_id="contract",
        artifact_id="contract.json",
        json_path="$.acceptance_criteria",
    )
    descriptor = seal_read_ledger(tmp_path, "attempt-contract")
    base = {
        "source_id": "contract",
        "artifact_id": "contract.json",
        "artifact_sha256": contract_digest,
        "json_path": "$.acceptance_criteria",
        "min_returned_bytes": 1,
    }
    policy = build_input_consumption_policy(
        workflow_run_id="run-1",
        attempt_id="attempt-contract",
        required_reads=[base],
    )
    assert validate_required_reads(
        tmp_path,
        policy=policy,
        ledger_descriptor=descriptor,
    ) == []
    for changed in (
        {**base, "artifact_id": "wrong.json"},
        {**base, "artifact_sha256": "0" * 64},
        {**base, "json_path": "$.verification_commands"},
    ):
        invalid = build_input_consumption_policy(
            workflow_run_id="run-1",
            attempt_id="attempt-contract",
            required_reads=[changed],
        )
        issues = validate_required_reads(
            tmp_path,
            policy=invalid,
            ledger_descriptor=descriptor,
        )
        assert [item["code"] for item in issues] == ["required_read_missing"]


def test_fanout_report_is_materialized_as_immutable_attempt_input(
    tmp_path: Path,
) -> None:
    report = tmp_path / "fanouts" / "fanout-1" / "children" / "scan" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    source = materialize_attempt_source_ref(
        state_dir=tmp_path,
        project_root=tmp_path.parent,
        ref=str(report),
        source_id="scan-report",
        kind="fanout_report",
    )
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1",
        task_id="T1",
        attempt_id="attempt-fanout",
        dispatch_id="dispatch-fanout",
        sources=[source],
    )

    assert source["ref"].startswith("artifacts/attempt-inputs/")
    result = read_attempt_artifact(
        tmp_path,
        manifest=manifest,
        source_id="scan-report",
        artifact_id="report.json",
    )
    assert '"status": "completed"' in result["content"]


def test_sealed_ledger_accumulates_correction_reads_without_active_file(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "artifacts" / "inputs"
    inputs.mkdir(parents=True)
    sources = []
    for name in ("objective", "plan", "contract"):
        path = inputs / f"{name}.json"
        path.write_text(json.dumps({"name": name}), encoding="utf-8")
        sources.append({
            "source_id": name,
            "artifact_id": name,
            "kind": name,
            "ref": f"artifacts/inputs/{name}.json",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "allowed_paths": ["$"],
        })
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1",
        task_id="T1",
        attempt_id="attempt-1",
        dispatch_id="dispatch-1",
        sources=sources,
    )

    descriptors = []
    for source in sources:
        read_attempt_artifact(
            tmp_path,
            manifest=manifest,
            source_id=source["source_id"],
            artifact_id=source["artifact_id"],
        )
        descriptors.append(seal_read_ledger(tmp_path, "attempt-1"))

    # A replay after the correction has no active ledger left. It must still
    # recover the cumulative immutable ledger rather than report missing reads.
    recovered = seal_read_ledger(tmp_path, "attempt-1")
    policy = build_input_consumption_policy(
        workflow_run_id="run-1",
        attempt_id="attempt-1",
        required_reads=[{
            "source_id": source["source_id"],
            "artifact_id": source["artifact_id"],
            "artifact_sha256": source["sha256"],
            "json_path": "$",
            "min_returned_bytes": 1,
        } for source in sources],
    )

    assert recovered == descriptors[-1]
    assert validate_required_reads(
        tmp_path,
        policy=policy,
        ledger_descriptor=recovered,
    ) == []


def test_required_read_mismatch_and_live_attempt_detection() -> None:
    events = [
        ZfEvent(
            type="fanout.child.dispatched",
            payload={"run_id": "attempt-1"},
        ),
    ]
    assert live_attempt_ids(events) == {"attempt-1"}
    assert active_ledger_attempt_id(
        "artifacts/attempts/attempt-1/read-ledger.active.jsonl"
    ) == "attempt-1"
    events.append(ZfEvent(
        type="fanout.child.completed",
        payload={"run_id": "attempt-1"},
    ))
    assert live_attempt_ids(events) == set()


def test_project_source_is_materialized_by_digest_for_attempt_reads(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    objective = tmp_path / "docs" / "objective.md"
    objective.parent.mkdir()
    objective.write_text("deliver the accepted Goal\n", encoding="utf-8")

    source = materialize_attempt_source_ref(
        state_dir=state_dir,
        project_root=tmp_path,
        ref="docs/objective.md",
        source_id="objective",
        kind="goal_objective",
    )

    assert source["source_id"] == "objective"
    assert source["kind"] == "goal_objective"
    assert source["ref"].startswith("artifacts/attempt-inputs/")
    materialized = state_dir / source["ref"]
    assert materialized.read_text(encoding="utf-8") == objective.read_text(
        encoding="utf-8",
    )
    objective.write_text("mutated after dispatch\n", encoding="utf-8")
    assert materialized.read_text(encoding="utf-8") == "deliver the accepted Goal\n"
