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
_DESIGN = _REPO / "docs" / "design"
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
    seen: dict[str, list[str]] = {}
    for path in _DESIGN.glob("[0-9][0-9]-*.md"):
        seen.setdefault(path.name[:2], []).append(path.name)
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
_OVERSIZED_FILE_CAPS = {
    # P1 seam 1 (2026-06-12): 4561 lines of read-side projections moved
    # to src/zf/web/projections/*; cap lowered to new size +10%.
    "src/zf/web/server.py": 8100,
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
    "src/zf/runtime/orchestrator.py": 4153,
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
    "src/zf/runtime/orchestrator_fanout.py": 5731,
    "src/zf/runtime/orchestrator_reactor.py": 6700,
    # Merge 2026-06-22: dispatch recovery helpers crossed the previous cap.
    # Freeze at merged size; next dispatch feature must extract first.
    "src/zf/runtime/orchestrator_dispatch.py": 4713,
    # P2 (2026-06-12): handler domains moved to 5 mixins + helpers
    # (control_actions_{channel_msg,channel_admin,product,ops,emit,
    # helpers}.py); cap lowered to new size +10%.
    # Full-suite reconciliation 2026-06-26: controlled-action runtime already
    # reached 497 in dev/HEAD before this branch's commit. Freeze here; next
    # action-domain growth must move into a domain sibling module.
    "src/zf/runtime/control_actions.py": 497,
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
    "web/src/app/App.tsx": 3191,
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
    "src/zf/runtime/candidates.py",
    "src/zf/runtime/channel_projection.py",
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
