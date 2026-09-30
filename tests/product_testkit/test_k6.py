from pathlib import Path

import pytest

from .k6 import K6ContractError, build_k6_command


def test_k6_command_is_docker_isolated_and_read_only_product(tmp_path: Path):
    script = tmp_path / "load.js"
    script.write_text("export default function () {}\n", encoding="utf-8")
    command = build_k6_command(
        project_root=tmp_path, output_dir=tmp_path / "evidence", script="load.js",
        image="grafana/k6:1.5.0", timeout_seconds=30,
    )
    assert command[:5] == ["docker", "run", "--rm", "--network", "host"]
    assert "grafana/k6:1.5.0" in command
    assert "/work/load.js" in command
    assert "--summary-export" in command
    assert ":ro" in next(value for value in command if value.endswith(":ro"))


def test_k6_command_forwards_only_explicit_environment(tmp_path: Path):
    script = tmp_path / "load.js"
    script.write_text("export default function () {}\n", encoding="utf-8")
    command = build_k6_command(
        project_root=tmp_path, output_dir=tmp_path / "out", script="load.js",
        image="grafana/k6:1.5.0", timeout_seconds=30, env={"ZF_BASE_URL": "http://127.0.0.1:1"},
    )
    assert command[command.index("-e") + 1] == "ZF_BASE_URL=http://127.0.0.1:1"


@pytest.mark.parametrize("script", ["../load.js", "/tmp/load.js", ""])
def test_k6_command_rejects_path_escape(tmp_path: Path, script: str):
    (tmp_path / "load.js").write_text("", encoding="utf-8")
    with pytest.raises(K6ContractError):
        build_k6_command(
            project_root=tmp_path, output_dir=tmp_path / "out", script=script,
            image="grafana/k6:1.5.0", timeout_seconds=30,
        )
