"""Two independent catalog lanes, real aggregate Git/probes and signed results."""

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess
import sys

import yaml

from tests.testkit.issue_route import git
from tests.testkit.refactor_strong_route import StrongRefactorRoute
from zf.runtime.sidecar_refs import write_sidecar_json


class TwoLaneStrongRefactorRoute(StrongRefactorRoute):
    second_task_id = "TASK-REFACTOR-SECOND"
    second_probe = "tests/verify_second.py"
    product_probe = "tests/verify_product.py"

    @staticmethod
    def probe_command(script: str) -> str:
        return f"{shlex.quote(sys.executable)} {script}"

    def __init__(self, root, monkeypatch):
        super().__init__(root, monkeypatch)
        (root / "answer-second.txt").write_text("broken\n")
        (self.source_root / "answer-second.txt").write_text("fixed\n")
        (root / self.second_probe).write_text(
            "from pathlib import Path\n"
            "assert Path('answer-second.txt').read_text().strip() == 'fixed'\n"
            "print('issue regression passed')\n",
        )
        (root / self.product_probe).write_text(
            "from pathlib import Path\n"
            "for name in ('answer.txt', 'answer-second.txt'):\n"
            "    assert Path(name).read_text().strip() == 'fixed', name\n"
            "print('two-lane product passed')\n",
        )
        (root / "docs/intake/bug.md").write_text(
            "# Refactor both answers\nanswer.txt and answer-second.txt must contain fixed.\n",
        )
        paths = ("answer-second.txt", self.second_probe, self.product_probe, "docs/intake/bug.md")
        git(root, "add", *paths)
        git(root, "commit", "-q", "-m", "fixture two-lane baseline", "--", *paths)
        self.base = git(root, "rev-parse", "main")
        self.evidence.model_name = "official-refactor-two-lane"
        self.evidence.source = Path(__file__).resolve()
        self.evidence.settings = "catalog two lanes; signed results; independent files/probes; local product acceptance/ship"

    def flow_spec(self):
        spec = super().flow_spec()
        template = Path(__file__).parents[2] / "examples/prod/controller/refactor-lane-v3.yaml"
        spec["lanes"] = next(yaml.safe_load_all(template.read_text()))["spec"]["lanes"]
        assert spec["lanes"] == 2
        return spec

    def plan_result(self):
        plan = super().plan_result()
        task_map = plan["task_map"]
        first = task_map["tasks"][0]
        first["affinity_tag"] = "first-answer"
        second = deepcopy(first)
        second.update(task_id=self.second_task_id, title="Deliver the second answer",
                      affinity_tag="second-answer", scope=["answer-second.txt"],
                      allowed_paths=["answer-second.txt"], goal_claim_ids=["CLAIM-SECOND"],
                      verification=self.probe_command(self.second_probe),
                      verification_read_paths=[self.second_probe, self.product_probe])
        second["acceptance_criteria"][0].update(id="AC-SECOND", statement="answer-second.txt contains fixed",
                                               verification_command_ids=["second-probe"])
        second["validation"]["commands"][0].update(id="second-probe", command=second["verification"],
                                                  acceptance_ids=["AC-SECOND"])
        task_map["goal_claims"].append({"goal_claim_id": "CLAIM-SECOND", "mandatory": True,
                                       "text": "answer-second.txt contains fixed"})
        task_map["tasks"].append(second)
        source_index = json.loads((self.state / plan["source_index_ref"]).read_text())
        source_index["tasks"].append({"task_id": self.second_task_id, "source_key": "second-answer",
                                      "source_ref": "docs/intake/bug.md",
                                      "source_excerpt": "answer-second.txt must contain fixed."})
        refs = []
        for name, body, key in (("task-map", task_map, "task_map_ref"),
                                ("source-index", source_index, "source_index_ref")):
            ref = write_sidecar_json(self.state, f"artifacts/{self.artifact_namespace}/{name}-two.json",
                                     body, kind=name, schema_version=f"{name}.v1", created_by="planner")
            plan[key] = ref["ref"]
            refs.append(ref["ref"])
        plan["evidence_refs"] = refs
        spec = plan["plan_ports"][0]["body"]
        spec["entrypoints"][0].update(start_command=self.probe_command(self.product_probe),
                                       health_check=self.probe_command(self.product_probe))
        spec["user_journeys"][0]["assertions"][0]["statement"] = "Both lane answers contain fixed"
        return plan

    @contextmanager
    def task_probe(self, dispatch):
        previous = self.answer_file, self.probe_file, self.command
        if dispatch.payload["task_id"] == self.second_task_id:
            self.answer_file, self.probe_file = "answer-second.txt", self.second_probe
            self.command = self.probe_command(self.second_probe)
        try:
            yield
        finally:
            self.answer_file, self.probe_file, self.command = previous

    def complete_writer(self, dispatch, **kwargs):
        with self.task_probe(dispatch):
            return super().complete_writer(dispatch, **kwargs)

    def complete_verify(self, dispatch, **kwargs):
        with self.task_probe(dispatch):
            return super().complete_verify(dispatch, **kwargs)

    def candidate(self):
        [ready], [quality] = self.events("candidate.ready"), self.events("candidate.quality.passed")
        commit = ready.payload["candidate_head_commit"]
        path = Path(quality.payload["merger_worktree"])
        assert git(path, "rev-parse", "HEAD") == commit == quality.payload["commit"]
        assert git(self.root, "rev-parse", ready.payload["candidate_ref"]) == commit
        assert git(self.root, "rev-parse", "main") == self.base
        checks = [row for rows in quality.payload["gate_checks"].values() for row in rows]
        assert {row["command"] for row in checks} == {
            self.probe_command("tests/verify_issue.py"), self.probe_command(self.second_probe),
        }
        assert all(row["exit_code"] == 0 and row["passed"] for row in checks)
        for name in ("answer.txt", "answer-second.txt"):
            assert git(self.root, "show", f"{commit}:{name}") == "fixed"
        return path, commit

    def publish_result(self, dispatch, payload):
        if "verification_result" in payload:
            path, commit = self.candidate()
            command = self.probe_command(self.product_probe)
            probe = subprocess.run(command, shell=True, cwd=path, capture_output=True, text=True, timeout=15)
            assert probe.returncode == 0, probe.stderr
            ref = write_sidecar_json(
                self.state, f"artifacts/testkit/product-{dispatch.payload['child_id']}-{commit}.json",
                {"command": command, "target_commit": commit, "exit_code": probe.returncode,
                 "stdout": probe.stdout, "stderr": probe.stderr}, kind="testkit_product_probe",
                schema_version="testkit-product-probe.v1", created_by=payload["role_instance"],
            )
            payload["evidence_refs"] = [*payload["evidence_refs"], ref["ref"]]
        return super().publish_result(dispatch, payload)
