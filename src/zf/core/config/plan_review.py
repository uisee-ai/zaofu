"""Validate an explicitly selected PRD review policy; never classify business risk."""
from __future__ import annotations

from typing import Any, Mapping


def validate_plan_review(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"mode", "reason", "focus"}:
        raise ValueError("planReview accepts mode, reason and focus only")
    mode, reason, focus = value.get("mode"), value.get("reason"), value.get("focus", [])
    if not isinstance(mode, str) or mode not in {"none", "targeted", "full"}:
        raise ValueError("planReview.mode must be none, targeted or full")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("planReview requires a non-empty decision reason")
    if not isinstance(focus, list) or any(not isinstance(x, str) or not x.strip() for x in focus):
        raise ValueError("planReview.focus must be a list of non-empty review questions")
    if mode == "targeted" and not focus:
        raise ValueError("targeted planReview requires explicit focus")
    if mode == "none" and focus:
        raise ValueError("none planReview cannot claim a reviewed focus")
    return {"mode": mode, "reason": reason.strip(), "focus": list(dict.fromkeys(focus))}


def resolve_plan_review(params: Mapping[str, Any]) -> dict[str, Any]:
    aliases = [key for key in ("planReview", "plan_review") if key in params]
    switches = [key for key in ("planCriticEnabled", "plan_critic_enabled") if key in params]
    if len(aliases) > 1 or len(switches) > 1:
        raise ValueError("ambiguous plan review aliases")
    if switches and type(params[switches[0]]) is not bool:
        raise ValueError("PrdFlow.planCriticEnabled must be a boolean")
    if aliases:
        policy = validate_plan_review(params[aliases[0]])
        if switches and params[switches[0]] != (policy["mode"] != "none"):
            raise ValueError("planReview conflicts with planCriticEnabled")
        return policy
    enabled = params[switches[0]] if switches else True
    return {"mode": "full" if enabled else "none", "reason": "legacy explicit switch" if switches else "default independent review", "focus": []}
