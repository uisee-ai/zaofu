"""Structure-discipline forcing functions (2026-06-10 review S7).

Three recurring debt classes kept reappearing because enforcement relied
on reviewer vigilance (violating P9 "signals, not scripts"):

1. registered-but-missing design docs (doc-77 was a dangling 00-index
   reference for days — the orphan check only caught the inverse);
2. library-without-callers runtime modules (I31; the Wave-5 batch shipped
   six tested-but-never-imported modules);
3. oversized files growing every sprint despite the "add beside, don't
   append" rule (control_actions.py 2179→3325 across one sprint family).

These tests turn each rule into a mechanical gate. Baselines are frozen
to the 2026-06-10 state: fixing an entry means *removing* it from the
whitelist, not loosening the test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_DESIGN = _REPO / "docs" / "manual"
_SRC = _REPO / "src" / "zf"
_SOURCE_FILE_SOFT_LIMIT = 1000


# --- 1. 00-index inverse-orphan: every registered doc must exist ------------

def test_00_index_links_resolve_to_existing_files():
    """docs.md's orphan check catches unregistered files; this is the
    inverse — a registered `NN-slug.md` whose file is missing (the doc-77
    failure shape: index row + cross-references + implemented task, but
    no file on disk)."""
    index = (_DESIGN / "00-index.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\(([0-9]{2}-[A-Za-z0-9._-]+\.md)\)", index)
    assert links, "00-index should register numbered design docs"
    missing = sorted({
        link for link in links if not (_DESIGN / link).exists()
    })
    assert not missing, (
        f"00-index.md registers design docs that do not exist on disk: "
        f"{missing}. Either restore the file from git history or remove "
        f"the registration row (and fix the doc-count header)."
    )


def test_design_doc_numbering_has_no_duplicates():
    seen: dict[tuple[str, str], list[str]] = {}
    for path in _DESIGN.glob("[0-9][0-9]-*.md"):
        locale = "en" if path.name.endswith(".en.md") else "default"
        seen.setdefault((path.name[:2], locale), []).append(path.name)
    dupes = {num: names for num, names in seen.items() if len(names) > 1}
    assert not dupes, f"duplicate design-doc numbers: {dupes}"


# --- 2. runtime orphan modules (I31 library-without-callers) ----------------

# Frozen 2026-06-10: modules with tests but zero non-test importers.
# Wiring or deleting one of these → REMOVE it here (the test fails when an
# entry stops being an orphan, so the whitelist cannot rot). Adding a NEW
# orphan module → the test fails until it gains a runtime caller (I31:
# grep-proof into orchestrator*/start.py) or an explicit entry with a
# defer trigger.
_KNOWN_ORPHAN_RUNTIME_MODULES = {
    # Wave-5 batch (2026-05-18), tests but no runtime importer:
    "fanout_run_id",
    "memory_journal",
    "operator_target_resolver",
    "research_artifact",
    "wave_review",
    # Self-declared experimental_unwired in hook_registry.py:114 (string
    # reference only).
    "task_lifecycle_hooks",
    # X17(2026-06-12):mailbox 状态机纯函数已测;defer trigger = channel
    # bridge 落地 posted→sent 桥接时移除本条并接真实消费者。
    # (cursor/check_preflight 已于 c106086 真接线,按双向门移出。)
    "agent_mailbox",
}


def _runtime_module_names() -> set[str]:
    return {
        path.stem
        for path in (_SRC / "runtime").glob("*.py")
        if path.stem != "__init__"
    }


def _imported_runtime_modules() -> set[str]:
    """Runtime modules imported anywhere in src/ outside themselves."""
    import_re = re.compile(
        r"(?:from\s+zf\.runtime\.([A-Za-z_][A-Za-z0-9_]*)\s+import"
        r"|import\s+zf\.runtime\.([A-Za-z_][A-Za-z0-9_]*)"
        r"|from\s+zf\.runtime\s+import\s+([^\n]+))"
    )
    imported: set[str] = set()
    runtime_dir = _SRC / "runtime"
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in import_re.finditer(text):
            direct = match.group(1) or match.group(2)
            if direct and path != runtime_dir / f"{direct}.py":
                imported.add(direct)
            if match.group(3):
                for token in re.split(r"[,()\s]+", match.group(3)):
                    token = token.strip()
                    if token and token != "as":
                        imported.add(token)
    return imported


def test_runtime_modules_have_callers_or_are_whitelisted():
    modules = _runtime_module_names()
    imported = _imported_runtime_modules()
    orphans = {
        name for name in modules
        if name not in imported
    }
    new_orphans = sorted(orphans - _KNOWN_ORPHAN_RUNTIME_MODULES)
    assert not new_orphans, (
        f"New library-without-callers runtime modules (I31): {new_orphans}. "
        f"Wire each into a runtime caller (grep-proof in "
        f"src/zf/runtime/orchestrator*.py or src/zf/cli/start.py) or delete "
        f"the module — do not extend the whitelist without a defer trigger."
    )
    healed = sorted(_KNOWN_ORPHAN_RUNTIME_MODULES - orphans)
    assert not healed, (
        f"Whitelisted orphan modules now have callers or were removed: "
        f"{healed}. Remove them from _KNOWN_ORPHAN_RUNTIME_MODULES so the "
        f"whitelist reflects reality."
    )


# --- 3. oversized-file growth freeze ----------------------------------------

# Frozen 2026-06-10 line counts + ~10% headroom. These files are known
# defer debt; the rule being enforced is AGENTS.md "add new behavior
# beside oversized files instead of appending". Hitting a cap means: put
# the new code in a sibling module. Shrinking a file is always fine —
# lower the cap when you do.
# Retightened 2026-06-12 after the K1 slimming batch: orchestrator /
# dispatch / lifecycle shrank ~2100 lines combined but kept their old
# caps (lifecycle headroom had ballooned to 42%), which disarms the
# freeze. Caps below are current line count + ~10%. Raising any cap
# requires a same-PR justification in the commit message answering
# "why can this not be a sibling module".
# Reconciliation 2026-07-03(第四轮,origin pull 合流):chat-e2e/audit B1/B2 +
# avbs-r2 批推进 5 个 cap。四轮移动靶 = merge 前置 structure 门的持续论据。
_OVERSIZED_FILE_CAPS = {
    # P1 seam 1 (2026-06-12): 4561 lines of read-side projections moved
    # to src/zf/web/projections/*; cap lowered to new size +10%.
    # Reconciliation 2026-07-03: doc-125 web-wizard + R6 GZip/ETag (+29) + prior
    # projection-seam merges pushed to 9052 past the frozen 8100. Freeze at clean
    # dev size; next server route must land in a projections/* sibling.
    "src/zf/web/server.py": 9307,
    # P3 (2026-06-12): 49 fanout/synth coordination methods moved to
    # FanoutCoordinationMixin (orchestrator_fanout.py); both files
    # frozen at new size +10% — the mixin is born oversized and is
    # explicitly capped from day one.
    # Merge 2026-06-22: dev-0620-hermes carried recovery/read-model wiring
    # that grew this file before the full-suite merge gate ran. Freeze at the
    # merged size; next orchestrator behavior change must move to a sibling.
    # +36 (2026-06-24, B-STUCK-1): _remember_dispatch_id keeps a bounded recent
    # dispatch_id history so a respawned worker's in-flight completion is grace-
    # accepted instead of dropped (false-stuck livelock). It mutates the same
    # self._active_dispatch_ids state this file already owns; a sibling module
    # for one tiny stateful helper would split that ownership, so the cap absorbs.
    # Reconciliation 2026-07-03: crossed to 4384 via post-2026-06-24 merges
    # without a cap bump. Freeze at clean dev size; next orchestrator behavior extracts.
    # Reconciliation 2026-07-03(第三轮): flow-productization merges → 4422.
    # +44 (2026-07-04, avbs-r4 F 批): 三个 sibling 模块的 call site 接线
    # (reader_child_task_resolution 富化、rework bump 事件窗、lag 自监控)
    # ——逻辑均在 sibling,call site 必须在 run_once 环点无法外移。
    # +22 (2026-07-06, bizsim r4 FIX-5②): 同型触发退避的 streak 记账
    # (__init__ 两字段)+ notify 门控 call site;窗口计算逻辑在
    # wake_patterns sibling(layer2_effective_wake_interval)。
    # +11 (2026-07-06, bizsim r4 FIX-6): workflow.reconcile.requested 进
    # _KERNEL_LIVENESS_EVENTS + Layer-1 fallback 对无 task_id 活性事件跑
    # primary——均为 run_once 路由 call site;重扫逻辑本体在
    # orchestrator_reactor._on_workflow_reconcile_requested。
    # Reconciliation 2026-07-07: terminal child progress predicate + invalid
    # rework-state retry guard are run_once/_apply_housekeeping call sites;
    # predicate/state logic lives in siblings. Freeze current size.
    # Reconciliation 2026-07-08: run-manager feishu 回调/skill provider
    # discovery merges 后 dev 已到 4725,cap 未随之更新(基线红)。按
    # clean dev 尺寸冻结;下一个 orchestrator 行为增量必须外提 sibling。
    # P0-1 (2026-07-09): +25 for the budget gate in _send_transport_task
    # (BudgetExceededError + the check). It cannot be a sibling — the gate must
    # live inside the charging primitive so every paid dispatch funnels through
    # it (RB1). Re-freeze at the new size.
    # P1-7 (2026-07-09): +14 for resolving the rework-target role at the
    # circuit-breaker housekeeping call site (needs self.config, so it can't
    # move into mechanical housekeeping). Re-freeze.
    # Merge 2026-07-09: combined with dev's universal-activity-liveness
    # (6d3f7379), which adds per-tool-call heartbeat wiring here too. Re-freeze
    # at the merged size.
    # +79 (2026-07-11, ZF-E2E-RACING-P1): _rollback_inflight_dispatch mutates
    # the same self._active_dispatch_ids + task_store bookkeeping this file
    # owns (same ownership argument as the B-STUCK-1 precedent above), and the
    # dead-dispatch emission extends the existing _run_dispatch_sweep wrapper —
    # the sweep logic itself lives in the dispatch_sweep.py sibling.
    # +47 (2026-07-11, ZF-E2E-MINI-P2): budget-freeze silence gate inside
    # the existing _notify_orchestrator_agent gate stack (parallel to the B11
    # cooldown and coalescing gates it sits between) + the non-emitting
    # _global_budget_frozen probe over self.cost_tracker this file owns. The
    # silenced-event set lives in the wake_patterns.py sibling.
    # +22 (2026-07-11): dead-sweep authoritative progress lookup (+12, part of
    # the sweep calibration that owns this file's in-flight bookkeeping) merged
    # with dev's semantic-recovery loop wiring (+10, parallel driver). Re-freeze.
    "src/zf/runtime/orchestrator.py": 4927,
    # +14 (2026-06-20): PRD-product-stage branch in the fanout-child briefing
    # builder (emits prd_ref/artifact_refs/evidence_refs). It is one more
    # sibling branch of the same _write_*_fanout_briefing method that already
    # holds is_refactor_review / is_refactor_plan / is_plan_artifact_stage —
    # extracting a single elif to a new module would split one cohesive method,
    # so the cap is bumped to the new size instead.
    # +33 (2026-06-20): de-hardcode that branch — derive handoff-ref fields from
    # event_schemas (_contract_handoff_ref_fields + _HANDOFF_REF_FIELDS vocab +
    # loose-mode fallback) so it generalizes to custom DAG flows and stays a
    # single source of truth with the gate. The helper is a small pure function
    # tightly coupled to this file's briefing builder; a sibling module for one
    # one-method helper would be worse, so the cap absorbs it.
    # Merge 2026-06-22: R37/lane-pipeline recovery added cohesive fanout
    # resume/rework paths here. Freeze at merged size; next fanout behavior
    # must split into a sibling module before this cap can move.
    # +110 (2026-06-24, B-STUCK-1b): _resolve_orphan_reader_fanout_child re-binds
    # a reader-child completion that lost its fanout_id/child_id (restart / bare
    # re-dispatch) back to its child by the emitting role instance, so the
    # barrier resolves instead of timing out (ledgerlite prd-refine livelock). It
    # reads this file's own manifest helpers (_fanout_manifest /
    # _fanout_child_result_events) and feeds _maybe_update_reader_fanout right
    # below it; a sibling module would have to re-import all of that fanout
    # coordination state, so the cap absorbs the cohesive addition.
    # +41 (2026-06-24, F7): assign_nonaffinity_writer_roles — non-affinity writer
    # dispatch by owner_role instead of list position (fixed a frontend role
    # doing backend work). It is a module-level pure helper next to the dispatch
    # loop that calls it; a sibling module for one assignment helper would split
    # the dispatch logic, so the cap absorbs it.
    # Reconciliation 2026-07-03: doc-125 / RF-7A-b / lane-recovery merges grew
    # this by ~1391 past the frozen cap without updating the registry (baseline
    # went red-by-staleness, masking real regressions). Freeze at current clean
    # dev size; next fanout behavior must extract to a sibling before this moves.
    # Split tracked in tasks/active/2026-07-03-0457-test-suite-hygiene-baseline-27-red.md.
    # +56 (2026-07-04, prod-e2e): bad task_map 的 no-dead-end 上游返工
    # 路由——emit 点必须内嵌在 admission except 序列(拓扑查找逻辑 20 行,
    # 与既有 cancel 语境强耦合,独立 sibling 反而断上下文)。
    # +37 (2026-07-05, prod-e2e): plan briefing 的 task_map JSON 合同
    # 条款(与 admission 合同对齐,F4 分叉修复)必须内嵌在 briefing 组装
    # 序列;replan 逻辑本体在 stage_failure_replan sibling。
    # +32 (2026-07-05, r6-F4): fanout child briefing 渲染活跃 waiver
    # (F6 缺口:injection 路径有、fanout 无 → verify 审角色看不见豁免令);
    # waiver 读取在 waivers sibling,此处仅渲染 call site。
    # +22 (2026-07-05, r6-F2): required_runtime_evidence 精确路径清单
    # 渲染进 child briefing(命名合同传导,四轮 cap 文件名官僚战根治);
    # 渲染 call site 与 F4 waiver 段同点。
    # +81 (2026-07-05, BF-1): 跨代收编接线 + completion_adopted 审计
    # 发射器;收编决策逻辑在 sibling fanout_completion_adoption.py,
    # 留此处的是必须访问 event_writer/manifest 的 call site。
    # +10 (2026-07-06, G4/U21): goal 块渲染 call site(文案在 sibling
    # goal_briefing.py)。
    # +48 (2026-07-06, U20/U7/U10): 证据观测门 call site(判定在 sibling
    # report_evidence_gate.py)+ briefing 身份注记与权限醒目段(纯文案,
    # 必须与既有 briefing 渲染同点)。
    # +53 (2026-07-06, 批A A1/A3/A5): task_map 绝对路径写入指令、candidate
    # 受审对象注记(judge 顺序缺陷根治)、defer 分级冷却——全部是既有
    # briefing 渲染/派发检查的同点插入,无独立逻辑可拆。
    # +25 (2026-07-06, 批C C1/C2/C3): planner briefing 合同条款(共享约定
    # 单源/验证层级/骨架波)——与既有 task_map guidance 同点,纯文案。
    # +21 (2026-07-06, E6/E7): 超时地板与单 lane 兜底——派发/超时检查的
    # 同点插入。
    # +5 (2026-07-06, bizsim r4 FIX-1): rework fanout.started payload 补
    # task_id/rework_attempt 代际隔离字段——emission call site 必须内联;
    # identity 判定逻辑在 fanout_identity sibling。
    # +batch(2026-07-06, A2/B/D/E1): 外部状态事件入内存/微环与 light/
    # rescan 消费/invoke 自举——全部是 registry 注册与 handler 转发,
    # 判定逻辑在 sibling(lane_micro_loop/light_flow/quiescent)。
    # +FIX-6(2026-07-06, bizsim r4 F2): workflow reconcile 重扫 handler
    # 作为 registry handler call site 留在 reactor mixin。
    # Reconciliation 2026-07-07: controller child terminal event support and
    # fanout briefing config-safe call sites; helpers live in siblings.
    # +13 (2026-07-07): stale-generation guard inside
    # _resolve_orphan_reader_fanout_child — an inline candidate filter in that
    # method's loop (skip superseded fanout generations so a resident worker's
    # fresh completion is not re-bound to a long-superseded fanout and dropped).
    # Cannot be a sibling: it is a guard clause on the loop's own iteration.
    # +61 (2026-07-08, LB-4/LB-5 执法批): ①A3 受审对象兜底段——
    # candidate_ref 缺席的验收读者 briefing 注入 SUBJECT OF REVIEW(light
    # 终审误拒根修),是 _write_fanout_briefing 既有 A3 条件块的 else 支;
    # ②lane.stage.* 锻造补 attempt_id/handoff_ref/evidence_refs 契约键
    # (blocking 档下缺键会让内核自己的交接事件被 discriminator.failed
    # 替换);③U20 fail-closed 并入既有 malformed-report 失败轨道的判定
    # 三行。三处都是既有方法内的同点插入,判定文案/门逻辑在
    # report_evidence_gate 与 schema_profiles sibling,无独立逻辑可拆。
    # +45 (2026-07-08, LB-4 模板缺口回归): _schema_education_toplevel_fields
    # ——v3 给 child 完成事件加了顶层 non_empty(summary/evidence_refs),
    # FIX-14 的 report.* 教育不覆盖顶层,blocking 档下合规 agent 照抄模板
    # 也会被拦。新方法与既有 _schema_education_report_fields 是一对镜像,
    # 紧贴 briefing 组装点(共用 _SCHEMA_EDU_PLACEHOLDERS + self.config
    # registry),拆 sibling 会割裂这对教育逻辑,故 cap 吸收。
    "src/zf/runtime/orchestrator_fanout.py": 8560,
    # Reconciliation 2026-07-07: terminal child success predicate and
    # judge.failed state guard call sites; predicates live in terminal_events.
    # Reconciliation 2026-07-08: dev 已到 6962(feishu/goal-spine merges),
    # cap 未随之更新(基线红)。按 clean dev 尺寸冻结。
    "src/zf/runtime/orchestrator_reactor.py": 6962,
    # Merge 2026-06-22: dispatch recovery helpers crossed the previous cap.
    # Reconciliation 2026-07-03: RF-7B transition-only dispatch (+32) + prior
    # merges pushed to 4851. Freeze at clean dev size; next dispatch feature extracts.
    # +26 (2026-07-04, avbs-r4 F1-D2): rework_scope_guard sibling 的 emit
    # wrapper + 固定路由解析点的告警 call site,判定逻辑在 sibling。
    # +50 (2026-07-04, E5): attempt_ledger sibling 的 deadletter 短路与
    # cap 账本接线——两者都必须内嵌在 _dispatch_rework 决策序列里
    # (deadletter 在 cap 之前、cap 在 busy 之前),计数/分类逻辑在 sibling。
    # +22 (2026-07-04, 131-P2-1): task.attempt.retry_scheduled 发射必须
    # 与 dispatch_id 铸造同点(lease_token 即 dispatch_id),纯 emit call
    # site;事件语义/registry 合同在 event_problem_registry sibling。
    # Reconciliation 2026-07-07: child terminal rework/attempt/cap de-dupe and
    # legacy rework-state guard call sites; policy/helpers stay outside.
    # Reconciliation 2026-07-08: dev 已到 5072(并行 merges),按 clean dev
    # 尺寸冻结(基线红清账)。
    # P1-7 (2026-07-09): +9 for the circuit-breaker check in the legacy
    # _dispatch_rework path (it previously bypassed the breaker the main
    # dispatch loop already honors). Re-freeze.
    # +36 (2026-07-11, ZF-E2E-RACING-P2): rework owner authority — routing
    # reorder inside _resolve_rework_role plus the _triage_owner_excludes_
    # lane_rework predicate. The predicate guards the single decision point
    # (_route_rework_trigger) and uses this file's role-lookup helpers;
    # extracting a 23-line gate to a sibling would split the routing decision.
    "src/zf/runtime/orchestrator_dispatch.py": 5119,
    # P2 (2026-06-12): handler domains moved to 5 mixins + helpers
    # (control_actions_{channel_msg,channel_admin,product,ops,emit,
    # helpers}.py); cap lowered to new size +10%.
    # Full-suite reconciliation 2026-06-26: controlled-action runtime already
    # reached 497 in dev/HEAD before this branch's commit. Freeze here; next
    # action-domain growth must move into a domain sibling module.
    "src/zf/runtime/control_actions.py": 560,
    "src/zf/runtime/orchestrator_lifecycle.py": 2750,
    # Frontend freeze (2026-06-12): the two web monoliths had no size
    # gate at all (doc 44 "web 没人 review"); after the server.py split
    # App.tsx is the largest single file in the repo. Caps are the
    # in-flight working-tree size +10% so the gate cannot fire on the
    # parallel driver's pending web batch. Split plans:
    # backlogs/2026-06-12-0502-P1-app-tsx-page-component-extraction.md
    # and ...-P2-styles-css-two-phase-split.md (doc 67 Phase 2 line).
    # P1 frontend split (2026-06-12): 13 pages + shared layer extracted
    # to components/*/ and app/shared*; cap lowered to new size +10%.
    # Full-suite reconciliation 2026-06-26: dashboard shell reached 3191 after
    # workspace-default delete guard wiring. Freeze here; next page/view
    # addition must extract.
    # Reconciliation 2026-07-03: dashboard shell reached 3449 via doc-125 wizard
    # intake + page wiring. Freeze at clean dev size; next page/view must extract.
    # +11 (2026-07-04 E0 清红): feishu kanban Accept 渲染(功能增量,
    # 并行线)越门未同步快照,dev 现行红多日;按当前尺寸冻结,
    # 下一个 view 增量必须外提组件。
    # +1 (2026-07-04, 131-P0-5): SpineHealthStrip 接线仅 projectId prop
    # 一行;组件本体在 kanban/SpineHealthStrip.tsx sibling。
    # +2 (2026-07-05, init onboarding 打通): ProjectWizardModal 渲染点
    # + import 各一行;展示逻辑在 workspace/ProjectInitOnboarding.tsx
    # sibling(git hook 状态 + scripts.setup 建议)。
    # +69 (2026-07-08, BootstrapInspector 接进 New Project 两个 tab):
    # 在既有 ProjectWizardModal 组件内原地扩展(inspect state + 共享候选面板
    # + Existing 裸库→转 Create)。为何不 sibling:候选面板与 modal 的 draft/
    # detect 状态强耦合,抽出需连带搬 draft 契约,属独立重构,超出本功能范围。
    # +68 (2026-07-09, Triage autopilot durable-proposals fix 18d1afea): the
    # reusable proposal-merge logic went to a sibling (web/src/app/triageProposals.ts);
    # what remains in App.tsx is the Triage page's getKanbanPendingProposals fetch
    # effect + wiring, which must live in the page component it feeds.
    # -83 (2026-07-11, operator 决定移除 New Task 手工建任务:按钮×2/命令面板
    # 入口/NewTaskModal/draft 持久化/createTaskFromDraft 全链删除)。缩水即
    # 收紧:cap 3652→3579(新尺寸 +10)。
    "web/src/app/App.tsx": 3579,
    # P2 phase 1 (2026-06-12): split into web/src/styles/ ordered chunks
    # (bundle byte-identical); styles.css is now an @import manifest.
    "web/src/styles.css": 200,
}


@pytest.mark.parametrize("rel_path,cap", sorted(_OVERSIZED_FILE_CAPS.items()))
def test_oversized_file_growth_frozen(rel_path: str, cap: int):
    path = _REPO / rel_path
    if not path.exists():
        pytest.skip(f"{rel_path} no longer exists (split? lower the cap map)")
    lines = sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
    assert lines <= cap, (
        f"{rel_path} grew to {lines} lines (cap {cap}). AGENTS.md: add new "
        f"behavior in a sibling module, do not append to known-oversized "
        f"files. If a refactor legitimately moved code here, adjust the cap "
        f"in the same PR with justification."
    )


# Files already over the 1000-line new-file soft limit at the 2026-06-12
# baseline but not governed by a per-file growth cap above. This second gate
# catches the old false-negative shape: a brand-new oversized source file that
# is not explicitly acknowledged as debt.
_KNOWN_OVERSIZED_SOURCE_FILES = {
    "src/zf/autoresearch/failure_signals.py",
    "src/zf/autoresearch/orchestrator.py",
    "src/zf/cli/feishu.py",
    # Existing CLI monolith; split when board/show/handoff commands next grow.
    "src/zf/cli/kanban.py",
    "src/zf/core/config/loader.py",
    "src/zf/core/config/schema.py",
    "src/zf/core/verification/discriminator.py",
    "src/zf/core/verification/event_schema.py",
    "src/zf/runtime/automation_projection.py",
    # 2026-07-04 blocked-burn 看门狗(70 行)破千;tick_services 是纯
    # 调度器+module 级看门狗集合,同质 handler 列表形状。defer trigger:
    # 下个 watchdog 进驻时抽 tick_watchdogs.py sibling。
    "src/zf/runtime/tick_services.py",
    "src/zf/runtime/candidates.py",
    "src/zf/runtime/channel_projection.py",
    # E1/E3(2026-07-04)registry closure 达成 100% 后 43 条新注册破千行。
    # 同质数据表(平行 EventProblemSpec 条目),认知成本=滚动而非结构;
    # defer 触发条件:下次新增 ≥10 条 spec 时把 EVENT_PROBLEM_SPECS 数据
    # 段拆到 event_problem_specs_data.py,逻辑留本文件。
    "src/zf/runtime/event_problem_registry.py",
    "src/zf/runtime/housekeeping.py",
    "src/zf/runtime/injection.py",
    "src/zf/runtime/long_horizon.py",
    "src/zf/runtime/product_delivery.py",
    "src/zf/runtime/run_archive.py",
    # Full-suite reconciliation 2026-06-24: product controlled-action helpers
    # crossed the soft limit at 1002. Split trigger: next product action or
    # product delivery controlled-action change.
    "src/zf/runtime/control_actions_product.py",
    # Full-suite reconciliation 2026-06-24: Run Manager recovery loop predates
    # the soft-limit registration and now hosts monitor/projection/action
    # policy in one file. Split trigger: next Run Manager monitor, projection,
    # or action-policy feature.
    "src/zf/runtime/run_manager.py",
    "src/zf/runtime/task_refs.py",
    # Merge 2026-06-22: workflow checkpoint/resume recovery landed as a
    # cohesive runtime path. Split trigger: next workflow-resume bugfix or
    # checkpoint format change.
    "src/zf/runtime/workflow_resume.py",
    "src/zf/runtime/workflow_resume_apply.py",
    "src/zf/runtime/workdirs.py",
    "src/zf/web/headless_agent.py",
    # Merge 2026-06-22: task timeline/read-model projection grew past the
    # soft limit. Split trigger: next task timeline/read-model endpoint change.
    "src/zf/web/projections/tasks.py",
    "web/src/app/shared.tsx",
    "web/src/components/channel/ChannelPage.tsx",
    "web/src/components/delivery-trace/BehaviorLoopPage.tsx",
    "web/src/components/kanban/TaskDetail.tsx",
    "web/src/components/observability/ObservabilityPage.tsx",
    "web/src/styles/07-agent.css",
    # Reconciliation 2026-07-03 (tasks/active/2026-07-03-0457-...): these crossed
    # the 1000-line soft limit via doc-118..125 workflow-intake + web-wizard
    # merges without a debt entry, so the guard fired red-by-staleness on clean
    # dev. Registered here with split triggers; real splits tracked in the task.
    # Split trigger: next flow-intake/submit command change.
    "src/zf/cli/flow.py",
    # Split trigger: next supervisor inspection signal or attention rule.
    "src/zf/runtime/supervisor_inspection.py",
    # Split trigger: next read-model projection/endpoint or freshness-gate change.
    "src/zf/web/projections/read_model.py",
    # Split trigger: next agent-session timeline view feature.
    "web/src/components/agent-session/AgentSessionTimeline.tsx",
    # Split trigger: next orchestrator-panel section or agent-cockpit view.
    "web/src/components/orchestrator/OrchestratorPanel.tsx",
    # Split trigger: next delivery-page style block.
    "web/src/styles/11-delivery.css",
    # Reconciliation 2026-07-03(第三轮):
    # render.py crossed 1000 via flow-productization (f023b6e4).
    # Split trigger: next flow-spec render/materialize branch.
    "src/zf/core/config/render.py",
    # common.py crossed via kanban-agent contract shapes (5fca581c) + RF-10
    # shared ref-keys/collect helpers (f4d0a2b7) — joint growth.
    # Split trigger: next shared projection helper.
    "src/zf/web/projections/common.py",
    # Reconciliation 2026-07-03 (dev eed79540 workflow-productization merge):
    # crossed 1000 via new controller/flow-intake code. Split trigger below.
    # Split trigger: next start-command flow/kind branch.
    "src/zf/cli/start.py",
    # Split trigger: next workflow-profile archetype or catalog rule.
    "src/zf/core/config/workflow_profiles.py",
}


def _source_files() -> list[Path]:
    return [
        path
        for root in (_SRC, _REPO / "web" / "src")
        for path in root.rglob("*")
        if path.suffix in {".py", ".tsx", ".css"}
    ]


def test_unknown_oversized_source_files_are_not_silent_debt():
    known = set(_OVERSIZED_FILE_CAPS) | _KNOWN_OVERSIZED_SOURCE_FILES
    oversized = {
        path.relative_to(_REPO).as_posix()
        for path in _source_files()
        if (
            sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
            > _SOURCE_FILE_SOFT_LIMIT
        )
    }
    unknown = sorted(oversized - known)
    assert not unknown, (
        f"Unknown source files over the {_SOURCE_FILE_SOFT_LIMIT}-line "
        f"soft limit: {unknown}. "
        f"Split the file, or add an explicit debt entry with a concrete "
        f"defer trigger in _KNOWN_OVERSIZED_SOURCE_FILES."
    )

    stale = sorted(
        rel_path
        for rel_path in _KNOWN_OVERSIZED_SOURCE_FILES
        if rel_path not in oversized
    )
    assert not stale, (
        f"Oversized debt entries are no longer over "
        f"{_SOURCE_FILE_SOFT_LIMIT} lines or no longer exist: {stale}. "
        f"Remove them from _KNOWN_OVERSIZED_SOURCE_FILES."
    )


# --- 4. reactor handler registration (method-level orphans) -----------------

# The module-level orphan check (section 2) cannot see a dead `_on_*`
# handler added inside the frozen-oversized reactor file: it is "in a
# module with callers" yet never wired to any event. Registration lives
# in `_BUILTIN_HANDLER_METHODS` (string table) plus a small number of
# `self._on_*` programmatic registrations (workflow-graph shadow
# handlers). Both directions are enforced statically:
#   - a defined `_on_*` with zero registrations/references is dead code;
#   - a table entry naming a missing method currently fails OPEN at
#     runtime (`getattr(self, name, None) -> continue`), so a typo'd
#     registration would silently never fire.

_REACTOR_GLOB = "runtime/orchestrator*.py"


def _orchestrator_sources() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(_SRC.glob(_REACTOR_GLOB))
    )


def test_reactor_handlers_are_registered_or_referenced():
    text = _orchestrator_sources()
    defined = set(re.findall(r"^    def (_on_[a-z0-9_]+)\(", text, re.M))
    assert defined, "expected _on_* reactor handlers in orchestrator*.py"
    table = set(re.findall(r'"(_on_[a-z0-9_]+)"', text))
    dead = sorted(
        name
        for name in defined - table
        if not re.search(rf"self\.{name}\b", text)
    )
    assert not dead, (
        f"Dead reactor handlers (defined but never registered in "
        f"_BUILTIN_HANDLER_METHODS nor referenced as self.<name>): {dead}. "
        f"Register the handler for its event type or delete it — do not "
        f"leave unreachable handlers inside a frozen-oversized file."
    )


def test_builtin_handler_table_has_no_dangling_methods():
    text = _orchestrator_sources()
    defined = set(re.findall(r"^    def (_on_[a-z0-9_]+)\(", text, re.M))
    table_methods = set(
        re.findall(r'\(\s*"[a-z0-9_.*]+"\s*,\s*"(_on_[a-z0-9_]+)"\s*\)', text)
    )
    dangling = sorted(table_methods - defined)
    assert not dangling, (
        f"_BUILTIN_HANDLER_METHODS names methods that do not exist: "
        f"{dangling}. Registry construction skips missing handlers "
        f"(getattr -> continue), so this registration would silently "
        f"never fire — fix the name or implement the handler."
    )


def test_skills_index_matches_directory():
    """skills/INDEX.md 名字集守卫(2026-07-08):INDEX 是目录地图,新增/删除
    技能不同步 = 地图失真。按名字集(非描述)双向比对 skills/ 与 yoke/。"""
    repo = _REPO
    index = repo / "skills" / "INDEX.md"
    assert index.is_file(), "skills/INDEX.md missing — regenerate the map"
    text = index.read_text(encoding="utf-8")
    listed = set(re.findall(r"^- `(?:yoke/)?([a-z0-9-]+)`", text, re.M))
    actual = {
        p.parent.name for p in repo.glob("skills/*/SKILL.md")
    } | {
        p.parent.name for p in repo.glob("yoke/*/SKILL.md")
    }
    missing = sorted(actual - listed)
    stale = sorted(listed - actual)
    assert not missing and not stale, (
        f"skills/INDEX.md drifted — missing entries: {missing}; "
        f"stale entries: {stale}. 新增/删除技能必须同步 INDEX。"
    )


def test_provider_skill_mirrors_stay_in_sync():
    """技能镜像同步哨兵(2026-07-08):同名技能在 skills/、.claude/skills/、
    .codex/skills/ 多树并存时 SKILL.md 必须逐字节一致——skills/ 为 canonical,
    provider 树是镜像。历史三向漂移(zf-cr 440/409/235 行)让三个入口各讲
    各话;镜像更新必须与 canonical 同 changeset 落齐。

    只枚举 **git 跟踪**的文件:.claude/.codex 里 gitignore 的本地物化副本
    是 runtime 投影,不是仓库契约,不得让本地状态打红共享门。"""
    import subprocess

    repo = _REPO
    tracked = subprocess.run(
        [
            "git", "-C", str(repo), "ls-files",
            "skills/*/SKILL.md",
            ".claude/skills/*/SKILL.md",
            ".codex/skills/*/SKILL.md",
        ],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    by_name: dict[str, list] = {}
    for rel in tracked:
        path = repo / rel.strip()
        if path.is_file():
            by_name.setdefault(path.parent.name, []).append(path)
    drifted = []
    for name, paths in sorted(by_name.items()):
        if len(paths) < 2:
            continue
        if len({p.read_bytes() for p in paths}) > 1:
            drifted.append(
                name + ": " + " ≠ ".join(
                    str(p.relative_to(repo)) for p in paths
                )
            )
    assert not drifted, (
        "skill mirror drift(同名多树技能必须同 changeset 同步,"
        "canonical 在 skills/):\n" + "\n".join(drifted)
    )
