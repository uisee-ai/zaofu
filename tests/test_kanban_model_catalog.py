from pathlib import Path

import pytest

from zf.web import kanban_model_catalog


def test_catalog_follows_provider_pages_and_exposes_only_public_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, dict]] = []
    closed: list[bool] = []

    class Backend:
        def available(self) -> bool:
            return True

        def build_args(self) -> list[str]:
            return ["mock"]

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def notify(self, method):
            pass

        def close(self):
            closed.append(True)

        def request(self, method, params):
            calls.append((method, params))
            if method == "initialize":
                return {}
            if method == "config/read":
                return {"config": {"model": "model-a", "model_reasoning_effort": "medium", "secret": "do-not-expose"}}
            if not params["cursor"]:
                return {"data": [{"model": "model-a", "displayName": "A", "isDefault": True,
                                  "defaultReasoningEffort": "medium", "secret": "do-not-expose",
                                  "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]}], "nextCursor": "next"}
            return {"data": [{"model": "hidden", "hidden": True}], "nextCursor": None}

    monkeypatch.setattr(kanban_model_catalog, "CodexHeadlessBackend", Backend)
    monkeypatch.setattr(kanban_model_catalog, "_CodexRpcClient", Client)
    result = kanban_model_catalog.codex_model_catalog(tmp_path)
    assert result == {"backend": "codex-headless", "session_defaults": {"model": "model-a", "effort": "medium", "source": "provider_config"}, "models": [{
        "model": "model-a", "label": "A", "default": True,
        "default_effort": "medium", "efforts": ["medium"],
    }]}
    assert calls[-1][1]["cursor"] == "next"
    assert closed == [True]


@pytest.mark.parametrize(("baseline", "read_result", "expected"), [
    ({"model": "original", "thinking_level": "high"}, None, {"model": "original", "effort": "high", "source": "session_baseline"}),
    (None, {"model": "session-model", "reasoningEffort": "low"}, {"model": "session-model", "effort": "low", "source": "provider_session"}),
    (None, None, {"model": None, "effort": None, "source": "unknown"}),
])
def test_catalog_uses_session_defaults_without_exposing_session_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, baseline: dict | None, read_result: dict | None, expected: dict,
) -> None:
    from zf.web.headless_agent import HeadlessThreadStore

    class Backend:
        def available(self): return True
        def build_args(self): return ["mock"]

    class Client:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        def notify(self, method): pass
        def close(self): pass
        def request(self, method, params):
            if method == "initialize": return {}
            if method == "config/read": return {"config": {"model": "global-model", "model_reasoning_effort": "medium"}}
            if method == "thread/read":
                if read_result is None: raise RuntimeError("session unavailable")
                return {"thread": read_result}
            return {"data": [], "nextCursor": None}

    monkeypatch.setattr(kanban_model_catalog, "CodexHeadlessBackend", Backend)
    monkeypatch.setattr(kanban_model_catalog, "_CodexRpcClient", Client)
    state = tmp_path / "state"
    store = HeadlessThreadStore(state_dir=state, project_root=tmp_path)
    thread = store.load(thread_key="t", task_id="T")
    thread["providers"] = {"codex-headless": {"provider_session_id": "private-id"}}
    if baseline:
        thread["providers"]["codex-headless"]["one_turn_model_restore"] = baseline
    store.save(thread)
    result = kanban_model_catalog.codex_model_catalog(tmp_path, state_dir=state, thread_key="t", task_id="T")
    assert result["session_defaults"] == expected
    assert "private-id" not in str(result)


def test_catalog_rejects_repeated_cursor_and_closes_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Backend:
        def available(self): return True
        def build_args(self): return ["mock"]

    closed: list[bool] = []

    class Client:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        def notify(self, method): pass
        def close(self): closed.append(True)
        def request(self, method, params):
            return {} if method == "initialize" else {"data": [], "nextCursor": "same"}

    monkeypatch.setattr(kanban_model_catalog, "CodexHeadlessBackend", Backend)
    monkeypatch.setattr(kanban_model_catalog, "_CodexRpcClient", Client)
    with pytest.raises(RuntimeError, match="repeated model cursor"):
        kanban_model_catalog.codex_model_catalog(tmp_path)
    assert closed == [True]


def test_unavailable_configuration_does_not_hide_provider_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Backend:
        def available(self): return True
        def build_args(self): return ["mock"]

    class Client:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        def notify(self, method): pass
        def close(self): pass
        def request(self, method, params):
            if method == "config/read": raise RuntimeError("unsupported")
            if method == "initialize": return {}
            return {"data": [{"model": "available", "supportedReasoningEfforts": []}], "nextCursor": None}

    monkeypatch.setattr(kanban_model_catalog, "CodexHeadlessBackend", Backend)
    monkeypatch.setattr(kanban_model_catalog, "_CodexRpcClient", Client)
    result = kanban_model_catalog.codex_model_catalog(tmp_path)
    assert result["models"][0]["model"] == "available"
    assert result["session_defaults"] == {"model": None, "effort": None, "source": "unknown"}
