import base64
import hashlib
import json

from tests.test_artifact_read_capability import _grant
from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.artifact_input_page import expand_input_items
from zf.runtime.artifact_read_ledger import active_read_ledger_path, source_manifest_from_payload


def test_png_reader_cli_assembles_frozen_image_without_mount_or_text_read_credit(tmp_path, monkeypatch, capsys):
    body = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 53
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "desktop.png").write_bytes(body)
    sha = hashlib.sha256(body).hexdigest()
    source = {"ref": "artifacts/desktop.png", "kind": "container_media_projection",
              "artifact_id": "desktop.png", "source_id": "desktop", "sha256": sha}
    forbidden = {**source, "source_id": "private", "access_scope": {"actors": ["someone-else"]}}
    partial = {**source, "source_id": "partial", "allowed_paths": ["$.thumbnail"]}
    manifest, _ = source_manifest_from_payload(state_dir=tmp_path, project_root=tmp_path.parent,
        payload={"input_refs": [source, forbidden, partial]}, workflow_run_id="r", task_id="T7",
        attempt_id="a", dispatch_id="a", manifest_metadata={"read_purpose": "implementation"})
    _grant(tmp_path, monkeypatch, manifest=manifest)
    log = EventLog(tmp_path / "events.jsonl")
    EventWriter(log).append(ZfEvent(type="task.dispatched", actor="orchestrator", task_id="T7",
                                    payload={"dispatch_id": "a", "required_reads": []}))
    options = ["--attempt","a","--state-dir",str(tmp_path)]
    assert main(["artifact","index",*options,"--source-id","desktop"]) == 0
    row, = expand_input_items(json.loads(capsys.readouterr().out))
    assert "--image-page 0" in row["image_page_command"]
    def read(source_id, number):
        return main(["artifact","read",*options,"--source",source_id,
                     "--artifact","desktop.png","--image-page",str(number)])
    chunks = []
    page = 0
    while page is not None:
        assert read("desktop", page) == 0
        frame = json.loads(capsys.readouterr().out)
        assert (frame["page"],frame["sha256"],frame["byte_count"]) == (page,sha,len(body))
        chunks.append(frame["data_base64"])
        page = frame["next_page"]
    assert base64.b64decode("".join(chunks),validate=True) == body
    assert not active_read_ledger_path(tmp_path,"a").exists()
    for source_id, number in [("desktop", -1), ("desktop", 5), ("unknown", 0), ("private",0), ("partial",0)]:
        assert read(source_id,number) == 1
        assert not capsys.readouterr().out
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN","unbound")
    monkeypatch.delenv("ZF_ARTIFACT_READ_TOKEN_FILE", raising=False)
    assert read("desktop",0) == 1
    assert not capsys.readouterr().out
