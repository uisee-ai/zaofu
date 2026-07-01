"""ProjectProfile data model — deterministic stack-detection output (doc 102 §3).

Pure data, no side effects. ``detected_at`` is stamped by the caller (kernel-edge)
to keep the detector itself free of wall-clock reads (I3).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StackUnit:
    """One independently buildable/testable unit. A monorepo has several."""

    root: str = "."  # relative to project_root; "." = single repo root
    language: str = "unknown"  # python | node | go | rust | unknown
    frameworks: tuple[str, ...] = ()
    surface: str = "unknown"  # frontend | backend | fullstack | library | unknown
    build_cmd: str = ""
    test_cmd: str = ""
    gate_cmds: tuple[str, ...] = ()
    has_tests: bool = False  # real test presence (dir/files/script), not just a default cmd

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "language": self.language,
            "frameworks": list(self.frameworks),
            "surface": self.surface,
            "build_cmd": self.build_cmd,
            "test_cmd": self.test_cmd,
            "gate_cmds": list(self.gate_cmds),
            "has_tests": self.has_tests,
        }


@dataclass(frozen=True)
class ProjectProfile:
    """Deterministic detector output (``project-profile.v1``)."""

    units: tuple[StackUnit, ...] = ()
    layout: str = "single"  # single | monorepo
    confidence: str = "low"  # high | low | declared
    source_signals: tuple[str, ...] = ()
    detected_at: str = ""  # stamped by caller, not the detector
    schema: str = "project-profile.v1"

    @property
    def languages(self) -> tuple[str, ...]:
        seen: list[str] = []
        for u in self.units:
            if u.language != "unknown" and u.language not in seen:
                seen.append(u.language)
        return tuple(seen)

    @property
    def surfaces(self) -> tuple[str, ...]:
        seen: list[str] = []
        for u in self.units:
            if u.surface not in ("unknown", "") and u.surface not in seen:
                seen.append(u.surface)
        return tuple(seen)

    @property
    def is_fullstack(self) -> bool:
        s = set(self.surfaces)
        return "fullstack" in s or {"frontend", "backend"} <= s

    @property
    def all_gate_cmds(self) -> tuple[str, ...]:
        """Union of every unit's gate commands, order-preserving + deduped."""
        seen: list[str] = []
        for u in self.units:
            for cmd in u.gate_cmds:
                if cmd and cmd not in seen:
                    seen.append(cmd)
        return tuple(seen)

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "layout": self.layout,
            "confidence": self.confidence,
            "detected_at": self.detected_at,
            "source_signals": list(self.source_signals),
            "languages": list(self.languages),
            "surfaces": list(self.surfaces),
            "is_fullstack": self.is_fullstack,
            "gate_cmds": list(self.all_gate_cmds),
            "units": [u.to_dict() for u in self.units],
        }


@dataclass(frozen=True)
class Recommendation:
    """zf.yaml three-axis recommendation (doc 102 §6)."""

    archetype: str = "minimal"  # MUST be a member of list_presets()
    roles: tuple[str, ...] = ()  # named role roster of the archetype
    harness_profile: str = "baseline"  # baseline | strict | release
    required_checks: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()
    misroute: str = ""  # non-empty when declared intent contradicts signals
    intent: str = "build"
    scale: str = ""  # hobby | internal | launch (survey input, "" = detect default)
    catalog: str = "preset"  # "flow" (validated prod flow) | "preset" (lightweight)
    backend: str = ""  # claude | codex (for flow archetypes)
    role_count: int = 0

    def to_dict(self) -> dict:
        return {
            "archetype": self.archetype,
            "roles": list(self.roles),
            "role_count": self.role_count,
            "harness_profile": self.harness_profile,
            "required_checks": list(self.required_checks),
            "rationale": list(self.rationale),
            "misroute": self.misroute,
            "intent": self.intent,
            "scale": self.scale,
            "catalog": self.catalog,
            "backend": self.backend,
        }
