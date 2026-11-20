from zipfile import ZipFile

from tests.e2e.prd_ui.recording import inspect_recordings


def test_recording_integrity_does_not_infer_body_or_product_success(tmp_path):
    assert inspect_recordings(tmp_path)["status"] == "not_recorded"
    with ZipFile(tmp_path / "trace.zip", "w") as archive:
        archive.writestr("trace", "body completed")
    assert inspect_recordings(tmp_path)["status"] == "validated"
    (tmp_path / "broken.zip").write_bytes(b"PK\x03\x04truncated")
    result = inspect_recordings(tmp_path)
    assert result["status"] == "failed"
    assert result["valid"] == ["trace.zip"]
    assert result["invalid"][0]["path"] == "broken.zip"
