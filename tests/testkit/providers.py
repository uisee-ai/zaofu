"""Deterministic provider doubles for ZaoFu transport tests.

The double models provider facts only.  It never writes EventLog or task
state, which keeps the kernel's event/state ownership visible in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ProviderReply:
    status: str = "completed"
    output: str = ""
    exit_code: int = 0
    timed_out: bool = False


@dataclass(frozen=True)
class ProviderReceipt:
    call_index: int
    briefing: str
    status: str
    output: str
    exit_code: int
    timed_out: bool


class ScriptedMockProvider:
    """Return a predeclared sequence of provider facts, in order."""

    def __init__(self, replies: Iterable[ProviderReply]) -> None:
        self._replies = list(replies)
        self.receipts: list[ProviderReceipt] = []

    def invoke(self, briefing: str | Path) -> ProviderReceipt:
        if not self._replies:
            raise RuntimeError("scripted provider exhausted")
        reply = self._replies.pop(0)
        receipt = ProviderReceipt(
            call_index=len(self.receipts),
            briefing=str(briefing),
            status=reply.status,
            output=reply.output,
            exit_code=reply.exit_code,
            timed_out=reply.timed_out,
        )
        self.receipts.append(receipt)
        return receipt
