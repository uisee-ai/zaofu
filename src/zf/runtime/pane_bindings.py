"""Runtime tmux pane binding diagnostics and repair."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class PaneObservation:
    pane_id: str
    option_instance: str
    title: str
    cwd: str
    inferred_instance: str


class PaneBindingManager:
    """Inspect and repair pane-grid role bindings for a live tmux session."""

    def __init__(
        self,
        *,
        project_root: Path,
        state_dir: Path,
        config: Any,
        runner: Runner | None = None,
    ) -> None:
        self.project_root = project_root
        self.state_dir = state_dir
        self.config = config
        self.runner = runner or subprocess.run
        self.session_name = config.session.tmux_session
        self.window_name = "roles"
        self.binding_path = state_dir / "pane_bindings.json"

    def doctor(self) -> list[str]:
        if not self._enabled():
            return []
        expected = self._expected_instances()
        try:
            observations = self.inspect()
        except RuntimeError as exc:
            return [str(exc)]
        by_instance, issues = self._group_observations(observations, expected)
        existing = self._read_bindings()
        existing_roles = existing.get("roles")
        bindings = existing_roles if isinstance(existing_roles, dict) else {}

        for instance_id in expected:
            observation = by_instance.get(instance_id)
            if observation is None:
                issues.append(f"{instance_id}: missing live pane")
                continue
            if observation.option_instance != instance_id:
                issues.append(
                    f"{instance_id}: pane {observation.pane_id} "
                    f"@zf_instance_id={observation.option_instance!r}"
                )
            entry = bindings.get(instance_id)
            if not isinstance(entry, dict):
                issues.append(f"{instance_id}: pane binding missing")
                continue
            if str(entry.get("pane") or "") != observation.pane_id:
                issues.append(
                    f"{instance_id}: binding pane={entry.get('pane')!r} "
                    f"live={observation.pane_id!r}"
                )
            if str(entry.get("cwd") or "") != observation.cwd:
                issues.append(f"{instance_id}: binding cwd mismatch")
        return issues

    def repair(self) -> list[str]:
        if not self._enabled():
            return ["pane_grid disabled; no pane binding repair needed"]
        expected = self._expected_instances()
        observations = self.inspect()
        by_instance, issues = self._group_observations(observations, expected)
        missing = [instance_id for instance_id in expected if instance_id not in by_instance]
        if missing or issues:
            details = issues + [f"{instance_id}: missing live pane" for instance_id in missing]
            raise RuntimeError("; ".join(details))

        roles: dict[str, dict[str, str]] = {}
        actions: list[str] = []
        for instance_id in expected:
            observation = by_instance[instance_id]
            self._tmux(
                [
                    "tmux", "set-option", "-p",
                    "-t", observation.pane_id,
                    "@zf_instance_id", instance_id,
                ],
                check=True,
            )
            self._tmux(
                [
                    "tmux", "select-pane",
                    "-t", observation.pane_id,
                    "-T", instance_id,
                ],
                check=True,
            )
            roles[instance_id] = {
                "cwd": observation.cwd,
                "pane": observation.pane_id,
                "session": self.session_name,
                "window": self.window_name,
            }
            actions.append(f"{instance_id}: bound {observation.pane_id}")

        self._write_bindings(roles)
        actions.append(f"wrote {self.binding_path}")
        return actions

    def inspect(self) -> list[PaneObservation]:
        result = self._tmux(
            [
                "tmux", "list-panes",
                "-t", f"{self.session_name}:{self.window_name}",
                "-F",
                "#{pane_id}\t#{@zf_instance_id}\t#{pane_title}\t#{pane_current_path}",
            ],
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "tmux list-panes failed"
            raise RuntimeError(message)

        expected = set(self._expected_instances())
        observations: list[PaneObservation] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 3)
            while len(parts) < 4:
                parts.append("")
            pane_id, option_instance, title, cwd = parts
            inferred = self._infer_instance(
                expected=expected,
                option_instance=option_instance,
                title=title,
                cwd=cwd,
            )
            if inferred:
                observations.append(
                    PaneObservation(
                        pane_id=pane_id,
                        option_instance=option_instance,
                        title=title,
                        cwd=cwd,
                        inferred_instance=inferred,
                    )
                )
        return observations

    def _enabled(self) -> bool:
        return getattr(self.config.session, "tmux_layout", "") == "pane_grid"

    def _expected_instances(self) -> list[str]:
        instances: list[str] = []
        for role in getattr(self.config, "roles", []):
            if getattr(role, "transport", "tmux") != "tmux":
                continue
            instance_id = getattr(role, "instance_id", "") or getattr(role, "name", "")
            if instance_id:
                instances.append(instance_id)
        return instances

    def _infer_instance(
        self,
        *,
        expected: set[str],
        option_instance: str,
        title: str,
        cwd: str,
    ) -> str:
        if option_instance in expected:
            return option_instance
        if title in expected:
            return title
        match = re.search(r"/\.zf/workdirs/([^/]+)/project$", cwd)
        if match and match.group(1) in expected:
            return match.group(1)
        return ""

    def _group_observations(
        self,
        observations: list[PaneObservation],
        expected: list[str],
    ) -> tuple[dict[str, PaneObservation], list[str]]:
        by_instance: dict[str, PaneObservation] = {}
        issues: list[str] = []
        expected_set = set(expected)
        for observation in observations:
            instance_id = observation.inferred_instance
            if instance_id not in expected_set:
                continue
            if instance_id in by_instance:
                issues.append(
                    f"{instance_id}: duplicate panes "
                    f"{by_instance[instance_id].pane_id}, {observation.pane_id}"
                )
                continue
            by_instance[instance_id] = observation
        return by_instance, issues

    def _read_bindings(self) -> dict[str, object]:
        if not self.binding_path.exists():
            return {}
        try:
            data = json.loads(self.binding_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_bindings(self, roles: dict[str, dict[str, str]]) -> None:
        self.binding_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "roles": dict(sorted(roles.items())),
            "session": self.session_name,
            "window": self.window_name,
        }
        tmp = self.binding_path.with_suffix(self.binding_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.binding_path)

    def _tmux(
        self,
        args: list[str],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner(
            args,
            capture_output=True,
            text=True,
            timeout=10,
            check=check,
        )
