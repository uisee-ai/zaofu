"""ZfEvent — canonical event record."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone


def _new_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ZfEvent:
    type: str
    id: str = field(default_factory=_new_id)
    ts: str = field(default_factory=_now_iso)
    actor: str | None = None
    task_id: str | None = None
    payload: dict = field(default_factory=dict)
    causation_id: str | None = None
    correlation_id: str | None = None
    # 1405(origin 分类法):谁发的=证明什么 —— kernel(机器铸造/镜像)、
    # worker(agent 自报,zf emit)、external(集成侧)。空 = 历史事件,
    # 消费侧按类型 allowlist 兜底(0325 语义保留)。EventWriter 按调用方
    # 自动标注,不信任 payload 自报。
    origin: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> ZfEvent:
        return cls.from_dict(json.loads(line))

    @classmethod
    def from_dict(cls, data: dict) -> ZfEvent:
        # 1405:未知键过滤 —— 新旧版本互读不炸(向后/向前兼容,
        # append-only 流的读侧必须宽进)。
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
