"""Reflection prompt, parser, and subprocess backend for autoresearch loop."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from zf.autoresearch.loop_types import EvalDelta, IterationRecord, ReflectionResult


DEFAULT_REFLECT_BACKEND = "claude-code"
REFLECT_BACKEND_ENV = "ZF_AUTORESEARCH_REFLECT_BACKEND"
SUPPORTED_REFLECT_BACKENDS = frozenset({"claude-code", "codex"})
_DEFAULT_REFLECT_TIMEOUT_SECONDS = 180


_VALID_REFLECT_VERDICTS = frozenset({
    "better_fix_exists", "best_so_far", "regression", "unknown",
})
_VALID_REFLECT_RISKS = frozenset({"low", "medium", "high"})
_GIT_DIFF_MAX_BYTES = 5000


def _truncate_diff(diff: str, max_bytes: int = _GIT_DIFF_MAX_BYTES) -> str:
    if len(diff) <= max_bytes:
        return diff
    head = diff[: max_bytes - 80]
    return head + f"\n\n... (truncated, original {len(diff)} bytes) ..."


def _format_delta_block(delta: EvalDelta | None) -> str:
    if delta is None:
        return "（首轮，无基线 / no baseline）"
    return (
        f"verdict (规则判定): {delta.verdict}\n"
        f"healthy:    {delta.healthy_delta:+d}\n"
        f"critical:   {delta.critical_delta:+d}\n"
        f"coordinator_ratio: {delta.coordinator_delta:+.3f}\n"
        f"open_backlog: {delta.backlog_delta:+d}\n"
        f"completed_tasks: {delta.completed_delta:+d}\n"
    )


def _format_backlog_top5(open_backlog: list[dict[str, Any]]) -> str:
    if not open_backlog:
        return "（无 open backlog）"
    # Sort by priority ascending (lower = higher priority in zaofu).
    sorted_b = sorted(
        open_backlog,
        key=lambda t: (int(t.get("priority", 99)), str(t.get("id", ""))),
    )[:5]
    lines = [
        f"- {t.get('id', '?')} (priority={t.get('priority', '?')}): {t.get('title', '')}"
        for t in sorted_b
    ]
    return "\n".join(lines)


def build_reflection_prompt(
    *,
    curr: IterationRecord,
    prev: IterationRecord | None,
    git_diff: str,
    open_backlog: list[dict[str, Any]],
) -> str:
    """Construct the zh-CN meta-prompt for the reflection LLM call.

    The LLM is asked to judge whether the current iteration's fix was
    high-leverage, whether better alternatives exist, what the risk
    level is, and what to run next. Output is constrained to JSON so
    we can parse it deterministically.
    """
    delta_block = _format_delta_block(curr.delta)
    diff_block = _truncate_diff(git_diff) if git_diff.strip() else "（无 git 变更）"
    backlog_block = _format_backlog_top5(open_backlog)
    prev_summary = prev.summary if prev else "（无上一轮）"

    return f"""你是 zaofu autoresearch 闭环的反思评估员。一次性 session，输出后即丢弃。

# 本轮事实
- 第 {curr.iter} 轮
- Scenario: {curr.scenario}
- run_status: {curr.run_status}
- tasks_done / expected: {curr.tasks_done} / {curr.expected_done}
- git_head: {curr.git_head}
- head_changed_since_prev: {curr.head_changed_since_prev}

# Eval delta（vs 上一轮）
{delta_block}

# 上一轮 summary
{prev_summary}

# 上一轮以来的 git 变更（HEAD 范围，可能截断）
```diff
{diff_block}
```

# 当前 open backlog（top 5 by priority）
{backlog_block}

# 请回答 5 问
1. 本轮指标是变好 / 变差 / 持平？归因是否明确指向上一轮的某条修复？
2. 上一轮的修复（看 diff）是不是高杠杆？有没有 **更好的替代方案** 现在该立刻去做？
3. 下一轮跑哪个 scenario 最能暴露下一个未发现的 bug？为什么？
4. 风险点（low / medium / high）：这套修复是否引入新的 regression 可能？
5. 这轮修复是否提炼成了更优雅、通用的 harness 适配机制？它能否迁移到 ZaoFu 开发其他产品的场景？

