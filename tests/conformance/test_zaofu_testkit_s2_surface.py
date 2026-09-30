"""S2 conformance for runtime and sidecar state-root propagation."""

from __future__ import annotations

import ast
import argparse
import io
import json
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.runtime.feishu_inbound_sidecar import build_feishu_inbound_command
from zf.runtime.feishu_projection_sidecar import build_feishu_projection_command
from zf.runtime.run_contract import build_run_contract
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


def test_runtime_and_sidecar_surfaces_preserve_explicit_state_dir(
    tmp_path: Path,
    monkeypatch, request,
) -> None:
    """Run contract and sidecar commands must share one configured state root."""
    monkeypatch.setenv("ZF_CLI_CMD", "zf")
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-surface\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )
    state_dir = (tmp_path / "runtime-state").resolve()
    attach_runtime_case_evidence(
        request, state_dir,
        given="project declares a non-default runtime state_dir",
        when="build the run contract and both sidecar commands",
        then="all runtime surfaces point to the configured state root",
        forbidden=["checkout .zf creation", "independent sidecar state root"],
        inputs={"state_dir": str(state_dir)},
    )
    config = load_config(config_path)

    contract = build_run_contract(
        config,
        config_path=config_path,
        project_root=tmp_path,
    )
    projection = build_feishu_projection_command(
        state_dir=state_dir,
        poll_interval_seconds=2.0,
    )
    inbound = build_feishu_inbound_command(
        state_dir=state_dir,
        debounce_ms=250,
    )

    assert contract["project"]["state_dir"] == str(state_dir)
    for command in (projection, inbound):
        index = command.index("--state-dir")
        assert Path(command[index + 1]).resolve() == state_dir
    assert not (tmp_path / ".zf").exists()


