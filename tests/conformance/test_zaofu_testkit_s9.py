"""S9 conformance case: a failed TestKit case enters the durable Issue path."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.runtime.regression_case import (
    capture_regression_case,
    replay_regression_case,
)

from tests.testkit.evidence import evidence_ref, sha256_file
from tests.test_issue_candidate_delivery import _issue, _service


def test_issue_ledger_has_a_replayable_test_reference_for_each_known_bug() -> None:
    """Issue entries must point to an executable detector, not only prose."""

    repo_root = Path(__file__).resolve().parents[2]
    issue_text = (
        repo_root
        / "awesome-zaofu/New_function/Test ZaoFu_Project/ZaoFu_issue.md"
    ).read_text(encoding="utf-8")
    sections = re.split(r"(?m)^## (ZF-TEST-\d+)[:：]", issue_text)[1:]
    assert sections, "the issue ledger must contain registered TestKit defects"
    for issue_id, body in zip(sections[::2], sections[1::2]):
        references = [
            line.strip()
            for line in body.splitlines()
            if line.strip().startswith("tests/")
        ]
        assert references, f"{issue_id} has no executable detector reference"
        for reference in references:
            test_path = reference.split("::", 1)[0]
            assert (repo_root / test_path).exists(), f"{issue_id}: {reference}"


def test_failed_case_is_traceable_into_self_issue_intake(tmp_path: Path, monkeypatch, capsys) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\nproject:\n  name: s9\n  state_dir: state\n",
        encoding="utf-8",
    )
    failure = tmp_path / "s3-failure.json"
    failure.write_text(
        json.dumps({
            "case_id": "duplicate-wakeup-does-not-redispatch",
            "command": "pytest tests/conformance/test_zaofu_testkit_s3.py -q",
            "exit_code": 1,
            "expected": "one task.dispatched",
            "actual": "two task.dispatched",
        }),
        encoding="utf-8",
    )
    failure_ref = evidence_ref(failure, root=tmp_path)
    monkeypatch.chdir(tmp_path)

    assert main([
        "issue",
        "report",
        "TestKit duplicate dispatch failure",
        "--legacy-intake",
        "--non-interactive",
    ]) == 0
    intake = json.loads(capsys.readouterr().out)["intake"]
    answer_file = tmp_path / "answers.json"
    answer_file.write_text(json.dumps({
        "answers": {
            "title": "TestKit duplicate dispatch failure",
            "bug_description": "The same wakeup produced two dispatches.",
            "reproduction_steps": (
                "Run `pytest tests/conformance/test_zaofu_testkit_s3.py -q`; "
                f"failure evidence: {failure_ref['ref']}"
            ),
            "expected_behavior": "The operation is dispatched at most once.",
            "attachments_context": "",
            "environment": {"os": "Linux", "version": "test"},
            "zaofu_version": "local",
            "additional_context": json.dumps(failure_ref),
        },
    }), encoding="utf-8")

    assert main([
        "issue", "answer", intake["intake_id"], "--answers-file", str(answer_file),
    ]) == 0
    draft_result = json.loads(capsys.readouterr().out)

    assert draft_result["status"] == "evidence_waiting_for_runtime"
    assert draft_result["draft"]["title"] == "TestKit duplicate dispatch failure"
    assert failure_ref["ref"] in draft_result["draft"]["additional_context"]
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(event.type == "self_issue.intake.promoted" for event in events)
    assert not any(event.type == "self_issue.publish.requested" for event in events)


def test_failed_case_becomes_replayable_regression_and_detects_rework(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    case = capture_regression_case(
        state_dir,
        case_id="s9-rework-regression",
        source_task_id="TASK-S9",
        command="true",
        assertions=("rework==0",),
        provenance={"source": "testkit", "case_id": "s9-rework-regression"},
    )

    clean = replay_regression_case(case, facts={"rework": 0}, run_command=True)
    assert clean["passed"] is True

    log = EventLog(state_dir / "events.jsonl")
    EventWriter(log).emit(
        "task.rework.requested",
        actor="reviewer",
        task_id="TASK-S9",
    )
    dirty = replay_regression_case(case, facts={"rework": 1})
    assert dirty["passed"] is False
    assert dirty["assertions"][0]["actual"] == 1


def test_regression_artifact_reference_survives_issue_intake_evidence_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A captured deterministic case remains traceable in the Issue draft."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\nproject:\n  name: s9-regression\n  state_dir: state\n",
        encoding="utf-8",
    )
    case = capture_regression_case(
        state_dir,
        case_id="s9-intake-case",
        source_task_id="TASK-S9-INTAKE",
        assertions=("rework==0",),
        provenance={"source": "testkit"},
    )
    case_path = state_dir / "artifacts" / "regression" / f"{case.case_id}.json"
    case_ref = evidence_ref(case_path, root=tmp_path)
    failure = tmp_path / "failure.json"
    failure.write_text(
        json.dumps({
            "case_id": case.case_id,
            "case_ref": case_ref,
            "expected": "rework==0",
            "actual": "rework==1",
        }),
        encoding="utf-8",
    )
    failure_ref = evidence_ref(failure, root=tmp_path)
    monkeypatch.chdir(tmp_path)

    assert main(["issue", "report", "Regression evidence handoff", "--legacy-intake", "--non-interactive"]) == 0
    intake = json.loads(capsys.readouterr().out)["intake"]
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({
        "answers": {
            "title": "Regression evidence handoff",
            "bug_description": "A deterministic regression case failed.",
            "reproduction_steps": f"Replay {case.case_id}; failure: {failure_ref['ref']}",
            "expected_behavior": "The regression assertion passes after the fix.",
            "attachments_context": "",
            "environment": {"os": "Linux", "version": "test"},
            "zaofu_version": "local",
            "additional_context": json.dumps({"regression_case": case_ref}),
        },
    }), encoding="utf-8")

    assert main(["issue", "answer", intake["intake_id"], "--answers-file", str(answers)]) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["status"] == "evidence_waiting_for_runtime"
    assert case_ref["ref"] in draft["draft"]["additional_context"]
    assert case_ref["digest"] in draft["draft"]["additional_context"]
    assert case_ref["digest"] == sha256_file(case_path)
    assert (state_dir / "artifacts" / "regression" / f"{case.case_id}.json").exists()


@pytest.mark.parametrize("existing", [False, True])
def test_regression_case_capture_does_not_leave_partial_artifact_on_write_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
) -> None:
    """A crash while capturing a regression case must leave no partial evidence."""

    state_dir = tmp_path / "state"
    target = state_dir / "artifacts" / "regression" / "atomic-case.json"
    if existing:
        capture_regression_case(state_dir, case_id="atomic-case", source_task_id="OLD")
    previous = target.read_bytes() if existing else None

    from contextlib import contextmanager
    from zf.core.state import atomic_io

    original_write_text = Path.write_text
    original_fdopen = atomic_io.os.fdopen
    injected = []

    def crash_on_final_path(path, data, *args, **kwargs):
        if path == target:  # Still detects a regression to direct final-path writes.
            path.parent.mkdir(parents=True, exist_ok=True)
            original_write_text(path, data[:12], *args, **kwargs)
            injected.append("final-path")
            raise OSError("injected regression artifact write crash")
        return original_write_text(path, data, *args, **kwargs)

    @contextmanager
    def crashing_fdopen(*args, **kwargs):
        with original_fdopen(*args, **kwargs) as stream:
            class PartialWriter:
                def write(self, data):
                    stream.write(data[:12])
                    stream.flush()
                    injected.append("temporary-path")
                    raise OSError("injected regression artifact write crash")
            yield PartialWriter()

    monkeypatch.setattr(Path, "write_text", crash_on_final_path)
    monkeypatch.setattr(atomic_io.os, "fdopen", crashing_fdopen)
    with pytest.raises(OSError, match="injected regression artifact write crash"):
        capture_regression_case(
            state_dir,
            case_id="atomic-case",
            source_task_id="TASK-ATOMIC",
            assertions=("rework==0",),
        )

    assert injected, "partial write fault must actually execute"
    if existing:
        assert target.read_bytes() == previous, "prior evidence must remain intact"
    else:
        assert not target.exists(), "partial regression evidence must not be published"
    assert not list(target.parent.glob("atomic-case.json.*.tmp"))


def test_new_issue_revision_rejects_old_page_review_and_prepare(
    tmp_path: Path,
) -> None:
    """A stale operator page cannot approve or publish an old revision.

    A verified candidate remains bound to the revision that produced it (a
    later Issue revision does not silently invalidate that candidate).  The
    delivery boundary must nevertheless reject an action whose caller claims
    the newer revision, and must leave no handoff or publication receipt.
    """

    from zf.runtime.issue_candidate_delivery import CandidateDeliveryError

    service, state_dir, _base, head = _service(tmp_path)
    issue = _issue()
    writer = EventWriter(event_log_from_project(state_dir))
    writer.emit(
        "external_issue.received",
        actor="github-poller",
        payload={"source_key": issue.issue_key, "source_revision": "sha256:source-2"},
    )

    with pytest.raises(CandidateDeliveryError, match="source revision changed"):
        service.review(
            issue,
            verdict="approve",
            expected_candidate_sha=head,
            expected_source_revision="sha256:source-2",
        )

    with pytest.raises(CandidateDeliveryError, match="source revision changed"):
        service.prepare(
            issue,
            expected_candidate_sha=head,
            expected_source_revision="sha256:source-2",
        )

    assert not (state_dir / "candidates/PDD-17/handoff.json").exists()
    assert not any(
        event.type in {"candidate.owner_review.approved", "candidate.publication.prepared"}
        for event in writer.event_log.read_all()
    )
