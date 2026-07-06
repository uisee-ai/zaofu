"""Project-semantics boundary: no new cangjie/hermes/cj-min tokens in kernel code.

Mechanizes the 2026-07-03 boundary review (tasks/active/2026-07-03-0829): runtime/
kernel keeps generic deterministic mechanisms; Cangjie/Hermes parity semantics,
scan strategy, and acceptance detail live in skills/prompt/artifacts/project
adapters. Comments and docstrings are exempt (incident provenance is an asset);
code-level hits are capped per file at today's reviewed-legit counts — legacy
read-side aliases, the hermes channel *backend* (a product integration, not
project semantics), and the deprecated `zf report hermes-run` CLI alias.

A new code-level token in an unlisted file, or a count increase in a listed
one, fails with the file/line so the author either moves the semantics out of
the kernel or (for a genuine new legacy-compat site) raises the cap in the
same PR with justification.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

PROJECT_TOKENS = ("cangjie", "hermes", "cj-min", "cj_min")
REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("src/zf/core", "src/zf/runtime", "src/zf/cli")

# path -> max allowed code-level (non-comment, non-docstring) hit lines.
# Reviewed 2026-07-03; every entry is legacy read-side compat, the hermes
# channel backend enum, or a deprecated CLI alias. Do not add entries for new
# behavior — new flows must use generic vocabulary.
_ALLOWED_CODE_HITS = {
    "src/zf/cli/bug_fix_cycle.py": 1,       # legacy cangjie_state_snapshot read
    "src/zf/cli/report.py": 10,             # deprecated `hermes-run` alias cmd
    "src/zf/core/config/render.py": 2,      # boundary lint baseline + message
    "src/zf/core/events/known_types.py": 2,  # legacy alias registration
    "src/zf/core/events/module_parity.py": 5,  # LEGACY_* alias constants
    "src/zf/runtime/artifact_matrix_gate.py": 2,  # legacy field aliases
    "src/zf/runtime/channel_adapter.py": 1,      # hermes backend enum
    "src/zf/runtime/channel_contracts.py": 3,    # hermes backend enum
    "src/zf/runtime/channel_projection.py": 1,   # hermes backend capabilities
    "src/zf/runtime/event_contracts.py": 1,   # legacy event prefix
    "src/zf/runtime/event_problem_registry.py": 2,  # legacy alias registry entry
    "src/zf/runtime/fanout_artifact_refs.py": 1,    # legacy ref alias map
    "src/zf/runtime/hermes_run_report.py": 4,  # compat wrapper module
    "src/zf/runtime/operator_intent.py": 1,    # hermes backend keyword
    "src/zf/runtime/orchestrator.py": 1,       # incident-provenance reason str
    "src/zf/runtime/refactor_artifacts.py": 1,  # legacy inventory-ref fallback
    "src/zf/runtime/shutdown.py": 1,           # incident-provenance reason str
    "src/zf/runtime/stage_actions.py": 1,      # incident-provenance reason str
    "src/zf/runtime/wake_patterns.py": 1,      # legacy alias wake entry
    "src/zf/runtime/writer_fanout_data.py": 1,  # legacy inventory-ref fallback
    "src/zf/runtime/zaofu_bug_signatures.py": 1,  # legacy snapshot alias prop
}


def _docstring_spans(tree: ast.AST) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                spans.append((body[0].lineno, body[0].end_lineno or body[0].lineno))
    return spans


def _code_level_token_lines(path: Path) -> list[int]:
    """Lines with a project token outside comments and docstrings."""
    text = path.read_text(encoding="utf-8")
    spans = _docstring_spans(ast.parse(text))

    def in_docstring(line: int) -> bool:
        return any(start <= line <= end for start, end in spans)

    flagged: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if not any(token in tok.string.lower() for token in PROJECT_TOKENS):
            continue
        if tok.type == tokenize.STRING and in_docstring(tok.start[0]):
            continue
        flagged.add(tok.start[0])
    return sorted(flagged)


def _scan_kernel_dirs() -> dict[str, list[int]]:
    hits: dict[str, list[int]] = {}
    for scan_dir in SCAN_DIRS:
        for path in sorted((REPO_ROOT / scan_dir).rglob("*.py")):
            lines = _code_level_token_lines(path)
            if lines:
                hits[path.relative_to(REPO_ROOT).as_posix()] = lines
    return hits


def test_no_new_project_tokens_in_kernel_code() -> None:
    hits = _scan_kernel_dirs()
    problems: list[str] = []
    for rel_path, lines in sorted(hits.items()):
        cap = _ALLOWED_CODE_HITS.get(rel_path)
        if cap is None:
            problems.append(
                f"{rel_path}: project token in kernel code at line(s) {lines} — "
                "move the semantics to skills/prompt/artifacts/project adapter, "
                "or (legacy-compat only) allowlist it here with justification"
            )
        elif len(lines) > cap:
            problems.append(
                f"{rel_path}: {len(lines)} code-level token lines (cap {cap}) "
                f"at {lines} — new hits must not land in kernel code"
            )
    assert not problems, "\n".join(problems)


def test_allowlist_caps_stay_tight() -> None:
    """A cap far above the actual count silently reopens headroom; shrink caps
    when hits are removed (same discipline as _OVERSIZED_FILE_CAPS)."""
    hits = _scan_kernel_dirs()
    stale = []
    for rel_path, cap in sorted(_ALLOWED_CODE_HITS.items()):
        actual = len(hits.get(rel_path, []))
        if actual < cap:
            stale.append(f"{rel_path}: cap {cap} but only {actual} hits — lower the cap")
    assert not stale, "\n".join(stale)


def test_scanner_classifies_comments_docstrings_and_code(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""Docstring mentioning cangjie is exempt."""\n'
        "# comment mentioning hermes is exempt\n"
        "def f():\n"
        '    """Function docstring: cj-min exempt."""\n'
        "    return 1\n"
        'EVENT = "cangjie.module.parity.scan.completed"  # code-level hit\n',
        encoding="utf-8",
    )
    assert _code_level_token_lines(sample) == [6]
