import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from tests import test_input_delivery_admission as delivery
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.worker_model_handoff import consult, model_profile


def setup(tmp_path, monkeypatch, port):
    factory = delivery._runtime

    def build(p):
        runtime = factory(p)
        runtime.config.workflow.flow_metadata["worker_model_planners"] = {
            "dev-1": {
                "enabled": True,
                "model": "mock-planner",
                "port": port,
                "max_calls": 2,
            }
        }
        return runtime

    monkeypatch.setattr(delivery, "_runtime", build)
    runtime, payload, prepared, _, _ = delivery._operation(tmp_path, pipeline=True)
    manifest = hydrate_sidecar_ref(
        runtime.state_dir, payload["attempt_source_manifest"]
    ).payload
    identity = {
        "operation_id": prepared.operation_id,
        "actor": "dev-1",
        "role": "dev",
        "provider": "codex",
        "purpose": manifest["read_purpose"],
        "attempt_id": manifest["attempt_id"],
    }
    args = (
        runtime.state_dir,
        runtime.config,
        runtime.event_log,
        runtime.event_writer,
        manifest,
        identity,
    )
    row = payload["required_reads"][0]
    refs = [(row["source_id"], row["artifact_id"], row.get("json_path", "$"))]
    return runtime, payload, args, refs, lambda: identity


def test_actual_local_http_planning_replay_permission_and_independent_verify(
    tmp_path, monkeypatch
):
    calls = []

    class MockPlanner(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/api/chat"
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert "tools" not in data
            calls.append(data)
            body = json.dumps(
                {
                    "model": data["model"],
                    "done": True,
                    "prompt_eval_count": 11,
                    "eval_count": 4,
                    "message": {
                        "role": "assistant",
                        "content": "Check the bound contract, then return to implementation.",
                    },
                }
            ).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), MockPlanner) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            runtime, payload, args, refs, auth = setup(
                tmp_path, monkeypatch, server.server_port
            )
            assert payload["worker_model_planner"] == model_profile(
                runtime.config, "dev-1"
            )
            result = consult(*args, "initial", refs, authorize=auth)
            assert (
                result["input_tokens"] == 11
                and result["independent_verification_required"]
            )
            assert not result["authorizes_execution"]
            assert consult(*args, "initial", refs, authorize=auth) == result
            with pytest.raises(ValueError, match="both failure"):
                consult(*args, "repeated_failure", refs, authorize=auth)
            with pytest.raises(ValueError):
                consult(*args, "design_choice", [("secrets", "x", "$")], authorize=auth)
            consult(*args, "interface_deviation", refs, authorize=auth)
            with pytest.raises(ValueError, match="allowance"):
                consult(*args, "design_choice", refs, authorize=auth)
            runtime.config.workflow.flow_metadata["worker_model_planners"]["dev-1"][
                "enabled"
            ] = False
            with pytest.raises(ValueError, match="single-model"):
                consult(*args, "initial", refs, authorize=auth)
            assert len(calls) == 2
            assert not any(
                e.type == "workflow.call.result.admitted"
                for e in runtime.event_log.read_all()
            )
        finally:
            server.shutdown()
            thread.join()


def test_provider_loss_keeps_original_attempt_and_records_unknown(
    tmp_path, monkeypatch
):
    runtime, _, args, refs, auth = setup(tmp_path, monkeypatch, 11434)

    def lost(*args):
        raise TimeoutError()

    with pytest.raises(ValueError, match="unavailable"):
        consult(*args, "initial", refs, authorize=auth, invoke=lost)
    with pytest.raises(ValueError, match="already claimed"):
        consult(*args, "initial", refs, authorize=auth, invoke=lost)
    assert [
        e.type
        for e in runtime.event_log.read_all()
        if e.actor == "worker-model-adapter"
    ] == ["worker.model.claimed", "worker.model.unavailable"]
