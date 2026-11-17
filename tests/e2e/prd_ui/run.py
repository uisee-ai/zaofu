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


def prepare(root: Path, mode: str, journey: str, *, review_policy=None,
            channel_template="prd-clarification") -> tuple[Path, dict[str, str]]:
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
    if review_policy:
        flow["spec"]["planReview"] = review_policy
        env["ZF_UI_REVIEW_POLICY"] = json.dumps(review_policy, ensure_ascii=False)
    env["ZF_UI_CHANNEL_TEMPLATE"] = channel_template
    spec = controller["spec"]
    spec["profile_sources"] = [str(REPO / path) for path in spec["profile_sources"]]
    spec["skill_sources"][0]["path"] = str(REPO / "skills")
    spec["project"] = {"name": f"ui-prd-{journey}-{mode}", "state_dir": ".ui-state"}
    spec["session"] = {"tmux_session": "zf-ui-prd"}
    spec["runtime"]["run_manager"] = {"backend": "codex", "resident_agent": {"enabled": False}}
    spec["runtime"]["autoresearch_resident"] = {"enabled": False}
    # Keep the Channel production budget; do not inherit Kanban's shorter test turn limit.
    spec.setdefault("channel", {})["provider_timeout_seconds"] = 1800
    # Fake starts with a different policy, forcing a real Apply/consumer handoff.
    spec["workflow"]["plan_approval"] = mode != "fake"
    if journey == "channel-recovery":
        from tests.e2e.prd_ui.recovery import configure
        configure(spec, env, REPO)
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
    if journey == "channel-recovery":
        from tests.e2e.prd_ui.recovery import setup
        setup(project)
    return project, env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["fake", "real"], required=True)
    parser.add_argument("--journey", choices=["kanban", "channel", "channel-recovery"], required=True)
    parser.add_argument("--requirement-file", type=Path, help="Frozen common product input for both UI journeys")
    parser.add_argument("--review-policy", type=Path, help="Explicit Owner-authorized plan_review JSON")
    parser.add_argument("--channel-template", choices=["prd-clarification", "prd-clarification-on-demand"],
                        default="prd-clarification")
    parser.add_argument("--delivery", action="store_true", help="Wait for a reviewed local authorization before UI Plan approval")
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--owner-checkpoint", action="store_true", help="Require a real Critic Owner checkpoint and answer it through UI")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--turn-timeout", type=int, help="Per-turn UI wait budget in seconds; real default 600, fake default 45")
    parser.add_argument("--workflow-runtime", choices=["existing", "cold"], default="existing",
                        help="cold stops the idle discussion runtime before the UI starts its Workflow")
    args = parser.parse_args()
    review_policy = None
    if args.review_policy:
        from zf.core.config.plan_review import validate_plan_review
        review_policy = validate_plan_review(json.loads(args.review_policy.read_text()))
    if args.turn_timeout is None:
        args.turn_timeout = min(args.timeout, 45 if args.provider == 'fake' else 600)
    if not 1 <= args.turn_timeout <= args.timeout:
        parser.error('turn-timeout must be positive and no greater than timeout')
    if args.journey == "channel-recovery" and args.provider != "real":
        parser.error("channel-recovery requires the explicitly authorized real Codex tier")
    if args.owner_checkpoint and (args.provider != "real" or args.journey != "kanban"):
        parser.error("owner-checkpoint requires the real Kanban journey")
    if args.delivery and (args.provider != "real" or args.journey == "channel-recovery"):
        parser.error("delivery requires a real PRD journey")
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
    project, env = prepare(root, args.provider, args.journey, review_policy=review_policy,
                          channel_template=args.channel_template)
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
                "status": "running", "body_status": "not_started", "audit_status": "not_run", "browser_exit_code": None, "test_boundary": "provider-only fake" if args.provider == "fake" else "real provider"}
    if args.journey == "channel-recovery":
        manifest["test_boundary"] = "real Codex, controlled stdio fault; setup via controlled actions, send/retry through UI"
    manifest["workflow_runtime"] = args.workflow_runtime
    manifest["requested_review_policy"] = review_policy
    manifest["channel_template"] = args.channel_template
    if args.provider == "fake":
        manifest["fake_fault"] = env.get("ZF_UI_FAKE_FAULT", "")
    manifest["owner_checkpoint_required"] = args.owner_checkpoint
    manifest["source_diff_sha256"] = hashlib.sha256(subprocess.check_output(
        ["git", "diff", "HEAD", "--", "src", "web/src", "web/tests", "tests", "skills"], cwd=REPO)).hexdigest()
    manifest["test_sources"] = {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in [*sorted((REPO / "tests/e2e/prd_ui").glob("*.py")), REPO / "web/tests/prd-ui-journeys.spec.ts", REPO / "web/tests/codex-channel-recovery.spec.ts"]}
    (evidence / "run.json").write_text(json.dumps(manifest, indent=2))
    # Freeze the actual configured Skill source, not only the runtime commit.
    skill_hashes = {str(p.relative_to(REPO / "skills")): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((REPO / "skills").rglob("*")) if p.is_file() and ".git" not in p.parts}
    (evidence / "skill-source.json").write_text(json.dumps({"path": str(REPO / "skills"), "files": skill_hashes}, indent=2))
    if args.requirement_file:
        requirement = args.requirement_file.read_bytes()
        (evidence / "product-input.md").write_bytes(requirement)
        manifest["product_input_sha256"] = hashlib.sha256(requirement).hexdigest()
    manifest["delivery_requested"] = args.delivery
    manifest['timeout_seconds'] = args.timeout
    manifest['turn_timeout_seconds'] = args.turn_timeout
    (evidence / "run.json").write_text(json.dumps(manifest, indent=2))
    processes = []
    print(f"[run] {root} http://127.0.0.1:{port}", flush=True)
    result = 1
    try:
        if args.provider == "fake":
            worker_log = (root / "worker.log").open("w")
            processes.append(subprocess.Popen([sys.executable, str(REPO / "tests/e2e/prd_ui/worker.py"), str(project)], cwd=project, env=env, stdout=worker_log, stderr=subprocess.STDOUT, start_new_session=True))
        if args.journey != "channel-recovery":
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
        browser_spec = "codex-channel-recovery.spec.ts" if args.journey == "channel-recovery" else "prd-ui-journeys.spec.ts"
        docker = ["docker", "run", "--rm", "--name", root.name, "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
                  "--entrypoint", "bash", "-v", f"{REPO}:/work", "-v", f"{(REPO / 'web/node_modules').resolve()}:/work/web/node_modules",
                  "-v", f"{root}:/zf-run", "-w", "/work/web", "-e", "HOME=/tmp/ui-playwright",
                  "-e", f"ZF_WEB_BASE_URL=http://127.0.0.1:{port}", "-e", f"ZF_WEB_ACTION_TOKEN_FOR_TEST={env['ZF_WEB_ACTION_TOKEN']}",
                  "-e", f"ZF_UI_DELIVERY={str(args.delivery).lower()}",
                  "-e", f"ZF_UI_PROVIDER={args.provider}", "-e", f"ZF_UI_JOURNEY={args.journey}",
                  "-e", f"ZF_UI_WORKFLOW_RUNTIME={args.workflow_runtime}",
                  "-e", f"ZF_UI_REVIEW_POLICY={env.get('ZF_UI_REVIEW_POLICY', '')}",
                  "-e", f"ZF_UI_CHANNEL_TEMPLATE={args.channel_template}",
                  "-e", f"ZF_UI_OWNER_CHECKPOINT={str(args.owner_checkpoint).lower()}",
                  "-e", f"ZF_UI_TURN_TIMEOUT_MS={args.turn_timeout * 1000}",
                  "-e", f"ZF_UI_TIMEOUT_MS={args.timeout * 1000}", "-e", f"ZF_E2E_VIDEO={os.environ.get('ZF_UI_RECORDING', 'on' if args.provider == 'fake' else 'off')}",
                  "-e", "ZF_E2E_TRACE=off" if args.provider == "real" else "ZF_E2E_TRACE=retain-on-failure", image, "-lc",
                  'mkdir -p "$HOME"; export ZF_E2E_CHROMIUM_EXECUTABLE_PATH=$(find /ms-playwright -type f -path "*/chrome-linux64/chrome" | sort | tail -1); '
                  f'test -n "$ZF_E2E_CHROMIUM_EXECUTABLE_PATH" && ./node_modules/.bin/playwright test tests/{browser_spec} '
                  '--workers=1 --reporter=line --output=/zf-run/evidence/playwright']
        browser = subprocess.Popen(docker, cwd=REPO)
        deadline = time.monotonic() + args.timeout + 120
        handoff = evidence / "runtime-handoff.json"
        while browser.poll() is None:
            if args.delivery and (evidence / "preview-ready.json").exists() and not (evidence / "preview-audit.json").exists():
                from tests.e2e.prd_ui.audit import audit
                (evidence / "preview-audit.json").write_text(json.dumps(audit(project, journey=args.journey), indent=2))
            if (args.workflow_runtime == "cold" and not handoff.exists()
                    and (evidence / "runtime-handoff-request.json").exists()):
                from zf.core.events import EventLog
                from zf.core.config.loader import load_config
                from zf.runtime.workflow_operation import reduce_workflow_operations, TERMINAL_OPERATION_STATUSES
                from zf.runtime.workflow_runtime_start import _runtime_owner, _process_identity
                state = project / load_config(project / "zf.yaml").project.state_dir
                before = EventLog(state / "events.jsonl").read_all()
                operations = reduce_workflow_operations(before)
                assert all(op["status"] in TERMINAL_OPERATION_STATUSES for op in operations.values()), "cannot stop an active Workflow operation"
                started = {e.payload.get("request_id") for e in before if e.type == "channel.agent.reply.started"}
                ended = {e.payload.get("request_id") for e in before if e.type in {
                    "channel.agent.reply.completed", "channel.agent.reply.failed"}}
                assert not started - ended, "cannot stop an active Channel reply"
                owner = _runtime_owner(state)
                old_pid = int(owner.get("owner_pid") or 0)
                old_identity = _process_identity(old_pid)
                command([REPO / ".venv/bin/zf", "stop", "--fast", "--include-run-manager"],
                        cwd=project, env=env, stdout=subprocess.DEVNULL, timeout=45)
                if old_pid:
                    launched_owner = next((process for process in processes if process.pid == old_pid), None)
                    assert launched_owner is not None, "runtime owner is outside this fixture"
                    launched_owner.wait(timeout=45)  # Reap it before the production PID guard checks liveness.
                for _ in range(450):
                    if not _runtime_owner(state) and (not old_identity or _process_identity(old_pid) != old_identity):
                        break
                    time.sleep(0.1)
                assert not _runtime_owner(state) and (not old_identity or _process_identity(old_pid) != old_identity), "discussion runtime has not exited"
                handoff.write_text(json.dumps({"mode": "cold", "action": "zf stop --fast --include-run-manager",
                                               "previous_owner": owner, "active_workflow_operations_before": 0,
                                               "terminal_operations_before": len(operations)}))
            if time.monotonic() >= deadline:
                raise TimeoutError("browser journey exceeded its total budget")
            time.sleep(0.25)
        result = browser.returncode
        manifest["browser_exit_code"] = result
        manifest["body_status"] = "completed" if (evidence / "body-completed.json").exists() else "incomplete"
        manifest["audit_status"] = "not_run"
        if result == 0 or (evidence / "body-completed.json").exists():
            from tests.e2e.prd_ui.audit import audit, check_config_handoff
            try:
                if args.journey == "channel-recovery":
                    from tests.e2e.prd_ui.recovery import audit as recovery_audit
                    result_audit = recovery_audit(project)
                elif args.delivery:
                    result_audit = json.loads((evidence / "preview-audit.json").read_text())
                    result_audit["boundary"] = "preview only; delivery evidence audited separately"
                else:
                    result_audit = audit(project, journey=args.journey, owner_checkpoint=args.owner_checkpoint)
                if args.provider == "fake" and args.workflow_runtime == "existing":
                    from zf.core.events import EventLog
                    from zf.core.config.loader import load_config
                    state = project / load_config(project / "zf.yaml").project.state_dir
                    log = EventLog(state / "events.jsonl")
                    try:
                        result_audit["config_handoff"] = check_config_handoff(log.read_all())
                    finally:
                        log.close()
                (evidence / "audit.json").write_text(json.dumps(result_audit, indent=2))
                manifest["audit_status"] = "passed"
            except Exception as exc:
                result = 1
                manifest["audit_status"] = "failed"
                (evidence / "audit-error.txt").write_text(f"{type(exc).__name__}: {exc}")
                print(f"[audit failed] {exc}", flush=True)
    finally:
        subprocess.run(["docker", "rm", "-f", root.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if not port_lock.closed:
            port_lock.close()
        from tests.e2e.prd_ui.recording import inspect_recordings
        manifest["recording"] = inspect_recordings(evidence / "playwright")
        if manifest["recording"]["invalid"]:
            result = 1
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
