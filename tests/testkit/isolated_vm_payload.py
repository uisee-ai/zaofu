"""Build the narrow offline payload for K571; never copy a host workspace/state."""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from tests.testkit.evidence import sha256_file, write_json_atomic


REPO = Path(__file__).resolve().parents[2]
DISTRIBUTIONS = (
    "PyYAML", "pytest", "hypothesis", "httpx", "rich", "click", "Pygments",
    "markdown-it-py", "mdurl", "anyio", "certifi", "httpcore", "idna", "h11",
    "typing_extensions", "sortedcontainers", "iniconfig", "packaging", "pluggy",
)
TEST_FILES = (
    "tests/__init__.py", "tests/e2e/__init__.py",
    "tests/e2e/generic_workflow_complex_support.py",
    "tests/e2e/scripts/prod_flow_terminal_audit.py",
    "tests/test_control_actions_workflow_request.py",
    *(f"tests/testkit/{name}.py" for name in (
        "case_evidence", "cleanup", "evidence", "failure_bundle", "general_route",
        "issue_route", "live_general_runtime", "memray_guard", "model_evidence",
        "isolated_vm_cli", "isolated_vm_guest", "isolated_vm_scenario", "ordinary_recovery", "parallel", "runner", "source_observation", "suites",
        "tmux_general_worker",
    )),
)


def _copy_tree(source: Path, destination: Path, *, interpreter: bool = False) -> None:
    """Preserve only internal symlinks; omit caches and host site-packages."""
    source = source.resolve()
    for entry in source.rglob("*"):
        if entry.is_symlink() and (
            Path(os.readlink(entry)).is_absolute() or not entry.resolve().is_relative_to(source)
        ):
            raise ValueError(f"external or absolute symlink forbidden: {entry}")
    ignored = ["__pycache__", "*.pyc", "*.pyo"]
    if interpreter:
        ignored.append("site-packages")
    shutil.copytree(source, destination, symlinks=True, ignore=shutil.ignore_patterns(*ignored))


def _copy_native_tools(payload: Path) -> dict:
    """Copy only trusted installed git/tmux ELF dependencies, never host config."""
    native = payload / "native"
    for name in ("bin", "executables", "lib"):
        (native / name).mkdir(parents=True)
    versions = {}
    for name in ("git", "tmux"):
        executable = Path("/usr/bin") / name
        linked = subprocess.run(["/usr/bin/ldd", str(executable)], check=True,
                                capture_output=True, text=True, timeout=10).stdout
        if "not found" in linked:
            raise ValueError(f"missing native dependency for {name}")
        libraries = re.findall(r"(?:=>\s+|^\s*)(/[^\s]+)\s+\(", linked, re.MULTILINE)
        assert libraries and any("ld-linux" in path for path in libraries)
        for library in libraries:
            source = Path(library)
            shutil.copy2(source.resolve(), native / "lib" / source.name)
        shutil.copy2(executable, native / "executables" / name)
        wrapper = native / "bin" / name
        wrapper.write_text(
            '#!/bin/sh\n'
            'native=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
            'export TERMINFO_DIRS="$native/terminfo"\n'
            'export GIT_TEMPLATE_DIR="$native/git-templates"\n'
            'exec "$native/lib/ld-linux-x86-64.so.2" --library-path "$native/lib" '
            f'"$native/executables/{name}" "$@"\n'
        )
        wrapper.chmod(0o755)
        result = subprocess.run([str(wrapper), "--version" if name == "git" else "-V"],
                                check=True, capture_output=True, text=True, timeout=10)
        versions[name] = result.stdout.strip()
    terminfo = next((path for path in (Path("/lib/terminfo"), Path("/usr/share/terminfo"))
                     if path.is_dir()), None)
    if terminfo is None:
        raise ValueError("system terminfo directory is missing")
    _copy_tree(terminfo, native / "terminfo")
    templates = Path("/usr/share/git-core/templates")
    if templates.is_dir():
        _copy_tree(templates, native / "git-templates")
    return versions


