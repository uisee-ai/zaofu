"""Deterministic capability-to-test-domain planning."""

from __future__ import annotations

from typing import Any, Mapping

from .manifest import ManifestError, ProductManifest

CORE_DOMAINS = ("P-01", "P-02", "P-03", "P-04", "P-05", "P-17", "P-18")
CAPABILITY_DOMAINS = {
    "cli": ("P-06",), "api": ("P-07", "P-11"), "web": ("P-08", "P-11", "P-16"),
    "database": ("P-09", "P-12", "P-13"), "external_service": ("P-10", "P-12"),
    "long_running": ("P-12", "P-13", "P-16"), "release": ("P-14", "P-15"),
}
# Project-shape labels are accepted as metadata, but do not select a domain.
# Executable capability switches remain the keys in ``CAPABILITY_DOMAINS``.
KNOWN_CAPABILITIES = frozenset(CAPABILITY_DOMAINS) | {
    "backend", "frontend", "library", "worker", "fullstack", "other",
}
ALL_DOMAINS = set(CORE_DOMAINS) | {domain for domains in CAPABILITY_DOMAINS.values() for domain in domains}


def select_test_domains(manifest: ProductManifest) -> tuple[dict[str, Any], ...]:
    """Return an auditable plan; every omitted capability needs a reason."""

    capabilities = manifest.capabilities
    unknown = sorted(set(capabilities) - KNOWN_CAPABILITIES)
    if unknown:
        raise ManifestError("unknown product capabilities: " + ", ".join(unknown))
    not_applicable = manifest.raw.get("not_applicable", {})
    selected = set(CORE_DOMAINS)
    rows: list[dict[str, Any]] = []
    for capability, domains in CAPABILITY_DOMAINS.items():
        enabled = capabilities.get(capability, False)
        if enabled is True:
            selected.update(domains)
            status = "selected"
        elif enabled is False and capability in not_applicable:
            status = "not_applicable"
        elif enabled is False:
            status = "not_applicable"
            # The manifest validator guarantees reasons for declared entries;
            # absent capability declarations are not evidence of applicability.
        else:
            raise ManifestError(f"capabilities[{capability}] must be boolean")
        rows.append({"capability": capability, "domains": list(domains), "status": status})
    for domain in CORE_DOMAINS:
        rows.append({"capability": "core", "domains": [domain], "status": "selected"})
    return tuple(sorted(rows, key=lambda row: (row["capability"], row["domains"])))


def commands_for_domains(manifest: ProductManifest, domains: set[str]) -> tuple[Mapping[str, Any], ...]:
    """Select explicitly mapped commands; never infer coverage from success."""
    selected: list[Mapping[str, Any]] = []
    for command in manifest.test_units:
        declared = command.get("domain_ids")
        if declared is None:
            continue
        if not isinstance(declared, list) or any(item not in ALL_DOMAINS for item in declared):
            raise ManifestError(f"command {command.get('id')} has invalid domain_ids")
        if domains.intersection(str(item) for item in declared):
            selected.append(command)
    return tuple(selected)


def require_domain_coverage(manifest: ProductManifest, domains: set[str]) -> None:
    """Fail closed when an applicable domain has no explicitly mapped command."""
    covered = {
        str(domain)
        for command in manifest.test_units
        for domain in (command.get("domain_ids") or [])
    }
    missing = sorted(domains - covered)
    if missing:
        raise ManifestError("test domains lack mapped commands: " + ", ".join(missing))


def require_capability_decisions(manifest: ProductManifest) -> None:
    """Require an auditable not_applicable record for explicit false capabilities."""
    capabilities = manifest.capabilities
    not_applicable = manifest.raw.get("not_applicable", {})
    missing = sorted(
        capability for capability, enabled in capabilities.items()
        if enabled is False and capability in CAPABILITY_DOMAINS and capability not in not_applicable
    )
    if missing:
        raise ManifestError("false capabilities lack not_applicable decisions: " + ", ".join(missing))

