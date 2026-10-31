"""The approved operator action that establishes a project Git baseline."""
from __future__ import annotations

from zf.runtime.project_git_baseline import apply_git_baseline


class ProjectGitActionsMixin:
    def _project_git_baseline(self, *, requested, action, requested_action, payload):
        if self.project_root is None or self.config is None:
            return self._failed(requested=requested, action=action, requested_action=requested_action,
                                task_id=None, reason="initialized project required", status_code=409)
        try:
            result = apply_git_baseline(self.project_root, self.state_dir, payload["manifest"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return self._failed(requested=requested, action=action, requested_action=requested_action,
                                task_id=None, reason=str(exc), status_code=409, status="baseline_blocked")
        self._completed(requested=requested, event=requested, action=action,
                        requested_action=requested_action, task_id=None, status=result["status"], extra=result)
        return {"ok": True, "action": action, "requested_action": requested_action, **result}
