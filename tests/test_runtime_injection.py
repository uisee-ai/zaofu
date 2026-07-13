"""Tests for CLAUDE.md injection — role instruction generation."""

from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.injection import (
    generate_role_instructions,
    generate_task_briefing,
    write_task_briefing,
    build_task_prompt,
)
from zf.runtime.briefing_hydration import evaluate_instruction_hydration
from zf.core.config.schema import (
    ZfConfig,
    ProjectConfig,
    RoleConfig,
    ConstraintsConfig,
    RuntimeConfig,
    GitIsolationConfig,
    WorkflowConfig,
    WorkflowDagConfig,
)
from zf.core.feature.schema import Feature
from zf.core.skills import SkillLockEntry
from zf.core.task.schema import Task, TaskContract


class TestGenerateRoleInstructions:
    def setup_method(self):
        self.config = ZfConfig(
            project=ProjectConfig(name="test-project"),
        )
        self.role = RoleConfig(
            name="dev",
            backend="mock",
            allowed_tools=["python3", "rg"],
            constraints=ConstraintsConfig(
                allowed_paths=["src", "tests"],
                blocked_paths=[".zf", ".git"],
            ),
            stages=["implement"],
        )

    def test_returns_string(self):
        result = generate_role_instructions(self.config, self.role)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_role_identity(self):
        result = generate_role_instructions(self.config, self.role)
        assert "dev" in result

    def test_includes_project_name(self):
        result = generate_role_instructions(self.config, self.role)
        assert "test-project" in result

    def test_includes_allowed_paths(self):
        result = generate_role_instructions(self.config, self.role)
        assert "src" in result
        assert "tests" in result

    def test_includes_blocked_paths(self):
        result = generate_role_instructions(self.config, self.role)
        assert ".zf" in result

    def test_includes_allowed_tools(self):
        result = generate_role_instructions(self.config, self.role)
        assert "python3" in result

    def test_includes_event_commands(self):
        result = generate_role_instructions(self.config, self.role)
        assert "zf emit" in result

    def test_with_task(self):
        task = Task(
            id="TASK-001",
            title="Implement auth",
            contract=TaskContract(behavior="JWT login works"),
        )
        result = generate_role_instructions(self.config, self.role, task=task)
        assert "TASK-001" in result
        assert "Implement auth" in result
        assert "JWT login works" in result

    def test_without_task(self):
        result = generate_role_instructions(self.config, self.role)
        # should not crash, just no task section
        assert isinstance(result, str)

    def test_orchestrator_gets_different_instructions(self):
        orch_role = RoleConfig(name="orchestrator", backend="mock")
        result = generate_role_instructions(self.config, orch_role)
        assert "orchestrator" in result.lower()

    def test_skill_entries_include_description_and_runtime_path(self):
        role = RoleConfig(name="dev", backend="codex", skills=["test-driven-development"])
        entries = [
            SkillLockEntry(
                role="dev",
                instance_id="dev",
                backend="codex",
                task_id=None,
                run_id=None,
                name="test-driven-development",
                source="skills/test-driven-development/SKILL.md",
                sha256="abc",
                description="Drives development with tests.",
                materialized_to=".zf/workdirs/dev/codex-home/skills/test-driven-development",
            )
        ]

        result = generate_role_instructions(
            self.config,
            role,
            skill_entries=entries,
        )

        assert "/test-driven-development" in result
        assert "Drives development with tests." in result
        assert ".zf/workdirs/dev/codex-home/skills/test-driven-development" in result

    def test_run_contract_context_included_when_state_dir_ref_is_available(
        self,
        tmp_path: Path,
    ):
        state_dir = tmp_path / ".zf"
        (state_dir / "config").mkdir(parents=True)
        (state_dir / "config" / "run-contract.json").write_text(
            json.dumps({
                "schema_version": "run-contract.v1",
                "contract_digest": "digest-123",
                "workflow": {"kind": "refactor", "strictness": "full-parity"},
                "refs": {
                    "task_map": ["docs/task-map.json"],
                    "real_e2e_matrix": ["docs/real-e2e-matrix.json"],
                },
                "required_delivery_artifacts": [
                    {"name": "task_map", "required_for": "strict"},
                ],
            }),
            encoding="utf-8",
        )

        result = generate_role_instructions(
            self.config,
            self.role,
            state_dir_ref=state_dir,
            project_root=tmp_path,
        )

        assert "## Run Contract Context" in result
        assert "digest-123" in result
        assert "docs/task-map.json" in result
        assert "docs/real-e2e-matrix.json" in result

    def test_strict_hydration_stops_when_required_ref_group_empty(self, tmp_path: Path):
        state_dir = tmp_path / ".zf"
        (state_dir / "config").mkdir(parents=True)
        (state_dir / "config" / "run-contract.json").write_text(
            json.dumps({
                "schema_version": "run-contract.v1",
                "contract_digest": "digest-123",
                "workflow": {"kind": "refactor", "strictness": "full-parity"},
                "refs": {
                    "task_map": ["docs/task-map.json"],
                    "real_e2e_matrix": [],
                },
                "required_delivery_artifacts": [
                    {"name": "task_map", "required_for": "strict"},
                    {"name": "real_e2e_matrix", "required_for": "full-parity"},
                ],
            }),
            encoding="utf-8",
        )

        report = evaluate_instruction_hydration(
            state_dir,
            "## Run Contract Context\n\n- `task_map`:\n  - `docs/task-map.json`\n",
        )

        assert report["status"] == "STOP"
        assert report["missing_required_groups"] == ["real_e2e_matrix"]

    def test_skill_entries_are_split_by_injection_mode(self):
        role = RoleConfig(
            name="dev",
            backend="codex",
            skills=["scan-map", "impl-helper"],
        )
        entries = [
            SkillLockEntry(
                role="dev",
                instance_id="dev",
                backend="codex",
                task_id=None,
                run_id=None,
                name="scan-map",
                source="skills/scan-map/SKILL.md",
                sha256="scan",
                description="Scan the codebase.",
                auto_inject=True,
                load_on_demand=False,
            ),
            SkillLockEntry(
                role="dev",
                instance_id="dev",
                backend="codex",
                task_id=None,
                run_id=None,
                name="impl-helper",
                source="skills/impl-helper/SKILL.md",
                sha256="impl",
                description="Implementation reference.",
                auto_inject=False,
                load_on_demand=True,
            ),
        ]

        result = generate_role_instructions(
            self.config,
            role,
            skill_entries=entries,
        )

        assert "## Auto-Injected Skills" in result
        assert "## Load-On-Demand Skills" in result
        auto_section = result.split("## Auto-Injected Skills", 1)[1].split(
            "## Load-On-Demand Skills",
            1,
        )[0]
        demand_section = result.split("## Load-On-Demand Skills", 1)[1].split(
            "## Event Commands",
            1,
        )[0]
        assert "/scan-map" in auto_section
        assert "mode: auto-inject" in auto_section
        assert "/impl-helper" in demand_section
        assert "mode: load-on-demand" in demand_section

    def test_skill_dependencies_are_rendered_as_allowed_index_entries(self):
        role = RoleConfig(
            name="verify",
            backend="claude-code",
            skills=["zf-yoke-test-evaluator-role-context"],
        )
        entries = [
            SkillLockEntry(
                role="verify",
                instance_id="verify",
                backend="claude-code",
                task_id=None,
                run_id=None,
                name="zf-yoke-test-evaluator-role-context",
                source="skills/zf-yoke-test-evaluator-role-context/SKILL.md",
                sha256="ctx",
                description="Verification role context.",
                auto_inject=True,
                load_on_demand=False,
                dependencies=("verify-review",),
            ),
            SkillLockEntry(
                role="verify",
                instance_id="verify",
                backend="claude-code",
                task_id=None,
                run_id=None,
                name="verify-review",
                source="yoke/verify-review/SKILL.md",
                sha256="method",
                description="Verify review method.",
                materialized_to=".zf/workdirs/verify/project/.claude/skills/verify-review",
                dependency_of=("zf-yoke-test-evaluator-role-context",),
            ),
        ]

        result = generate_role_instructions(
            self.config,
            role,
            skill_entries=entries,
        )

        assert "/zf-yoke-test-evaluator-role-context" in result
        assert "/verify-review" in result
        assert "dependency of: zf-yoke-test-evaluator-role-context" in result
        assert "unlisted Claude skills" in result

    def test_codex_skill_entries_include_backend_discipline(self):
        role = RoleConfig(name="dev", backend="codex", skills=["impl-helper"])

        result = generate_role_instructions(self.config, role)

        assert "## Backend Skill Discipline" in result
        assert "role-local `CODEX_HOME`" in result
        assert "allowed skill index" in result
        assert "unlisted Codex skills" in result

    def test_claude_skill_entries_include_backend_discipline(self):
        role = RoleConfig(
            name="review",
            backend="claude-code",
            skills=["review-helper"],
        )

        result = generate_role_instructions(self.config, role)

        assert "## Backend Skill Discipline" in result
        assert "project-local `.claude/skills/`" in result
        assert "allowed skill index" in result
        assert "unlisted Claude skills" in result


