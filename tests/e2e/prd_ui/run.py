"""Run isolated Docker Playwright PRD journeys, with fake or real providers."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import hashlib
import fcntl
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import yaml

REPO = Path(__file__).resolve().parents[3]


def command(args, *, cwd, env, **kwargs):
    return subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True, **kwargs)


def prepare(root: Path, mode: str, journey: str) -> tuple[Path, dict[str, str]]:
    root.mkdir(parents=True, exist_ok=False)
    project = root / "project"
    project.mkdir()
    (root / "tmux").mkdir()
    env = dict(os.environ, PYTHONPATH=f"{REPO / 'src'}:{REPO}",
               ZF_WORKSPACE_HOME=str(root / "workspace"), TMUX_TMPDIR=str(root / "tmux"),
               ZF_UI_RUN_ROOT=str(root), ZF_TMUX_SESSION="zf-ui-prd",
               ZF_STATE_DIR=str(project / ".ui-state"), ZF_WEB_ACTION_TOKEN="ui-test-" + root.name,
               ZF_KANBAN_AGENT_BACKEND="codex-headless", ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S="600",
               ZF_CODEX_HEADLESS_TOOL_TIMEOUT_S="600", PYTHONUNBUFFERED="1")
    for key in ("ZF_PROJECT_ROOT", "ZF_CLI_CMD", "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
                "ZF_KANBAN_AGENT_CODEX_HEADLESS_CMD", "ZF_KANBAN_AGENT_CODEX_CMD"):
        env.pop(key, None)
    if mode == "fake":
        binary = root / "bin"
        binary.mkdir()
        shim = binary / "codex"
        shim.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(REPO / 'tests/e2e/prd_ui/provider.py')!r}, run_name='__main__')\n")
        shim.chmod(0o755)
        env["PATH"] = str(binary) + os.pathsep + env["PATH"]
        env["ZF_KANBAN_AGENT_CODEX_HEADLESS_CMD"] = str(shim)
    documents = list(yaml.safe_load_all((REPO / "zf-prd-v5.yaml").read_text()))
    flow, controller = documents
    flow["spec"].update(prdRef="docs/prd/ui-requirement.md", targetRoot=str(project))
    spec = controller["spec"]
    spec["profile_sources"] = [str(REPO / path) for path in spec["profile_sources"]]
    spec["skill_sources"][0]["path"] = str(REPO / "skills")
    spec["project"] = {"name": f"ui-prd-{journey}-{mode}", "state_dir": ".ui-state"}
    spec["session"] = {"tmux_session": "zf-ui-prd"}
    spec["runtime"]["run_manager"] = {"backend": "codex", "resident_agent": {"enabled": False}}
    spec["runtime"]["autoresearch_resident"] = {"enabled": False}
    spec["workflow"]["plan_approval"] = True  # Approved test preset; UI must display it before starting.
    (project / "zf.yaml").write_text(yaml.safe_dump_all(documents, sort_keys=False))
    (project / "README.md").write_text("# Isolated PRD UI acceptance project\n")
    (project / ".gitignore").write_text(".ui-state/\nartifacts/\ndocs/intake/\ndocs/prd/\n.claude/\n.codex/\n")
    command(["git", "init", "-q", "-b", "dev"], cwd=project, env=env)
    command(["git", "config", "user.name", "ZaoFu UI test"], cwd=project, env=env)
    command(["git", "config", "user.email", "ui-test@example.invalid"], cwd=project, env=env)
    command(["git", "add", "--", "README.md", ".gitignore", "zf.yaml"], cwd=project, env=env)
    command(["git", "commit", "-qm", "chore: initialize isolated PRD UI project"], cwd=project, env=env)
    command([REPO / ".venv/bin/zf", "init", "--force", "--no-workspace-register", "--no-git-hooks", "--skip-instruction-docs"],
            cwd=project, env=env, stdout=subprocess.DEVNULL)
    from zf.core.workspace.onboarding import apply_action
    previous = os.environ.get("ZF_WORKSPACE_HOME")
    os.environ["ZF_WORKSPACE_HOME"] = env["ZF_WORKSPACE_HOME"]
    try:
        apply_action("skip")
    finally:
        if previous is None:
            os.environ.pop("ZF_WORKSPACE_HOME", None)
        else:
            os.environ["ZF_WORKSPACE_HOME"] = previous
    return project, env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["fake", "real"], required=True)
    parser.add_argument("--journey", choices=["kanban", "channel"], required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    root = args.run_root or Path(f"/tmp/zf-prd-ui-{args.provider}-{args.journey}-{stamp}")
    if root.parent != Path("/tmp") or not root.name.startswith("zf-") or root.exists():
        parser.error("run-root must be a new /tmp/zf-* directory")
    image = os.environ.get("ZF_PLAYWRIGHT_IMAGE", "mcp/playwright:latest")
    command(["docker", "image", "inspect", image], cwd=REPO, env=os.environ, stdout=subprocess.DEVNULL)
    if args.provider == "real":
        command(["codex", "login", "status"], cwd=REPO, env=os.environ, timeout=15)
    if not args.skip_build:
        command(["npm", "--prefix", "web", "run", "build"], cwd=REPO, env=os.environ)
    project, env = prepare(root, args.provider, args.journey)
    port_lock = open("/tmp/zf-prd-ui-port-allocation.lock", "a")
    fcntl.flock(port_lock, fcntl.LOCK_EX)
    port = 8002
    while port < 65535:
        with socket.socket() as sock:
            try:
                sock.bind(("0.0.0.0", port)); break
            except OSError:
                port += 1
    evidence = root / "evidence"
    evidence.mkdir()
    manifest = {"provider": args.provider, "journey": args.journey, "project_root": str(project),
                "port": port, "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
                "status": "running", "test_boundary": "provider-only fake" if args.provider == "fake" else "real provider"}
    manifest["source_diff_sha256"] = hashlib.sha256(subprocess.check_output(
        ["git", "diff", "HEAD", "--", "src", "web/src", "web/tests", "tests", "skills"], cwd=REPO)).hexdigest()
    manifest["test_sources"] = {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in [*sorted((REPO / "tests/e2e/prd_ui").glob("*.py")), REPO / "web/tests/prd-ui-journeys.spec.ts"]}
    (evidence / "run.json").write_text(json.dumps(manifest, indent=2))
    processes = []
    print(f"[run] {root} http://127.0.0.1:{port}", flush=True)
    result = 1
    try:
        if args.provider == "fake":
            worker_log = (root / "worker.log").open("w")
            processes.append(subprocess.Popen([sys.executable, str(REPO / "tests/e2e/prd_ui/worker.py"), str(project)], cwd=project, env=env, stdout=worker_log, stderr=subprocess.STDOUT, start_new_session=True))
        runtime_log = (root / "runtime.log").open("w")
        processes.append(subprocess.Popen([str(REPO / ".venv/bin/zf"), "start"], cwd=project, env=env, stdout=runtime_log, stderr=subprocess.STDOUT, start_new_session=True))
        log = (root / "web.log").open("w")
        processes.append(subprocess.Popen([str(REPO / ".venv/bin/zf"), "web", "--host", "0.0.0.0", "--port", str(port)],
                                          cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
        for _ in range(90):
            if processes[-1].poll() is not None:
                raise RuntimeError("Web exited: " + (root / "web.log").read_text()[-4000:])
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot/light", timeout=2) as response:
                    snapshot = json.load(response)
                if snapshot["project"]["root"] != str(project):
                    raise RuntimeError("port collision: server belongs to another project")
                break
            except OSError:
                time.sleep(1)
        else:
            raise TimeoutError("Web startup")
        fcntl.flock(port_lock, fcntl.LOCK_UN)
        port_lock.close()
        docker = ["docker", "run", "--rm", "--name", root.name, "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
                  "--entrypoint", "bash", "-v", f"{REPO}:/work", "-v", f"{(REPO / 'web/node_modules').resolve()}:/work/web/node_modules",
                  "-v", f"{root}:/zf-run", "-w", "/work/web", "-e", "HOME=/tmp/ui-playwright",
                  "-e", f"ZF_WEB_BASE_URL=http://127.0.0.1:{port}", "-e", f"ZF_WEB_ACTION_TOKEN_FOR_TEST={env['ZF_WEB_ACTION_TOKEN']}",
                  "-e", f"ZF_UI_PROVIDER={args.provider}", "-e", f"ZF_UI_JOURNEY={args.journey}",
                  "-e", f"ZF_UI_TIMEOUT_MS={args.timeout * 1000}", "-e", "ZF_E2E_VIDEO=on", image, "-lc",
                  'mkdir -p "$HOME"; export ZF_E2E_CHROMIUM_EXECUTABLE_PATH=$(find /ms-playwright -type f -path "*/chrome-linux64/chrome" | sort | tail -1); '
                  'test -n "$ZF_E2E_CHROMIUM_EXECUTABLE_PATH" && ./node_modules/.bin/playwright test tests/prd-ui-journeys.spec.ts '
                  '--workers=1 --reporter=line --output=/zf-run/evidence/playwright']
        result = subprocess.run(docker, cwd=REPO, timeout=args.timeout + 120).returncode
        if result == 0:
            from tests.e2e.prd_ui.audit import audit
            try:
                (evidence / "audit.json").write_text(json.dumps(audit(project, journey=args.journey), indent=2))
            except Exception as exc:
                result = 1
                (evidence / "audit-error.txt").write_text(f"{type(exc).__name__}: {exc}")
                print(f"[audit failed] {exc}", flush=True)
    finally:
        subprocess.run(["docker", "rm", "-f", root.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if not port_lock.closed:
            port_lock.close()
        manifest["status"] = "passed" if result == 0 else "failed"
        (evidence / "run.json").write_text(json.dumps(manifest, indent=2))
        zf = str(REPO / ".venv/bin/zf")
        subprocess.run([zf, "emit", "simulation.done", "--actor", "ui-test", "--payload", '{"purpose":"prd-ui"}'],
                       cwd=project, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        subprocess.run([zf, "stop", "--fast", "--include-run-manager"], cwd=project, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        subprocess.run(["tmux", "kill-server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[evidence] {evidence}", flush=True)
    return result

if __name__ == "__main__":
    raise SystemExit(main())
