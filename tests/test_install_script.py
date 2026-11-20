"""Installer behavior with isolated host tools; no package/network/provider side effects."""

import os
from pathlib import Path
import pty
import shutil
import shlex
import subprocess
import sys

import pytest
import yaml

from zf.core.config.controller_workspace import load_controller_workspace


ROOT = Path(__file__).resolve().parents[1]


def executable(path, body):
    path.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
    path.chmod(0o755)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "source with spaces"
    for child in ("tools", ".venv/bin", "web/dist"):
        (root / child).mkdir(parents=True)
    for name in ("install.sh", "tools/install_config.py", "tools/install_herdr.py", "tools/remove_install.py"):
        shutil.copy(ROOT / name, root / name)
    (root / "web/dist/index.html").write_text("test")
    (root / "pyproject.toml").write_text('[project]\nname = "zaofu"\n')
    executable(root / ".venv/bin/python", f'exec {shlex.quote(sys.executable)} "$@"')
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = {"HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "PYTHONPATH": str(ROOT / "src"), "CALLS": str(tmp_path / "calls"),
           "STARTED": str(tmp_path / "started"), "ENROLLED": str(tmp_path / "enrolled")}
    for name in ("git", "tar", "node", "npm", "uv", "sleep"):
        executable(bin_dir / name, f'echo "{name} $*" >> "$CALLS"\n'
                   + ('exit "${SYNC_DEPS_FAIL:-0}"' if name == "uv" else "exit 0"))
    executable(bin_dir / "curl", 'exit "${UNHEALTHY:-0}"')
    executable(bin_dir / "herdr", '''
if [[ "$1" == --version ]]; then echo "herdr ${HERDR_VERSION:-0.9.5}";
elif [[ "$1" == api ]]; then echo '{}';
else exit "${HERDR_PROTOCOL_FAIL:-0}"; fi
''')
    executable(bin_dir / "tmux", '''
echo "tmux $*" >> "$CALLS"
if [[ "$1" == has-session ]]; then
  [[ "$3" == =zf-web-* && -e "$STARTED" ]] || [[ "$3" == =zf-controller-* && -e "$STARTED.connector" ]]
elif [[ "$1" == new-session ]]; then
  touch "$STARTED.connector"
elif [[ "$1" == list-sessions && -e "$STARTED" ]]; then
  printf '$1 zf-web-%s\n' "$(cat "$STARTED")"
  if [[ -e "$STARTED.connector" ]]; then printf '$2 zf-controller-%s\n' "$(cat "$STARTED")"; fi
elif [[ "$1" == set-option && "$3" == =* ]]; then
  exit 1  # set-option uses session IDs, not target-pane exact-name syntax.
fi
''')
    executable(root / "tools/start-webkanban.sh", '''
echo "launcher $*" >> "$CALLS"
echo 'write_url: http://127.0.0.1/#zf-action-token=private-test-token'
[[ "${LAUNCH_FAIL:-0}" == 0 ]] || exit 2
port=8001
while (($#)); do
  if [[ "$1" == --port ]]; then port="$2"; shift 2; else shift; fi
done
printf '%s' "$port" > "$STARTED"
''')
    executable(root / ".venv/bin/zf", '''
echo "zf $*" >> "$CALLS"
case "$2" in
  installation-key) echo '{"public_key":"test-public-key"}' ;;
  status)
    if [[ -e "$ENROLLED" ]]; then echo '{"status":"enrolled"}'; else echo '{"status":"unenrolled"}'; fi ;;
  enroll) touch "$ENROLLED"; echo '{"status":"enrolled"}' ;;
  sync) echo '{"status":"sync-result"}'; exit "${SYNC_FAIL:-0}" ;;
esac
''')
    return root, env


def run(checkout, *args, **extra):
    root, env = checkout
    return subprocess.run(["/bin/bash", str(root / "install.sh"), *args],
                          env={**env, **extra}, text=True, capture_output=True, timeout=10)


def calls(checkout):
    path = Path(checkout[1]["CALLS"])
    return path.read_text() if path.exists() else ""


def observed_args():
    return ("--mode", "observed", "--endpoint", "https://controller.example",
            "--issuer", "https://identity.example/realms/zaofu", "--workspace-id", "external-1",
            "--project", "project-exact-id", "--skip-deps")