class TestCompletionProtocol:
    # P2-1 (2026-04-20): completion events are inferred from
    # role.publishes, not a hardcoded role-name map. Tests must declare
    # publishes to exercise the real behavior (custom YAML roles work
    # the same way).

    def test_dev_gets_build_done_command(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="dev", publishes=["dev.build.done", "dev.blocked"])
        task = Task(id="T1", title="Build it")
        result = generate_role_instructions(config, role, task=task)
        assert "dev.build.done" in result
        assert "zf emit" in result
        assert "T1" in result

    def test_review_gets_approved_and_rejected(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(
            name="review",
            publishes=["review.approved", "review.rejected", "review.suspended"],
        )
        task = Task(id="T1", title="Review it")
        result = generate_role_instructions(config, role, task=task)
        assert "review.approved" in result
        assert "review.rejected" in result

    def test_test_gets_passed_and_failed(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(
            name="test",
            publishes=["test.passed", "test.failed", "test.suspended"],
        )
        task = Task(id="T1", title="Test it")
        result = generate_role_instructions(config, role, task=task)
        assert "test.passed" in result
        assert "test.failed" in result


class TestTaskBriefing:
    def test_generate_briefing(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="dev", publishes=["dev.build.done", "dev.blocked"])
        task = Task(
            id="T1",
            title="Build auth",
            contract=TaskContract(
                behavior="JWT works",
                scope=["src/auth.py"],
            ),
        )
        briefing = generate_task_briefing(config, role, task)
        assert "T1" in briefing
        assert "Build auth" in briefing
        assert "JWT works" in briefing
        assert "dev.build.done" in briefing
        assert "raw relative paths" in briefing
        assert "zf guard ownership --task T1 --actor dev" in briefing
        assert "do not emit completion" in briefing
        assert "zf emit worker.heartbeat" in briefing

    def test_briefing_uses_configured_zf_cli_cmd(self, monkeypatch):
        monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="dev", publishes=["dev.build.done", "dev.blocked"])
        task = Task(id="T1", title="Build auth")

        briefing = generate_task_briefing(config, role, task)

        assert "uv --project /repo run zf guard ownership --task T1 --actor dev" in briefing
        assert "uv --project /repo run zf emit dev.build.done --task T1" in briefing
        assert "uv --project /repo run zf emit worker.heartbeat" in briefing

    def test_feature_context_preserves_scope_for_small_task_slices(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="dev", publishes=["dev.build.done"])
        task = Task(id="T1", title="Build slice")
        feature = Feature(
            id="F-1",
            title="Ship product",
            description="Deliver the full product flow.",
        )

        briefing = generate_task_briefing(config, role, task, feature=feature)

        assert "Small tasks reduce execution granularity only" in briefing
        assert "they do not reduce the planned product scope" in briefing

    def test_completion_payload_requires_snapshot_ref(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="dev", backend="codex", publishes=["dev.build.done"])
        task = Task(id="T1", title="Build it")

        briefing = generate_task_briefing(config, role, task)

        assert '"snapshot_ref": "<snapshot_ref from Runtime Snapshot section>"' in briefing
        assert "Do not omit it from terminal completion payloads" in briefing

    def test_artifact_manifest_guidance_mentions_workdir_resolved_path(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="arch", backend="codex", publishes=["arch.proposal.done"])
        task = Task(id="T1", title="Plan it")

        briefing = generate_task_briefing(config, role, task)

        assert "`workdir_path`" in briefing
        assert "`hash_status.resolved_path`" in briefing

    def test_briefing_points_to_task_doc_as_primary_contract(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="dev", publishes=["dev.build.done", "dev.blocked"])
        task = Task(
            id="T1",
            title="Build auth",
            active_dispatch_id="disp-1",
            contract=TaskContract(
                behavior="JWT works",
                spec_ref="docs/spec.md",
                plan_ref="docs/plan.md",
                tdd_ref="tests/test_auth.py",
                acceptance_criteria=["login succeeds", "logout succeeds"],
                evidence_contract={"required_events": ["dev.build.done"]},
            ),
        )

        briefing = generate_task_briefing(
            config,
            role,
            task,
            task_doc_path=".zf/task_docs/T1/task.md",
            source_doc_path=".zf/task_docs/T1/source.md",
            progress_doc_path=".zf/task_docs/T1/progress.md",
            source_revision="source-r1",
            contract_revision="contract-r1",
            capsule_revision="capsule-r1",
        )

        assert "Read Order (authoritative)" in briefing
        assert ".zf/task_docs/T1/task.md" in briefing
        assert ".zf/task_docs/T1/source.md" in briefing
        assert ".zf/task_docs/T1/progress.md" in briefing
        assert "source_revision: `source-r1`" in briefing
        assert "contract_revision: `contract-r1`" in briefing
        assert "capsule_revision: `capsule-r1`" in briefing
        assert "workers must not edit task.md to mark completion" in briefing
        assert "`spec_ref`: `docs/spec.md`" in briefing
        assert "`plan_ref`: `docs/plan.md`" in briefing
        assert "`tdd_ref`: `tests/test_auth.py`" in briefing
        assert "login succeeds" in briefing
        assert "required_events" in briefing

    def test_briefing_includes_replan_amendment_context_from_evidence_contract(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="dev", publishes=["dev.build.done", "dev.blocked"])
        task = Task(
            id="ISSUE-123-GAP-001",
            title="Fill replan gap",
            contract=TaskContract(
                behavior="API regression gap is closed",
                evidence_contract={
                    "replan_history_ref": "docs/plans/ISSUE-123/replan-history.jsonl",
                    "affected_tasks": ["ISSUE-123-PLAN-001"],
                    "gate_changes": ["require API regression evidence"],
                },
            ),
        )

        briefing = generate_task_briefing(config, role, task)

        assert "### Replan Amendment Context" in briefing
        assert "`replan_history_ref`: `docs/plans/ISSUE-123/replan-history.jsonl`" in briefing
        assert "ISSUE-123-PLAN-001" in briefing
        assert "require API regression evidence" in briefing

    def test_gate_briefing_explains_dispatch_semantics(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="review", publishes=["review.approved", "review.rejected"])
        task = Task(id="T1", title="Review it", active_dispatch_id="disp-review")

        briefing = generate_task_briefing(config, role, task)

        assert "Use the active dispatch id from this briefing for your `zf emit`" in briefing
        assert "verification_tiers" in briefing
        assert "test/behavior/..." in briefing
        assert "tier: e2e" in briefing
        assert "transient role/gate routing id" in briefing
        assert "Do not require product source" in briefing
        assert "static scorecards" in briefing
        assert "Read-only gate rule" in briefing
        assert "changed_files: []" in briefing

    def test_arch_briefing_requires_structured_handoff_evidence(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="arch", publishes=["arch.proposal.done"])
        task = Task(id="T1", title="Design it")
        briefing = generate_task_briefing(config, role, task)

        assert "Handoff Evidence Required" in briefing
        assert "Do not edit implementation files" in briefing
        assert "Do not produce the final accepted runtime deliverable" in briefing
        assert "run full/long verification" in briefing
        assert "file_plan" in briefing
        assert "test_plan" in briefing
        assert "full-stage plan" in briefing
        assert "backlog draft" in briefing
        assert "`draft` or `proposed`" in briefing
        assert "Orchestrator owns final acceptance/merge" in briefing
        assert "do not need to scrape your transcript" in briefing
        assert "artifact.manifest.published" in briefing
        assert "full plan markdown" in briefing
        assert "current_stage: design" in briefing
        assert (
            "required_next_event: artifact.manifest.published -> "
            "arch.proposal.done"
        ) in briefing

    def test_critic_briefing_requires_verdict_payload(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="critic", publishes=["design.critique.done", "gate.failed"])
        task = Task(id="T1", title="Critique it")
        briefing = generate_task_briefing(config, role, task)

        assert "verdict" in briefing
        assert "evidence_refs" in briefing
        assert "Do not edit implementation files" in briefing
        assert "do not replace dev/review/test/judge" in briefing
        assert "Do not run full test suites" in briefing
        assert "artifact.manifest.published" in briefing
        assert "`draft`, `proposed`, or `accepted`" in briefing
        assert "candidate package" in briefing
        assert "not transcript-only prose" in briefing
        assert "current_stage: design_review" in briefing
        assert (
            "required_next_event: artifact.manifest.published -> "
            "design.critique.done"
        ) in briefing

    def test_verify_lane_briefing_requires_evidence_refs(self):
        """LB-3: fanout verify-lane completes on lane.stage.completed and must
        get the evidence clause, else it ships empty evidence_refs (U20
        stage.report.evidence_missing on the light baseline)."""
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="verify-lane-0", publishes=["lane.stage.completed"])
        task = Task(id="T1", title="Verify the candidate")
        briefing = generate_task_briefing(config, role, task)

        assert "Handoff Evidence Required" in briefing
        assert "evidence_refs" in briefing

    def test_briefing_prefers_configured_workflow_stage(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        task = Task(id="T1", title="Implement it")

        dev_role = RoleConfig(
            name="dev",
            publishes=["dev.build.done", "dev.blocked"],
            stages=["impl"],
        )
        review_role = RoleConfig(
            name="review",
            publishes=["review.approved", "review.rejected"],
            stages=["verify"],
        )

        dev_briefing = generate_task_briefing(config, dev_role, task)
        review_briefing = generate_task_briefing(config, review_role, task)

        assert "current_stage: impl" in dev_briefing
        assert "current_stage: verify" in review_briefing

    def test_advisory_briefing_includes_6field_contract_block(self):
        """EVAL-PAYLOAD-CONTRACT-001 advisory-gap fix.

        Live e2e (2026-05-18) showed arch.proposal.done / design.critique.done
        tripping ``task.contract.invalid`` because the briefing never told the
        LLM that the kernel requires ``changed_files`` (advisory roles pass
        ``[]``) plus ``residual_risks`` / ``next_agent_input`` WARN fields.
        """
        config = ZfConfig(project=ProjectConfig(name="test"))
        task = Task(id="T1", title="Plan it")
        for role_name, success_event in (
            ("arch", "arch.proposal.done"),
            ("critic", "design.critique.done"),
        ):
            role = RoleConfig(name=role_name, publishes=[success_event])
            briefing = generate_task_briefing(config, role, task)
            assert "6-field contract" in briefing, f"{role_name} missing 6-field heading"
            assert "EVAL-PAYLOAD-CONTRACT-001" in briefing
            # Advisory line must explicitly tell the LLM to pass [].
            assert "pass `[]`" in briefing, f"{role_name} missing empty-list hint"
            assert "residual_risks" in briefing
            assert "next_agent_input" in briefing

    def test_manifest_publisher_briefing_includes_artifact_refs_field_contract(self):
        """Payload-contract education for artifact.manifest.published.

        cj-mono / calc full-flow E2E: arch emitted ``version: "v0.1"`` and
        critic an out-of-enum ``status``; both were rejected as
        ``artifact.manifest.rejected`` and the handoff stalled / retried. The
        briefing must spell out the artifact_refs field contract so the LLM
        emits valid values the first time.
        """
        config = ZfConfig(project=ProjectConfig(name="test"))
        task = Task(id="T1", title="Plan it")
        for role_name, success_event in (
            ("arch", "arch.proposal.done"),
            ("critic", "design.critique.done"),
        ):
            role = RoleConfig(name=role_name, publishes=[success_event])
            briefing = generate_task_briefing(config, role, task)
            assert "artifact_refs field contract" in briefing, role_name
            assert "a positive integer (1, 2, 3" in briefing, role_name
            assert (
                "draft, proposed, accepted, superseded, rejected" in briefing
            ), role_name
            assert "repo-relative path, never absolute" in briefing, role_name

    def test_verifier_briefing_includes_verification_command_fidelity(self):
        """B7: judge self-verified with bare `python` → exit 127 on a
        python3-only host → spurious judge.failed (bypassing the deterministic
        gate). The verifier briefing must steer to the configured command /
        python3 and not fail the gate on a substituted missing binary.
        """
        config = ZfConfig(project=ProjectConfig(name="test"))
        task = Task(id="T1", title="Verify it")
        for role_name, success_event in (
            ("review", "review.approved"),
            ("test", "test.passed"),
            ("judge", "judge.passed"),
        ):
            role = RoleConfig(name=role_name, publishes=[success_event])
            briefing = generate_task_briefing(config, role, task)
            assert "Verification-command fidelity" in briefing, role_name
            assert "python3" in briefing, role_name
            assert "127" in briefing, role_name

    def test_worker_example_payload_includes_changed_files_and_warn_fields(self):
        """dev/review/test/judge example JSON must have changed_files +
        residual_risks + next_agent_input so the LLM copies all six fields."""
        config = ZfConfig(project=ProjectConfig(name="test"))
        task = Task(id="T1", title="Build it", active_dispatch_id="d-1")
        for role_name in ("dev", "review", "test", "judge"):
            role = RoleConfig(name=role_name, publishes=[f"{role_name}.build.done"])
            briefing = generate_task_briefing(config, role, task)
            assert '"changed_files"' in briefing, f"{role_name} example missing changed_files"
            assert '"residual_risks"' in briefing, f"{role_name} example missing residual_risks"
            assert '"next_agent_input"' in briefing, f"{role_name} example missing next_agent_input"
        # Verifier roles also get tests_run in the example.
        for verifier in ("test", "judge"):
            role = RoleConfig(name=verifier, publishes=[f"{verifier}.passed"])
            briefing = generate_task_briefing(config, role, task)
            assert '"tests_run"' in briefing, f"{verifier} example missing tests_run"

    def test_verifier_alias_with_test_passed_gets_tests_run_example(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="qa", publishes=["test.passed", "test.failed"])
        task = Task(id="T1", title="QA it", active_dispatch_id="d-qa")

        briefing = generate_task_briefing(config, role, task)

        assert '"changed_files": []' in briefing
        assert '"tests_run"' in briefing

    def test_briefing_state_packet_ref_uses_configured_state_dir(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="qa", publishes=["test.passed"])
        task = Task(id="T1", title="QA it", active_dispatch_id="disp-qa")

        briefing = generate_task_briefing(
            config,
            role,
            task,
            state_dir_ref=".zf-mini-codex",
        )

        assert ".zf-mini-codex/briefings/T1/disp-qa/state-packet.json" in briefing
        assert ".zf/briefings/T1/disp-qa/state-packet.json" not in briefing

    def test_dev_briefing_does_not_require_remote_push_by_default(self):
        config = ZfConfig(project=ProjectConfig(name="test"))
        role = RoleConfig(name="dev", publishes=["dev.build.done"])
        task = Task(id="T1", title="Build it", active_dispatch_id="d-1")

        briefing = generate_task_briefing(config, role, task)

        assert "git push origin" not in briefing
        assert "runtime.git.remote_policy=local" in briefing
        assert "Do NOT `git push` unless" in briefing
        assert "local commit sha" in briefing
        assert "Do NOT stop after green tests or final prose" in briefing
        assert "terminal `zf emit` command" in briefing

    def test_dev_briefing_documents_required_remote_policy(self):
        config = ZfConfig(
            project=ProjectConfig(name="test"),
            runtime=RuntimeConfig(git=GitIsolationConfig(remote_policy="required")),
        )
        role = RoleConfig(name="dev", publishes=["dev.build.done"])
        task = Task(id="T1", title="Build it", active_dispatch_id="d-1")

        briefing = generate_task_briefing(config, role, task)

        assert "runtime.git.remote_policy=required" in briefing
        assert "remote publication is required" in briefing
        assert "emit `dev.blocked`" in briefing
        assert "retrying blind pushes" in briefing

    def test_dev_briefing_documents_local_only_remote_policy(self):
        config = ZfConfig(
            project=ProjectConfig(name="test"),
            runtime=RuntimeConfig(git=GitIsolationConfig(remote_policy="local_only")),
        )
        role = RoleConfig(name="dev", publishes=["dev.build.done"])
        task = Task(id="T1", title="Build it", active_dispatch_id="d-1")

        briefing = generate_task_briefing(config, role, task)

        assert "runtime.git.remote_policy=local_only" in briefing
        assert "do not push to external remotes" in briefing
        assert "local bare remote" in briefing

    def test_write_briefing(self, tmp_path: Path):
        task = Task(id="T1", title="Build auth")
        briefing = "## Task: T1\nBuild auth\n"
        path = write_task_briefing(tmp_path, "dev", task, briefing)
        assert path.exists()
        assert "T1" in path.read_text()

        # Also writes JSON
        task_json = tmp_path / "briefings" / "T1.json"
        assert task_json.exists()

    def test_write_briefing_json_includes_dispatch_semantics(self, tmp_path: Path):
        task = Task(
            id="T1",
            title="Build auth",
            active_dispatch_id="disp-dev",
            contract=TaskContract(
                spec_ref="docs/spec.md",
                plan_ref="docs/plan.md",
                tdd_ref="tests/test_auth.py",
                acceptance_criteria=["login succeeds"],
                evidence_contract={"required_events": ["dev.build.done"]},
            ),
        )
        path = write_task_briefing(tmp_path, "dev", task, "## Task: T1\n")
        task_json = path.parent / "T1.json"

        data = json.loads(task_json.read_text())

        assert data["active_dispatch_id"] == "disp-dev"
        assert data["dispatch_semantics"]["active_dispatch_id"].startswith("Transient")
        assert "static scorecards" in data["dispatch_semantics"]["product_artifacts"]
        assert data["contract"]["spec_ref"] == "docs/spec.md"
        assert data["contract"]["plan_ref"] == "docs/plan.md"
        assert data["contract"]["tdd_ref"] == "tests/test_auth.py"
        assert data["contract"]["acceptance_criteria"] == ["login succeeds"]
        assert data["contract"]["evidence_contract"]["required_events"] == [
            "dev.build.done",
        ]

    def test_write_briefing_json_includes_task_doc_binding(self, tmp_path: Path):
        task = Task(id="T1", title="Build auth", active_dispatch_id="disp-dev")
        path = write_task_briefing(
            tmp_path,
            "dev",
            task,
            "## Task: T1\n",
            task_doc_path=tmp_path / ".zf" / "task_docs" / "T1" / "task.md",
            source_doc_path=tmp_path / ".zf" / "task_docs" / "T1" / "source.md",
            progress_doc_path=tmp_path / ".zf" / "task_docs" / "T1" / "progress.md",
            source_revision="source-r1",
            contract_revision="contract-r1",
            capsule_revision="capsule-r1",
        )
        task_json = path.parent / "T1.json"

        data = json.loads(task_json.read_text())

        assert data["task_doc"]["path"].endswith("task_docs/T1/task.md")
        assert data["task_doc"]["source_doc"].endswith("task_docs/T1/source.md")
        assert data["task_doc"]["progress_doc"].endswith("task_docs/T1/progress.md")
        assert data["task_doc"]["source_revision"] == "source-r1"
        assert data["task_doc"]["contract_revision"] == "contract-r1"
        assert data["task_doc"]["capsule_revision"] == "capsule-r1"
        assert data["task_doc"]["source"] == "kernel_projection"
        assert data["task_doc"]["worker_may_mark_done"] is False

    def test_completion_protocol_teaches_memory_note_emit(self):
        """G-MEM-1: every role's completion protocol mentions memory.note."""
        config = ZfConfig(project=ProjectConfig(name="test"))
        task = Task(id="T1", title="Do something", contract=TaskContract())
        for role_name in ("dev", "arch", "review", "test", "judge"):
            role = RoleConfig(name=role_name, backend="mock")
            text = generate_role_instructions(config, role, task=task)
            assert "memory.note" in text, f"{role_name} instructions missing memory.note"
            assert "decision" in text and "pattern" in text and "fix" in text
            assert "max_days" in text.lower() or "decay" in text.lower() or "30" in text

    def test_configured_writer_completion_contract_from_event_schema(self):
        config = ZfConfig(
            project=ProjectConfig(name="test"),
            workflow=WorkflowConfig(
                dag=WorkflowDagConfig(
                    event_schemas={
                        "impl.done": {
                            "required": ["source_commit", "evidence_refs"]
                        }
                    }
                )
            ),
        )
        role = RoleConfig(
            name="builder-lane-7",
            backend="mock",
            role_kind="writer",
            publishes=["impl.done", "impl.failed"],
        )

        text = generate_role_instructions(config, role)

        assert "Configured Completion Payload Contract" in text
        assert "`impl.done` is declared" in text
        assert "`source_commit`" in text
        assert "`evidence_refs`" in text

    def test_configured_reader_completion_contract_defaults_changed_files_empty(self):
        config = ZfConfig(
            project=ProjectConfig(name="test"),
            workflow=WorkflowConfig(
                dag=WorkflowDagConfig(
                    event_schemas={
                        "quality.approved": {
                            "required": ["changed_files", "evidence_refs"]
                        }
                    }
                )
            ),
        )
        role = RoleConfig(
            name="contract-gate",
            backend="mock",
            role_kind="reader",
            publishes=["quality.approved", "quality.rejected"],
        )

        text = generate_role_instructions(config, role)

        assert "Read-only role note" in text
        assert '"changed_files": []' in text
        assert "`evidence_refs`" in text

    def test_record_what_you_learned_is_optional_hint(self):
        """K5(2026-06-11)有据反转 G-MEM-1:当年升级为 checkpoint 是因
        workers 跳过 Optional 节;审计 Q2 判定该'必须评估'块为无门高危
        prose,且 kernel auto-promote 已兜底关键事件(candidate.conflict/
        dev.blocked)—— 残余价值不抵 briefing 噪音,降级为单行提示。"""
        config = ZfConfig(project=ProjectConfig(name="test"))
        task = Task(id="T1", title="Do something", contract=TaskContract())
        role = RoleConfig(name="dev", backend="mock")
        text = generate_role_instructions(config, role, task=task)
        assert "可选:记录跨会话经验" in text
        assert "Emit when" not in text  # 长清单已撤
        assert "memory.note" in text    # 命令示例仍在

    def test_build_task_prompt_points_at_briefing_and_instructions(self, tmp_path: Path):
        briefing_path = tmp_path / "briefings" / "dev-T1.md"
        briefing_path.parent.mkdir(parents=True)
        briefing_path.write_text("## Task: T1\nBuild auth\n")
        prompt = build_task_prompt("dev", briefing_path)
        assert str(briefing_path) in prompt
        assert "instructions/dev.md" in prompt
        assert "completion protocol" in prompt

    def test_build_task_prompt_uses_role_name_for_instructions_path(self, tmp_path: Path):
        briefing_path = tmp_path / "briefings" / "review-T2.md"
        briefing_path.parent.mkdir(parents=True)
        briefing_path.write_text("## Task: T2\n")
        prompt = build_task_prompt("review", briefing_path)
        assert "instructions/review.md" in prompt
        assert "instructions/dev.md" not in prompt

    def test_build_task_prompt_for_fanout_child_does_not_require_task_doc(
        self,
        tmp_path: Path,
    ):
        briefing_path = tmp_path / "briefings" / "review-fanout.md"
        briefing_path.parent.mkdir(parents=True)
        briefing_path.write_text("## Fanout Reader Child\n")
        prompt = build_task_prompt(
            "review",
            briefing_path,
            prompt_kind="fanout_child",
        )

        assert str(briefing_path) in prompt
        assert "fanout child briefing" in prompt
        assert "target_ref" in prompt
        assert "do not look for a task.md" in prompt
        assert "load the kernel-managed task.md" not in prompt

    def test_build_task_prompt_for_fanout_synth_does_not_require_task_doc(
        self,
        tmp_path: Path,
    ):
        briefing_path = tmp_path / "briefings" / "review-synth.md"
        briefing_path.parent.mkdir(parents=True)
        briefing_path.write_text("## Fanout Synthesis\n")
        prompt = build_task_prompt(
            "review-synth",
            briefing_path,
            prompt_kind="fanout_synth",
        )

        assert str(briefing_path) in prompt
        assert "fanout synthesis briefing" in prompt
        assert "child reports" in prompt
        assert "do not look for a task.md" in prompt
        assert "load the kernel-managed task.md" not in prompt


def test_completion_contract_names_ref_grammar():
    """ref 语法契约(2026-07-08 第四批):evidence_refs 条目 = <scheme>:<value>
    结构化引用或真实存在的裸路径——agent scheme 拼写漂移(task_map:/task-map:)
    与裸路径虚指都靠这句合同教育 + completion_honesty 按盘核验兜底。"""
    from zf.runtime.injection import _append_completion_contract_block

    lines: list[str] = []
    _append_completion_contract_block(lines, advisory=False)
    text = "\n".join(lines)
    assert "`<scheme>:<value>` reference" in text
    assert "actually exists on disk" in text
    assert "completion honesty gate" in text
