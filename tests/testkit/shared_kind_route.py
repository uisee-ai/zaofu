"""One project/runtime for formal General, Issue, PRD and Refactor requests."""

from pathlib import Path
import shlex
import sys

import pytest
import yaml

from tests.e2e.generic_workflow_complex_support import ROLE_NAMES
from tests.test_control_actions_workflow_request import _execute
from tests.testkit.general_route import GeneralRoute
from tests.testkit.issue_route import git
from tests.testkit.refactor_catalog_route import ReadyRecordingTransport
from zf.cli.flow import draft_multi_kind_project_spec
from zf.core.events import ZfEvent
from zf.core.profile.apply import materialize_config_skills
from zf.runtime import result_submit


class SharedKindRoute(GeneralRoute):
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch, *, concurrent: bool = False,
                 general_scope: tuple[str, ...] = ()) -> None:
        self.general_scope = general_scope
        super().__init__(root, monkeypatch)
        self.command = f"{shlex.quote(sys.executable)} tests/verify_issue.py"
        (root / "tests").mkdir()
        (root / "answer.txt").write_text("broken\n")
        (root / "tests/verify_issue.py").write_text(
            "from pathlib import Path\n"
            "assert Path('answer.txt').read_text().strip() == 'fixed'\n"
            "print('issue regression passed')\n"
        )
        (root / "docs/intake").mkdir(parents=True)
        (root / "docs/intake/bug.md").write_text("answer.txt must contain fixed.\n")
        self.source_root = root.parent / "source"
        self.source_root.mkdir()
        (self.source_root / "README.md").write_text("Read-only refactor source.\n")
        (self.source_root / "answer.txt").write_text("fixed\n")
        docs = draft_multi_kind_project_spec(
            backend="codex", lanes=1, project_name="testkit-shared-kinds",
            state_dir="custom-state", project_root=root, parity_scope=("core",),
        )
        for doc in docs:
            spec = doc["spec"]
            if doc["kind"] == "IssueFlow":
                spec["issueRef"] = "README.md"
            elif doc["kind"] == "PrdFlow":
                spec.update(prdRef="README.md", targetRoot=str(root))
            elif doc["kind"] == "RefactorFlow":
                spec.update(objectiveRef="README.md", sourceRoot=str(self.source_root), targetRoot=str(root))
            elif doc["kind"] == "ZfConfig":
                spec["goal"] = {"enabled": True}
                spec["quality_gates"] = {"test": {"required_checks": [self.command]}}
                spec["roles"] = [
                    {"name": role, "instance_id": role, "backend": "mock", "role_kind": "reader"}
                    for role in ROLE_NAMES
                ]
                spec.setdefault("workflow", {})["execution_profiles"] = {"direct-v1": {"strategy": "direct"}}
                spec["workflow"]["run_admission"] = {
                    "mode": "concurrent" if concurrent else "serial",
                    "max_active_runs": 2 if concurrent else 1,
                }
                spec["runtime"] = {
                    "workdirs": {"enabled": True, "mode": "worktree"},
                    "git": {"candidate_base_ref": "main", "remote_policy": "local_only",
                            "auto_ship_on_candidate_complete": False, "auto_ship_on_judge_passed": False},
                }
        self.config_path.write_text(yaml.safe_dump_all(docs, sort_keys=False))
        materialize_config_skills(self.config_path, root)
        paths = ("zf.yaml", "skills", "answer.txt", "tests/verify_issue.py")
        git(root, "add", "--", *paths)
        git(root, "commit", "-q", "-m", "fixture shared kind configuration", "--", *paths)
        self.base = git(root, "rev-parse", "HEAD")
        self.reopen()
        for role in self.config.roles:
            if role.name in self.credentials:
                continue
            token = f"testkit-public-shared-token-{role.name}"
            with monkeypatch.context() as issuance:
                issuance.setattr(result_submit.secrets, "token_urlsafe", lambda _size, token=token: token)
                path = result_submit.provision_role_submit_credential(self.state, role.name)
            self.credentials[role.name] = path.read_text().strip()
        assert self.config.workflow.run_admission.mode == ("concurrent" if concurrent else "serial")
        assert self.config.workflow.run_admission.max_active_runs == (2 if concurrent else 1)
        self.evidence.model_name = "same-runtime-four-kind-admission"
        self.evidence.source = Path(__file__).resolve()
        self.evidence.settings = "shared config/state/runtime; formal requests; scripted worker; bounded serial FIFO"

    def reopen(self) -> None:
        super().reopen()
        self.transport = ReadyRecordingTransport()
        self.runtime.transport = self.transport

    def prepare(self, *, pattern_id: str = "scope") -> tuple[Path, dict]:
        return super().prepare(pattern_id=pattern_id, scope=self.general_scope)

    def request_delivery(
        self,
        kind: str,
        *,
        scope: tuple[str, ...] = (),
        target_root: str | None = None,
        objective: str = "Set answer.txt to fixed and pass tests/verify_issue.py",
    ) -> ZfEvent:
        before = {event.id for event in self.events("workflow.invoke.requested")}
        proposal = _execute(self.service, "workflow-request", {
            "kind": kind, "objective": objective,
            "backend": "codex", "allow_missing_env": True,
            "scope": list(scope),
            **({"target_root": target_root or str(self.root)} if kind == "prd" else
               {"source_root": str(self.source_root),
                "target_root": target_root or str(self.root)}
               if kind == "refactor" else {}),
        })
        assert proposal["ok"] and proposal["status"] == "proposal_ready", proposal
        submitted = _execute(self.service, "workflow-submit", {
            **{key: proposal[key] for key in ("intake_ref", "request_id", "proposal_ref", "proposal_digest")},
            "kind": kind, "allow_missing_env": True,
        })
        assert submitted["ok"], submitted
        [invoke] = [event for event in self.events("workflow.invoke.requested") if event.id not in before]
        return invoke