def test_noninteractive_requires_intent_and_cloud_has_no_host_effects(checkout):
    assert run(checkout).returncode != 0
    result = run(checkout, "--mode", "cloud")
    assert result.returncode == 0 and "Nothing is installed" in result.stdout
    assert not calls(checkout)


def test_installer_interface_and_helpers_are_english():
    for name in ("install.sh", "tools/install_config.py", "tools/install_herdr.py", "tools/remove_install.py"):
        assert (ROOT / name).read_text().isascii(), name


def test_remove_command_deletes_runtime_without_installing_dependencies(checkout):
    root, env = checkout
    (root / "web/node_modules").mkdir()
    workspace = Path(env["HOME"]) / ".zaofu"
    workspace.mkdir()
    (workspace / "user-data").write_text("preserve")
    preview = run(checkout, "remove")
    assert preview.returncode != 0 and "--yes" in preview.stderr
    assert (root / ".venv").exists()
    result = run(checkout, "--mode", "remove", "--yes")
    assert result.returncode == 0, result.stderr
    assert "ZaoFu runtime removed" in result.stdout
    assert not (root / ".venv").exists() and not (root / "web/dist").exists()
    assert not (root / "web/node_modules").exists()
    assert (workspace / "user-data").read_text() == "preserve"
    assert (root / "install.sh").exists()
    assert "uv sync" not in calls(checkout) and "npm" not in calls(checkout)
    again = run(checkout, "remove", "--yes")
    assert again.returncode == 0 and "already removed" in again.stdout


