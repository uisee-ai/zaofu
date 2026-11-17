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
        raise ValueError("部署配置必须是普通 zf.yaml 文件")
    if path.exists():
        config = load_controller_workspace(path)
        if config.management != "observed" or config.hosting != "external":
            raise ValueError("已有托管配置，请使用 zf controller 生命周期")
        requested = {
            "endpoint": args.endpoint, "issuer": args.issuer,
            "workspace_id": args.workspace_id, "mode": args.model_mode,
            "projects": tuple(args.project),
        }
        if any(value and value != getattr(config, key) for key, value in requested.items()):
            raise ValueError("已有配置与参数冲突；不会修改安装目标或授权项目")
        print("复用已有 WorkspaceConfig；未修改配置或安装身份")
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
    print("已创建 observed WorkspaceConfig（尚未注册）")


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
        print("配置失败：检查 zf.yaml、HTTPS 地址、已批准 Workspace ID；已有配置不会被覆盖。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
