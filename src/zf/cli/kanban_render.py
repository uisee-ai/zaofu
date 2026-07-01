"""Terminal kanban board renderer with ANSI colors."""

from __future__ import annotations

import shutil

from zf.core.task.kanban_projection import kanban_column_projection
from zf.core.task.schema import Task

# ANSI color codes
_COLORS = {
    "backlog": "\033[37m",     # white/gray
    "in_progress": "\033[33m", # yellow
    "review": "\033[36m",      # cyan
    "testing": "\033[35m",     # magenta
    "done": "\033[32m",        # green
    "cancelled": "\033[31m",   # red
    "blocked": "\033[91m",     # bright red
}
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_COLUMNS = ["backlog", "in_progress", "testing", "blocked", "done"]


def render_board(tasks: list[Task], *, use_color: bool = True) -> str:
    """Render tasks as a terminal kanban board."""
    if not tasks:
        return "(empty board)"

    term_width = shutil.get_terminal_size((120, 40)).columns
    col_width = max(20, min(30, term_width // len(_COLUMNS) - 2))

    by_status: dict[str, list[Task]] = {col: [] for col in _COLUMNS}
    for task in tasks:
        by_status[_display_column(task)].append(task)

    lines: list[str] = []

    lines.append(
        "Active Board (runtime kanban; delivery history lives in `zf trace delivery`)"
    )
    lines.append("")

    # Header
    header_parts = []
    for col in _COLUMNS:
        count = len(by_status[col])
        label = f" {col} ({count}) "
        if use_color:
            label = f"{_BOLD}{_COLORS.get(col, '')}{label}{_RESET}"
        header_parts.append(label.center(col_width))
    lines.append("│".join(header_parts))
    lines.append("─" * (col_width * len(_COLUMNS) + len(_COLUMNS) - 1))

    # Find max rows needed
    max_rows = max(len(by_status[col]) for col in _COLUMNS) if tasks else 0

    # Render rows
    for i in range(max_rows):
        row_parts = []
        for col in _COLUMNS:
            col_tasks = by_status[col]
            if i < len(col_tasks):
                task = col_tasks[i]
                card = _render_card(task, col_width - 2, use_color, column=col)
                row_parts.append(f" {card} ")
            else:
                row_parts.append(" " * col_width)
        lines.append("│".join(row_parts))

    # WIP summary
    lines.append("")
    wip_parts = []
    for col in _COLUMNS:
        count = len(by_status[col])
        wip_parts.append(f"{col}:{count}")
    lines.append(f"WIP: {' | '.join(wip_parts)}")

    return "\n".join(lines)


def _display_column(task: Task) -> str:
    projection = kanban_column_projection(task)
    if projection.column == "ready":
        return "backlog"
    if projection.column in {"in_progress", "testing", "blocked", "done"}:
        return projection.column
    return "backlog"


def _render_card(task: Task, width: int, use_color: bool, *, column: str) -> str:
    """Render a single task card."""
    title = task.title[:width - 8]  # leave room for ID
    short_id = task.id[-6:] if len(task.id) > 6 else task.id
    assigned = f"@{task.assigned_to}" if task.assigned_to else ""
    projection = kanban_column_projection(task)
    badges = "".join(
        f"[{badge}]" for badge in projection.badges if badge != "not ready"
    )

    card = f"{short_id}"
    if badges:
        card += f" {badges}"
    card += f" {title}"
    if assigned:
        card += f" {assigned}"

    card = card[:width]

    if use_color:
        color = _COLORS.get(column, "")
        card = f"{color}{card}{_RESET}"

    return card.ljust(width)
