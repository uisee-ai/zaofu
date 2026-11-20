import json

from tests.test_artifact_read_snapshot import IDENTITY, published
from zf.cli.artifact_required_read import prepare_required_read
from zf.runtime.artifact_input_recovery import build_input_recovery


def test_recovery_guidance_keeps_existing_authority_and_a_finite_read_exit(tmp_path):
    manifest, required, _ = published(tmp_path)
    result = build_input_recovery(state_dir=tmp_path, manifest=manifest,
        identity=IDENTITY, events=[], required=required, reads=[])
    assert result["authorizes_execution"] is False
    assert "does not revoke" in result["instructions"]
    assert "--resume" in result["commands"]["required_read"]
    assert result["recovery_exit"]["after_complete_delivery"] == "continue_current_step"
    assert result["recovery_exit"]["requires_current_execution_authority"] is True


def test_last_required_page_moves_to_delivery_check_instead_of_restart(tmp_path):
    manifest, required, _ = published(tmp_path, body={"shared": "rules" * 2000, "tasks": [{} for _ in range(8)]})
    cursor = None
    for _ in range(100):
        output, _ = prepare_required_read(tmp_path, manifest=manifest, rows=required,
            identity=IDENTITY, cursor=cursor, page_bytes=2048)
        page = json.loads(output)
        assert len(output.encode()) + 1 <= 2048
        if page["complete"]:
            assert page["next_action"] == "confirm_delivery_then_continue_current_step"
            assert page["next_cursor"] is None
            break
        assert page["next_action"] == "read_next_page"
        cursor = page["next_cursor"]
    else:
        raise AssertionError("finite required selection never finished")
