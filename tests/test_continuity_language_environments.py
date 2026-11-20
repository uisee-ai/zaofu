"""Opt-in local language qualification. No downloads or provider invocation."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from zf.runtime.environment_usage import environment_in_use, guarded_provider_argv
from zf.runtime.worktree_env import SETUP_MARKER, provision_worktree_env, run_project_setup

pytestmark = pytest.mark.host


def language_fixture(root: Path, language: str):
    source = root / "source"
    source.mkdir()
    if language == "python":
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(source / ".venv")], check=True, timeout=30)
        python = source / ".venv/bin/python"
        result = subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            check=True, capture_output=True, text=True, timeout=10)
        (Path(result.stdout.strip()) / "continuity_dependency.py").write_text("VALUE = 42\n")
        resource = ".venv"
        argv = [".venv/bin/python", "-c", "import continuity_dependency; print(continuity_dependency.VALUE)"]
        files = {"requirements.txt": "# isolated offline dependency fixture\n"}
    elif language == "node":
        node = shutil.which("node")
        if not node:
            pytest.skip("Node executable unavailable; no host installation authorized by this test")
        dependency = source / "node_modules/continuity-dependency"
        dependency.mkdir(parents=True)
        (dependency / "index.js").write_text("module.exports = 42;\n")
        resource, argv = "node_modules", [node, "-e", "console.log(require('continuity-dependency'))"]
        files = {"package.json": '{"name":"continuity-fixture","version":"1.0.0"}\n'}
    else:
        go = shutil.which("go")
        if not go:
            pytest.skip("Go executable unavailable; no host installation authorized by this test")
        dependency = source / "go-deps/dependency"
        dependency.mkdir(parents=True)
        (dependency / "go.mod").write_text("module example.local/dependency\n\ngo 1.20\n")
        (dependency / "value.go").write_text("package dependency\nconst Value = 42\n")
        resource, argv = "go-deps", [go, "run", "."]
        files = {
            "go.mod": "module example.local/consumer\n\ngo 1.20\nrequire example.local/dependency v0.0.0\nreplace example.local/dependency => ./go-deps/dependency\n",
            "main.go": 'package main\nimport ("fmt"; "example.local/dependency")\nfunc main() {fmt.Println(dependency.Value)}\n',
        }
    consumers = [root / "first", root / "second"]
    for consumer in consumers:
        consumer.mkdir()
        for name, body in files.items():
            (consumer / name).write_text(body)
        (consumer / "tool-identity.txt").write_text("qualified-v1\n")
        (consumer / "candidate.txt").write_text("preserve unfinished source\n")
        assert provision_worktree_env(consumer, source, [resource]) == [resource]
    return source, consumers, resource, argv


@pytest.mark.parametrize("language", ["python", "node", "go"])
def test_real_language_reuse_invalidation_and_failed_preparation(tmp_path, monkeypatch, language):
    # Every cache and compiler effect stays in this fixture. Go cannot fetch a toolchain/module.
    for name, value in {"GOCACHE": str(tmp_path / "go-cache"), "GOMODCACHE": str(tmp_path / "go-mod-cache"),
                        "GOPROXY": "off", "GOSUMDB": "off", "GOENV": "off", "GOTOOLCHAIN": "local"}.items():
        monkeypatch.setenv(name, value)
    source, consumers, resource, argv = language_fixture(tmp_path, language)
    command = shlex.join(argv)
    for consumer in consumers:
        ready = run_project_setup(consumer, command, setup_inputs=["tool-identity.txt"])
        assert ready.ok and ready.ran, ready.detail
    first, second = consumers
    with environment_in_use(first), environment_in_use(second):
        for consumer in consumers:
            assert (consumer / resource).resolve() == source / resource
            reused = run_project_setup(consumer, command, setup_inputs=["tool-identity.txt"])
            assert reused.ok and not reused.ran, reused.detail
            output = subprocess.run(guarded_provider_argv(consumer, argv), cwd=consumer,
                env=os.environ.copy(), check=True, text=True, capture_output=True, timeout=45)
            assert output.stdout.strip() == "42"
        (second / "tool-identity.txt").write_text("qualified-v2\n")
        refused = run_project_setup(second, command, setup_inputs=["tool-identity.txt"])
        assert not refused.ok and not refused.ran
    failed = run_project_setup(second, "exit 17", setup_inputs=["tool-identity.txt"])
    assert not failed.ok and failed.exit_code == 17
    assert not (second / SETUP_MARKER).exists()
    repaired = run_project_setup(second, command, setup_inputs=["tool-identity.txt"])
    assert repaired.ok and repaired.ran, repaired.detail
    for consumer in consumers:
        assert (consumer / "candidate.txt").read_text() == "preserve unfinished source\n"
