"""Installer adapter for the existing WorkspaceConfig, never a second config format."""

import argparse
import os
from pathlib import Path
import tempfile

import yaml

from zf.core.config.controller_workspace import load_controller_workspace


def configure(args) -> None:
    path = args.path.expanduser().absolute()
    if path.name != "zf.yaml" or path.is_symlink():
        raise ValueError("Deployment configuration must be a regular zf.yaml file")
    if path.exists():
        config = load_controller_workspace(path)
        if config.management != "observed" or config.hosting != "external":
            raise ValueError("Managed configuration exists; use the zf controller lifecycle")
        requested = {
            "endpoint": args.endpoint, "issuer": args.issuer,
            "workspace_id": args.workspace_id, "mode": args.model_mode,
            "projects": tuple(args.project),
        }
        if any(value and value != getattr(config, key) for key, value in requested.items()):
            raise ValueError("Options conflict with the existing configuration; installation identity and project scope are preserved")
        print("Reusing WorkspaceConfig; configuration and installation identity are unchanged")
        return
    raw = {
        "version": "1.0", "kind": "WorkspaceConfig",
        "workspace": {
            "name": "default", "state_dir": "state",
            "controller": {
                "management": "observed", "hosting": "external",
                "endpoint": args.endpoint, "issuer": args.issuer,
                "workspace_id": args.workspace_id,
                "projects": args.project, "mode": args.model_mode or "self_managed_cli",
            },
        },
    }
    # Validate with the production loader before publishing. link() never clobbers
    # a configuration another installer created concurrently.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=path.parent) as directory:
        candidate = Path(directory) / "zf.yaml"
        candidate.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        candidate.chmod(0o600)
        load_controller_workspace(candidate)
        os.link(candidate, path)
    print("Created observed WorkspaceConfig (enrollment pending)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    for name in ("endpoint", "issuer", "workspace-id", "model-mode"):
        parser.add_argument(f"--{name}", default="")
    parser.add_argument("--project", action="append", default=[])
    args = parser.parse_args()
    try:
        configure(args)
    except (ValueError, OSError, yaml.YAMLError):
        # URLs/invalid YAML can contain credentials: never render inputs or exceptions.
        print("Configuration failed: check zf.yaml, HTTPS URLs and the approved workspace ID. Existing configuration is preserved.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
