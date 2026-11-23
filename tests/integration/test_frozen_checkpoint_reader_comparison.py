"""Opt-in model-side cost and exact fact recall for a targeted post-resume read."""

import json
import os
import time
from pathlib import Path

import pytest

from tests.e2e.test_workflow_input_replay_mock_e2e import copy_descriptor, copy_sources
from zf.cli.artifact_required_read import prepare_required_read
from zf.runtime.artifact_workdir_view import AttemptFiles
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.worker_model_handoff import local_chat

pytestmark = pytest.mark.real_provider


def test_real_reader_frozen_checkpoint_comparison(tmp_path):
    source = os.getenv("ZF_8X8_FIXTURE_STATE")
    attempts = os.getenv("ZF_REAL_READER_ATTEMPTS", "").split(",")
    if not source or not all(attempts):
        pytest.skip(
            "specify the frozen state and comma-separated Candidate Verify attempts"
        )
    source = Path(source)
    model = os.getenv("ZF_READER_MODEL", "qwen2.5:latest")
    profile = dict(port=11434, model=model, timeout_seconds=240, max_output_tokens=100)
    # All scopes are copied. The run's manifest and execution ledger remain immutable.
    for index, attempt in enumerate(attempts):
        state = tmp_path / str(index)
        with (source / "events.jsonl").open() as stream:
            operation = next(
                e["payload"]
                for line in stream
                if (e := json.loads(line))["type"] == "workflow.operation.requested"
                and e["payload"].get("active_attempt_id") == attempt
            )
        request = hydrate_sidecar_ref(source, operation["request_ref"]).payload[
            "request"
        ]
        policy = hydrate_sidecar_ref(
            source, request["input_consumption_policy_ref"]
        ).payload
        descriptor = policy["delivery_evidence"]["source_manifest_ref"]
        copy_descriptor(source, state, descriptor)
        manifest = hydrate_sidecar_ref(state, descriptor).payload
        copy_sources(source, state, manifest["sources"])
        rows = policy["required_reads"]
        assert len(rows) == 40
        ident = dict(
            role=request["role_instance"],
            actor=request["role_instance"],
            provider="codex",
            purpose=manifest["read_purpose"],
            operation_id=operation["operation_id"],
            attempt_id=attempt,
        )
        view = AttemptFiles(state, manifest, ident, rows, lambda: {})
        checkpoint = json.loads(view.read("RESUME.md")[0].split(b"\n", 2)[2])[
            "host_evidence"
        ]
        assert (
            checkpoint["total"],
            checkpoint["available"],
            len(checkpoint["gaps"]),
        ) == (19, 18, 1)
        (required,) = [
            r
            for r in rows
            if r["source_id"] == "target" and r.get("json_path", "$") == "$"
        ]
        path = next(
            name
            for name, (r, _) in view.files.items()
            if r["source_id"] == "target" and r["json_path"] == "$"
        )
        target = json.loads(view.read(path)[0])["target_commit"]
        for mode in "ABC" if index % 2 == 0 else "CBA":
            started = time.monotonic()
            if mode == "C":
                output = view.read(path)[0].decode()
            else:
                output, _ = prepare_required_read(
                    state,
                    manifest=manifest,
                    rows=[required],
                    identity=ident,
                    output_format="json" if mode == "A" else "text",
                    page_bytes=8192 if mode == "A" else 32768,
                )
            assert target in output
            packet = {
                "question": "Return just these three facts: first eight characters of target_commit, the exact receipt_id for the gap, and the available receipt count.",
                "checkpoint": checkpoint,
                "target_source": output,
            }
            result = local_chat(
                profile,
                packet,
                "Use only the evidence in the user input. Answer the three questions on one line.",
            )
            import re

            answer = result["advice"].lower()
            criteria = {
                "target": target[:8] in answer,
                "receipt": checkpoint["gaps"][0]["receipt_id"].lower() in answer,
                "available": bool(
                    re.search(r"\b" + str(checkpoint["available"]) + r"\b", answer)
                ),
            }
            print(
                "ZF_READER_CHECKPOINT",
                json.dumps(
                    dict(
                        attempt=attempt,
                        mode=mode,
                        model=model,
                        policy_required=len(rows),
                        targeted_reads=1,
                        source_sha256=descriptor["sha256"],
                        input_tokens=result["input_tokens"],
                        output_tokens=result["output_tokens"],
                        cached_input_tokens=None,
                        elapsed_seconds=round(time.monotonic() - started, 2),
                        facts=criteria,
                        model_calls=1,
                    )
                ),
                flush=True,
            )
        assert not (state / "events.jsonl").exists()
