"""Stateless inference over a frozen packet using an installed local Ollama model."""

from __future__ import annotations

import http.client
import json
import re
import uuid

from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.skill_semantic_review import FINDINGS, OWNERS, SYSTEM, validate_decision

MAX_INPUT_BYTES = 18_000
OUTPUT_TOKENS = 700
OPTIONS = {"temperature": 0, "seed": 214, "num_thread": 4, "num_ctx": 8192, "num_predict": OUTPUT_TOKENS}


def _request(path, payload=None, *, timeout=5):
    # Direct loopback connection: no proxy, redirect, tool runtime or shared conversation.
    conn = http.client.HTTPConnection("127.0.0.1", 11434, timeout=timeout)
    try:
        conn.request(
            "GET" if payload is None else "POST",
            "/api/" + path,
            body=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        data = response.read(2_000_001)
        if response.status != 200 or len(data) > 2_000_000:
            raise ValueError("local reviewer response unavailable")
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("invalid local reviewer response")
        return parsed
    finally:
        conn.close()


def model_identity(name):
    from zf.integrations.controller.binding import ManagedDenied, managed_config

    try:
        managed = managed_config()
    except ManagedDenied as exc:
        raise ValueError("local review needs a standalone installation") from exc
    if managed is not None:
        raise ValueError("standalone local inference unavailable for managed projects")
    models = _request("tags").get("models")
    if not isinstance(models, list):
        raise ValueError("invalid local model registry")
    tag = next((row for row in models if isinstance(row, dict) and row.get("name") == name), {})
    digest = tag.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("select an installed model by its full tag")
    # Newer daemons can list cloud-backed models as local aliases.
    info = _request("show", {"model": name})
    if (
        info.get("remote_model")
        or info.get("remote_host")
        or tag.get("remote_model")
        or tag.get("remote_host")
    ):
        raise ValueError("review requires a local model")
    version = _request("version").get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("missing local reviewer version")
    return {
        "backend": "ollama",
        "model": name,
        "model_digest": digest,
        "backend_version": version,
    }


def _schema(packet):
    def enum(values):
        return {"type": "string", "enum": list(values)}

    def obj(fields):
        return {
            "type": "object",
            "properties": fields,
            "required": list(fields),
            "additionalProperties": False,
        }

    string = {"type": "string"}
    item = obj(
        {
            "id": enum(row["id"] for row in packet["obligations"]),
            "finding": enum(FINDINGS if packet["host_facts"] else ("unresolved",)),
            "citations": {"type": "array", "items": enum(["contract", *packet["host_facts"]])},
            "reason": string,
        }
    )
    return obj(
        {
            "items": {"type": "array", "items": item},
            "owner": enum(OWNERS),
            "action": string,
            "limitation": string,
        }
    )


def review_prompt_digest(packet):
    return canonical_json_sha256({"system": SYSTEM, "schema": _schema(packet)})


def blinded_input(packet):
    return {key: packet[key] for key in ("obligations", "host_facts")}


def review_once(packet, identity, timeout):
    evidence = blinded_input(packet)
    content = json.dumps(evidence, ensure_ascii=False)
    if len(content.encode()) > MAX_INPUT_BYTES or not packet["obligations"]:
        raise ValueError("input unavailable or outside review context budget")
    schema = _schema(packet)
    session = str(uuid.uuid4())
    response = _request(
        "chat",
        {
            "model": identity["model"],
            "stream": False,
            "format": schema,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
            "options": OPTIONS,
        },
        timeout=timeout,
    )
    message = response.get("message", {})
    if (
        response.get("done") is not True
        or response.get("done_reason") != "stop"
        or response.get("model") != identity["model"]
        or not isinstance(message, dict)
        or not isinstance(message.get("content"), str)
        or message.get("role") != "assistant"
        or message.get("tool_calls")
        or type(response.get("prompt_eval_count")) is not int
        or not 0 < response["prompt_eval_count"] < OPTIONS["num_ctx"] - OUTPUT_TOKENS
        or type(response.get("eval_count")) is not int
        or not 0 < response["eval_count"] <= OUTPUT_TOKENS
    ):
        raise ValueError("incomplete local review")
    if model_identity(identity["model"]) != identity:
        raise ValueError("review model changed during inference")
    decision = validate_decision(packet, json.loads(message["content"]))
    return {
        "decision": decision,
        "session_id": session,
        "reviewer": identity,
        "prompt_digest": review_prompt_digest(packet),
        "model_input_digest": canonical_json_sha256(evidence),
        "inference_options": dict(OPTIONS),
        "input_tokens": response.get("prompt_eval_count"),
        "output_tokens": response.get("eval_count"),
    }
