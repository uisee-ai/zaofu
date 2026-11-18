"""Explicit, finite authorization to publish scoped blocked-writer candidates."""
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath


@dataclass
class TaskPublicationConfig:
    enabled: bool = False
    approval_ref: str = ''
    paths: list[str] = field(default_factory=list)
    max_publications_per_run: int = 0
    max_actions_per_tick: int = 1

    def authorization_payload(self) -> dict:
        """Keep legacy single-action receipts valid; bind opt-in batching."""
        payload = asdict(self)
        if self.max_actions_per_tick == 1:
            payload.pop('max_actions_per_tick')
        return payload


def parse_task_publication(raw) -> TaskPublicationConfig:
    if raw is None:
        return TaskPublicationConfig()
    if not isinstance(raw, dict) or set(raw) - set(TaskPublicationConfig.__dataclass_fields__):
        raise ValueError('runtime.task_publication requires a known policy mapping')
    policy = TaskPublicationConfig(**raw)
    if type(policy.enabled) is not bool or not isinstance(policy.approval_ref, str):
        raise ValueError('task_publication requires boolean enabled and string approval_ref')
    if not isinstance(policy.paths, list) or any(
        not isinstance(p, str) or not p or PurePosixPath(p).is_absolute()
        or '..' in p.split('/') or any(c in p for c in '*?[]\\\x00\n')
        or str(PurePosixPath(p)) != p for p in policy.paths
    ):
        raise ValueError('task_publication paths must be exact relative file paths, not globs')
    if type(policy.max_publications_per_run) is not int or not 0 <= policy.max_publications_per_run <= 100:
        raise ValueError('task_publication budget must be 0..100')
    if type(policy.max_actions_per_tick) is not int or not 1 <= policy.max_actions_per_tick <= 100:
        raise ValueError('task_publication max_actions_per_tick must be 1..100')
    if policy.enabled and (not policy.approval_ref.strip() or not policy.paths or not policy.max_publications_per_run):
        raise ValueError('enabled task_publication requires approval_ref, paths and finite budget')
    return policy
