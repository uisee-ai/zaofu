"""Explicit deterministic TestKit stage registry.

The registry is test infrastructure, not ZaoFu runtime configuration.  Keeping
node paths explicit prevents a newly added host/performance test from silently
entering the ordinary correctness gate.
"""

from __future__ import annotations

from typing import Final

SUITES: Final[dict[str, tuple[str, ...]]] = {
    "S0": ("tests/conformance/test_zaofu_testkit_s0.py",),
    "S1": (
        "tests/test_testkit_s1.py",
        "tests/test_testkit_s1_lifecycle.py",
        # Runner self-tests exercise fail-closed verdicts in fresh nested
        # processes; they do not invoke the stage registry recursively.
        "tests/test_testkit_runner.py",
        "tests/testkit/test_memray_guard.py",
        # Long allocation work is not run by default; this deterministic
        # contract only validates fixed segment planning/reconciliation and
        # rejects stitched or incomplete evidence.
        "tests/testkit/test_allocation_segments.py",
        # Open incident root causes require a fresh same-signature failure;
        # normal green controls and historical reports cannot reopen them.
        "tests/testkit/test_incident_reentry.py",
        # OS crash evidence is admitted per exact fault model; open/destructive
        # models cannot be inferred from an existing QEMU or guest-panic run.
        "tests/testkit/test_os_fault_models.py",
        # Guest filesystem corruption has a method/evidence contract only;
        # destructive VM injection remains an explicit host tier.
        "tests/testkit/test_guest_filesystem_corruption.py",
        # Physical host power loss is method-only; actual power control is
        # never part of the deterministic S1 tier.
        "tests/testkit/test_physical_host_power_loss.py",
        # Read-only inventory for open host/incident tiers; never authorizes
        # provider, VM, Memray, or fault execution.
        "tests/testkit/test_open_item_readiness.py",
        # Final same-source acceptance is a read-only readiness check; it is
        # never release authorization and rejects missing/mixed evidence.
        "tests/testkit/test_unified_acceptance.py",
        # Current need-to-do status inventory separates operator skips,
        # missing historical evidence, open destructive models, and completed
        # framework-only capabilities; it never computes product completion.
        "tests/testkit/test_need_to_do_inventory.py",
        # Single-child supervision uses bounded local stand-ins; actual QEMU
        # remains in the explicit host tier, never the default registry.
        "tests/testkit/test_isolated_vm_supervisor.py",
        "tests/testkit/test_isolated_vm_payload.py",
        "tests/testkit/test_isolated_vm_guest.py",
        "tests/testkit/test_isolated_vm_run.py",
        "tests/testkit/test_isolated_vm_panic_probe.py",
        "tests/testkit/test_model_evidence.py",
        "tests/testkit/test_phase_reports.py",
        "tests/testkit/test_native_acceptance.py",
        "tests/testkit/test_requirement_audit.py",
        "tests/testkit/test_native_run.py",
        "tests/testkit/test_source_observation.py",
        "tests/testkit/test_premerge_run.py",
        # Opt-in runtime timing is diagnostic-only infrastructure.  Keep its
        # wrapper/CLI passthrough contracts in S1; live timing remains an
        # explicit browser/testkit invocation and never affects a verdict.
        "tests/testkit/test_runtime_timing.py",
        "tests/testkit/test_tmux_general_worker.py",
        "tests/testkit/test_case_evidence.py",
        "tests/testkit/test_case_evidence_lifecycle.py",
        "tests/testkit/test_case_evidence_windows.py",
        "tests/testkit/test_ordinary_recovery.py",
        "tests/testkit/test_case_evidence_adoption.py",
        "tests/testkit/test_async_case_evidence.py",
        "tests/testkit/test_parallel.py",
        "tests/testkit/test_rerun_evidence.py",
        "tests/testkit/test_network_fault.py",
        "tests/testkit/test_provider_http_fault.py",
        # Every named commit point must be observable through a fresh process
        # and leave no child process behind.
        "tests/conformance/test_zaofu_testkit_s1_crash_matrix.py",
    ),
    "S2": (
        "tests/conformance/test_zaofu_testkit_s2.py",
        # Actual Writer admission and default stage reports, not an oracle
        # tested with intentionally corrupt raw append inputs (EVT-05).
        "tests/conformance/test_zaofu_testkit_s2_event_ingress.py",
        "tests/conformance/test_zaofu_testkit_s2_store_integrity.py",
        "tests/conformance/test_zaofu_testkit_s2_store_concurrency.py",
        "tests/conformance/test_zaofu_testkit_s2_projection_rebuild.py",
        "tests/conformance/test_zaofu_testkit_s2_config.py",
        "tests/conformance/test_zaofu_testkit_s2_cli_validation_context.py",
        # Actual validation/doctor plus finite configured-route contracts.
        # These fixtures have no live provider backends or auth probes.
        "tests/test_cli_validate.py",
        "tests/test_cli_doctor.py",
        "tests/test_workflow_route_catalog.py",
        "tests/conformance/test_zaofu_testkit_s2_multiprocess.py",
        # Sidecar refs/digests are required evidence, not disposable
        # projections; run their strict path and hydration contracts in S2.
        "tests/test_sidecar_refs.py",
        # Verification payload schema is part of the evidence gate.
        "tests/test_payload_contract.py",
        # Canonical Feature/Session/Role stores are P0 state owners; keep
        # their persistence and atomic-write contracts in the S2 gate.
        "tests/test_feature_store.py",
        "tests/test_session_store.py",
        "tests/test_role_sessions.py",
        # Reuse the existing owner/archive/lock contracts rather than only
        # exercising wrappers. No provider CLI or live host sessions here.
        "tests/test_task_store.py",
        "tests/test_task_terminal_archive.py",
        "tests/test_feature_terminal_archive.py",
        "tests/test_state_locks.py",
        "tests/test_role_sessions_by_instance.py",
        "tests/test_role_sessions_backend_binding.py",
        "tests/test_i1_rebuild.py",
        "tests/conformance/test_zaofu_testkit_s2_store_crash.py",
        "tests/conformance/test_zaofu_testkit_s2_surface.py",
        # EventWriter projectors are the bridge from append-only facts to
        # canonical projections; keep their ordering/idempotency contracts in
        # the EventLog/Store stage.
        "tests/test_event_writer_projectors.py",
        # Core EventLog/schema/archive modules have direct append, validation
        # and rotation contracts in addition to conformance wrappers.
        "tests/test_event_contracts.py",
        "tests/test_events_log.py",
        "tests/test_event_log_durability.py",
        "tests/test_workflow_intake_durability.py",
        "tests/test_event_writer_schema.py",
        "tests/test_events_archive.py",
        # Project-context resolution, environment loading, worktree setup and
        # admission/profile validation are configuration boundary contracts.
        "tests/test_project_context.py",
        "tests/test_load_project_env.py",
        "tests/test_worktree_env.py",
        "tests/test_project_admission.py",
        "tests/test_project_profile.py",
        "tests/test_env_preflight.py",
    ),
    "S3": (
        "tests/conformance/test_zaofu_testkit_s3.py",
        "tests/conformance/test_zaofu_testkit_s3_terminal_store.py",
        "tests/test_task_statemachine.py",
        "tests/conformance/test_zaofu_testkit_s3_fanout.py",
        "tests/conformance/test_zaofu_testkit_s3_capacity.py",
        "tests/conformance/test_zaofu_testkit_s3_generation.py",
        "tests/conformance/test_zaofu_testkit_s3_blocking_dispatch.py",
        "tests/conformance/test_zaofu_testkit_s3_impl_commit_identity.py",
        "tests/conformance/test_zaofu_testkit_s3_blocking_recovery.py",
        "tests/conformance/test_zaofu_testkit_s3_plan_approval.py",
        "tests/conformance/test_zaofu_testkit_s3_candidate_batch_resume.py",
        "tests/conformance/test_zaofu_testkit_s3_writer_batch_resume.py",
        "tests/conformance/test_zaofu_testkit_s3_writer_queue_provider_turn.py",
        "tests/conformance/test_zaofu_testkit_s3_cli_full_preflight.py",
        "tests/conformance/test_zaofu_testkit_s3_respawn_termination.py",
        "tests/conformance/test_zaofu_testkit_s3_live_map_replacement.py",
        "tests/conformance/test_zaofu_testkit_s3_cli_lifecycle.py",
        "tests/conformance/test_zaofu_testkit_s3_task_ref_replay.py",
        "tests/conformance/test_zaofu_testkit_s3_old_verify_replacement.py",
        "tests/test_task_pipeline_result.py",
        "tests/test_task_pipeline_projection.py",
        "tests/test_workflow_spine_projection.py",
        # Covers generation/target identity through the real stage-dispatch
        # seam, in addition to the conformance selectors above.
        "tests/test_task_pipeline_generation.py",
        # Terminal ownership and completion-gate authority must run with the
        # orchestration contract, not only as an unregistered unit suite.
        "tests/test_goal_completion_authority.py",
        # Judge convergence/delta gate prevents unbounded retrigger loops.
        "tests/test_judge_convergence_gate.py",
        # Rework routing and required-delta fail-closed contracts.
        "tests/runtime/test_rework_routing.py",
        # Broad impact-closure regression probes remain strict xfails until
        # the corresponding production issues are repaired.
        "tests/conformance/test_zaofu_testkit_s3_regressions.py",
    ),
    "S4": (
        "tests/conformance/test_zaofu_testkit_s4_watcher_batch_recovery.py",
        "tests/test_fanout_recovery_handoff.py",
        "tests/conformance/test_zaofu_testkit_s4.py",
        "tests/test_stall_redispatch.py",
        "tests/test_tick_invoke_backlog_replay.py",
        "tests/conformance/test_zaofu_testkit_s4_budget_runtime.py",
        "tests/test_workloop_resources.py",
        "tests/test_scheduler_task_attempt.py",
        "tests/conformance/test_zaofu_testkit_s4_commit_matrix.py",
        "tests/conformance/test_zaofu_testkit_s4_action_crash.py",
        "tests/conformance/test_zaofu_testkit_s4_persistence.py",
        "tests/conformance/test_zaofu_testkit_s4_multiprocess.py",
        "tests/test_task_pipeline_fault_matrix.py",
        "tests/test_workflow_operation_resume.py",
        "tests/test_workflow_operation_projection.py",
        # Runtime fanout/recovery and provider-stop contracts exercise the
        # actual recovery closures beyond the narrow conformance matrix.
        "tests/test_fanout_recovery_runtime.py",
        "tests/test_fanout_failure_recovery.py",
        "tests/runtime/test_late_success_recovery.py",
        "tests/runtime/test_provider_stop_recovery.py",
        "tests/test_runtime_self_recovery_closures.py",
        # Deterministic serial recovery E2E exercises the real control loop
        # across restart boundaries without real providers.
        "tests/e2e/test_recovery_control_loop_serial.py",
        # Canonical attempt recovery and projection-conflict contracts are
        # deterministic and belong in the recovery stage.
        "tests/test_task_attempt_recovery.py",
        # Control-action replay/restart must preserve operation identity and
        # never duplicate an external effect.
        "tests/test_control_actions_recovery.py",
        # Shutdown cleanup is part of recovery correctness: stale inflight
        # work must be closed without losing canonical state.
        "tests/test_shutdown_stale_inflight_cleanup.py",
        "tests/test_cost_budget_enforcement.py",
        "tests/test_budget_sweep.py",
        "tests/test_workflow_budget_guard.py",
    ),
    "S5": (
        "tests/conformance/test_zaofu_testkit_s5.py",
        "tests/test_codex_runtime_hook_trust.py",
        "tests/test_codex_restricted_runtime.py",
        "tests/test_codex_hook_settings.py::test_recorded_hash_inputs_cover_two_paths_and_all_events",
        "tests/test_codex_hook_settings.py::test_codex_hook_hash_matches_real_codex_values",
        "tests/testkit/test_tmux_probe_cost.py",
        "tests/testkit/test_tmux_liveness_snapshot.py",
        "tests/test_testkit_native_long_task.py",
        "tests/test_tmux_process_cleanup.py",
        "tests/test_testkit_native_map_replacement.py",
        "tests/test_testkit_native_writer_batch.py",
        "tests/test_testkit_native_codex_legacy_queue.py",
        "tests/test_spawn_coordinator.py::TestClaudeRespawn::test_respawn_finds_transcript_in_actual_spawn_cwd",
        "tests/test_cli_start_task_stage_cwd.py",
        "tests/test_shutdown_instance_logs.py",
        "tests/test_testkit_native_cli_lifecycle.py",
        "tests/conformance/test_zaofu_testkit_s5_claude_readiness.py",
        "tests/conformance/test_zaofu_testkit_s5_claude_prompt_matrix.py",
        "tests/test_testkit_claude_tmux_canary.py",
        # Fake transport crash/respawn cases exercise the host lifecycle
        # without requiring a real provider or a live tmux session.
        "tests/integration/test_pane_crash_recovery.py",
        "tests/test_capture_logs_watchdog.py",
        # Sidecars are provider-bound runtime processes; include their
        # injected lifecycle and state-dir isolation contracts in S5.
        "tests/test_feishu_projection_sidecar.py",
        "tests/test_feishu_inbound_sidecar.py",
        # The resident autoresearch sidecar is another long-lived runtime
        # process; its command/state-root, authorization and stop/cleanup
        # contracts belong in the provider/host boundary stage.
        "tests/test_autoresearch_resident_sidecar.py",
        # Feishu/run-manager/workspace bridge projections are injected
        # integration boundaries and must preserve project identity without
        # live credentials.
        "tests/test_feishu_run_manager_projection.py",
        "tests/test_feishu_projection_target.py",
        "tests/test_feishu_project_group_binding.py",
        "tests/test_workspace_feishu_bridge.py",
        # Provider session configuration must remain closed over role,
        # backend, model and transport identity before dispatch.
        "tests/test_provider_session_config.py",
        # Turn liveness distinguishes progress, stuck and terminal provider
        # states without invoking a real provider process.
        "tests/test_provider_turn_liveness.py",
        # Composite transport routing must preserve role/instance isolation
        # and deterministic fallback behavior across provider adapters.
        "tests/test_composite_transport.py",
        # Adapter, stream-json and workdir contracts are mock/isolated host
        # boundary tests; real Provider and host canaries remain explicit.
        "tests/test_backend_adapters.py",
        "tests/test_transport_stream_json.py",
        "tests/test_transport_stream_json_alive.py",
        "tests/test_transport_stream_json_status.py",
        "tests/test_transport_stream_json_stuck_parity.py",
        "tests/test_transport_drain.py",
        "tests/test_tmux_transport_lifecycle.py",
        "tests/test_workdirs.py",
    ),
    "S6": (
        "tests/conformance/test_zaofu_testkit_s6.py",
        "tests/conformance/test_zaofu_testkit_s6_cli.py",
        "tests/conformance/test_zaofu_testkit_s6_cli_parser.py",
        "tests/test_cli_main.py",
        "tests/test_cli_command.py",
        "tests/test_manual_docs.py",
        "tests/test_cli_output_contract.py",
        "tests/test_cli_restart.py",  # Dry-run lifecycle only, not host E2E.
        "tests/test_workflow_start_service.py",  # Auth/apply, not route completion.
        "tests/conformance/test_zaofu_testkit_s6_schemathesis.py",
        "tests/conformance/test_zaofu_testkit_s6_projection_contract.py",
        # CLI flow initialization/routing probes retain current specialized
        # kind and submit STOP regressions as strict xfails (ZF-TEST-024..026).
        "tests/conformance/test_zaofu_testkit_s6_cli_flow.py",
        # These deterministic backend projection tests are part of the S6
        # gate; browser specs remain an explicit Docker tier below.
        "tests/test_web_server.py",
        "tests/test_web_auth.py",
        "tests/test_web_sse.py",
        "tests/test_workspace_projects.py",
        # Project-scoped terminal WebSocket ticket, origin and protocol guards.
        "tests/test_web_terminal_routes.py",
        # Core channel/kanban/log/loop projections feed the Web/API truth
        # chain and are deterministic without a browser or live runtime.
        "tests/test_channel_projection.py",
        "tests/test_kanban_projection.py",
        "tests/test_log_projection.py",
        "tests/test_loop_projection.py",
        # The isolated browser app factory itself is deterministic backend
        # harness code, independent of the Docker browser canary.
        "tests/testkit/test_browser_app.py",
        # Shared API/SSE/DOM convergence assertions used by browser truth
        # evidence; keep the helper's contract in the deterministic S6 gate.
        "tests/testkit/test_web_truth.py",
    ),
    "S7": (
        "tests/conformance/test_zaofu_testkit_s7.py",
        "tests/conformance/test_zaofu_testkit_s7_untrusted.py",
        "tests/test_hook_task_pipeline_workdir.py",
        # Existing path/signature/ingress contracts are part of the declared
        # source-to-sink security matrix; keep them in the executable S7 gate.
        "tests/test_path_guard.py",
        "tests/test_security_signing.py",
        "tests/test_external_issue_ingress.py",
        # Deterministic destination-swap seam exposes binary artifact TOCTOU;
        # it is strict xfail until production sinks revalidate before writing.
        "tests/conformance/test_zaofu_testkit_s7_toctou.py",
        # Forge adapters are injected with deterministic transports; these
        # contracts verify remote effect boundaries without live credentials.
        "tests/test_self_issue_forge_contract.py",
        # Feishu HTTP requests use an injected urlopen and remain hermetic.
        "tests/test_feishu_transport.py",
        # ControlledAction orchestration is the deterministic authorization
        # and idempotency sink for external side effects.
        "tests/test_action_orchestrator.py",
    ),
    "S8": (
        "tests/e2e/test_zaofu_testkit_s8_e2e.py",
        "tests/conformance/test_zaofu_testkit_s8_remote.py",
        "tests/e2e/test_terminal_kanban_convergence_mock_e2e.py",
        # Mock full-stack and workflow E2E contracts validate the complete
        # evidence/gate path without external network side effects.
        "tests/e2e/test_full_stack_validation.py",
        "tests/e2e/test_generic_workflow_complex_mock_e2e.py",
        "tests/e2e/test_agent_owned_verification_mock_e2e.py",
        "tests/test_product_acceptance.py",
        "tests/test_research_fanout_template.py",  # Generic-invoke Research mechanisms.
        "tests/conformance/test_zaofu_testkit_s8_research.py",  # Real controlled admission.
        "tests/conformance/test_zaofu_testkit_s8_research_replan.py",
        "tests/conformance/test_zaofu_testkit_s8_issue_route.py",  # Real light Issue through Goal settlement.
        "tests/conformance/test_zaofu_testkit_s8_general_route.py",  # Real five-stage General and owner delivery.
        "tests/conformance/test_zaofu_testkit_s8_prd_route.py",  # Default PRD fanout through owner delivery.
        "tests/conformance/test_zaofu_testkit_s8_refactor_route.py",  # Default Refactor parity barrier and owner delivery.
        "tests/conformance/test_zaofu_testkit_s8_refactor_strong.py",  # Catalog blocking acceptance and local ship.
        "tests/conformance/test_zaofu_testkit_s8_refactor_two_lane.py",  # Two independent lanes join before delivery.
        "tests/conformance/test_zaofu_testkit_s8_refactor_catalog_config.py",  # Full catalog/profile deployment and shadow checkpoint.
        "tests/conformance/test_zaofu_testkit_s8_shared_kind_queue.py",  # Same-runtime four-kind admission and real terminal release.
        "tests/conformance/test_zaofu_testkit_s8_shared_kind_delivery.py",  # Four shared Run terminal deliveries; active K539 detector.
        "tests/conformance/test_zaofu_testkit_s8_shared_kind_concurrent.py",  # K539-C formal same-config scope gate.
        "tests/conformance/test_zaofu_testkit_s8_shared_kind_concurrent_delivery.py",  # K539-C3 interleaved Run delivery.
        "tests/conformance/test_zaofu_testkit_s8_shared_kind_identity.py",  # K539-C4 formal identity rejection.
        "tests/conformance/test_zaofu_testkit_s8_shared_kind_scope.py",  # K539-C5 project target and writer scope.
        "tests/conformance/test_zaofu_testkit_s8_shared_kind_plan_scope.py",  # Signed planner cannot widen request.
        "tests/conformance/test_zaofu_testkit_s8_shared_code_concurrent.py",  # Disjoint code candidates and queue release.
        "tests/test_run_isolation_target_root.py",
        "tests/test_light_flow_request_scope.py",
        "tests/test_run_write_scope.py",
        "tests/test_workflow_invoke_rejection_replay.py",  # ZF-TEST-095: replay and split rejection writes.
        "tests/test_workflow_task_request_rotation.py",  # ZF-TEST-096: active anchor ownership.
        "tests/test_run_isolation_config_identity.py",  # ZF-TEST-093: verified config identity and invalid bindings.
        "tests/test_goal_completion_contract_isolation.py",  # ZF-TEST-092: per-claim pinned contract and fail-closed bindings.
        "tests/testkit/test_shared_kind_worker.py",  # K539-B Run-scoped candidate receipt association.
        "tests/conformance/test_zaofu_testkit_s8_triage_route.py",  # Signed reader/verifier and real triage closure.
        "tests/conformance/test_zaofu_testkit_s8_workflow_operation_contract.py",
        "tests/test_managed_workflow.py",
        # Candidate quality gates bind verification evidence before delivery.
        "tests/test_candidates.py",
        # Plan-candidate preflight is the structural admission gate for
        # acceptance/matrix/command closure; include its deterministic
        # contract set before any candidate delivery effect.
        "tests/test_plan_candidate_preflight.py",
        # Candidate integration owns dependency, cleanliness, attempt
        # identity, ancestry and merge-conflict classification contracts.
        "tests/test_candidate_integration.py",
        # Artifact delivery results are the terminal evidence handoff seam.
        "tests/test_artifact_delivery_result.py",
        # Fix Run candidate-preflight and merge-side-effect contracts are
        # declared evidence gates for the mock delivery path.
        "tests/conformance/test_zaofu_testkit_s9_fixrun.py",
        # Run Archive manifest, artifact digest, redaction and path-safety
        # contracts are the immutable evidence handoff for S8.
        "tests/test_run_archive.py",
        # The repository's mandatory workflow smoke entry is deterministic
        # mock E2E and must run inside the stage registry as well.
        "tests/test_flow_smoke_e2e.py",
        "tests/test_controller_flow_smoke_matrix.py",
        "tests/e2e/test_task_pipeline_v4_runtime_noise_mock_e2e.py",
        # Browser handoff reports observe admission/terminal facts; an invoke
        # alone must never be presented as completed workflow delivery.
        "tests/test_four_flow_e2e_support.py",
    ),
    "S9": (
        "tests/conformance/test_zaofu_testkit_s9.py",
        "tests/conformance/test_zaofu_testkit_s9_fixrun.py",
        "tests/test_issue_candidate_delivery.py",
        # External Issue ingress is the first trusted boundary of the
        # issue-to-fix loop; run its revision/source guards in S9.
        "tests/test_external_issue_ingress.py",
        # ShipService is the actual local candidate-to-main merge boundary;
        # keep its lock, conflict, idempotency, and Web action contracts in
        # the Issue -> regression -> merge stage.
        "tests/test_ship.py",
        # Issue triage mirror owns the ingress-to-approval projection and
        # must be exercised before Fix Run/candidate delivery contracts.
        "tests/test_issue_triage_mirror.py",
    ),
    "S10": (
        "tests/conformance/test_zaofu_testkit_s10.py",
        # Release compatibility is limited to the historical config/event/
        # state-packet shapes explicitly supported by current code.  There is
        # no migration/rollback command yet, so those remain release gaps.
        "tests/conformance/test_zaofu_testkit_s10_compatibility.py",
        # k6 summary/threshold parsing is deterministic; invoking long k6
        # load runs remains an explicit release/soak tier.
        "tests/testkit/test_performance.py",
        # ZAP report parsing and risk-threshold verdict are deterministic;
        # launching a scanner remains an explicit release/host tier.
        "tests/testkit/test_zap.py",
        "tests/testkit/test_zap_launcher.py",
        # Deterministic perf budget smoke; long soak/k6 remains a release tier.
        "tests/test_web_perf_smoke.py",
        # Mutation result parsing is deterministic TestKit infrastructure;
        # the mutmut process itself remains an explicit release-tier audit.
        "tests/test_testkit_mutation.py",
        # Metrics and redaction are observability evidence, not correctness
        # scores; include their deterministic contracts in the release tier.
        "tests/test_metrics_collector.py",
        "tests/test_metrics_cli.py",
        "tests/test_redacted_diagnostics.py",
        "tests/test_otlp_exporter.py",
        # Hypothesis stateful EventLog replay invariant; bounded local files only.
        "tests/conformance/test_zaofu_testkit_s11_hypothesis.py",
        "tests/conformance/test_zaofu_testkit_s11_control_models.py",
        "tests/conformance/test_zaofu_testkit_s11_attempt_model.py",
        "tests/conformance/test_zaofu_testkit_s11_eventlog_model.py",
        "tests/conformance/test_zaofu_testkit_s11_generation_model.py",
        "tests/conformance/test_zaofu_testkit_s11_fanout_model.py",
        "tests/conformance/test_zaofu_testkit_s11_candidate_rework_model.py",
        "tests/conformance/test_zaofu_testkit_s11_candidate_sweep_model.py",
        "tests/conformance/test_zaofu_testkit_s11_task_pipeline_recovery_model.py",
        "tests/conformance/test_zaofu_testkit_s11_reader_retry_concurrency_model.py",
        "tests/conformance/test_zaofu_testkit_s11_task_pipeline_rework_model.py",
        "tests/conformance/test_zaofu_testkit_s11_rework_model.py",
        "tests/conformance/test_zaofu_testkit_s11_action_loss_model.py",
    ),
}

