"""Native, client-controlled detail panels for Feishu Agent replies."""

from __future__ import annotations

from typing import Any


def reply_details(title: str, elements: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap read-only content without changing its text or adding callbacks.

    Supported in both card JSON versions; children retain their native schema.
    Status, questions and action buttons stay outside the panel at call sites.
    """
    return {
        "tag": "collapsible_panel",
        "expanded": False,
        "header": {
            "title": {"tag": "plain_text", "content": f"{title}（展开 / 收起）"},
            "icon": {
                "tag": "standard_icon",
                "token": "down-small-ccm_outlined",
                "size": "16px 16px",
            },
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "elements": elements,
    }
