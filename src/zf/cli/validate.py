"""zf validate — validate zf.yaml configuration."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from zf.core.agents_md import (
    AgentsMdError,
    extract_managed_block,
    render_canonical_block,
)
from zf.core.config.loader import validate_config, load_config, ConfigError
from zf.core.config.tool_closure import validate_tool_closure
from zf.core.config.lkg import (
    infer_state_dir,
    promote_last_known_good,
    write_validation_report,
)
from zf.core.skills import validate_skill_sources


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("validate", help="Validate zf.yaml configuration")
    parser.add_argument("--path", type=str, default=None, help="Path to config file")
    parser.add_argument("--cold-start", action="store_true", help="Run cold-start readiness check (5-point)")
    parser.add_argument("--architecture", action="store_true", help="Check architecture rules")
    parser.add_argument("--instructions", action="store_true", help="Lint instruction files (AGENTS.md / CLAUDE.md)")
    parser.add_argument(
        "--strict-skills",
        action="store_true",
        help="Fail when enabled skills are missing, invalid, or ambiguous",
    )
    parser.add_argument(
        "--strict-contracts",
        action="store_true",
        help="Fail when active tasks have incomplete strict contracts",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else Path.cwd() / "zf.yaml"

    if getattr(args, "cold_start", False):
        return _run_cold_start(path)
    if getattr(args, "architecture", False):
        return _run_architecture(path.parent)
    if getattr(args, "instructions", False):
        return _run_instructions(path.parent)

    errors = validate_config(path)

    if errors:
        write_validation_report(
            state_dir=infer_state_dir(path),
            config_path=path,
            status="invalid",
            errors=errors,
        )
        print("Validation errors:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print("\nTo fix: check zf.yaml against the schema in docs/design/02-harness-yaml.md", file=sys.stderr)
        return 1

    try:
        config = load_config(path)
    except ConfigError as e:
        write_validation_report(
            state_dir=infer_state_dir(path),
            config_path=path,
            status="invalid",
            errors=[str(e)],
        )
        print(f"Validation errors:\n  - {e}", file=sys.stderr)
        return 1
    remote_errors = _validate_remote_policy(config, path.parent)
    if remote_errors:
        write_validation_report(
            state_dir=Path(path.parent) / config.project.state_dir,
            config_path=path,
            status="invalid",
            errors=remote_errors,
        )
        print("Remote policy errors:", file=sys.stderr)
        for error in remote_errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    warnings = validate_skill_sources(
        config=config,
        project_root=path.parent,
    )
    strict_skills = (
        bool(getattr(args, "strict_skills", False))
        or config.runtime.skills.strict
    )
    if warnings:
        label = "Skill validation errors" if strict_skills else "Validation warnings"
        print(f"{label}:", file=sys.stderr)
        for warning in warnings:
            print(f"  - {warning}", file=sys.stderr)
        if strict_skills:
            write_validation_report(
                state_dir=Path(path.parent) / config.project.state_dir,
                config_path=path,
                status="invalid",
                errors=warnings,
            )
            return 1
    owner_env = dict(os.environ)
    try:
        from zf.core.config.project_context import load_env_file

        owner_env.update(load_env_file(path.parent / ".env"))
    except Exception:
        pass
    owner_delivery_warnings = _owner_visible_delivery_warnings(owner_env)
    if owner_delivery_warnings:
        # P0-7(审计 D5 unattended 硬门):配置已授权自主动作
        # (autoresearch continuous / source_repair 开启)而 owner 通道
        # 未配 → escalate 无人可达 = 结构性 dead-end,validate FAIL。
        # 纯人工值守(未授权自主)维持 WARN。
        if _unattended_autonomy_enabled(config):
            print("Unattended autonomy without owner channel:", file=sys.stderr)
            for warning in owner_delivery_warnings:
                print(f"  - FAIL: {warning}", file=sys.stderr)
            print(
                "  To fix: set ZF_OWNER_VISIBLE_CHAT, or disable "
                "autoresearch continuous / source_repair.",
                file=sys.stderr,
            )
            return 1
        print("Owner-visible delivery warnings:", file=sys.stderr)
        for warning in owner_delivery_warnings:
            print(f"  - {warning}", file=sys.stderr)

    budget_usd = getattr(config, "global_budget_usd", None)
    if budget_usd and not bool(getattr(config, "budget_enforcement_enabled", True)):
        # FIX-5③(bizsim r4:$806 击穿 $700 预算无刹车):声明了预算却
        # 显式关闭 enforcement,预算门形同虚设。观测型预算是合法选择,
        # 故 WARN 不 FAIL,但必须可见。
        print(
            f"  WARNING: global_budget_usd={budget_usd} declared but "
            "budget_enforcement_enabled=false — overspend will NOT be "
            "blocked. Set budget_enforcement_enabled: true to enforce.",
            file=sys.stderr,
        )
    fanout_writer_stages = [
        stage for stage in getattr(config.workflow, "stages", [])
        if str(getattr(stage, "topology", "")).startswith("fanout_writer")
    ]
    if fanout_writer_stages and not getattr(config, "quality_gates", None):
        # FIX-10(bizsim r4 F10):多任务写入型 workflow 没配 quality_gates,
        # candidate 合成树不经任何验证即发 candidate.ready——r4 churn 期
        # candidate typecheck 断裂而 judge 照审坏树。观测型运行合法,WARN。
        print(
            "  WARNING: workflow has fanout_writer stages but no "
            "quality_gates configured — the integrated candidate tree is "
            "NEVER verified (per-lane verify cannot catch cross-lane "
            "skew). Configure quality_gates (e.g. typecheck + unit tests).",
            file=sys.stderr,
        )
    if getattr(args, "strict_contracts", False):
        from zf.core.task.contract_validation import validate_runtime_contracts

        contract_errors = validate_runtime_contracts(
            config=config,
            project_root=path.parent,
            state_dir=path.parent / config.project.state_dir,
        )
        if contract_errors:
            write_validation_report(
                state_dir=Path(path.parent) / config.project.state_dir,
                config_path=path,
                status="invalid",
                errors=contract_errors,
            )
            print("Strict contract errors:", file=sys.stderr)
            for error in contract_errors:
                print(f"  - {error}", file=sys.stderr)
            return 1

    promote_last_known_good(
        config_path=path,
        state_dir=Path(path.parent) / config.project.state_dir,
        warnings=warnings,
    )
    print(f"OK: {path} is valid")
    return 0


def _unattended_autonomy_enabled(config) -> bool:
    """已授权自主动作的判定(P0-7):autoresearch continuous 或
    run_manager source_repair 开启,即 run 期望在无人值守下自我修复——
    此时 escalate 的唯一出口是 owner 通道,通道缺配即 dead-end。"""
    try:
        trigger = getattr(getattr(config, "autoresearch", None), "trigger_policy", None)
        if str(getattr(trigger, "mode", "") or "") == "continuous":
            return True
        source_repair = getattr(
            getattr(getattr(config, "runtime", None), "run_manager", None),
            "source_repair",
            None,
        )
        return bool(getattr(source_repair, "enabled", False))
    except Exception:
        return False


def _owner_visible_delivery_warnings(env: dict[str, str]) -> list[str]:
    chat = str(env.get("ZF_OWNER_VISIBLE_CHAT") or "").strip()
    if chat:
        return []
    return [
        "ZF_OWNER_VISIBLE_CHAT is not configured; owner.visible_message "
        "alerts will stay in the Web inbox and Feishu delivery will emit "
        "failed/no-target receipts instead of silently succeeding."
    ]


def _run_cold_start(config_path: Path) -> int:
    """Run 5-point cold-start readiness check + workflow topology check."""
    from zf.core.config.cold_start import cold_start_check

    if not config_path.exists():
        print(f"Error: {config_path} not found. To fix: run 'zf init'", file=sys.stderr)
        return 1

    try:
        config = load_config(config_path)
    except ConfigError as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1
    remote_errors = _validate_remote_policy(config, config_path.parent)
    if remote_errors:
        print("Remote policy errors:", file=sys.stderr)
        for error in remote_errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    if config.safety.tool_closure_enabled:
        closure_errors = validate_tool_closure(config)
        if closure_errors:
            print("Tool closure errors:", file=sys.stderr)
            for error in closure_errors:
                print(f"  - {error}", file=sys.stderr)
            return 1

    workspace = config_path.parent
    result = cold_start_check(workspace, config)

    print(f"Cold-start score: {result.score}/5\n")
    for name, passed, detail in result.checks:
        icon = "PASS" if passed else "FAIL"
        print(f"  [{icon}] {name}: {detail}")

    # Workflow topology check. Warning-level by default, but in the strict
    # harness profile the unambiguously-fatal handoff classes gate cold-start:
    #   - dead_end_roles: a role triggers on an event nothing publishes → it
    #     can never run (the stage→stage handoff is broken at the consumer end).
    #   - unwoken_events: a reactor handler exists but EventWatcher never wakes
    #     the orchestrator (LH-3 SUSPEND silent route break).
    # 2026-06-19 handoff-prevention: the prod-E2E P0s were all handoff-boundary
    # bugs; cold-start printed signals but never gated (validate.py:178). Turn
    # "read it if you remember to" into "doesn't pass, doesn't start".
    print()
    topo = _print_topology_report(config)
    strict_profile = str(
        getattr(getattr(config, "workflow", None), "harness_profile", "") or ""
    ) == "strict"
    handoff_fatal: list[str] = []
    if strict_profile and topo is not None:
        if getattr(topo, "dead_end_roles", None):
            handoff_fatal.append(f"dead_end_roles={list(topo.dead_end_roles)}")
        if getattr(topo, "unwoken_events", None):
            handoff_fatal.append(f"unwoken_events={list(topo.unwoken_events)}")

    print()
    event_contract = _print_event_contract_report(config)
    if strict_profile and not event_contract.get("ok", True):
        errors = event_contract.get("errors") or []
        handoff_fatal.append(
            f"event_contract_errors={len(errors)}"
        )

    # ZF-TR-PROVIDER-CAP-001: backend capability matrix.
    print()
    _print_backend_capability_matrix(config)

    # EVAL-BACKEND-ISOLATION-001: warn when adversarial roles share
    # backend with dev/builder (self-confirmation bias risk).
    print()
    _print_backend_isolation_check(config)

    if result.score < 5:
        print(f"\nTo fix: address the FAIL items above before running 'zf start'")

    if handoff_fatal:
        print(
            "\n  [FAIL] workflow_handoff (strict profile): fatal handoff break — "
            + "; ".join(handoff_fatal)
            + "\nTo fix: every stage success_event needs a producer and every "
            "reactor handler needs a wake pattern before 'zf start'."
        )
        return 1

    return 0 if result.score >= 4 else 1


def _validate_remote_policy(config, workspace: Path) -> list[str]:
    git_config = getattr(getattr(config, "runtime", None), "git", None)
    policy = getattr(git_config, "remote_policy", "local")
    if policy != "required":
        return []
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "remote", "get-url", "origin"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return []
    return [
        "runtime.git.remote_policy=required but git remote 'origin' is not "
        "configured; add an approved origin or use remote_policy=local/optional/local_only."
    ]


_ADVERSARIAL_ROLE_NAMES: tuple[str, ...] = (
    "review", "test", "judge", "critic", "verifier",
)
_BUILDER_ROLE_NAMES: tuple[str, ...] = ("dev", "builder")


def _print_backend_isolation_check(config) -> None:
    """EVAL-BACKEND-ISOLATION-001 (doc 43 §2.9): warn when an
    adversarial role (review / test / judge / critic / verifier) uses
    the same backend as the builder (dev / builder).

    Same backend → reviewer trained on dev's output may produce self-
    confirmation bias. Warning-only — operator may have a legitimate
    reason to use the same backend (cost, single-provider deployment).
    """
    roles = getattr(config, "roles", []) or []
    role_backends: dict[str, str] = {}
    for role in roles:
        name = (getattr(role, "name", "") or "").lower()
        backend = (getattr(role, "backend", "") or "").lower()
        if name and backend:
            role_backends[name] = backend

    builder_backend = None
    for builder_name in _BUILDER_ROLE_NAMES:
        if builder_name in role_backends:
            builder_backend = role_backends[builder_name]
            break

    print("Backend Isolation:")
    if not builder_backend:
        print(
            "  (no dev/builder role found — skipping adversarial-backend "
            "comparison)"
        )
        return

    issues = 0
    for adv in _ADVERSARIAL_ROLE_NAMES:
        adv_backend = role_backends.get(adv)
        if adv_backend and adv_backend == builder_backend:
            print(
                f"  ⚠ {adv} and dev/builder use same backend "
                f"({adv_backend!r}). "
                f"Risk: self-confirmation bias. Recommend: assign a "
                f"different backend to {adv}."
            )
            issues += 1
    if issues == 0:
        print(
            "  ✓ adversarial roles use different backend(s) from "
            f"builder ({builder_backend!r})"
        )


def _print_backend_capability_matrix(config) -> None:
    """ZF-TR-PROVIDER-CAP-001 (doc 39 §2.1.3): report backend
    capabilities for every distinct backend used in this config.

    Surfaces hook / resume / context-usage / nested-agent capability
    so operators see why a role is in degraded mode (e.g. Codex
    sub-agent only ``partial``-disabled) instead of having to
    re-derive capability per call site.
    """
    from zf.runtime.backend import get_adapter

    backends = sorted({
        (role.backend or "python") for role in getattr(config, "roles", [])
    })
    if not backends:
        return

    print("Backend Capability Matrix:")
    for backend in backends:
        try:
            adapter = get_adapter(backend)
        except ValueError as exc:
            print(f"  {backend}: ERROR — {exc}")
            continue
        caps = adapter.capabilities
        print(
            f"  {backend}: per_turn_hook={caps.per_turn_hook} "
            f"session_start_hook={caps.session_start_hook} "
            f"native_resume={caps.native_resume} "
            f"context_usage_reader={caps.context_usage_reader} "
            f"stream_json={caps.stream_json} "
            f"hook_review_required={caps.hook_review_required} "
            f"nested_agent_disable={caps.nested_agent_disable!r}"
        )


def _print_topology_report(config) -> None:
    """Report orphan events, dead-end roles, and handler coverage.

    Warnings only — does not fail validate. Some orphans are legitimate
    (e.g. `design.critique.done` may be the terminal output of a GAN
    loop). Use this as a diagnostic, not a gate.
    """
    from zf.core.workflow.topology import WorkflowEventSets, WorkflowTopology
    from zf.runtime.wake_patterns import (
        WAKE_PATTERNS,
        reactor_handler_events,
    )

    topology = WorkflowTopology.from_config(config)
    report = topology.check(
        reactor_handlers=reactor_handler_events(),
        wake_patterns=set(WAKE_PATTERNS),
    )

    print("Workflow Topology:")
    orphans = report.orphan_events
    dead_ends = report.dead_end_roles
    print(f"  Orphan events (published, no role triggers them): "
          f"{orphans if orphans else 'none'}")
    print(f"  Dead-end roles (trigger on unpublished events): "
          f"{dead_ends if dead_ends else 'none'}")
    if report.unhandled_events:
        print(f"  WARNING: events published but reactor has no handler: "
              f"{report.unhandled_events}")
    if report.unwoken_events:
        # This is the LH-3 SUSPEND bug class: handler exists but
        # EventWatcher will never wake the orchestrator. Silent route
        # failure. Report it prominently.
        print(f"  WARNING: reactor handlers NOT in wake_patterns "
              f"(silent route break): {report.unwoken_events}")

    # PREREQ-B (doc 40 §6 I57): cross-check WorkflowEventSets baseline
    # against the topology. Flags role-published events with success /
    # failure suffix that the baseline does not classify — the B-NEW-4
    # bug class root cause.
    event_sets = WorkflowEventSets.baseline()
    drift = event_sets.cross_check_topology(topology)
    if drift:
        print(
            "  WARNING: WorkflowEventSets baseline drift "
            "(possibly new pipeline events that need classification):"
        )
        for line in drift:
            print(f"    - {line}")
    try:
        from zf.core.workflow.graph import compile_workflow_graph

        graph = compile_workflow_graph(config)
    except Exception as exc:
        print(f"  WARNING: workflow graph compile failed: {exc}")
        return report
    graph_diagnostics = _filter_expected_graph_diagnostics(config, graph.diagnostics)
    if graph_diagnostics:
        print("  WARNING: workflow graph diagnostics:")
        for item in graph_diagnostics:
            stage = item.get("stage_id") or "-"
            event = item.get("event") or "-"
            kind = item.get("kind") or "diagnostic"
            print(f"    - {kind}: stage={stage} event={event}")
    return report


def _filter_expected_graph_diagnostics(config, diagnostics: list[dict]) -> list[dict]:
    """Hide known runtime bridge sinks from validate's raw graph warning list."""

    try:
        from zf.core.config.render import _classify_expected_event_sinks
    except Exception:
        return list(diagnostics)
    normalized = []
    for item in diagnostics:
        normalized.append({
            "severity": "WARN",
            "kind": str(item.get("kind") or ""),
            "source": "workflow_graph",
            "stage_id": str(item.get("stage_id") or ""),
            "event": str(item.get("event") or ""),
            "field": str(item.get("field") or ""),
            "message": "",
            "detail": {},
        })
    classified = _classify_expected_event_sinks(config, normalized)
    keep: list[dict] = []
    for original, item in zip(diagnostics, classified, strict=False):
        if str(item.get("kind") or "") == "expected_event_without_consumer":
            continue
        keep.append(original)
    return keep