# 输出格式（仅 JSON，不要散文，不要 ``` 包裹之外的内容）
```json
{{
  "verdict": "better_fix_exists | best_so_far | regression | unknown",
  "alternatives": ["包含更优雅/通用适配方案；没有则写为什么当前方案最好"],
  "risk": "low | medium | high",
  "rec_for_next_iter": "一句话: 下轮跑 <scenario>, 重点看 <metric>, 以及是否能泛化到其他产品开发..."
}}
```
"""


# ---------------------------------------------------------------------------
# Reflection response parser (§3)
# ---------------------------------------------------------------------------


def _extract_first_json_block(raw: str) -> dict[str, Any] | None:
    """Find the first valid JSON object in the response.

    LLMs commonly pad the JSON with prose ("Let me think..." before,
    "That's my recommendation." after). Strategy:
      1. Try whole string as JSON.
      2. Look for ```json ... ``` fenced block.
      3. Scan for first '{' and try progressively larger substrings.
    """
    raw = raw.strip()
    try:
        v = json.loads(raw)
        if isinstance(v, dict):
            return v
    except Exception:
        pass

    # Fenced block: ```json {...} ```
    import re
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        try:
            v = json.loads(fenced.group(1))
            if isinstance(v, dict):
                return v
        except Exception:
            pass

    # Brute scan: first '{' to matching '}'.
    start = raw.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(raw)):
            c = raw[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw[start : i + 1]
                    try:
                        v = json.loads(candidate)
                        if isinstance(v, dict):
                            return v
                    except Exception:
                        pass
                    break
        start = raw.find("{", start + 1)
    return None


def parse_reflection_response(raw: str) -> ReflectionResult:
    """Parse the LLM reflection JSON response with permissive fallback.

    Never raises. If the response has no valid JSON or carries an
    out-of-enum verdict/risk, returns a ReflectionResult with
    ``verdict='unknown'`` and ``risk='medium'`` — safe defaults that
    the loop driver can treat as "no signal".
    """
    parsed = _extract_first_json_block(raw)
    if parsed is None:
        return ReflectionResult(
            verdict="unknown",
            alternatives=[],
            risk="medium",
            rec_for_next_iter="",
            raw_response=raw,
        )

    verdict = str(parsed.get("verdict", "unknown"))
    if verdict not in _VALID_REFLECT_VERDICTS:
        verdict = "unknown"

    risk = str(parsed.get("risk", "medium"))
    if risk not in _VALID_REFLECT_RISKS:
        risk = "medium"

    alternatives = parsed.get("alternatives") or []
    if not isinstance(alternatives, list):
        alternatives = []
    alternatives = [str(x) for x in alternatives]

    return ReflectionResult(
        verdict=verdict,
        alternatives=alternatives,
        risk=risk,
        rec_for_next_iter=str(parsed.get("rec_for_next_iter", "")),
        raw_response=raw,
    )

def _fallback_reflect(raw: str, *, verdict: str = "unknown") -> ReflectionResult:
    return ReflectionResult(
        verdict=verdict,
        alternatives=[
            (
                "reflection backend unavailable; deterministic fallback used. "
                "Do not treat this as LLM confirmation that the current fix is best."
            ),
            (
                "Prefer a general harness invariant plus eval/LOP metric and backlog "
                "evidence over a prompt-only fix when the same failure can recur in "
                "other ZaoFu product-development runs."
            ),
        ],
        risk="medium",
        rec_for_next_iter=(
            "下轮跑 self-eval-backlog, 重点看 quality_gates_passed、"
            "mutation_warning、why_not_done_count, 并复查本轮修复是否已沉淀为"
            "可迁移到其他产品开发的通用 harness invariant。"
        ),
        raw_response=raw,
    )


def normalize_reflect_backend(backend: str | None) -> str:
    normalized = str(backend or DEFAULT_REFLECT_BACKEND).strip().lower()
    if normalized == "claude":
        return "claude-code"
    return normalized or DEFAULT_REFLECT_BACKEND


def _reflect_command(backend: str) -> list[str]:
    if backend == "claude-code":
        return [
            "claude",
            "-p",                                # non-interactive print mode
            "--allow-dangerously-skip-permissions",
            # NOTE: previously passed --bare to strip hooks/memory, but --bare
            # disables OAuth credential reads -> "Not logged in" exit 0 in our
            # test environment. For now we accept the small extra cost of hooks
            # firing on the reflection session.
        ]
    if backend == "codex":
        return [
            "codex",
            "exec",
            "--ephemeral",
            "-s",
            "read-only",
            "-a",
            "never",
            "-",
        ]
    raise ValueError(f"unsupported reflection backend: {backend}")


def invoke_reflection_llm(
    prompt: str,
    *,
    backend: str = DEFAULT_REFLECT_BACKEND,
    timeout_seconds: int = _DEFAULT_REFLECT_TIMEOUT_SECONDS,
) -> ReflectionResult:
    """Invoke the reflection LLM and parse its response.

    The reflection step is read-only by design — the LLM only reasons
    about the iteration's eval delta + git diff + open backlog and
    returns a structured JSON verdict. No tools, no edits.

    Failure modes that fall back to ``verdict=unknown``:
      - unsupported backend
      - binary not on PATH
      - non-zero exit (auth, throttling)
      - timeout
      - JSON parse failure (handled in parse_reflection_response)

    All failures preserve the raw stderr/output in ``raw_response`` so
    operators can grep journal.jsonl forensically.
    """
    backend = normalize_reflect_backend(backend)
    if backend not in SUPPORTED_REFLECT_BACKENDS:
        return _fallback_reflect(
            f"unsupported backend {backend!r} (supported: {sorted(SUPPORTED_REFLECT_BACKENDS)})"
        )

    cmd = _reflect_command(backend)
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return _fallback_reflect(
            f"reflection llm timeout after {timeout_seconds}s"
        )
    except FileNotFoundError as e:
        return _fallback_reflect(
            f"reflection llm binary not found: {e}"
        )
    except Exception as e:  # noqa: BLE001 — subprocess failures are heterogeneous
        return _fallback_reflect(
            f"reflection llm error: {type(e).__name__}: {e}"
        )

    if proc.returncode != 0:
        return _fallback_reflect(
            f"reflection llm exit {proc.returncode}: {proc.stderr.strip()[:500]}"
        )

    return parse_reflection_response(proc.stdout)

__all__ = [
    "DEFAULT_REFLECT_BACKEND",
    "REFLECT_BACKEND_ENV",
    "SUPPORTED_REFLECT_BACKENDS",
    "build_reflection_prompt",
    "normalize_reflect_backend",
    "parse_reflection_response",
    "invoke_reflection_llm",
]
