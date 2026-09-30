"""Developer project binding, independent of report investigation sources."""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache, wraps
from pathlib import Path
import subprocess
import hashlib
from typing import Any

from zf.core.config.loader import load_config, ConfigError
from zf.core.config.project_context import ProjectContext
from zf.core.config.schema import ZfConfig
from zf.core.config.self_issue_policy import inherit_workspace_self_issue_config
from zf.core.events.factory import event_log_from_project
from zf.core.events.segments import build_event_manifest
from zf.core.self_issue.store import PublicationIntentStore
from zf.core.state.rotation import list_archives
from zf.core.state.locks import locked_path
from zf.core.workspace.project_resolver import ProjectResolver
from zf.core.workspace.registry import WorkspaceRegistry, stable_project_id, workspace_home
from zf.runtime.issue_settings import _read_raw


def configured_execution_root(project_root: Path, config: ZfConfig) -> Path:
    """Read only the local developer binding, also for long-lived pollers."""
    raw = config.self_issue.execution_project_root
    path = project_root / "zf.yaml"
    if path.is_file():
        _, settings = _read_raw(path)
        raw = settings.get("execution_project_root", raw)
    if not isinstance(raw, str):
        raise ValueError("self_issue.execution_project_root must be text")
    candidate = Path(raw.strip()).expanduser() if raw.strip() else project_root
    return (candidate if candidate.is_absolute() else project_root / candidate).resolve()


def execution_context(root: Path, workspace_config: ZfConfig) -> ProjectContext:
    root = root.resolve()
    path = root / "zf.yaml"
    if not root.is_dir() or not path.is_file():
        raise ValueError("执行项目目录必须存在，并包含 zf.yaml；请先初始化该项目。")
    config = inherit_workspace_self_issue_config(load_config(path), workspace_config)
    assert config is not None
    state = Path(config.project.state_dir)
    return ProjectContext(root, path, config, (state if state.is_absolute() else root / state).resolve())


def project_identity(root: Path, config: ZfConfig) -> dict[str, str]:
    registry = WorkspaceRegistry()
    row = next((row for row in registry.list_projects() if Path(row.root).resolve() == root.resolve()), None)
    return {"workflow_project_id": row.project_id if row else stable_project_id(name=config.project.name, root=root),
            "workflow_project_name": config.project.name, "execution_project_root": str(root.resolve())}


@lru_cache(maxsize=64)
def _admissions(state: str, manifest: str, config: ZfConfig | None = None) -> dict[str, tuple[str, str]]:
    """Small rebuildable index; unrelated runtime events are never decoded."""
    del manifest
    root = Path(state)
    log = event_log_from_project(root, config=config)
    paths = [*list_archives(log.path.parent / log.path.stem, suffix=".jsonl"), log.path]
    paths = [p for p in paths if p.is_file()]
    if not paths:
        return {}
    try:
        result = subprocess.run(["rg", "-F", "--no-filename", "external_issue.triage.queued", *map(str, paths)],
                                capture_output=True, text=True, timeout=10, check=False)
        if result.returncode not in {0, 1}:
            raise ValueError("Cannot read Issue admission ledger")
        lines = result.stdout.splitlines()
    except FileNotFoundError:
        lines = [line for path in paths for line in path.read_text().splitlines() if "external_issue.triage.queued" in line]
    rows = {}
    for line in lines:
        event = log.decode_line(line)
        if event and event.type == "external_issue.triage.queued":
            key = str(event.payload.get("source_key") or "")
            rank = (event.ts, event.id)
            if key and rank > rows.get(key, ("", "")):
                rows[key] = rank
    return rows


@lru_cache(maxsize=64)
def _publications(path: str, stamp: tuple[int, int]) -> tuple[Any, ...]:
    del stamp
    return tuple(PublicationIntentStore(Path(path)).list())


