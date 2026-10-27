"""The real-provider evaluator retains read-only inputs and native TLS trust."""
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import json

import pytest


@pytest.mark.parametrize("ca_source", ["custom", "default", "directory"])
def test_container_uses_writable_ephemeral_home_and_verified_tls(tmp_path, monkeypatch, ca_source):
    source = Path(__file__).resolve().parents[1] / "scripts/eval-review-skill.py"
    spec = importlib.util.spec_from_file_location("review_eval", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirement.md").write_text("must remain unchanged")
    ca = tmp_path / "ca-certificates.crt"
    ca.write_text("fixture CA")
    if ca_source == "custom":
        monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    else:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.setattr(module.ssl, "get_default_verify_paths", lambda: SimpleNamespace(
            cafile=str(ca) if ca_source == "default" else None, capath=str(tmp_path),
        ))
    calls = []

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            calls.append(command)
            self.out = kwargs["stdout"]

        def communicate(self, prompt, timeout):
            assert timeout is None  # Explicit no-deadline evaluation survives the runner.
            self.out.write(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}) + "\n")

    monkeypatch.setattr(module.subprocess, "Popen", Process)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.shutil, "which", lambda name: str(tmp_path / name))
    args = SimpleNamespace(model="fixed-model", docker_image="sha256:fixed", auth_file=tmp_path / "auth.json", timeout=None)
    result = module.run_one(tmp_path, "read-only review", args)
    command = calls[0]
    assert command[command.index("--user") + 1] == "0:0"
    assert "--read-only" in command
    assert "/root:rw,nosuid,size=128m" in command
    assert f"{project}:/workspace:ro" in command
    assert f"{ca}:/etc/zf-eval-ca.pem:ro" in command
    assert "SSL_CERT_FILE=/etc/zf-eval-ca.pem" in command
    assert result["turn_completed"]
    assert result["changed_paths"] == []
