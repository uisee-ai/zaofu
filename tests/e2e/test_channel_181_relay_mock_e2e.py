import importlib.util
from pathlib import Path

import pytest


@pytest.mark.mock_e2e
def test_five_member_relay_preserves_original_requirements_through_api(tmp_path):
    script = Path(__file__).parent / "scripts/channel_181_relay.py"
    spec = importlib.util.spec_from_file_location("channel_181_relay", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run(tmp_path)
    assert len(result.report["roles"]) == 5
    assert [r["source_count"] for r in result.report["roles"]] == [1, 2, 3, 4, 5]
