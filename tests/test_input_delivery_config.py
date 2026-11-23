"""Public Flow configuration must pin opt-in delivery without changing defaults."""
import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.runtime.run_contract import build_run_contract


def _config(tmp_path, kind, field):
    path = tmp_path / "zf.yaml"
    if kind == "RefactorFlow":
        field += "\n  flowProfile: refactor-flow/v3\n  assembly: {task: DEMO-ASM}"
    path.write_text(f"""apiVersion: zaofu.dev/v1
kind: {kind}
spec:
  {field}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  project: {{name: demo}}
""")
    return path, load_config(path)


@pytest.mark.parametrize("kind", ["PrdFlow", "IssueFlow", "RefactorFlow"])
@pytest.mark.parametrize("key", ["inputDeliveryMode", "input_delivery_mode"])
def test_flow_compiler_and_run_contract_pin_observed_delivery(tmp_path, kind, key):
    path, cfg = _config(tmp_path, kind, f"{key}: observed")
    assert cfg.workflow.flow_metadata["result_protocol"]["input_delivery_mode"] == "observed"
    contract = build_run_contract(cfg, config_path=path, project_root=tmp_path, state_dir=tmp_path / ".zf")
    assert contract["protocols"]["required_read"]["input_delivery_mode"] == "observed"


@pytest.mark.parametrize("field", ["inputDeliveryMode: yes", "inputDeliveryMode: null",
    "inputDeliveryMode: observed\n  input_delivery_mode: produced"])
def test_flow_rejects_invalid_or_ambiguous_mode(tmp_path, field):
    with pytest.raises(ConfigError, match="inputDeliveryMode"):
        _config(tmp_path, "PrdFlow", field)
