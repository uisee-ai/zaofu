"""Real default Issue admission/dispatch fixture; only the worker is scripted."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys

import pytest
import yaml

from tests.e2e.generic_workflow_complex_support import _RecordingTransport
from tests.test_control_actions_workflow_request import _execute
from tests.testkit.model_evidence import ModelEvidence
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.artifact_read_ledger import read_attempt_artifact, seal_read_ledger, validate_required_reads
from zf.runtime.impl_self_check import completion_payload_template
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_contract_snapshot import TASK_CONTRACT_CURRENTNESS_FIELDS
from zf.runtime.task_pipeline_briefing import verification_result_template


class IssueClosureReplayDrift(AssertionError):
    """An old candidate event supersedes the same verified Goal closure."""


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
        timeout=15,
    ).stdout.strip()


def isolate_issue_processes(root: Path, monkeypatch: pytest.MonkeyPatch, *,
                            extra_commands: tuple[str, ...] = ()) -> list[str]:
    """Allow local Git and the one declared probe, never a Provider or network."""
    original = subprocess.Popen
    command = f"{shlex.quote(sys.executable)} tests/verify_issue.py"
    commands = (command, *extra_commands)
    forbidden: list[str] = []

    def guarded(args, *positional, **kwargs):
        try:
            cwd = Path(kwargs.get("cwd") or Path.cwd()).resolve()
            assert cwd == root or cwd.is_relative_to(root), (args, cwd)
            if isinstance(args, str):
                assert args in commands, args
            elif any(list(args) == ["sh", "-n", "-c", item] for item in commands):
                pass  # Real admission validates syntax, without running the command.
            else:
                assert args and Path(str(args[0])).name == "git", args
                assert not {"push", "fetch", "pull", "clone", "remote"}.intersection(args), args
        except AssertionError:
            forbidden.append(repr(args))
            raise
        return original(args, *positional, **kwargs)

    def no_network(*args, **kwargs):
        forbidden.append("socket connection")
        pytest.fail("Issue fixture attempted a network connection")

    monkeypatch.setattr(subprocess, "Popen", guarded)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    for name in (
        "ZF_PROJECT_ROOT", "ZF_STATE_DIR", "ZF_WORKFLOW_ACTION_TOKEN",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(root / "workspace"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.chdir(root)
    return forbidden


class IssueRoute:
    answer_file = "answer.txt"
    probe_file = "tests/verify_issue.py"
    request_backend = "mock"

    def __init__(self, root: Path, *, flow_kind: str = "issue", flow_spec: dict | None = None) -> None:
        assert flow_kind in {"issue", "prd", "refactor"}
        self.flow_kind = flow_kind
        self.root = root
        self.state = root / "custom-state"
        self.command = f"{shlex.quote(sys.executable)} tests/verify_issue.py"
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "testkit@example.invalid")
        git(root, "config", "user.name", "ZaoFu TestKit")
        (root / "tests").mkdir()
        (root / "answer.txt").write_text("broken\n", encoding="utf-8")
        (root / "tests/verify_issue.py").write_text(
            "from pathlib import Path\n"
            "assert Path('answer.txt').read_text().strip() == 'fixed'\n"
            "print('issue regression passed')\n", encoding="utf-8",
        )
        (root / ".gitignore").write_text("custom-state/\nworkspace/\n", encoding="utf-8")
        docs = root / "docs/intake"
        docs.mkdir(parents=True)
        (docs / "bug.md").write_text(
            "# Fixture regression\nanswer.txt must contain fixed.\n", encoding="utf-8",
        )
        (root / "zf.yaml").write_text(yaml.safe_dump_all([
            {
                "apiVersion": "zaofu.dev/v1", "kind": (
                    "IssueFlow" if flow_kind == "issue" else
                    "PrdFlow" if flow_kind == "prd" else "RefactorFlow"
                ),
                "metadata": {"name": "testkit-issue"},
                "spec": flow_spec if flow_spec is not None else (
                    {"backend": "mock", "lanes": 1,
                     "issueRef": "docs/intake/bug.md"}
                    if flow_kind == "issue" else
                    {"backend": "mock", "lanes": 1,
                     "prdRef": "docs/intake/bug.md"}
                    if flow_kind == "prd" else
                    {"flowProfile": "refactor-flow/v3", "lanes": 1, "assembly": "none",
                     "laneRoleTemplate": {"backend": "mock"},
                     "roleDefaults": {"backend": "mock", "permission_mode": "bypass"},
                     "objectiveRef": "docs/intake/bug.md", "sourceRoot": str(self.source_root),
                     "targetRoot": str(root), "parityScope": ["core"]}
                ),
            },
            {
                "apiVersion": "zaofu.dev/v1", "kind": "ZfConfig",
                "metadata": {"name": "testkit-issue"},
                "spec": {
                    "version": "1.0",
                    "project": {"name": "testkit-issue", "state_dir": "custom-state"},
                    "goal": {"enabled": True},
                    "runtime": {
                        "workdirs": {"enabled": True, "mode": "worktree"},
                        "git": {
                            "candidate_base_ref": "main", "remote_policy": "local_only",
                            "auto_ship_on_candidate_complete": False,
                            "auto_ship_on_judge_passed": False,
                        },
                    },
                    "quality_gates": {"test": {"required_checks": [self.command]}},
                },
            },
        ], sort_keys=False), encoding="utf-8")
        git(root, "add", "answer.txt", "tests/verify_issue.py", ".gitignore", "docs/intake/bug.md", "zf.yaml")
        git(root, "commit", "-q", "-m", "fixture baseline")
        self.base = git(root, "rev-parse", "HEAD")
        self.invoke: ZfEvent | None = None
        self.proposal: dict = {}
        self.cursor = 0
        self.deliveries: list = []
        self.reopen()
        self.evidence = ModelEvidence(
            root=self.state, model_name="official-issue-route", source=Path(__file__),
            reference=lambda: {"base": self.base, "command": self.command,
                               "invoke": self.invoke.to_json() if self.invoke else None,
                               "case_source_sha256": hashlib.sha256((Path(__file__).parents[1] /
                                   "conformance/test_zaofu_testkit_s8_issue_route.py").read_bytes()).hexdigest()},
            settings="local Git; scripted worker; at most 12 reactor ticks per stage",
            max_files=2048, max_bytes=32 * 1024 * 1024,
        )

    def reopen(self) -> None:
        if hasattr(self, "transport"):
            self.deliveries.extend(self.transport.sent)
        self.config = load_config(self.root / "zf.yaml")
        assert self.config.goal.enabled
        if self.flow_kind == "issue":
            assert self.config.workflow.flow_metadata["topology"] == "light"
        elif self.flow_kind == "prd":
            scan = next(stage for stage in self.config.workflow.stages if stage.id == "prd-scan")
            assert scan.topology == "fanout_reader" and len(scan.roles) == 2
        else:
            scan = next(stage for stage in self.config.workflow.stages if stage.id == "flow-scan")
            assert scan.topology == "fanout_reader" and len(scan.roles) == 3
        assert self.config.runtime.git.remote_policy == "local_only"
        self.state.mkdir(exist_ok=True)
        self.log = EventLog(self.state / "events.jsonl")
        self.writer = EventWriter(self.log)
        self.store = TaskStore(self.state / "kanban.json")
        self.transport = _RecordingTransport()
        self.runtime = Orchestrator(self.state, self.config, self.transport, project_root=self.root)
        self.service = ControlledActionService(
            self.state, self.writer, config=self.config, project_root=self.root,
            actor="operator", source="kanban-agent", surface="web",
        )
        self.cursor = 0

    def events(self, kind: str) -> list[ZfEvent]:
        return [event for event in self.log.read_all() if event.type == kind]

    def submit(self) -> dict:
        return _execute(self.service, "workflow-submit", {
            **{key: self.proposal[key] for key in (
                "intake_ref", "request_id", "proposal_ref", "proposal_digest",
            )}, "kind": self.flow_kind, "allow_missing_env": True,
        })

    def start(self) -> ZfEvent:
        self.proposal = _execute(self.service, "workflow-request", {
            "kind": self.flow_kind, "objective": "Set answer.txt to fixed and pass tests/verify_issue.py",
            "backend": self.request_backend, "allow_missing_env": True,
            **(
                {"target_root": str(self.root)} if self.flow_kind == "prd" else
                {"source_root": str(self.source_root), "target_root": str(self.root)}
                if self.flow_kind == "refactor" else {}
            ),
        })
        assert self.proposal["ok"] and self.proposal["status"] == "proposal_ready", self.proposal
        assert not self.events("workflow.invoke.requested")
        submitted = self.submit()
        assert submitted["ok"], submitted
        [self.invoke] = self.events("workflow.invoke.requested")
        assert self.invoke.payload["workflow_run_id"]
        return self.dispatch(
            "issue-lanes-impl" if self.flow_kind == "issue" else
            "prd-scan" if self.flow_kind == "prd" else "flow-scan"
        )

    def tick(self) -> None:
        events = self.log.read_all()
        pending = events[self.cursor:]
        self.cursor = len(events)
        self.runtime.run_once(events=pending)

    def dispatch(self, stage_id: str) -> ZfEvent:
        for _ in range(12):
            self.tick()
            found = [event for event in self.events("fanout.child.dispatched")
                     if event.payload.get("stage_id") == stage_id]
            if found:
                [event] = found
                assert self.invoke is not None
                assert event.payload["trace_id"] == self.invoke.payload["workflow_run_id"]
                assert any(row[0] == event.payload["role_instance"] for row in
                           [*self.deliveries, *self.transport.sent])
                return event
        raise AssertionError(f"No {stage_id} dispatch: " + json.dumps([
            {"type": event.type, "reason": event.payload.get("reason"),
             "failure_kind": event.payload.get("failure_kind")}
            for event in self.log.read_all()
        ], ensure_ascii=False))

    def child(self, dispatch: ZfEvent) -> dict:
        manifest = json.loads((self.state / "fanouts" / dispatch.payload["fanout_id"] /
                               "manifest.json").read_text(encoding="utf-8"))
        projected = next(child for child in manifest["children"]
                         if child["child_id"] == dispatch.payload["child_id"])
        # Dispatch fields are immutable and win; projection supplies only the
        # legacy/light payload fields absent from the event envelope.
        handoff = dispatch.payload.get("payload") or {}
        child = {**projected, **handoff, **dispatch.payload}
        child["payload"] = {
            **(projected.get("payload") or {}),
            **handoff,
        }
        return child

    def body(self, ref: str, digest: str) -> dict:
        path = self.state / ref
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
        return json.loads(raw)

    def read_inputs(self, child: dict) -> dict:
        manifest = self.body(child["attempt_source_manifest_ref"], child["attempt_source_manifest_digest"])
        policy = child["input_consumption_policy"]
        assert policy["required_reads"], policy
        for item in policy["required_reads"]:
            read_attempt_artifact(
                self.state, manifest=manifest, source_id=item["source_id"],
                artifact_id=item["artifact_id"], json_path=item["json_path"],
                actor=child["role_instance"], role=child["role_instance"], provider="mock",
            )
        ledger = seal_read_ledger(self.state, manifest["attempt_id"])
        assert validate_required_reads(self.state, policy=policy, ledger_descriptor=ledger) == []
        return ledger

    def complete_writer(
        self, dispatch: ZfEvent, *, broken: bool = False, mask_probe: bool = False,
    ) -> ZfEvent:
        assert not mask_probe or broken
        child = self.child(dispatch)
        workdir = Path(child["workdir"])
        (workdir / self.answer_file).write_text("still broken\n" if broken else "fixed\n", encoding="utf-8")
        git(workdir, "add", self.answer_file)
        git(workdir, "commit", "-q", "-m", "fixture worker change")
        payload = deepcopy(child["payload"])
        for key in ("contract_snapshot_ref", "contract_snapshot_digest", "contract_revision",
                    "operation_id", "request_hash", "attempt_source_manifest_ref",
                    "attempt_source_manifest_digest"):
            payload[key] = child[key]
        snapshot = self.body(child["contract_snapshot_ref"], child["contract_snapshot_digest"])
        commit = git(workdir, "rev-parse", "HEAD")
        ledger = self.read_inputs(child)
        probe_path = workdir / self.probe_file
        committed_probe = probe_path.read_bytes()
        try:
            if mask_probe:
                # Untrusted worker input: a real green probe on an uncommitted
                # script is NOT evidence that the submitted tree passes.
                probe_path.write_text('print("issue regression passed")\n', encoding="utf-8")
            probe_digest = hashlib.sha256(probe_path.read_bytes()).hexdigest()
            probe = subprocess.run(self.command, shell=True, cwd=workdir, capture_output=True,
                                   text=True, timeout=15, check=False)
        finally:
            if mask_probe:
                probe_path.write_bytes(committed_probe)
        assert probe.returncode == (1 if broken and not mask_probe else 0), probe.stderr
        assert not git(workdir, "diff", "HEAD", "--", self.answer_file, self.probe_file)
        receipt = write_sidecar_json(
            self.state, kind="testkit_issue_probe", relative_ref=f"artifacts/testkit/worker-{commit}.json",
            payload={"command": self.command, "target_commit": commit,
                     "exit_code": probe.returncode, "stdout": probe.stdout, "stderr": probe.stderr,
                     "executed_probe_sha256": probe_digest,
                     "committed_probe_sha256": hashlib.sha256(committed_probe).hexdigest()},
            schema_version="testkit-issue-probe.v1", created_by=child["role_instance"],
        )
        check = completion_payload_template(
            contract_snapshot=snapshot, task_item=child, task_id=child["task_id"],
            run_id=child["run_id"], child_id=child["child_id"],
        )["impl_self_check"]
        check.update(source_commit=commit, target_commit=commit, evidence_refs=[receipt["ref"]])
        for row in check["command_receipts"]:
            row.update(target_commit=commit, exit_code=probe.returncode,
                       status="failed" if probe.returncode else "passed", evidence_refs=[receipt["ref"]])
        for row in check["acceptance_results"]:
            row.update(status="failed" if probe.returncode else "passed", evidence_refs=[receipt["ref"]])
        [attempt] = [event for event in self.events("task.attempt.sent")
                     if event.payload["dispatch_id"] == child["run_id"]]
        check["attempt_id"] = attempt.payload["attempt_id"]
        payload.update(
            fanout_id=dispatch.payload["fanout_id"], child_id=child["child_id"],
            run_id=child["run_id"], dispatch_id=child["run_id"],
            source_commit=commit,
            source_branch=child["source_branch"], workdir=str(workdir),
            impl_self_check=check, changed_files=[self.answer_file], evidence_refs=[receipt["ref"]],
            read_ledger_ref=ledger["ref"], read_ledger_digest=ledger["sha256"],
            input_consumption_status="satisfied",
            **{key: attempt.payload[key] for key in ("attempt_id", "lease_id")},
        )
        return self.publish_result(dispatch, payload)

    def publish_result(self, dispatch: ZfEvent, payload: dict) -> ZfEvent:
        assert payload.get("semantic_result_submit_mode", "off") == "off"
        return self.writer.append(ZfEvent(
            type="dev.build.done" if "impl_self_check" in payload else payload["canonical_success_event"],
            actor=payload["role_instance"],
            task_id=payload.get("task_id", dispatch.task_id), origin="worker", payload=payload,
            causation_id=dispatch.id, correlation_id=self.invoke.correlation_id,
        ))

    def candidate(self) -> tuple[Path, str]:
        [ready] = self.events("candidate.ready")
        [quality] = self.events("candidate.quality.passed")
        commit = ready.payload["candidate_head_commit"]
        path = Path(quality.payload["merger_worktree"])
        assert git(path, "rev-parse", "HEAD") == commit
        assert git(self.root, "rev-parse", ready.payload["candidate_ref"]) == commit
        assert quality.payload["commit"] == commit
        assert quality.payload["gate_source"] == "task_contract"
        checks = [row for rows in quality.payload["gate_checks"].values() for row in rows]
        assert len(checks) == 1
        assert checks[0]["command"] == self.command
        assert checks[0]["exit_code"] == 0 and checks[0]["passed"]
        assert checks[0]["stdout_tail"] == "issue regression passed\n"
        assert git(self.root, "show", f"{commit}:{self.answer_file}") == "fixed"
        assert git(self.root, "rev-parse", "main") == self.base
        assert not any(line.startswith("remote.") for line in
                       git(self.root, "config", "--local", "--list").splitlines())
        return path, commit

    def complete_verify(
        self, dispatch: ZfEvent, *, missing_reads: bool = False, stale_target: bool = False,
    ) -> ZfEvent:
        child = self.child(dispatch)
        payload = deepcopy(child["payload"])
        ledger = {} if missing_reads else self.read_inputs(child)
        snapshot = self.body(child["contract_snapshot_ref"], child["contract_snapshot_digest"])
        path, commit = self.candidate()
        assert child["target_commit"] == commit
        target = self.body(child["target_snapshot_ref"], child["target_snapshot_digest"])
        assert target["target_commit"] == commit
        probe = subprocess.run(self.command, shell=True, cwd=path, capture_output=True,
                               text=True, timeout=15, check=False)
        assert probe.returncode == 0, probe.stderr
        receipt = write_sidecar_json(
            self.state, f"artifacts/testkit/verify-{commit}.json",
            {"command": self.command, "target_commit": commit, "exit_code": probe.returncode,
             "stdout": probe.stdout, "stderr": probe.stderr},
            kind="testkit_issue_probe", schema_version="testkit-issue-probe.v1",
            created_by=child["role_instance"],
        )
        result = verification_result_template(snapshot)
        result.update({key: payload[key] for key in (
            *TASK_CONTRACT_CURRENTNESS_FIELDS, "contract_snapshot_ref", "contract_snapshot_digest",
            "target_snapshot_ref", "target_snapshot_digest", "target_commit",
        ) if key in payload})
        result["evidence_refs"] = [receipt["ref"]]
        result["failure_class"] = "none"
        for row in result["probe_receipts"]:
            assert row["command"] == self.command
            row.update(target_commit=commit, exit_code=probe.returncode, evidence_refs=[receipt["ref"]])
        for row in result["requirement_results"]:
            if row["status"] == "passed":
                row["evidence_refs"] = [receipt["ref"]]
        payload.update(
            status="completed", verification_result=result, evidence_refs=[receipt["ref"]],
            summary="One real candidate regression passed",
            report={"status": "passed", "summary": "One real candidate regression passed",
                    "findings": [], "recommendation": "approve", "evidence_refs": [receipt["ref"]]},
            read_ledger_ref=ledger.get("ref", ""), read_ledger_digest=ledger.get("sha256", ""),
            input_consumption_status="not_satisfied" if missing_reads else "satisfied",
        )
        if stale_target:
            # The baseline SHA exists but has never passed this regression.
            payload["target_commit"] = result["target_commit"] = self.base
            for row in result["probe_receipts"]:
                row["target_commit"] = self.base
        return self.publish_result(dispatch, payload)

    def complete_judge(self, dispatch: ZfEvent, *, blocked: bool = False) -> ZfEvent:
        child = self.child(dispatch)
        payload = deepcopy(child["payload"])
        ledger = self.read_inputs(child)
        claims = self.body(payload["goal_claim_set_ref"], payload["goal_claim_set_digest"])
        fact = self.body(payload["closure_fact_ref"], payload["closure_fact_digest"])
        assert fact["candidate_head_commit"] == payload["target_commit"]
        admitted = {event.payload["envelope_ref"]["ref"]: event.payload["envelope_ref"]
                    for event in self.events("workflow.call.result.admitted")}
        inputs = payload["input_result_refs"]
        assert inputs and set(inputs) <= admitted.keys()
        for ref in inputs:
            self.body(ref, admitted[ref]["sha256"])
        result = {key: payload[key] for key in (
            "workflow_run_id", "goal_id", "flow_kind", "task_map_generation", "target_commit",
            "objective_ref", "goal_claim_set_ref", "goal_claim_set_digest", "planning_result_ref",
            "candidate_ref", "closure_fact_ref", "closure_fact_digest",
        )}
        result.update(
            schema_version="goal-closure-result.v1", verdict="blocked" if blocked else "passed",
            goal_coverage=[{"goal_claim_id": claim["goal_claim_id"],
                            "status": "blocked" if blocked else "closed",
                            "supporting_result_refs": inputs} for claim in claims["claims"]],
            input_result_refs=inputs, open_gap_refs=[],
            recommended_action="human" if blocked else "complete",
            summary="Fixture needs external decision" if blocked else "Fixture claims match the admitted regression evidence",
        )
        payload.update(
            status="completed", goal_closure_result=result, evidence_refs=inputs,
            summary=result["summary"],
            report={"status": "passed", "summary": result["summary"],
                    "findings": [], "recommendation": "approve", "evidence_refs": inputs},
            read_ledger_ref=ledger["ref"], read_ledger_digest=ledger["sha256"],
            input_consumption_status="satisfied",
        )
        return self.publish_result(dispatch, payload)

    def assert_completed(self) -> None:
        [created] = self.events("task.created")
        for _ in range(12):
            self.tick()
            if self.events("run.goal.completed") and self.store.get(created.task_id).status == "done":
                break
        self.assert_no_closure_replay_drift()
        [terminal] = self.events("run.goal.completed")
        [claim] = self.events("run.goal.completion.claimed")
        [result] = self.events("goal.closure.synthesized")
        run_id = self.invoke.payload["workflow_run_id"]
        assert terminal.payload["run_id"] == run_id
        assert terminal.origin == result.origin == "kernel"
        assert claim.payload["run_id"] == run_id
        assert terminal.payload["claim_event_id"] == claim.id
        assert terminal.payload["source_event_id"] == result.id
        assert terminal.payload["verified_target_commit"] == terminal.payload["target_commit"]
        assert terminal.payload["candidate_head_commit"] == terminal.payload["target_commit"]
        assert terminal.payload["delivery_policy"] == "report_only"
        assert terminal.payload["completed_task_ids"] == [created.task_id]
        assert self.store.get(created.task_id).status == "done"
        rows = self.log.read_all()
        expected = ["task_map.ready", "candidate.ready", "test.passed", "flow.goal.closed",
                    "goal.closure.synthesized", "run.goal.completion.claimed", "run.goal.completed"]
        positions = []
        for kind in expected:
            [event] = self.events(kind)
            assert event.origin == "kernel", (kind, event.origin)
            positions.append(next(index for index, row in enumerate(rows) if row.id == event.id))
        assert positions == sorted(positions)
        [verify] = [event for event in rows if event.id == terminal.payload["verification_event_id"]]
        assert verify.type == "fanout.child.completed"
        assert verify.payload["stage_id"] == "issue-lanes-verify"
        assert verify.payload["target_commit"] == terminal.payload["target_commit"]
        assert verify.payload["admitted_call_result_ref"] == terminal.payload["verification_admitted_call_result_ref"]
        admitted = self.events("workflow.call.result.admitted")
        assert len(admitted) == 3
        for event in admitted:
            for key in ("envelope_ref", "control_result_ref", "read_ledger_ref"):
                descriptor = event.payload[key]
                raw = (self.state / descriptor["ref"]).read_bytes()
                assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
        self.candidate()
        assert not any(event.type.startswith("ship.") for event in self.log.read_all())

    def settled_snapshot(self) -> dict:
        kinds = {"workflow.invoke.requested", "fanout.child.dispatched", "fanout.child.completed",
                 "workflow.call.result.admitted", "candidate.ready", "candidate.quality.passed",
                 "test.passed", "goal.closure.synthesized", "run.goal.completion.claimed",
                 "run.goal.completed", "task.done"}
        return {"events": [event.to_json() for event in self.log.read_all() if event.type in kinds],
                "tasks": {task.id: task.status for task in self.store.list_all()},
                "candidate": self.candidate()[1]}

    def assert_no_closure_replay_drift(self) -> None:
        closures = self.events("flow.goal.closed")
        if len(closures) < 2:
            return
        dimensions = ("workflow_run_id", "goal_id", "task_map_generation", "candidate_head_commit",
                      "plan_artifact_package_id", "plan_artifact_package_digest")
        if not all(len({event.payload.get(key) for event in closures}) == 1 for key in dimensions):
            return  # A different generation/target is not this defect signature.
        identities = [event.payload["closure_identity"] for event in closures]
        facts = [self.body(event.payload["closure_fact_ref"], event.payload["closure_fact_digest"])
                 for event in closures]
        if len(set(identities)) != 2:
            return
        if {fact["source_event_type"] for fact in facts} != {"test.passed", "candidate.ready"}:
            return
        assert self.events("goal.closure.superseded")
        raise IssueClosureReplayDrift(
            f"same run/generation/candidate closure changed {identities}; "
            f"goal terminals={len(self.events('run.goal.completed'))}, "
            f"final dispatches={sum(event.payload['stage_id'] == 'issue-lanes-final' for event in self.events('fanout.child.dispatched'))}"
        )