def _print_event_contract_report(config) -> dict:
    from zf.runtime.event_contracts import build_event_contract_report

    report = build_event_contract_report(config)
    summary = report.get("summary", {})
    print("Event Contract:")
    print(
        f"  producers={summary.get('producers', 0)} "
        f"event_types={summary.get('producer_event_types', 0)} "
        f"errors={summary.get('errors', 0)} "
        f"warnings={summary.get('warnings', 0)}"
    )
    for item in report.get("errors", [])[:10]:
        print(
            "  [FAIL] "
            f"{item.get('kind')}: {item.get('event_type')} — "
            f"{item.get('message')}"
        )
    for item in report.get("warnings", [])[:10]:
        print(
            "  [WARN] "
            f"{item.get('kind')}: {item.get('event_type')} — "
            f"{item.get('message')}"
        )
    return report


def _run_architecture(workspace: Path) -> int:
    """Check architecture rules from ARCHITECTURE_RULES.md."""
    from zf.core.verification.architecture_rules import parse_rules, rules_to_gates

    rules_file = workspace / "ARCHITECTURE_RULES.md"
    if not rules_file.exists():
        print("No ARCHITECTURE_RULES.md found. Skipping.", file=sys.stderr)
        return 0

    rules = parse_rules(rules_file)
    if not rules:
        print("No rules found in ARCHITECTURE_RULES.md.")
        return 0

    gates = rules_to_gates(rules)
    passed = 0
    failed = 0
    for gate in gates:
        result = gate.run()
        if result.passed:
            print(f"  [PASS] {gate.name}")
            passed += 1
        else:
            print(f"  [FAIL] {gate.name}: {result.output[:100]}")
            failed += 1

    print(f"\nArchitecture rules: {passed} passed, {failed} failed")
    return 1 if failed > 0 else 0


