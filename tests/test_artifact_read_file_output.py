import json
from pathlib import Path
import subprocess

import pytest

from zf.runtime.artifact_read_file_output import required_read_stream


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("__pycache__/\n.cache/\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_large_unicode_output_is_complete_and_stdout_only_has_receipt(repo, capsys):
    body = json.dumps({"data": "正文" * 100000}, ensure_ascii=False) + "\n"
    with required_read_stream("app/__pycache__/read.jsonl") as f:
        f.write(body)
    receipt = json.loads(capsys.readouterr().out)
    assert Path(receipt["path"]).read_text() == body
    assert receipt["model_consumption_proven"] is False
    assert receipt["byte_count"] == len(body.encode())
    with pytest.raises(ValueError, match="already exists"):
        with required_read_stream("app/__pycache__/read.jsonl"):
            pass


@pytest.mark.parametrize(
    "path",
    ["../outside/__pycache__/read.jsonl", "app/read.jsonl", ".cache/../../outside"],
)
def test_invalid_destinations_are_rejected(repo, path):
    with pytest.raises(ValueError):
        with required_read_stream(path):
            pass


def test_failure_does_not_publish_partial_cache(repo, capsys):
    with pytest.raises(ValueError):
        with required_read_stream("app/__pycache__/failed.jsonl") as f:
            f.write("partial")
            raise ValueError("source digest failed")
    assert not (repo / "app/__pycache__/failed.jsonl").exists()
    assert not list((repo / "app/__pycache__").glob(".required-read-*"))
    assert not capsys.readouterr().out