# Deterministic mainline acceptance profile.  This is intentionally a small
# cross-stage closure: it exercises approval, blocking dispatch/recovery,
# shutdown/restart, candidate delivery, and report/issue contracts.  Native
# Provider/host tests remain explicit tiers and are not silently run here.
MAINLINE: Final[tuple[str, ...]] = (
    "tests/test_codex_runtime_hook_trust.py",
    "tests/test_codex_restricted_runtime.py",
    "tests/test_codex_hook_settings.py::test_recorded_hash_inputs_cover_two_paths_and_all_events",
    "tests/test_codex_hook_settings.py::test_codex_hook_hash_matches_real_codex_values",
    "tests/test_event_log_durability.py",
    "tests/test_workflow_intake_durability.py",
    "tests/conformance/test_zaofu_testkit_s4_watcher_batch_recovery.py",
    "tests/test_fanout_recovery_handoff.py",
    "tests/testkit/test_tmux_general_worker.py",
    "tests/test_capture_logs_watchdog.py",
    "tests/test_stall_redispatch.py",
    "tests/test_tick_invoke_backlog_replay.py",
    "tests/test_tmux_process_cleanup.py",
    "tests/test_testkit_native_long_task.py",
    "tests/test_testkit_native_map_replacement.py",
    "tests/test_testkit_native_writer_batch.py",
    "tests/test_testkit_native_codex_legacy_queue.py",
    "tests/test_shutdown_instance_logs.py",
    "tests/test_cli_start_task_stage_cwd.py",
    "tests/test_testkit_native_cli_lifecycle.py",
    "tests/testkit/test_native_acceptance.py",
    "tests/testkit/test_requirement_audit.py",
    "tests/testkit/test_native_run.py",
    "tests/testkit/test_source_observation.py",
    "tests/testkit/test_premerge_run.py",
    "tests/testkit/test_runtime_timing.py",
    "tests/testkit/test_tmux_probe_cost.py",
    "tests/testkit/test_tmux_liveness_snapshot.py",
    "tests/conformance/test_zaofu_testkit_s3_plan_approval.py",
    "tests/conformance/test_zaofu_testkit_s3_blocking_dispatch.py",
    "tests/conformance/test_zaofu_testkit_s3_impl_commit_identity.py",
    "tests/conformance/test_zaofu_testkit_s3_blocking_recovery.py",
    "tests/conformance/test_zaofu_testkit_s3_respawn_termination.py",
    "tests/conformance/test_zaofu_testkit_s3_cli_lifecycle.py",
    "tests/conformance/test_zaofu_testkit_s3_cli_full_preflight.py",
    "tests/conformance/test_zaofu_testkit_s3_writer_batch_resume.py",
    "tests/conformance/test_zaofu_testkit_s3_writer_queue_provider_turn.py",
    "tests/conformance/test_zaofu_testkit_s5_claude_prompt_matrix.py",
    "tests/conformance/test_zaofu_testkit_s5_claude_readiness.py",
    "tests/test_spawn_coordinator.py::TestClaudeRespawn::test_respawn_finds_transcript_in_actual_spawn_cwd",
    "tests/test_ship.py",
    "tests/test_hook_task_pipeline_workdir.py",
    "tests/conformance/test_zaofu_testkit_s9_fixrun.py",
    "tests/conformance/test_zaofu_testkit_s9.py",
)


def nodeids_for_stage(stage: str) -> tuple[str, ...]:
    normalized = stage.upper()
    if normalized == "ALL":
        return tuple(dict.fromkeys(nodeid for paths in SUITES.values() for nodeid in paths))
    try:
        return SUITES[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown TestKit stage: {stage}") from exc
