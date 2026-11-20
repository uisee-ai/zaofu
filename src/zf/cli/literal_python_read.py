"""Recognize a small, side-effect-free subset of literal Python read queries."""
from __future__ import annotations

import ast
import re


_NODES = (
    ast.Module, ast.Import, ast.ImportFrom, ast.alias, ast.Assign, ast.Name,
    ast.Load, ast.Store, ast.Constant, ast.Call, ast.Attribute, ast.keyword,
    ast.For, ast.If, ast.Expr, ast.Compare, ast.Eq, ast.NotEq, ast.Gt, ast.GtE,
    ast.Lt, ast.LtE, ast.BoolOp, ast.And, ast.Or, ast.Dict,
)
_METHODS = {"open", "read_text", "get", "loads", "dumps"}
_RESERVED = {"json", "Path", "print"}


def literal_python_read_query(command: str) -> bool:
    """Prove only this bounded syntax; unknown programs keep existing guards.

    A quoted delimiter prevents shell expansion. The AST excludes arbitrary
    imports, reflection, dynamic execution, callable rebinding and file writes.
    It is inspected, never executed, and grants no filesystem access itself.
    """
    lines = command.strip().splitlines()
    if len(lines) < 3:
        return False
    header = re.fullmatch(
        r"python(?:3)?[ \t]+-[ \t]+<<[ \t]*(['\"])([A-Za-z_][A-Za-z_0-9]*)\1[ \t]*",
        lines[0],
    )
    if not header or lines[-1] != header[2] or header[2] in lines[1:-1]:
        return False
    try:
        tree = ast.parse("\n".join(lines[1:-1]))
    except (SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            return False
        if isinstance(node, ast.Import) and any(
            alias.name != "json" or alias.asname for alias in node.names
        ):
            return False
        if isinstance(node, ast.ImportFrom) and (
            node.module != "pathlib" or node.level
            or any(alias.name != "Path" or alias.asname for alias in node.names)
        ):
            return False
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in _RESERVED:
            return False
        if isinstance(node, ast.Assign) and any(not isinstance(t, ast.Name) for t in node.targets):
            return False
        if isinstance(node, ast.Attribute) and (
            node.attr not in _METHODS or not isinstance(node.ctx, ast.Load)
        ):
            return False
        if not isinstance(node, ast.Call):
            continue
        if any(keyword.arg is None for keyword in node.keywords):
            return False
        if isinstance(node.func, ast.Name):
            if node.func.id not in {"Path", "print"}:
                return False
            if node.func.id == "print" and any(k.arg == "file" for k in node.keywords):
                return False
        elif isinstance(node.func, ast.Attribute):
            # No mode arguments: Path.open() defaults to a read-only handle.
            if node.func.attr == "open" and (node.args or node.keywords):
                return False
        else:
            return False
    return True
