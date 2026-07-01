"""Verification for git handoff and candidate refs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from zf.core.config.schema import ZfConfig


@dataclass
class RefVerifyResult:
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


class RefVerifier:
    def __init__(
        self,
        *,
        state_dir: Path,
        project_root: Path,
        config: ZfConfig,
    ) -> None:
        self.state_dir = state_dir
        self.project_root = project_root
        self.config = config

    def verify(self) -> RefVerifyResult:
        issues: list[str] = []
        issues.extend(self._verify_task_refs())
        issues.extend(self._verify_candidate_manifests())
        return RefVerifyResult(issues=issues)

    def _verify_task_refs(self) -> list[str]:
        issues: list[str] = []
        index_path = self.state_dir / "refs" / "task-index.json"
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return issues
        except json.JSONDecodeError as exc:
            return [f"task-index.json: invalid json: {exc}"]
        if not isinstance(data, dict):
            return ["task-index.json: expected object"]
        for task_id, raw_entry in sorted(data.items()):
            if not isinstance(raw_entry, dict):
                issues.append(f"{task_id}: task-index entry is not an object")
                continue
            task_ref = str(raw_entry.get("task_ref") or "")
            source_commit = str(raw_entry.get("source_commit") or "")
            if not task_ref:
                issues.append(f"{task_id}: missing task_ref")
                continue
            if not source_commit:
                issues.append(f"{task_id}: missing source_commit")
                continue
            ref_commit = self._git("rev-parse", "--verify", f"refs/heads/{task_ref}^{{commit}}")
            if ref_commit is None:
                issues.append(f"{task_id}: missing task ref {task_ref}")
                continue
            commit = self._git("rev-parse", "--verify", f"{source_commit}^{{commit}}")
            if commit is None:
                issues.append(f"{task_id}: missing source commit {source_commit}")
                continue
            if ref_commit != commit:
                issues.append(
                    f"{task_id}: {task_ref} points to {ref_commit}, expected {commit}"
                )
        return issues

    def _verify_candidate_manifests(self) -> list[str]:
        issues: list[str] = []
        candidates_dir = self.state_dir / "candidates"
        if not candidates_dir.exists():
            return issues
        for manifest_path in sorted(candidates_dir.glob("*/manifest.json")):
            candidate_id = manifest_path.parent.name
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                issues.append(f"{candidate_id}: invalid candidate manifest: {exc}")
                continue
            if not isinstance(manifest, dict):
                issues.append(f"{candidate_id}: candidate manifest is not an object")
                continue
            branch = str(manifest.get("branch") or "")
            if not branch:
                issues.append(f"{candidate_id}: missing candidate branch")
            elif self._git("rev-parse", "--verify", f"refs/heads/{branch}") is None:
                issues.append(f"{candidate_id}: missing candidate ref {branch}")
            commit = str(manifest.get("commit") or "")
            if commit and branch:
                branch_commit = self._git(
                    "rev-parse",
                    "--verify",
                    f"refs/heads/{branch}^{{commit}}",
                )
                expected = self._git("rev-parse", "--verify", f"{commit}^{{commit}}")
                if branch_commit and expected and branch_commit != expected:
                    issues.append(
                        f"{candidate_id}: {branch} points to {branch_commit}, "
                        f"manifest records {expected}"
                    )
            included = manifest.get("included_tasks")
            if isinstance(included, list):
                for item in included:
                    if not isinstance(item, dict):
                        continue
                    task_ref = str(item.get("task_ref") or "")
                    task_id = str(item.get("task_id") or task_ref)
                    if task_ref and self._git(
                        "rev-parse",
                        "--verify",
                        f"refs/heads/{task_ref}",
                    ) is None:
                        issues.append(
                            f"{candidate_id}: included task {task_id} missing {task_ref}"
                        )
        return issues

    def _git(self, *args: str) -> str | None:
        result = subprocess.run(
            ["git", *args],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
