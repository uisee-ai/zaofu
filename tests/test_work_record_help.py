"""公共帮助中的示例必须能通过当前 WRC 发布入口。"""
import json
from pathlib import Path

import pytest

from tests.test_work_record import setup
from zf.cli.main import main
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def test_publish_help_example_reaches_existing_work_record_caller(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["artifact", "work-record", "publish", "--help"])
    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    example, _ = json.JSONDecoder().raw_decode(help_text[help_text.index("{\n"):])
    state = tmp_path / "state"
    _, _, log, record, token = setup(state)
    record.write_text(json.dumps(example, ensure_ascii=False))
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "dev-1")
    monkeypatch.setenv("ZF_RESULT_SUBMIT_TOKEN", token)
    assert main(["artifact", "work-record", "publish", "--operation", "op", "--file", str(record),
                 "--request-id", "from-help", "--state-dir", str(state)]) == 0
    result = json.loads(capsys.readouterr().out)
    body = hydrate_sidecar_ref(state, result["work_record_ref"]).payload
    assert body["completed_actions"] == example["completed_actions"]
    assert body["claims_status"] == "worker_report_unverified"
    assert body["authorizes_execution"] is False
    evidence = hydrate_sidecar_ref(state, body["evidence_refs"][0]).payload
    assert evidence["path"] == "tests.log"
    assert len([event for event in log.read_all() if event.type == "worker.work_record.published"]) == 1


@pytest.mark.parametrize("name", ["zf-harness-evidence-collection", "zf-yoke-dev-worker-role-context",
                                  "zf-yoke-test-evaluator-role-context", "zf-harness-done-contract"])
def test_work_record_method_provider_copies_match_canonical(name):
    root = Path(__file__).resolve().parents[1]
    source = root / "skills" / name
    files = [Path("SKILL.md")]
    if name == "zf-harness-evidence-collection":
        files.append(Path("references/work-record.md"))
    for provider in (".codex", ".claude"):
        for relative in files:
            assert (root / provider / "skills" / name / relative).read_bytes() == (source / relative).read_bytes()
