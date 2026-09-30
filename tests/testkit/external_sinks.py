"""External side-effect spies for deterministic idempotency tests."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Generic, Mapping, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class EffectCall:
    """One attempted invocation of an externally observable operation."""

    operation_key: str
    executed: bool
    sequence: int
    arguments: tuple[tuple[str, str], ...] = ()


class ExternalEffectSpy(Generic[T]):
    """Record and deduplicate effects by the operation's stable key.

    The spy models the contract a controlled external sink should expose to a
    recovery test: a retried operation may be observed more than once, but the
    externally visible callback is executed at most once for a key.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._calls: list[EffectCall] = []
        self._results: dict[str, T] = {}

    def execute(
        self,
        operation_key: str,
        callback: Callable[[], T],
        *,
        arguments: Mapping[str, Any] | None = None,
    ) -> T:
        if not operation_key:
            raise ValueError("operation_key must not be empty")
        normalized_arguments = tuple(
            sorted((str(key), repr(value)) for key, value in (arguments or {}).items())
        )
        with self._lock:
            if operation_key in self._results:
                self._calls.append(
                    EffectCall(
                        operation_key,
                        executed=False,
                        sequence=len(self._calls),
                        arguments=normalized_arguments,
                    )
                )
                return self._results[operation_key]
            result = callback()
            self._results[operation_key] = result
            self._calls.append(
                EffectCall(
                    operation_key,
                    executed=True,
                    sequence=len(self._calls),
                    arguments=normalized_arguments,
                )
            )
            return result

    @property
    def calls(self) -> tuple[EffectCall, ...]:
        with self._lock:
            return tuple(self._calls)

    def execution_count(self, operation_key: str) -> int:
        return sum(
            call.executed
            for call in self.calls
            if call.operation_key == operation_key
        )

    def assert_at_most_once(self) -> None:
        duplicates = {
            key: self.execution_count(key)
            for key in {call.operation_key for call in self.calls}
            if self.execution_count(key) > 1
        }
        if duplicates:
            raise AssertionError(f"external effects repeated: {duplicates}")
