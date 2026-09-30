"""Atomic, rebuildable storage for the GitHub Issue mirror projection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from zf.core.issue_triage.models import IssueComment, IssueMirror, SyncState
from zf.core.issue_triage.lifecycle_states import reconcile_lifecycle_labels
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


class IssueMirrorStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.root = self.state_dir / "issue-triage"
        self.issues_path = self.root / "issues.json"
        self.sync_path = self.root / "sync.json"
        self.deliveries_path = self.root / "webhook-deliveries.json"
        self.refresh_lock_path = self.root / "refresh"

    def list(self) -> list[IssueMirror]:
        if not self.issues_path.exists():
            return []
        value = json.loads(self.issues_path.read_text(encoding="utf-8") or "[]")
        if not isinstance(value, list):
            raise ValueError("Issue mirror store must contain a list")
        return [IssueMirror.from_dict(dict(item)) for item in value if isinstance(item, dict)]

    def get(self, number: int) -> IssueMirror | None:
        return next((item for item in self.list() if item.number == number), None)

    def project_lifecycle_label(
        self,
        issue_key: str,
        desired_state: str,
        label_colors: dict[str, str] | None = None,
    ) -> IssueMirror | None:
        """Project a confirmed Forge lifecycle write into the local mirror.

        This deliberately patches only managed lifecycle labels.  It keeps
        the newest remote content and metadata already present in the mirror,
        so a concurrent GitHub refresh cannot make a successful action look
        stale merely because its ``updated_at`` won the normal upsert guard.
        """

        return self.project_lifecycle_labels({issue_key: (desired_state, label_colors or {})}).get(issue_key)

    def project_lifecycle_labels(
        self, updates: dict[str, tuple[str, dict[str, str]]],
    ) -> dict[str, IssueMirror]:
        """Publish confirmed names and colors for one batch in a single snapshot."""
        if not updates:
            return {}
        with locked_path(self.issues_path):
            rows = self.list()
            projected: dict[str, IssueMirror] = {}
            for current in rows:
                if current.issue_key not in updates:
                    continue
                desired, confirmed_colors = updates[current.issue_key]
                labels = reconcile_lifecycle_labels(current.labels, desired)
                colors = {name: color for name, color in current.label_colors.items() if name in labels}
                if desired in confirmed_colors:
                    colors[desired] = confirmed_colors[desired]
                item = replace(current, labels=labels, label_colors=colors)
                item.validate()
                projected[current.issue_key] = item
            next_rows = [projected.get(row.issue_key, row) for row in rows]
            if next_rows == rows:
                return projected
            atomic_write_text(
                self.issues_path,
                json.dumps(
                    [row.to_dict() for row in next_rows],
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
            )
            return projected

    def read_body(self, item: IssueMirror) -> str:
        path = (self.state_dir / item.body_ref).resolve()
        artifacts_root = (self.state_dir / "artifacts" / "issue-triage").resolve()
        if artifacts_root not in path.parents or not path.is_file():
            return ""
        body = path.read_text(encoding="utf-8")
        digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
        if digest != item.body_digest:
            raise ValueError("Issue body sidecar digest mismatch")
        return body

    def read_comments(self, item: IssueMirror) -> list[IssueComment]:
        if not item.comments_ref:
            return []
        path = (self.state_dir / item.comments_ref).resolve()
        artifacts_root = (self.state_dir / "artifacts" / "issue-triage").resolve()
        if artifacts_root not in path.parents or not path.is_file():
            return []
        raw = path.read_text(encoding="utf-8")
        digest = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if digest != item.comments_digest:
            raise ValueError("Issue comments sidecar digest mismatch")
        value = json.loads(raw or "[]")
        if not isinstance(value, list):
            raise ValueError("Issue comments sidecar must contain a list")
        return [IssueComment.from_dict(dict(row)) for row in value if isinstance(row, dict)]

    def upsert(self, item: IssueMirror, body: str) -> tuple[IssueMirror, bool]:
        if len(body.encode("utf-8")) > 1_000_000:
            raise ValueError("GitHub Issue body exceeds safe limit")
        body_digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
        body_ref = f"artifacts/issue-triage/github/{item.number}/body.md"
        current_item = replace(item, body_digest=body_digest, body_ref=body_ref)
        with locked_path(self.issues_path):
            rows = self.list()
            existing = next((row for row in rows if row.issue_key == item.issue_key), None)
            if existing is not None and not current_item.comments_ref:
                current_item = replace(
                    current_item,
                    comments_digest=existing.comments_digest,
                    comments_ref=existing.comments_ref,
                )
            current_item.validate()
            if existing is not None and existing.updated_at > current_item.updated_at:
                return existing, False
            existing_value = existing.to_dict() if existing is not None else {}
            current_value = current_item.to_dict()
            existing_value.pop("last_seen_at", None)
            current_value.pop("last_seen_at", None)
            if existing is not None and existing_value == current_value:
                return existing, False
            atomic_write_text(self.state_dir / body_ref, body)
            next_rows = [row for row in rows if row.issue_key != item.issue_key]
            next_rows.append(current_item)
            next_rows.sort(key=lambda row: (row.updated_at, row.number), reverse=True)
            atomic_write_text(
                self.issues_path,
                json.dumps(
                    [row.to_dict() for row in next_rows],
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
            )
        return current_item, True

    def write_comments(
        self,
        item: IssueMirror,
        comments: list[IssueComment],
    ) -> tuple[IssueMirror, bool]:
        raw = json.dumps(
            [comment.to_dict() for comment in comments],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        if len(raw.encode("utf-8")) > 5_000_000:
            raise ValueError("GitHub comments sidecar exceeds safe limit")
        digest = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        comments_ref = f"artifacts/issue-triage/github/{item.number}/comments.json"
        current_item = replace(
            item,
            comments_digest=digest,
            comments_ref=comments_ref,
        )
        current_item.validate()
        with locked_path(self.issues_path):
            rows = self.list()
            existing = next((row for row in rows if row.issue_key == item.issue_key), None)
            if existing is None:
                raise ValueError("Issue must be mirrored before its comments")
            if (
                existing.comments_digest == digest
                and existing.comments_ref == comments_ref
                and (self.state_dir / comments_ref).is_file()
                and (self.state_dir / comments_ref).read_text(encoding="utf-8") == raw
            ):
                return existing, False
            atomic_write_text(self.state_dir / comments_ref, raw)
            next_rows = [row for row in rows if row.issue_key != item.issue_key]
            next_rows.append(current_item)
            next_rows.sort(key=lambda row: (row.updated_at, row.number), reverse=True)
            atomic_write_text(
                self.issues_path,
                json.dumps(
                    [row.to_dict() for row in next_rows],
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
            )
        return current_item, True

    def sync_state(self) -> SyncState:
        if not self.sync_path.exists():
            return SyncState()
        value = json.loads(self.sync_path.read_text(encoding="utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("Issue sync state must contain an object")
        return SyncState.from_dict(value)

    def save_sync_state(self, state: SyncState) -> SyncState:
        with locked_path(self.sync_path):
            atomic_write_text(
                self.sync_path,
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
        return state

    def claim_webhook_delivery(self, delivery_id: str) -> bool:
        delivery_id = delivery_id.strip()
        if not delivery_id or len(delivery_id) > 200:
            raise ValueError("invalid GitHub webhook delivery id")
        with locked_path(self.deliveries_path):
            rows: list[str] = []
            if self.deliveries_path.exists():
                value = json.loads(self.deliveries_path.read_text(encoding="utf-8") or "[]")
                if not isinstance(value, list):
                    raise ValueError("webhook delivery store must contain a list")
                rows = [str(item) for item in value if isinstance(item, str)]
            if delivery_id in rows:
                return False
            rows.append(delivery_id)
            atomic_write_text(
                self.deliveries_path,
                json.dumps(rows[-1000:], ensure_ascii=False, indent=2) + "\n",
            )
        return True
