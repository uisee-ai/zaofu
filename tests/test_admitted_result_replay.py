"""Optional secure result submission remains authoritative on replay."""

import pytest

from tests import test_result_submit as fixtures
from zf.runtime.call_result_runtime import admit_runtime_call_result


@pytest.mark.parametrize("mode", ["off", "blocking"])
def test_secure_result_replay_does_not_readapt_optional_submission(tmp_path, monkeypatch, mode):
    original = fixtures._runtime

    def configured(path):
        runtime = original(path)
        runtime.config.workflow.flow_metadata["result_protocol"][
            "semantic_submit_profiles"]["implementation"] = mode
        return runtime

    monkeypatch.setattr(fixtures, "_runtime", configured)
    runtime, prepared, service, token = fixtures._running_operation(tmp_path)
    submitted = service.submit(operation_id=prepared.operation_id,
                               semantic_result=fixtures._semantic(),
                               role_instance="dev-1", credential=token)
    canonical = next(e for e in runtime.event_log.read_all()
                     if e.id == submitted.canonical_event_id)
    before = [e.id for e in runtime.event_log.read_all()]
    for _ in range(2):
        outcome = admit_runtime_call_result(runtime, canonical, dispatch_correction=False)
        assert outcome.admitted
        assert outcome.envelope_ref == canonical.payload["call_result_envelope_ref"]
    assert [e.id for e in runtime.event_log.read_all()] == before