def test_interactive_default_is_standalone(checkout):
    root, env = checkout
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(["/bin/bash", str(root / "install.sh"), "--skip-deps", "--no-start"],
                                   stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        os.write(master, b"\n")
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert "Install and enroll with a Controller" in stdout.decode()
        assert "zf controller" not in calls(checkout)
    finally:
        os.close(master)
        os.close(slave)


def test_interactive_remove_can_be_cancelled(checkout):
    root, env = checkout
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(["/bin/bash", str(root / "install.sh")],
                                   stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        os.write(master, b"4\nno\n")
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert "4) Remove ZaoFu" in stdout.decode() and "Removal cancelled" in stdout.decode()
        assert (root / ".venv").exists() and (root / "web/dist/index.html").exists()
    finally:
        os.close(master)
        os.close(slave)


def test_standalone_health_and_secret_output(checkout):
    result = run(checkout, "--yes", "--skip-deps", "--port", "8002")
    assert result.returncode == 0, result.stderr
    assert "Dashboard ready" in result.stdout
    assert "private-test-token" not in result.stdout + result.stderr
    assert "zf controller" not in calls(checkout)
    assert "--workspace-only" in calls(checkout) and "--no-restart" in calls(checkout)
    path = Path(checkout[1]["HOME"]) / ".zaofu/install-web-8002.log"
    assert path.stat().st_mode & 0o777 == 0o600
    assert "private-test-token" in path.read_text()
    previous = path.read_text()
    result = run(checkout, "--yes", "--skip-deps", "--port", "8002", LAUNCH_FAIL="1")
    assert result.returncode != 0
    assert path.read_text().startswith(previous)
    assert path.read_text().count("private-test-token") == 2


@pytest.mark.parametrize("failure", ["UNHEALTHY", "LAUNCH_FAIL"])
def test_web_failures_never_claim_ready(checkout, failure):
    result = run(checkout, "--yes", "--skip-deps", **{failure: "1"})
    assert result.returncode != 0
    assert "Dashboard ready" not in result.stdout


def test_locked_dependencies_fail_before_launch(checkout):
    result = run(checkout, "--yes", SYNC_DEPS_FAIL="7")
    assert result.returncode == 7
    assert "uv sync --locked --extra web --extra stream-json --extra controller" in calls(checkout)
    assert "npm" not in calls(checkout) and "launcher" not in calls(checkout)


@pytest.mark.parametrize("extra", [{"HERDR_VERSION": "0.7.0"}, {"HERDR_PROTOCOL_FAIL": "1"}])
def test_incompatible_herdr_blocks_even_skip_deps(checkout, extra):
    result = run(checkout, "--yes", "--skip-deps", **extra)
    assert result.returncode != 0 and "Herdr not ready" in result.stdout
    assert "launcher" not in calls(checkout) and "zf controller" not in calls(checkout)


@pytest.mark.parametrize("context", ["pin", "ZF_CONTROLLER_WORKSPACE_CONFIG", "ZF_CONTROLLER_CLIENT_ID", "ZFC_WORKSPACE_ID"])
@pytest.mark.parametrize("mode", ["standalone", "remove"])
def test_managed_installation_cannot_be_downgraded(checkout, context, mode):
    extra = {}
    if context == "pin":
        pin = Path(checkout[1]["HOME"]) / ".zaofu/controller-binding.json"
        pin.parent.mkdir()
        pin.symlink_to(pin.parent / "missing")
    else:
        extra[context] = "managed"
    result = run(checkout, "--mode", mode, "--yes", **extra)
    assert result.returncode != 0
    assert not calls(checkout)


def test_pending_enrollment_preserves_config_and_identity_on_rerun(checkout):
    first = run(checkout, *observed_args())
    assert first.returncode == 3, first.stderr
    config = Path(checkout[1]["HOME"]) / ".zaofu/controller/zf.yaml"
    original = config.read_bytes()
    loaded = load_controller_workspace(config)
    assert loaded.management == "observed" and loaded.projects == ("project-exact-id",)
    assert config.stat().st_mode & 0o777 == 0o600
    assert "launcher" not in calls(checkout) and "zf controller sync" not in calls(checkout)
    second = run(checkout, "--mode", "observed", "--skip-deps")
    assert second.returncode == 3 and config.read_bytes() == original
    conflicting = run(checkout, *observed_args(), "--workspace-id", "other")
    assert conflicting.returncode != 0 and config.read_bytes() == original
    assert run(checkout, "--yes").returncode != 0


def test_enroll_then_ack_then_start_watcher(checkout, tmp_path):
    code = tmp_path / "private-code"
    code.write_text("test-only-code-not-to-be-printed")
    code.chmod(0o600)
    result = run(checkout, *observed_args(), "--code-file", str(code))
    assert result.returncode == 0, result.stderr
    history = calls(checkout)
    assert history.index("zf controller enroll") < history.index("zf controller sync") < history.index("launcher")
    assert "--watch" in history and "zf-controller-8001" in history
    assert code.read_text() not in result.stdout + result.stderr + history
    # Reruns reuse the existing installation and still verify server delivery.
    result = run(checkout, "--mode", "observed", "--skip-deps", "--no-start")
    assert result.returncode == 0
    history = calls(checkout)
    result = run(checkout, "--mode", "observed", "--skip-deps", "--no-start", "--code-file", str(code))
    assert result.returncode == 0
    assert calls(checkout).count("zf controller enroll") == history.count("zf controller enroll")


def test_failed_controller_ack_does_not_fall_back_to_standalone(checkout, tmp_path):
    Path(checkout[1]["ENROLLED"]).touch()
    result = run(checkout, *observed_args(), SYNC_FAIL="1")
    assert result.returncode != 0
    assert "launcher" not in calls(checkout) and "--watch" not in calls(checkout)


@pytest.mark.parametrize("argument,value", [("--endpoint", "http://public.example"),
    ("--endpoint", "https://user:secret@example.com"), ("--issuer", "https://issuer/#secret"),
    ("--project", "invalid id"), ("--model-mode", "invalid-model-mode")])
def test_invalid_controller_config_not_published(checkout, argument, value):
    result = run(checkout, *observed_args(), argument, value)
    assert result.returncode != 0
    config = Path(checkout[1]["HOME"]) / ".zaofu/controller/zf.yaml"
    assert not config.exists()
    assert value not in result.stdout + result.stderr


def test_managed_yaml_not_rewritten_or_enrolled(checkout):
    run(checkout, *observed_args())
    config = Path(checkout[1]["HOME"]) / ".zaofu/controller/zf.yaml"
    raw = yaml.safe_load(config.read_text())
    raw["workspace"]["controller"].update(management="managed", gateway_origin="https://gateway.example")
    config.write_text(yaml.safe_dump(raw))
    before = config.read_bytes()
    Path(checkout[1]["CALLS"]).unlink()
    result = run(checkout, "--mode", "observed", "--skip-deps")
    assert result.returncode != 0 and config.read_bytes() == before
    assert not calls(checkout)


@pytest.mark.parametrize("argument,value", [("--port", "0"), ("--port", "65536"),
    ("--host", "127.0.0.1;touch /tmp/invalid"), ("--mode", "managed")])
def test_invalid_cli_arguments_fail_without_changes(checkout, argument, value):
    assert run(checkout, "--yes", argument, value).returncode != 0
    assert not calls(checkout)
