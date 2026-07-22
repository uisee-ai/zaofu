"""Resolve the local source checkout recorded by a PEP 610 install."""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import unquote, urlparse


def installed_local_source_root(package_name: str = "zaofu") -> Path | None:
    """Return a local direct-url source root, if the install records one."""

    try:
        direct_url_text = distribution(package_name).read_text("direct_url.json")
    except (PackageNotFoundError, OSError):
        return None
    if not direct_url_text:
        return None
    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError:
        return None
    parsed = urlparse(str(direct_url.get("url") or ""))
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path)).resolve()


__all__ = ["installed_local_source_root"]