def test_hook_recv_from_nested_project_directory_uses_configured_state_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request,
) -> None:
    """Hook ingress must append to the owning project root, not nested ``.zf``."""
    project_root = tmp_path / "project"
    nested = project_root / "src" / "worker"
    nested.mkdir(parents=True)
    state_dir = project_root / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="hook is invoked from a nested project directory",
        when="receive a stop hook with project context set",
        then="the event is appended under the configured project state_dir",
        forbidden=["nested .zf creation", "project-root .zf creation"],
        inputs={"nested_dir": str(nested)},
    )
    (project_root / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-hook-context\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(nested)
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(project_root))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"session_id": "s2-hook-session"})),
    )

    from zf.cli.hook_recv import run as hook_recv_run

    result = hook_recv_run(
        argparse.Namespace(
            event="codex.hook.stop",
            state_dir=None,
            backend="",
        ),
    )

    assert result == 0
    events = (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "codex.hook.stop" in events
    assert not (nested / ".zf").exists()
    assert not (project_root / ".zf").exists()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-023: bug-fix-cycle defaults to cwd/.zf instead of the shared "
        "project context when launched from a project subdirectory"
    ),
)
def test_bug_fix_cycle_from_nested_directory_uses_configured_state_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operator diagnostics must read the configured project state root."""
    project_root = tmp_path / "project"
    nested = project_root / "src" / "worker"
    nested.mkdir(parents=True)
    state_dir = project_root / "runtime-state"
    (project_root / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-bug-cycle\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )
    state_dir.mkdir()
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(
            type="zaofu.bug.detected",
            actor="testkit",
            payload={"signature": "s2-context", "confidence": "high"},
        ),
    )
    monkeypatch.chdir(nested)
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(project_root))

    from zf.cli.bug_fix_cycle import _run

    result = _run(
        argparse.Namespace(
            state_dir=None,
            signature="",
            json=True,
        ),
    )

    assert result == 0
    assert "s2-context" in capsys.readouterr().out
    assert not (nested / ".zf").exists()
    assert not (project_root / ".zf").exists()


def test_registered_cli_stateful_entries_use_one_state_root_resolver() -> None:
    """New CLI modules must not silently construct an independent ``.zf`` root."""
    repo_root = Path(__file__).resolve().parents[2]
    main_source = (repo_root / "src/zf/cli/main.py").read_text(encoding="utf-8")
    tree = ast.parse(main_source)
    registered: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "zf.cli":
            continue
        registered.extend(alias.name for alias in node.names)

    # These commands intentionally do not resolve runtime state: presets and
    # profile are pure/project-file helpers, update edits AGENTS.md, workspace
    # edits its own registry, and self_repair receives an explicit state-dir.
    stateless = {"eval_preset", "presets", "profile", "update", "workspace"}
    delegated = {"init": "ProjectInitializer"}
    explicit_state = {"self_repair": "--state-dir"}
    # These commands still have a known cwd/context bypass covered by strict
    # behavioral xfails below.  Keep them explicit so a future registration
    # cannot silently turn a known defect into an unreviewed static exception.
    known_context_bypasses = {"attach", "panes", "bug_fix_cycle"}
    shared_context_module = "zf.core.config.project_context"
    runtime_loader_markers = {"_load_runtime", "event_log_from_project"}

    failures: list[str] = []
    for module in registered:
        path = repo_root / "src/zf/cli" / f"{module}.py"
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        if module in stateless or module in known_context_bypasses:
            continue
        if module in delegated:
            if delegated[module] not in source:
                failures.append(f"{module}: missing state-aware initializer delegation")
            continue
        if module in explicit_state:
            if explicit_state[module] not in source:
                failures.append(f"{module}: missing explicit state-dir contract")
            continue
        module_tree = ast.parse(source)
        shared_context_import = any(
            isinstance(node, ast.ImportFrom)
            and node.module == shared_context_module
            and any(
                alias.name in {"resolve_project_context", "resolve_state_dir"}
                for alias in node.names
            )
            for node in ast.walk(module_tree)
        )
        runtime_loader_use = any(
            isinstance(node, ast.Name) and node.id in runtime_loader_markers
            for node in ast.walk(module_tree)
        )
        if not (shared_context_import or runtime_loader_use):
            failures.append(
                f"{module}: no shared project-context/state-dir resolver"
            )

    assert not failures, "\n".join(failures)


def test_integrations_and_web_do_not_write_canonical_files_directly() -> None:
    """Canonical truth files must be mutated only through their Store/Event APIs."""
    repo_root = Path(__file__).resolve().parents[2]
    canonical_names = {
        "events.jsonl",
        "kanban.json",
        "feature_list.json",
        "session.yaml",
        "role_sessions.yaml",
    }
    mutating_methods = {
        "write_text",
        "write_bytes",
        "unlink",
        "rename",
        "replace",
    }
    violations: list[str] = []
    for source_root in (repo_root / "src/zf/integrations", repo_root / "src/zf/web"):
        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function_name = (
                    node.func.attr if isinstance(node.func, ast.Attribute) else ""
                )
                if function_name not in mutating_methods:
                    continue
                literals = {
                    value.value
                    for value in ast.walk(node)
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                }
                names = literals & canonical_names
                if names:
                    violations.append(
                        f"{path.relative_to(repo_root)}:{node.lineno}: "
                        f"{function_name}({sorted(names)})"
                    )
    assert not violations, "direct canonical-file mutation: " + "; ".join(violations)


@pytest.mark.parametrize("module", ("attach", "panes", "bug_fix_cycle"))
@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-019/023: CLI helper resolves zf.yaml or state_dir from cwd "
        "instead of the shared project context"
    ),
)
def test_known_cli_context_bypasses_are_removed(module: str) -> None:
    """Keep the discovered production state-root bypasses visible."""
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / "src/zf/cli" / f"{module}.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(source)
    shared_context_import = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "zf.core.config.project_context"
        and any(
            alias.name in {"resolve_project_context", "resolve_state_dir"}
            for alias in node.names
        )
        for node in ast.walk(tree)
    )
    runtime_loader_use = any(
        isinstance(node, ast.Name)
        and node.id in {"_load_runtime", "event_log_from_project"}
        for node in ast.walk(tree)
    )
    assert shared_context_import or runtime_loader_use, module


@pytest.mark.parametrize("module", ("attach", "panes"))
@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-019: CLI entrypoint launched from a project subdirectory "
        "does not resolve the configured project context"
    ),
)
def test_nested_cli_entrypoints_honor_inherited_project_context(
    module: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detached operator shells must still target the owning project state root."""
    project_root = tmp_path / "project"
    nested = project_root / "src" / "worker"
    nested.mkdir(parents=True)
    (project_root / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: nested-context\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )
    state_dir = project_root / "runtime-state"
    monkeypatch.chdir(nested)
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("ZF_STATE_DIR", str(state_dir))

    if module == "attach":
        from zf.cli import attach

        captured: dict[str, object] = {}

        class _Handle:
            argv: list[str] = []
            note = ""

        class _Transport:
            def attach_handle(self, role):  # noqa: ANN001
                captured["role"] = role
                return _Handle()

        def make_transport(config):  # noqa: ANN001
            captured["config"] = config
            return _Transport()

        monkeypatch.setattr(attach, "make_transport", make_transport)
        monkeypatch.setattr(attach, "TmuxTransport", lambda *_args, **_kwargs: _Transport())
        # ``attach`` normally replaces the process; the empty handle path keeps
        # this detector entirely in-process and side-effect free.
        result = attach.run(argparse.Namespace(role=None))
        assert result == 1
        config = captured["config"]
        assert config.project.name == "nested-context"
        assert (project_root / config.project.state_dir).resolve() == state_dir.resolve()
        return

    from zf.cli import panes

    manager = panes._load_manager()
    assert manager.project_root.resolve() == project_root.resolve()
    assert manager.state_dir.resolve() == state_dir.resolve()
