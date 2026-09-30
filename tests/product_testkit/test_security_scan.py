import json
from pathlib import Path
import sys

from .security_scan import run_zap_contract


def _spec(report: str = "evidence/zap.json") -> dict[str, object]:
    return {
        "id": "zap", "tool": "zap", "command": f"{sys.executable} scanner.py",
        "report": report, "max_risk": 0, "tier": "security", "timeout_seconds": 20,
    }


def _write_scanner(project: Path, *, alert: bool = False) -> None:
    project.joinpath("scanner.py").write_text(
        "from pathlib import Path\n"
        "import json\n"
        "payload = {'@programName': 'ZAP', 'site': [{'alerts': []}]}\n"
        + ("payload['site'][0]['alerts'] = [{'pluginid': '1', 'alert': 'bad', 'riskcode': '2', 'confidence': 'high', 'instances': [{}]}]\n" if alert else "")
        + "Path('evidence').mkdir(exist_ok=True)\nPath('evidence/zap.json').write_text(json.dumps(payload), encoding='utf-8')\n",
        encoding="utf-8",
    )


def test_zap_contract_requires_complete_report_and_preserves_evidence(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    _write_scanner(project)
    result = run_zap_contract(_spec(), project_root=project, output_dir=tmp_path / "out")
    assert result["verdict"] == "pass"
    assert result["report_ref"] == "security/zap/zap-report.json"
    assert (tmp_path / "out" / result["report_ref"]).is_file()
    assert (tmp_path / "out" / "security/zap/result.json").is_file()


def test_zap_contract_classifies_report_alert_as_product_failure(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    _write_scanner(project, alert=True)
    result = run_zap_contract(_spec(), project_root=project, output_dir=tmp_path / "out")
    assert result["verdict"] == "fail"
    assert result["violations"]


def test_zap_contract_zero_exit_without_report_is_harness_failure(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    project.joinpath("scanner.py").write_text("pass\n", encoding="utf-8")
    result = run_zap_contract(_spec(), project_root=project, output_dir=tmp_path / "out")
    assert result["verdict"] == "harness_failure"
    assert "without a regular report" in result["reason"]
