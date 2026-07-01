"""zf.yaml three-axis recommender (doc 102 §6).

Maps (profile + operator intent) onto a *validated* archetype from the preset
catalog, a strictness profile, and the stack-derived required_checks. It only
ever *recommends from a finite catalog* — never synthesises a bespoke zf.yaml
(PB7). Pure function over data; no filesystem, no LLM.
"""

from __future__ import annotations

from zf.core.config.presets import get_preset, list_presets
from zf.core.profile.flows import flow_id_for_intent, flow_roles, is_flow_id
from zf.core.profile.schema import ProjectProfile, Recommendation

VALID_INTENTS = ("build", "refactor", "review", "maintain")
VALID_SCALES = ("hobby", "internal", "launch")
VALID_BACKENDS = ("claude", "codex")


def preset_roles(archetype: str) -> tuple[str, ...]:
    try:
        roles = get_preset(archetype).get("roles", [])
    except KeyError:
        return ()
    return tuple(r.get("name", "") for r in roles if isinstance(r, dict) and r.get("name"))


def recommend(
    profile: ProjectProfile,
    intent: str = "build",
    *,
    declared: bool = False,
    scale: str | None = None,
    backend: str = "claude",
) -> Recommendation:
    intent = intent if intent in VALID_INTENTS else "build"
    scale = scale if scale in VALID_SCALES else None
    backend = backend if backend in VALID_BACKENDS else "claude"
    presets = set(list_presets())

    has_tests = any(u.has_tests for u in profile.units)
    fullstack = profile.is_fullstack
    multi = len(profile.units) > 1 or profile.layout == "monorepo"
    low_signal = profile.confidence == "low"
    rationale: list[str] = []

    # --- axis ①: intent → validated prod flow, else lightweight preset -------
    # Catalog = examples/prod/ flows (PB7 validated). Long tail (hobby / empty /
    # tiny single-unit-no-tests) falls back to the minimal preset.
    lightweight = scale == "hobby" or low_signal or (
        not has_tests and not fullstack and not multi
    )
    flow_archetype = flow_id_for_intent(intent, backend)
    if lightweight or flow_archetype is None:
        archetype = "minimal"
        catalog_kind = "preset"
        why = "hobby" if scale == "hobby" else ("低信号" if low_signal else "小型/无测试")
        rationale.append(f"长尾({why})→ minimal preset 兜底(单 dev)")
    else:
        archetype = flow_archetype
        catalog_kind = "flow"
        rationale.append(
            f"intent={intent} → prod flow {archetype}"
            f"(validated,{flow_roles(archetype)} 角色,{backend} 后端)"
        )

    if catalog_kind == "preset" and archetype not in presets:  # PB7 safety
        archetype = "minimal"

    # --- axis ②: scale (human judgment) authoritative; else detect's guess ---
    if scale is not None:
        # survey overrides detect's strictness guess — incl. lowering a fullstack
        # auto-strict down to baseline for a hobby project (§6.1 / user 0623).
        harness_profile = "strict" if scale == "launch" else "baseline"
        rationale.append(f"scale={scale} → harness_profile={harness_profile}(人定,覆盖 detect 默认)")
    elif intent == "refactor" or (fullstack and multi):
        harness_profile = "strict"
        rationale.append("高复杂度 / 重构 → harness_profile=strict(detect 默认,可用 scale 覆盖)")
    else:
        harness_profile = "baseline"

    # --- axis ③: stack → required_checks overlay -----------------------------
    required_checks = profile.all_gate_cmds
    if required_checks:
        rationale.append(f"栈 overlay → required_checks={list(required_checks)}")

    # --- misroute detection (§6.6.1) -----------------------------------------
    misroute = ""
    if intent == "build" and not declared and _looks_existing(profile):
        misroute = (
            "声明 intent=build,但探到存量代码(已有 manifest/源码)。"
            "若是接手已有项目,考虑 --intent refactor 或 maintain。"
        )

    if catalog_kind == "flow":
        roles_tuple: tuple[str, ...] = ()
        role_count_val = flow_roles(archetype)
    else:
        roles_tuple = preset_roles(archetype)
        role_count_val = len(roles_tuple)
    return Recommendation(
        archetype=archetype,
        roles=roles_tuple,
        role_count=role_count_val,
        harness_profile=harness_profile,
        required_checks=required_checks,
        rationale=tuple(rationale),
        misroute=misroute,
        intent=intent,
        scale=scale or "",
        catalog=catalog_kind,
        backend=backend if catalog_kind == "flow" else "",
    )


def _looks_existing(profile: ProjectProfile) -> bool:
    return profile.confidence == "high" and any(
        u.language != "unknown" for u in profile.units
    )
