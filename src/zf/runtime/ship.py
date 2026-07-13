"""Deterministic ship service for task and candidate refs."""

from __future__ import annotations

import fcntl
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.git_capture import git_env


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_TERMINAL_CANDIDATES = ("judge.passed", "verify.passed", "test.passed", "review.approved")


@dataclass(frozen=True)
class ShipResult:
    status: str
    ok: bool
    event_type: str
    payload: dict


class MainLockBusy(RuntimeError):
    pass


class MainLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def __enter__(self) -> "MainLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._fh.close()
            self._fh = None
            raise MainLockBusy("main lock is already held") from exc
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None


class ShipService:
    def __init__(
        self,
        *,
        state_dir: Path,
        project_root: Path,
        config: ZfConfig,
        event_log: EventLog,
    ) -> None:
        self.state_dir = state_dir
        self.project_root = project_root
        self.config = config
        self.event_log = event_log

    def ship(
        self,
        *,
        target_ref: str = "",
        pdd_id: str = "",
        task_id: str = "",
        event_writer: EventWriter | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ShipResult:
        target = self._resolve_target_ref(
            target_ref=target_ref,
            pdd_id=pdd_id,
            task_id=task_id,
        )
        payload_base = self._payload_base(target, pdd_id=pdd_id, task_id=task_id)
        lock_path = self.state_dir / "locks" / "main.lock"
        try:
            with MainLock(lock_path):
                self._emit(
                    event_writer,
                    "ship.lock_acquired",
                    payload_base,
                    causation_id=causation_id,
                    correlation_id=correlation_id,
                )
                try:
                    return self._ship_locked(
                        target,
                        payload_base=payload_base,
                        event_writer=event_writer,
                        causation_id=causation_id,
                        correlation_id=correlation_id,
                    )
                finally:
                    self._emit(
                        event_writer,
                        "ship.lock_released",
                        payload_base,
                        causation_id=causation_id,
                        correlation_id=correlation_id,
                    )
        except MainLockBusy as exc:
            return self._blocked(
                payload_base,
                [str(exc)],
                event_writer=event_writer,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )

    def _ship_locked(
        self,
        target_ref: str,
        *,
        payload_base: dict,
        event_writer: EventWriter | None,
        causation_id: str | None,
        correlation_id: str | None,
    ) -> ShipResult:
        try:
            blockers = self._blockers(target_ref, payload_base)
        except RuntimeError as exc:
            blockers = [str(exc)]
        if blockers:
            return self._blocked(
                payload_base,
                blockers,
                event_writer=event_writer,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )

        target_branch, target_resolved_from = self._resolve_ship_target_branch(
            target_ref,
            payload_base=payload_base,
            event_writer=event_writer,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        payload_base = {**payload_base, "target_resolved_from": target_resolved_from}
        original_head = self._git(self.project_root, "rev-parse", target_branch)
        self._emit(
            event_writer,
            "ship.started",
            {**payload_base, "target_branch": target_branch, "original_head": original_head},
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

        try:
            self._checkout_target_branch(target_branch)
            if target_ref.startswith(f"{self.config.runtime.git.candidate_branch_prefix}/"):
                self._merge_candidate(target_ref)
            else:
                self._cherry_pick_task(target_ref)
            gate_result = self._run_final_gate()
            if gate_result:
                self._reset_main(target_branch, original_head)
                return self._blocked(
                    {**payload_base, "target_branch": target_branch},
                    [gate_result],
                    event_writer=event_writer,
                    causation_id=causation_id,
                    correlation_id=correlation_id,
                )
            dirty = self._dirty_files()
            if dirty:
                self._reset_main(target_branch, original_head)
                return self._blocked(
                    {**payload_base, "target_branch": target_branch},
                    ["final gate left dirty files: " + ", ".join(dirty)],
                    event_writer=event_writer,
                    causation_id=causation_id,
                    correlation_id=correlation_id,
                )
            final_commit = self._git(self.project_root, "rev-parse", "HEAD")
            final_tag = self._final_tag(target_ref, payload_base)
            if final_tag:
                self._git(self.project_root, "tag", final_tag, final_commit)
            payload = {
                **payload_base,
                "target_branch": target_branch,
                "original_head": original_head,
                "final_commit": final_commit,
                "final_tag": final_tag,
                "completed_at": _now(),
            }
            self._emit(
                event_writer,
                "ship.completed",
                payload,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
            return ShipResult(
                status="completed",
                ok=True,
                event_type="ship.completed",
                payload=payload,
            )
        except RuntimeError as exc:
            conflict_files = self._conflict_files()
            self._abort_in_progress()
            self._reset_main(target_branch, original_head)
            payload = {
                **payload_base,
                "target_branch": target_branch,
                "original_head": original_head,
                "conflict_files": conflict_files,
                "error": str(exc),
                "completed_at": _now(),
            }
            self._emit(
                event_writer,
                "ship.conflict",
                payload,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
            return ShipResult(
                status="conflict",
                ok=False,
                event_type="ship.conflict",
                payload=payload,
            )

    def _blockers(self, target_ref: str, payload_base: dict) -> list[str]:
        blockers: list[str] = []
        dirty = self._dirty_files()
        if dirty:
            blockers.append("working tree is dirty: " + ", ".join(dirty))
        if not self._ref_exists(target_ref):
            blockers.append(f"target ref {target_ref!r} not found")
            return blockers
        if target_ref.startswith(f"{self.config.runtime.git.candidate_branch_prefix}/"):
            if not self._candidate_ready(target_ref, str(payload_base.get("pdd_id") or "")):
                blockers.append("candidate is not ready")
        elif target_ref.startswith(f"{self.config.runtime.git.task_ref_prefix}/"):
            task_id = str(payload_base.get("task_id") or target_ref.split("/", 1)[1])
            if not self._task_terminal(task_id):
                blockers.append("task terminal gate not satisfied")
        else:
            blockers.append(f"unsupported ship target: {target_ref}")
        final_tag = self._final_tag(target_ref, payload_base)
        if final_tag and self._ref_exists(f"refs/tags/{final_tag}"):
            blockers.append(f"final tag already exists: {final_tag}")
        return blockers

    def _candidate_ready(self, target_ref: str, pdd_id: str) -> bool:
        # B-NEW-14 (2026-05-17): the kernel's CandidateRebuilder writes
        # manifest.status="updated" (not "ready") and emits "candidate.updated"
        # + "candidate.integration.completed" (not "candidate.ready") on the
        # happy path (candidates.py:436-448). Nothing in src/zf/ emits
        # candidate.ready or writes status="ready", so the legacy checks
        # below are dead. Bridge by also accepting:
        #   - candidate.integration.completed (terminal success event)
        #   - manifest status="updated" AND quality_status=="passed"
        # while still respecting later candidate.conflict / candidate.quality.failed
        # as override. cangjie r-next-8 + r-next-9 ship.blocked on this gap.
        for event in reversed(self.event_log.read_all()):
            payload = event.payload if isinstance(event.payload, dict) else {}
            matches_target = (
                payload.get("branch") == target_ref
                or payload.get("candidate_ref") == target_ref
                or payload.get("pdd_id") == pdd_id
            )
            if not matches_target:
                continue
            if event.type in ("candidate.conflict", "candidate.quality.failed"):
                return False
            if event.type in (
                "candidate.ready",
                "candidate.integration.completed",
            ):
                return True
        manifest = self.state_dir / "candidates" / pdd_id / "manifest.json"
        try:
            import json

            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(data, dict):
            return False
        status = data.get("status")
        if status == "ready":
            return True
        if status == "updated" and data.get("quality_status") == "passed":
            return True
        return False

    def _task_terminal(self, task_id: str) -> bool:
        gate = self._terminal_gate()
        return any(
            event.type == gate and event.task_id == task_id
            for event in self.event_log.read_all()
        )

    def _terminal_gate(self) -> str:
        publishes = {
            event_type
            for role in self.config.roles
            for event_type in role.publishes
        }
        for candidate in _TERMINAL_CANDIDATES:
            if candidate in publishes:
                return candidate
        return "review.approved"

    def _resolve_target_ref(
        self,
        *,
        target_ref: str,
        pdd_id: str,
        task_id: str,
    ) -> str:
        target_ref = target_ref.strip()
        pdd_id = pdd_id.strip()
        task_id = task_id.strip()
        if target_ref:
            return target_ref
        if pdd_id:
            self._validate_id(pdd_id)
            return f"{self.config.runtime.git.candidate_branch_prefix}/{pdd_id}"
        if task_id:
            self._validate_id(task_id)
            return f"{self.config.runtime.git.task_ref_prefix}/{task_id}"
        return ""

    def _payload_base(self, target_ref: str, *, pdd_id: str, task_id: str) -> dict:
        if not target_ref:
            return {"target_ref": "", "pdd_id": pdd_id, "task_id": task_id}
        if target_ref.startswith(f"{self.config.runtime.git.candidate_branch_prefix}/"):
            pdd_id = pdd_id or target_ref.split("/", 1)[1]
            self._validate_id(pdd_id)
        if target_ref.startswith(f"{self.config.runtime.git.task_ref_prefix}/"):
            task_id = task_id or target_ref.split("/", 1)[1]
            self._validate_id(task_id)
        return {
            "target_ref": target_ref,
            "pdd_id": pdd_id,
            "task_id": task_id,
        }

    def _merge_candidate(self, target_ref: str) -> None:
        if self.config.runtime.git.ship_candidate_strategy != "merge":
            raise RuntimeError(
                "unsupported candidate ship strategy: "
                f"{self.config.runtime.git.ship_candidate_strategy}"
            )
        self._git(
            self.project_root,
            "merge",
            "--no-ff",
            "--no-edit",
            target_ref,
        )

    def _cherry_pick_task(self, target_ref: str) -> None:
        if self.config.runtime.git.ship_task_strategy != "cherry-pick":
            raise RuntimeError(
                "unsupported task ship strategy: "
                f"{self.config.runtime.git.ship_task_strategy}"
            )
        self._git(self.project_root, "cherry-pick", target_ref)

    def _checkout_target_branch(self, target_branch: str) -> None:
        self._git(self.project_root, "checkout", target_branch)

    def _reset_main(self, target_branch: str, original_head: str) -> None:
        try:
            self._git(self.project_root, "checkout", target_branch)
            self._git(self.project_root, "reset", "--hard", original_head)
            clean_args = ["clean", "-fd"]
            try:
                state_rel = self.state_dir.resolve().relative_to(
                    self.project_root.resolve(),
                )
            except (OSError, ValueError):
                state_rel = None
            if state_rel is not None and state_rel.parts:
                clean_args.extend(["-e", f"{state_rel.as_posix()}/"])
            self._git(self.project_root, *clean_args)
        except RuntimeError:
            pass

    def _run_final_gate(self) -> str:
        command = self.config.runtime.git.ship_final_command.strip()
        if not command:
            return ""
        result = subprocess.run(
            command,
            cwd=self.project_root,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return ""
        output = (result.stderr.strip() or result.stdout.strip())[:2000]
        return f"final gate failed ({result.returncode}): {output}"

    def _final_tag(self, target_ref: str, payload_base: dict) -> str:
        if target_ref.startswith(f"{self.config.runtime.git.candidate_branch_prefix}/"):
            pdd_id = str(payload_base.get("pdd_id") or "")
            return f"pdd/{pdd_id}-final" if pdd_id else ""
        if target_ref.startswith(f"{self.config.runtime.git.task_ref_prefix}/"):
            task_id = str(payload_base.get("task_id") or "")
            return f"task/{task_id}-final" if task_id else ""
        return ""

    def _dirty_files(self) -> list[str]:
        out = self._git(self.project_root, "status", "--porcelain")
        dirty: list[str] = []
        for line in out.splitlines():
            # B-NEW-13 (2026-05-17): untracked files (porcelain status "??")
            # never affect what `git merge candidate/<id>` produces — they
            # were never committed by anyone. Treating them as "dirty" forced
            # operators to manually clean long-lived local files
            # (autoresearch-seed.txt, .env.local, IDE state) before every
            # ship. cangjie r-next-8 + r-next-9 both ship.blocked on the
            # same untracked autoresearch-seed.txt. Skip "??" lines so only
            # tracked modifications (M/A/D/R/C) gate ship.
            status_code = line[:2]
            if status_code == "??":
                continue
            # _git() strips the whole porcelain output, so a leading unstaged
            # line (" M path") loses its blank X column and lands as "M path";
            # slice from col 2 (not the raw col-3 path offset) so the path
            # parses whole in both cases (else the first file loses its first
            # char: "README.md" -> "EADME.md").
            path = line[2:].strip()
            if " -> " in path:
                parts = [part.strip() for part in path.split(" -> ")]
            else:
                parts = [path]
            if all(part == ".zf" or part.startswith(".zf/") for part in parts):
                continue
            dirty.append(path)
        return dirty

    def _conflict_files(self) -> list[str]:
        try:
            out = self._git(
                self.project_root,
                "diff",
                "--name-only",
                "--diff-filter=U",
            )
        except RuntimeError:
            return []
        return [line for line in out.splitlines() if line.strip()]

    def _abort_in_progress(self) -> None:
        for args in (("merge", "--abort"), ("cherry-pick", "--abort")):
            subprocess.run(
                ["git", *args],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=False,
            )

    def _resolve_ship_target_branch(
        self,
        target_ref: str,
        *,
        payload_base: dict,
        event_writer: EventWriter | None,
        causation_id: str | None,
        correlation_id: str | None,
    ) -> tuple[str, str]:
        """Resolve the ship target branch, creating it when absent.

        ZF-E2E-PRDCTL-P0-2 (2026-07-12): the hardcoded default `main` failed
        `git rev-parse` in master-based repos and auto-ship died after
        judge.passed. Order: explicit config (create if missing) > scan
        main/master > create main.
        """
        configured = str(self.config.runtime.git.ship_target_branch or "").strip()
        if configured:
            if self._ref_exists(configured):
                return configured, "config"
            self._create_ship_target_branch(
                configured,
                target_ref,
                payload_base=payload_base,
                event_writer=event_writer,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
            return configured, "created"
        for candidate in ("main", "master"):
            if self._ref_exists(candidate):
                return candidate, "scan"
        self._create_ship_target_branch(
            "main",
            target_ref,
            payload_base=payload_base,
            event_writer=event_writer,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        return "main", "created"

    def _create_ship_target_branch(
        self,
        branch: str,
        target_ref: str,
        *,
        payload_base: dict,
        event_writer: EventWriter | None,
        causation_id: str | None,
        correlation_id: str | None,
    ) -> None:
        base = target_ref if self._ref_exists(target_ref) else "HEAD"
        self._git(self.project_root, "branch", branch, base)
        self._emit(
            event_writer,
            "ship.target.created",
            {**payload_base, "target_branch": branch, "created_from": base},
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def _ref_exists(self, ref: str) -> bool:
        if not ref:
            return False
        result = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _blocked(
        self,
        payload_base: dict,
        blockers: list[str],
        *,
        event_writer: EventWriter | None,
        causation_id: str | None,
        correlation_id: str | None,
    ) -> ShipResult:
        payload = {
            **payload_base,
            "blockers": blockers,
            "completed_at": _now(),
        }
        self._emit(
            event_writer,
            "ship.blocked",
            payload,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        return ShipResult(
            status="blocked",
            ok=False,
            event_type="ship.blocked",
            payload=payload,
        )

    @staticmethod
    def _emit(
        event_writer: EventWriter | None,
        event_type: str,
        payload: dict,
        *,
        causation_id: str | None,
        correlation_id: str | None,
    ) -> None:
        if event_writer is None:
            return
        event_writer.append(ZfEvent(
            type=event_type,
            actor="zf-cli",
            payload=payload,
            causation_id=causation_id,
            correlation_id=correlation_id,
        ))

    @staticmethod
    def _validate_id(value: str) -> None:
        if not value or not _SAFE_ID_RE.match(value):
            raise RuntimeError(f"invalid ship id: {value!r}")

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            env=git_env(),
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {detail}")
        return result.stdout.strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
