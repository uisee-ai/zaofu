"""Machine-readable boundary for the current ZaoFu TestKit need-to-do list.

The inventory is deliberately a status guard, not a completion percentage.  It
keeps operator skips, missing historical evidence, waiting-for-a-new-scene
items, destructive OS models, and completed framework capabilities separate so
that a green deterministic test cannot silently close an open requirement.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "zf-testkit-need-to-do-inventory.v1"
OPEN_STATUSES = frozenset({
    "operator_skip",
    "waiting_same_signature_scene",
    "method_ready_open",
    "destructive_model_open",
    "historical_evidence_missing",
    "blocked_by_open_items",
    "excluded_unstable",
    "deferred_degraded",
})
COMPLETED_STATUS = "completed_framework_only"
ALL_STATUSES = OPEN_STATUSES | {COMPLETED_STATUS}


@dataclass(frozen=True)
class NeedToDoItem:
    item_id: str
    area: str
    status: str
    method_ref: str
    completion_condition: str
    evidence_ref: str = ""
    counts_as_product_pass: bool = False


CURRENT_INVENTORY: tuple[NeedToDoItem, ...] = (
    NeedToDoItem(
        "K547-codex-native",
        "原生 Provider",
        "operator_skip",
        "tests/conformance/test_zaofu_testkit_s5_native_codex_legacy_queue.py::test_native_codex_legacy_queue_timeout_resumes_only_gap",
        "操作者恢复同一生产 CODEX_HOME 后显式启用原生 host/real_provider 节点",
    ),
    NeedToDoItem(
        "claude-native-pipeline",
        "原生 Provider",
        "operator_skip",
        "tests/testkit/native_run.py（normal/impl/verify）",
        "操作者恢复可执行原生 CLI、认证和模型后显式启用原生 pipeline",
    ),
    NeedToDoItem(
        "K530-watchdog-root-cause",
        "开放生产 Issue",
        "waiting_same_signature_scene",
        "tests/testkit/incident_reentry.py::IncidentReentryEvidence",
        "出现 exact issue/signature 的新现场并保留完整诊断、source ref/SHA-256 和时间戳",
    ),
    NeedToDoItem(
        "K584-R-handshake",
        "开放生产 Issue",
        "waiting_same_signature_scene",
        "tests/testkit/incident_reentry.py::IncidentReentryEvidence",
        "出现 provider_restart.shell_handshake_timeout 同签名新现场并通过准入合同",
    ),
    NeedToDoItem(
        "K552-F2-long-sample",
        "性能与资源",
        "method_ready_open",
        "tests/conformance/test_zaofu_testkit_s4_workloop_host.py::test_sustained_task_pipeline_releases_owned_resources",
        "取得可验证的连续无损观测重新进入证据后，按单 Tracker/单 capture、32 Task、1200 秒运行",
    ),
    NeedToDoItem(
        "physical-host-power-loss",
        "OS 故障模型",
        "destructive_model_open",
        "tests/testkit/physical_host_power_loss.py::validate_physical_host_power_loss_evidence",
        "由人工批准的专用宿主、外部电源控制器和耐久性方案提供独立 power-loss 证据；不得用 QEMU SIGKILL 冒充；方法合同完成不等于 host 验收",
    ),
    NeedToDoItem(
        "guest-filesystem-corruption",
        "OS 故障模型",
        "destructive_model_open",
        "tests/testkit/guest_filesystem_corruption.py::validate_guest_filesystem_corruption_evidence",
        "在独立 disposable overlay 中取得精确损坏注入、持久前缀和恢复证据；不得从 panic 推断；方法合同完成不等于 host 验收",
    ),
    NeedToDoItem(
        "historical-evidence",
        "统一证据审计",
        "historical_evidence_missing",
        "tests/testkit/requirement_audit.py::build_requirement_audit",
        "原始报告和 sidecar 以原 SHA 可回读；不能重建、回填或用新报告替代历史字节",
    ),
    NeedToDoItem(
        "final-unified-acceptance",
        "最终验收",
        "blocked_by_open_items",
        "tests/testkit/unified_acceptance.py::evaluate_unified_acceptance",
        "所有显式需求均 pass、source_verified 且同一 revision；人工 review 仍独立于 release 授权",
    ),
    NeedToDoItem(
        "issue-merge",
        "不稳定新功能",
        "excluded_unstable",
        "need-to-do 仅保留测试位置，不实现",
        "功能冻结命令、事件、权限和回滚契约后另开测试设计阶段",
    ),
    NeedToDoItem(
        "feishu-integration",
        "不稳定新功能",
        "excluded_unstable",
        "need-to-do 仅保留测试位置，不实现",
        "联调契约冻结且由用户明确进入该阶段",
    ),
    NeedToDoItem(
        "degraded-work-packages",
        "降级事项",
        "deferred_degraded",
        "steps.md 降级清单",
        "所有非降级主线完成并满足各自恢复条件后再排期",
    ),
    NeedToDoItem(
        "deterministic-mainline",
        "测试框架",
        COMPLETED_STATUS,
        "tests/testkit/suites.py::MAINLINE",
        "当前注册面可执行、可判定、可留证且来源绑定",
        "/tmp/zf-mainline-current-20260929/testkit-run.json",
    ),
    NeedToDoItem(
        "guest-panic-recovery-framework",
        "OS 故障模型",
        COMPLETED_STATUS,
        "tests/conformance/test_zaofu_testkit_s4_isolated_vm_recovery_host.py::test_guest_panic_inflight_run_recovers_through_full_cli_start",
        "guest panic exact model 的同盘恢复和 cleanup 证据可回读",
        "attachments/k591-guest-panic-recovery-20260928/host-acceptance/testkit-run.json",
    ),
    NeedToDoItem(
        "requirement-audit-framework",
        "统一证据审计",
        COMPLETED_STATUS,
        "tests/testkit/requirement_audit.py::build_requirement_audit",
        "缺证、篡改、历史选择和来源漂移均 fail-closed",
        "tests/testkit/test_requirement_audit.py",
    ),
    NeedToDoItem(
        "unified-readiness-gate",
        "最终验收",
        COMPLETED_STATUS,
        "tests/testkit/unified_acceptance.py::evaluate_unified_acceptance",
        "只读返回 ready_for_operator_review，release_authorized 永远为 false",
        "tests/testkit/test_unified_acceptance.py",
    ),
)


def validate_inventory(items: Iterable[NeedToDoItem]) -> dict[str, Any]:
    """Validate the inventory's classification invariants and summarize it."""
    rows = tuple(items)
    if not rows:
        raise ValueError("need-to-do inventory must not be empty")
    ids = [row.item_id for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("need-to-do item ids must be unique")
    for row in rows:
        if row.status not in ALL_STATUSES:
            raise ValueError(f"unknown need-to-do status: {row.status}")
        if not row.method_ref.strip() or not row.completion_condition.strip():
            raise ValueError(f"need-to-do item lacks method/condition: {row.item_id}")
        if row.status in OPEN_STATUSES and row.counts_as_product_pass:
            raise ValueError(f"open item cannot count as product pass: {row.item_id}")
        if row.status == COMPLETED_STATUS and not row.evidence_ref.strip():
            raise ValueError(f"completed framework item lacks evidence: {row.item_id}")
        if row.status == "operator_skip" and row.counts_as_product_pass:
            raise ValueError(f"operator skip cannot count as product pass: {row.item_id}")
    return {
        "schema_version": SCHEMA_VERSION,
        "items": [asdict(row) for row in rows],
        "open_item_ids": [row.item_id for row in rows if row.status in OPEN_STATUSES],
        "completed_framework_ids": [
            row.item_id for row in rows if row.status == COMPLETED_STATUS
        ],
        "product_pass_count": sum(row.counts_as_product_pass for row in rows),
        "scope": "status guard only; no completion percentage and no product verdict inference",
    }


def current_inventory() -> dict[str, Any]:
    return validate_inventory(CURRENT_INVENTORY)


def inventory_from_mappings(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Parse external rows for audit tests without trusting a precomputed summary."""
    return validate_inventory(NeedToDoItem(**dict(row)) for row in rows)