def build_payload(root: Path) -> dict:
    root = root.absolute()
    if root.parent != Path("/tmp") or not root.name.startswith("zf-k571-payload-"):
        raise ValueError("fresh /tmp/zf-k571-payload-<UTC>-<nonce> root required")
    root.mkdir(mode=0o700, exist_ok=False)
    payload = root / "payload"
    payload.mkdir()
    native_versions = _copy_native_tools(payload)
    base = Path(sys.base_prefix).resolve()
    if sys.version_info[:2] != (3, 11) or not base.name.startswith("cpython-3.11."):
        raise ValueError("audited standalone CPython 3.11 base required")
    _copy_tree(base, payload / "python", interpreter=True)
    site = payload / "python/lib/python3.11/site-packages"
    site.mkdir()
    distributions = {}
    for name in DISTRIBUTIONS:
        dist = metadata.distribution(name)
        distributions[dist.metadata["Name"]] = dist.version
        origin = Path(dist.locate_file("")).resolve()
        if dist.files is None:
            raise ValueError(f"missing installed file manifest: {name}")
        for relative in dist.files:
            path = Path(str(relative))
            # Distribution script entrypoints are outside site-packages. Do not
            # copy them, editable hooks, host install origins, or bytecode.
            if (path.is_absolute() or ".." in path.parts or "__pycache__" in path.parts
                    or path.suffix in {".pyc", ".pyo", ".pth"}
                    or path.name == "direct_url.json"):
                continue
            source = Path(dist.locate_file(relative))
            if source.is_symlink() or not source.resolve().is_relative_to(origin):
                raise ValueError(f"distribution path escape: {source}")
            destination = site / path
            if destination.exists():
                raise ValueError(f"overlapping distribution payload: {path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    repo = payload / "repo"
    for package in ("zf", "issue_core"):
        _copy_tree(REPO / "src" / package, repo / "src" / package)
    for relative in TEST_FILES:
        source, destination = REPO / relative, repo / relative
        if source.is_symlink():
            raise ValueError(f"fixture symlink forbidden: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    # Inventory before executing imports; no pycache or runtime state in payload.
    files = []
    for path in sorted(payload.rglob("*")):
        if path.is_symlink():
            files.append({"path": str(path.relative_to(payload)), "link": os.readlink(path)})
        elif path.is_file():
            files.append({"path": str(path.relative_to(payload)), "sha256": sha256_file(path),
                          "bytes": path.stat().st_size})
    manifest = {"schema_version": "zf-testkit-vm-payload.v1", "python": sys.version,
                "native_versions": native_versions,
                "distributions": distributions, "files": files,
                "builder_sha256": sha256_file(Path(__file__)), "guest_verified": False}
    manifest_sha = write_json_atomic(root / "payload-manifest.json", manifest)
    env = {
        "PATH": f"{payload / 'native/bin'}:{payload / 'python/bin'}:/usr/bin:/bin", "LANG": "C.UTF-8",
        "PYTHONPATH": f"{repo}:{repo / 'src'}", "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "ZF_DISABLE_LIVE_FEISHU": "1", "ZF_WORKSPACE_HOME": str(root / "workspace"),
    }
    executable = str(payload / "python/bin/python3")
    probe = subprocess.run(
        [executable, "-c", "import sys, zf.cli.main, tests.testkit.isolated_vm_guest, "
         "tests.testkit.tmux_general_worker, tests.testkit.live_general_runtime; import json; "
         "print(json.dumps({'prefix':sys.prefix,'executable':sys.executable}))"],
        cwd=root, env=env, capture_output=True, text=True, timeout=20,
    )
    help_result = subprocess.run(
        [executable, "-m", "zf.cli.main", "--help"], cwd=root, env=env,
        capture_output=True, text=True, timeout=20,
    )
    # The fixture uses real configuration synthesis/admission but starts no
    # watcher, tmux session or worker. This closes dynamic template imports.
    config_result = subprocess.run(
        [executable, "-c", """
import json
from pathlib import Path
import pytest
from tests.testkit.general_route import GeneralRoute
from tests.e2e.generic_workflow_complex_support import _execute_action
project = Path('config-probe').absolute()
project.mkdir()
with pytest.MonkeyPatch.context() as patch:
    route = GeneralRoute(project, patch)
    _, preview = route.prepare()
    proposal = preview['proposal']
    applied = _execute_action(route.service, route.writer, 'workflow-config-apply', {
        'proposal_id': proposal['proposal_id'], 'proposal_ref': preview['proposal_ref'],
        'proposal_digest': proposal['proposal_digest'],
        'validation_result_ref': proposal['validation_result_ref'],
        'approval_ref': 'owner:k571-offline-payload-probe',
        'idempotency_key': 'k571-payload:'+proposal['proposal_digest'],
    })
    assert applied['ok'], applied
    route.reopen()
    assert not route.events('workflow.invoke.requested')
    assert not route.events('run.goal.completed')
    assert len(route.events('workflow.config.change.applied')) == 1
    print(json.dumps({'config_apply':True,'run_started':False,
                      'stages':[stage.id for stage in route.config.workflow.stages]}))
"""], cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )
    report = {"scope": "host-relocated-payload-preflight-not-guest", "verdict": "fail",
              "manifest_sha256": manifest_sha, "files": len(files),
              "bytes": sum(row.get("bytes", 0) for row in files),
              "probe_exit": probe.returncode, "probe_stdout": probe.stdout,
              "probe_stderr": probe.stderr, "cli_help_exit": help_result.returncode,
              "cli_help_stdout": help_result.stdout, "cli_help_stderr": help_result.stderr,
              "config_exit": config_result.returncode, "config_stdout": config_result.stdout,
              "config_stderr": config_result.stderr}
    if probe.returncode == help_result.returncode == config_result.returncode == 0:
        observed = json.loads(probe.stdout)
        if Path(observed["prefix"]) == payload / "python":
            report["verdict"] = "pass"
    write_json_atomic(root / "payload-preflight.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    result = build_payload(parser.parse_args().root)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["verdict"] == "pass" else 1)