def _run_instructions(workspace: Path) -> int:
    """Lint instruction files (AGENTS.md, CLAUDE.md, and role CLAUDE.md files)."""
    issues: list[str] = []

    agents_md = workspace / "AGENTS.md"
    if not agents_md.exists():
        issues.append("AGENTS.md not found at project root")
    else:
        try:
            managed = extract_managed_block(agents_md.read_text(encoding="utf-8"))
            if managed != render_canonical_block().rstrip("\n"):
                issues.append("AGENTS.md managed block is out of sync")
        except AgentsMdError as exc:
            issues.append(f"AGENTS.md managed block invalid: {exc}")

    # Check main CLAUDE.md
    main_claude = workspace / "CLAUDE.md"
    if not main_claude.exists():
        issues.append("CLAUDE.md not found at project root")
    else:
        lines = main_claude.read_text().splitlines()
        if len(lines) < 5:
            issues.append(f"CLAUDE.md too short ({len(lines)} lines, recommend 10+)")
        if len(lines) > 500:
            issues.append(f"CLAUDE.md very long ({len(lines)} lines, consider splitting)")

    # Check role instruction files
    roles_dir = workspace / "roles"
    if roles_dir.exists():
        for role_dir in roles_dir.iterdir():
            if role_dir.is_dir():
                claude_md = role_dir / "CLAUDE.md"
                if not claude_md.exists():
                    issues.append(f"roles/{role_dir.name}/CLAUDE.md missing")

    if issues:
        print("Instruction file issues:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    print("Instruction files: OK")
    return 0
