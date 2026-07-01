"""LH-4: error taxonomy + circuit breaker + retry policy."""

from zf.core.errors.taxonomy import (
    FailureCategory, RetryPolicy, classify, policy_for,
)
from zf.core.errors.circuit_breaker import CircuitBreaker, CircuitState
from zf.core.errors.retry import exponential_backoff

__all__ = [
    "FailureCategory", "RetryPolicy", "classify", "policy_for",
    "CircuitBreaker", "CircuitState", "exponential_backoff",
]