class IssueExecutionRoutes:
    """Resolve trusted local admissions/publications once per intake pass."""

    def __init__(self, service: Any):
        self.service = service
        current = ProjectContext(service.project_root, service.project_root / "zf.yaml", service.config, service.state_dir)
        self.contexts = [current]
        self.services: dict[str, Any] = {}
        seen = {service.state_dir.resolve()}
        resolver = ProjectResolver()
        for row in resolver.registry.list_projects():
            try:
                ctx = resolver.resolve(row.project_id).context
            except (OSError, ValueError, ConfigError):
                continue
            if ctx.state_dir in seen:
                continue
            seen.add(ctx.state_dir)
            target = ctx.config.self_issue.targets.get("github")
            if target and target.project.casefold() != service.repository.casefold():
                continue
            self.contexts.append(ctx)
        self.owners: dict[str, tuple[tuple[str, str], ProjectContext]] = {}
        for ctx in self.contexts:
            manifest = build_event_manifest(ctx.state_dir).digest
            # Signed ledgers are reverified against the current project key.
            rows = (_admissions.__wrapped__(str(ctx.state_dir), manifest, ctx.config)
                    if ctx.config.security.event_signing.enabled else _admissions(str(ctx.state_dir), manifest))
            for key, rank in rows.items():
                if key not in self.owners or rank > self.owners[key][0]:
                    self.owners[key] = (rank, ctx)

    def target(self, mirror: Any, *, automatic: bool) -> ProjectContext | None:
        # Running lifecycles stay pinned. An explicit Start after cancellation
        # is a new admission and may use the viewer's updated developer setting.
        if mirror.issue_key in self.owners:
            owner = self.owners[mirror.issue_key][1]
            if automatic:
                return owner
            from zf.runtime.external_issue_label_sync import _relevant_events
            from zf.runtime.issue_lifecycle_projection import _workflow_projection
            events = _relevant_events(event_log_from_project(owner.state_dir, config=owner.config), {mirror.issue_key})
            workflow = _workflow_projection(events, mirror, prefer_remote=False) or {}
            if workflow.get("state") not in {"triage_cancelled", "fix_cancelled"}:
                return owner
        origin = self.contexts[0]
        if automatic:
            matches = []
            pending_publication = False
            body = self.service.mirrors.read_body(mirror)
            for ctx in self.contexts:
                path = ctx.state_dir / "self-issues" / "publication-intents.json"
                if not path.is_file():
                    continue
                stat = path.stat()
                for intent in _publications(str(path), (stat.st_mtime_ns, stat.st_size)):
                    if (intent.status in {"publishing", "outcome_unknown"} and intent.marker
                        and intent.target_binding.get("provider") == mirror.provider
                        and intent.target_binding.get("project", "").casefold() == mirror.repository.casefold()
                        and f"<!-- zf-self-issue:{intent.marker} -->" in body):
                        pending_publication = True
                    ref = intent.published_issue_ref
                    if (ref.get("provider") == mirror.provider and
                        ref.get("project", "").casefold() == mirror.repository.casefold() and
                        str(ref.get("number")) == str(mirror.number)):
                        matches.append(ctx)
            roots = {ctx.project_root for ctx in matches}
            if len(roots) > 1:
                raise ValueError("Issue has conflicting local publication owners; select an execution project explicitly")
            if matches:
                origin = matches[0]
            elif pending_publication:
                # Publication may still be uploading evidence. A public marker
                # cannot authorize a local path; wait for the trusted receipt.
                return None
        root = configured_execution_root(origin.project_root, origin.config)
        if root == self.service.project_root:
            return self.contexts[0]
        return execution_context(root, self.service.config)

    def service_for(self, ctx: ProjectContext, mirror: Any) -> Any:
        from zf.runtime.external_issue_ingress import ExternalIssueIngressService
        if ctx.state_dir.resolve() == self.service.state_dir.resolve():
            return self.service
        # Reuse one owner service per pass; routing many Issues must not rescan
        # the complete owner's ledger separately for every mirrored row.
        key = str(ctx.state_dir.resolve())
        target = self.services.get(key)
        if target is None:
            config = inherit_workspace_self_issue_config(ctx.config, self.service.config)
            target = ExternalIssueIngressService(ctx.state_dir, config, project_root=ctx.project_root,
                lifecycle_label_writer=self.service.lifecycle_label_writer,
                clarification_comment_writer=self.service.clarification_comment_writer, now=self.service.now)
            target._execution_bound = True
            if target.repository.casefold() != self.service.repository.casefold():
                raise ValueError("执行项目的 Issue 发布仓库与当前仓库不一致。")
            self.services[key] = target
            if not any(existing.state_dir == ctx.state_dir for existing in self.contexts):
                WorkspaceRegistry().upsert_context(ctx)
        # Only copy the rebuildable mirror; canonical tasks/events are created by intake.
        body = self.service.mirrors.read_body(mirror)
        comments = self.service.mirrors.read_comments(mirror)
        with locked_path(target.mirrors.refresh_lock_path, timeout_seconds=0.1):
            existing = target.mirrors.get(mirror.number)
            if existing is None or existing.updated_at <= mirror.updated_at:
                copied, _ = target.mirrors.upsert(replace(mirror, comments_ref="", comments_digest=""), body)
                if mirror.comments_ref:
                    target.mirrors.write_comments(copied, comments)
        return target


def serialize_issue_admission(operation):
    """Serialize routing + admission across project processes sharing a forge."""
    @wraps(operation)
    def wrapped(service, *args, **kwargs):
        if service._execution_bound:
            return operation(service, *args, **kwargs)
        key = hashlib.sha256(service.repository.casefold().encode()).hexdigest()
        manual = operation.__name__ == "start_manual_triage"
        timeout = kwargs.get("lock_timeout_seconds", 60) if manual else 0.1
        try:
            with locked_path(workspace_home() / "locks" / f"issue-admission-{key}", timeout_seconds=timeout):
                return operation(service, *args, **kwargs)
        except (ValueError, ConfigError) as exc:
            if not manual:
                raise
            from zf.runtime.external_issue_ingress import ExternalIssueManualTriageResult
            return ExternalIssueManualTriageResult(ok=False, status="invalid_execution_project",
                issue_number=args[0] if args else kwargs["issue_number"], error=str(exc))
        except TimeoutError:
            from zf.runtime.external_issue_ingress import ExternalIssueManualTriageResult, ExternalIssuePollResult
            if manual:
                return ExternalIssueManualTriageResult(ok=False, status="syncing", issue_number=args[0] if args else kwargs["issue_number"],
                    error="Issue admission is already updating this repository")
            return ExternalIssuePollResult(ok=True, status="syncing", changed=kwargs.get("changed", 0))
    return wrapped
