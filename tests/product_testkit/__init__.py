"""Reusable, product-agnostic helpers for testing ZaoFu generated projects."""

from .manifest import ManifestError, ProductManifest, load_manifest, validate_manifest
from .matrix_adapter import adapt_test_matrix, audit_test_matrix
from .report import build_verification_result, write_junit_report, write_verification_result
from .acceptance import AcceptanceReportError, build_acceptance_report
from .planner import commands_for_domains, require_capability_decisions, require_domain_coverage, select_test_domains
from .executor import run_manifest_file
from .failure_bundle import build_failure_bundle, write_failure_bundle
from .runner import run_manifest_and_collect, run_product_closure
from .delivery import evaluate_delivery_readiness
from .tiers import ci_plan, commands_for_ci_tier
from .stability import StabilityError, assess_stability, load_verification_result
from .schemathesis import (
    build_schemathesis_command,
    classify_schemathesis_result,
    run_schemathesis_contract,
)
from .browser import build_playwright_command, classify_playwright_result, run_playwright_contract
from .containers import (
    ContainerError,
    ContainerReceipt,
    docker_container_command,
    start_dependency_container,
)
from .mocks import MockServiceReceipt, start_mock_service
from .recovery import CrashRecoveryReceipt, run_crash_restart, run_recovery_scenarios
from .concurrency import ConcurrentProcessResult, run_concurrent_processes, run_concurrency_scenarios
from .persistence import run_persistence_scenarios
from .k6 import K6ContractError, build_k6_command, run_k6_contract
from .release import ReleaseArtifactError, inspect_artifact, run_release_artifact_checks, run_release_scenarios
from .security_scan import run_zap_contract
from .issue_loop import IssueLoopError, IssueLoopResult, IssueMergeLoop
from .ci import run_ci_matrix

__all__ = ["ManifestError", "ProductManifest", "load_manifest", "validate_manifest", "adapt_test_matrix", "audit_test_matrix",
           "build_verification_result", "write_junit_report", "write_verification_result",
           "AcceptanceReportError", "build_acceptance_report"]
__all__.append("select_test_domains")
__all__.append("commands_for_domains")
__all__.append("require_domain_coverage")
__all__.append("require_capability_decisions")
__all__.append("run_manifest_file")
__all__.extend(["build_failure_bundle", "write_failure_bundle"])
__all__.append("run_manifest_and_collect")
__all__.append("run_product_closure")
__all__.append("evaluate_delivery_readiness")
__all__.append("ci_plan")
__all__.append("commands_for_ci_tier")
__all__.extend(["StabilityError", "assess_stability", "load_verification_result"])
__all__.extend([
    "build_schemathesis_command",
    "classify_schemathesis_result",
    "run_schemathesis_contract",
])
__all__.append("run_persistence_scenarios")
__all__.extend(["MockServiceReceipt", "start_mock_service"])
__all__.extend([
    "CrashRecoveryReceipt", "run_crash_restart", "run_recovery_scenarios",
    "ConcurrentProcessResult", "run_concurrent_processes", "run_concurrency_scenarios",
])
__all__.extend(["K6ContractError", "build_k6_command", "run_k6_contract"])
__all__.extend(["ReleaseArtifactError", "inspect_artifact", "run_release_artifact_checks", "run_release_scenarios"])
__all__.append("run_zap_contract")
__all__.extend(["IssueLoopError", "IssueLoopResult", "IssueMergeLoop"])
__all__.append("run_ci_matrix")
__all__.extend(["build_playwright_command", "classify_playwright_result", "run_playwright_contract"])
__all__.extend([
    "ContainerError",
    "ContainerReceipt",
    "docker_container_command",
    "start_dependency_container",
])

