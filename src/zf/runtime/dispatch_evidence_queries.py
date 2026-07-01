"""Dispatch evidence queries — K1 切片 3(2026-06-11)。

从 orchestrator_dispatch.py verbatim 迁出的只读查询簇:派发前置
检查/handoff 识别/预算与熔断读取/role 检索/fanout 独立性评估。
零裁决零状态写;模式同 lifecycle 三 mixin(方法体一字未改,
self._* 缓存留宿主)。"""

from __future__ import annotations

import time
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.config.schema import RoleConfig
from zf.core.task.contract_validation import validate_task_contract
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.recovery_sufficiency import build_artifact_recovery_refs


class DispatchEvidenceQueriesMixin:
    def _dispatch_cycle_limit(self) -> int:
        roles = self.all_roles() if hasattr(self, "all_roles") else self.config.roles
        workers = [
            role for role in roles
            if role.name != "orchestrator"
            and role.instance_id not in getattr(self, "_hard_cap_exceeded", set())
        ]
        return max(1, len(workers))

    def _contract_ready_for_backlog_scheduler(self, task: Task) -> bool:
        contract = getattr(task, "contract", None)
        if contract is None:
            return False
        return bool(
            getattr(contract, "behavior", "")
            or getattr(contract, "verification", "")
            or getattr(contract, "verification_tiers", [])
            or getattr(contract, "scope", [])
            or getattr(contract, "owner_role", "")
            or getattr(contract, "owner_instance", "")
            or getattr(contract, "shared_files", [])
            or getattr(contract, "exclusive_files", [])
            or getattr(contract, "handoff_artifacts", [])
        )

    def _initial_role_for_ready_task(self, task: Task) -> str:
        contract = getattr(task, "contract", None)
        if contract is not None:
            owner_instance = getattr(contract, "owner_instance", "")
            if owner_instance:
                return owner_instance
            owner_role = getattr(contract, "owner_role", "")
            if owner_role:
                # #H-1 fix (TR-OWNER-ROLE-SCOPE-FALLBACK-001, cangjie
                # 2026-05-21 observation-H): when plan declares
                # owner_role=arch/critic but scope contains writer
                # paths (src/test/packages/.ts), fallback to dev to
                # prevent arch single-instance overload. cangjie
                # round 2 plan all 54 vertical with owner_role=arch
                # caused 1292 dispatch_skipped no_available_role +
                # 230k tokens arch context overload. This is defense-
                # in-depth against plan-level owner_role misconfig
                # (root fix should also be arch skill prompt 改善
                # so arch emits correct owner_role per vertical type).
                if owner_role in ("arch", "critic"):
                    scope = list(getattr(contract, "scope", []) or [])
                    if self._scope_suggests_writer_role(scope):
                        for role in self.config.roles:
                            if (
                                role.name == "dev"
                                and "task.assigned" in role.triggers
                            ):
                                return "dev"
                return owner_role
        for preferred in ("arch", "dev"):
            for role in self.config.roles:
                if role.name == preferred and "task.assigned" in role.triggers:
                    return role.name
        for role in self.config.roles:
            if role.name == "orchestrator":
                continue
            if "task.assigned" in role.triggers:
                return role.name
        return ""

    def _scope_suggests_writer_role(self, scope: list[str]) -> bool:
        """#H-1 helper: return True if scope paths suggest implementation
        rather than pure design.

        Heuristic: any path under src/, packages/, apps/, test/, tests/,
        providers/, or ending with a code extension (.ts/.py/.go/etc).
        Pure docs/specs/ scope stays arch.
        """
        if not scope:
            return False
        writer_prefixes = (
            "src/", "packages/", "apps/", "test/", "tests/",
            "providers/",
        )
        writer_extensions = (
            ".ts", ".tsx", ".js", ".jsx",
            ".py", ".go", ".rs", ".java",
            ".cpp", ".cc", ".c", ".h",
        )
        for raw in scope:
            path = str(raw).strip()
            if not path:
                continue
            if any(path.startswith(prefix) for prefix in writer_prefixes):
                return True
            if any(path.endswith(ext) for ext in writer_extensions):
                return True
        return False

    def _task_wave(self, task: Task) -> int:
        try:
            return int(getattr(getattr(task, "contract", None), "wave", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _task_exclusive_files(self, task: Task) -> list[str]:
        contract = getattr(task, "contract", None)
        raw = getattr(contract, "exclusive_files", []) if contract is not None else []
        out: list[str] = []
        for item in raw or []:
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
        return out

    def _check_fanout_independence(
        self,
        task_items: list[dict],
    ) -> tuple[bool, str]:
        """α-1 (2026-05-17): refuse fanout when proposed task siblings
        write overlapping files.

        Returns (independent, reason). ``independent=True, reason=""``
        is the safe-to-fanout case. ``independent=False`` carries a
        human-readable conflict summary with the offending file(s) and
        the two task ids that collide; caller emits ``fanout.serialize``
        with this reason and reroutes the tasks through serial backlog
        dispatch.

        Rules (per docs/design/36 §4.2 + α-1 backlog):

        - exclusive_files ∩ exclusive_files → conflict (both want to
          write the same file)
        - exclusive_files ∩ shared_files → conflict (one task locks a
          file the other reads)
        - shared_files ∩ shared_files → OK (read-only overlap)
        - Empty / single task list → trivially independent
        - Task with no contract or missing from store → skipped from
          the pairwise check (no claims to compare)
        - ``contract.fanout_force=True`` on ANY participating task →
          bypass the check entirely (operator escape hatch)
        """
        contracts: list[tuple[str, object]] = []
        for item in task_items:
            tid = str(item.get("task_id") or "").strip()
            if not tid:
                continue
            task = self.task_store.get(tid)
            if task is None:
                continue
            contract = getattr(task, "contract", None)
            if contract is None:
                continue
            if getattr(contract, "fanout_force", False):
                # Self-attest only: the forced task removes itself from
                # the pairwise gate, but **its siblings still get
                # checked against each other**. Original semantics let
                # one force task short-circuit the whole group — that
                # allowed operator escape-hatch misuse to silently
                # paper over real conflicts between non-forced
                # siblings. (review 2026-05-18 A1 narrowing)
                continue
            contracts.append((tid, contract))
        if len(contracts) < 2:
            return True, ""
        for i in range(len(contracts)):
            tid_i, c_i = contracts[i]
            excl_i = frozenset(getattr(c_i, "exclusive_files", []) or [])
            shrx_i = frozenset(getattr(c_i, "shared_files", []) or [])
            for j in range(i + 1, len(contracts)):
                tid_j, c_j = contracts[j]
                excl_j = frozenset(getattr(c_j, "exclusive_files", []) or [])
                shrx_j = frozenset(getattr(c_j, "shared_files", []) or [])
                overlap = excl_i & excl_j
                if overlap:
                    return False, (
                        f"exclusive_files overlap between {tid_i} and "
                        f"{tid_j}: {sorted(overlap)}"
                    )
                overlap = excl_i & shrx_j
                if overlap:
                    return False, (
                        f"{tid_i} exclusive collides with {tid_j} "
                        f"shared: {sorted(overlap)}"
                    )
                overlap = shrx_i & excl_j
                if overlap:
                    return False, (
                        f"{tid_j} exclusive collides with {tid_i} "
                        f"shared: {sorted(overlap)}"
                    )
        return True, ""

    def _exclusive_file_reservations(self) -> dict[str, str]:
        reservations: dict[str, str] = {}
        for task in self.task_store.list_all():
            if task.status != "in_progress":
                continue
            for path in self._task_exclusive_files(task):
                reservations.setdefault(path, task.id)
        return reservations

    def _contract_schedule_blocker(
        self,
        task: Task,
        *,
        exclusive_reservations: dict[str, str],
    ) -> str:
        wave = self._task_wave(task)
        if wave > 0:
            blocker_scope = self._task_wave_scope(task)
            for other in self.task_store.list_all():
                if other.id == task.id or other.status in {"done", "cancelled"}:
                    continue
                if not self._same_wave_scope(blocker_scope, self._task_wave_scope(other)):
                    continue
                other_wave = self._task_wave(other)
                if 0 < other_wave < wave:
                    return (
                        "wave_blocked:"
                        f"scope={blocker_scope[0]},scope_id={blocker_scope[1]},"
                        f"waiting_for={other.id},waiting_wave={other_wave},wave={wave}"
                    )

        conflicts: list[str] = []
        for path in self._task_exclusive_files(task):
            owner = exclusive_reservations.get(path)
            if owner and owner != task.id:
                conflicts.append(f"{path}:{owner}")
        if conflicts:
            return "exclusive_files_conflict:" + ",".join(conflicts)
        return ""

    def _task_wave_scope(self, task: Task) -> tuple[str, str]:
        contract = task.contract
        refs = {}
        if contract and isinstance(contract.evidence_contract, dict):
            refs = contract.evidence_contract.get("source_refs") or {}
        task_map_ref = str(refs.get("task_map_ref") or "").strip()
        if task_map_ref:
            return ("task_map_ref", task_map_ref)
        feature_id = str(getattr(contract, "feature_id", "") or "").strip()
        if feature_id:
            return ("feature_id", feature_id)
        return ("global", "legacy")

    @staticmethod
    def _same_wave_scope(left: tuple[str, str], right: tuple[str, str]) -> bool:
        return bool(left[0] and left[1] and left == right)

    def _strict_contract_preflight_errors(
        self,
        task: Task,
        role: RoleConfig | None = None,
    ) -> list[str]:
        try:
            if not bool(self.config.verification.contract.required):
                return []
        except Exception:
            return []
        if role is not None and self._is_design_intake_dispatch(task, role):
            return []
        if role is not None and self._is_design_critique_dispatch(task, role):
            return []
        errors = validate_task_contract(
            task,
            config=self.config,
            project_root=self.project_root,
        )
        errors.extend(self._artifact_hash_preflight_errors(task))
        return errors

    def _is_design_intake_dispatch(self, task: Task, role: RoleConfig) -> bool:
        """Allow design-intake arch tasks before final implementation contract.

        Canonical design-first flow creates a feature plus first arch task with
        no implementation contract. The final deliverable contract is
        synthesized after arch+critic. Keep strict validation for writer/gate
        roles, but do not force the normal arch intake path through an
        expected task.contract.invalid self-heal loop.
        """
        if role.name != "arch":
            return False
        if "arch.proposal.done" not in (role.publishes or []):
            return False
        if task.status not in {"backlog", "in_progress"}:
            return False
        return True

    def _is_design_critique_dispatch(self, task: Task, role: RoleConfig) -> bool:
        """Allow critic to review arch proposals before backlog synthesis.

        Design-first flow intentionally keeps the implementation contract out of
        kanban until critic approves the arch proposal. That means the
        arch->critic handoff cannot require the final behavior/verification
        fields yet; those are synthesized later from the approved design.
        """
        if role.name != "critic":
            return False
        if "arch.proposal.done" not in (role.triggers or []):
            return False
        if task.status not in {"in_progress", "review"}:
            return False
        try:
            events = self.event_log.read_days(1)
        except Exception:
            return False
        for event in reversed(events):
            if event.task_id != task.id:
                continue
            if event.type == "task.contract.update":
                return False
            if event.type == "design.critique.done":
                return False
            if event.type == "arch.proposal.done":
                return True
        return False

    def _artifact_hash_preflight_errors(self, task: Task) -> list[str]:
        try:
            recovery = build_artifact_recovery_refs(
                self.state_dir,
                task,
                project_root=self.project_root,
            )
        except Exception as exc:
            return [f"{task.id}: artifact refs could not be verified: {exc}"]
        errors: list[str] = []
        for status in recovery.get("accepted_hash_status", []):
            if not isinstance(status, dict):
                continue
            value = str(status.get("status") or "").strip()
            if value not in {"missing", "mismatch"}:
                continue
            path = str(status.get("path") or "").strip()
            artifact_id = str(status.get("artifact_id") or "").strip()
            label = artifact_id or path or "artifact"
            reason = str(status.get("reason") or value).strip()
            errors.append(
                f"{task.id}: accepted artifact {label} hash verification failed "
                f"({value}: {reason})"
            )
        return errors

    def _contract_preflight_already_reported(
        self,
        task: Task,
        errors: list[str],
    ) -> bool:
        try:
            events = self.event_log.read_days(1)
        except Exception:
            return False
        latest_invalid: tuple[int, list[str]] | None = None
        latest_update_idx = -1
        for idx, event in enumerate(events):
            if event.task_id != task.id:
                continue
            if event.type == "task.contract.update":
                latest_update_idx = idx
                continue
            if event.type != "task.contract.invalid":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if payload.get("source") != "dispatch_preflight":
                continue
            raw_errors = payload.get("errors")
            if not isinstance(raw_errors, list):
                continue
            latest_invalid = (idx, [str(item) for item in raw_errors])
        if latest_invalid is None:
            return False
        invalid_idx, invalid_errors = latest_invalid
        if latest_update_idx > invalid_idx:
            return False
        return invalid_errors == [str(item) for item in errors]

    def _dispatch_globally_paused(self) -> bool:
        """Return true when the latest pause intent is not resumed yet."""
        try:
            return is_dispatch_paused(
                self.state_dir,
                events=self.event_log.read_all(),
            )
        except Exception:
            return False

