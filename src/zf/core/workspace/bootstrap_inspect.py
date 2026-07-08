"""Bootstrap inspection: detect a project → structured setup/gate/doc candidates.

orca's setup-script-imports probes a project and produces reviewable
candidates; ZaoFu's equivalent assembles the existing deterministic detectors
(`profile.detect` for stack/gates, `suggest_setup_script` for env setup) into
one candidate payload for the Welcome wizard STEP 3. The core value over the
bare New Project modal is the **gate candidates** — filling the historically
empty ``required_checks`` (presets.py silent-empty-gate) from detected
test/lint/typecheck commands, so a fresh project ships with real gates.

Deterministic and read-only: it proposes, the operator confirms (candidate
review), the caller writes. No project semantics hardcoded — everything comes
from file signatures via the profile detector.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.profile.detector import detect
from zf.core.workspace.setup_suggestion import suggest_setup_script

SCHEMA_VERSION = "bootstrap-inspect.v1"


def inspect_project(project_root: str | Path, *, backend: str = "claude") -> dict[str, Any]:
    """Return structured bootstrap candidates for a target project root.

    ``recommended_flow`` is the validated prod controller flow the New Project
    creation should materialize (examples/prod/controller/*.yaml), NOT a generic
    preset — that is the config the operator actually wants.
    """
    root = Path(project_root).expanduser()
    if not root.exists() or not root.is_dir():
        return {"schema_version": SCHEMA_VERSION, "root": str(root), "confidence": "low",
                "candidates": [], "recommended_flow": "", "error": "path not found"}

    profile = detect(root)
    candidates: list[dict[str, Any]] = []

    recommended_flow = ""
    try:
        from zf.core.profile.flows import is_flow_id
        from zf.core.profile.recommender import recommend

        rec = recommend(profile, "build", backend=backend)
        if is_flow_id(rec.archetype):
            recommended_flow = rec.archetype
            candidates.append({
                "kind": "flow", "label": "controller flow", "value": recommended_flow,
                "note": "examples/prod/controller 下 e2e 验证过的生产 flow,创建时直接复制(非通用 preset)",
            })
    except Exception:
        recommended_flow = ""

    setup = suggest_setup_script(root)
    if setup:
        candidates.append({
            "kind": "setup", "label": "scripts.setup", "value": setup,
            "note": "worktree 铸造时自动执行,使 worker 新 worktree 开箱可运行",
        })

    gates = list(profile.all_gate_cmds)
    if gates:
        candidates.append({
            "kind": "gate", "label": "gates.static.required_checks", "values": gates,
            "note": "补掉空门禁(此前 required_checks=[] 永远 trivially pass)",
        })

    stack = profile.languages[0] if profile.languages else ""
    test_cmd = next((u.test_cmd for u in profile.units if u.test_cmd), "")
    facts = {"stack": stack, "layout": profile.layout, "test_cmd": test_cmd}
    if stack:
        candidates.append({
            "kind": "doc_fact", "label": "指令文档事实",
            "facts": {k: v for k, v in facts.items() if v},
            "note": "写进 CLAUDE.md/AGENTS.md,让 agent 读到项目真相而非模板",
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "confidence": profile.confidence,
        "stack": stack,
        "layout": profile.layout,
        "recommended_flow": recommended_flow,
        "candidates": candidates,
    }
