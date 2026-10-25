"""The Web launcher preserves proxy access across a stale tmux environment."""
import json
import os
from pathlib import Path
import subprocess


def test_launcher_forwards_proxy_without_shell_expansion(tmp_path):
    root = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "checkout"
    (checkout / "tools").mkdir(parents=True)
    script = checkout / "tools/start-webkanban.sh"
    script.write_text((root / "tools/start-webkanban.sh").read_text())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # No real tmux, provider, port probing or interactive shell configuration.
    for name, body in {
        "bash": "exit 0",
        "tmux": 'if [ "$1" = has-session ]; then exit 1; fi; printf "%s\\n" "$@" > "$LAUNCH_ARGS"',
        "ss": "exit 0",
        "curl": "exit 0",
    }.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    marker = tmp_path / "should-not-exist"
    proxy = f"http://test:p'$(touch {marker})@127.0.0.1:3128"
    workspace = tmp_path / "workspace"
    env = {"HOME": os.environ["HOME"], "PATH": str(bin_dir) + ":" + os.environ["PATH"],
           "https_proxy": proxy, "HTTPS_PROXY": "http://127.0.0.1:3129", "NO_PROXY": "",
           "LAUNCH_ARGS": str(tmp_path / "args"), "ZF_WEB_ACTION_TOKEN": "test-only"}
    result = subprocess.run(["/bin/bash", str(script), "--no-build", "--workspace-home", str(workspace),
                             "--port", "8002"], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    runtime_env = workspace / "webkanban-8002.env"
    assert runtime_env.stat().st_mode & 0o777 == 0o600
    assert (workspace / "webkanban-8002.log").stat().st_mode & 0o777 == 0o600
    assert str(runtime_env) in (tmp_path / "args").read_text()
    command = '. "$1"; python3 -c \'import os,json; print(json.dumps({k:os.environ.get(k) for k in ["https_proxy","HTTPS_PROXY","NO_PROXY","ZF_WEB_ACTION_TOKEN"]}))\''
    values = json.loads(subprocess.check_output(["/bin/bash", "-c", command, "_", str(runtime_env)],
                                               env={"PATH": os.environ["PATH"]}, text=True))
    assert values == {"https_proxy": proxy, "HTTPS_PROXY": "http://127.0.0.1:3129", "NO_PROXY": "", "ZF_WEB_ACTION_TOKEN": "test-only"}
    assert not marker.exists()
    assert proxy not in result.stdout
    assert "write_url: http://0.0.0.0:8002/#zf-action-token=test-only" in result.stdout
